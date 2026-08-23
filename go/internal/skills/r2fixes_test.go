package skills

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// jsonNum builds a stored numeric LITERAL, the way a decoded row carries
// one — a raw float64 could not represent an overflowing literal at all.
func jsonNum(lit string) json.Number { return json.Number(lit) }

// H1. Go randomizes map iteration per range, and float addition is not
// associative, so summing a TF-IDF vector by ranging its map made dot/na/nb
// differ between two calls on the SAME inputs. The ranking sort is stable,
// so an ulp difference flipped which of two tied skills was injected —
// measured at 2302/698 over 3000 identical calls. Every injected-outcome
// counter built on that was recording a coin flip.
func TestTFIDFRankingIsDeterministicAcrossCalls(t *testing.T) {
	// The corpus shape matters. Two documents must be an EXACT tie, and the
	// weights within a vector must be heterogeneous — a vector whose terms
	// all carry the same weight sums identically in any order and would hide
	// the bug entirely. So token repeat counts vary (tf varies) and twelve
	// overlapping "odd" documents make idf vary per term. Measured on the
	// pre-fix code this corpus flipped the top two on 128 of 400 calls; a
	// simpler one flipped on 3 of 400 and would have been a test that passes
	// by luck.
	//
	// No trigger patterns and no tags, so the keyword tier scores zero and
	// the ladder falls through to TF-IDF — the tier this test is about.
	var words []string
	for i := 0; i < 60; i++ {
		for r := 0; r <= i%7; r++ {
			words = append(words, fmt.Sprintf("token%03d", i))
		}
	}
	doc := strings.Join(words, " ")
	var pool []Skill
	for n := 0; n < 2; n++ {
		s := base(fmt.Sprintf("tie%02d", n), fmt.Sprintf("Doc %d", n))
		s.Description = doc
		pool = append(pool, s)
	}
	for n := 0; n < 12; n++ {
		s := base(fmt.Sprintf("odd%02d", n), "Odd")
		s.Description = strings.Join(words[n*7:n*7+40], " ") +
			fmt.Sprintf(" uniq%03d", n)
		pool = append(pool, s)
	}

	ids := func() string {
		got, tel := FindMatchingSkillsIn(pool, doc, MatchOptions{})
		if tel.Method != "tfidf_fallback" {
			t.Fatalf("this test must exercise the TF-IDF tier, got %q", tel.Method)
		}
		var out []string
		for _, s := range got {
			out = append(out, s.ID)
		}
		return strings.Join(out, ",")
	}
	// A tie resolves to POOL order, the way Python's stable sort does.
	first := ids()
	if first != "tie00,tie01" {
		t.Fatalf("a tie must keep pool order, got %q", first)
	}
	for i := 0; i < 400; i++ {
		if got := ids(); got != first {
			t.Fatalf("run %d ranked %q, the first run ranked %q — the ranking "+
				"is deciding by map order", i, got, first)
		}
	}
}

// M1. The stored needs_escalation flag and the live success_rate drift apart
// by design (the injection recorder deliberately does not recompute the
// flag, and a legacy row predating the field defaults to false). Reading the
// flag returned a set DISJOINT from Python's on the same store.
func TestEscalationRecomputesFromTheRateNotTheStoredFlag(t *testing.T) {
	ws := t.TempDir()
	// A legacy row: low rate, flag absent → false. Python flags it.
	legacy := `{"skill_id":"legacy","skill_name":"Legacy","total_uses":10,` +
		`"successes":1,"failures":9,"success_rate":0.1}`
	// A stale row: healthy rate, flag stuck true. Python does not flag it.
	stale := `{"skill_id":"stale","skill_name":"Stale","total_uses":10,` +
		`"successes":9,"failures":1,"success_rate":0.9,"needs_escalation":true}`
	path := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(legacy+"\n"+stale+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	got := SkillsNeedingEscalation(ws)
	if len(got) != 1 || got[0].SkillID != "legacy" {
		var ids []string
		for _, s := range got {
			ids = append(ids, s.SkillID)
		}
		t.Fatalf("the rate decides, not the flag: %v", ids)
	}
}

// M3. A row that fails the proof is stranded — and strandees ride FIRST in
// the rewrite. So minting a fresh zeroed record for that same id put the
// reset row LAST, where it won the last-row-wins keyed read in BOTH
// runtimes: a routine counter bump silently destroyed the skill's evidence.
func TestCounterBumpRefusesToMintOverAnUnprovableRow(t *testing.T) {
	ws := t.TempDir()
	// total_uses is a huge integer literal: Python's ints are unbounded so
	// it admits the row, Go's int64 predicate strands it. Whatever the
	// cause, the evidence is present and unreadable.
	drift := `{"skill_id":"k","skill_name":"K","total_uses":9223372036854775808,` +
		`"successes":4,"failures":1,"injected_runs":4,"injected_successes":3}`
	path := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(drift+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := RecordSkillOutcome(ws, "k", true, OutcomeTelemetry{Confidence: 1})
	if err == nil {
		t.Fatal("minting over an unprovable row must be refused")
	}
	if !strings.Contains(err.Error(), "refusing to mint a fresh record over it") {
		t.Fatalf("the refusal must say why: %v", err)
	}
	// A batch is one transaction: the refusal aborts it entirely.
	if _, err := RecordSkillInjectionOutcomes(ws, []string{"safe", "k"}, true); err == nil {
		t.Fatal("the batch must abort rather than record part of a verdict set")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != drift+"\n" {
		t.Fatalf("the store must be untouched by a refused write:\n%s", raw)
	}
}

// M4. Python's str.strip() counts U+001C–U+001F as whitespace; Go's
// unicode.IsSpace does not — the only difference between the two sets over
// the whole rune range. It drives a REFUSAL, so with TrimSpace Go ADMITTED
// rows Python strands: the unsafe direction, since an admitted row becomes
// eligible to be matched, replaced and archived-and-removed while the other
// runtime carries it verbatim forever.
func TestValidatorStripsPythonsWhitespaceSet(t *testing.T) {
	for _, blank := range []string{"\x1c", "\x1d", "\x1e", "\x1f", " \x1c ", "\t"} {
		row := map[string]any{"id": blank, "name": "N", "content_hash": "h",
			"created_at": "2026-08-20T10:00:00+00:00"}
		if _, err := ValidateSkillRow(row); err == nil {
			t.Errorf("id %q must be refused as empty-after-strip", blank)
		}
	}
	// And a tag of pure separators is dropped by the normalizer, not stored.
	got := NormalizeTags([]any{"\x1c", " Real ", "\x1f\x1e"}, -1)
	if len(got) != 1 || got[0] != "real" {
		t.Fatalf("tags: %+v", got)
	}
}

// L6. Go's strings.ToLower is SIMPLE case mapping; Python's str.lower() is
// full, and U+0130 lowercases to two runes. It is the only unconditional
// multi-rune lowercase mapping in Unicode, and it changes both a stored tag
// and the matching corpus.
func TestLowercasingMatchesPythonForTheDottedCapitalI(t *testing.T) {
	if got := pyLower("İstanbul"); got != "i̇stanbul" {
		t.Fatalf("pyLower: %q (% x)", got, []rune(got))
	}
	if got := NormalizeTags([]any{"İSTANBUL"}, -1); len(got) != 1 ||
		got[0] != "i̇stanbul" {
		t.Fatalf("a stored tag must be spelled Python's way: %q", got)
	}
	if got := pyLower("PLAIN"); got != "plain" {
		t.Fatalf("the common path must be untouched: %q", got)
	}
}

// L1. An empty id in a manifest means the caller's manifest is wrong.
// Recording the rest of the batch hides that AND writes a verdict set that
// does not match the run; Python raises and records nothing.
func TestInjectionBatchRefusesAnEmptyIDOutright(t *testing.T) {
	ws := t.TempDir()
	_, err := RecordSkillInjectionOutcomes(ws, []string{"a", "", "b"}, true)
	if err == nil {
		t.Fatal("an empty id must refuse the whole batch")
	}
	if _, statErr := os.Stat(skillStatsPath(ws)); statErr == nil {
		t.Fatal("nothing must have been recorded")
	}
}

// L7. The collapse is right; the silence was not. A quietly smaller
// denominator is exactly what makes A/B evidence untrustworthy.
func TestInjectionBatchAnnouncesCollapsedDuplicates(t *testing.T) {
	ws := t.TempDir()
	warns, err := RecordSkillInjectionOutcomes(ws, []string{"a", "a", "b", "a"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(warns, "\n"),
		"2 duplicate id(s) collapsed — one verdict per skill per batch") {
		t.Fatalf("the collapse must be announced: %+v", warns)
	}
	st, ok := GetSkillStats(ws, "a")
	if !ok || st.InjectedRuns != 1 {
		t.Fatalf("one verdict per skill per batch: %+v", st)
	}
}

// L4. The top-level non-finite refusal never saw the nested `imported`
// blob, so a nested 1e400 was written while Python's
// json.dumps(allow_nan=False) refused the whole save.
func TestWriterRefusesANestedNonFiniteNumber(t *testing.T) {
	ws := t.TempDir()
	s := base("u", "Nested")
	s.Imported = map[string]any{"n": jsonNum("1e400"), "z": jsonNum("1")}
	if err := SaveSkill(ws, &s); err == nil {
		t.Fatal("a nested overflow must be refused, as Python refuses it")
	}
	if _, err := os.Stat(skillsPath(ws)); err == nil {
		t.Fatal("a refused save must not create the store")
	}
	// A large INTEGER literal is not an overflow: Python's ints are
	// unbounded and json.dumps re-emits it exactly.
	s.Imported = map[string]any{"n": jsonNum(strings.Repeat("9", 40))}
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatalf("a big integer literal is exact in Python: %v", err)
	}
}
