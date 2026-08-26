package pyval

import (
	"fmt"
	"os/exec"
	"strings"
	"testing"
)

// pyFloorDivSrc reads (a, b) pairs off argv and prints `repr(a // b)` for
// each, one per line. ONE interpreter for the whole sweep: a CPython process
// per case is the reason an earlier differential in this port took minutes.
//
// The pairs arrive as the exact repr of a double so no decimal round-trip
// sits between the two runtimes — `float.fromhex` would work too, but repr
// is exact for round-tripping in CPython and is what a reader can check.
const pyFloorDivSrc = `
import sys
for arg in sys.argv[1:]:
    a_s, b_s = arg.split("|")
    a = float(a_s)
    b = int(b_s)
    try:
        print(repr(a // b))
    except ZeroDivisionError as e:
        print("ZeroDivisionError")
`

// TestFloatFloorDivMatchesCPython pins FloorDivAny's FLOAT lane against
// CPython's `//`.
//
// The lane used to be `math.Floor(a / b)`, which is right for every small
// non-negative input and wrong in two separate ways:
//
//   - past 2^53 the division rounds across an integer boundary the remainder
//     does not, so `2.543036645110022e16 // 3` is 8476788817033405.0 in
//     CPython and 8476788817033407 through a floored division. Two units.
//   - on negatives, fmod takes the sign of the dividend where Python's %
//     takes the sign of the divisor. The first attempt at this fix ported the
//     fmod half and not the sign correction; it passed every positive case
//     here and `TestAnalyzeStepCostsMatchesCPython` caught it on -3001.0.
//
// Both failure modes are in the table below, deliberately, because a fixture
// set that only samples small positive doubles cannot tell any of the three
// implementations apart.
func TestFloatFloorDivMatchesCPython(t *testing.T) {
	type pair struct {
		a float64
		b int
	}
	cases := []pair{
		// past 2^53 — where floor(a/b) parts company with fmod
		{2.543036645110022e16, 3},
		{9007199254740993.0, 3},
		{1e17, 7},
		{1.8014398509481984e16, 3},
		// negatives — where the sign correction is the whole answer
		{-3001.0, 3},
		{-1.0, 1000},
		{-1500.0, 1000},
		{-0.5, 2},
		{-2.5, 2},
		// signed zero
		{-0.0, 5},
		{0.0, 5},
		// negative divisors: no caller passes one today (count is a len()),
		// but the helper is exported and the sign rule is the interesting half
		{7.0, -2},
		{-7.0, -2},
		{7.5, -2},
		// ordinary
		{5.0, 2},
		{2.5, 1},
		{1234.5, 10},
		{0.1, 3},
	}

	args := make([]string, 0, len(cases))
	for _, c := range cases {
		args = append(args, fmt.Sprintf("%s|%d", Repr(c.a), c.b))
	}
	out, err := exec.Command("python3", append([]string{"-c", pyFloorDivSrc}, args...)...).Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	want := strings.Split(strings.TrimRight(string(out), "\n"), "\n")
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d lines for %d cases", len(want), len(cases))
	}

	for i, c := range cases {
		got, gerr := FloorDivAny(c.a, c.b)
		if gerr != nil {
			t.Errorf("%v // %d: unexpected error %v", c.a, c.b, gerr)
			continue
		}
		f, ok := got.(float64)
		if !ok {
			t.Errorf("%v // %d: float lane returned %T, not float64", c.a, c.b, got)
			continue
		}
		if Repr(f) != want[i] {
			t.Errorf("%v // %d = %s, cpython %s", c.a, c.b, Repr(f), want[i])
		}
	}
}
