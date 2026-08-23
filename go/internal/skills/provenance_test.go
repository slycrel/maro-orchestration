package skills

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The record's BYTES are the interop contract: Python's load_skill_provenance
// globs this directory, and doctor-style audits compare records across
// runtimes. This expectation was not hand-written — it is the exact output of
// Python's write_skill_provenance for these arguments, captured by a live
// differential on 2026-08-23 (an isolated MARO_WORKSPACE, verified resolved
// before the write), and re-read back by load_skill_provenance to confirm the
// Go-written file parses on the Python side.
func TestProvenanceRecordMatchesPythonBytes(t *testing.T) {
	ws := t.TempDir()
	if err := WriteSkillProvenance(ws, ProvenanceRecord{
		SkillName: "http-fetch-v1.2", Decision: "demote",
		Reason:        "utility_score=0.100 < 0.4",
		SuccessRate:   1.0,
		SourceLoopIDs: []string{"loop-a", "loop-b"},
		DecidedAt:     "2026-08-23T09:19:44.401102+00:00",
		Extra: []ProvenanceField{
			{"utility_score", 0.1}, {"circuit_state", "closed"},
		},
	}); err != nil {
		t.Fatal(err)
	}
	// The filename carries the skill NAME and a microsecond stamp — and the
	// name's own '.' must survive, since strftime's %f is digits only and a
	// naive strip of the first point corrupted "v1.2".
	want := "http-fetch-v1.2_20260823T091944401102Z.json"
	path := filepath.Join(ws, "memory", "skill_provenance", want)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("expected %s: %v", want, err)
	}
	// json.dumps(record, indent=2): two-space indent, ", " between key and
	// value, Python's float spelling (1.0, not 1), no trailing newline, and
	// keys in the dict's INSERTION order with **extra spread last.
	const python = `{
  "skill_name": "http-fetch-v1.2",
  "decision": "demote",
  "reason": "utility_score=0.100 < 0.4",
  "decided_at": "2026-08-23T09:19:44.401102+00:00",
  "success_rate": 1.0,
  "efficiency_score": 0.0,
  "source_loop_ids": [
    "loop-a",
    "loop-b"
  ],
  "utility_score": 0.1,
  "circuit_state": "closed"
}`
	if string(raw) != python {
		t.Fatalf("record diverges from Python's bytes:\n--- want ---\n%s\n--- got ---\n%s",
			python, raw)
	}
}

// An empty list is "[]" inline; Python's indent writer emits no newline for
// an empty container. A record with no extras stops at source_loop_ids.
func TestProvenanceRendersEmptyListInline(t *testing.T) {
	ws := t.TempDir()
	if err := WriteSkillProvenance(ws, ProvenanceRecord{
		SkillName: "s", Decision: "retire", Reason: "r",
		DecidedAt: "2026-08-23T09:19:44.401102+00:00",
	}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "skill_provenance",
		"s_20260823T091944401102Z.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(string(raw), "\"source_loop_ids\": []\n}") {
		t.Fatalf("empty list must render inline and last: %s", raw)
	}
}

// An extra whose key repeats a modeled one REPLACES the value and keeps the
// modeled position — Python's `{**modeled, **extra}` spread.
func TestProvenanceExtraOverridesInPlace(t *testing.T) {
	ws := t.TempDir()
	if err := WriteSkillProvenance(ws, ProvenanceRecord{
		SkillName: "s", Decision: "retire", Reason: "r", SuccessRate: 0.5,
		DecidedAt: "2026-08-23T09:19:44.401102+00:00",
		Extra:     []ProvenanceField{{"success_rate", 0.9}},
	}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "skill_provenance",
		"s_20260823T091944401102Z.json"))
	if err != nil {
		t.Fatal(err)
	}
	body := string(raw)
	if strings.Count(body, "success_rate") != 1 || !strings.Contains(body, `"success_rate": 0.9`) {
		t.Fatalf("override must replace in place, not append: %s", body)
	}
	if strings.Index(body, "success_rate") > strings.Index(body, "efficiency_score") {
		t.Fatalf("the modeled position must be kept: %s", body)
	}
}

// Python builds the same path and lets the write raise into a debug-level
// except, so a name that is not a filename loses the record silently. Here
// it is an error the caller announces.
func TestProvenanceRefusesNamesThatAreNotFilenames(t *testing.T) {
	ws := t.TempDir()
	for _, name := range []string{"", "../escape", "a/b", ".hidden"} {
		err := WriteSkillProvenance(ws, ProvenanceRecord{
			SkillName: name, Decision: "retire",
		})
		if err == nil {
			t.Errorf("%q was accepted as a filename", name)
		}
	}
	if _, err := os.Stat(filepath.Join(ws, "memory", "skill_provenance")); err == nil {
		t.Fatal("a refused record must not create the store directory")
	}
}
