package pyargparse

import (
	"errors"
	"math"
	"strings"
	"testing"
)

// The saturating branch of IntValue is unreachable from either consumer's
// CPython differential, and for a reason worth writing down: it fires only
// for a literal Python holds exactly and Go cannot, so any scenario that
// reached it would be comparing two numbers that are SUPPOSED to differ.
//
// What is testable — and what matters — is that the port does not invent a
// refusal CPython never makes. `maro-run --max-steps <40 digits>` runs in
// Python; a port that answered "invalid int value" would have changed the
// CLI's contract, not merely its precision.
func TestIntValueSaturatesRatherThanRefusing(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want int
	}{
		{strings.Repeat("9", 40), math.MaxInt},
		{"-" + strings.Repeat("9", 40), math.MinInt},
		{"  " + strings.Repeat("9", 30) + " ", math.MaxInt},
		{"  -" + strings.Repeat("9", 30) + " ", math.MinInt},
	} {
		v, err := IntValue("--max-steps", tc.raw)
		if err != nil {
			t.Errorf("%q: refused with %v; CPython accepts it", tc.raw, err)
			continue
		}
		if v != tc.want {
			t.Errorf("%q: got %v, want %v", tc.raw, v, tc.want)
		}
	}
}

// The other half: a value that is not a number at all IS refused, with
// argparse's wording and a repr'd operand.
func TestIntValueRefusesNonNumbers(t *testing.T) {
	v, err := IntValue("--max-steps", "x")
	if err == nil {
		t.Fatalf("accepted %q as an int: %v", "x", v)
	}
	var ue *UsageError
	if !errors.As(err, &ue) {
		t.Fatalf("not a UsageError: %v", err)
	}
	const want = "argument --max-steps: invalid int value: 'x'"
	if ue.Error() != want {
		t.Errorf("message:\n got %q\nwant %q", ue.Error(), want)
	}
}
