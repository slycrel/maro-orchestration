package pyjson

import (
	"math"
	"strconv"
	"strings"
)

// FloatRepr spells a float the way CPython's float.__repr__ does, which is
// also how json.dumps spells one.
//
// Go and Python agree on the DIGITS — both emit the shortest decimal that
// round-trips — and disagree on when to switch to exponent notation. Go's
// 'g' switches when the decimal exponent is < -4 or >= the digit count, so
// 1000000.0 comes out "1e+06". CPython switches when the decimal point
// position is <= -4 or > 16, so the same value stays "1000000.0" and does
// not reach exponent notation until 1e+16.
//
// This used to be recorded as a narrow known gap justified by "every field
// emitted through here is a rate or a small counter". That justification
// was wrong: SkillStats.avg_latency_ms goes through here and is
// MILLISECONDS, so it crosses 1e6 at a ~16-minute average step, and a
// 50,321-value sweep found 4,979 spellings differing, all at |v| >= 1e6
// (adversarial r4, L5). The parsed value is identical either way, so
// nothing was misreading numbers — but Python's doctor re-serializes the
// rows it dedups, so two runtimes describing the same skill produced two
// different identities again, which is the exact bug the ".0" rule above
// was written to fix.
//
// Non-finite input has no repr json.dumps would accept; callers refuse it
// before reaching here, and the spellings below are float.__repr__'s so
// that error messages quoting a value read as Python's do.
func FloatRepr(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	}

	// Shortest round-tripping digits, in a form whose exponent is explicit:
	// "d.dddde±dd". FormatFloat('e', -1) is the same digit generator Go's
	// 'g' uses, so only the presentation rule below differs from Go's.
	sci := strconv.FormatFloat(f, 'e', -1, 64)
	neg := strings.HasPrefix(sci, "-")
	if neg {
		sci = sci[1:]
	}
	epos := strings.IndexByte(sci, 'e')
	mant, expPart := sci[:epos], sci[epos+1:]
	exp, err := strconv.Atoi(expPart)
	if err != nil {
		return strconv.FormatFloat(f, 'g', -1, 64) // unreachable; never lie
	}
	digits := strings.Replace(mant, ".", "", 1)

	// decpt is the decimal point's position within `digits`, i.e. the value
	// is 0.<digits> * 10^decpt. CPython's repr_float uses fixed notation
	// when -4 < decpt <= 16 and exponent notation otherwise.
	decpt := exp + 1

	var out string
	if decpt > -4 && decpt <= 16 {
		switch {
		case decpt <= 0:
			// 0.0001 -> "0." + "000" + digits
			out = "0." + strings.Repeat("0", -decpt) + digits
		case decpt >= len(digits):
			// 1000000.0 -> digits padded with zeros, then the mandatory ".0"
			out = digits + strings.Repeat("0", decpt-len(digits)) + ".0"
		default:
			out = digits[:decpt] + "." + digits[decpt:]
		}
	} else {
		// Python's exponent carries a sign and AT LEAST two digits:
		// "1e+16", "1e-05", "1e+308".
		m := digits[:1]
		if len(digits) > 1 {
			m += "." + digits[1:]
		}
		sign := "+"
		e := decpt - 1
		if e < 0 {
			sign, e = "-", -e
		}
		es := strconv.Itoa(e)
		if len(es) < 2 {
			es = "0" + es
		}
		out = m + "e" + sign + es
	}
	if neg {
		out = "-" + out
	}
	return out
}
