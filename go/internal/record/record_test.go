package record

import (
	"fmt"
	"sync"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteOutcomeOmitsCostRatherThanLying(t *testing.T) {
	ws := t.TempDir()
	if _, err := New(ws).WriteOutcome(Outcome{Goal: "g", Status: "done"}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(raw))), &row); err != nil {
		t.Fatal(err)
	}
	// No estimator is ported; a hardcoded 0.0 would be a record that lies
	// about spend (adversarial round 2026-08-22). Missing != zero.
	if _, present := row["cost_usd"]; present {
		t.Fatal("cost_usd present without an estimator — a lying record")
	}
	for _, key := range []string{"outcome_id", "recorded_at", "measurement_class"} {
		if _, ok := row[key]; !ok {
			t.Fatalf("row missing %q", key)
		}
	}
}

// Ported from Python locked_append: a crash-torn tail (no final LF) must
// be framed before appending, so the fragment strands as its own row
// instead of fusing with ours.
func TestAppendFramesTornTail(t *testing.T) {
	ws := t.TempDir()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "captains_log.jsonl")
	if err := os.WriteFile(path, []byte(`{"torn": tru`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := New(ws).Event("E", "s", "sum", nil, "lp"); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("want torn fragment + new row on separate lines, got %d: %q", len(lines), raw)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(lines[1]), &row); err != nil {
		t.Fatalf("new row fused with torn tail: %v (%q)", err, lines[1])
	}
	if row["event_type"] != "E" {
		t.Fatalf("row content wrong: %v", row)
	}
}

// The Python-interop lock protocol: appends flock the same sibling
// "<name>.lock" file file_lock.locked_append uses.
func TestAppendTakesPythonCompatibleLockFile(t *testing.T) {
	ws := t.TempDir()
	if err := New(ws).Event("E", "s", "sum", nil, ""); err != nil {
		t.Fatal(err)
	}
	lock := filepath.Join(ws, "memory", "captains_log.jsonl.lock")
	if _, err := os.Stat(lock); err != nil {
		t.Fatalf("lock file not created at the Python path: %v", err)
	}
}

func TestNewIDShape(t *testing.T) {
	a, b := NewID(), NewID()
	if len(a) != 8 || a == b {
		t.Fatalf("ids: %q %q", a, b)
	}
}

// The lock's actual job, contended: concurrent appenders must produce
// exactly N*M valid rows, no torn/fused lines (adversarial r3, QA:
// "lock file exists" is not "lock serializes").
func TestConcurrentAppendsDoNotCorrupt(t *testing.T) {
	ws := t.TempDir()
	rec := New(ws)
	const workers, rows = 8, 25
	var wg sync.WaitGroup
	errs := make(chan error, workers)
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < rows; i++ {
				if err := rec.Event("E", "s",
					strings.Repeat("payload-", 100)+fmt.Sprintf("w%d-i%d", w, i),
					nil, "lp"); err != nil {
					errs <- err
					return
				}
			}
		}(w)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != workers*rows {
		t.Fatalf("want %d rows, got %d — rows lost or fused", workers*rows, len(lines))
	}
	uniq := map[string]bool{}
	for n, line := range lines {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("line %d torn/fused: %v", n, err)
		}
		msg, _ := m["summary"].(string)
		idx := strings.LastIndex(msg, "w")
		if idx < 0 {
			t.Fatalf("line %d payload missing tag: %q", n, msg)
		}
		tag := msg[idx:]
		if uniq[tag] {
			t.Fatalf("duplicate append %q — a drop compensated by a double-write", tag)
		}
		uniq[tag] = true
	}
	if len(uniq) != workers*rows {
		t.Fatalf("want %d distinct payloads, got %d", workers*rows, len(uniq))
	}
}

// TestStampOutcomeVerdictPatchesNewestRow: the post-hoc row stamp
// (Python memory_ledger.stamp_outcome_verdict) patches the NEWEST
// matching row, sets/leaves goal_achieved per tri-state, and never
// fabricates a confidence (adversarial routing r2, both lenses).
func TestStampOutcomeVerdictPatchesNewestRow(t *testing.T) {
	ws := t.TempDir()
	r := New(ws)
	if _, err := r.WriteOutcome(Outcome{Goal: "g", Status: "done", LoopID: "loopA"}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.WriteOutcome(Outcome{Goal: "g2", Status: "done", LoopID: "loopB"}); err != nil {
		t.Fatal(err)
	}
	yes := true
	conf := 0.85
	if err := r.StampOutcomeVerdict("loopA", &yes, SourceClosure, &conf); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var rows []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("row unreadable after rewrite: %v", err)
		}
		rows = append(rows, m)
	}
	if len(rows) != 2 {
		t.Fatalf("rewrite must not add/drop rows: %d", len(rows))
	}
	if rows[0]["goal_achieved"] != true || rows[0]["goal_verdict_source"] != SourceClosure ||
		rows[0]["goal_verdict_confidence"] != 0.85 {
		t.Fatalf("loopA row not stamped: %v", rows[0])
	}
	if _, has := rows[1]["goal_achieved"]; has {
		t.Fatalf("loopB row must be untouched: %v", rows[1])
	}

	// Unjudged stamp (nil, nil): source updates, prior verdict survives,
	// confidence key REMOVED, not zeroed.
	if err := r.StampOutcomeVerdict("loopA", nil, SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	var first map[string]any
	if err := json.Unmarshal([]byte(strings.SplitN(strings.TrimSpace(string(raw)), "\n", 2)[0]), &first); err != nil {
		t.Fatal(err)
	}
	if first["goal_achieved"] != true {
		t.Fatalf("nil achieved must not erase a prior verdict: %v", first)
	}
	if _, has := first["goal_verdict_confidence"]; has {
		t.Fatalf("nil confidence must remove the key: %v", first)
	}
	if err := r.StampOutcomeVerdict("missing", &yes, SourceClosure, nil); err == nil {
		t.Fatalf("missing row must error, not silently no-op")
	}
}
