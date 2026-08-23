package runs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The manifest's BYTES matter: it is the cross-runtime attribution rail, so
// a Go-written line and a Python-written line for the same injection must
// diff clean. This was the one writer in the chunk still on plain
// json.Marshal, which sorts keys alphabetically ("match" before "stage"),
// escapes < > & as <-style sequences, and spells a whole float without
// its ".0". The expectation below is Python's json.dumps output for the
// same record, minus its separator spacing.
func TestSkillsManifestMatchesPythonsBytes(t *testing.T) {
	dir := t.TempDir()
	parent := "parent-1"
	err := AppendSkillsManifest(dir, []SkillManifestEntry{{
		ID: "a&b", Name: `Report <x>`, ContentHash: "h1", VariantOf: &parent,
		Tier: "established", RoutingKey: "task-9",
		MatchMethod: "keyword", MatchScore: 2.0,
	}}, "decompose", &SkillManifestMeta{
		Method: "keyword", NCandidates: 3, TopScore: 2.0,
	}, "2026-08-23T09:19:44.401102+00:00")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "source", "skills_manifest.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"ts":"2026-08-23T09:19:44.401102+00:00","stage":"decompose",` +
		`"skills":[{"id":"a&b","name":"Report <x>","content_hash":"h1",` +
		`"variant_of":"parent-1","tier":"established","routing_key":"task-9",` +
		`"match_method":"keyword","match_score":2.0}],` +
		`"match":{"method":"keyword","n_candidates":3,"top_score":2.0}}` + "\n"
	if string(raw) != want {
		t.Fatalf("\nwant %s\ngot  %s", want, raw)
	}
}

// An EMPTY entries list is RECORDED, not skipped: absence of this file used
// to mean two different things, which makes it useless as an attribution
// rail. A nil variant_of renders as null, the way the dataclass field does.
func TestSkillsManifestRecordsAnEmptyInjection(t *testing.T) {
	dir := t.TempDir()
	if err := AppendSkillsManifest(dir, nil, "decompose",
		&SkillManifestMeta{Method: "none"}, "T"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "source", "skills_manifest.jsonl"))
	if err != nil {
		t.Fatal("present-and-empty is the signal; the file must exist")
	}
	want := `{"ts":"T","stage":"decompose","skills":[],` +
		`"match":{"method":"none","n_candidates":0,"top_score":0.0}}` + "\n"
	if string(raw) != want {
		t.Fatalf("\nwant %s\ngot  %s", want, raw)
	}

	if err := AppendSkillsManifest(dir, []SkillManifestEntry{{ID: "x"}},
		"replan", nil, "T2"); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(filepath.Join(dir, "source", "skills_manifest.jsonl"))
	if got := string(raw); len(got) <= len(want) {
		t.Fatal("JSONL: a second injection appends, it does not replace")
	}
	if !strings.Contains(string(raw), `"variant_of":null`) {
		t.Fatalf("a nil variant must render as null: %s", raw)
	}
}
