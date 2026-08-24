package pyval

import (
	"encoding/json"
	"fmt"
	"math"
	"os/exec"
	"testing"
)

// Round replaced two wrong spellings, both of which shipped and both of
// which wrote to files the two runtimes share:
//
//	math.RoundToEven(f*1e4)/1e4      scans.go, under a comment claiming
//	                                 it matched round()
//	float64(int64(f*1000+0.5))/1000  inspector.go, round half-UP
//
// The sweep below is the one that falsified the first comment: it walks
// every success rate a real scan can produce, which is where the values
// actually come from (adversarial mission-r6 MEDIUM).
func TestRoundMatchesCPythonOverEveryReachableRate(t *testing.T) {
	type rateCase struct {
		V float64 `json:"v"`
		N int     `json:"n"`
	}
	var cases []rateCase
	for total := 1; total <= 400; total++ {
		for _, done := range []int{1, 9, 13, total / 2, total} {
			if done < 0 || done > total {
				continue
			}
			cases = append(cases, rateCase{float64(done) / float64(total), 4})
			cases = append(cases, rateCase{float64(done) / float64(total), 3})
		}
	}
	// The exact half-way values, which are where half-to-even and
	// half-up disagree and where scaling-then-rounding goes wrong.
	for i := 5; i < 4000; i += 10 {
		cases = append(cases, rateCase{float64(i) / 10000, 3})
	}
	cases = append(cases,
		rateCase{0.6675, 3}, rateCase{0.0625, 3}, rateCase{0.8885, 3},
		rateCase{0.1235, 3}, rateCase{1.0 / 160, 4}, rateCase{13.0 / 160, 4},
		rateCase{9.0 / 160, 4}, rateCase{5.0 / 32, 4},
	)

	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"print(json.dumps([round(c['v'], c['n'])\n"+
			"                  for c in json.loads(sys.argv[1])]))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []float64
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var diffs []string
	for i, c := range cases {
		if got := Round(c.V, c.N); got != want[i] {
			if len(diffs) < 8 {
				diffs = append(diffs, fmt.Sprintf("round(%v, %d): go %v py %v",
					c.V, c.N, got, want[i]))
			}
		}
	}
	if len(diffs) > 0 {
		t.Fatalf("Round diverges from CPython on %d of %d values; first few:\n  %v",
			len(diffs), len(cases), diffs)
	}

	// Anti-vacuity: prove the corpus CAN separate the implementations by
	// running the two spellings Round replaced over it. If neither of
	// them fails here, this test could not have caught the finding.
	var evenDiff, upDiff int
	for i, c := range cases {
		scale := 1.0
		for k := 0; k < c.N; k++ {
			scale *= 10
		}
		if roundToEven(c.V*scale)/scale != want[i] {
			evenDiff++
		}
		if float64(int64(c.V*scale+0.5))/scale != want[i] {
			upDiff++
		}
	}
	if evenDiff == 0 {
		t.Error("the corpus cannot separate math.RoundToEven(f*scale)/scale " +
			"from round(): the scans.go finding is not pinned")
	}
	if upDiff == 0 {
		t.Error("the corpus cannot separate int64(f*scale+0.5)/scale from " +
			"round(): the inspector.go finding is not pinned")
	}
	t.Logf("corpus separates the two wrong spellings on %d and %d of %d values",
		evenDiff, upDiff, len(cases))
}

// roundToEven is the spelling scans.go used, kept HERE only so the
// anti-vacuity check above has something to run. It must never come back
// to production.
func roundToEven(f float64) float64 {
	i, frac := float64(int64(f)), f-float64(int64(f))
	switch {
	case frac > 0.5 || frac < -0.5:
		if f > 0 {
			return i + 1
		}
		return i - 1
	case frac == 0.5 || frac == -0.5:
		if int64(i)%2 == 0 {
			return i
		}
		if f > 0 {
			return i + 1
		}
		return i - 1
	}
	return i
}

// Round must leave the non-finite alone rather than mangling or
// refusing them, which is what CPython's round() does.
func TestRoundLeavesNonFiniteAlone(t *testing.T) {
	for _, v := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		got := Round(v, 3)
		if Repr(got) != Repr(v) {
			t.Errorf("Round(%s, 3) = %s", Repr(v), Repr(got))
		}
	}
}
