package skills

import (
	"os"
	"strings"
	"testing"
)

// Physical rows are counted apart from ids: a legacy store holding duplicate
// rows for one dropped id used to announce fewer removals than it performed.
func TestSaveSkillsCountsPhysicalRowsRemovedNotIDs(t *testing.T) {
	ws := t.TempDir()
	seed(t, ws, "a", "dup")
	// A second physical row for "dup" — legal in an append-only store, read
	// last-row-wins. Appended raw, because SaveSkill upserts in place and so
	// cannot produce the shape a legacy store actually holds.
	second := base("dup", "Name dup")
	second.Description = "second row"
	second.ContentHash = ComputeSkillHash(second)
	line, err := proveLine(second)
	if err != nil {
		t.Fatal(err)
	}
	appendLine(t, skillsPath(ws), line)
	if got := len(poolLines(t, ws)); got != 3 {
		t.Fatalf("expected a duplicate row on disk, got %d", got)
	}
	var keep []Skill
	for _, s := range LoadSkills(ws).Skills {
		if s.ID != "dup" {
			keep = append(keep, s)
		}
	}
	warns, err := SaveSkills(ws, keep, NewIDSet("dup"), NewIDSet())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(warns, "\n"),
		"2 physical row(s) for 1 named id(s) removed") {
		t.Fatalf("the announcement must count rows, not ids: %+v", warns)
	}
	if got := len(poolLines(t, ws)); got != 1 {
		t.Fatalf("both rows must go: %+v", poolLines(t, ws))
	}
}

// content_hash is DERIVED, so a not-yet-backfilled empty hash is not an edit
// and must not raise the divergence alarm — the alarm is for real drift, and
// one that fires on every unhashed pool trains operators to ignore it.
func TestSaveSkillsDoesNotCallAMissingHashADivergence(t *testing.T) {
	ws := t.TempDir()
	seed(t, ws, "a", "b")
	fresh := LoadSkills(ws).Skills
	for i := range fresh {
		fresh[i].ContentHash = ""
	}
	warns, err := SaveSkills(ws, fresh, NewIDSet(), NewIDSet("a"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.Join(warns, "\n"), "differ from the live store") {
		t.Fatalf("a derived field must not read as an edit: %+v", warns)
	}
}

// --- island selection pressure ---

// The compactness penalty DIVIDES utility and is floored at 1.0. Without the
// floor a short skill is boosted above its own utility, which inverts the
// cull order and retires the better skill.
func TestCullOrdersByCompactnessAdjustedScoreWithTheFloor(t *testing.T) {
	ws := t.TempDir()
	// Both are SHORTER than the floor's crossover (200*(e-1) ≈ 344 chars),
	// which is the only region where the floor changes anything.
	short := base("short", "Short")
	short.Description = "tiny" // penalty ln(1.02) ≈ 0.02
	short.UtilityScore = 0.3
	short.Island, short.CircuitState = "research", "open"
	long := base("long", "Long")
	long.Description = strings.Repeat("verbose ", 42) // 336 chars, penalty ≈ 0.99
	long.UtilityScore = 0.4
	long.Island, long.CircuitState = "research", "open"
	filler1, filler2 := base("f1", "F1"), base("f2", "F2")
	filler1.Island, filler2.Island = "research", "research"
	for _, s := range []Skill{short, long, filler1, filler2} {
		sk := s
		if err := SaveSkill(ws, &sk); err != nil {
			t.Fatal(err)
		}
	}
	rep, err := CullIslandBottomHalf(ws, "research", 4, true)
	if err != nil {
		t.Fatal(err)
	}
	// With the floor both keep their own utility: 0.3 vs 0.4, so "short" is
	// worst. Without it, dividing by a sub-1 penalty BOOSTS the shorter
	// skill to ~15 and the better one is retired instead.
	if len(rep.CulledIDs) != 1 || rep.CulledIDs[0] != "short" {
		t.Fatalf("the length penalty must never boost a skill above its own "+
			"utility: %+v", rep.CulledIDs)
	}
}

// max(1, len//2): an island large enough to cull with a single open-circuit
// skill still culls it. Integer division alone would return zero and the
// selection pressure would never apply to the first proven-bad skill.
func TestCullAlwaysRetiresAtLeastOneEligibleSkill(t *testing.T) {
	ws := t.TempDir()
	bad := base("bad", "Bad")
	bad.Island, bad.CircuitState = "research", "open"
	if err := SaveSkill(ws, &bad); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"f1", "f2", "f3"} {
		s := base(id, "Fine "+id)
		s.Island = "research"
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
	rep, err := CullIslandBottomHalf(ws, "research", 4, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.CulledIDs) != 1 || rep.CulledIDs[0] != "bad" {
		t.Fatalf("1/2 == 0, but one open-circuit skill is still the bottom "+
			"half: %+v", rep.CulledIDs)
	}
}

// Retention decree: the archive lands BEFORE the rewrite, and its error
// ABORTS. A retention copy that did not land must never be followed by a
// removal — the alternative is a silently destroyed skill.
func TestCullAbortsWhenTheRetentionCopyCannotBeWritten(t *testing.T) {
	ws := t.TempDir()
	for i, id := range []string{"o1", "o2", "f1", "f2"} {
		s := base(id, "Skill "+id)
		s.Island = "research"
		s.UtilityScore = 0.1 * float64(i+1)
		if strings.HasPrefix(id, "o") {
			s.CircuitState = "open"
		}
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
	// Make the archive unwritable: a directory where the file must go.
	if err := os.MkdirAll(skillsArchivePath(ws), 0o755); err != nil {
		t.Fatal(err)
	}
	before := poolLines(t, ws)

	_, err := CullIslandBottomHalf(ws, "research", 4, false)
	if err == nil {
		t.Fatal("a cull whose retention copy failed must return the error")
	}
	if !strings.Contains(err.Error(), "retention copy not written") {
		t.Fatalf("the error must say what was not written: %v", err)
	}
	after := poolLines(t, ws)
	if len(after) != len(before) {
		t.Fatalf("the pool was rewritten anyway: %d → %d", len(before), len(after))
	}
	for _, s := range LoadSkills(ws).Skills {
		if s.ID == "o1" {
			return
		}
	}
	t.Fatal("the skill was destroyed with no archive copy")
}

// --- promote / demote gates ---

// Uses alone are not enough: the utility floor is the quality half of the
// bar, and a well-used bad skill must stay provisional.
func TestAutoPromoteHeldByTheUtilityFloor(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Well used, not good")
	s.UseCount = AutoPromoteMinUses // legacy counter, no live stats needed
	s.UtilityScore = AutoPromoteMinRate - 0.01
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	rep, err := MaybeAutoPromoteSkills(ws, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 0 {
		t.Fatalf("below the utility floor: %+v", rep.PromotedIDs)
	}
	s.UtilityScore = AutoPromoteMinRate
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	rep, err = MaybeAutoPromoteSkills(ws, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 1 {
		t.Fatalf("exactly at the floor must pass (>=, not >): %+v", rep)
	}
}

// Injected evidence that AGREES confirms rather than vetoes — the veto is a
// contradiction test, not a penalty for having evidence at all.
func TestAutoPromoteConfirmedByAgreeingInjectedEvidence(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Candidate")
	s.UseCount = AutoPromoteMinUses
	s.UtilityScore = 0.9
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if _, err := RecordSkillInjectionOutcomes(ws, []string{"a"}, true); err != nil {
			t.Fatal(err)
		}
	}
	rep, err := MaybeAutoPromoteSkills(ws, 10, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 1 || len(rep.Held) != 0 {
		t.Fatalf("agreeing evidence must confirm: %+v held=%+v", rep.PromotedIDs, rep.Held)
	}
}

// Only provisional skills are candidates, so an established pool does not
// consume the sweep's cap.
func TestAutoPromoteSkipsNonProvisionalSkills(t *testing.T) {
	ws := t.TempDir()
	for _, id := range []string{"e1", "e2"} {
		s := base(id, "Established "+id)
		s.Tier = "established"
		s.UseCount, s.UtilityScore = AutoPromoteMinUses, 0.9
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
	p := base("p", "Provisional")
	p.UseCount, p.UtilityScore = AutoPromoteMinUses, 0.9
	if err := SaveSkill(ws, &p); err != nil {
		t.Fatal(err)
	}
	rep, err := MaybeAutoPromoteSkills(ws, 1, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 1 || rep.PromotedIDs[0] != "p" {
		t.Fatalf("established skills must not consume the cap: %+v", rep.PromotedIDs)
	}
}

// The EMA lags a sudden break, so a structural failure STREAK qualifies a
// skill for rewriting even while its smoothed utility still looks fine.
func TestSkillsNeedingRewriteCatchesAStreakTheEMAHasNotCaughtUpWith(t *testing.T) {
	ws := t.TempDir()
	s := base("a", "Recently broken")
	s.CircuitState = "open"
	s.UtilityScore = 0.95 // EMA still high
	s.ConsecutiveFailures = CircuitOpenThreshold
	s.UseCount = RewriteMinUses
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	got := SkillsNeedingRewrite(ws)
	if len(got) != 1 || got[0].ID != "a" {
		t.Fatalf("a structural streak qualifies on its own: %+v", got)
	}
	// One failure short of the threshold, with a healthy EMA: neither half
	// holds, so nothing is spent.
	s.ConsecutiveFailures = CircuitOpenThreshold - 1
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	if got := SkillsNeedingRewrite(ws); len(got) != 0 {
		t.Fatalf("neither half holds: %+v", got)
	}
}
