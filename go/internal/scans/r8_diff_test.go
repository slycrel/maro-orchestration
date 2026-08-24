package scans

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestEscalationAndEventRowsAreJSONDumpsShaped drives the two writers in
// this file, both of which r7's writer sweep missed entirely because the
// sweep was an ENUMERATION of eight files rather than a search for the
// class.
//
// escalations.jsonl is the decreed headless escalation surface — "the
// thing you check when nothing else is configured" — and events.jsonl is
// the cross-runtime feed maro-observe tails. Python writes both with a
// bare json.dumps, and both were on encoding/json here: `>` in a reason
// (every "A -> B" recommendation) came out `>`, and a non-ASCII
// character came out raw where json.dumps writes `\uXXXX`.
func TestEscalationAndEventRowsAreJSONDumpsShaped(t *testing.T) {
	ws := t.TempDir()

	writeEscalation(ws, map[string]any{
		"ts":         "2026-08-23T00:00:00Z",
		"event_type": "escalation",
		"summary":    "prefer a > b in the café path",
	})
	esc := readOneLine(t, filepath.Join(ws, "output", "escalations.jsonl"))
	assertPythonShaped(t, "escalations.jsonl", esc)

	writeEvent(ws, "escalation", "prefer a > b in the café path",
		"prefer a > b in the café path")
	ev := readOneLine(t, filepath.Join(ws, "memory", "events.jsonl"))
	assertPythonShaped(t, "events.jsonl", ev)
}

func assertPythonShaped(t *testing.T, name, line string) {
	t.Helper()
	// The six literal characters, not the rune: this is the ESCAPE
	// sequence encoding/json emits and json.dumps never does.
	if strings.Contains(line, `\u003e`) {
		t.Fatalf("%s is HTML-escaped: no CPython writer produces \\u003e for `>`\n%s",
			name, line)
	}
	if !strings.Contains(line, `caf\u00e9`) {
		t.Fatalf("%s is not ensure_ascii: json.dumps escapes é\n%s", name, line)
	}
	if !strings.Contains(line, `": "`) {
		t.Fatalf("%s does not carry json.dumps' key separator\n%s", name, line)
	}
	if strings.Contains(line, "\n") {
		t.Fatalf("%s row must be ONE line\n%s", name, line)
	}
}

func readOneLine(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("no %s: %v", path, err)
	}
	return strings.TrimRight(string(raw), "\n")
}
