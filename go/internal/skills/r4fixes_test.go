package skills

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// readLog returns the captain's-log rows a test produced.
func readLog(t *testing.T, ws string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		t.Fatal(err)
	}
	var rows []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("unreadable log row: %v", err)
		}
		rows = append(rows, m)
	}
	return rows
}

func eventsOfType(rows []map[string]any, typ string) []map[string]any {
	var out []map[string]any
	for _, r := range rows {
		if r["event_type"] == typ {
			out = append(out, r)
		}
	}
	return out
}

// tripCircuit drives a skill to its third consecutive failure, which is the
// closed->open transition, and logs the transition.
func tripCircuit(t *testing.T, ws string, rec *record.Recorder, id, stepText string) UtilityUpdate {
	t.Helper()
	var u UtilityUpdate
	var err error
	for i := 0; i < CircuitOpenThreshold; i++ {
		u, err = UpdateSkillUtility(ws, id, false, "boom", stepText)
		if err != nil {
			t.Fatal(err)
		}
	}
	if err := LogCircuitTransition(rec, id, u, "boom"); err != nil {
		t.Fatal(err)
	}
	return u
}

// M2. When a circuit trips on input the skill's own trigger vocabulary
// says it was never meant to handle, Python says so — INPUT_MISMATCH,
// alongside the trip, with a note telling the inspector to read it as a
// domain mismatch rather than skill rot. Go opened the circuit and stopped
// there, which is r3's pattern 2: the decision ports and the announcement
// does not. A store differential cannot catch it, because the stores agree.
func TestACircuitTripOnOutOfDomainInputIsAnnouncedAsSuch(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	mustSeedSkill(t, ws, Skill{ID: "jina", Name: "jina-web-fetch",
		TriggerPatterns: []string{"fetch url", "web scrape"},
		Tier:            "established", CircuitState: "closed", UtilityScore: 0.5})

	// A web-fetch skill handed plain prose.
	tripCircuit(t, ws, rec, "jina", "summarize the meeting notes from yesterday")

	rows := readLog(t, ws)
	if len(eventsOfType(rows, "SKILL_CIRCUIT_OPEN")) != 1 {
		t.Fatalf("the trip itself must still be logged: %v", rows)
	}
	mismatch := eventsOfType(rows, "INPUT_MISMATCH")
	if len(mismatch) != 1 {
		t.Fatalf("want exactly one INPUT_MISMATCH, got %d", len(mismatch))
	}
	m := mismatch[0]
	if m["subject"] != "jina-web-fetch" {
		t.Errorf("subject: %v", m["subject"])
	}
	// SYSTEM audience: it is absent from Python's USER_SURFACED_EVENTS,
	// and it rides beside a trip that IS user-surfaced. That asymmetry is
	// deliberate — the trip is the decision, this is the qualifier.
	if m["audience"] != "system" {
		t.Errorf("audience: %v, want system", m["audience"])
	}
	if m["note"] != "Inspector: treat this as INPUT_MISMATCH, not skill degradation." {
		t.Errorf("note: %v", m["note"])
	}
	want := "Skill 'jina-web-fetch' expects url input but received 'plain_text'. " +
		"Circuit opened — failures may reflect domain mismatch."
	if m["summary"] != want {
		t.Errorf("summary:\n got %v\nwant %v", m["summary"], want)
	}
	ctx, _ := m["context"].(map[string]any)
	if ctx["input_type"] != "plain_text" || ctx["skill_url_domain"] != true {
		t.Errorf("context: %v", ctx)
	}
	rel, _ := m["related_ids"].([]any)
	if len(rel) != 1 || rel[0] != "skill:jina" {
		t.Errorf("related_ids: %v", m["related_ids"])
	}
}

// The mirror case: a prose skill handed a URL.
func TestAProseSkillTrippedOnAURLIsAlsoAMismatch(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	mustSeedSkill(t, ws, Skill{ID: "sum", Name: "summarizer",
		TriggerPatterns: []string{"summarize", "condense"},
		Tier:            "established", CircuitState: "closed", UtilityScore: 0.5})
	tripCircuit(t, ws, rec, "sum", "https://example.com/article")

	m := eventsOfType(readLog(t, ws), "INPUT_MISMATCH")
	if len(m) != 1 {
		t.Fatalf("want one INPUT_MISMATCH, got %d", len(m))
	}
	if !strings.Contains(m[0]["summary"].(string), "expects non-url input but received 'url'") {
		t.Errorf("summary: %v", m[0]["summary"])
	}
}

// The guards, each of which must SUPPRESS the event. Python's condition is
// (just opened) AND (had step text) AND (this outcome failed) AND (the
// vocabulary and the input disagree) — an ordinary degradation must stay
// unqualified, or the qualifier means nothing.
func TestInputMismatchIsSilentWhenItShouldBe(t *testing.T) {
	for _, tc := range []struct {
		name     string
		triggers []string
		stepText string
	}{
		{"vocabulary and input agree", []string{"fetch url"}, "https://example.com/x"},
		{"no step text to judge", []string{"fetch url"}, ""},
		{"neither is a url", []string{"summarize"}, "some prose to condense"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := t.TempDir()
			rec := record.New(ws)
			mustSeedSkill(t, ws, Skill{ID: "s", Name: "s", TriggerPatterns: tc.triggers,
				Tier: "established", CircuitState: "closed", UtilityScore: 0.5})
			tripCircuit(t, ws, rec, "s", tc.stepText)
			if got := eventsOfType(readLog(t, ws), "INPUT_MISMATCH"); len(got) != 0 {
				t.Fatalf("expected silence, got %v", got)
			}
			// Positive control: the trip itself still had to be announced,
			// so the silence above is the guard and not a dead call path.
			if got := eventsOfType(readLog(t, ws), "SKILL_CIRCUIT_OPEN"); len(got) != 1 {
				t.Fatalf("the trip must still be logged: %d", len(got))
			}
		})
	}
}

// A circuit RECOVERING is not a mismatch however mismatched the input:
// Python's guard is the transition INTO open, on a failure.
func TestOnlyTheTripIntoOpenCanBeAMismatch(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	mustSeedSkill(t, ws, Skill{ID: "s", Name: "s", TriggerPatterns: []string{"fetch url"},
		Tier: "established", CircuitState: "open", UtilityScore: 0.2})
	// open -> half_open, on a SUCCESS, with thoroughly mismatched text.
	u, err := UpdateSkillUtility(ws, "s", true, "", "ordinary prose input")
	if err != nil {
		t.Fatal(err)
	}
	if u.CircuitAfter != "half_open" {
		t.Fatalf("setup: circuit is %s", u.CircuitAfter)
	}
	if err := LogCircuitTransition(rec, "s", u, ""); err != nil {
		t.Fatal(err)
	}
	if got := eventsOfType(readLog(t, ws), "INPUT_MISMATCH"); len(got) != 0 {
		t.Fatalf("a recovery is not a mismatch: %v", got)
	}
}

// The two guards above that the outer Changed() check makes unreachable
// from LogCircuitTransition are still guards, and the r4 battery showed
// nothing pinned them: mutants deleting each one SURVIVED, because the
// caller can never present that state. They are called directly here, so
// the pin is on the function's own contract rather than on what one caller
// happens to pass it — the next caller is what they exist for.
func TestTheMismatchGuardsHoldWhenCalledDirectly(t *testing.T) {
	mismatched := UtilityUpdate{
		Found: true, SkillName: "jina", TriggerPatterns: []string{"fetch url"},
		StepText: "ordinary prose with no link in it",
	}
	for _, tc := range []struct {
		name string
		u    UtilityUpdate
		want int
	}{
		{"a re-trip of an already-open circuit is not a NEW mismatch",
			with(mismatched, "open", "open", false), 0},
		{"a SUCCESS never raises one",
			with(mismatched, "closed", "open", true), 0},
		{"the trip into open on a failure does",
			with(mismatched, "closed", "open", false), 1},
		{"and so does half_open -> open, which is also a trip",
			with(mismatched, "half_open", "open", false), 1},
		{"a move to half_open does not",
			with(mismatched, "open", "half_open", true), 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := t.TempDir()
			if err := logInputMismatch(record.New(ws), "jina", tc.u); err != nil {
				t.Fatal(err)
			}
			if got := len(eventsOfType(readLog(t, ws), "INPUT_MISMATCH")); got != tc.want {
				t.Fatalf("got %d INPUT_MISMATCH events, want %d", got, tc.want)
			}
		})
	}
}

func with(u UtilityUpdate, before, after string, success bool) UtilityUpdate {
	u.CircuitBefore, u.CircuitAfter, u.Success = before, after, success
	return u
}

// M3. A promotion must write the SKILL.md workspace overlay. Python does it
// on every promotion; Go did the tier write and the event and stopped, so a
// Go-promoted skill had no curated markdown and Python would never create
// it later — the promotion sweep only looks at skills still at
// `provisional`, and this one is now `established`. The overlays diverged
// permanently, one skill at a time, silently.
func TestAPromotionWritesTheSkillMarkdownOverlay(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	mustSeedSkill(t, ws, Skill{ID: "pm", Name: "Promote Me",
		Description:     "A provisional worth promoting since it performs",
		TriggerPatterns: []string{"promote", "elevate"},
		StepsTemplate:   []string{"first do this", "then do that"},
		Tier:            "provisional", CircuitState: "closed",
		UtilityScore: 0.9, UseCount: 7, SuccessRate: 0.855})

	rep, err := MaybeAutoPromoteSkills(ws, 10, rec)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 1 {
		t.Fatalf("setup: promoted %v", rep.PromotedIDs)
	}
	if len(rep.Warnings) != 0 {
		t.Fatalf("unexpected warnings: %v", rep.Warnings)
	}

	// The filename is the SLUG of the display name, which is what Python
	// writes — two runtimes disagreeing here would write the same skill to
	// two different files.
	dest := filepath.Join(ws, "skills", "promote_me.md")
	raw, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("no overlay written: %v", err)
	}
	got := string(raw)
	// 0.855 renders "86%": scaled to 85.5, then half-to-even.
	for _, want := range []string{
		"name: promote_me\n",
		`description: "A provisional worth promoting since it performs"`,
		"triggers: ['promote', 'elevate']",
		"# Promote Me\n",
		"> Auto-extracted from runtime skill (tier: established, use_count: 7, success_rate: 86%)",
		"## Steps\n\n1. first do this\n2. then do that\n",
		"- Success rate: 86%",
		"- Tier: established",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("overlay missing %q\n---\n%s", want, got)
		}
	}
	if !strings.HasSuffix(got, "\n") || strings.HasSuffix(got, "\n\n\n") {
		t.Errorf("trailing newlines: %q", got[len(got)-4:])
	}
}

// Python writes `triggers[:8]` into the frontmatter. Nothing pinned the cap
// — the r4 battery removed it and every test still passed, because every
// fixture had two triggers.
func TestTheOverlayCapsTheTriggerListAtEight(t *testing.T) {
	ws := t.TempDir()
	var many []string
	for i := 0; i < 12; i++ {
		many = append(many, fmt.Sprintf("trigger%d", i))
	}
	if _, err := ExportSkillAsMarkdown(ws, Skill{ID: "t", Name: "Capped",
		Tier: "established", TriggerPatterns: many, SuccessRate: 1.0}, false); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "skills", "capped.md"))
	if err != nil {
		t.Fatal(err)
	}
	want := "triggers: ['trigger0', 'trigger1', 'trigger2', 'trigger3', " +
		"'trigger4', 'trigger5', 'trigger6', 'trigger7']"
	if !strings.Contains(string(raw), want) {
		t.Fatalf("trigger line not capped at eight:\n%s", raw)
	}
	if strings.Contains(string(raw), "trigger8") {
		t.Fatal("the ninth trigger leaked into the frontmatter")
	}
}

// A human may have curated the overlay since. A later promotion of the same
// name must not overwrite it.
func TestTheOverlayExportDoesNotClobberAnExistingFile(t *testing.T) {
	ws := t.TempDir()
	if err := os.MkdirAll(filepath.Join(ws, "skills"), 0o755); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(ws, "skills", "kept.md")
	if err := os.WriteFile(dest, []byte("hand written\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	written, err := ExportSkillAsMarkdown(ws, Skill{ID: "k", Name: "Kept", Tier: "established"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if written != "" {
		t.Fatalf("should have skipped, wrote %s", written)
	}
	raw, _ := os.ReadFile(dest)
	if string(raw) != "hand written\n" {
		t.Fatalf("clobbered: %q", raw)
	}
}

// The slug is a filename, so it has to agree across runtimes exactly.
// Python's \w and \s are UNICODE; Go's are ASCII, which would have turned
// "café" into "caf" and "日本語 skill" into "_skill". Every expectation
// measured against skill_loader._slugify.
func TestSlugifyMatchesPython(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"Fetch URL", "fetch_url"},
		{"café-résumé!", "café-résumé"},
		{"a  b\tc", "a_b_c"},
		{"___", "unnamed_skill"},
		{"--x--", "x"},
		{"日本語 skill", "日本語_skill"},
		{"a.b/c", "abc"},
		{"Ünïcødé_Skîll", "ünïcødé_skîll"},
		{"", "unnamed_skill"},
		{"  ", "unnamed_skill"},
		{"emoji 🎉 here", "emoji_here"}, // So is in neither runtime's \w
		{"tab nbsp", "tab_nbsp"},       // NBSP is \s in Python, not in Go
	} {
		if got := Slugify(c.in); got != c.want {
			t.Errorf("Slugify(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// Python's format(f, ".0%") scales by 100 and rounds half-to-EVEN.
// Half-to-even is the whole content of this pin: half-AWAY-from-zero, the
// rounding most people would reach for, gets three of these eight wrong.
func TestPercentFormatMatchesPython(t *testing.T) {
	for _, c := range []struct {
		in   float64
		want string
	}{
		{1.0, "100%"},
		{0.855, "86%"}, // 85.5 -> 86, the even neighbour
		{0.845, "84%"}, // 84.5 -> 84, likewise; half-UP would say 85
		{0.995, "100%"},
		{0.125, "12%"}, // 12.5 -> 12; half-UP would say 13
		{0.0, "0%"},
		{0.005, "0%"},
		{0.3333, "33%"},
	} {
		if got := pyPercent0(c.in); got != c.want {
			t.Errorf("pyPercent0(%v) = %s, want %s", c.in, got, c.want)
		}
	}
}

// L1. variant_of was the one string field missing from the writer's
// clean-text enumeration, and pyjson.Value has no *string case — so it fell
// through to encoding/json, which launders invalid UTF-8 to U+FFFD and
// WRITES the row where Python refuses it.
func TestATaintedVariantOfIsRefusedLikeEveryOtherStringField(t *testing.T) {
	tainted := "parent\xed\xa0\x80" // a lone surrogate in WTF-8
	s := base("s1", "s")
	s.VariantOf = &tainted
	s.ContentHash = ComputeSkillHash(s)
	if _, err := proveLine(s); err == nil {
		t.Fatal("a byte-tainted variant_of must be refused, not laundered")
	}
	// Positive control: the same skill without the taint writes fine, so
	// the refusal above is the field and not some unrelated precondition.
	clean := "parent"
	s.VariantOf = &clean
	s.ContentHash = ComputeSkillHash(s)
	if _, err := proveLine(s); err != nil {
		t.Fatalf("clean variant_of must still write: %v", err)
	}
}

// L3. Python checks the three telemetry fields in a fixed tuple, so a call
// with several non-finite values always names cost_usd. Ranging a Go map
// named a different one on different runs of identical input, and this
// message is returned on the caller's durable warning rail.
func TestNonFiniteTelemetryAlwaysNamesTheSameFieldFirst(t *testing.T) {
	ws := t.TempDir()
	nan := math.NaN()
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		_, err := RecordSkillOutcome(ws, "s", true,
			OutcomeTelemetry{CostUSD: nan, LatencyMS: nan, Confidence: nan})
		if err == nil {
			t.Fatal("non-finite telemetry must be refused")
		}
		seen[err.Error()] = true
	}
	if len(seen) != 1 {
		t.Fatalf("nondeterministic refusal across identical calls: %v", seen)
	}
	// And it spells the value Python's way: repr(nan) is "nan", not "NaN".
	for msg := range seen {
		if msg != "cost_usd must be a finite number, got nan" {
			t.Fatalf("message: %q", msg)
		}
	}
}

// L4. Python writes "\n".join(live) + "\n", so a rewrite that drops the
// last skill leaves one newline, not an empty file.
func TestARewriteThatEmptiesThePoolWritesPythonsBytes(t *testing.T) {
	ws := t.TempDir()
	mustSeedSkill(t, ws, Skill{ID: "only", Name: "only", Tier: "provisional",
		CircuitState: "closed"})
	if _, err := SaveSkills(ws, nil, NewIDSet("only"), NewIDSet()); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(skillsPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "\n" {
		t.Fatalf("empty store bytes = %q, want %q", raw, "\n")
	}
	if got := LoadSkills(ws).Skills; len(got) != 0 {
		t.Fatalf("and it must still read as empty: %v", got)
	}
}

// mustSeedSkill writes one skill into a fresh store, filling in the
// fields the store's admission predicate requires but this test does not
// care about, and stamping the hash last so the row is self-consistent.
func mustSeedSkill(t *testing.T, ws string, s Skill) {
	t.Helper()
	if s.CreatedAt == "" {
		s.CreatedAt = "2026-08-20T10:00:00+00:00"
	}
	s.ContentHash = ComputeSkillHash(s)
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
}
