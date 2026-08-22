package llm

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeCapture(t *testing.T, name, content string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

// Python _parse_stream_json keeps the LAST result event; the port must
// not return the first (adversarial round 2026-08-22, Minimalist).
func TestScanForResultLastEventWins(t *testing.T) {
	out := writeCapture(t, "out",
		`{"type":"result","subtype":"success","result":"first","is_error":false}`+"\n"+
			`{"type":"result","subtype":"success","result":"second","is_error":false}`+"\n")
	res, _, err := scanForResult(out)
	if err != nil {
		t.Fatal(err)
	}
	if res.Result != "second" {
		t.Fatalf("first event won: %q", res.Result)
	}
}

// A result-shaped line that fails strict unmarshal must be named in the
// error, not silently skipped (adversarial round 2026-08-22, Expert QA).
func TestScanForResultReportsUnparseableResultLine(t *testing.T) {
	out := writeCapture(t, "out",
		`{"type":"result","usage":{"input_tokens":"not-a-number"}}`+"\n")
	_, _, err := scanForResult(out)
	if err == nil {
		t.Fatal("want error")
	}
	if !strings.Contains(err.Error(), "result-shaped line failed to parse") {
		t.Fatalf("unparseable result line not surfaced: %v", err)
	}
}

// Python's _extract_result_object fallback: a pretty-printed single
// object with no NDJSON events still yields the result.
func TestScanForResultPrettyPrintedFallback(t *testing.T) {
	out := writeCapture(t, "out", "{\n  \"type\": \"result\",\n  \"subtype\": \"success\",\n  \"result\": \"hi\",\n  \"is_error\": false\n}\n")
	res, _, err := scanForResult(out)
	if err != nil {
		t.Fatal(err)
	}
	if res.Result != "hi" {
		t.Fatalf("fallback missed: %+v", res)
	}
}

// Python emits [END SYSTEM INSTRUCTIONS] unconditionally, system block
// or not (adversarial round 2026-08-22, Skeptic).
func TestBuildPromptEndMarkerUnconditional(t *testing.T) {
	p := BuildPrompt([]Message{{Role: "user", Content: "hello"}})
	if !strings.Contains(p, "[END SYSTEM INSTRUCTIONS]") {
		t.Fatalf("END marker missing without system block:\n%s", p)
	}
	if strings.Contains(p, "[SYSTEM INSTRUCTIONS]\nhello") {
		t.Fatalf("user content leaked into system block:\n%s", p)
	}
}

// A parse-failing result-shaped line beside a successful one must
// surface as a suspect, not vanish (adversarial r2, Expert QA).
func TestScanForResultKeepsSuspectBesideSuccess(t *testing.T) {
	out := writeCapture(t, "out",
		`{"type":"result","usage":{"input_tokens":"bad"}}`+"\n"+
			`{"type":"result","subtype":"success","result":"ok","is_error":false}`+"\n")
	res, suspects, err := scanForResult(out)
	if err != nil || res.Result != "ok" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	if len(suspects) != 1 || !strings.Contains(suspects[0], "failed to parse") {
		t.Fatalf("suspect lost beside success: %v", suspects)
	}
}

// --- end-to-end Complete coverage through a fixture "claude" script
// (adversarial r2, Skeptic: the literal production entry point had no
// test touching its exec.CommandContext invocation).

func fixtureBin(t *testing.T, script string) *Subprocess {
	t.Helper()
	p := filepath.Join(t.TempDir(), "fake-claude")
	if err := os.WriteFile(p, []byte("#!/bin/sh\n"+script), 0o755); err != nil {
		t.Fatal(err)
	}
	return &Subprocess{Bin: p, DefaultTimeout: 5e9}
}

func TestCompleteEndToEndSuccessAndModelFlag(t *testing.T) {
	// The script proves the --model flag reached argv by echoing "$@"
	// into the result payload, and proves stdout/stderr merge by writing
	// a non-JSON stderr line that must not break parsing.
	a := fixtureBin(t, `echo "stderr noise" 1>&2
printf '{"type":"result","subtype":"success","result":"args: %s","is_error":false,"usage":{"input_tokens":3,"output_tokens":4}}\n' "$*"`)
	resp, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "hi"}},
		Options{Model: "sonnet", Purpose: "test"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Content, "--model sonnet") {
		t.Fatalf("model flag not threaded: %q", resp.Content)
	}
	if resp.TokensIn != 3 || resp.TokensOut != 4 {
		t.Fatalf("usage lost: %+v", resp)
	}
}

func TestCompleteRejectsMissingSubtype(t *testing.T) {
	a := fixtureBin(t, `printf '{"type":"result","result":"looks fine","is_error":false,"usage":{"input_tokens":1,"output_tokens":2}}\n'`)
	_, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "hi"}}, Options{Purpose: "test"})
	if err == nil {
		t.Fatal("missing subtype accepted as success")
	}
	var re *ResultError
	if !errors.As(err, &re) || re.TokensIn != 1 || re.TokensOut != 2 {
		t.Fatalf("usage not salvaged on subtype rejection: %v", err)
	}
}

func TestCompleteRejectsErrorSubtypeShapes(t *testing.T) {
	a := fixtureBin(t, `printf '{"type":"result","subtype":"error_during_execution","result":"boom","is_error":false}\n'`)
	if _, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "hi"}}, Options{Purpose: "test"}); err == nil {
		t.Fatal("error_during_execution accepted as success")
	}
}

func TestCompleteTimeoutBranch(t *testing.T) {
	a := fixtureBin(t, "sleep 5")
	_, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "hi"}},
		Options{Timeout: 150e6, Purpose: "test"}) // 150ms
	if err == nil || !strings.Contains(err.Error(), "timed out") {
		t.Fatalf("timeout branch: %v", err)
	}
}

func TestCompleteRunErrCarriesOutput(t *testing.T) {
	a := fixtureBin(t, `echo "fatal: config exploded" 1>&2
exit 3`)
	_, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "hi"}}, Options{Purpose: "test"})
	if err == nil || !strings.Contains(err.Error(), "config exploded") {
		t.Fatalf("diagnostic lost on nonzero exit: %v", err)
	}
}

func TestFindClaudeBinRejectsBrokenCLAUDE_BIN(t *testing.T) {
	t.Setenv("CLAUDE_BIN", filepath.Join(t.TempDir(), "nope"))
	if _, err := FindClaudeBin(); err == nil {
		t.Fatal("nonexistent CLAUDE_BIN accepted — auto would commit to a dead backend")
	}
	dir := t.TempDir()
	t.Setenv("CLAUDE_BIN", dir)
	if _, err := FindClaudeBin(); err == nil {
		t.Fatal("directory CLAUDE_BIN accepted")
	}
	plain := filepath.Join(dir, "not-exec")
	if err := os.WriteFile(plain, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CLAUDE_BIN", plain)
	if _, err := FindClaudeBin(); err == nil {
		t.Fatal("non-executable CLAUDE_BIN accepted")
	}
}

// Suspects must survive the ERROR branches too, not just success
// (adversarial r3, QA — the r2 fix was one-sided).
func TestCompleteCarriesSuspectsOnErrorResult(t *testing.T) {
	a := fixtureBin(t, `printf '{"type":"result","usage":{"input_tokens":"bad"}}\n{"type":"result","subtype":"success","result":"real failure text","is_error":true}\n'`)
	_, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "hi"}}, Options{Purpose: "test"})
	var re *ResultError
	if !errors.As(err, &re) {
		t.Fatalf("want ResultError, got %v", err)
	}
	if len(re.Warnings) != 1 || !strings.Contains(re.Warnings[0], "failed to parse") {
		t.Fatalf("suspect dropped on error branch: %v", re.Warnings)
	}
}

func TestCompleteExecutorLaneBindsCwdEnvAndKeepsTranscript(t *testing.T) {
	// The fixture proves the executor contract end-to-end: pwd is the
	// bound Cwd, BASH_MAX_OUTPUT_LENGTH is injected, the utility-only
	// output ceiling is NOT, and the merged capture survives at
	// TranscriptPath (data-retention: kept on success, not just failure).
	t.Setenv("MARO_BASH_MAX_OUTPUT_CHARS", "7777")
	a := fixtureBin(t, `printf '{"type":"result","subtype":"success","result":"pwd=%s bash_cap=%s ceiling=%s","is_error":false,"usage":{"input_tokens":1,"output_tokens":2}}\n' "$(pwd)" "${BASH_MAX_OUTPUT_LENGTH:-unset}" "${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-unset}"`)
	cwd := t.TempDir()
	tr := filepath.Join(t.TempDir(), "step-1-transcript.jsonl")
	resp, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "work"}},
		Options{AgentTools: true, Cwd: cwd, TranscriptPath: tr, Purpose: "test"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Content, "pwd="+cwd) {
		t.Fatalf("cwd not bound: %q", resp.Content)
	}
	if !strings.Contains(resp.Content, "bash_cap=7777") {
		t.Fatalf("bash cap not injected: %q", resp.Content)
	}
	if !strings.Contains(resp.Content, "ceiling=unset") {
		t.Fatalf("utility ceiling leaked into executor lane: %q", resp.Content)
	}
	raw, err := os.ReadFile(tr)
	if err != nil || !strings.Contains(string(raw), `"type":"result"`) {
		t.Fatalf("transcript not kept at %s: %v", tr, err)
	}
}

func TestCompleteUtilityLaneCeilingReachesChild(t *testing.T) {
	a := fixtureBin(t, `printf '{"type":"result","subtype":"success","result":"ceiling=%s","is_error":false,"usage":{"input_tokens":1,"output_tokens":2}}\n' "${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-unset}"`)
	resp, err := a.Complete(t.Context(), []Message{{Role: "user", Content: "classify"}},
		Options{Purpose: "test"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(resp.Content, "ceiling=16000") {
		t.Fatalf("utility output ceiling missing: %q", resp.Content)
	}
}
