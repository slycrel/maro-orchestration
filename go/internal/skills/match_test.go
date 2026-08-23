package skills

import (
	"strings"
	"testing"
)

func skillRow(t *testing.T, ws string, s Skill) {
	t.Helper()
	if s.CreatedAt == "" {
		s.CreatedAt = "2026-08-20T10:00:00+00:00"
	}
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
}

func base(id, name string) Skill {
	s := newSkill()
	s.ID, s.Name, s.Description = id, name, "does "+name
	s.CreatedAt = "2026-08-20T10:00:00+00:00"
	return s
}

// --- tokenization parity ---

func TestStemMatchesPythonRuleOrder(t *testing.T) {
	cases := map[string]string{
		"researching":   "research", // "ing" with a 8-char root
		"analyses":      "analys",   // "es"
		"builder":       "build",    // "er"
		"cat":           "cat",      // root too short to strip "s"? no suffix match
		"ads":           "ads",      // "s" would leave a 2-char root → untouched
		"organizations": "organiz",  // "ations" wins over "s" (longest first)
		"quickly":       "quick",    // "ly"
	}
	for in, want := range cases {
		if got := stem(in); got != want {
			t.Errorf("stem(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestSkillTokensDropsShortAndStopWords(t *testing.T) {
	got := skillTokens("We should Research the WEB for data!")
	joined := strings.Join(got, ",")
	// "we"/"should"/"the"/"for" are stop words; "research" stems to itself.
	if joined != "research,web,data" {
		t.Fatalf("tokens: %q", joined)
	}
}

// --- match tiers ---

func TestFindMatchingSkillsKeywordTierBeatsTFIDF(t *testing.T) {
	ws := t.TempDir()
	kw := base("kw", "Polymarket edges")
	kw.TriggerPatterns = []string{"polymarket"}
	skillRow(t, ws, kw)
	other := base("other", "Web research")
	other.TriggerPatterns = []string{"unrelated"}
	other.Description = "polymarket polymarket polymarket"
	skillRow(t, ws, other)

	got, tel := FindMatchingSkills(ws, "find polymarket edges", MatchOptions{})
	if len(got) == 0 || got[0].ID != "kw" {
		t.Fatalf("trigger match must win: %+v", got)
	}
	if tel.Method != "keyword" || tel.NCandidates != 2 {
		t.Fatalf("telemetry: %+v", tel)
	}
	if got[0].MatchMethod != "keyword" || got[0].MatchScore != 1 {
		t.Fatalf("match stamp: method=%q score=%v", got[0].MatchMethod, got[0].MatchScore)
	}
	if tel.Scores["kw"] != 1 {
		t.Fatalf("telemetry scores: %+v", tel.Scores)
	}
}

func TestFindMatchingSkillsFallsBackToTFIDF(t *testing.T) {
	ws := t.TempDir()
	s := base("t1", "Source summarizer")
	s.Description = "fetch articles and summarize sources"
	s.TriggerPatterns = []string{"nothing that matches"}
	skillRow(t, ws, s)
	// A second, unrelated document: with a single-doc corpus the smoothed
	// IDF log((N+1)/(1+df)) is log(1)=0 for every term, so nothing scores
	// above zero. Verified against Python on the identical fixture — the
	// degenerate case is shared, and TestTFIDFDegenerateCorpusScoresZero
	// pins it deliberately.
	filler := base("filler", "Git rebase")
	filler.Description = "rebase branches and resolve conflicts"
	filler.TriggerPatterns = []string{"rebase"}
	skillRow(t, ws, filler)

	got, tel := FindMatchingSkills(ws, "summarize some articles", MatchOptions{})
	if len(got) != 1 || got[0].ID != "t1" {
		t.Fatalf("tfidf tier must fire: %+v", got)
	}
	if tel.Method != "tfidf_fallback" || got[0].MatchMethod != "tfidf_fallback" {
		t.Fatalf("tier stamp: %+v / %q", tel, got[0].MatchMethod)
	}
	if got[0].MatchScore <= 0 || tel.TopScore != got[0].MatchScore {
		t.Fatalf("cosine must be stamped and reported: %v vs %v",
			got[0].MatchScore, tel.TopScore)
	}
}

// A graded gap signal: "nothing matched" is method "none", not an empty
// telemetry dict — the whole point of the caller-supplied telemetry.
func TestFindMatchingSkillsReportsNoneWhenNothingMatches(t *testing.T) {
	ws := t.TempDir()
	s := base("x", "Git rebase helper")
	s.Description = "rebase branches"
	s.TriggerPatterns = []string{"rebase"}
	skillRow(t, ws, s)

	got, tel := FindMatchingSkills(ws, "zzz", MatchOptions{})
	if len(got) != 0 {
		t.Fatalf("no match expected: %+v", got)
	}
	if tel.Method != "none" || tel.TopScore != 0 {
		t.Fatalf("telemetry: %+v", tel)
	}
}

func TestFindMatchingSkillsExcludesOpenCircuit(t *testing.T) {
	ws := t.TempDir()
	s := base("open", "Broken thing")
	s.TriggerPatterns = []string{"broken"}
	s.CircuitState = "open"
	skillRow(t, ws, s)
	got, tel := FindMatchingSkills(ws, "the broken thing", MatchOptions{})
	if len(got) != 0 || tel.Method != "none" {
		t.Fatalf("open circuit must not be injectable: %+v %+v", got, tel)
	}
}

// A challenger is reachable ONLY via its parent's routing: it must not be
// an independent candidate (else the arms are non-exclusive and both get
// credited on every outcome), but it stays eligible when the caller names
// it in the run's manifest.
func TestFindMatchingSkillsVariantOnlyViaManifest(t *testing.T) {
	ws := t.TempDir()
	parent := base("p", "Parent")
	parent.TriggerPatterns = []string{"widget"}
	skillRow(t, ws, parent)
	child := base("c", "Challenger")
	child.TriggerPatterns = []string{"widget"}
	pid := "p"
	child.VariantOf = &pid
	skillRow(t, ws, child)

	got, _ := FindMatchingSkills(ws, "make a widget", MatchOptions{})
	if len(got) != 1 || got[0].ID != "p" {
		t.Fatalf("challenger must not be an independent candidate: %+v", got)
	}
	got, _ = FindMatchingSkills(ws, "make a widget",
		MatchOptions{RestrictToIDs: true, OnlyIDs: []string{"c"}})
	if len(got) != 1 || got[0].ID != "c" {
		t.Fatalf("challenger must stay eligible via the manifest: %+v", got)
	}
	// Restrict-to-nothing must mean nothing. A Go caller that builds ids by
	// appending over an EMPTY manifest holds a nil slice, and reading the
	// mode off that nil would have scored the entire library instead —
	// attributing a run's outcome to skills it never injected.
	var fromEmptyManifest []string
	got, tel := FindMatchingSkills(ws, "make a widget",
		MatchOptions{RestrictToIDs: true, OnlyIDs: fromEmptyManifest})
	if len(got) != 0 || tel.Method != "none" {
		t.Fatalf("an empty manifest must restrict to nothing: %+v %+v", got, tel)
	}
}

func TestFindMatchingSkillsProjectIsolation(t *testing.T) {
	ws := t.TempDir()
	global := base("g", "Global")
	global.TriggerPatterns = []string{"widget"}
	skillRow(t, ws, global)
	mine := base("m", "Mine")
	mine.TriggerPatterns = []string{"widget"}
	mine.Project = "alpha"
	skillRow(t, ws, mine)
	theirs := base("o", "Theirs")
	theirs.TriggerPatterns = []string{"widget"}
	theirs.Project = "beta"
	skillRow(t, ws, theirs)

	got, _ := FindMatchingSkills(ws, "a widget", MatchOptions{Project: "alpha"})
	ids := map[string]bool{}
	for _, s := range got {
		ids[s.ID] = true
	}
	if !ids["g"] || !ids["m"] || ids["o"] {
		t.Fatalf("project isolation wrong: %+v", ids)
	}
}

// The island boost is what makes a same-cosine skill win, so it must be
// applied from the GOAL's detected intent, not the skill's alone. The
// expected scores are Python's ACTUAL output on this exact fixture
// (_tfidf_skill_rank, run 2026-08-23): b=1.2, a=1.0 — a differential pin,
// not a Go-derived expectation.
func TestTFIDFIslandBoostBreaksTies(t *testing.T) {
	a := base("a", "summarize articles")
	a.Description = "summarize articles"
	b := base("b", "summarize articles")
	b.Description = "summarize articles"
	b.Island = "research"
	c := base("c", "git rebase")
	c.Description = "rebase branches and resolve conflicts"
	got := tfidfSkillRank("research and summarize articles", []Skill{a, b, c}, 2)
	if len(got) != 2 || got[0].ID != "b" || got[1].ID != "a" {
		t.Fatalf("island-matching skill must rank first: %+v", got)
	}
	if got[0].MatchScore != 1.2 || got[1].MatchScore != 1.0 {
		t.Fatalf("scores must match Python: got %v/%v want 1.2/1.0",
			got[0].MatchScore, got[1].MatchScore)
	}
}

// The smoothed IDF zeroes every term when a term appears in EVERY document
// (df==N → log((N+1)/(N+1)) == 0), so a single-skill pool — and any pool of
// identical documents — ranks nothing. Surprising, deliberately pinned, and
// verified identical in Python: both runtimes return an empty list here, so
// the TF-IDF tier is silent on a one-skill workspace rather than injecting
// a spurious match.
func TestTFIDFDegenerateCorpusScoresZero(t *testing.T) {
	one := base("solo", "Source summarizer")
	one.Description = "fetch articles and summarize sources"
	if got := tfidfSkillRank("summarize some articles", []Skill{one}, 2); len(got) != 0 {
		t.Fatalf("single-doc corpus must score zero (Python parity): %+v", got)
	}
	a := base("a", "summarize articles")
	a.Description = "summarize articles"
	b := base("b", "summarize articles")
	b.Description = "summarize articles"
	if got := tfidfSkillRank("summarize articles", []Skill{a, b}, 2); len(got) != 0 {
		t.Fatalf("identical-doc corpus must score zero (Python parity): %+v", got)
	}
}

func TestGoalIslandTieResolvesByFixedOrder(t *testing.T) {
	// One keyword from research ("find") and one from build ("make"):
	// Python's max() over an insertion-ordered dict returns research.
	if got := goalIsland("find and make it"); got != "research" {
		t.Fatalf("tie must resolve to the first island, got %q", got)
	}
	if got := goalIsland("nothing here"); got != "" {
		t.Fatalf("no keyword must mean no island, got %q", got)
	}
}

func TestFormatSkillsForPromptShape(t *testing.T) {
	if got := FormatSkillsForPrompt(nil); got != "" {
		t.Fatalf("no skills must format to empty, got %q", got)
	}
	s := base("a", "Web Research")
	s.Description = "fetch and summarize"
	s.StepsTemplate = []string{"search", "read"}
	s.OptimizationObjective = "fewest tokens"
	want := "Reusable skills from past successful goals:\n" +
		"\nSkill: Web Research — fetch and summarize\n" +
		"Optimize for: fewest tokens\n" +
		"Steps:\n  - search\n  - read"
	if got := FormatSkillsForPrompt([]Skill{s}); got != want {
		t.Fatalf("prompt block:\n got %q\nwant %q", got, want)
	}
}
