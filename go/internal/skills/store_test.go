package skills

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// pythonRow is a row emitted by fork-point Python (skill_types.skill_to_dict
// + json.dumps, generated live 2026-08-23). It pins the cross-runtime read
// contract: whatever Python writes, Go admits — including the content_hash,
// which both runtimes must recompute to the same digest.
const pythonRow = `{"id": "sk-1", "name": "Web Research", "description": "Fetch and summarize sources", "trigger_patterns": ["research", "look up"], "steps_template": ["search", "read", "summarize"], "source_loop_ids": ["l1"], "created_at": "2026-08-20T10:00:00+00:00", "use_count": 0, "success_rate": 1.0, "content_hash": "251bd33ac618513d231acfd43933aaa1481867e9b04ec3447166739197331256", "tier": "provisional", "utility_score": 1.0, "failure_notes": [], "consecutive_failures": 0, "consecutive_successes": 0, "circuit_state": "closed", "optimization_objective": "", "island": "research", "variant_of": null, "variant_wins": 0, "variant_losses": 0, "project": "", "imported": {}, "origin": "", "domain": "web-research", "tags": ["web", "research", "sources"]}`

func writeStore(t *testing.T, ws string, lines ...string) {
	t.Helper()
	path := skillsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func readStore(t *testing.T, ws string) []string {
	t.Helper()
	raw, err := os.ReadFile(skillsPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, l := range strings.Split(string(raw), "\n") {
		if l != "" {
			out = append(out, l)
		}
	}
	return out
}

// rowOf builds a minimal ADMISSIBLE row, then applies overrides. Overrides
// carrying nil DELETE the key, so a test can pin an absence.
func rowOf(id string, over map[string]any) string {
	base := map[string]any{
		"id": id, "name": "n-" + id, "description": "d",
		"trigger_patterns": []any{"trigger " + id}, "steps_template": []any{"s"},
		"source_loop_ids": []any{}, "created_at": "2026-08-20T10:00:00+00:00",
		"use_count": 0, "success_rate": 1.0, "tier": "provisional",
		"utility_score": 1.0, "failure_notes": []any{},
		"consecutive_failures": 0, "consecutive_successes": 0,
		"circuit_state": "closed", "optimization_objective": "",
		"island": "", "variant_of": nil, "variant_wins": 0,
		"variant_losses": 0, "project": "", "imported": map[string]any{},
		"origin": "", "domain": "", "tags": []any{},
	}
	// content_hash must match the content, or the row is admissible but
	// flagged; tests that care set it explicitly.
	s, _ := DictToSkill(map[string]any{"id": id, "name": "n-" + id,
		"description": "d", "steps_template": []any{"s"}})
	base["content_hash"] = ComputeSkillHash(s)
	for k, v := range over {
		if v == nil {
			delete(base, k)
			continue
		}
		base[k] = v
	}
	raw, _ := json.Marshal(base)
	return string(raw)
}

func TestLoadSkillsReadsPythonWrittenRow(t *testing.T) {
	ws := t.TempDir()
	writeStore(t, ws, pythonRow)
	res := LoadSkills(ws)
	if len(res.Skills) != 1 {
		t.Fatalf("want the Python row admitted, got %+v", res)
	}
	s := res.Skills[0]
	if s.ID != "sk-1" || s.Domain != "web-research" || len(s.Tags) != 3 {
		t.Fatalf("fields lost in translation: %+v", s)
	}
	// Both runtimes must recompute the SAME digest, or a Go save would
	// rewrite every Python row's hash (and vice versa).
	if got := ComputeSkillHash(s); got != s.ContentHash {
		t.Fatalf("hash divergence: go=%s python=%s", got, s.ContentHash)
	}
	if len(res.HashMismatch) != 0 {
		t.Fatalf("Python row must not read as tampered: %+v", res.HashMismatch)
	}
}

func TestLoadSkillsLastRowWinsPerID(t *testing.T) {
	ws := t.TempDir()
	writeStore(t, ws,
		rowOf("a", map[string]any{"description": "old"}),
		rowOf("a", map[string]any{"description": "new"}))
	res := LoadSkills(ws)
	if len(res.Skills) != 1 || res.Skills[0].Description != "new" {
		t.Fatalf("last row must win: %+v", res.Skills)
	}
}

// The id is claimed AFTER the proof. A drifted NEWER row must not hide the
// older WORKING row — Python r10/r11: with the id claimed and the row
// skipped, the valid row was in no caller's list and the next save deleted
// it as a deliberate drop.
func TestLoadSkillsDriftedRowDoesNotClaimTheID(t *testing.T) {
	ws := t.TempDir()
	writeStore(t, ws,
		rowOf("a", map[string]any{"description": "the working one"}),
		rowOf("a", map[string]any{"utility_score": "not-a-number"}))
	res := LoadSkills(ws)
	if len(res.Skills) != 1 {
		t.Fatalf("the older valid row must survive: %+v", res)
	}
	if res.Skills[0].Description != "the working one" {
		t.Fatalf("wrong row survived: %+v", res.Skills[0])
	}
	if res.Drifted != 1 {
		t.Fatalf("the drop must be announced, drifted=%d", res.Drifted)
	}
}

func TestLoadSkillsTornByteCostsOneRowNotTheLoad(t *testing.T) {
	ws := t.TempDir()
	path := skillsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	content := rowOf("a", nil) + "\n" + "{\"id\":\"b\",\"name\":\"\xff\xfe\"}" +
		"\n" + rowOf("c", nil) + "\n"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	res := LoadSkills(ws)
	if len(res.Skills) != 2 {
		t.Fatalf("a torn byte must cost its row only: %+v", res)
	}
	if res.Unparseable != 1 {
		t.Fatalf("the loss must be announced: %+v", res)
	}
}

func TestLoadSkillsFlagsHashMismatch(t *testing.T) {
	ws := t.TempDir()
	writeStore(t, ws, rowOf("a", map[string]any{
		"content_hash": strings.Repeat("0", 64)}))
	res := LoadSkills(ws)
	if len(res.Skills) != 1 || len(res.HashMismatch) != 1 {
		t.Fatalf("tampering must be reported, not fatal: %+v", res)
	}
}

// A row that cannot be PROVEN to be a Skill is not a version of that skill,
// so a save of the same id must not delete it (Python r10, probed with
// exactly this operator note).
func TestSaveSkillCarriesUnprovableSameIDRow(t *testing.T) {
	ws := t.TempDir()
	keep := `{"id":"same","operator_note":"keep this row"}`
	writeStore(t, ws, keep)
	s, _ := DictToSkill(map[string]any{"id": "same", "name": "n",
		"description": "d", "created_at": "2026-08-20T10:00:00+00:00"})
	if err := SaveSkill(ws, s); err != nil {
		t.Fatal(err)
	}
	lines := readStore(t, ws)
	if len(lines) != 2 || lines[0] != keep {
		t.Fatalf("the unprovable row must be carried verbatim: %+v", lines)
	}
}

func TestSaveSkillCarriesTaintedLineVerbatimAndReplacesItsOwn(t *testing.T) {
	ws := t.TempDir()
	path := skillsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	tainted := "{\"id\":\"x\",\"name\":\"\xff\"}"
	old := rowOf("a", map[string]any{"description": "old"})
	if err := os.WriteFile(path, []byte(tainted+"\n"+old+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	s, _ := DictToSkill(map[string]any{"id": "a", "name": "n-a",
		"description": "new", "created_at": "2026-08-20T10:00:00+00:00"})
	if err := SaveSkill(ws, s); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), tainted) {
		t.Fatalf("tainted bytes must survive verbatim, got %q", raw)
	}
	res := LoadSkills(ws)
	if len(res.Skills) != 1 || res.Skills[0].Description != "new" {
		t.Fatalf("own row must be replaced: %+v", res.Skills)
	}
}

// The writer proves what the reader will admit — refusing BEFORE the store
// is touched, so a bad skill can never delete the healthy row it replaces.
func TestSaveSkillRefusesTaintedContentWithoutTouchingTheStore(t *testing.T) {
	ws := t.TempDir()
	original := rowOf("a", map[string]any{"description": "healthy"})
	writeStore(t, ws, original)

	bad, _ := DictToSkill(map[string]any{"id": "a", "name": "n",
		"description": "d", "created_at": "2026-08-20T10:00:00+00:00"})
	bad.Tier = "\xff\xfe" // hash-excluded field: the hash cannot see this
	if err := SaveSkill(ws, bad); err == nil {
		t.Fatal("a byte-tainted skill must be refused")
	}
	if lines := readStore(t, ws); len(lines) != 1 || lines[0] != original {
		t.Fatalf("a refused save must not touch the store: %+v", lines)
	}
}

func TestSaveSkillRefusesNonFiniteScore(t *testing.T) {
	ws := t.TempDir()
	s, _ := DictToSkill(map[string]any{"id": "a", "name": "n",
		"description": "d", "created_at": "2026-08-20T10:00:00+00:00"})
	s.UtilityScore = mathInf()
	if err := SaveSkill(ws, s); err == nil {
		t.Fatal("a non-finite score must be refused (Python allow_nan=False)")
	}
	if _, err := os.Stat(skillsPath(ws)); err == nil {
		t.Fatal("the refused save must not have created the store")
	}
}

func TestArchiveSkillsProvesEveryLineBeforeAnyAppend(t *testing.T) {
	ws := t.TempDir()
	good, _ := DictToSkill(map[string]any{"id": "a", "name": "n",
		"description": "d", "created_at": "2026-08-20T10:00:00+00:00"})
	bad := good
	bad.ID = "b"
	bad.Island = "\xff"
	if err := ArchiveSkills(ws, []Skill{good, bad}, "island_cull"); err == nil {
		t.Fatal("a refused line must abort the whole archive")
	}
	if _, err := os.Stat(skillsArchivePath(ws)); err == nil {
		t.Fatal("nothing may land when the batch is refused")
	}
	if err := ArchiveSkills(ws, []Skill{good}, "island_cull"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(skillsArchivePath(ws))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `"archived_reason":"island_cull"`) ||
		!strings.Contains(string(raw), `"archived_at":`) {
		t.Fatalf("archive stamp missing: %q", raw)
	}
	// The stamp must EXTEND the proven line, not reorder its keys.
	if !strings.HasPrefix(string(raw), `{"id":"a"`) {
		t.Fatalf("archive re-marshalled the row: %q", raw)
	}
}

func mathInf() float64 { return math.Inf(1) }
