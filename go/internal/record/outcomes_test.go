package record

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func seedOutcomes(t *testing.T, ws string, lines ...string) {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	content := ""
	for _, l := range lines {
		content += l + "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "outcomes.jsonl"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// Must-detect: newest FIRST (M84 target: returning file order). The
// inspector's "Recent outcomes" head and the evolver window both key
// on this.
func TestLoadOutcomesNewestFirstWithLimit(t *testing.T) {
	ws := t.TempDir()
	seedOutcomes(t, ws,
		`{"goal": "first", "status": "done"}`,
		`{"goal": "second", "status": "done"}`,
		`{"goal": "third", "status": "stuck"}`,
	)
	rows, err := LoadOutcomes(ws, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("limit not applied: %d rows", len(rows))
	}
	if rows[0]["goal"] != "third" || rows[1]["goal"] != "second" {
		t.Fatalf("not newest-first: %v, %v", rows[0]["goal"], rows[1]["goal"])
	}
}

func TestLoadOutcomesTornLineCostsOneRow(t *testing.T) {
	ws := t.TempDir()
	seedOutcomes(t, ws,
		`{"goal": "good one", "status": "done"}`,
		`{"goal": "torn`,
		`{"goal": "good two", "status": "done"}`,
	)
	rows, err := LoadOutcomes(ws, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("torn line must cost one row, not the read: %d rows", len(rows))
	}
}

func TestLoadOutcomesMissingFileIsEmptyStore(t *testing.T) {
	rows, err := LoadOutcomes(t.TempDir(), 10)
	if err != nil || rows != nil {
		t.Fatalf("missing file must be empty store: rows=%v err=%v", rows, err)
	}
}

// limit <= 0 means ALL rows — the zero value degrades to "everything",
// never to "nothing" (the recall r1 LoadOptions lesson, applied here).
func TestLoadOutcomesZeroLimitMeansAll(t *testing.T) {
	ws := t.TempDir()
	seedOutcomes(t, ws,
		`{"goal": "a"}`, `{"goal": "b"}`, `{"goal": "c"}`,
	)
	rows, err := LoadOutcomes(ws, 0)
	if err != nil || len(rows) != 3 {
		t.Fatalf("zero limit must return all: %d err=%v", len(rows), err)
	}
}

func TestLockedRMWCreatesAndRewrites(t *testing.T) {
	path := filepath.Join(t.TempDir(), "memory", "counter.json")
	if err := LockedRMW(path, func(old string) string {
		if old != "" {
			t.Fatalf("missing file must present as empty, got %q", old)
		}
		return `{"n": 1}`
	}); err != nil {
		t.Fatal(err)
	}
	if err := LockedRMW(path, func(old string) string {
		var m map[string]any
		if json.Unmarshal([]byte(old), &m) != nil || m["n"] != float64(1) {
			t.Fatalf("second RMW did not see first write: %q", old)
		}
		return `{"n": 2}`
	}); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != `{"n": 2}` {
		t.Fatalf("final content wrong: %s", raw)
	}
}
