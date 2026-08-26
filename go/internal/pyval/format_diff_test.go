package pyval_test

import (
	"encoding/json"
	"math"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Both probes take the value as a Python EXPRESSION rather than a JSON
// number, for the reason the sibling PercentF differential documents: a
// double that has no short decimal form does not survive a round trip
// through the probe's own JSON, and half of what these two helpers get
// wrong lives exactly at values with no short form.
const pyGroupedFSrc = `
import json, sys
out = []
for prec, expr in json.loads(sys.argv[1]):
    out.append(("{:,." + str(prec) + "f}").format(eval(expr)))
print(json.dumps(out))
`

const pyPercentFmtSrc = `
import json, sys
out = []
for prec, expr in json.loads(sys.argv[1]):
    out.append(("{:." + str(prec) + "%}").format(eval(expr)))
print(json.dumps(out))
`

// formatRows is shared by both differentials on purpose: the two format
// specs differ only in what they do BEFORE the rounding, so every value
// that distinguishes one is worth running through the other.
type formatRow struct {
	name   string
	val    float64
	prec   int
	pyExpr string
}

// pointThree is 0.1 + 0.2 computed at RUNTIME, and it must not be written
// as a constant expression.
//
// Go evaluates untyped constant arithmetic exactly and rounds ONCE at the
// conversion, so `const c = 0.1 + 0.2` is the nearest double to 3/10 —
// 0.29999999999999998890. Python rounds 0.1 and 0.2 to doubles first and
// then adds, giving 0.30000000000000004441. They are different doubles, and
// a fixture written the natural way measures the wrong one: this table
// caught it at 17 decimals, where the two spellings finally print
// differently. `internal/record/r4fixes_test.go` records the same hazard —
// it is a property of Go's spec, not of this helper.
//
// Division is not affected: 2.0 and 3.0 are exactly representable, so the
// constant quotient and the runtime quotient round identically. Only
// operands that are themselves inexact diverge.
var pointOne, pointTwo = 0.1, 0.2
var pointThree = pointOne + pointTwo

func formatRows() []formatRow {
	return []formatRow{
		{"zero", 0, 0, "0.0"},

		// Round-half-to-EVEN, and the four cases that show it is not
		// half-away-from-zero. 0.5 rounds DOWN and 1.5 rounds UP.
		{"a half that rounds down", 0.5, 0, "0.5"},
		{"a half that rounds up", 1.5, 0, "1.5"},
		{"two and a half rounds to two", 2.5, 0, "2.5"},
		{"three and a half rounds to four", 3.5, 0, "3.5"},

		// NEGATIVE ZERO SURVIVES. Python renders "-0", and a port that
		// normalised it would differ on every rate rounding down from
		// below zero.
		{"a negative half rounds to negative zero", -0.5, 0, "-0.5"},
		{"negative zero itself", math.Copysign(0, -1), 0, "-0.0"},
		{"negative zero at two places", math.Copysign(0, -1), 2, "-0.0"},

		// The separator's own boundaries: exactly three digits takes no
		// comma, four takes one, and a length that is an exact multiple of
		// three must not acquire a leading comma.
		{"three digits", 999, 0, "999.0"},
		{"four digits", 1000, 0, "1000.0"},
		{"exactly six digits", 100000, 0, "100000.0"},
		{"exactly nine digits", 100000000, 0, "100000000.0"},
		{"a negative four-digit value", -1234.5, 0, "-1234.5"},

		// Only the INTEGER part is grouped; the fraction keeps its digits.
		{"a grouped value with a fraction", 1234567.891, 3, "1234567.891"},
		{"a negative grouped value with a fraction", -1234.5678, 2,
			"-1234.5678"},
		{"a fraction long enough to group if the rule were wrong",
			1.123456789, 9, "1.123456789"},

		// Halves at the grouping boundary, where a wrong rounding and a
		// wrong grouping look alike.
		{"a half at four digits, rounding to even", 1234.5, 0, "1234.5"},
		{"the next half up, rounding the other way", 1235.5, 0, "1235.5"},

		// A value with no short decimal form, and one whose expansion is
		// far wider than any fixed-width integer. 1e308 renders 309 digits
		// and Python groups every one of them.
		{"a value with no short decimal form", pointThree, 17, "0.1 + 0.2"},
		{"the largest finite double", math.MaxFloat64, 0, "1.7976931348623157e308"},
		{"a power of ten past int64", 1e308, 0, "1e308"},
		{"the smallest subnormal", 5e-324, 0, "5e-324"},

		// The three Go and Python spell differently.
		{"nan", math.NaN(), 0, "float('nan')"},
		{"inf", math.Inf(1), 0, "float('inf')"},
		{"negative inf", math.Inf(-1), 2, "float('-inf')"},

		// Percent-specific: 0.125 and 0.375 are exact halves AFTER the
		// multiply and round in OPPOSITE directions. Rounding before
		// multiplying answers "13%" and "38%"; Python answers "12%" and
		// "38%". Nothing else in this table separates the two orders.
		{"an exact half-percent rounding down", 0.125, 0, "0.125"},
		{"an exact half-percent rounding up", 0.375, 0, "0.375"},
		{"a rate just under a half percent", 0.305, 0, "0.305"},
		{"two thirds", 2.0 / 3.0, 0, "2.0 / 3.0"},
		{"a whole one", 1.0, 0, "1.0"},
		{"a rate above one", 2.5, 0, "2.5"},
		{"a negative rate", -0.305, 0, "-0.305"},

		// A precision the helper cannot be ignoring.
		{"two places", 1.005, 2, "1.005"},
		{"six places", 1.0 / 3.0, 6, "1.0 / 3.0"},
	}
}

func runFormatDiff(t *testing.T, src string, got func(formatRow) string) {
	t.Helper()
	rows := formatRows()
	args := make([][2]any, len(rows))
	for i, r := range rows {
		args[i] = [2]any{r.prec, r.pyExpr}
	}
	arg, err := json.Marshal(args)
	if err != nil {
		t.Fatal(err)
	}
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want, string(arg))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}
	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			if g := got(r); g != want[i] {
				t.Errorf("cpython %q, go %q", want[i], g)
			}
		})
	}
}

func TestGroupedFMatchesCPython(t *testing.T) {
	runFormatDiff(t, pyGroupedFSrc, func(r formatRow) string {
		return pyval.GroupedF(r.val, r.prec)
	})
}

func TestPercentFmtMatchesCPython(t *testing.T) {
	runFormatDiff(t, pyPercentFmtSrc, func(r formatRow) string {
		return pyval.PercentFmt(r.val, r.prec)
	})
}

// TestGroupedAndGroupedFAgreeOnIntegers is the crossing test for the shared
// separator rule. Grouped takes an int64 and GroupedF a float64, and they
// now run through one groupDigits — so a change to it that suits one caller
// and breaks the other has to fail somewhere.
func TestGroupedAndGroupedFAgreeOnIntegers(t *testing.T) {
	for _, n := range []int64{0, 1, 12, 123, 1000, 999999, 1000000,
		-1, -999, -1000, -1234567, 1 << 53} {
		if a, b := pyval.Grouped(n), pyval.GroupedF(float64(n), 0); a != b {
			t.Errorf("%d: Grouped %q, GroupedF %q", n, a, b)
		}
	}
	// 2^53 is where a float64 stops representing every integer, so the
	// agreement above is asserted only where both CAN agree. Past it the
	// int64 spelling is the correct one and the float spelling is the
	// nearest double — a real difference, named rather than papered over.
	const past = int64(1)<<53 + 1
	if a, b := pyval.Grouped(past), pyval.GroupedF(float64(past), 0); a == b {
		t.Errorf("expected int64 and float64 to disagree past 2^53, both %q", a)
	}
}
