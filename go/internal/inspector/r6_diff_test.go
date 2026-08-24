package inspector

import (
	"context"
	"encoding/json"
	"math"
	"os/exec"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// TestAssessGoalAlignmentParseFallback had one fixture ("not a number")
// and asserted 0.5. Both runtimes return 0.5 for it, so it could not
// fail on any of the four heads below — a corpus that cannot separate
// (adversarial mission-r6, LENS 1 head 3).
//
// Python is `float(resp.content.strip())` under `except (ValueError,
// TypeError): return 0.5`. ParseFloat(TrimSpace(...)) is a different
// function, and the number it produces is averaged into
// alignment_score_avg and written to inspection-log.jsonl, which both
// runtimes read.
func TestAlignmentParseMatchesCPython(t *testing.T) {
	replies := []string{
		// str.strip() covers U+001C..U+001F; TrimSpace does not.
		"\u001c0.8", "0.8\u001f", "\u001e0.8", "\u001d0.8",
		// ...and the wide spaces both DO strip, so the corpus carries
		// the agreeing half of that boundary too.
		"\u00a00.8", "\u30000.8", "\u000b0.8", "\u20280.8",
		// float() accepts any Unicode decimal digit; ParseFloat is
		// ASCII-only.
		"\u0660.\u0665", "\u0669", "\u0966.\u096b",
		// PEP 515 underscores: BOTH accept, which is worth pinning
		// because the port's comment used to claim both refused.
		"1_000",
		// The ordinary lane.
		"0.8", " 0.8 ", "0", "1", "0.0", "1.0", ".5", "1e-3",
		"+0.8", "-0.2", "  \t\n0.95\r\n  ",
		// The refusal lane.
		"not a number", "", "   ", "0.8 is my answer", "0,8", "--1",
		// float() takes these and so does ParseFloat, so the score can
		// leave 0.0-1.0 on BOTH sides. Pinned, not fixed: the clamp is
		// Python's to add.
		"42", "-3.5",
	}
	in, err := json.Marshal(replies)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"def f(t):\n"+
			"    try:\n"+
			"        return float(t.strip())\n"+
			"    except (ValueError, TypeError):\n"+
			"        return 0.5\n"+
			"print(json.dumps([repr(f(t)) for t in json.loads(sys.argv[1])]))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var parsed, defaulted, oldDiffers int
	for i, reply := range replies {
		fake := &llm.Fake{Script: []string{reply}}
		got := AssessGoalAlignment(context.Background(), fake, "g", "s")
		if got == nil {
			t.Fatalf("a non-nil adapter must always produce a score; reply %q", reply)
		}
		if repr := pyval.Repr(*got); repr != want[i] {
			t.Errorf("the ALIGNMENT score diverges — this number is averaged "+
				"into alignment_score_avg and stored\n  reply %q\n  go %s\n  py %s",
				reply, repr, want[i])
		}
		if want[i] == "0.5" {
			defaulted++
		} else {
			parsed++
		}
		// Anti-vacuity, by running the pre-fix spelling rather than by
		// counting fixture shapes.
		old := 0.5
		if f, e := strconv.ParseFloat(strings.TrimSpace(reply), 64); e == nil {
			old = f
		}
		if pyval.Repr(old) != want[i] {
			oldDiffers++
		}
	}
	if parsed == 0 || defaulted == 0 {
		t.Fatalf("corpus reaches only one arm: parsed=%d defaulted=%d",
			parsed, defaulted)
	}
	if oldDiffers < 4 {
		t.Fatalf("the pre-fix parse matches CPython on all but %d of %d "+
			"replies: this corpus could not have caught the finding",
			oldDiffers, len(replies))
	}
	t.Logf("corpus separates the pre-fix parse on %d of %d replies",
		oldDiffers, len(replies))
}

// The NAMED divergence, pinned so it cannot drift silently. Go's
// ParseFloat accepted "nan"/"inf"/"-inf" with a nil error, so a judge
// reply of "nan" became AlignmentScoreAvg and saveReport's json.Marshal
// then failed with "unsupported value: NaN" — the whole inspection row
// lost. SafeFloat returns the 0.5 default instead. CPython stores the
// non-finite; this port refuses it, per pyjson.RefuseNonFinite's stance.
// Owed to the Python side: a safe_float there makes both agree.
func TestNonFiniteAlignmentRepliesAreTheNamedDivergence(t *testing.T) {
	for _, reply := range []string{"nan", "inf", "-inf", "Infinity", "NaN"} {
		fake := &llm.Fake{Script: []string{reply}}
		got := AssessGoalAlignment(context.Background(), fake, "g", "s")
		if got == nil || *got != 0.5 {
			t.Fatalf("reply %q must fall back to the 0.5 default, got %v",
				reply, got)
		}
	}
	// The half of the finding that is not a divergence at all: whatever
	// the score is, saveReport must be able to write it. A NaN average
	// used to take the entire row down with it.
	r := InspectionReport{AlignmentScoreAvg: math.NaN()}
	if _, err := json.Marshal(r); err == nil {
		t.Fatal("json.Marshal accepted a NaN average — the failure mode this " +
			"test guards against no longer reproduces, so the guard above " +
			"needs re-justifying rather than keeping")
	}
}

// round3 was float64(int64(f*1000+0.5))/1000 — round half-UP, under no
// test at all. Its output is alignment_score_avg in inspection-log.jsonl,
// which both runtimes read, and Python is round(avg, 3): half-to-even on
// the EXACT value of the double (adversarial mission-r6 MEDIUM).
//
// The corpus is the averages the call site can actually produce —
// sum-of-scores over session count — not arbitrary floats, because that
// is where the value comes from.
func TestAlignmentAverageRoundsLikeCPython(t *testing.T) {
	var vals []float64
	for n := 1; n <= 24; n++ {
		for k := 0; k <= n*20; k++ {
			vals = append(vals, (float64(k)/20)/float64(n))
		}
	}
	// The exact half-way values at the third decimal, where half-up and
	// half-to-even part company.
	for i := 5; i < 1000; i += 10 {
		vals = append(vals, float64(i)/10000)
	}
	in, err := json.Marshal(vals)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"print(json.dumps([round(v, 3) for v in json.loads(sys.argv[1])]))",
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

	var diffs, oldDiffers int
	for i, v := range vals {
		if got := round3(v); got != want[i] {
			if diffs < 5 {
				t.Errorf("alignment_score_avg diverges: round3(%v) = %v, "+
					"CPython round(v, 3) = %v", v, got, want[i])
			}
			diffs++
		}
		if float64(int64(v*1000+0.5))/1000 != want[i] {
			oldDiffers++
		}
	}
	if oldDiffers < 5 {
		t.Fatalf("the pre-fix half-up spelling matches CPython on all but "+
			"%d of %d averages: this corpus could not have caught the finding",
			oldDiffers, len(vals))
	}
	t.Logf("corpus separates the pre-fix half-up spelling on %d of %d averages",
		oldDiffers, len(vals))
}
