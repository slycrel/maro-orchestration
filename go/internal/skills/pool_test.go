package skills

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func poolLines(t *testing.T, ws string) []string {
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

func seed(t *testing.T, ws string, ids ...string) []Skill {
	t.Helper()
	var out []Skill
	for _, id := range ids {
		s := base(id, "Name "+id)
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
		out = append(out, s)
	}
	return out
}

// --- the rewrite contract ---

// Absence means CARRY. Every caller builds its list from an UNLOCKED load,
// so reading "absent from the list" as "deliberately deleted" destroyed any
// skill a concurrent process saved in between — with no archive copy.
func TestSaveSkillsCarriesRowsAbsentFromTheCallersList(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a", "b")
	// A concurrent writer adds "c" after our snapshot.
	c := base("c", "Concurrent")
	if err := SaveSkill(ws, &c); err != nil {
		t.Fatal(err)
	}
	if _, err := SaveSkills(ws, pool, NewIDSet(), NewIDSet()); err != nil {
		t.Fatal(err)
	}
	ids := map[string]bool{}
	for _, s := range LoadSkills(ws).Skills {
		ids[s.ID] = true
	}
	if !ids["c"] {
		t.Fatal("a concurrently saved skill was destroyed by an unrelated rewrite")
	}
	if len(ids) != 3 {
		t.Fatalf("pool: %v", ids)
	}
}

// A row the caller HOLDS but did not NAME must not be written: the live row
// is at least as fresh as the caller's stale copy. Without this, a
// concurrent save was reverted by any unrelated caller that loaded before
// it and saved after it.
func TestSaveSkillsDoesNotWriteUnnamedRowsAndAnnouncesDivergence(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a", "b")
	// A concurrent writer revises "a"...
	live := LoadSkills(ws).Skills
	for i := range live {
		if live[i].ID == "a" {
			live[i].Description = "revised by the concurrent writer"
			if err := SaveSkill(ws, &live[i]); err != nil {
				t.Fatal(err)
			}
		}
	}
	// ...and our stale caller edits its own copy without naming it.
	for i := range pool {
		if pool[i].ID == "a" {
			pool[i].Description = "stale edit"
		}
	}
	warns, err := SaveSkills(ws, pool, NewIDSet(), NewIDSet())
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range LoadSkills(ws).Skills {
		if s.ID == "a" && s.Description != "revised by the concurrent writer" {
			t.Fatalf("the live row was reverted by a stale unnamed copy: %q", s.Description)
		}
	}
	joined := strings.Join(warns, "\n")
	if !strings.Contains(joined, "unnamed row(s) in the caller's list differ") {
		t.Fatalf("divergence must be announced: %+v", warns)
	}
	// The announcement must NOT assert which cause it was: staleness is the
	// common one under load, and a lying warning trains operators to ignore
	// the honest ones.
	if !strings.Contains(joined, "either an unnamed edit was discarded, or a concurrent write") {
		t.Fatalf("the announcement must name both causes: %q", joined)
	}
}

func TestSaveSkillsWritesOnlyNamedRows(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a", "b")
	for i := range pool {
		pool[i].Description = "edited " + pool[i].ID
	}
	if _, err := SaveSkills(ws, pool, NewIDSet(), NewIDSet("a")); err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, s := range LoadSkills(ws).Skills {
		got[s.ID] = s.Description
	}
	if got["a"] != "edited a" {
		t.Fatalf("a named write must land: %q", got["a"])
	}
	if got["b"] == "edited b" {
		t.Fatalf("an unnamed edit must not land: %q", got["b"])
	}
}

// Naming is not creation. A named id with no live row is a lost race with a
// deliberate drop (cull, retirement, rollback); appending it resurrected
// the retired row, with none of the retirement's reasoning.
func TestSaveSkillsDoesNotResurrectANamedButAbsentRow(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a", "b")
	// "b" is retired by a concurrent cull.
	var survivors []Skill
	for _, s := range LoadSkills(ws).Skills {
		if s.ID != "b" {
			survivors = append(survivors, s)
		}
	}
	if _, err := SaveSkills(ws, survivors, NewIDSet("b"), NewIDSet()); err != nil {
		t.Fatal(err)
	}
	warns, err := SaveSkills(ws, pool, NewIDSet(), NewIDSet("b"))
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range LoadSkills(ws).Skills {
		if s.ID == "b" {
			t.Fatal("a retired skill was resurrected by a named write")
		}
	}
	if !strings.Contains(strings.Join(warns, "\n"), "no parseable live row holds these id(s)") {
		t.Fatalf("the refusal must be announced: %+v", warns)
	}
}

// The caller's list came from a load, which cannot represent a row it could
// not parse — so a naive rewrite from that list DELETES every torn line.
func TestSaveSkillsCarriesUnparseableAndUnprovableRowsVerbatim(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a")
	torn := "{\"id\":\"torn\",\"name\":\"\xff\"}"
	unprovable := `{"id":"drift","name":"Drift","utility_score":"nope"}`
	f, err := os.OpenFile(skillsPath(ws), os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(torn + "\n" + unprovable + "\n"); err != nil {
		t.Fatal(err)
	}
	f.Close()

	warns, err := SaveSkills(ws, pool, NewIDSet(), NewIDSet())
	if err != nil {
		t.Fatal(err)
	}
	lines := poolLines(t, ws)
	if len(lines) != 3 {
		t.Fatalf("every row must survive: %+v", lines)
	}
	var sawTorn, sawDrift bool
	for _, l := range lines {
		if l == torn {
			sawTorn = true
		}
		if l == unprovable {
			sawDrift = true
		}
	}
	if !sawTorn || !sawDrift {
		t.Fatalf("carried rows must be BYTE-identical: %+v", lines)
	}
	if !strings.Contains(strings.Join(warns, "\n"),
		"1 unparseable/byte-tainted and 1 unprovable row(s) carried") {
		t.Fatalf("carry-through must be announced: %+v", warns)
	}
}

// A named write against a row that is present but UNPROVABLE must say
// "present, repair and retry" — "concurrently removed" would send the
// operator hunting a deletion that never happened.
func TestSaveSkillsDistinguishesStrandedFromGhostNamedWrites(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a")
	unprovable := `{"id":"drift","name":"Drift","utility_score":"nope"}`
	appendLine(t, skillsPath(ws), unprovable)

	drift := base("drift", "Drift")
	warns, err := SaveSkills(ws, append(pool, drift), NewIDSet(), NewIDSet("drift"))
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(warns, "\n")
	if !strings.Contains(joined, "present but unprovable, carried verbatim; repair and retry") {
		t.Fatalf("stranded named write: %+v", warns)
	}
	if strings.Contains(joined, "concurrently removed") {
		t.Fatalf("a present row must not be reported as removed: %q", joined)
	}
	for _, l := range poolLines(t, ws) {
		if l == unprovable {
			return
		}
	}
	t.Fatal("the unprovable row must still be there, verbatim")
}

// A named DROP whose live row fails the proof silently no-op'd: the cull
// returned clean and the row survived.
func TestSaveSkillsAnnouncesADropItCouldNotApply(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a")
	unprovable := `{"id":"drift","name":"Drift","utility_score":"nope"}`
	appendLine(t, skillsPath(ws), unprovable)

	warns, err := SaveSkills(ws, pool, NewIDSet("drift"), NewIDSet())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(warns, "\n"),
		"named drop(s) NOT applied") {
		t.Fatalf("an unapplied drop must be announced: %+v", warns)
	}
	found := false
	for _, l := range poolLines(t, ws) {
		if l == unprovable {
			found = true
		}
	}
	if !found {
		t.Fatal("the row was removed despite failing the proof")
	}
}

// With unreadable rows present, absence is NOT proven — a drop that landed
// in no bucket must hedge. With none, silence is honest.
func TestSaveSkillsHedgesOnlyWhenUnreadableRowsRode(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a")
	warns, err := SaveSkills(ws, pool, NewIDSet("gone"), NewIDSet())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.Join(warns, "\n"), "could NOT be verified") {
		t.Fatalf("with no unreadable rows the drop is vacuously satisfied: %+v", warns)
	}
	appendLine(t, skillsPath(ws), "{\"id\":\"torn\",\"name\":\"\xff\"}")
	warns, err = SaveSkills(ws, pool, NewIDSet("gone"), NewIDSet())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(warns, "\n"), "could NOT be verified") {
		t.Fatalf("an unreadable row makes absence unproven: %+v", warns)
	}
}

// Ordinals are held: this store is read last-row-wins by id, so a rewrite
// that appends the named rows after the survivors could promote a carried
// row over a live skill purely by moving it.
func TestSaveSkillsHoldsOrdinals(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a", "b", "c")
	for i := range pool {
		if pool[i].ID == "a" {
			pool[i].Description = "named write"
		}
	}
	if _, err := SaveSkills(ws, pool, NewIDSet(), NewIDSet("a")); err != nil {
		t.Fatal(err)
	}
	lines := poolLines(t, ws)
	if len(lines) != 3 || !strings.Contains(lines[0], `"id":"a"`) {
		t.Fatalf("the rewritten row must hold its ordinal: %+v", lines)
	}
}

// Contradictory intent is a caller bug, refused before the lock with the
// store untouched.
func TestSaveSkillsRefusesContradictoryIntent(t *testing.T) {
	ws := t.TempDir()
	pool := seed(t, ws, "a")
	before := poolLines(t, ws)
	for _, c := range []struct {
		name             string
		dropped, updated IDSet
		want             string
	}{
		{"both", NewIDSet("a"), NewIDSet("a"), "both updated and dropped"},
		{"updated absent", NewIDSet(), NewIDSet("nope"), "absent from the caller's list"},
		{"dropped present", NewIDSet("a"), NewIDSet(), "still present in the caller's list"},
	} {
		_, err := SaveSkills(ws, pool, c.dropped, c.updated)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: got %v", c.name, err)
		}
	}
	after := poolLines(t, ws)
	if len(before) != len(after) || before[0] != after[0] {
		t.Fatal("a refused rewrite must leave the store untouched")
	}
}

// A Skill built in memory carries no content_hash, and content_hash is a
// field the reader REQUIRES — without the backfill the store fills with
// rows that can never be removed again (probed in Python: a deliberate
// island cull left every culled skill live).
func TestSaveSkillsBackfillsHashForNamedWritesOnly(t *testing.T) {
	ws := t.TempDir()
	seed(t, ws, "a", "b")
	fresh := LoadSkills(ws).Skills
	for i := range fresh {
		fresh[i].ContentHash = ""
	}
	if _, err := SaveSkills(ws, fresh, NewIDSet(), NewIDSet("a")); err != nil {
		t.Fatal(err)
	}
	got := LoadSkills(ws).Skills
	if len(got) != 2 {
		t.Fatalf("pool: %+v", got)
	}
	for _, s := range got {
		if s.ContentHash == "" {
			t.Fatalf("%s has no hash: the row can never be removed again", s.ID)
		}
	}
}

// provenanceText concatenates every sidecar provenance record, newest
// first — the same set (and order) load_skill_provenance globs.
func provenanceText(t *testing.T, ws string) string {
	t.Helper()
	dir := filepath.Join(ws, "memory", "skill_provenance")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("no provenance directory — Python's reader globs %s: %v", dir, err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sortStrings(names)
	var sb strings.Builder
	for i := len(names) - 1; i >= 0; i-- {
		raw, err := os.ReadFile(filepath.Join(dir, names[i]))
		if err != nil {
			t.Fatal(err)
		}
		sb.WriteString(names[i] + "\n" + string(raw) + "\n")
	}
	return sb.String()
}

func appendLine(t *testing.T, path, line string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(line + "\n"); err != nil {
		t.Fatal(err)
	}
}

// --- islands ---

func TestAssignIslandScoresAndTieBreaks(t *testing.T) {
	research := base("r", "Fetcher")
	research.TriggerPatterns = []string{"research the web", "search for news"}
	if got := AssignIsland(research); got != "research" {
		t.Errorf("research: %s", got)
	}
	build := base("b", "Builder")
	build.Description = "implement and generate code"
	if got := AssignIsland(build); got != "build" {
		t.Errorf("build: %s", got)
	}
	none := base("n", "Nothing")
	none.Description = "zzz"
	if got := AssignIsland(none); got != IslandDefault {
		t.Errorf("default: %s", got)
	}
	// A tie resolves by islandOrder, not by map iteration — the same skill
	// must land on the same island every run, in both runtimes. Exactly one
	// keyword each, and no keyword that is a substring of another ("research"
	// would also match research's own "search" and score two).
	tie := base("t", "T")
	tie.Description = "web make" // research:"web" 1, build:"make" 1
	first := AssignIsland(tie)
	if first != "research" {
		t.Fatalf("a tie must go to the first island in order: %s", first)
	}
	for i := 0; i < 20; i++ {
		if AssignIsland(tie) != first {
			t.Fatal("island assignment is not deterministic")
		}
	}
	// Tags and domain participate.
	tagged := base("g", "Zzz")
	tagged.Description = "zzz"
	tagged.Tags = []string{"AUDIT"}
	if got := AssignIsland(tagged); got != "analysis" {
		t.Errorf("tags must count (lowercased): %s", got)
	}
}

// Only proven-underperforming skills are eligible, and the archive lands
// BEFORE the pool rewrite: a crash between them leaves a harmless
// duplicate, never a destroyed skill.
func TestCullIslandBottomHalfArchivesBeforeRemoving(t *testing.T) {
	ws := t.TempDir()
	var pool []Skill
	for i, id := range []string{"o1", "o2", "o3", "closed1"} {
		s := base(id, "Skill "+id)
		s.Island = "research"
		s.CircuitState = "open"
		s.UtilityScore = 0.1 * float64(i+1)
		if id == "closed1" {
			s.CircuitState = "closed"
		}
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
		pool = append(pool, s)
	}
	rep, err := CullIslandBottomHalf(ws, "research", 4, false)
	if err != nil {
		t.Fatal(err)
	}
	// 3 open skills → bottom half = 1 (integer division, floored at 1).
	if len(rep.CulledIDs) != 1 || rep.CulledIDs[0] != "o1" {
		t.Fatalf("worst open-circuit skill first: %+v", rep.CulledIDs)
	}
	ids := map[string]bool{}
	for _, s := range LoadSkills(ws).Skills {
		ids[s.ID] = true
	}
	if ids["o1"] {
		t.Fatal("the culled skill is still live")
	}
	if !ids["closed1"] {
		t.Fatal("a closed-circuit skill is not eligible for culling")
	}
	archived, err := os.ReadFile(skillsArchivePath(ws))
	if err != nil {
		t.Fatalf("retention copy missing: %v", err)
	}
	if !strings.Contains(string(archived), `"id":"o1"`) ||
		!strings.Contains(string(archived), "island_cull") {
		t.Fatalf("archive row: %s", archived)
	}
	// And the decision is auditable — in the sidecar directory Python's
	// load_skill_provenance globs, keyed by NAME, not the skill id.
	prov := provenanceText(t, ws)
	if !strings.Contains(prov, `"decision": "retire"`) ||
		!strings.Contains(prov, "island cull: bottom-half of open-circuit pool in 'research'") ||
		!strings.Contains(prov, `"skill_id": "o1"`) ||
		!strings.Contains(prov, `"archived_to": "skills_archive.jsonl"`) {
		t.Fatalf("provenance record: %s", prov)
	}
	if !strings.HasPrefix(prov, "Skill o1_") || !strings.Contains(prov, "Z.json") {
		t.Fatalf("filename must be {skill_name}_{stamp}Z.json: %s", prov)
	}
}

func TestCullIslandBottomHalfGates(t *testing.T) {
	ws := t.TempDir()
	for _, id := range []string{"a", "b", "c"} {
		s := base(id, "Skill "+id)
		s.Island = "research"
		s.CircuitState = "open"
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
	rep, err := CullIslandBottomHalf(ws, "research", 4, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.CulledIDs) != 0 {
		t.Fatalf("an island below the minimum size must be left alone: %+v", rep.CulledIDs)
	}
	if _, err := os.Stat(skillsArchivePath(ws)); err == nil {
		t.Fatal("a skipped cull must not write an archive")
	}
	// Dry run names the same ids and changes nothing.
	s := base("d", "Skill d")
	s.Island, s.CircuitState = "research", "open"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	before := poolLines(t, ws)
	rep, err = CullIslandBottomHalf(ws, "research", 4, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.CulledIDs) != 2 {
		t.Fatalf("4 open skills → bottom half = 2: %+v", rep.CulledIDs)
	}
	if got := poolLines(t, ws); len(got) != len(before) {
		t.Fatal("a dry run must not touch the store")
	}
}

// The cycle's island assignment must NAME its writes, or it reverts every
// concurrent save — its list comes from an unlocked read.
func TestRunIslandCyclePersistsAssignmentsWithoutRevertingConcurrentWrites(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Fetcher")
	s.TriggerPatterns = []string{"research the web"}
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	rep, err := RunIslandCycle(ws, nil, 4, false)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Assigned != 1 {
		t.Fatalf("assigned=%d", rep.Assigned)
	}
	got := LoadSkills(ws).Skills
	if got[0].Island != "research" {
		t.Fatalf("island not persisted: %+v", got[0])
	}
	// A second cycle assigns nothing and writes nothing.
	before := poolLines(t, ws)
	rep, err = RunIslandCycle(ws, nil, 4, false)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Assigned != 0 {
		t.Fatalf("re-assigned an already-assigned skill: %+v", rep)
	}
	if after := poolLines(t, ws); after[0] != before[0] {
		t.Fatal("a no-op cycle rewrote the store")
	}
}

// --- promote / demote ---

func TestAutoPromoteGatesOnLiveStatsNotTheDeadCounter(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Candidate")
	s.UtilityScore = 0.9
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	rep, err := MaybeAutoPromoteSkills(ws, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 0 {
		t.Fatalf("no uses yet: %+v", rep)
	}
	for i := 0; i < AutoPromoteMinUses; i++ {
		if _, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
	}
	rep, err = MaybeAutoPromoteSkills(ws, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 1 || rep.PromotedIDs[0] != "a" {
		t.Fatalf("live stats must satisfy the uses gate: %+v", rep)
	}
	if LoadSkills(ws).Skills[0].Tier != "established" {
		t.Fatal("the tier change did not land")
	}
}

// Verdict-grounded evidence vetoes the inflated legacy counters.
func TestAutoPromoteHeldByContradictingInjectedEvidence(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Candidate")
	s.UtilityScore = 0.9
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < AutoPromoteMinUses; i++ {
		if _, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
	}
	// Three injected runs, all failed: the honest evidence disagrees.
	for i := 0; i < 3; i++ {
		if _, err := RecordSkillInjectionOutcomes(ws, []string{"a"}, false); err != nil {
			t.Fatal(err)
		}
	}
	rep, err := MaybeAutoPromoteSkills(ws, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 0 {
		t.Fatalf("injected evidence must veto: %+v", rep)
	}
	if !strings.Contains(rep.Held["a"], "contradicts legacy counters") {
		t.Fatalf("the hold must say why: %+v", rep.Held)
	}
}

// The cap counts CANDIDATES, not successes.
func TestAutoPromoteLimitCountsCandidates(t *testing.T) {
	ws := t.TempDir()
	for _, id := range []string{"a", "b", "c"} {
		s := base(id, "Skill "+id)
		s.UtilityScore = 0.9
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
		for i := 0; i < AutoPromoteMinUses; i++ {
			if _, err := RecordSkillOutcome(ws, id, true, OutcomeTelemetry{Confidence: 1}); err != nil {
				t.Fatal(err)
			}
		}
	}
	rep, err := MaybeAutoPromoteSkills(ws, 2, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 2 {
		t.Fatalf("the cap must bound one sweep: %+v", rep.PromotedIDs)
	}
}

func TestDemoteNeedsSustainedEvidence(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Established")
	s.Tier = "established"
	s.UtilityScore = 0.1
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	rep, err := MaybeDemoteSkills(ws, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.DemotedIDs) != 0 {
		t.Fatalf("a bad score with no uses is not evidence: %+v", rep)
	}
	for i := 0; i < RewriteMinUses; i++ {
		if _, err := RecordSkillOutcome(ws, "a", false, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
	}
	rep, err = MaybeDemoteSkills(ws, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.DemotedIDs) != 1 {
		t.Fatalf("a settled low EMA with uses must demote: %+v", rep)
	}
	if LoadSkills(ws).Skills[0].Tier != "provisional" {
		t.Fatal("the demotion did not land")
	}
	// The stored reason is prose in a shared file — spelled Python's way.
	prov := provenanceText(t, ws)
	if !strings.Contains(prov, `"reason": "utility_score=0.100 < 0.4"`) {
		t.Fatalf("reason prose: %s", prov)
	}
	if !strings.Contains(prov, `"utility_score": 0.1`) ||
		!strings.Contains(prov, `"circuit_state": "closed"`) {
		t.Fatalf("Python's extras must ride: %s", prov)
	}
}

func TestDemoteOnOpenCircuitEvenWithFineUtility(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Established")
	s.Tier = "established"
	s.UtilityScore = 0.95
	s.CircuitState = "open"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < RewriteMinUses; i++ {
		if _, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
	}
	rep, err := MaybeDemoteSkills(ws, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.DemotedIDs) != 1 {
		t.Fatalf("a tripped circuit is sustained failure: %+v", rep)
	}
	if prov := provenanceText(t, ws); !strings.Contains(prov,
		"circuit breaker open (sustained failures)") {
		t.Fatalf("reason: %s", prov)
	}
}

func TestSkillsNeedingRewriteRequiresAnOpenCircuit(t *testing.T) {
	ws := t.TempDir()
	blip := base("blip", "Blip")
	blip.UtilityScore = 0.1
	blip.CircuitState = "closed"
	open := base("open", "Open")
	open.UtilityScore = 0.1
	open.CircuitState = "open"
	for _, s := range []Skill{blip, open} {
		sk := s
		if err := SaveSkill(ws, &sk); err != nil {
			t.Fatal(err)
		}
		for i := 0; i < RewriteMinUses; i++ {
			if _, err := RecordSkillOutcome(ws, sk.ID, false, OutcomeTelemetry{Confidence: 1}); err != nil {
				t.Fatal(err)
			}
		}
	}
	got := SkillsNeedingRewrite(ws)
	if len(got) != 1 || got[0].ID != "open" {
		t.Fatalf("a transient failure must not spend a rewrite: %+v", got)
	}
}

// The captain's-log linkage is related_ids, not loop_id: filing a subject
// linkage as a run id invents a run AND loses the linkage.
func TestSkillEventsCarryRelatedIDsNotLoopID(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	s := base("a", "Established")
	s.Tier = "established"
	s.UtilityScore = 0.1
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < RewriteMinUses; i++ {
		if _, err := RecordSkillOutcome(ws, "a", false, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := MaybeDemoteSkills(ws, rec); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(ws + "/memory/captains_log.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	line := string(raw)
	if !strings.Contains(line, `"related_ids":["skill:a"]`) {
		t.Fatalf("linkage missing: %s", line)
	}
	if strings.Contains(line, `"loop_id":"skill:a"`) {
		t.Fatalf("a subject linkage was filed as a run id: %s", line)
	}
}
