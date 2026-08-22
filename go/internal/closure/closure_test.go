package closure

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

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
