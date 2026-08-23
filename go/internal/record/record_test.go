package record

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
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
	if _, err := r.StampOutcomeVerdict("loopA", &yes, SourceClosure, &conf); err != nil {
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

	// Capture the first stamp's timestamp so the unjudged re-stamp can
	// be shown to ADVANCE it — non-empty alone is satisfied by the
	// first stamp and detects nothing (mutation M34 escape).
	raw, _ = os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	var first map[string]any
	if err := json.Unmarshal([]byte(strings.SplitN(strings.TrimSpace(string(raw)), "\n", 2)[0]), &first); err != nil {
		t.Fatal(err)
	}
	firstAt, _ := first["goal_verdict_at"].(string)

	// Unjudged stamp (nil, nil): source + goal_verdict_at update, prior
	// verdict AND its confidence survive untouched (row-stamp merge
	// semantics — Python parity; runs.StampVerdict's metadata stamp is
	// the full-replacement one where nil pops).
	if _, err := r.StampOutcomeVerdict("loopA", nil, SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if err := json.Unmarshal([]byte(strings.SplitN(strings.TrimSpace(string(raw)), "\n", 2)[0]), &first); err != nil {
		t.Fatal(err)
	}
	if first["goal_achieved"] != true {
		t.Fatalf("nil achieved must not erase a prior verdict: %v", first)
	}
	if first["goal_verdict_confidence"] != 0.85 {
		t.Fatalf("nil confidence must leave the prior value untouched: %v", first)
	}
	// "Unverifiable stamps get it too" (Python): the UNJUDGED re-stamp
	// must itself advance the timestamp — a branch-gated write leaves
	// it stale exactly on the stamps whose only new fact is WHEN.
	if s, _ := first["goal_verdict_at"].(string); s == "" || s == firstAt || s < firstAt {
		t.Fatalf("unjudged re-stamp must advance goal_verdict_at: first=%q now=%q", firstAt, s)
	}
	// An unjudged re-stamp writes NO history (nothing was superseded).
	if _, has := first["verdict_history"]; has {
		t.Fatalf("unjudged re-stamp must not write history: %v", first)
	}
	if _, err := r.StampOutcomeVerdict("missing", &yes, SourceClosure, nil); err == nil {
		t.Fatalf("missing row must error, not silently no-op")
	}
}

// TestStampOutcomeVerdictNewestDuplicateAndHistory: two rows SHARING a
// loop_id — the stamp must land on the newest only (a flipped scan
// direction or dropped break would pass every distinct-id fixture, r3
// Skeptic); and a judged-over-judged re-stamp preserves the superseded
// verdict in verdict_history (Jeremy decree 2026-08-10).
func TestStampOutcomeVerdictNewestDuplicateAndHistory(t *testing.T) {
	ws := t.TempDir()
	r := New(ws)
	for _, status := range []string{"stuck", "done"} {
		if _, err := r.WriteOutcome(Outcome{Goal: "g", Status: status, LoopID: "dup"}); err != nil {
			t.Fatal(err)
		}
	}
	no := false
	if _, err := r.StampOutcomeVerdict("dup", &no, SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	var oldRow, newRow map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &oldRow); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(lines[1]), &newRow); err != nil {
		t.Fatal(err)
	}
	if _, has := oldRow["goal_achieved"]; has {
		t.Fatalf("stamp landed on the OLDER duplicate: %v", oldRow)
	}
	if newRow["goal_achieved"] != false {
		t.Fatalf("newest duplicate must carry the verdict: %v", newRow)
	}
	// Judged-over-judged re-stamp (a correction): history preserves the
	// superseded verdict, honestly.
	yes := true
	conf := 0.7
	if _, err := r.StampOutcomeVerdict("dup", &yes, SourceNowVerify, &conf); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	lines = strings.Split(strings.TrimSpace(string(raw)), "\n")
	if err := json.Unmarshal([]byte(lines[1]), &newRow); err != nil {
		t.Fatal(err)
	}
	if newRow["goal_achieved"] != true {
		t.Fatalf("re-stamp must apply the correction: %v", newRow)
	}
	hist, _ := newRow["verdict_history"].([]any)
	if len(hist) != 1 {
		t.Fatalf("superseded verdict must be preserved in history: %v", newRow)
	}
	entry, _ := hist[0].(map[string]any)
	if entry["goal_achieved"] != false || entry["superseded_by"] != SourceNowVerify {
		t.Fatalf("history entry must carry the old verdict + superseder: %v", entry)
	}
	// The live row's own goal_verdict_at must ADVANCE on every stamp
	// (µs-granularity nowISO) — a branch-gated timestamp would leave it
	// stale while history still captured correctly (r4 Skeptic).
	liveAt, _ := newRow["goal_verdict_at"].(string)
	histAt, _ := entry["goal_verdict_at"].(string)
	if liveAt == "" || liveAt == histAt || liveAt < histAt {
		t.Fatalf("goal_verdict_at must advance on re-stamp: live=%q hist=%q", liveAt, histAt)
	}
}

// TestStampOutcomeVerdictHistoryOnForeignJudgedRow: re-stamping a row
// judged by ANOTHER writer (goal_achieved set at WriteOutcome, no
// source/at keys — a NOW row or a Python row) writes ""-defaulted
// strings into history, never JSON null (r4 Skeptic; Python .get(k,"")
// parity).
func TestStampOutcomeVerdictHistoryOnForeignJudgedRow(t *testing.T) {
	ws := t.TempDir()
	r := New(ws)
	no := false
	if _, err := r.WriteOutcome(Outcome{Goal: "g", Status: "done", LoopID: "f",
		GoalAchieved: &no}); err != nil {
		t.Fatal(err)
	}
	yes := true
	if _, err := r.StampOutcomeVerdict("f", &yes, SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	var row map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(raw))), &row); err != nil {
		t.Fatal(err)
	}
	hist, _ := row["verdict_history"].([]any)
	if len(hist) != 1 {
		t.Fatalf("foreign judged row must still get history on re-stamp: %v", row)
	}
	entry, _ := hist[0].(map[string]any)
	if v, ok := entry["goal_verdict_source"].(string); !ok || v != "" {
		t.Fatalf("missing prior source must be \"\", not null: %v", entry)
	}
	if v, ok := entry["goal_verdict_at"].(string); !ok || v != "" {
		t.Fatalf("missing prior at must be \"\", not null: %v", entry)
	}
}

// TestStampOutcomeVerdictNullGoalAchievedIsUnjudged: a row carrying an
// EXPLICIT JSON null goal_achieved (no Go/Python writer produces one —
// both pop nulls — but foreign tooling can) counts as UNJUDGED: the
// stamp must NOT push verdict_history, matching Python's
// `row.get("goal_achieved") is not None` gate (r6 Skeptic — the r5
// `prior != nil` normalization shipped unpinned).
func TestStampOutcomeVerdictNullGoalAchievedIsUnjudged(t *testing.T) {
	ws := t.TempDir()
	mem := filepath.Join(ws, "memory")
	if err := os.MkdirAll(mem, 0o755); err != nil {
		t.Fatal(err)
	}
	raw := `{"loop_id":"nulljudge","goal":"g","status":"done","goal_achieved":null}` + "\n"
	if err := os.WriteFile(filepath.Join(mem, "outcomes.jsonl"), []byte(raw), 0o644); err != nil {
		t.Fatal(err)
	}
	yes := true
	if _, err := New(ws).StampOutcomeVerdict("nulljudge", &yes, SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(mem, "outcomes.jsonl"))
	var row map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(b))), &row); err != nil {
		t.Fatal(err)
	}
	if _, has := row["verdict_history"]; has {
		t.Fatalf("explicit-null prior is unjudged — no history entry belongs here: %v", row)
	}
	if v, _ := row["goal_achieved"].(bool); !v {
		t.Fatalf("stamp must still land the new verdict: %v", row)
	}
}

// LockedTailAppend: the check-then-append primitive frames a torn tail,
// bounds the read, and hands fn only whole lines.
func TestLockedTailAppendFramesAndBounds(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "ledger.jsonl")
	// Torn tail: last line has no newline.
	if err := os.WriteFile(p, []byte("{\"a\":1}\n{\"torn\":tr"), 0o644); err != nil {
		t.Fatal(err)
	}
	var sawTail string
	err := LockedTailAppend(p, 1<<20, func(tail string) [][]byte {
		sawTail = tail
		return [][]byte{[]byte(`{"b":2}`)}
	})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(p)
	want := "{\"a\":1}\n{\"torn\":tr\n{\"b\":2}\n"
	if string(raw) != want {
		t.Fatalf("framing: %q", raw)
	}
	if sawTail != "{\"a\":1}\n{\"torn\":tr" {
		t.Fatalf("fn tail: %q", sawTail)
	}
	// Bounded read: a small cap must hand fn only the file's suffix, whole
	// lines only.
	err = LockedTailAppend(p, 10, func(tail string) [][]byte {
		sawTail = tail
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if sawTail != "{\"b\":2}\n" {
		t.Fatalf("bounded tail must drop the torn first line: %q", sawTail)
	}
}
