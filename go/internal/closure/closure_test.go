package closure

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// TestClassifyProbeModalityMatchesCPython pins the per-segment
// classifier against closure_verify._classify_probe_modality (fixtures
// generated 2026-08-22).
func TestClassifyProbeModalityMatchesCPython(t *testing.T) {
	cases := []struct{ cmd, want string }{
		{"grep -q 'listen' server.go", "static"},
		{"./bin/tool --help >/tmp/tool.out && grep -q 'usage' /tmp/tool.out", "process"},
		{"curl -fsS http://127.0.0.1:8000/health", "http"},
		{"go build ./cmd/foo", "static"},
		{"go test ./... -run TestX", "process"},
		{"pytest --collect-only -q", "static"},
		{"python3 smoke.py --dry-run", "process"},
		{"websocat ws://localhost:9001/ws", "ws"},
		{"playwright test e2e.spec.ts", "browser"},
		{"python server.py >/tmp/s.log 2>&1 & pid=$!; sleep 2; curl -i http://127.0.0.1:8080/", "http"},
		{"grep -q 'a && ./x' file.txt", "static"},
		{"cat README.md | head -5", "static"},
		{"timeout 5 ./server & sleep 1; grep started /tmp/log", "process"},
		{"node index.js", "process"},
		{"make test", "process"},
		{"", "static"},
	}
	for _, c := range cases {
		if got := ClassifyProbeModality(c.cmd); got != c.want {
			t.Errorf("ClassifyProbeModality(%q) = %q, want CPython %q", c.cmd, got, c.want)
		}
	}
}

// TestSplitProbeSegmentsQuoteAware: operators inside quotes do not
// split (CPython fixture).
func TestSplitProbeSegmentsQuoteAware(t *testing.T) {
	got := SplitProbeSegments(`a && b || c; d | e '1 && 2' "x;y"`)
	want := []string{"a", "b", "c", "d", `e '1 && 2' "x;y"`}
	if len(got) != len(want) {
		t.Fatalf("segments: %q", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("segment %d: %q want %q", i, got[i], want[i])
		}
	}
}

// TestCheckOutcomeMatchesCPython pins the pass/fail/inconclusive
// classification branch-for-branch.
func TestCheckOutcomeMatchesCPython(t *testing.T) {
	cases := []struct {
		code   int
		stderr string
		want   string
	}{
		{0, "", "pass"},
		{1, "", "fail"},
		{127, "", "inconclusive"},
		{126, "", "inconclusive"},
		{-1, "", "inconclusive"},
		{2, "command not found", "inconclusive"},
		{1, "timed out", "inconclusive"},
		{1, `SyntaxError: File "<string>", line 1`, "inconclusive"},
		{1, `SyntaxError: File "app.py", line 3`, "fail"},
		{1, "syntax error near unexpected token", "inconclusive"},
		{1, "Permission denied", "inconclusive"},
		{1, "fatal: not a git repository", "inconclusive"},
		{1, "AssertionError", "fail"},
	}
	for _, c := range cases {
		if got := CheckOutcome(c.code, c.stderr); got != c.want {
			t.Errorf("CheckOutcome(%d, %q) = %q, want CPython %q", c.code, c.stderr, got, c.want)
		}
	}
}

// TestFingerprintMatchesCPython: md5-12hex parity, whitespace
// normalization, order-insensitivity, empty means no-signal.
func TestFingerprintMatchesCPython(t *testing.T) {
	v := Verdict{FailedChecks: []string{
		"pytest -q => exit 1: FAILED tests/test_a.py::test_x",
		"grep -q done out.txt => exit 1",
	}}
	if got := Fingerprint(v); got != "8d60cd2f8f3e" {
		t.Fatalf("Fingerprint = %q, want CPython 8d60cd2f8f3e", got)
	}
	v2 := Verdict{FailedChecks: []string{
		"  grep   -q done   out.txt  => exit 1",
		"pytest -q => exit 1: FAILED tests/test_a.py::test_x",
	}}
	if got := Fingerprint(v2); got != "8d60cd2f8f3e" {
		t.Fatalf("normalized/reordered fingerprint diverged: %q", got)
	}
	if got := Fingerprint(Verdict{}); got != "" {
		t.Fatalf("no hard failures must fingerprint to no-signal, got %q", got)
	}
}

func TestFailedCheckSignatureMatchesCPython(t *testing.T) {
	sig := FailedCheckSignature(CheckResult{
		Command: "pytest -q", ExitCode: 1,
		Stderr: "boom  err", Stdout: "FAILED  x",
	})
	if sig != "pytest -q => exit 1: boom err FAILED x" {
		t.Fatalf("signature = %q", sig)
	}
	bare := FailedCheckSignature(CheckResult{Command: "grep -q done out.txt", ExitCode: 1})
	if bare != "grep -q done out.txt => exit 1" {
		t.Fatalf("no-output signature = %q", bare)
	}
}

// TestVerdictFirstSummaryMatchesCPython: the flag writes the opener;
// the prose can only elaborate, never contradict, in a truncated view.
func TestVerdictFirstSummaryMatchesCPython(t *testing.T) {
	cases := []struct {
		summary          string
		complete, judged bool
		want             string
	}{
		{"The goal was achieved. Files present.", true, true, "Achieved: Files present."},
		{"Goal not achieved: missing tests.", false, true, "Not achieved: missing tests."},
		{"plain words", true, true, "Achieved: plain words"},
		{"", false, false, "Not judged (verification evidence inconclusive)."},
	}
	for _, c := range cases {
		if got := VerdictFirstSummary(c.summary, c.complete, c.judged); got != c.want {
			t.Errorf("VerdictFirstSummary(%q,%v,%v) = %q, want %q",
				c.summary, c.complete, c.judged, got, c.want)
		}
	}
	dg := "Downgraded to not-achieved — reason. rest"
	if got := VerdictFirstSummary(dg, false, true); got != dg {
		t.Errorf("downgrade opener must be left alone: %q", got)
	}
}

// TestRuntimeGapAdmissionMatchesCPython pins the self-admission regex.
func TestRuntimeGapAdmissionMatchesCPython(t *testing.T) {
	cases := []struct {
		text string
		want bool
	}{
		{"Gap: runtime validation (server startup + browser connection) was not performed.", true},
		{"the server was never started", true},
		{"all files verified present", false},
		{"no behavioral probes ran", true},
		{"tests were not run", true},
	}
	for _, c := range cases {
		if got := runtimeGapAdmissionRe.MatchString(c.text); got != c.want {
			t.Errorf("admission(%q) = %v, want CPython %v", c.text, got, c.want)
		}
	}
}

func TestRenderStepForClosureTruncationVisible(t *testing.T) {
	short := RenderStepForClosure("do it", "done", 3)
	if short != "Step 3: do it\nResult: done" {
		t.Fatalf("short render: %q", short)
	}
	long := RenderStepForClosure(strings.Repeat("t", 400), strings.Repeat("r", 5000), 1)
	if !strings.Contains(long, "… [step text truncated at 300]") {
		t.Fatalf("step-text cut not marked:\n%.400s", long)
	}
	if !strings.Contains(long, "TRUNCATED — showing the first 4000 of 5000 characters; the rest was NOT shown to you") {
		t.Fatalf("result cut not marked:\n%.400s", long)
	}
}

// --- pipeline tests -------------------------------------------------------

func planJSON(cmds ...string) string {
	var checks []string
	for _, c := range cmds {
		checks = append(checks, `{"failure_mode":"fm","description":"probe","command":"`+c+`"}`)
	}
	return `{"checks":[` + strings.Join(checks, ",") + `]}`
}

func collectRows(rows *[]map[string]any) func(map[string]any) {
	return func(r map[string]any) { *rows = append(*rows, r) }
}

// TestVerifyHappyPath: plan → mechanical checks → verdict, with the
// file inventory grounding the plan prompt and a durable verdict row.
func TestVerifyHappyPath(t *testing.T) {
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, "deliverable.md"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	fake := &llm.Fake{Script: []string{
		planJSON("true", "echo hi"),
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"Goal achieved. Both probes passed."}`,
	}}
	var rows []map[string]any
	v := Verify(context.Background(), fake, "write deliverable.md",
		[]StepView{{Text: "write it", Result: "wrote deliverable.md"}},
		Options{WorkspacePath: ws, PersistRow: collectRows(&rows)})
	if !v.Judged || !v.Complete || v.ChecksRun != 2 || v.ChecksPassed != 2 {
		t.Fatalf("verdict: %+v", v)
	}
	if v.Summary != "Achieved: Both probes passed." {
		t.Fatalf("summary not verdict-first: %q", v.Summary)
	}
	if !strings.Contains(fake.Prompts[0], "deliverable.md") {
		t.Fatalf("file inventory missing from plan prompt:\n%s", fake.Prompts[0])
	}
	if len(rows) != 1 || rows[0]["fingerprint"] != "" || rows[0]["judged"] != true {
		t.Fatalf("verdict row: %+v", rows)
	}
}

// TestVerifyFailedCheckFeedsFingerprint: a hard-failed probe keeps the
// verdict's standing (no cap) and mints a non-empty fingerprint.
func TestVerifyFailedCheckFeedsFingerprint(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("exit 3"),
		`{"complete":false,"confidence":0.9,"gaps":["probe failed"],"summary":"Goal not achieved."}`,
	}}
	var rows []map[string]any
	v := Verify(context.Background(), fake, "g", nil,
		Options{WorkspacePath: t.TempDir(), PersistRow: collectRows(&rows)})
	if v.Complete || !v.Judged {
		t.Fatalf("verdict: %+v", v)
	}
	if v.Confidence != 0.9 {
		t.Fatalf("grounded False must keep its confidence, got %v", v.Confidence)
	}
	if len(v.FailedChecks) != 1 || Fingerprint(v) == "" {
		t.Fatalf("failed check not fingerprinted: %+v", v)
	}
	if rows[0]["fingerprint"] == "" {
		t.Fatalf("fingerprint missing from persisted row: %+v", rows[0])
	}
}

// TestVerifyUngroundedFalseCapped: complete=false contradicting EVERY
// executed probe, with no file content in evidence, loses standing —
// confidence capped below the trust floor, cut marked in the summary.
func TestVerifyUngroundedFalseCapped(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"complete":false,"confidence":0.9,"gaps":["narrative doubt"],"summary":"Goal not achieved."}`,
	}}
	v := Verify(context.Background(), fake, "g", nil, Options{WorkspacePath: t.TempDir()})
	if v.Confidence != 0.65 {
		t.Fatalf("ungrounded False kept standing: %+v", v)
	}
	if !strings.Contains(v.Summary, "verdict confidence capped") {
		t.Fatalf("cap not marked in summary: %q", v.Summary)
	}
}

// TestVerifyInconclusiveMeansUnjudged: every probe inconclusive (the
// verifier's own tooling failed) → judged=false. The caller must stamp
// NOTHING from an unjudged verdict.
func TestVerifyInconclusiveMeansUnjudged(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("this_command_does_not_exist_xyzzy"),
		`{"complete":false,"confidence":0.8,"gaps":[],"summary":"Goal not achieved."}`,
	}}
	v := Verify(context.Background(), fake, "g", nil, Options{WorkspacePath: t.TempDir()})
	if v.Judged {
		t.Fatalf("all-inconclusive verdict claims judged: %+v", v)
	}
	if v.InconclusiveCount != 1 || len(v.FailedChecks) != 0 {
		t.Fatalf("inconclusive is a verifier failure, not goal evidence: %+v", v)
	}
	if !strings.HasPrefix(v.Summary, "Not judged") {
		t.Fatalf("unjudged summary opener: %q", v.Summary)
	}
}

// TestVerifyCwdUnresolvedRefusesToRun: no workspace path → checks are
// refused as env-unresolved inconclusive rows, never run in the
// launcher's own directory (B3a probe-env hardening).
func TestVerifyCwdUnresolvedRefusesToRun(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("rm -rf should_never_run; true"),
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"Goal achieved."}`,
	}}
	v := Verify(context.Background(), fake, "g", nil, Options{})
	if v.Judged || v.InconclusiveCount != 1 {
		t.Fatalf("cwd-unresolved check was not honestly inconclusive: %+v", v)
	}
}

// TestVerifySkipsAreNamed: dry-run and nil adapter are intentional
// skips (no row); an empty plan persists its named skip row.
func TestVerifySkipsAreNamed(t *testing.T) {
	var rows []map[string]any
	v := Verify(context.Background(), nil, "g", nil, Options{PersistRow: collectRows(&rows)})
	if v.Judged || v.SkipReason != "dry_run" || len(rows) != 0 {
		t.Fatalf("nil-adapter skip: %+v rows=%v", v, rows)
	}
	fake := &llm.Fake{Script: []string{`{"checks":[]}`}}
	v = Verify(context.Background(), fake, "research topic", nil,
		Options{WorkspacePath: t.TempDir(), PersistRow: collectRows(&rows)})
	if v.Judged || v.SkipReason != "no_checks_generated" {
		t.Fatalf("empty-plan skip: %+v", v)
	}
	if len(rows) != 1 || rows[0]["skipped"] != "no_checks_generated" {
		t.Fatalf("skip row missing — closure-never-ran must be distinguishable: %v", rows)
	}
}

// TestVerifyVerdictMissingCompleteSkips: Go-stricter refusal — Python
// defaults a missing "complete" to true; a verdict without its
// load-bearing flag is not a verdict here (named in PORT.md).
func TestVerifyVerdictMissingCompleteSkips(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"confidence":0.9,"summary":"looks fine"}`,
	}}
	v := Verify(context.Background(), fake, "g", nil, Options{WorkspacePath: t.TempDir()})
	if v.Judged || v.SkipReason != "verdict_parse_failed" {
		t.Fatalf("missing complete flag accepted: %+v", v)
	}
}

// TestVerifyBehavioralDowngrade: the verdict claims complete but its
// own prose admits runtime wasn't exercised and every probe was static
// — Signal 1 self-contradiction flips it, and the downgrade opener
// survives verdict-first normalization.
func TestVerifyBehavioralDowngrade(t *testing.T) {
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, "server.go"), []byte("package main"), 0o644); err != nil {
		t.Fatal(err)
	}
	fake := &llm.Fake{Script: []string{
		planJSON("grep -q main server.go"),
		`{"complete":true,"confidence":0.9,"gaps":["runtime validation was not performed"],"summary":"Goal achieved. Code present."}`,
	}}
	v := Verify(context.Background(), fake, "build and run the server", nil,
		Options{WorkspacePath: ws})
	if v.Complete {
		t.Fatalf("self-admitted runtime gap survived as complete: %+v", v)
	}
	if v.DowngradeReason == "" || !strings.HasPrefix(v.Summary, "Downgraded to not-achieved") {
		t.Fatalf("downgrade not named as cause: %+v", v)
	}
}

// TestDetectBehavioralGapRespectsBehavioralEvidence: one behavioral
// probe stands the downgrade down; incomplete verdicts are exempt.
func TestDetectBehavioralGapRespectsBehavioralEvidence(t *testing.T) {
	if r := DetectBehavioralGap(true, "runtime validation was not performed", nil,
		map[string]int{"process": 1}); r != "" {
		t.Fatalf("behavioral evidence present but downgrade fired: %q", r)
	}
	if r := DetectBehavioralGap(false, "runtime validation was not performed", nil,
		map[string]int{"static": 2}); r != "" {
		t.Fatalf("incomplete verdict downgraded: %q", r)
	}
	if r := DetectBehavioralGap(true, "server was never started", nil,
		map[string]int{"static": 2}); r == "" {
		t.Fatal("admission with zero behavioral probes must fire")
	}
}

// TestProjectFileInventoryBounded: cap marked, VCS dirs skipped, lock
// files dropped, missing root degrades to "".
func TestProjectFileInventoryBounded(t *testing.T) {
	ws := t.TempDir()
	for _, f := range []string{"a.txt", "b.lock", ".git/config", "sub/c.md"} {
		p := filepath.Join(ws, f)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	inv := projectFileInventory(ws, 120)
	if !strings.Contains(inv, "a.txt") || !strings.Contains(inv, filepath.Join("sub", "c.md")) {
		t.Fatalf("inventory: %q", inv)
	}
	if strings.Contains(inv, "b.lock") || strings.Contains(inv, ".git") {
		t.Fatalf("lock/VCS leaked into inventory: %q", inv)
	}
	capped := projectFileInventory(ws, 1)
	if !strings.Contains(capped, "truncated at 1 files") {
		t.Fatalf("cap not marked: %q", capped)
	}
	if projectFileInventory(filepath.Join(ws, "nope"), 10) != "" {
		t.Fatal("missing root must degrade to empty")
	}
}

// --- closure r1 fix-layer pins (adversarial round 1, 2026-08-22) ---

// TestCutRunesIsRuneSafe: every truncation in this package must cut on
// runes (Python str[:n] parity); a byte slice mid-multibyte corrupts
// judge-facing evidence. Mutation must-detect: byte-slicing cutRunes.
func TestCutRunesIsRuneSafe(t *testing.T) {
	s := strings.Repeat("é", 10) // 2 bytes per rune
	got := cutRunes(s, 5)
	if got != strings.Repeat("é", 5) {
		t.Fatalf("cutRunes not rune-based: %q", got)
	}
	if cutRunes("abc", 5) != "abc" {
		t.Fatal("short string must pass through")
	}
}

// TestRenderStepForClosureRuneCounts: the honesty marker's "%d of %d
// characters" must be RUNE counts — Python's len() — and the cut must
// not split a multibyte character.
func TestRenderStepForClosureRuneCounts(t *testing.T) {
	result := strings.Repeat("汉", workSummaryResultCut+7)
	out := RenderStepForClosure("step", result, 1)
	want := fmt.Sprintf("showing the first %d of %d characters",
		workSummaryResultCut, workSummaryResultCut+7)
	if !strings.Contains(out, want) {
		t.Fatalf("marker counts bytes, not runes:\n%s", out[:200])
	}
	if strings.Contains(out, "�") || !utf8.ValidString(out) {
		t.Fatal("truncation split a rune")
	}
}

// TestFailedCheckSignatureRuneSafe: signature cuts land on rune
// boundaries so the fingerprint stays cross-runtime comparable.
func TestFailedCheckSignatureRuneSafe(t *testing.T) {
	r := CheckResult{Command: strings.Repeat("ü", 250), ExitCode: 1,
		Stderr: strings.Repeat("ö", 250)}
	sig := FailedCheckSignature(r)
	if !utf8.ValidString(sig) || strings.Contains(sig, "�") {
		t.Fatalf("signature split a rune: %q", sig)
	}
	if !strings.HasPrefix(sig, strings.Repeat("ü", 200)+" => exit 1") {
		t.Fatalf("command not cut at 200 runes: %q", sig[:50])
	}
}

// TestVerifyPanicRecovered: the never-returns-an-error contract must
// hold for UNANTICIPATED bugs — Python added its catch-all only after
// two 2026-07-27 runs lost closure invisibly, and recall repeated the
// omission-then-fix one tranche ago. The seam forces the panic; the
// recover must persist a named "exception" row and return the skip
// verdict instead of crashing the loop.
func TestVerifyPanicRecovered(t *testing.T) {
	closurePanicHook = func() { panic("injected: doubly-asserted type") }
	defer func() { closurePanicHook = nil }()
	fake := &llm.Fake{Script: []string{planJSON("true"), `unused`}}
	var rows []map[string]any
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir(), PersistRow: collectRows(&rows)})
	if v.SkipReason != "exception" || v.Judged {
		t.Fatalf("panic must yield nullVerdict(exception): %+v", v)
	}
	if len(rows) != 1 || rows[0]["skipped"] != "exception" {
		t.Fatalf("exception row missing: %+v", rows)
	}
	detail, _ := rows[0]["skip_detail"].(string)
	if !strings.Contains(detail, "injected: doubly-asserted type") ||
		!strings.Contains(detail, "closure.Verify") {
		t.Fatalf("skip_detail must carry panic value + stack: %q", detail)
	}
}

// TestVerifyRowCarriesCheckResults: the durable row must answer "why
// did check N fail" from disk alone (persist-the-artifacts decree —
// Python's check_results array; the aggregate-only draft could not).
func TestVerifyRowCarriesCheckResults(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("echo out-marker; echo err-marker >&2; exit 3"),
		`{"complete":false,"confidence":0.8,"gaps":["g"],"summary":"failed"}`,
	}}
	var rows []map[string]any
	Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir(), PersistRow: collectRows(&rows)})
	if len(rows) != 1 {
		t.Fatalf("rows: %+v", rows)
	}
	if _, has := rows[0]["commands"]; has {
		t.Fatal("nonstandard bare commands list must be gone")
	}
	crs, ok := rows[0]["check_results"].([]map[string]any)
	if !ok || len(crs) != 1 {
		t.Fatalf("check_results missing: %+v", rows[0])
	}
	cr := crs[0]
	if cr["exit_code"] != 3 || cr["outcome"] != "fail" ||
		!strings.Contains(cr["stdout"].(string), "out-marker") ||
		!strings.Contains(cr["stderr"].(string), "err-marker") {
		t.Fatalf("check_results row incomplete: %+v", cr)
	}
}

// TestVerifyClassifiesOnFullStderr: outcome classification reads the
// UNTRUNCATED stderr (Python's order) — the inconclusive phrases sit at
// the end of verbose diagnostics, and classifying the 300-char head
// flips verifier failures into goal-disproving hard fails.
func TestVerifyClassifiesOnFullStderr(t *testing.T) {
	cmd := `printf 'x%.0s' $(seq 1 400) >&2; echo 'foo: command not found' >&2; exit 1`
	fake := &llm.Fake{Script: []string{
		planJSON(cmd),
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"ok"}`,
	}}
	var rows []map[string]any
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir(), PersistRow: collectRows(&rows)})
	if v.InconclusiveCount != 1 || v.Judged {
		t.Fatalf("phrase past byte 300 must still classify inconclusive: %+v", v)
	}
	crs := rows[0]["check_results"].([]map[string]any)
	if got := crs[0]["stderr"].(string); len([]rune(got)) > 300 {
		t.Fatalf("stored stderr must still be truncated: %d runes", len([]rune(got)))
	}
}

// TestVerifyConfidenceStringCoerced: safe_float parity — a judge that
// emits "confidence": "0.9" keeps its signal instead of silently
// falling to the 0.7 default the ungrounded-False cap reads.
func TestVerifyConfidenceStringCoerced(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"complete":true,"confidence":"0.9","gaps":[],"summary":"ok"}`,
	}}
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir()})
	if v.Confidence != 0.9 {
		t.Fatalf("string confidence must coerce: %+v", v)
	}
	fake2 := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"complete":true,"confidence":"NaN","gaps":[],"summary":"ok"}`,
	}}
	v2 := Verify(context.Background(), fake2, "goal", nil,
		Options{WorkspacePath: t.TempDir()})
	if v2.Confidence != 0.7 {
		t.Fatalf("non-finite confidence must fall to default: %+v", v2)
	}
}

// TestVerifyGapsBareStringCoerced: a bare-string gaps field is drift,
// not absence — carrying it beats silently reading a stated gap as a
// clean verdict (the recall tranche's evidence-coercion direction).
func TestVerifyGapsBareStringCoerced(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"complete":false,"confidence":0.9,"gaps":"the one gap","summary":"no"}`,
	}}
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir()})
	if len(v.Gaps) != 1 || v.Gaps[0] != "the one gap" {
		t.Fatalf("bare-string gaps must coerce to one-element slice: %+v", v.Gaps)
	}
}

// TestRunCheckKillsProcessGroupOnTimeout: a timed-out probe must not
// orphan backgrounded children — SIGKILL to bash alone never runs the
// trap-EXIT cleanup the plan prompt encourages, leaving servers bound
// to ports across runs. Setpgid + kill(-pgid) closes it structurally.
func TestRunCheckKillsProcessGroupOnTimeout(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "child.pid")
	cmd := `sleep 30 & echo $! > child.pid; wait`
	started := time.Now()
	code, _, _ := runCheck(context.Background(), cmd, dir, 300*time.Millisecond)
	elapsed := time.Since(started)
	if code != -1 {
		t.Fatalf("expected timeout exit -1, got %d", code)
	}
	// The surviving orphan ALSO holds runCheck's output pipes open, so
	// without the group kill this call blocks the child's full lifetime
	// (mutation M4 escaped the liveness probe alone: 30s later the child
	// had exited naturally). The prompt return IS the observable fix.
	if elapsed > 5*time.Second {
		t.Fatalf("runCheck blocked %s past its 300ms timeout — orphan held the pipes", elapsed)
	}
	raw, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatalf("child pid never written: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if syscall.Kill(pid, 0) != nil {
			return // child gone — group kill worked
		}
		time.Sleep(50 * time.Millisecond)
	}
	syscall.Kill(pid, syscall.SIGKILL)
	t.Fatalf("backgrounded child %d survived the timeout kill", pid)
}

// --- closure r2 fix-layer pins (adversarial round 2, 2026-08-22) ---

// TestRunCheckWaitDelayBackstopsEscapedProcess: a probe that DETACHES
// (setsid) escapes the group kill AND holds the output pipes — without
// WaitDelay, Run() blocks for the escapee's whole lifetime and the loop
// hangs on an LLM-authored one-liner (r2 Skeptic HIGH).
func TestRunCheckWaitDelayBackstopsEscapedProcess(t *testing.T) {
	dir := t.TempDir()
	cmd := `setsid sleep 30 & echo started; exit 0`
	started := time.Now()
	code, stdout, stderr := runCheck(context.Background(), cmd, dir, 10*time.Second)
	elapsed := time.Since(started)
	if elapsed > 6*time.Second {
		t.Fatalf("runCheck blocked %s on an escaped descendant", elapsed)
	}
	if code != 0 {
		t.Fatalf("probe itself exited 0; its real code must survive: %d (stderr %q)", code, stderr)
	}
	if !strings.Contains(stdout, "started") {
		t.Fatalf("stdout lost: %q", stdout)
	}
	if !strings.Contains(stderr, "descendant process outlived the probe") {
		t.Fatalf("held-pipes note missing: %q", stderr)
	}
}

// TestVerifyScrubsJudgeProseAtBoundary: the verdict prompt carries raw
// probe output, and Summary/Gaps flow to FOUR consumers (row, metadata
// stamp, event, CLI) — the scrub happens once at Verify's return
// boundary so no consumer can forget it (r2 Architect HIGH: the CLI
// line was a second, unscrubbed egress).
func TestVerifyScrubsJudgeProseAtBoundary(t *testing.T) {
	secret := "sk-ant-api03-" + strings.Repeat("b", 40)
	fake := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"complete":true,"confidence":0.9,"gaps":["leaked ` + secret + ` in output"],"summary":"Goal achieved. Saw ` + secret + ` in logs."}`,
	}}
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir()})
	if strings.Contains(v.Summary, "sk-ant-api03-") {
		t.Fatalf("summary left Verify unscrubbed: %q", v.Summary)
	}
	if len(v.Gaps) != 1 || strings.Contains(v.Gaps[0], "sk-ant-api03-") {
		t.Fatalf("gaps left Verify unscrubbed: %+v", v.Gaps)
	}
	if !strings.HasPrefix(v.Summary, "Achieved:") {
		t.Fatalf("verdict-first shape must survive the scrub: %q", v.Summary)
	}
}

// TestVerifyPanicInPersistDoesNotCrash: if the ORIGINAL panic came from
// the persist path, the recovery's own persist re-enters it — recover()
// does not re-arm, so without the inner guard the loop dies after all
// (r2 Skeptic). A dropped row beats a dead loop.
func TestVerifyPanicInPersistDoesNotCrash(t *testing.T) {
	fake := &llm.Fake{Script: []string{planJSON("true"),
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"ok"}`}}
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir(),
			PersistRow: func(map[string]any) { panic("write path is broken") }})
	if v.SkipReason != "exception" || v.Judged {
		t.Fatalf("persist-path panic must degrade to the exception skip: %+v", v)
	}
}

// TestVerifyDryRunSkipNamed: the dry_run skip reason itself, pinned —
// r2 flagged that only the a==nil half of the guard was ever exercised.
// NOTE the composed loop path gates on !opts.DryRun BEFORE Verify, so
// this field is honest only for direct callers; named in PORT.md.
func TestVerifyDryRunSkipNamed(t *testing.T) {
	v := Verify(context.Background(), &llm.Fake{}, "goal", nil,
		Options{WorkspacePath: t.TempDir(), DryRun: true})
	if v.SkipReason != "dry_run" || v.Judged {
		t.Fatalf("dry-run skip: %+v", v)
	}
}

// --- closure r3 fix-layer pins (adversarial round 3, 2026-08-22) ---

// TestRunCheckReapsBackgroundedChildAfterWaitDelay: the r2 WaitDelay
// early-return stood down the ctx-watchdog group kill, so a MERELY
// backgrounded child (same group, no setsid) — which pre-r2 was reaped
// at the ctx deadline — would have leaked for its natural lifetime
// (r3 Skeptic HIGH). The ErrWaitDelay path must reap the group itself.
func TestRunCheckReapsBackgroundedChildAfterWaitDelay(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "child.pid")
	cmd := `sleep 30 & echo $! > child.pid; exit 0`
	started := time.Now()
	code, _, stderr := runCheck(context.Background(), cmd, dir, 10*time.Second)
	if e := time.Since(started); e > 6*time.Second {
		t.Fatalf("runCheck blocked %s — WaitDelay backstop gone", e)
	}
	if code != 0 {
		t.Fatalf("probe exited 0; got %d (stderr %q)", code, stderr)
	}
	raw, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatal(err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if syscall.Kill(pid, 0) != nil {
			return // reaped — the explicit group kill fired
		}
		time.Sleep(50 * time.Millisecond)
	}
	syscall.Kill(pid, syscall.SIGKILL)
	t.Fatalf("backgrounded same-group child %d leaked past the WaitDelay return", pid)
}

// TestVerifyScrubsDowngradeReason: the admission regex quotes raw
// summary words into DowngradeReason, which reaches the metadata stamp
// and the captain's-log event — the r2 boundary scrub missed it (r3
// Skeptic: \w+-only secret shapes ride the captured words intact).
func TestVerifyScrubsDowngradeReason(t *testing.T) {
	// AKIA-shaped: pure \w, so the admission regex's captured words
	// carry it INTO DowngradeReason verbatim (an sk-ant- secret's
	// hyphens break the \w+ capture and made the first fixture
	// vacuous — mutation M11 escaped until this shape).
	secret := "AKIAIOSFODNN7EXAMPLE"
	fake := &llm.Fake{Script: []string{
		planJSON("true"),
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"Achieved. No ` + secret + ` rotation was tested."}`,
	}}
	v := Verify(context.Background(), fake, "goal", nil,
		Options{WorkspacePath: t.TempDir()})
	if v.DowngradeReason == "" {
		t.Fatalf("fixture must trip the behavioral-gap downgrade: %+v", v)
	}
	if strings.Contains(v.DowngradeReason, "AKIAIOSFODNN7") {
		t.Fatalf("DowngradeReason left Verify unscrubbed: %q", v.DowngradeReason)
	}
	if strings.Contains(v.Summary, "AKIAIOSFODNN7") {
		t.Fatalf("summary unscrubbed: %q", v.Summary)
	}
}

// TestRunCheckWaitDelayScalesToShortTimeouts: the timeout/4 scaling was
// unreachable in production (no caller sets TimeoutPerCheck) and
// unpinned — an instrument with no must-detect fixture (r4 Skeptic).
// A 2s-timeout probe with a pipe-holding child must return on the
// ~500ms scaled grace, nowhere near the flat 2s.
func TestRunCheckWaitDelayScalesToShortTimeouts(t *testing.T) {
	dir := t.TempDir()
	cmd := `sleep 30 & exit 0`
	started := time.Now()
	code, _, _ := runCheck(context.Background(), cmd, dir, 2*time.Second)
	elapsed := time.Since(started)
	if code != 0 {
		t.Fatalf("probe exited 0; got %d", code)
	}
	if elapsed > 1500*time.Millisecond {
		t.Fatalf("scaled WaitDelay did not fire: %s (flat 2s grace would exceed this)", elapsed)
	}
}
