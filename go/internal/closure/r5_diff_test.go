package closure

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Three r5 findings landed in this file and none of them had coverage.
// Each test here drives the real CPython function it ports.

func srcDirClosure(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// VerdictFirstSummary exists so the stored prose can only ELABORATE the
// flag, never contradict it — the regression run d2f4e2f4 produced. Its
// opener regex used RE2's `\s` (five code points) where Python's re
// reads twenty-nine, so a non-breaking space ahead of the opener left it
// in place and a complete=false verdict stored
// "Not achieved: Goal achieved. ..." (adversarial mission-r5 HIGH).
func TestVerdictFirstSummaryMatchesCPython(t *testing.T) {
	const nbsp = " "
	type vfCase struct {
		Name     string `json:"-"`
		Summary  string `json:"summary"`
		Complete bool   `json:"complete"`
		Judged   bool   `json:"judged"`
	}
	cases := []vfCase{
		// THE r5 HIGH: the whitespace the two engines disagree about,
		// in the two positions where the disagreement is visible.
		{"nbsp inside the opener", "The" + nbsp + "goal was achieved. the file exists.", true, true},
		{"nbsp before the opener", nbsp + "Goal achieved. the file exists.", true, true},
		{"nbsp before the opener, NOT achieved", nbsp + "Goal achieved. the file exists.", false, true},
		{"nbsp after the opener", "Goal achieved." + nbsp + "the file exists.", true, true},
		{"an ideographic space before", "　Goal achieved. done.", true, true},
		{"a file separator before", "\x1cGoal achieved. done.", true, true},
		{"a line separator inside", "Goal achieved. done.", true, true},

		// mission-r6: every r5 case put its separator BEFORE or INSIDE
		// the opener, where the rebuilt REGEX eats it — so the corpus
		// could not see that the `.strip()` one line below the regex was
		// still strings.TrimSpace, on the stdlib's narrower set. These
		// reach the strip and not the regex.
		{"a trailing file separator, no opener", "the file exists.\x1c", true, true},
		{"a leading file separator, no opener", "\x1cthe file exists.", true, true},
		{"a trailing separator after a stripped opener",
			"Goal achieved. the file exists.\x1f", true, true},
		{"a trailing separator, not achieved",
			"Goal achieved. the file exists.\x1e", false, true},
		{"only separators", "\x1c\x1d", true, true},

		// The ordinary ASCII lane, which already agreed.
		{"a plain opener", "Goal achieved. the file exists.", true, true},
		{"a not-achieved opener", "Goal not achieved. no file.", false, true},
		{"the fully variant", "The goal was fully achieved. done.", true, true},
		{"no opener at all", "the file exists.", true, true},
		{"empty", "", true, true},
		{"unjudged", "Goal achieved. done.", true, false},
		{"unjudged and incomplete", "Goal achieved. done.", false, false},
	}

	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"print(json.dumps([cv._verdict_first_summary("+
			"c['summary'], complete=c['complete'], judged=c['judged'])"+
			" for c in json.loads(sys.argv[1])]))",
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

	var stripped, contradictionPossible int
	for i, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			got := VerdictFirstSummary(c.Summary, c.Complete, c.Judged)
			if got != want[i] {
				t.Errorf("VerdictFirstSummary diverges\n  in %q\n  go %q\n  py %q",
					c.Summary, got, want[i])
			}
		})
		if strings.Contains(strings.ToLower(c.Summary), "achieved") &&
			!strings.Contains(strings.ToLower(want[i][strings.Index(want[i], ":")+1:]),
				"achieved") {
			stripped++
		}
		// The shape the function exists to prevent: a NOT-achieved
		// verdict whose prose still opens by claiming achievement.
		if !c.Complete && strings.Contains(strings.ToLower(c.Summary), "goal achieved") {
			contradictionPossible++
		}
	}
	if stripped == 0 {
		t.Fatal("no case where the opener is actually stripped: the corpus " +
			"cannot tell the two whitespace classes apart")
	}
	if contradictionPossible == 0 {
		t.Fatal("no complete=false case with an achievement opener: the " +
			"regression this function exists to stop is not pinned")
	}

	// mission-r6 anti-vacuity: the regex and the strip are two separate
	// whitespace decisions one line apart, and r5 rebuilt only the first.
	// Prove the corpus reaches the SECOND by running the pre-fix spelling
	// of it — counting fixtures would not have caught this, since r5's
	// fixtures all carried separators the regex happens to eat.
	var stripDiffers int
	for i, c := range cases {
		body := strings.TrimSpace(verdictOpenerRe.ReplaceAllString(c.Summary, ""))
		if !strings.Contains(want[i], body) || body != pytext.Strip(
			verdictOpenerRe.ReplaceAllString(c.Summary, "")) {
			stripDiffers++
		}
	}
	if stripDiffers == 0 {
		t.Fatal("no case where the trailing strip does any work the regex " +
			"has not already done: the second whitespace decision in this " +
			"function is unpinned")
	}
	t.Logf("the strip (not the regex) is decisive on %d of %d cases",
		stripDiffers, len(cases))
}

// Fingerprint's doc claims BYTE-PARITY with CPython closure_fingerprint.
// It normalised with strings.Fields (25 code points) where Python uses
// str.split() (29), so a captured stderr carrying U+001C..U+001F
// fingerprinted differently. This is the §9.3 restart-convergence
// identity: a divergence means one runtime declares thesis-refuted while
// the other keeps restarting on identical evidence (mission-r5 MEDIUM).
func TestFingerprintMatchesCPython(t *testing.T) {
	corpus := [][]string{
		{"a\x1cb"}, // THE finding
		{"pytest -q => exit 1: assert\x1ffailed"}, // realistic
		{"a\x1cb", "c d"},                         // sorted-join path
		{"pytest -q => exit 1: boom"},             // plain ASCII
		{"  leading and trailing  "},              // normalisation
		{"tabs\tand\nnewlines"},                   // the agreeing set
		{"a b"},                                   // nbsp: in BOTH sets
		{"x"},
	}
	in, err := json.Marshal(corpus)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"r=[]\n"+
			"for checks in json.loads(sys.argv[1]):\n"+
			"    V=type('V',(),{'failed_checks':checks})\n"+
			"    r.append(cv.closure_fingerprint(V()))\n"+
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

	var narrowSetCases int
	for i, checks := range corpus {
		got := Fingerprint(Verdict{FailedChecks: checks})
		if got != want[i] {
			t.Errorf("Fingerprint diverges for %q\n  go %s\n  py %s",
				checks, got, want[i])
		}
		for _, c := range checks {
			if strings.ContainsAny(c, "\x1c\x1d\x1e\x1f") {
				narrowSetCases++
				break
			}
		}
	}
	// strings.Fields and str.split() agree on space/tab/newline. Only
	// the four separators separate them.
	if narrowSetCases == 0 {
		t.Fatal("no case carrying U+001C..U+001F: strings.Fields and " +
			"str.split() agree on everything else, so nothing is pinned")
	}

	// An empty verdict is "" on both sides and must stay no-signal.
	if got := Fingerprint(Verdict{}); got != "" {
		t.Fatalf("an empty verdict must fingerprint to the empty string, got %q", got)
	}
}

// closure_verify skips a check whose command strips to empty
// (safe_str is str(value).strip()), and reading the field raw let "   "
// reach sh -c, which exits 0 — a PASSING check CPython never ran
// (adversarial mission-r5 MEDIUM).
//
// The r5 fix dropped those entries inside parsePlanChecks, and THAT was
// a second divergence: Python slices the RAW list with checks[:5] and
// then skips empties inside the loop, so filtering first silently moved
// the cap onto the filtered list (mission-r6 HIGH). Both facts are
// pinned here, against the real CPython slice-then-skip order.
func TestPlanCheckCapAppliesBeforeTheEmptySkipLikeCPython(t *testing.T) {
	out, perr := exec.Command("python3", "-c",
		"import json,sys; sys.path.insert(0, sys.argv[1])\n"+
			"from llm_parse import safe_str, safe_list\n"+
			"checks = json.loads(sys.argv[2])['checks']\n"+
			"checks = safe_list(checks, element_type=dict)\n"+
			"ran = []\n"+
			"for _i, c in enumerate(checks[:5]):\n"+
			"    cmd = safe_str(c.get('command', ''))\n"+
			"    if not cmd: continue\n"+
			"    ran.append(cmd)\n"+
			"print(json.dumps([ran, len(checks)]))",
		srcDirClosure(t), sixCheckPlan).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []json.RawMessage
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	var pyRan []string
	var pyLen int
	if err := json.Unmarshal(want[0], &pyRan); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(want[1], &pyLen); err != nil {
		t.Fatal(err)
	}
	// The fixture must actually separate the two orders, or this test
	// passes while pinning nothing: it needs MORE than five entries and
	// a blank command inside the first five.
	if len(pyRan) != 4 {
		t.Fatalf("the fixture no longer separates the two cap orders: "+
			"CPython ran %d commands, want 4 (%v)", len(pyRan), pyRan)
	}

	parsed, err := parsePlanChecks(sixCheckPlan)
	if err != nil {
		t.Fatal(err)
	}
	if len(parsed) != pyLen {
		t.Errorf("parsePlanChecks must return what safe_list returns "+
			"(the no_checks_generated gate counts it): go %d, py %d",
			len(parsed), pyLen)
	}

	// Replay the run loop's own cap-then-skip over the parsed list.
	capped := parsed
	if len(capped) > 5 {
		capped = capped[:5]
	}
	var goRan []string
	for _, c := range capped {
		if c.command == "" {
			continue
		}
		goRan = append(goRan, c.command)
	}
	if strings.Join(goRan, "|") != strings.Join(pyRan, "|") {
		t.Errorf("a different set of LLM-authored shell commands runs\n"+
			"  go %v\n  py %v", goRan, pyRan)
	}
}

// sixCheckPlan has a blank command FIRST and six checks total, which is
// the only shape where cap-then-skip and skip-then-cap disagree.
const sixCheckPlan = `{"checks":[
	{"description":"blank","command":"   "},
	{"description":"  padded  ","command":"  echo 1  "},
	{"description":"two","command":"echo 2"},
	{"description":"three","command":"echo 3"},
	{"description":"four","command":"echo 4"},
	{"description":"five","command":"echo 5"}]}`

// The strip half of the same Python line, still load-bearing: an
// unstripped command changes the stored failed_checks signature and
// therefore Fingerprint.
func TestPlanCheckFieldsStripLikeCPython(t *testing.T) {
	out, perr := exec.Command("python3", "-c",
		"import sys; sys.path.insert(0, sys.argv[1])\n"+
			"from llm_parse import safe_str\n"+
			"print(repr(safe_str('   ')), repr(safe_str('  pytest -q  ')),"+
			" repr(safe_str(42)))",
		srcDirClosure(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	if got := strings.TrimSpace(string(out)); got != `'' 'pytest -q' '42'` {
		t.Fatalf("safe_str has moved (%s) — the premise of this test is gone", got)
	}

	checks, err := parsePlanChecks(`{"checks":[
		{"description":"blank","command":"   "},
		{"description":"  padded  ","command":"  pytest -q  "},
		{"description":42,"command":"true"}]}`)
	if err != nil {
		t.Fatal(err)
	}
	if len(checks) != 3 {
		t.Fatalf("nothing may be dropped at parse time: got %d %+v", len(checks), checks)
	}
	if checks[0].command != "" {
		t.Errorf("a whitespace-only command must strip to empty so the run "+
			"loop's skip fires; got %q", checks[0].command)
	}
	if checks[1].command != "pytest -q" || checks[1].description != "padded" {
		t.Errorf("safe_str strips both fields: %+v", checks[1])
	}
	if checks[2].description != "42" {
		t.Errorf("safe_str coerces a non-string description: %q — a bare "+
			"Go assertion gives \"\" and that string is persisted in "+
			"check_results", checks[2].description)
	}
}

// FailedCheckSignature is the material Fingerprint hashes, and it had
// the SAME name-claims-a-differential problem: its old test asserted two
// constants. Python is
//
//	cmd = safe_str(row.get("command", ""))[:200]
//	out = " ".join((safe_str(stderr) + " " + safe_str(stdout)).split())
//
// so the command is STRIPPED before the 200-slice — which the Go port
// was not doing (found while writing this file). An unstripped command
// forks the §9.3 restart identity through Fingerprint.
func TestFailedCheckSignatureMatchesCPython(t *testing.T) {
	type sigCase struct {
		Name     string `json:"-"`
		Command  string `json:"command"`
		ExitCode int    `json:"exit_code"`
		Stderr   string `json:"stderr"`
		Stdout   string `json:"stdout"`
	}
	cases := []sigCase{
		{"the ordinary shape", "pytest -q", 1, "boom  err", "FAILED  x"},
		{"no output at all", "grep -q done out.txt", 1, "", ""},
		{"a padded command", "  pytest -q  ", 1, "boom", ""},
		{"a command padded with separators", "\x1cpytest -q\x1f", 1, "boom", ""},
		{"separators inside the output", "pytest -q", 1, "assert\x1cfailed", ""},
		{"separators around the output", "pytest -q", 1, "\x1cboom\x1f", ""},
		{"whitespace-only output is no output", "pytest -q", 1, "   ", "  "},
		{"separator-only output is no output", "pytest -q", 1, "\x1c", "\x1f"},
		{"exit zero", "true", 0, "", ""},
		{"a negative exit code", "killed", -1, "sig", ""},
	}
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import closure_verify as cv\n"+
			"print(json.dumps([cv._failed_check_signature(c)"+
			" for c in json.loads(sys.argv[1])]))",
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
	var separatorCases int
	for i, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			got := FailedCheckSignature(CheckResult{
				Command: c.Command, ExitCode: c.ExitCode,
				Stderr: c.Stderr, Stdout: c.Stdout,
			})
			if got != want[i] {
				t.Errorf("signature diverges\n  go %q\n  py %q", got, want[i])
			}
		})
		if strings.ContainsAny(c.Command+c.Stderr+c.Stdout, "\x1c\x1d\x1e\x1f") {
			separatorCases++
		}
	}
	if separatorCases == 0 {
		t.Fatal("no case carrying U+001C..U+001F: the strip/split set is not pinned")
	}
}
