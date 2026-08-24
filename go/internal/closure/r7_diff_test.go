package closure

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// pySrc is this repo's Python tree, relative to a Go package directory.
const pySrc = "../../../src"

const admissionProbe = `
import json, sys
sys.path.insert(0, sys.argv[1])
import closure_verify as cv
out = []
for s in json.loads(sys.stdin.read()):
    m = cv._RUNTIME_GAP_ADMISSION.search(s)
    out.append(m.group(1) if m else None)
print(json.dumps(out))
`

// admissionCorpus is the r6 table plus the cases that make it separable.
// The r6 version was five ASCII strings compared against hand-written
// booleans under the name "MatchesCPython" — a frozen snapshot wearing a
// differential's name, over a corpus that could not have shown either of
// this round's two forks (adversarial mission-r7 HIGH).
var admissionCorpus = []string{
	// The r6 five, kept.
	"Gap: runtime validation (server startup + browser connection) was not performed.",
	"the server was never started",
	"all files verified present",
	"no behavioral probes ran",
	"tests were not run",

	// Fork one, in Go's favour: Go's `\w` is a strict SUBSET of Python's,
	// so Go's `\b` sees a boundary between a non-ASCII letter and an
	// ASCII word where Python sees none. Go OVER-fires.
	"研究runtime validation was skipped",
	"füruntime check was skipped",

	// Fork two, the other direction: `no \w+...` needs Python's \w to
	// span the accented letter. CPython matches the whole phrase; an
	// ASCII \w+ stops at the 'é' and matches nothing.
	"no café was tested",
	"no naïve probe was run",
	"no résumé page was verified",

	// The ASCII controls for both, so the corpus carries the pairs.
	"no cafe was tested",
	"runtime validation was skipped",

	// The quoted TEXT matters, not just the boolean: the match is
	// repr()'d into the downgrade reason, which is scrubbed, stored on
	// the verdict row and prefixed into goal_verdict_summary. An
	// apostrophe there switches repr() to double quotes.
	"the probe wasn't run at all",
	"the suite weren't executed",

	// Multi-word windows at the {0,3} cap and one past it.
	"no a b c d was tested",
	"no a b c d e was tested",

	// Near-misses that must stay false.
	"runtime validated everything",
	"nothing was run-of-the-mill",
	"",
}

// TestRuntimeGapAdmissionMatchesCPythonLive drives the real
// _RUNTIME_GAP_ADMISSION and compares the MATCHED TEXT, not just whether
// something matched. DetectBehavioralGap quotes group 1 into the stored
// reason, so a boundary that consumes where Python's is zero-width is a
// content divergence even when both runtimes agree that there was a gap.
func TestRuntimeGapAdmissionMatchesCPythonLive(t *testing.T) {
	if _, err := os.Stat(pySrc); err != nil {
		t.Skipf("python tree not beside this checkout: %v", err)
	}
	in, err := json.Marshal(admissionCorpus)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", admissionProbe, pySrc)
	cmd.Stdin = strings.NewReader(string(in))
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []*string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(want) != len(admissionCorpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(admissionCorpus))
	}

	// Anti-vacuity: r6's pattern — Go's own \b, \w and \s — replayed over
	// the same corpus, required to lose.
	oldLost := 0
	for i, s := range admissionCorpus {
		m := oldAdmissionRe.FindStringSubmatch(s)
		var got *string
		if m != nil {
			got = &m[1]
		}
		if !samePtr(got, want[i]) {
			oldLost++
		}
	}
	if oldLost < 4 {
		t.Fatalf("r6's ASCII-class pattern differs from CPython on only %d "+
			"of %d cases: this corpus barely discriminates", oldLost, len(admissionCorpus))
	}

	for i, s := range admissionCorpus {
		m := runtimeGapAdmissionRe.FindStringSubmatch(s)
		var got *string
		if m != nil {
			got = &m[1]
		}
		if !samePtr(got, want[i]) {
			t.Errorf("admission(%q):\n go: %s\n py: %s", s, showPtr(got), showPtr(want[i]))
		}
	}
}

func samePtr(a, b *string) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

func showPtr(p *string) string {
	if p == nil {
		return "<no match>"
	}
	return "\"" + *p + "\""
}

// oldAdmissionRe is r6's spelling — Go's ASCII `\b`, `\w` and `\s` —
// kept in the test file so the corpus above is PROVED to discriminate
// rather than assumed to. Counting the right shape of fixture is not
// evidence; the pre-fix implementation losing is.
var oldAdmissionRe = regexp.MustCompile(`(?i)\b(runtime (validation|check|verification|test)|` +
	`(?:not|never|wasn'?t|weren'?t) (?:run|tested|performed|exercised|executed|verified|started|booted)|` +
	`no \w+(?:\s+\w+){0,3} (?:was |were )?(?:run|tested|performed|exercised|executed|verified|started|booted)|` +
	`unexercised runtime|no behavioral|no runtime probe|` +
	`browser connection (?:was )?not|server (?:startup|boot) (?:was )?not)\b`)

const modalityProbe = `
import json, sys
sys.path.insert(0, sys.argv[1])
import closure_verify as cv
print(json.dumps([cv._classify_probe_modality(c)
                  for c in json.loads(sys.stdin.read())]))
`

// modalityCorpus reaches every branch of the four patterns r7 rebuilt,
// with a non-ASCII neighbour on each. The label rides every
// check_results row and feeds the behavioral-gap branch, so a command
// classified "http" here and "static" there is a fork in the durable
// record AND in whether the verdict gets downgraded.
var modalityCorpus = []string{
	// THE finding: Go's ASCII \b fires beside a non-ASCII letter where
	// Python's does not, so Go OVER-classifies.
	"研究curl x", "füwget y", "研究pytest", "研究grep -q x f",
	"研究playwright test", "研究wscat -c x",
	// The ASCII controls for each.
	"curl x", "wget y", "pytest", "grep -q x f",
	"playwright test", "wscat -c x",
	// The `://` branches, where the trailing \b means "a word character
	// FOLLOWS" — the inverse of WordEnd. A bare scheme must NOT match.
	"open https://example.com", "open https://", "open wss://h/s", "open wss://",
	// The space-ending static branches, same inversion. The bare command
	// must not match, and — the case the first draft of this corpus
	// missed — a WORD character after the space must, which is exactly
	// what distinguishes the trailing `\b` from WordEnd. "ls -la" does
	// NOT test it: `-` is a non-word character, so both spellings agree.
	"ls -la", "ls", "find . -name x", "find", "jq .a f", "jq",
	"ls src", "find src -name x", "jq keys f",
	// ...and those still agree, because staticHints only CHANGES the
	// answer when it preempts a later pattern (the hint-before-runner
	// precedence at classifySegment). Each of these pairs a
	// word-character-after-the-space hint with a `./binary` that would
	// otherwise classify "process", so static-vs-process is observable.
	"ls src ./app", "find src -exec ./app", "jq keys ./out.json",
	// The runner and non-exec-flag lanes.
	"pytest --collect-only", "pytest -q", "go test ./...", "npm run test",
	"go build ./...", "./server & sleep 1", "timeout 5 ./app &",
	"echo hi", "",
}

// TestProbeModalityMatchesCPython drives the real
// _classify_probe_modality. r6 converted the `process` pattern in this
// same var block and left the other four on Go's ASCII classes — a fix
// is evidence about its SIBLINGS, and the siblings were one screen away
// (adversarial mission-r7 LOW).
func TestProbeModalityMatchesCPython(t *testing.T) {
	if _, err := os.Stat(pySrc); err != nil {
		t.Skipf("python tree not beside this checkout: %v", err)
	}
	in, err := json.Marshal(modalityCorpus)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", modalityProbe, pySrc)
	cmd.Stdin = strings.NewReader(string(in))
	out, perr := cmd.Output()
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
	if len(want) != len(modalityCorpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(modalityCorpus))
	}

	// Anti-vacuity: the pre-fix patterns replayed over the same corpus.
	oldLost := 0
	for i, c := range modalityCorpus {
		if oldClassify(c) != want[i] {
			oldLost++
		}
	}
	if oldLost < 4 {
		t.Fatalf("r6's ASCII-class patterns differ from CPython on only %d of "+
			"%d cases: this corpus barely discriminates", oldLost, len(modalityCorpus))
	}

	for i, c := range modalityCorpus {
		if got := ClassifyProbeModality(c); got != want[i] {
			t.Errorf("modality(%q) = %q, want CPython %q", c, got, want[i])
		}
	}
}

// oldClassify replays r6's spelling of the four patterns through the
// production aggregation, so the corpus above is PROVED to discriminate.
// Only the regexes are swapped — reimplementing the segment walk here
// would let a shared bug report agreement.
func oldClassify(cmd string) string {
	saveM := modalityPatterns
	saveT, saveN, saveS := testRunnerRe, nonExecRunnerFlagsRe, staticHintsRe
	defer func() {
		modalityPatterns = saveM
		testRunnerRe, nonExecRunnerFlagsRe, staticHintsRe = saveT, saveN, saveS
	}()
	modalityPatterns = []struct {
		label string
		re    *regexp.Regexp
	}{
		{"browser", regexp.MustCompile(`(?i)\b(playwright|puppeteer|selenium|chromium|chrome --headless|firefox --headless)\b`)},
		{"ws", regexp.MustCompile(`(?i)\b(wscat|websocat|wss?://)\b`)},
		{"http", regexp.MustCompile(`(?i)\b(curl|wget|httpie|http [A-Z]+|https?://)\b`)},
		saveM[3], // the process pattern r6 already converted
	}
	testRunnerRe = regexp.MustCompile(`(?i)\b(pytest|go test|cargo test|(npm|pnpm|yarn) (run )?test|make test|tox)\b`)
	nonExecRunnerFlagsRe = regexp.MustCompile(`(?i)(^|\s)--?(no-run|collect-only|co|list-?tests?|dry-run|list)\b`)
	staticHintsRe = regexp.MustCompile(`(?i)\b(grep|rg|test -[efdrs]|cat|head|tail|wc -[lc]|ls |find |jq |go build|go vet|tsc --noEmit|ruff|flake8|mypy)\b`)
	return ClassifyProbeModality(cmd)
}

// TestPlanIndexIsThePlansIndexNotTheResults: plan_index is the join key
// claim_coverage.check_index uses, and Python takes it from
// `enumerate(checks[:5])` — the PLAN's ordinal. The two lists drift apart
// exactly when a plan is malformed: a check with a blank command is
// skipped without running, so the surviving row's results-index is 0 and
// its plan-index is 1. Taking the results-index would join every
// downstream claim to the wrong check, silently, only on malformed plans
// (adversarial mission-r7 HIGH).
func TestPlanIndexIsThePlansIndexNotTheResults(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("", "true"),
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"ok"}`,
	}}
	var rows []map[string]any
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir(), PersistRow: collectRows(&rows)})
	if v.ChecksRun != 1 {
		t.Fatalf("the blank-command check must be skipped, not run: %+v", v)
	}
	if len(rows) != 1 {
		t.Fatalf("rows: %+v", rows)
	}
	crs, _ := rows[0]["check_results"].([]any)
	if len(crs) != 1 {
		t.Fatalf("check_results: %v", rows[0]["check_results"])
	}
	cr, _ := crs[0].(map[string]any)
	if cr["plan_index"] != 1 {
		t.Fatalf("plan_index = %v, want 1 — the surviving row is the plan's "+
			"SECOND check and the results list's first: %+v", cr["plan_index"], cr)
	}
}

const admissionReasonProbe = `
import json, sys
sys.path.insert(0, sys.argv[1])
import closure_verify as cv
out = []
for s in json.loads(sys.stdin.read()):
    m = cv._RUNTIME_GAP_ADMISSION.search(s + "\n")
    out.append(("LLM summary admits runtime was not exercised: " +
                repr(m.group(1))) if m else "")
print(json.dumps(out))
`

// TestBehavioralGapReasonMatchesCPython closes the other half of the
// admission port. r7's admissionCorpus compares the matched TEXT, so it
// pins the regex — but nothing read DetectBehavioralGap's REASON, and the
// battery replaced m[1] with m[0] (quoting the consumed boundary
// characters) with every closure test still passing. The reason is
// scrubbed, stored on the verdict row and prefixed into
// goal_verdict_summary, so its bytes are shared-store bytes.
//
// It also pins pytext.Repr at a call site that reaches the apostrophe
// branch: the regex matches `wasn'?t`, and repr() switches to DOUBLE
// quotes for a string containing an apostrophe.
func TestBehavioralGapReasonMatchesCPython(t *testing.T) {
	if _, err := os.Stat(pySrc); err != nil {
		t.Skipf("python tree not beside this checkout: %v", err)
	}
	summaries := []string{
		"All done. the probe wasn't run at all.",
		"Shipped it; 研究runtime validation was skipped.",
		"Complete — no café was tested.",
		"Everything verified: tests were not run.",
		"the server was never started",
		"all files verified present",
	}
	in, err := json.Marshal(summaries)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", admissionReasonProbe, pySrc)
	cmd.Stdin = strings.NewReader(string(in))
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output: %v (%s)", err, out)
	}

	// Anti-vacuity: at least one case must actually produce a reason, and
	// at least one must produce none, or an all-empty table would report
	// agreement no matter what this function returned.
	reasons, blanks := 0, 0
	for _, w := range want {
		if w == "" {
			blanks++
		} else {
			reasons++
		}
	}
	if reasons < 3 || blanks < 1 {
		t.Fatalf("corpus yields %d reasons and %d blanks: it cannot separate",
			reasons, blanks)
	}
	// And the apostrophe branch must be reachable, or pytext.Repr's
	// double-quote switch is untested here.
	if !strings.Contains(want[0], `"`) {
		t.Fatalf("CPython did not repr() with double quotes: %q", want[0])
	}

	for i, s := range summaries {
		got := DetectBehavioralGap(true, s, nil, map[string]int{"static": 1})
		if got != want[i] {
			t.Errorf("reason for %q:\n go %q\n py %q", s, got, want[i])
		}
	}
}
