package closure

import (
	"context"
	"encoding/json"
	"os/exec"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// The verdict's gaps and summary are written verbatim into
// closure_verdicts.jsonl and gaps feeds DetectBehavioralGap, which can
// flip `complete`. Python is
//
//	gaps    = [safe_str(g) for g in safe_list(verdict_data.get("gaps")) if g]
//	summary = safe_str(verdict_data.get("summary", ""))
//
// and the Go port had a bare `.(string)` on both plus a deliberate
// bare-string arm on gaps. Three of the four rows below separated the
// two runtimes (adversarial mission-r6 MEDIUM).
func TestVerdictGapsAndSummaryMatchCPython(t *testing.T) {
	verdicts := []string{
		// THE finding: safe_list's element_type defaults to str, so a
		// bare string is not a list and yields [].
		`{"complete":false,"confidence":0.9,"gaps":"the one gap","summary":"no"}`,
		// safe_str strips each surviving element...
		`{"complete":false,"confidence":0.9,"gaps":["  gap one  "],"summary":"no"}`,
		// ...and `if g` drops "" BEFORE the strip, so a whitespace-only
		// gap survives the filter and lands as "".
		`{"complete":false,"confidence":0.9,"gaps":["   "],"summary":"no"}`,
		`{"complete":false,"confidence":0.9,"gaps":[""],"summary":"no"}`,
		// safe_str COERCES; a bare assertion zeroes.
		`{"complete":false,"confidence":0.9,"gaps":[],"summary":42}`,
		`{"complete":false,"confidence":0.9,"gaps":[1,"two",null],"summary":"no"}`,
		// The separator set again, in both fields.
		`{"complete":false,"confidence":0.9,"gaps":["gap\u001c"],"summary":"no\u001f"}`,
		// The ordinary shapes, which already agreed.
		`{"complete":false,"confidence":0.9,"gaps":["a","b"],"summary":"two gaps"}`,
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"done"}`,
		`{"complete":true,"confidence":0.9,"summary":"done"}`,
		`{"complete":true,"confidence":0.9,"gaps":null,"summary":null}`,
	}
	in, err := json.Marshal(verdicts)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"from llm_parse import safe_str, safe_list\n"+
			"r=[]\n"+
			"for raw in json.loads(sys.argv[1]):\n"+
			"    vd = json.loads(raw)\n"+
			"    gaps = [safe_str(g) for g in safe_list(vd.get('gaps')) if g]\n"+
			"    summary = safe_str(vd.get('summary', ''))\n"+
			"    r.append([gaps, summary])\n"+
			"print(json.dumps(r))",
		string(in), srcDirClosure(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []struct {
		Gaps    []string
		Summary string
	}
	var rawWant [][]json.RawMessage
	if err := json.Unmarshal(out, &rawWant); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	want = make([]struct {
		Gaps    []string
		Summary string
	}, len(rawWant))
	for i, row := range rawWant {
		if err := json.Unmarshal(row[0], &want[i].Gaps); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(row[1], &want[i].Summary); err != nil {
			t.Fatal(err)
		}
	}

	var emptyGaps, nonEmptyGaps int
	for i, vd := range verdicts {
		fake := &llm.Fake{Script: []string{planJSON("true"), vd}}
		v := Verify(context.Background(), fake, "goal", nil,
			Options{WorkspacePath: t.TempDir()})
		if strings.Join(v.Gaps, "\x00") != strings.Join(want[i].Gaps, "\x00") {
			t.Errorf("gaps diverge — this list is stored AND feeds the "+
				"behavioral-gap downgrade\n  in %s\n  go %q\n  py %q",
				vd, v.Gaps, want[i].Gaps)
		}
		// The COERCION is what this test pins, not the whole pipeline:
		// Verify runs the stored summary through VerdictFirstSummary and
		// the ungrounded-False cap, and the CPython probe above
		// deliberately reproduces only the two safe_* calls. Containment
		// is therefore the honest assertion — and it still separates the
		// finding, because a bare `.(string)` turns a numeric summary
		// into "" and "" is contained in everything.
		if want[i].Summary == "" {
			if strings.Contains(v.Summary, "42") {
				t.Errorf("summary %q kept a value CPython coerced away", v.Summary)
			}
		} else if !strings.Contains(v.Summary, want[i].Summary) {
			t.Errorf("the coerced summary is missing from the stored one\n"+
				"  in %s\n  go %q\n  py %q", vd, v.Summary, want[i].Summary)
		}
		if want[i].Summary != "" && !strings.Contains(v.Summary, want[i].Summary) {
			t.Errorf("summary coercion diverges for %s", vd)
		}
		if len(want[i].Gaps) == 0 {
			emptyGaps++
		} else {
			nonEmptyGaps++
		}
	}
	if emptyGaps == 0 || nonEmptyGaps == 0 {
		t.Fatalf("corpus reaches only one shape: empty=%d non-empty=%d",
			emptyGaps, nonEmptyGaps)
	}
}

// CheckOutcome's old test had thirteen all-ASCII stderr fixtures, so
// strings.ToLower and str.lower() could not possibly differ on it —
// a corpus that cannot separate. str.lower() expands U+0130 to two
// runes, which BREAKS an ASCII substring match that Go's simple mapping
// preserves, so the two runtimes classify the same stderr as fail vs
// inconclusive and move checks_passed / inconclusive_count /
// failed_checks (adversarial mission-r6 LOW).
func TestCheckOutcomeMatchesCPythonOnNonASCIIStderr(t *testing.T) {
	type ocCase struct {
		Exit   int    `json:"exit"`
		Stderr string `json:"stderr"`
	}
	cases := []ocCase{
		// THE finding: the dotted capital I inside a keyword.
		{1, "TİMED OUT waiting for the server"},
		{1, "PERMİSSİON DENİED"},
		{1, "COMMAND NOT FOUND: pytest"},
		// The ASCII twins, so the corpus carries the pair.
		{1, "TIMED OUT waiting for the server"},
		{1, "PERMISSION DENIED"},
		{1, "command not found: pytest"},
		// The other lanes.
		{0, ""},
		{1, "AssertionError: expected 3 got 4"},
		{127, "anything"},
		{1, `  File "<string>", line 1\n    SyntaxError: bad`},
		{1, "not a git repository"},
	}
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"print(json.dumps([cv._check_outcome(exit_code=c['exit'],\n"+
			"                                    stderr=c['stderr'])\n"+
			"                  for c in json.loads(sys.argv[1])]))",
		string(in), srcDirClosure(t)).Output()
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

	seen := map[string]int{}
	var nonASCII int
	for i, c := range cases {
		if got := CheckOutcome(c.Exit, c.Stderr); got != want[i] {
			t.Errorf("CheckOutcome diverges — a fail and an inconclusive are "+
				"DIFFERENT evidence, not different labels\n  exit %d stderr %q\n"+
				"  go %s\n  py %s", c.Exit, c.Stderr, got, want[i])
		}
		seen[want[i]]++
		for _, r := range c.Stderr {
			if r > 127 {
				nonASCII++
				break
			}
		}
	}
	if len(seen) < 3 {
		t.Fatalf("corpus does not reach all three outcomes: %v", seen)
	}
	if nonASCII == 0 {
		t.Fatal("no case with a non-ASCII stderr: ToLower and str.lower() " +
			"cannot differ on ASCII, so the corpus pins nothing")
	}
}
