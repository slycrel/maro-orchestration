package closure

import (
	"encoding/json"
	"os/exec"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// CPython's re.IGNORECASE folds U+0130 and U+0131 onto `i`; Go's (?i)
// does not, and `i` is the ONLY one of the 36 ASCII letters and digits
// where the two engines disagree (pytext.PyFoldI, and the exhaustive
// sweep in pytext/foldi_diff_test.go).
//
// These patterns are not cosmetic. classifySegment decides a check's
// MODALITY, which is recorded on every check_results row and read by the
// behavioral-gap branch; runtimeGapAdmissionRe decides whether a
// complete=true verdict is contradicted by its own prose. A miss and a
// false hit are different bugs here, which is why the rows below drive
// both directions.
//
// Driven against the real _classify_probe_segment, _verdict_first_summary
// and _detect_behavioral_gap, so no arm is argued from a reading of the
// pattern.

// Each row names the pattern it exists for, so a later trim cannot
// remove the only case reaching one of them without saying so.
var foldSegmentCorpus = []struct{ pattern, seg string }{
	// browser: the i in selenium, chromium, playwright, firefox.
	{"browser", "run selenium tests"},
	{"browser", "run selenıum tests"},
	{"browser", "RUN SELENİUM TESTS"},
	{"browser", "RUN SELENIUM TESTS"},
	{"browser", "chromıum --headless x"},
	{"browser", "playwrıght test"},

	// http: the i in httpie. curl/wget/http carry none.
	{"http", "httpie GET /x"},
	{"http", "httpıe GET /x"},
	{"http", "HTTPİE GET /X"},
	{"http", "HTTPIE GET /X"},

	// process: the i is in `timeout`, and nowhere else in the pattern --
	// `./`, `go run`, `node ` and `python` carry none. Found by asking
	// PyFoldI rather than by reading the alternation, which is how the
	// two patterns with NO i in this file came to be left unwrapped.
	// These must NOT contain `./`, or the first alternative decides and
	// the timeout branch is never consulted -- which is what the first
	// draft did (`timeout 30 ./run.sh &`), and the mutation sweep caught
	// it: emptying the wrap killed nothing because the row had a second
	// way to reach "process" (L41, an over-determined fixture).
	// Also note the shape: `timeout [0-9]+\s+\S+\s*&` wants the `&`
	// directly after ONE non-space run, so `timeout 30 sleep 5 &` is
	// static in both engines and proves nothing either.
	{"process", "timeout 30 sleep &"},
	{"process", "tımeout 30 sleep &"},
	{"process", "TİMEOUT 30 SLEEP &"},
	{"process", "TIMEOUT 30 SLEEP &"},
	{"static", "timeout 30 sleep 5 &"},
	{"process", "timeout 30 ./run.sh &"},

	// staticHints: the i in tail, build, noEmit, find. This one runs
	// BEFORE the test-runner branch, so a fold miss here does not merely
	// lose "static" -- it lets `go build ./cmd/x` fall through to
	// "process" and claim the run exercised the artifact.
	{"static", "go build ./cmd/x"},
	{"static", "go buıld ./cmd/x"},
	{"static", "GO BUİLD ./CMD/X"},
	{"static", "GO BUILD ./CMD/X"},
	{"static", "tail -n 5 log.txt"},
	{"static", "taıl -n 5 log.txt"},

	// nonExecRunnerFlags: the i in `list`. Direction matters -- a fold
	// miss here classifies a NON-executing pytest run as "process", so
	// the pass-audit credits an execution that never happened.
	{"nonexec", "pytest --list"},
	{"nonexec", "pytest --lıst"},
	{"nonexec", "PYTEST --LİST"},
	{"nonexec", "PYTEST --LIST"},
	{"nonexec", "pytest --collect-only"},

	// The two patterns with no `i` at all, so the corpus still reaches
	// them and a future edit that ADDS an i to either is caught by the
	// row rather than by nothing.
	{"ws", "wscat -c wss://x/y"},
	{"testRunner", "pytest tests/"},

	// Ordinary ASCII, so the corpus is not all edge cases.
	{"static", "grep -n foo bar.go"},
	{"process", "./bin/server"},
	{"", ""},
}

func TestClassifySegmentFoldsTheTurkishILikeCPython(t *testing.T) {
	segs := make([]string, len(foldSegmentCorpus))
	for i, c := range foldSegmentCorpus {
		segs[i] = c.seg
	}
	in, err := json.Marshal(segs)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"print(json.dumps([cv._classify_probe_segment(s)\n"+
			"                  for s in json.loads(sys.argv[1])]))",
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

	seen := map[string]bool{}
	for i, c := range foldSegmentCorpus {
		if got := classifySegment(c.seg); got != want[i] {
			t.Errorf("MODALITY diverges for the %s pattern — this is "+
				"recorded on every check_results row and read by the "+
				"behavioral-gap branch\n  in %q\n  go %s\n  py %s",
				c.pattern, c.seg, got, want[i])
		}
		seen[want[i]] = true
	}
	// A corpus that reached only one answer would pass while measuring
	// nothing about the branch order this function is mostly made of.
	if len(seen) < 4 {
		t.Fatalf("the corpus reaches only %d distinct modalities (%v); it "+
			"cannot separate the branches it claims to cover", len(seen), seen)
	}
}

func TestVerdictOpenerFoldsTheTurkishILikeCPython(t *testing.T) {
	// The opener is STRIPPED from the summary that reaches disk as
	// goal_verdict_summary. A fold miss leaves "The goal ıs achieved"
	// in the body, so the stored prose reads "Not achieved. The goal ıs
	// achieved." -- the exact contradiction the opener rewrite exists to
	// prevent (the d2f4e2f4 incident in _verdict_first_summary's doc).
	summaries := []string{
		"The goal is achieved. Everything works.",
		"The goal ıs achieved. Everything works.",
		"THE GOAL İS ACHIEVED. EVERYTHING WORKS.",
		"THE GOAL IS ACHIEVED. EVERYTHING WORKS.",
		"the goal was achieved, mostly",
		"Downgraded to not-achieved — left alone",
		"",
	}
	in, err := json.Marshal(summaries)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"r=[]\n"+
			"for s in json.loads(sys.argv[1]):\n"+
			"    r.append(cv._verdict_first_summary(s, complete=False, judged=True))\n"+
			"print(json.dumps(r))",
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
	for i, s := range summaries {
		if got := VerdictFirstSummary(s, false, true); got != want[i] {
			t.Errorf("goal_verdict_summary diverges\n  in %q\n  go %q\n  py %q",
				s, got, want[i])
		}
	}
}

func TestRuntimeGapAdmissionFoldsTheTurkishILikeCPython(t *testing.T) {
	// modality_dist is all-static ON PURPOSE: the signal "fires only
	// when complete=True AND modality_distribution has zero behavioral
	// probes" (_detect_behavioral_gap's own doc). The first draft passed
	// {"process": 1}, which suppresses the branch entirely -- every row
	// answered "" and the comparison agreed on nothing. The vacuity floor
	// at the bottom is what caught it, which is the whole reason it is
	// there.
	//
	// The i lives in `validation` and `verification`. This decides
	// whether a complete=true verdict is contradicted by its own prose,
	// so a fold miss lets a run claim completion while admitting in the
	// same sentence that nothing was validated.
	summaries := []string{
		"runtime validation was skipped",
		"runtime valıdation was skipped",
		"RUNTIME VALİDATION WAS SKIPPED",
		"RUNTIME VALIDATION WAS SKIPPED",
		"runtime verıfication was skipped",
		"everything was exercised end to end",
	}
	in, err := json.Marshal(summaries)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"r=[]\n"+
			"for s in json.loads(sys.argv[1]):\n"+
			"    r.append(cv._detect_behavioral_gap(complete=True, summary=s,\n"+
			"             gaps=[], modality_dist={'static': 3}))\n"+
			"print(json.dumps(r))",
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
	var fired, quiet int
	for i, s := range summaries {
		got := DetectBehavioralGap(true, s, nil, map[string]int{"static": 3})
		if got != want[i] {
			t.Errorf("the behavioral-gap reason diverges — one runtime "+
				"contradicts a complete=true verdict and the other does "+
				"not\n  in %q\n  go %q\n  py %q", s, got, want[i])
		}
		if want[i] == "" {
			quiet++
		} else {
			fired++
		}
	}
	if fired == 0 || quiet == 0 {
		t.Fatalf("the corpus reaches only one answer (fired=%d quiet=%d)",
			fired, quiet)
	}
}

// The two patterns in modality.go that are deliberately NOT wrapped.
//
// pytext's own census (TestNoProductionPatternFoldsAnUnwrappedI) cannot
// certify these: both are built through wordBounded/urlBounded, so the
// literal reaching MustCompile is opaque to a scan that reads string
// literals, and the census correctly refuses to call an unreadable
// pattern safe. That refusal is right, and the answer is not to promise
// it in an allowlist reason -- it is to make the claim mechanical, here,
// where the pattern is.
//
// If anyone adds an alternative containing an `i` to either, this fails
// and names it, which is the only thing standing between "no i today"
// and "no i forever".
func TestTheTwoUnwrappedPatternsReallyCarryNoI(t *testing.T) {
	for name, p := range map[string]string{
		"wsPattern":         wsPattern,
		"testRunnerPattern": testRunnerPattern,
	} {
		if folded := pytext.PyFoldI(p); folded != p {
			t.Errorf("%s now carries a foldable `i` — CPython's IGNORECASE "+
				"matches U+0130/U+0131 for it and Go's (?i) does not, so "+
				"this pattern must be wrapped in pytext.PyFoldI and given a "+
				"row in foldSegmentCorpus\n  is     %q\n  folded %q",
				name, p, folded)
		}
	}
}
