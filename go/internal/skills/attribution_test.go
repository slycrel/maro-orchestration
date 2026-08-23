package skills

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// seedRun creates a run dir holding a skills manifest naming ids, and
// returns the run dir.
func seedRun(t *testing.T, ws, loopID string, lines ...string) string {
	t.Helper()
	runDir := filepath.Join(ws, "output", "runs", loopID)
	src := filepath.Join(runDir, "source")
	if err := os.MkdirAll(src, 0o777); err != nil {
		t.Fatal(err)
	}
	body := strings.Join(lines, "\n")
	if body != "" {
		body += "\n"
	}
	if err := os.WriteFile(filepath.Join(src, "skills_manifest.jsonl"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return runDir
}

func manifestLine(ids ...string) string {
	entries := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		entries = append(entries, map[string]any{"id": id, "name": id, "tier": "provisional"})
	}
	raw, _ := json.Marshal(map[string]any{
		"ts": "2026-08-23T00:00:00.000000+00:00", "stage": "decompose", "skills": entries,
	})
	return string(raw)
}

// seedOutcomeRow writes one outcomes.jsonl row for loopID so the stamp has
// something to land on.
func seedOutcomeRow(t *testing.T, ws, loopID string) *record.Recorder {
	t.Helper()
	rec := record.New(ws)
	if _, err := rec.WriteOutcome(record.Outcome{
		LoopID: loopID, Goal: "g", Status: "done", TaskType: "general",
	}); err != nil {
		t.Fatal(err)
	}
	return rec
}

func hasStatsRow(t *testing.T, ws, id string) bool {
	t.Helper()
	for _, st := range LoadSkillStats(ws).Stats {
		if st.SkillID == id {
			return true
		}
	}
	return false
}

func injectedCounts(t *testing.T, ws, id string) (runs, successes int) {
	t.Helper()
	load := LoadSkillStats(ws)
	if load.Err != nil {
		t.Fatal(load.Err)
	}
	for _, st := range load.Stats {
		if st.SkillID == id {
			return st.InjectedRuns, st.InjectedSuccesses
		}
	}
	return 0, 0
}

// The headline: a stamped FULL-trust verdict reaches the skills the run
// actually injected. Before this wiring both ends were live — the loop wrote
// the manifest on every run and stamped the verdict on every run — and
// nothing joined them, so injected_runs sat at 0 forever.
func TestAStampedVerdictCreditsTheRunsInjectedSkills(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1", manifestLine("sk-a", "sk-b"))
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	mustSeedSkill(t, ws, base("sk-b", "Skill B"))

	yes := true
	warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(warns) != 0 {
		t.Errorf("unexpected warnings: %v", warns)
	}
	for _, id := range []string{"sk-a", "sk-b"} {
		runs, succ := injectedCounts(t, ws, id)
		if runs != 1 || succ != 1 {
			t.Errorf("%s: injected_runs=%d successes=%d, want 1/1", id, runs, succ)
		}
	}
	if _, err := os.Stat(filepath.Join(runDir, "source", "skill_attribution.json")); err != nil {
		t.Errorf("no idempotence marker was written: %v", err)
	}
}

// THE CONSEQUENCE. This is the test that says the gate is alive rather than
// merely wired: four FAILED injected verdicts must hold a skill at
// provisional whose legacy counters would otherwise promote it. Measured
// against Python on identical seeds, this was the exact divergence — Python
// held, Go promoted, off the same store.
func TestFailedInjectedEvidenceVetoesAPromotionThatLegacyCountersWouldAllow(t *testing.T) {
	ws := t.TempDir()
	s := base("sk-inj", "Injected Skill")
	s.Description = "a provisional whose legacy counters look great"
	s.TriggerPatterns = []string{"inject"}
	s.Tier, s.UtilityScore = "provisional", 0.95
	s.UseCount, s.SuccessRate = 20, 1.0
	s.ContentHash = ComputeSkillHash(s)
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}

	rec := record.New(ws)
	no := false
	for _, loopID := range []string{"L1", "L2", "L3", "L4"} {
		if _, err := rec.WriteOutcome(record.Outcome{
			LoopID: loopID, Goal: "g", Status: "done", TaskType: "general",
		}); err != nil {
			t.Fatal(err)
		}
		runDir := seedRun(t, ws, loopID, manifestLine("sk-inj"))
		if _, err := StampVerdictWithAttribution(rec, runDir, loopID, &no,
			record.SourceClosure, nil); err != nil {
			t.Fatal(err)
		}
	}
	runs, succ := injectedCounts(t, ws, "sk-inj")
	if runs != 4 || succ != 0 {
		t.Fatalf("injected evidence = %d runs / %d successes, want 4/0", runs, succ)
	}

	rep, err := MaybeAutoPromoteSkills(ws, 10, rec)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.PromotedIDs) != 0 {
		t.Errorf("promoted %v despite four failed injected runs — the veto is "+
			"open, which is what a dead attribution writer looks like",
			rep.PromotedIDs)
	}
	if rep.Held["sk-inj"] == "" {
		t.Error("the hold was not announced; a silent veto is as bad as none")
	}
	after := LoadSkills(ws)
	for _, sk := range after.Skills {
		if sk.ID == "sk-inj" && sk.Tier != "provisional" {
			t.Errorf("tier = %q, want provisional", sk.Tier)
		}
	}
}

// The three gates, each with a positive control so a test that stopped
// reaching the writer would not read as a pass.
func TestAttributionIsSilentWhenTheVerdictDoesNotEarnIt(t *testing.T) {
	low := 0.3
	cases := []struct {
		name       string
		achieved   *bool
		source     string
		confidence *float64
		wantRuns   int
	}{
		{"a full-trust verdict credits (control)", boolPtr(true), record.SourceClosure, nil, 1},
		{"an unjudged stamp is not a verdict", nil, record.SourceClosure, nil, 0},
		{"a below-floor confidence is directional, not full", boolPtr(true), record.SourceClosure, &low, 0},
		{"the verifier's own failure is excluded", boolPtr(true), "closure_unverifiable", nil, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			rec := seedOutcomeRow(t, ws, "L1")
			runDir := seedRun(t, ws, "L1", manifestLine("sk-a"))
			mustSeedSkill(t, ws, base("sk-a", "Skill A"))
			if _, err := StampVerdictWithAttribution(rec, runDir, "L1",
				c.achieved, c.source, c.confidence); err != nil {
				t.Fatal(err)
			}
			if runs, _ := injectedCounts(t, ws, "sk-a"); runs != c.wantRuns {
				t.Errorf("injected_runs = %d, want %d", runs, c.wantRuns)
			}
		})
	}
}

func boolPtr(b bool) *bool { return &b }

// The bool gate is NOT redundant with the trust gate, and a mutation battery
// is what proved it: deleting `if !present || !isBool { return nil }` left
// every other pin green.
//
// The reason is record.GoalAchieved's deliberate hardening — a goal_achieved
// that is present but not a bool grades judged-NOT-achieved rather than
// unjudged, so such a row reaches VerdictTrustFull and sails past gate 2.
// Without gate 1 the failed type assertion then yields achieved=false and the
// run's skills are credited with a FAILURE that nobody ever judged. Python is
// explicit here — `if not isinstance(row.get("goal_achieved"), bool): return`
// — and the two hardenings pull opposite ways on purpose: reading a malformed
// verdict pessimistically is right for a TRUST policy, and refusing to read
// it at all is right for a learning COUNTER.
//
// The row shapes below are the ones that separate the two gates. Absent and
// nil are screened by trust as neutral, so they prove nothing about gate 1;
// only a present non-bool does.
func TestAMalformedVerdictCreditsNothingEvenThoughItIsTrustedFull(t *testing.T) {
	for _, bad := range []any{"true", "false", 1.0, 0.0, []any{true},
		map[string]any{"v": true}} {
		ws := t.TempDir()
		runDir := seedRun(t, ws, "L1", manifestLine("sk-a"))
		mustSeedSkill(t, ws, base("sk-a", "Skill A"))
		row := map[string]any{"loop_id": "L1", "goal_achieved": bad}

		// Anti-vacuity: if this row did NOT reach full trust, the test would
		// pass on gate 2 and say nothing at all about gate 1.
		if got := record.VerdictTrust(row); got != record.VerdictTrustFull {
			t.Fatalf("goal_achieved=%#v graded %s, not full — this row no "+
				"longer exercises gate 1 and the test is vacuous", bad, got)
		}
		if warns := AttributeRunVerdict(ws, runDir, "L1", row); len(warns) != 0 {
			t.Errorf("goal_achieved=%#v: warnings %v", bad, warns)
		}
		if runs, succ := injectedCounts(t, ws, "sk-a"); runs != 0 || succ != 0 {
			t.Errorf("goal_achieved=%#v credited injected_runs=%d successes=%d, "+
				"want 0/0 — a verdict nobody judged was attributed as a failure",
				bad, runs, succ)
		}
	}
}

// A missing manifest means the recorder never ran; a present-but-empty one
// means nothing was injected. Neither is an error and neither credits
// anything — but they must not be conflated, because the loop's writer
// deliberately records the empty case.
func TestAnAbsentManifestAndAnEmptyOneBothCreditNothingWithoutComplaint(t *testing.T) {
	for _, lines := range [][]string{nil, {manifestLine()}} {
		ws := t.TempDir()
		rec := seedOutcomeRow(t, ws, "L1")
		runDir := filepath.Join(ws, "output", "runs", "L1")
		if lines != nil {
			runDir = seedRun(t, ws, "L1", lines...)
		} else if err := os.MkdirAll(filepath.Join(runDir, "source"), 0o777); err != nil {
			t.Fatal(err)
		}
		yes := true
		warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
			record.SourceClosure, nil)
		if err != nil {
			t.Fatal(err)
		}
		if len(warns) != 0 {
			t.Errorf("warnings for a legitimately empty manifest: %v", warns)
		}
	}
}

// Re-stamping is by design (audit repair re-runs it), so the marker must
// stop a second credit. Without it the same run's verdict lands twice and
// every rate built on injected_runs is wrong by a factor nobody can see.
func TestARestampDoesNotDoubleCredit(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1", manifestLine("sk-a"))
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	yes := true
	for i := 0; i < 3; i++ {
		if _, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
			record.SourceClosure, nil); err != nil {
			t.Fatal(err)
		}
	}
	if runs, succ := injectedCounts(t, ws, "sk-a"); runs != 1 || succ != 1 {
		t.Errorf("three stamps produced %d runs / %d successes, want 1/1", runs, succ)
	}
}

// A verdict a later stamp CORRECTED is announced, not absorbed and not
// re-applied: the committed batch cannot be decremented from here. "Is a
// bool" once accepted such a marker and the correction vanished silently.
func TestACorrectedVerdictIsAnnouncedAndNotReapplied(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1", manifestLine("sk-a"))
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	yes, no := true, false
	if _, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &no,
		record.SourceClosure, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(warns) == 0 || !strings.Contains(strings.Join(warns, "\n"), "corrected verdict") {
		t.Errorf("a flipped verdict was absorbed silently: %v", warns)
	}
	if runs, succ := injectedCounts(t, ws, "sk-a"); runs != 1 || succ != 1 {
		t.Errorf("the correction adjusted the store (%d/%d); it must not", runs, succ)
	}
}

// A marker that is not proof of what completion would have said is UNKNOWN:
// warn, do NOT re-apply. A zero-byte or copied marker once suppressed a
// whole run's verdicts in silence.
func TestAnUnprovableMarkerIsAnnouncedAsUnknownAndNotReapplied(t *testing.T) {
	for _, body := range []string{
		"", "not json at all", `{"loop_id": "OTHER", "goal_achieved": true, "skill_ids": ["sk-a"]}`,
		`{"loop_id": "L1", "goal_achieved": "yes", "skill_ids": ["sk-a"]}`,
		`{"loop_id": "L1", "goal_achieved": true, "skill_ids": ["sk-z"]}`,
	} {
		ws := t.TempDir()
		rec := seedOutcomeRow(t, ws, "L1")
		runDir := seedRun(t, ws, "L1", manifestLine("sk-a"))
		mustSeedSkill(t, ws, base("sk-a", "Skill A"))
		marker := filepath.Join(runDir, "source", "skill_attribution.json")
		if err := os.WriteFile(marker, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		yes := true
		warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
			record.SourceClosure, nil)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(strings.Join(warns, "\n"), "UNKNOWN") {
			t.Errorf("marker %q was not announced as unknown: %v", body, warns)
		}
		if runs, _ := injectedCounts(t, ws, "sk-a"); runs != 0 {
			t.Errorf("marker %q was re-applied over an unknown state", body)
		}
	}
}

// A marker whose id list is the same SET in a different ORDER is the same
// attribution — Python compares sets.
func TestAMarkerWithTheSameIDsInADifferentOrderStillSuppresses(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1", manifestLine("sk-a", "sk-b"))
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	mustSeedSkill(t, ws, base("sk-b", "Skill B"))
	marker := filepath.Join(runDir, "source", "skill_attribution.json")
	if err := os.WriteFile(marker,
		[]byte(`{"loop_id": "L1", "goal_achieved": true, "skill_ids": ["sk-b", "sk-a"]}`),
		0o644); err != nil {
		t.Fatal(err)
	}
	yes := true
	warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(warns) != 0 {
		t.Errorf("a reordered id list was treated as a mismatch: %v", warns)
	}
	if runs, _ := injectedCounts(t, ws, "sk-a"); runs != 0 {
		t.Error("the batch was re-applied over a valid marker")
	}
}

// A manifest id must BE a string. str() coercion once minted stats
// identities "True" and "7" out of malformed rows — laundered evidence, not
// admission. Excluded AND announced; the well-formed entries still land.
func TestMalformedManifestIDsAreExcludedAndAnnouncedNotCoerced(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	raw := `{"ts":"t","stage":"decompose","skills":[{"id":true},{"id":7},{"id":""},{"id":null},"loose",{"id":"sk-a"}]}`
	runDir := seedRun(t, ws, "L1", raw)
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	yes := true
	warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(warns, "\n"), "5 entr(ies) without a string id") {
		t.Errorf("malformed ids were not announced: %v", warns)
	}
	for _, bogus := range []string{"True", "true", "7", ""} {
		if hasStatsRow(t, ws, bogus) {
			t.Errorf("a stats identity %q was minted from a malformed id", bogus)
		}
	}
	if runs, _ := injectedCounts(t, ws, "sk-a"); runs != 1 {
		t.Error("the well-formed entry was dropped along with the malformed ones")
	}
}

// One verdict per skill per run, even when the manifest names it on several
// injection lines (decompose, replan, curated summaries all append).
func TestASkillNamedOnSeveralManifestLinesIsCreditedOnce(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1",
		manifestLine("sk-a", "sk-b"), manifestLine("sk-a"), manifestLine("sk-a", "sk-b"))
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	mustSeedSkill(t, ws, base("sk-b", "Skill B"))
	yes := true
	if _, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"sk-a", "sk-b"} {
		if runs, _ := injectedCounts(t, ws, id); runs != 1 {
			t.Errorf("%s credited %d times, want 1", id, runs)
		}
	}
}

// A torn manifest line is skipped and ANNOUNCED, and the readable lines
// still credit. This is the announced-read posture, which is Python's here —
// deliberately unlike internal/tasks, whose queue reads fail closed.
func TestATornManifestLineIsAnnouncedAndTheRestStillCredits(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1", manifestLine("sk-a"), `{"ts":"t","ski`)
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	yes := true
	warns, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(warns, "\n"), "unparseable line") {
		t.Errorf("a torn manifest line was skipped in silence: %v", warns)
	}
	if runs, _ := injectedCounts(t, ws, "sk-a"); runs != 1 {
		t.Error("a torn line cost the readable ones their credit")
	}
}

// The marker's bytes are Python's: a bare json.dumps, so `", "` and `": "`
// separators and no trailing newline. It is read back by both runtimes.
func TestTheMarkerIsWrittenWithPythonsCompactSpelling(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	runDir := seedRun(t, ws, "L1", manifestLine("sk-a"))
	mustSeedSkill(t, ws, base("sk-a", "Skill A"))
	yes := true
	if _, err := StampVerdictWithAttribution(rec, runDir, "L1", &yes,
		record.SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(runDir, "source", "skill_attribution.json"))
	if err != nil {
		t.Fatal(err)
	}
	got := string(raw)
	if !strings.HasPrefix(got, `{"loop_id": "L1", "goal_achieved": true, "skill_ids": ["sk-a"], "attributed_at": "`) {
		t.Errorf("marker bytes are not Python's json.dumps spelling:\n%s", got)
	}
	if strings.Contains(got, "\n") {
		t.Error("the marker carries a newline; json.dumps writes none")
	}
	// The prefix check above stops one byte short of the timestamp, so
	// every wrong spelling of it used to survive. Python writes
	// datetime.now(timezone.utc).isoformat() — an AWARE datetime, so the
	// "+00:00" offset is part of the value and there is no trailing "Z";
	// the fractional part is six digits or absent, never anything else.
	// This is the only field a reader has to parse, and the Python readers
	// that parse it use datetime.fromisoformat.
	stamp := regexp.MustCompile(`"attributed_at": "(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00)"}$`)
	if !stamp.MatchString(got) {
		t.Errorf("attributed_at is not Python's aware isoformat spelling "+
			"(expected ...+00:00, six fractional digits or none):\n%s", got)
	}
}

// The stamp must still return the row it wrote — attribution decides from
// the row that LANDED, not from the arguments the caller passed, and a
// reconstruction would drift the moment the merge rules change.
func TestTheStampReturnsTheRowItWrote(t *testing.T) {
	ws := t.TempDir()
	rec := seedOutcomeRow(t, ws, "L1")
	conf := 0.9
	yes := true
	row, err := rec.StampOutcomeVerdict("L1", &yes, record.SourceClosure, &conf)
	if err != nil {
		t.Fatal(err)
	}
	if row["goal_achieved"] != true {
		t.Errorf("goal_achieved = %v", row["goal_achieved"])
	}
	if row["goal_verdict_source"] != record.SourceClosure {
		t.Errorf("source = %v", row["goal_verdict_source"])
	}
	if record.VerdictTrust(row) != record.VerdictTrustFull {
		t.Errorf("trust = %q, want full", record.VerdictTrust(row))
	}
	if row["loop_id"] != "L1" {
		t.Errorf("the row is not the stamped one: %v", row["loop_id"])
	}
}
