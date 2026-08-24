package loop

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestSlotHolderMetadataIsJSONDumps: the per-project lock file carries
// the holder's identity, and interrupt.py writes it with
// `path.write_text(json.dumps(payload))`. The GOAL goes in verbatim —
// which is user text, so `>` and non-ASCII are the normal case, not the
// exotic one — and this file is read by whichever runtime contends next.
// A Go-written lock file was therefore telling a Python contender the
// holder's goal in bytes json.dumps never produces
// (adversarial mission-r8).
func TestSlotHolderMetadataIsJSONDumps(t *testing.T) {
	dir := t.TempDir()
	goal := "compare a > b for the café rollout"

	release, warn, err := acquireProjectSlot(dir, "proj", "loop-1", goal)
	if err != nil {
		t.Fatalf("the slot must be free in a fresh dir: %v", err)
	}
	if warn != "" {
		t.Fatalf("no gate warning expected: %s", warn)
	}
	defer release()

	raw, err := os.ReadFile(filepath.Join(dir, "loop-proj.lock"))
	if err != nil {
		t.Fatalf("holder metadata was not written: %v", err)
	}
	line := strings.TrimRight(string(raw), "\x00\n")

	for _, want := range []string{
		`{"loop_id": "loop-1"`, // writer's order + separators
		"\"goal\": \"compare a > b for the caf\\u00e9 rollout\"", // plain >, escaped e
		`"started": "`,
	} {
		if !strings.Contains(line, want) {
			t.Errorf("holder metadata is not json.dumps-shaped (missing %s):\n%s",
				want, line)
		}
	}
	// pid is an int in both runtimes and must not have become a float.
	if strings.Contains(line, `"pid": `) && strings.Contains(line, ".0,") {
		t.Errorf("pid must stay an int:\n%s", line)
	}

	// Anti-vacuity: the pre-fix encoder over the same payload, required to
	// lose — and to lose on all three of sorting, HTML escaping and
	// ensure_ascii, so no single fixture property carries the test.
	old, err := json.Marshal(map[string]any{
		"loop_id": "loop-1", "pid": 1, "goal": goal, "started": "x",
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, marker := range []string{
		`{"goal":`,     // SORTED: the writer's first key is loop_id
		`a \u003e b`,   // HTML-escaped into six literal chars
		"caf\u00e9 ro", // raw UTF-8, where ensure_ascii writes the escape
	} {
		if !strings.Contains(string(old), marker) {
			t.Fatalf("the pre-fix encoder does not exhibit %s here, so one of "+
				"the forks is untested:\n%s", marker, old)
		}
	}
}
