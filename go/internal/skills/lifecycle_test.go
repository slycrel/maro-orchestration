package skills

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func statsLines(t *testing.T, ws string) []string {
	t.Helper()
	raw, err := os.ReadFile(skillStatsPath(ws))
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

func writeStats(t *testing.T, ws string, lines ...string) {
	t.Helper()
	p := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

// --- the store's hard-won properties ---

// ONE torn byte must not wipe the store. Python's pre-fix read was a strict
// whole-file decode swallowed by a bare except, so the map came back empty
// and the next counter bump rebuilt the file from nothing (probed live:
// 4 lines → 1).
func TestRecordSkillOutcomeTornByteDoesNotWipeTheStore(t *testing.T) {
	ws := t.TempDir()
	torn := "{\"skill_id\":\"torn\",\"skill_name\":\"\xff\"}"
	writeStats(t, ws,
		`{"skill_id":"a","skill_name":"A","total_uses":4,"successes":4}`,
		torn,
		`{"skill_id":"b","skill_name":"B","total_uses":2,"successes":1}`)

	if _, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1}); err != nil {
		t.Fatal(err)
	}
	lines := statsLines(t, ws)
	if len(lines) != 3 {
		t.Fatalf("every row must survive a counter bump: %+v", lines)
	}
	// The strandee rides FIRST and VERBATIM: at the tail, a same-id
	// stranded row would override the repaired one for a naive
	// last-row-wins parser.
	if lines[0] != torn {
		t.Fatalf("strandee must ride first, verbatim: %q", lines[0])
	}
	got, ok := GetSkillStats(ws, "a")
	if !ok || got.TotalUses != 5 || got.Successes != 5 {
		t.Fatalf("counter bump lost: %+v", got)
	}
	if b, ok := GetSkillStats(ws, "b"); !ok || b.TotalUses != 2 {
		t.Fatalf("bystander row destroyed: %+v", b)
	}
}

// A schema-drifted row must STRAND rather than ride the coercing
// constructor: Python's injection recorder flipped a stored "false" to
// JSON true on a routine bump because it does not recompute the field.
func TestRecordSkillOutcomeStrandsDriftedRowInsteadOfLaunderingIt(t *testing.T) {
	ws := t.TempDir()
	drifted := `{"skill_id":"d","needs_escalation":"false","total_uses":3}`
	writeStats(t, ws, drifted)
	if _, err := RecordSkillOutcome(ws, "other", true, OutcomeTelemetry{Confidence: 1}); err != nil {
		t.Fatal(err)
	}
	lines := statsLines(t, ws)
	if lines[0] != drifted {
		t.Fatalf("drifted row must be carried verbatim, got %q", lines[0])
	}
	if len(lines) != 2 {
		t.Fatalf("want strandee + the new row: %+v", lines)
	}
}

// A keyless row is one this store cannot represent: it strands rather than
// being silently dropped by the rewrite.
func TestRecordSkillOutcomeStrandsKeylessRows(t *testing.T) {
	ws := t.TempDir()
	for _, keyless := range []string{
		`{"skill_id":null,"total_uses":1}`,
		`{"skill_id":"","total_uses":1}`,
		`{"skill_id":1,"total_uses":1}`,
		`{"skill_id":true,"total_uses":1}`,
		`{"total_uses":1}`,
	} {
		ws2 := filepath.Join(ws, strings.NewReplacer("\"", "", ":", "", "{", "",
			"}", "", ",", "").Replace(keyless))
		if err := os.MkdirAll(ws2, 0o755); err != nil {
			t.Fatal(err)
		}
		writeStats(t, ws2, keyless)
		if _, err := RecordSkillOutcome(ws2, "x", true, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
		lines := statsLines(t, ws2)
		if len(lines) != 2 || lines[0] != keyless {
			t.Fatalf("%s: keyless row must strand verbatim, got %+v", keyless, lines)
		}
	}
}

// Fields the updater does not own must survive a counter bump — an
// operator's note was deleted by every routine update before Python's fix.
func TestRecordSkillOutcomeMergesOverStoredRow(t *testing.T) {
	ws := t.TempDir()
	writeStats(t, ws,
		`{"skill_id":"a","skill_name":"A","total_uses":1,"successes":1,"operator_note":"keep me"}`)
	if _, err := RecordSkillOutcome(ws, "a", false, OutcomeTelemetry{Confidence: 1}); err != nil {
		t.Fatal(err)
	}
	line := statsLines(t, ws)[0]
	if !strings.Contains(line, `"operator_note":"keep me"`) {
		t.Fatalf("unowned field deleted by a counter bump: %q", line)
	}
	if !strings.HasPrefix(line, `{"skill_id":"a","skill_name":"A","total_uses":2`) {
		t.Fatalf("modeled keys must lead, in model order: %q", line)
	}
}

// Evidence must arrive as evidence: refuse at the door, store untouched.
func TestRecordSkillOutcomeRefusesBadEvidenceWithoutTouchingStore(t *testing.T) {
	ws := t.TempDir()
	for _, c := range []struct {
		name string
		id   string
		tel  OutcomeTelemetry
	}{
		{"empty id", "", OutcomeTelemetry{Confidence: 1}},
		{"tainted id", "\xff", OutcomeTelemetry{Confidence: 1}},
		{"NaN cost", "a", OutcomeTelemetry{CostUSD: math.NaN(), Confidence: 1}},
		{"Inf latency", "a", OutcomeTelemetry{LatencyMS: math.Inf(1), Confidence: 1}},
		{"NaN confidence", "a", OutcomeTelemetry{Confidence: math.NaN()}},
	} {
		if _, err := RecordSkillOutcome(ws, c.id, true, c.tel); err == nil {
			t.Errorf("%s: must be refused", c.name)
		}
	}
	if _, err := os.Stat(skillStatsPath(ws)); err == nil {
		t.Fatal("a refused outcome must not create the store")
	}
}

// The whole transaction runs under the store lock: concurrent recorders
// must not lose an update (both read N, both write N+1).
func TestRecordSkillOutcomeConcurrentNoLostUpdate(t *testing.T) {
	ws := t.TempDir()
	const n = 12
	var wg sync.WaitGroup
	errs := make(chan error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1}); err != nil {
				errs <- err
			}
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	got, ok := GetSkillStats(ws, "a")
	if !ok || got.TotalUses != n {
		t.Fatalf("lost update: total_uses=%d want %d", got.TotalUses, n)
	}
}

func TestRecordSkillOutcomeComputesRateAndEscalation(t *testing.T) {
	ws := t.TempDir()
	for i := 0; i < 3; i++ {
		if _, err := RecordSkillOutcome(ws, "a", false, OutcomeTelemetry{Confidence: 1}); err != nil {
			t.Fatal(err)
		}
	}
	got, _ := GetSkillStats(ws, "a")
	if got.SuccessRate != 0 || !got.NeedsEscalation {
		t.Fatalf("0/3 must escalate: %+v", got)
	}
	if _, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1}); err != nil {
		t.Fatal(err)
	}
	got, _ = GetSkillStats(ws, "a")
	if got.SuccessRate != 0.25 || !got.NeedsEscalation {
		t.Fatalf("1/4 = 0.25 is still below 0.4: %+v", got)
	}
	if len(SkillsNeedingEscalation(ws)) != 1 {
		t.Fatal("escalation list must carry the skill")
	}
}

// The batch is ONE transaction: a per-id loop made a mid-list failure a
// reachable partial batch, and the retry then double-counted the prefix.
func TestRecordInjectionOutcomesIsOneTransaction(t *testing.T) {
	ws := t.TempDir()
	if _, err := RecordSkillInjectionOutcomes(ws, []string{"a", "b", "a"}, true); err != nil {
		t.Fatal(err)
	}
	a, _ := GetSkillStats(ws, "a")
	b, _ := GetSkillStats(ws, "b")
	if a.InjectedRuns != 1 || a.InjectedSuccesses != 1 || a.InjectedSuccessRate != 1 {
		t.Fatalf("duplicate id in one manifest must count once: %+v", a)
	}
	if b.InjectedRuns != 1 {
		t.Fatalf("b: %+v", b)
	}
	if _, err := RecordSkillInjectionOutcomes(ws, []string{"a"}, false); err != nil {
		t.Fatal(err)
	}
	a, _ = GetSkillStats(ws, "a")
	if a.InjectedRuns != 2 || a.InjectedSuccesses != 1 || a.InjectedSuccessRate != 0.5 {
		t.Fatalf("verdict counters: %+v", a)
	}
	// The legacy counters must be untouched by a verdict — they measure a
	// different (inflated) thing.
	if a.TotalUses != 0 {
		t.Fatalf("injection verdicts must not touch the legacy counters: %+v", a)
	}
}

// --- utility EMA + circuit breaker ---

// The expected values are Python's ACTUAL trace over the same sequence
// (run 2026-08-23), not Go-derived expectations.
func TestUpdateSkillUtilityMatchesPythonTrace(t *testing.T) {
	ws := t.TempDir()
	s := newSkill()
	s.ID, s.Name, s.Description = "a", "A", "d"
	s.CreatedAt = "2026-08-20T10:00:00+00:00"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	// Full-precision repr() from Python, not rounded: the EMA compounds, so
	// a rounded pin would hide an arithmetic-order divergence for several
	// steps before it grew past the tolerance. Compared EXACTLY — both
	// runtimes evaluate alpha*obs + (1-alpha)*u in float64, and any
	// difference is a real one.
	steps := []struct {
		success bool
		utility float64
		state   string
		cf, cs  int
	}{
		{false, 0.7, "closed", 1, 0},
		{false, 0.48999999999999994, "closed", 2, 0},
		{false, 0.3429999999999999, "open", 3, 0},
		{true, 0.5400999999999999, "half_open", 0, 1},
		{true, 0.67807, "closed", 0, 2},
		{false, 0.47464899999999993, "closed", 1, 0},
		{true, 0.6322542999999999, "closed", 0, 1},
		{true, 0.7425780099999999, "closed", 0, 2},
	}
	for i, step := range steps {
		u, err := UpdateSkillUtility(ws, "a", step.success, "")
		if err != nil {
			t.Fatal(err)
		}
		if u.UtilityAfter != step.utility {
			t.Fatalf("step %d utility %.17g, want %.17g", i, u.UtilityAfter, step.utility)
		}
		if u.CircuitAfter != step.state {
			t.Fatalf("step %d state %q, want %q", i, u.CircuitAfter, step.state)
		}
		if u.ConsecutiveFails != step.cf || u.ConsecutiveWins != step.cs {
			t.Fatalf("step %d counters cf=%d cs=%d, want %d/%d",
				i, u.ConsecutiveFails, u.ConsecutiveWins, step.cf, step.cs)
		}
	}
}

// A failure during probation trips the breaker again IMMEDIATELY — it does
// not wait for CircuitOpenThreshold, and the threshold branch must stay
// exclusive of the half-open one (skills.py:1671).
func TestUpdateSkillUtilityHalfOpenFailureTripsImmediately(t *testing.T) {
	ws := t.TempDir()
	s := newSkill()
	s.ID, s.Name, s.Description = "a", "A", "d"
	s.CreatedAt = "2026-08-20T10:00:00+00:00"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	for _, success := range []bool{false, false, false, true} {
		if _, err := UpdateSkillUtility(ws, "a", success, ""); err != nil {
			t.Fatal(err)
		}
	}
	u, err := UpdateSkillUtility(ws, "a", false, "")
	if err != nil {
		t.Fatal(err)
	}
	if u.CircuitBefore != "half_open" || u.CircuitAfter != "open" {
		t.Fatalf("one failure on probation must re-open: %s -> %s",
			u.CircuitBefore, u.CircuitAfter)
	}
	if u.ConsecutiveFails != 1 {
		t.Fatalf("re-open must not need the threshold: cf=%d", u.ConsecutiveFails)
	}
}

// The reported "before" must be the value from BEFORE the EMA — Python
// captures it after and logs before == after on every transition
// (backport candidate #14).
func TestUpdateSkillUtilityReportsTheRealPriorValue(t *testing.T) {
	ws := t.TempDir()
	s := newSkill()
	s.ID, s.Name, s.Description = "a", "A", "d"
	s.CreatedAt = "2026-08-20T10:00:00+00:00"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	u, err := UpdateSkillUtility(ws, "a", false, "")
	if err != nil {
		t.Fatal(err)
	}
	if u.UtilityBefore != 1.0 || u.UtilityAfter != 0.7 {
		t.Fatalf("before/after must differ: %v -> %v", u.UtilityBefore, u.UtilityAfter)
	}
}

func TestUpdateSkillUtilityKeepsFiveNewestFailureNotes(t *testing.T) {
	ws := t.TempDir()
	s := newSkill()
	s.ID, s.Name, s.Description = "a", "A", "d"
	s.CreatedAt = "2026-08-20T10:00:00+00:00"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	for _, note := range []string{"n1", "n2", "n3", "n4", "n5", "n6"} {
		if _, err := UpdateSkillUtility(ws, "a", false, note); err != nil {
			t.Fatal(err)
		}
	}
	got := LoadSkills(ws).Skills[0]
	if len(got.FailureNotes) != 5 || got.FailureNotes[0] != "n2" ||
		got.FailureNotes[4] != "n6" {
		t.Fatalf("newest five: %+v", got.FailureNotes)
	}
	// A long reason is clipped to 200 RUNES, not bytes.
	long := strings.Repeat("é", 300)
	if _, err := UpdateSkillUtility(ws, "a", false, long); err != nil {
		t.Fatal(err)
	}
	got = LoadSkills(ws).Skills[0]
	if n := len([]rune(got.FailureNotes[4])); n != 200 {
		t.Fatalf("clip must be by rune: %d", n)
	}
}

func TestUpdateSkillUtilityMissingSkillIsNoop(t *testing.T) {
	ws := t.TempDir()
	u, err := UpdateSkillUtility(ws, "nope", true, "")
	if err != nil || u.Found {
		t.Fatalf("missing skill must be a silent no-op: %+v %v", u, err)
	}
}

// --- A/B variants ---

// The bucket is the FULL sha1 digest mod pool size. The expectations are
// Python's actual values for these keys (run 2026-08-23): a truncated
// prefix would route differently and the arms would stop being comparable
// across runtimes.
func TestSelectVariantMatchesPythonRouting(t *testing.T) {
	parent := base("p", "Parent")
	c1 := base("c1", "Challenger 1")
	pid := "p"
	c1.VariantOf = &pid
	c2 := base("c2", "Challenger 2")
	c2.VariantOf = &pid

	pool2 := []Skill{parent, c1}
	pool3 := []Skill{parent, c1, c2}
	cases := []struct {
		key          string
		want2, want3 string
	}{
		{"abc12345", "c1", "c1"}, // python: pool2->1, pool3->1
		{"deadbeef", "p", "p"},   // python: pool2->0, pool3->0
		{"0000ffff", "c1", "p"},  // python: pool2->1, pool3->0
	}
	for _, c := range cases {
		if got := SelectVariantForTask(parent, c.key, pool2); got.ID != c.want2 {
			t.Errorf("%s pool2: got %s want %s", c.key, got.ID, c.want2)
		}
		if got := SelectVariantForTask(parent, c.key, pool3); got.ID != c.want3 {
			t.Errorf("%s pool3: got %s want %s", c.key, got.ID, c.want3)
		}
	}
	// No challengers → the parent, unchanged.
	if got := SelectVariantForTask(parent, "any", []Skill{parent}); got.ID != "p" {
		t.Fatalf("no variants must return the parent: %+v", got)
	}
}

func TestRecordVariantOutcomeOnlyCountsChallengers(t *testing.T) {
	ws := t.TempDir()
	parent := base("p", "Parent")
	if err := SaveSkill(ws, &parent); err != nil {
		t.Fatal(err)
	}
	child := base("c", "Challenger")
	pid := "p"
	child.VariantOf = &pid
	if err := SaveSkill(ws, &child); err != nil {
		t.Fatal(err)
	}
	if err := RecordVariantOutcome(ws, "c", true); err != nil {
		t.Fatal(err)
	}
	if err := RecordVariantOutcome(ws, "c", false); err != nil {
		t.Fatal(err)
	}
	if err := RecordVariantOutcome(ws, "p", true); err != nil { // no-op
		t.Fatal(err)
	}
	pool := LoadSkills(ws).Skills
	byID := map[string]Skill{}
	for _, s := range pool {
		byID[s.ID] = s
	}
	if byID["c"].VariantWins != 1 || byID["c"].VariantLosses != 1 {
		t.Fatalf("challenger counters: %+v", byID["c"])
	}
	if byID["p"].VariantWins != 0 || byID["p"].VariantLosses != 0 {
		t.Fatalf("a parent is not an arm: %+v", byID["p"])
	}
}

// The frontier gate reads the HONEST injected counters — use_count is
// legacy-frozen and sat at 0 for 312 of 314 live Python skills, silently
// starving this gate and the whole variant subsystem behind it.
func TestFrontierSkillsSelectsTheBandHardestFirst(t *testing.T) {
	ws := t.TempDir()
	// hot: legacy use_count only — the dead gate must not qualify it.
	hot := base("hot", "Hot")
	hot.UseCount = 99
	// solved: enough runs, but a perfect record — nothing to experiment on.
	solved := base("solved", "Solved")
	// mid / harder: inside the band, at different rates.
	mid := base("mid", "Mid")
	harder := base("harder", "Harder")
	// broken: inside the band by rate, but its circuit is open — that is
	// skills_needing_rewrite's job, not a variant experiment's.
	broken := base("broken", "Broken")
	broken.CircuitState = "open"
	for _, s := range []Skill{hot, solved, mid, harder, broken} {
		sk := s
		if err := SaveSkill(ws, &sk); err != nil {
			t.Fatal(err)
		}
	}
	if got := FrontierSkills(ws, LoadSkills(ws).Skills, 3); len(got) != 0 {
		t.Fatalf("use_count must not qualify: %+v", got)
	}
	// 4 runs each: solved 4/4 = 1.00 (above HIGH), mid 2/4 = 0.50,
	// harder 2/4 = 0.50 then dropped to 0.25 below — see the verdicts.
	verdicts := map[string][]bool{
		"solved": {true, true, true, true},
		"mid":    {true, true, false, false},  // 0.50
		"harder": {true, false, false, false}, // 0.25 — still >= LOW? no: 0.25 < 0.40
		"broken": {true, true, false, false},  // 0.50, but open circuit
	}
	for id, vs := range verdicts {
		for _, v := range vs {
			if _, err := RecordSkillInjectionOutcomes(ws, []string{id}, v); err != nil {
				t.Fatal(err)
			}
		}
	}
	got := FrontierSkills(ws, LoadSkills(ws).Skills, 3)
	if len(got) != 1 || got[0].ID != "mid" {
		var ids []string
		for _, s := range got {
			ids = append(ids, s.ID)
		}
		t.Fatalf("only the in-band, non-open skill qualifies: %v", ids)
	}

	// A challenger IS eligible: one that lands mid-band is exactly the thing
	// worth splitting again. Excluding it looked principled and diverged.
	child := base("child", "Challenger")
	pid := "mid"
	child.VariantOf = &pid
	if err := SaveSkill(ws, &child); err != nil {
		t.Fatal(err)
	}
	// 2/5 = 0.40, exactly at LOW (inclusive) and BELOW mid's 0.50.
	for _, v := range []bool{true, true, false, false, false} {
		if _, err := RecordSkillInjectionOutcomes(ws, []string{"child"}, v); err != nil {
			t.Fatal(err)
		}
	}
	got = FrontierSkills(ws, LoadSkills(ws).Skills, 3)
	if len(got) != 2 {
		t.Fatalf("a challenger is a frontier candidate: %+v", got)
	}
	// Hardest first: the evolver spends its budget in this order.
	if got[0].ID != "child" || got[1].ID != "mid" {
		t.Fatalf("must sort ascending by injected rate: %s, %s", got[0].ID, got[1].ID)
	}
}

func TestEfficiencyScoreNeedsThreeUses(t *testing.T) {
	s := SkillStats{TotalUses: 2, SuccessRate: 1.0}
	if s.EfficiencyScore() != 0 {
		t.Fatal("under three uses is not enough data")
	}
	s = SkillStats{TotalUses: 10, SuccessRate: 0.9, TotalCostUSD: 0.02}
	// cost_per_run = 0.002 → penalty 0.2 → 0.7
	if math.Abs(s.EfficiencyScore()-0.7) > 1e-9 {
		t.Fatalf("efficiency: %v", s.EfficiencyScore())
	}
	s = SkillStats{TotalUses: 3, SuccessRate: 0.4, TotalCostUSD: 3}
	// penalty capped at 0.5 → max(0, -0.1) = 0
	if s.EfficiencyScore() != 0 {
		t.Fatalf("floor at zero: %v", s.EfficiencyScore())
	}
}

// --- the read's and the write's announcements ---

// The read reports WHAT IT EXCLUDED, and the two exclusion reasons are
// distinct: a keyless row is one this store cannot key, not one it cannot
// parse. Collapsing them would file a legitimate-but-unkeyable row behind
// a corruption count.
func TestLoadSkillStatsCountsKeylessApartFromTainted(t *testing.T) {
	ws := t.TempDir()
	writeStats(t, ws,
		`{"skill_id":"","total_uses":1}`,
		`{"skill_id":null,"total_uses":1}`,
		"{\"skill_id\":\"torn\",\"skill_name\":\"\xff\"}",
		`{"skill_id":"a","total_uses":1}`,
		`{"skill_id":"a","total_uses":2}`)
	l := LoadSkillStats(ws)
	if l.Keyless != 2 {
		t.Errorf("an unkeyable row is keyless, not tainted: keyless=%d", l.Keyless)
	}
	if l.Tainted != 1 {
		t.Errorf("tainted=%d", l.Tainted)
	}
	if l.Compacted != 1 {
		t.Errorf("compacted=%d", l.Compacted)
	}
	if len(l.Stats) != 1 {
		t.Fatalf("stats=%+v", l.Stats)
	}
	w := l.Warnings(skillStatsPath(ws))
	if len(w) != 2 {
		t.Fatalf("read must announce both exclusions: %+v", w)
	}
	// A READ announces exclusion from THIS READ — never a rewrite it has
	// no knowledge of. Pure readers used to claim rows were "carried
	// through the rewrite" with no rewrite anywhere in the call.
	for _, s := range w {
		if strings.Contains(s, "rewrite") || strings.Contains(s, "compacted by") {
			t.Fatalf("a read must not claim a rewrite: %q", s)
		}
	}
}

// The carry-through claim belongs to the write that actually performed it,
// and only after its commit.
func TestRecordSkillOutcomeAnnouncesTheRewriteItPerformed(t *testing.T) {
	ws := t.TempDir()
	writeStats(t, ws,
		"{\"skill_id\":\"torn\",\"skill_name\":\"\xff\"}",
		`{"skill_id":"a","total_uses":1}`,
		`{"skill_id":"a","total_uses":2}`)
	warns, err := RecordSkillOutcome(ws, "a", true, OutcomeTelemetry{Confidence: 1})
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(warns, "\n")
	if !strings.Contains(joined, "1 stranded row(s) carried through the rewrite") {
		t.Errorf("write must announce the carry-through: %q", joined)
	}
	if !strings.Contains(joined, "1 older duplicate row(s) compacted by this rewrite") {
		t.Errorf("write must announce the compaction it performed: %q", joined)
	}
	// A clean store has nothing to announce.
	ws2 := t.TempDir()
	warns, err = RecordSkillOutcome(ws2, "a", true, OutcomeTelemetry{Confidence: 1})
	if err != nil || len(warns) != 0 {
		t.Fatalf("a clean rewrite is silent: %+v %v", warns, err)
	}
}

// Refused AT THE DOOR means before ANY mutation — not caught downstream by
// the serializer. With the door open, non-finite telemetry still fails the
// write, so the only observable difference is that the store directory got
// created on the way.
func TestRecordSkillOutcomeRefusesBeforeCreatingTheStoreDir(t *testing.T) {
	ws := t.TempDir()
	if _, err := RecordSkillOutcome(ws, "a",
		true, OutcomeTelemetry{CostUSD: math.NaN(), Confidence: 1}); err == nil {
		t.Fatal("NaN telemetry must be refused")
	}
	if _, err := os.Stat(filepath.Join(ws, "memory")); err == nil {
		t.Fatal("the door must refuse before touching the filesystem")
	}
}

// A rekeyed entry would silently write a row the next keyed read files
// under a different id than the caller updated — so the map key and the
// row's own skill_id must agree, and the refusal must land BEFORE the
// store is touched.
func TestWriteSkillStatsRefusesKeyDisagreement(t *testing.T) {
	ws := t.TempDir()
	path := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	r := statsRead{
		records: map[string]map[string]any{
			"a": {"skill_id": "b", "total_uses": 1},
		},
		order: []string{"a"},
	}
	err := writeSkillStats(path, r)
	if err == nil || !strings.Contains(err.Error(), "disagrees") {
		t.Fatalf("key disagreement must be refused: %v", err)
	}
	if _, err := os.Stat(path); err == nil {
		t.Fatal("a refused write must not create the store")
	}
}
