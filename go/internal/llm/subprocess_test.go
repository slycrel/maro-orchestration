package llm

import (
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
	res, err := scanForResult(out)
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
	_, err := scanForResult(out)
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
	res, err := scanForResult(out)
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
