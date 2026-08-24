package runs

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func readMeta(t *testing.T, rd string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(rd, "metadata.json"))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func TestCreateSeedsSkeletonAndMetadata(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "h1", "the goal text")
	if err != nil {
		t.Fatal(err)
	}
	for _, sub := range []string{"source", "build", "artifact"} {
		if fi, err := os.Stat(filepath.Join(rd, sub)); err != nil || !fi.IsDir() {
			t.Fatalf("skeleton dir %s missing", sub)
		}
	}
	prompt, _ := os.ReadFile(filepath.Join(rd, "source", "prompt.txt"))
	if string(prompt) != "the goal text" {
		t.Fatalf("prompt.txt: %q", prompt)
	}
	m := readMeta(t, rd)
	if m["handle_id"] != "h1" || m["prompt"] != "the goal text" ||
		m["status"] != "running" || m["started_at"] == nil {
		t.Fatalf("metadata: %v", m)
	}
	// Idempotent re-create must not clobber prompt.txt (first call wins).
	if err := os.WriteFile(filepath.Join(rd, "source", "prompt.txt"), []byte("edited"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Create(ws, "h1", "different prompt"); err != nil {
		t.Fatal(err)
	}
	prompt, _ = os.ReadFile(filepath.Join(rd, "source", "prompt.txt"))
	if string(prompt) != "edited" {
		t.Fatalf("re-create clobbered prompt.txt: %q", prompt)
	}
}

// TestWriteMetadataMergeSemantics: started_at first-writer-wins, nil
// POPS a key (tri-state delete, not "set to null"), other keys survive.
func TestWriteMetadataMergeSemantics(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "h2", "g")
	if err != nil {
		t.Fatal(err)
	}
	started := readMeta(t, rd)["started_at"]
	if err := WriteMetadata(rd, pyval.Obj{
		{Key: "started_at", Val: "2020-01-01T00:00:00Z"}, // must be ignored
		{Key: "custom", Val: "kept"},
		{Key: "goal_achieved", Val: true},
	}); err != nil {
		t.Fatal(err)
	}
	m := readMeta(t, rd)
	if m["started_at"] != started {
		t.Fatalf("started_at overwritten: %v -> %v", started, m["started_at"])
	}
	if m["custom"] != "kept" || m["goal_achieved"] != true {
		t.Fatalf("merge: %v", m)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "goal_achieved", Val: nil}}); err != nil {
		t.Fatal(err)
	}
	m = readMeta(t, rd)
	if _, present := m["goal_achieved"]; present {
		t.Fatalf("nil must POP the key, not write null: %v", m)
	}
	if m["custom"] != "kept" {
		t.Fatalf("unrelated key lost on pop: %v", m)
	}
}

// TestStampVerdictTriStateAndReplacement: every member set or popped —
// nothing left to a merge (the drifted-second-field-list lesson). An
// unjudged stamp (nil) removes a previous judged stamp entirely.
func TestStampVerdictTriStateAndReplacement(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "h3", "g")
	if err != nil {
		t.Fatal(err)
	}
	no := false
	conf := 0.9
	gaps := []string{"g1", "g2", "g3", "g4", "g5", "g6", "g7"}
	if err := StampVerdict(rd, &no, "go_closure_v1", "Not achieved: x",
		&conf, "behavioral gap", gaps); err != nil {
		t.Fatal(err)
	}
	m := readMeta(t, rd)
	if m["goal_achieved"] != false || m["goal_verdict_downgrade_reason"] != "behavioral gap" {
		t.Fatalf("stamp: %v", m)
	}
	if m["goal_verdict_confidence"] != 0.9 {
		t.Fatalf("judged confidence must be written: %v", m)
	}
	stamped, _ := m["goal_verdict_gaps"].([]any)
	if len(stamped) != 6 || !strings.Contains(stamped[5].(string), "(+2 more gap(s)") {
		t.Fatalf("gap cap must announce its cut: %v", stamped)
	}
	// Replacement by an unjudged clean verdict: booleans/downgrade/gaps
	// must all clear, not linger from the judged predecessor.
	if err := StampVerdict(rd, nil, "go_closure_v1", "Not judged.", nil, "", nil); err != nil {
		t.Fatal(err)
	}
	m = readMeta(t, rd)
	if _, present := m["goal_achieved"]; present {
		t.Fatalf("unjudged stamp left goal_achieved standing: %v", m)
	}
	// nil confidence POPS the key — a fabricated 0 would read as
	// "verified with zero confidence" (Python: confidence=None pops).
	if _, present := m["goal_verdict_confidence"]; present {
		t.Fatalf("nil confidence must pop the key, not write 0: %v", m)
	}
	if _, present := m["goal_verdict_downgrade_reason"]; present {
		t.Fatalf("stale downgrade reason survived replacement: %v", m)
	}
	if _, present := m["goal_verdict_gaps"]; present {
		t.Fatalf("stale gaps survived replacement: %v", m)
	}
}

func TestAppendVerdictRowAppends(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "h4", "g")
	if err != nil {
		t.Fatal(err)
	}
	if err := AppendVerdictRow(rd, "loop-a",
		pyval.Obj{{Key: "skipped", Val: "no_checks_generated"}}); err != nil {
		t.Fatal(err)
	}
	if err := AppendVerdictRow(rd, "loop-b",
		pyval.Obj{{Key: "complete", Val: true}}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(rd, "build", "closure_verdicts.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("rows: %q", lines)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &row); err != nil || row["ts"] == nil {
		t.Fatalf("row 0 unstamped: %q %v", lines[0], err)
	}
	// loop_id is Python's second key and is what tells a restarted run's
	// parent verdict from its child in this append-only file — the §9.3
	// join nothing else persists. It was absent from every Go row until
	// mission-r7.
	if row["loop_id"] != "loop-a" {
		t.Fatalf("row 0 must carry its loop_id: %q", lines[0])
	}
	var row1 map[string]any
	if err := json.Unmarshal([]byte(lines[1]), &row1); err != nil ||
		row1["loop_id"] != "loop-b" {
		t.Fatalf("row 1 must carry ITS OWN loop_id, not row 0's: %q", lines[1])
	}
}

// TestAppendVerdictRowScrubsSecrets: the row writer is the single scrub
// owner (Python: scrub({...**row}) in _persist_verdict_row) — probe
// stderr and LLM prose land in a durable jsonl and nothing downstream
// rescrubs it (adversarial closure r1 2026-08-22, Skeptic HIGH).
func TestAppendVerdictRowScrubsSecrets(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "build"), 0o755); err != nil {
		t.Fatal(err)
	}
	leaky := "curl -H 'Authorization: Bearer sk-ant-api03-" +
		strings.Repeat("a", 40) + "' https://x"
	if err := AppendVerdictRow(dir, "loop-scrub", pyval.Obj{
		{Key: "summary", Val: leaky},
		// []map[string]any AND the ordered spelling: both nests have to
		// be walked, and the ordered one is newer than the scrub
		// (mission-r7).
		{Key: "check_results", Val: []map[string]any{{"stderr": leaky}}},
		{Key: "ordered_results", Val: pyval.List{
			pyval.Obj{{Key: "stderr", Val: leaky}}}},
	}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "build", "closure_verdicts.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "sk-ant-api03-") {
		t.Fatalf("secret survived into the durable row: %s", raw)
	}
	if !strings.Contains(string(raw), "Authorization") {
		t.Fatalf("scrub should redact the secret, not the whole field: %s", raw)
	}
}

// TestAppendVerdictRowPreservesLargeIntegers: the scrub round-trip must
// not widen numerics through float64 — an int64 past 2^53 written by a
// future caller would silently corrupt (r2 Architect; UseNumber keeps
// json.Number through scrub.Walk untouched).
func TestAppendVerdictRowPreservesLargeIntegers(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "build"), 0o755); err != nil {
		t.Fatal(err)
	}
	big := int64(9007199254740993) // 2^53 + 1 — not representable in float64
	if err := AppendVerdictRow(dir, "loop-big",
		pyval.Obj{{Key: "big", Val: big}}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "build", "closure_verdicts.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "9007199254740993") {
		t.Fatalf("large integer corrupted through the round-trip: %s", raw)
	}
}
