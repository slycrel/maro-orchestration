package record

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode"
)

// Verdict-trust policy (Python memory_ledger.verdict_trust — VERIFY_LEARN_ARC
// §4): classify how much a run's goal-verdict may be trusted by learning
// consumers. The single policy function, consumed by the V2 cadence windows
// (internal/scans) and any future crystallization gate.
//
// Ported semantics, with ONE named divergence carried over from the
// inspector/evolver/recall twins (r3 fix, backport-candidate #9): a MALFORMED
// goal_achieved (present but not a bool) is graded judged-NOT-achieved, where
// fork-point Python's `is False` / `is None` checks let it read as unjudged.
// The safe direction for a trust policy is the same as for the fair-cap: a
// value someone wrote but nobody can read must not earn the trusted-good path.

const (
	// VerdictConfidenceFloor mirrors memory_ledger.VERDICT_CONFIDENCE_FLOOR.
	VerdictConfidenceFloor = 0.7

	VerdictTrustFull        = "full"
	VerdictTrustDirectional = "directional"
	VerdictTrustNeutral     = "neutral"
	VerdictTrustExcluded    = "excluded"
)

// GoalAchieved reads a row's goal_achieved with the hardened tri-state the
// port's readers share (inspector.triState/evolver.triState/recall):
// (judged, achieved). Absent or nil → unjudged. A bool → judged, its value.
// Any other type → judged, NOT achieved (malformed values never read good).
func GoalAchieved(row map[string]any) (judged, achieved bool) {
	v, present := row["goal_achieved"]
	if !present || v == nil {
		return false, false
	}
	if b, ok := v.(bool); ok {
		return true, b
	}
	return true, false
}

// VerdictTrust classifies one outcomes.jsonl row (ledger rehydration shape).
//
//	full        — judged, confidence >= floor (or no confidence attached:
//	              deterministic judges are authoritative), not env-capped.
//	directional — judged but an explicit confidence below the floor.
//	neutral     — verdict absent (not judged ≠ failed).
//	excluded    — closure_unverifiable: the verifier's own failure, excluded
//	              from ALL learning consumers.
func VerdictTrust(row map[string]any) string {
	if s, _ := row["goal_verdict_source"].(string); s == "closure_unverifiable" {
		return VerdictTrustExcluded
	}
	judged, _ := GoalAchieved(row)
	if !judged {
		return VerdictTrustNeutral
	}
	if conf, ok := coerceFloat(row["goal_verdict_confidence"]); ok && conf < VerdictConfidenceFloor {
		return VerdictTrustDirectional
	}
	return VerdictTrustFull
}

// coerceFloat accepts float64/int/json.Number-ish values the tolerant JSONL
// readers can produce, PLUS numeric strings — Python's `float(conf)` parses
// "0.5", and this is the single §4 policy site both runtimes consume, so a
// string-typed confidence must classify identically (r1 parity review: Go
// read "0.5" as unparseable → FULL where Python reads DIRECTIONAL). Only a
// NON-numeric value is (0, false) — that is Python's TypeError/ValueError
// pass, and an unreadable confidence never downgrades a judged verdict.
// Two more Python float() edges matched in r4 (both had flipped trust in
// the UNSAFE direction — malformed row earning the trusted path): bools
// coerce (float(False)=0.0 → directional), and out-of-range numeric
// strings coerce ("-1e999" → -inf → directional; Go's ParseFloat returns
// the same ±Inf/0 with ErrRange, which is a successful parse in Python's
// eyes, not an error).
func coerceFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case bool:
		if n {
			return 1, true
		}
		return 0, true
	case string:
		// Python's float() runs the text through
		// PyUnicode_TransformDecimalAndSpaceToASCII first, so EVERY Unicode
		// decimal digit is accepted. Measured on CPython 3.14.3: all 760 Nd
		// code points parse, 750 of them non-ASCII, and "٠.٥" is 0.5. Go's
		// ParseFloat takes ASCII only, so without this a confidence written
		// in Arabic-Indic digits was unparseable HERE and readable THERE —
		// and the failure direction is the unsafe one, because an
		// unparseable confidence never downgrades a judged verdict, so a
		// below-floor value read as FULL trust. (Adversarial r5, LOW; it
		// named 75 divergences, the measured figure is 750.)
		t := transformDecimals(strings.TrimSpace(n))
		// Go ParseFloat accepts hex floats ("0x1p-2"); Python float()
		// raises on them and classifies FULL via the error pass — reject
		// hex so both runtimes agree (r2 review LOW-1).
		low := strings.TrimLeft(strings.ToLower(t), "+-")
		if strings.HasPrefix(low, "0x") {
			return 0, false
		}
		f, err := strconv.ParseFloat(t, 64)
		if err != nil && errors.Is(err, strconv.ErrRange) {
			return f, true // over/underflow: Python float() yields ±inf / 0.0
		}
		return f, err == nil
	case interface{ Float64() (float64, error) }: // json.Number
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}

// coerceInt is Python's int() for the shapes YAML can produce, and it is
// NOT coerceFloat with a truncation bolted on: int("10.5") raises where
// float("10.5") does not, so a config value that would silently become 10
// under a float-then-truncate reading must instead fall back to the
// default the way Python's except branch does.
//
// Underscores are handled by hand because Go's ParseInt accepts them only
// in base 0, and base 0 would also accept "0x10" and "0o17", which Python's
// one-argument int() rejects.
func coerceInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		if n > math.MaxInt || n < math.MinInt {
			return 0, false
		}
		return int(n), true
	case float64:
		// Python's int(float) truncates toward zero and raises on nan/inf.
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return 0, false
		}
		if n >= math.MaxInt || n <= math.MinInt {
			return 0, false // Python yields a big int; refusing beats wrapping
		}
		return int(math.Trunc(n)), true
	case bool:
		if n {
			return 1, true
		}
		return 0, true
	case string:
		// Same decimal fold as float(): int() runs the text through
		// PyUnicode_TransformDecimalAndSpaceToASCII too, so Arabic-Indic
		// digits parse here exactly as they do there.
		t := transformDecimals(strings.TrimSpace(n))
		if strings.Contains(t, "_") {
			// Python allows single underscores BETWEEN digits only.
			if strings.HasPrefix(t, "_") || strings.HasSuffix(t, "_") ||
				strings.Contains(t, "__") {
				return 0, false
			}
			body := strings.TrimLeft(t, "+-")
			if strings.HasPrefix(body, "_") {
				return 0, false
			}
			t = strings.ReplaceAll(t, "_", "")
		}
		i, err := strconv.ParseInt(t, 10, 64)
		if err != nil || i > math.MaxInt || i < math.MinInt {
			return 0, false
		}
		return int(i), true
	case interface{ Float64() (float64, error) }: // json.Number
		f, err := n.Float64()
		if err != nil || f != math.Trunc(f) {
			return 0, false
		}
		return int(f), true
	}
	return 0, false
}

// transformDecimals folds every Unicode decimal digit to its ASCII
// equivalent, which is what CPython's PyUnicode_TransformDecimalAndSpace-
// ToASCII does before float() ever sees the text. Leading/trailing spaces
// are already handled by strings.TrimSpace at the call site: Go's
// unicode.White_Space set and CPython's Py_UNICODE_ISSPACE set were
// measured to be the same 25 code points, so the trim is exact.
//
// The digit VALUE is recovered by walking back to the start of the rune's
// run and taking the offset mod 10. Measured over Go's OWN digit table:
// 64 maximal runs, 63 of length 10 and one of length 50 (U+1D7CE–U+1D7FF),
// and in all 680 cases the digit value equals (offset from run start) mod
// 10 — zero mismatches. The walk-back is therefore exact, not approximate,
// and the modulo is load-bearing for the length-50 run.
//
// digitSupplement covers the code points CPython folds and Go's table does
// not. Go ships unicode 15.0.0 and CPython here has 16.0.0, so 80 Nd code
// points in 7 ranges are digits THERE and not HERE — and the failure
// direction is the unsafe one (an unfoldable confidence is unparseable, an
// unparseable confidence never downgrades a judged verdict, so a below-floor
// value would read as FULL trust). Seven range literals close it; the
// pinned sweep in verdict_digits_test.go re-derives the whole set from
// CPython and fails if a gap ever reopens.
//
// A string with no non-ASCII digits is returned unchanged, so the common
// path allocates nothing.
var digitSupplement = [...][2]rune{
	{0x10D40, 0x10D49}, // Garay
	{0x116D0, 0x116E3}, // Myanmar Pao / Eastern Pwo Karen (two 10-blocks)
	{0x11BF0, 0x11BF9}, // Sunuwar
	{0x16130, 0x16139}, // Gurung Khema
	{0x16D70, 0x16D79}, // Kirat Rai
	{0x1CCF0, 0x1CCF9}, // Outlined
	{0x1E5F1, 0x1E5FA}, // Ol Onal
}

// asciiDigit returns r's decimal value as an ASCII byte, and whether r is a
// decimal digit at all.
func asciiDigit(r rune) (byte, bool) {
	if unicode.IsDigit(r) {
		start := r
		for start > 0 && unicode.IsDigit(start-1) {
			start--
		}
		return byte('0' + (r-start)%10), true
	}
	for _, rg := range digitSupplement {
		if r >= rg[0] && r <= rg[1] {
			return byte('0' + (r-rg[0])%10), true
		}
	}
	return 0, false
}

func transformDecimals(s string) string {
	if !strings.ContainsFunc(s, func(r rune) bool {
		if r <= unicode.MaxASCII {
			return false
		}
		_, ok := asciiDigit(r)
		return ok
	}) {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if r > unicode.MaxASCII {
			if d, ok := asciiDigit(r); ok {
				b.WriteByte(d)
				continue
			}
		}
		b.WriteRune(r)
	}
	return b.String()
}
