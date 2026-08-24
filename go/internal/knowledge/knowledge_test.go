package knowledge

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCoerceScope(t *testing.T) {
	for _, c := range []struct {
		in   any
		want string
		bad  bool
	}{
		{"method", "method", false},
		{"world", "world", false},
		{"", "", false},
		{nil, "", false},
		{"Method", "", true},
		{[]any{"world"}, "", true},
		{float64(3), "", true},
	} {
		got, bad := CoerceScope(c.in)
		if got != c.want || bad != c.bad {
			t.Errorf("CoerceScope(%v) = (%q,%v) want (%q,%v)", c.in, got, bad, c.want, c.bad)
		}
	}
}

func TestAbsorbVariantBounds(t *testing.T) {
	v := AbsorbVariant(nil, "  a variant  ", "the canonical")
	if len(v) != 1 || v[0] != "a variant" {
		t.Fatalf("trim failed: %v", v)
	}
	if got := AbsorbVariant(v, "a variant", "x"); len(got) != 1 {
		t.Fatal("duplicate absorbed")
	}
	if got := AbsorbVariant(v, "the canonical", "the canonical"); len(got) != 1 {
		t.Fatal("canonical absorbed as its own variant")
	}
	long := strings.Repeat("y", 800)
	v = AbsorbVariant(v, long, "x")
	if len(v[1]) > VariantMaxChars+64 {
		t.Fatalf("variant not clipped: %d chars", len(v[1]))
	}
	for i := 0; i < 10; i++ {
		v = AbsorbVariant(v, strings.Repeat("z", i+1), "x")
	}
	if len(v) > MergedVariantsCap {
		t.Fatalf("cap breached: %d", len(v))
	}
}

func TestUnionVariantsRewritesOnlyTheTwin(t *testing.T) {
	ws := t.TempDir()
	s := NewStore(ws)
	dir := filepath.Join(ws, "memory", "medium")
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, "lessons.jsonl"),
		[]byte(`{"lesson_id":"a","lesson":"twin text","merged_variants":[]}`+"\n"+
			`{"lesson_id":"b","lesson":"other text","merged_variants":[]}`+"\n"+
			"not json at all\n"), 0o644)
	if err := s.UnionVariantsIntoLesson("twin text", []string{"new variant"}); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(dir, "lessons.jsonl"))
	content := string(raw)
	// json.dumps' separators, not encoding/json's. A rewritten lesson row
	// is read by the Python runtime, so `", "` and `": "` are the
	// contract (adversarial mission-r8 HIGH).
	if !strings.Contains(content, `"merged_variants": ["new variant"]`) {
		t.Fatalf("variant not absorbed, or not in json.dumps' spelling:\n%s", content)
	}
	// And the row's KEY ORDER survived the rewrite. A map decode had
	// already lost it before the renderer saw it, so the rewritten row
	// came back alphabetised where Python emits dataclass field order.
	if !strings.HasPrefix(strings.TrimSpace(content), `{"lesson_id": "a", "lesson": "twin text"`) {
		t.Fatalf("rewrite reordered the row's keys:\n%s", content)
	}
	if !strings.Contains(content, "not json at all") {
		t.Fatal("undecodable row destroyed by the rewrite — decay trust, never data")
	}
	if strings.Count(content, "new variant") != 1 {
		t.Fatalf("variant leaked into non-twin rows:\n%s", content)
	}
}

// The variant-union rewrite re-marshals WHOLE store rows — a plain
// float64 decode would silently round large integer fields on rows the
// union merely passes through (r4 2026-08-22, QA).
func TestUnionVariantsPreservesLargeIntegers(t *testing.T) {
	ws := t.TempDir()
	s := NewStore(ws)
	path := s.TieredLessonsPath("medium")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	row := `{"lesson_id":"x","lesson":"the twin","times_applied":9007199254740993,"merged_variants":[]}`
	if err := os.WriteFile(path, []byte(row+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := s.UnionVariantsIntoLesson("the twin", []string{"a rationale"}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "9007199254740993") {
		t.Fatalf("large integer rounded on rewrite: %s", raw)
	}
	if !strings.Contains(string(raw), "a rationale") {
		t.Fatalf("variant not unioned: %s", raw)
	}
}
