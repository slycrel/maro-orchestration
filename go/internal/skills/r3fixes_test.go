package skills

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Pins for the r3 findings. Each was checked to FAIL against the pre-fix
// code before the fix was kept, and each comment says which wrong answer
// the old shape produced — a pin whose failure mode is not written down
// stops being re-derivable the first time it goes red.

// poolOrder is the order ids appear in the store, which is the only thing
// deciding who wins a last-row-wins keyed read and who lands inside a
// limit-capped sweep.
func poolOrder(ws string) []string {
	var ids []string
	for _, s := range LoadSkills(ws).Skills {
		ids = append(ids, s.ID)
	}
	return ids
}

func seedPool(t *testing.T, ws string, ids ...string) {
	t.Helper()
	for _, id := range ids {
		s := base(id, strings.ToUpper(id))
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
}

// H1. Both outcome writers used to save through SaveSkill — the port of
// Python's save_skill, which DROPS the matching row and appends the new one
// at the tail. Python routes both through _save_skills(updated_ids={id}),
// the ordinal-holding rewrite. pool.go states that invariant in its own
// words; the library's two highest-frequency writers were breaking it.
func TestOutcomeWritersHoldPoolOrdinals(t *testing.T) {
	ws := t.TempDir()
	seedPool(t, ws, "a", "b", "c", "d")
	if _, err := UpdateSkillUtility(ws, "b", false, "boom", ""); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(poolOrder(ws), ","); got != "a,b,c,d" {
		t.Fatalf("an outcome moved the row to %q — a tail-append gives "+
			`"a,c,d,b"; Python leaves it in place`, got)
	}

	ws = t.TempDir()
	seedPool(t, ws, "p", "q")
	parent := "p"
	chal := base("chal", "Challenger")
	chal.VariantOf = &parent
	if err := SaveSkill(ws, &chal); err != nil {
		t.Fatal(err)
	}
	seedPool(t, ws, "z")
	before := strings.Join(poolOrder(ws), ",")
	if _, err := RecordVariantOutcome(ws, "chal", true); err != nil {
		t.Fatal(err)
	}
	if after := strings.Join(poolOrder(ws), ","); after != before {
		t.Fatalf("a variant outcome reordered the pool: %q -> %q", before, after)
	}
}

// The order divergence only matters because a capped sweep reads it. This
// is that consequence end to end: one outcome, then a promotion sweep whose
// limit caps candidates. The promoted SET must not depend on which writer
// touched the store last.
func TestACappedPromotionSweepIsUnmovedByAnOutcome(t *testing.T) {
	ws := t.TempDir()
	for _, id := range []string{"a", "b", "c", "d"} {
		s := base(id, strings.ToUpper(id))
		// All four qualify, so the CAP is what selects and pool order is
		// what the cap reads.
		s.Tier, s.UseCount, s.UtilityScore = "provisional", AutoPromoteMinUses, 0.9
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := UpdateSkillUtility(ws, "b", true, "", ""); err != nil {
		t.Fatal(err)
	}
	rep, err := MaybeAutoPromoteSkills(ws, 2, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(rep.PromotedIDs, ","); got != "a,b" {
		t.Fatalf("promoted %q — Python promotes \"a,b\"; a tail-append "+
			"writer reorders the pool to a,c,d,b and promotes \"a,c\"", got)
	}
}

// L1. update_skill_utility recomputes content_hash; record_variant_outcome
// does NOT — and _save_skills backfills only an EMPTY hash for a named
// write, so a stored hash that disagrees with its skill SURVIVES and keeps
// warning on every load. Routing the variant write through SaveSkill
// recomputed it silently, so one A/B win permanently erased the
// tamper-detection signal with nothing announced.
func TestAVariantOutcomeDoesNotLaunderATamperedHash(t *testing.T) {
	ws := t.TempDir()
	seedPool(t, ws, "p")
	parent := "p"
	chal := base("chal", "Challenger")
	chal.VariantOf = &parent
	if err := SaveSkill(ws, &chal); err != nil {
		t.Fatal(err)
	}
	// Hand-set the challenger's hash to something that cannot be right,
	// the way a tampered row reads.
	pool := LoadSkills(ws).Skills
	for i := range pool {
		if pool[i].ID == "chal" {
			pool[i].ContentHash = strings.Repeat("0", 12)
		}
	}
	if _, err := SaveSkills(ws, pool, nil, NewIDSet("chal")); err != nil {
		t.Fatal(err)
	}
	if !containsSub(LoadSkills(ws).Announce(), "content_hash mismatch") {
		t.Fatal("the tamper signal must be present before the outcome")
	}

	if _, err := RecordVariantOutcome(ws, "chal", true); err != nil {
		t.Fatal(err)
	}
	if !containsSub(LoadSkills(ws).Announce(), "content_hash mismatch") {
		t.Error("an A/B win erased the tamper signal — Python's " +
			"record_variant_outcome does not recompute the hash")
	}
	// The asymmetry is the point: the OTHER writer does recompute, so a
	// real mutation stops claiming the old content.
	if _, err := UpdateSkillUtility(ws, "chal", true, "", ""); err != nil {
		t.Fatal(err)
	}
	if containsSub(LoadSkills(ws).Announce(), "content_hash mismatch") {
		t.Error("update_skill_utility must recompute the hash")
	}
}

func containsSub(list []string, sub string) bool {
	for _, s := range list {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}

// H2. The compactness penalty is the sort key for the only destructive tier
// path, and Python's len() on a str counts CODE POINTS. Counting bytes
// inflates the penalty for any non-ASCII skill, so two skills Python treats
// as an exact tie get ranked.
func TestCompactnessCountsCodePointsNotBytes(t *testing.T) {
	mk := func(id, desc string) Skill {
		s := base(id, id)
		s.Description, s.UtilityScore = desc, 0.3
		return s
	}
	ascii := mk("ascii", strings.Repeat("a", 400))
	accented := mk("accented", strings.Repeat("\u00e9", 400)) // 400 chars, 800 bytes
	a, b := compactnessAdjustedScore(ascii), compactnessAdjustedScore(accented)
	if a != b {
		t.Fatalf("identical code-point lengths must tie: %.17g vs %.17g "+
			"(a byte count gives the accented skill 0.1855)", a, b)
	}
	if want := 0.3 / math.Max(math.Log(1.0+400.0/200.0), 1.0); a != want {
		t.Fatalf("%.17g want %.17g", a, want)
	}
	// Steps count toward the same total, and the max(penalty, 1.0) floor is
	// why the divergence was invisible on small skills.
	short := mk("short", "tiny")
	short.StepsTemplate = []string{"\u00e9\u00e9\u00e9"}
	if got := compactnessAdjustedScore(short); got != 0.3 {
		t.Fatalf("the floor must hold below ~344 characters: %.17g", got)
	}
}

// The consequence of H2 where it bites: a cull that ranks a true tie and
// retires the wrong skill. A stable sort over equal scores keeps store
// order, so the FIRST of the tied pair goes; with a byte count the accented
// one scores 0.1855 and is retired instead.
func TestACullTreatsEqualLengthSkillsAsATie(t *testing.T) {
	ws := t.TempDir()
	mk := func(id, desc string, util float64, open bool) {
		s := base(id, id)
		s.Description, s.UtilityScore, s.Island = desc, util, "build"
		if open {
			s.CircuitState = "open"
		}
		if err := SaveSkill(ws, &s); err != nil {
			t.Fatal(err)
		}
	}
	mk("c1", strings.Repeat("a", 400), 0.30, true)
	mk("c2", strings.Repeat("\u00e9", 400), 0.30, true)
	mk("c3", "short", 0.90, true)
	mk("c4", "short", 0.95, false)

	cr, err := CullIslandBottomHalf(ws, "build", 0, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(cr.CulledIDs) != 1 || cr.CulledIDs[0] != "c1" {
		t.Fatalf("culled %v — a true tie resolves to store order, so c1 "+
			"goes; a byte count ranks c2 worst and retires it instead",
			cr.CulledIDs)
	}
}

// M3. Python's run_island_cycle writes one ISLAND_CULLED per island. This
// port wrote none, so a retirement happened with no entry in the one lane
// an operator watches — the archive and provenance rails carried the record
// and the log did not.
func TestIslandCycleAnnouncesEveryCull(t *testing.T) {
	seed := func(ws string) {
		for _, isl := range []string{"build", "analyze"} {
			for i, spec := range []struct {
				util float64
				open bool
			}{{0.10, true}, {0.90, true}, {0.95, false}, {0.99, false}} {
				s := base(isl+strconv.Itoa(i), isl)
				s.Description = strings.Repeat("x", 400)
				s.UtilityScore, s.Island = spec.util, isl
				if spec.open {
					s.CircuitState = "open"
				}
				if err := SaveSkill(ws, &s); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	ws := t.TempDir()
	seed(ws)
	rep, err := RunIslandCycle(ws, record.New(ws), 0, false)
	if err != nil {
		t.Fatal(err)
	}
	if rep.TotalCulled != 2 {
		t.Fatalf("expected one cull per island: %+v", rep)
	}
	var culls []map[string]any
	for _, r := range logRows(t, ws) {
		if r["event_type"] == "ISLAND_CULLED" {
			culls = append(culls, r)
		}
	}
	if len(culls) != 2 {
		t.Fatalf("one ISLAND_CULLED per island, got %d", len(culls))
	}
	// Sorted island order, so the rows land the same way every run rather
	// than in Go map order.
	if culls[0]["subject"] != "analyze" || culls[1]["subject"] != "build" {
		t.Errorf("island order: %v then %v", culls[0]["subject"], culls[1]["subject"])
	}
	c := culls[0]
	if c["audience"] != "user" {
		t.Errorf("a retirement is a user-lane decision, got %v", c["audience"])
	}
	if c["summary"] != "Culled 1 bottom-half skills from island." {
		t.Errorf("summary %q must match Python's wording", c["summary"])
	}
	ctx, _ := c["context"].(map[string]any)
	ids, _ := ctx["culled_ids"].([]any)
	if len(ids) != 1 || ids[0] != "analyze0" {
		t.Errorf("context.culled_ids: %+v", ctx)
	}
	rel, _ := c["related_ids"].([]any)
	if len(rel) != 1 || rel[0] != "skill:analyze0" {
		t.Errorf("related_ids: %+v", c["related_ids"])
	}

	// A dry run decides but does not announce, because it did not act.
	ws2 := t.TempDir()
	seed(ws2)
	rep2, err := RunIslandCycle(ws2, record.New(ws2), 0, true)
	if err != nil {
		t.Fatal(err)
	}
	if rep2.TotalCulled != 2 {
		t.Fatalf("a dry run still decides: %+v", rep2)
	}
	if n := len(logRows(t, ws2)); n != 0 {
		t.Errorf("a dry run announced %d retirement(s) it did not perform", n)
	}
}

func logPath(ws string) string {
	return filepath.Join(ws, "memory", "captains_log.jsonl")
}

// logRows reads every captain's-log entry in a workspace.
func logRows(t *testing.T, ws string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(logPath(ws))
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		t.Fatal(err)
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("unparseable log row %q: %v", line, err)
		}
		out = append(out, m)
	}
	return out
}

// M1 + M2 + L2 on one row: a circuit trip is a user-lane decision, its
// failure reason is a TOP-LEVEL note (context values render nowhere), and
// the row is spelled the way json.dumps spells it.
func TestCircuitEventCarriesItsEvidenceInTheUserLane(t *testing.T) {
	ws := t.TempDir()
	seedPool(t, ws, "brk")
	rec := record.New(ws)
	const reason = "jina timeout after 3 retries"
	var u UtilityUpdate
	for i := 0; i < CircuitOpenThreshold; i++ {
		var err error
		if u, err = UpdateSkillUtility(ws, "brk", false, reason, ""); err != nil {
			t.Fatal(err)
		}
	}
	if u.CircuitAfter != "open" {
		t.Fatalf("setup did not trip the breaker: %+v", u)
	}
	if err := LogCircuitTransition(rec, "brk", u, reason); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(logPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	line := strings.TrimSpace(string(raw))

	// M2: Python's render_entry reads entry["note"]; nothing renders
	// context values, so filed under context the reason survived search and
	// vanished from every human-facing render.
	row := logRows(t, ws)[0]
	if row["note"] != reason {
		t.Errorf("the failure reason must be a top-level note, got %v", row["note"])
	}
	if ctx, _ := row["context"].(map[string]any); ctx["note"] != nil {
		t.Errorf("the note must not ALSO be filed under context: %+v", ctx)
	}
	// M1: the registry drifted — every circuit trip was stamped "system"
	// and dropped from the curated user lane.
	if row["audience"] != "user" {
		t.Errorf("a circuit trip is a user-lane decision, got %v", row["audience"])
	}
	// L2: json.dumps does not escape > or <, and every transition summary
	// contains "->". Go's generic encoder writes \u003e.
	if strings.Contains(line, `\u003e`) || !strings.Contains(line, "Circuit closed -> open.") {
		t.Errorf("the summary's arrow was HTML-escaped:\n%s", line)
	}
	// L2: Python's log_event insertion order, not Go's alphabetical map
	// order. Alphabetical would put audience first and timestamp last.
	order := []string{`{"timestamp":`, `"event_type"`, `"subject"`, `"summary"`,
		`"audience"`, `"context"`, `"note"`, `"related_ids"`}
	for i := 0; i+1 < len(order); i++ {
		if strings.Index(line, order[i]) >= strings.Index(line, order[i+1]) {
			t.Fatalf("%s must precede %s:\n%s", order[i], order[i+1], line)
		}
	}

	// Half-open is deliberately NOT a user-lane event, matching Python: it
	// is a probation STATE, and the trip and recovery bracketing it are both
	// surfaced. An empty note writes NO key (`if note:`).
	u2, err := UpdateSkillUtility(ws, "brk", true, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if err := LogCircuitTransition(rec, "brk", u2, ""); err != nil {
		t.Fatal(err)
	}
	rows := logRows(t, ws)
	last := rows[len(rows)-1]
	if last["event_type"] != "SKILL_CIRCUIT_HALF_OPEN" {
		t.Fatalf("expected the probation transition, got %v", last["event_type"])
	}
	if last["audience"] != "system" {
		t.Errorf("half-open is a state, not a decision: %v", last["audience"])
	}
	if _, present := last["note"]; present {
		t.Error("an empty note must write no key at all")
	}
}

// L2, the half that only shows up one level down: pyjson used to hand
// containers to encoding/json, so every Python-compatibility rule stopped
// applying inside `context` — which is where a captain's-log row keeps all
// of its numbers. A whole float must keep its ".0" or json.loads parses a
// DIFFERENT TYPE on the Python side.
func TestNestedContextNumbersKeepPythonsSpelling(t *testing.T) {
	ws := t.TempDir()
	s := base("whole", "Whole")
	s.Tier, s.UseCount, s.UtilityScore = "provisional", AutoPromoteMinUses, 1.0
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	if _, err := MaybeAutoPromoteSkills(ws, 5, record.New(ws)); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(logPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	line := strings.TrimSpace(string(raw))
	if !strings.Contains(line, `"utility":1.0`) {
		t.Errorf("a whole float nested in context must keep its .0 "+
			"(json.dumps writes 1.0; the generic Go encoder writes 1):\n%s", line)
	}
	if !strings.Contains(line, `"use_count":5`) {
		t.Errorf("an int must NOT gain a .0:\n%s", line)
	}
	if strings.Contains(line, `\u003e`) {
		t.Errorf("the nested-container path must not HTML-escape either:\n%s", line)
	}
}

// L4. Python's round() rounds the exact value of the double; the obvious Go
// spelling — Round(f*1000)/1000 — rounds the PRODUCT, which carries its own
// representation error. Over the 400 three-decimal half-values 0.0005…
// 0.3995, 202 diverge. The symptom was a SKILL_DEMOTED row printing
// "utility_score=0.877 < 0.4" in its reason next to context.utility 0.878 —
// two spellings of one number in one event.
func TestRoundingMatchesPythonsRound(t *testing.T) {
	// Every want below was measured against CPython on this box.
	for _, c := range []struct {
		in          float64
		n           int
		want, naive float64
	}{
		{0.6675, 3, 0.667, 0.668},
		{0.8775, 3, 0.877, 0.878},
		{0.1235, 3, 0.123, 0.124},
		{2.675, 2, 2.67, 2.68},
		{0.66675, 4, 0.6667, 0.6667}, // agrees; kept so round4's digits are covered
	} {
		if got := pyRound(c.in, c.n); got != c.want {
			t.Errorf("pyRound(%v, %d) = %.17g, Python gives %.17g (a scaled "+
				"round gives %.17g)", c.in, c.n, got, c.want, c.naive)
		}
	}
	if got := round3(0.8775); got != 0.877 {
		t.Errorf("round3(0.8775) = %.17g", got)
	}
	if got := round4(0.66675); got != 0.6667 {
		t.Errorf("round4(0.66675) = %.17g", got)
	}
	// Go's own %.3f already agrees with Python's round, which is how the bug
	// showed itself: the printed reason and the stored context value
	// disagreed. They must not.
	trim := func(f float64) string {
		return strings.TrimSuffix(strings.TrimRight(
			strconv.FormatFloat(f, 'f', 3, 64), "0"), ".")
	}
	for _, v := range []float64{0.6675, 0.8775, 0.1235, 0.5, 0.343} {
		if printed, stored := trim(v), trim(round3(v)); printed != stored {
			t.Errorf("%v prints as %s and stores as %s", v, printed, stored)
		}
	}
	// Non-finite passes through rather than landing on the parse-failure
	// fallback by accident.
	if !math.IsNaN(pyRound(math.NaN(), 3)) || !math.IsInf(pyRound(math.Inf(-1), 3), -1) {
		t.Error("non-finite values must pass through unchanged")
	}
}
