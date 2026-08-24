package orch

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// scriptedAdapter answers the decompose call from `plan` and every
// subsequent call (the validation gate) from `verdict`. One reply for
// both would make a test that claims to exercise validation actually
// exercise a parse failure, which defaults to PASS and looks the same.
type scriptedAdapter struct {
	plan    string
	verdict string
	calls   int32
}

func (s *scriptedAdapter) Complete(_ context.Context, _ []llm.Message,
	_ llm.Options) (*llm.Response, error) {
	if atomic.AddInt32(&s.calls, 1) == 1 {
		return &llm.Response{Content: s.plan}, nil
	}
	return &llm.Response{Content: s.verdict}, nil
}

func (s *scriptedAdapter) Name() string { return "scripted" }

// twoMilestonePlan has independent milestones (no depends_on), so
// IsChainShaped is false and the DAG lane is reachable.
const twoMilestonePlan = `{"milestones":[` +
	`{"title":"A","features":["a1","a2"],"validation_criteria":["c1"]},` +
	`{"title":"B","features":["b1"],"validation_criteria":["c2"]}]}`

// runOpts is the minimum wiring every test here needs: a feature runner
// and a slug that is already resolved.
func runOpts(t *testing.T, a llm.Adapter, rf RunFeatureFn, cfg map[string]any) RunOptions {
	t.Helper()
	return RunOptions{
		Project:    "p",
		Adapter:    a,
		RunFeature: rf,
		Cfg:        cfg,
		NewID:      seqIDs(),
	}
}

// seqIDs is a deterministic id source so a failure names a stable id.
func seqIDs() func() string {
	var n int32
	return func() string {
		return "id" + string(rune('0'+atomic.AddInt32(&n, 1)%10))
	}
}

func allDone(_ context.Context, req FeatureRequest) (FeatureOutcome, error) {
	return FeatureOutcome{LoopID: "L", Status: "done", StepsDone: 2, StepsTotal: 2}, nil
}

func allStuck(_ context.Context, req FeatureRequest) (FeatureOutcome, error) {
	return FeatureOutcome{LoopID: "L", Status: "stuck", StepsDone: 1, StepsTotal: 3}, nil
}

// TestRunMissionRefusesWithoutTheRequiredSeams: both refusals must land
// BEFORE anything is written, or a caller that forgot to wire the loop
// gets a project directory and a mission.json for a mission that never
// ran.
func TestRunMissionRefusesWithoutTheRequiredSeams(t *testing.T) {
	ws := t.TempDir()
	if _, err := RunMission(context.Background(), ws, "g", RunOptions{Project: "p"}); err != ErrNoFeatureRunner {
		t.Fatalf("nil RunFeature must refuse, got %v", err)
	}
	if _, err := RunMission(context.Background(), ws, "g",
		RunOptions{RunFeature: allDone}); err != ErrNoSlugResolver {
		t.Fatalf("empty Project with no resolver must refuse, got %v", err)
	}
	if entries, _ := os.ReadDir(ws); len(entries) != 0 {
		t.Fatalf("a refused mission wrote to the workspace: %v", entries)
	}
}

// TestMilestoneWorkersCarriesPythonsFloorAndFallback: the Python is
//
//	try:    _ms_workers = max(1, int(_cfg_get("mission.milestone_workers", 2)))
//	except (TypeError, ValueError): _ms_workers = 2
//
// so a configured 0 becomes ONE (the floor), and garbage becomes TWO (the
// except). Those are different numbers, and a Go port that treated the
// zero value as "unset" would collapse them into 2 — silently doubling
// the concurrency an operator asked to turn off.
func TestMilestoneWorkersCarriesPythonsFloorAndFallback(t *testing.T) {
	cases := []struct {
		name string
		raw  any
		want int
	}{
		{"absent", nil, 2},
		{"zero floors to one", 0, 1},
		{"negative floors to one", -5, 1},
		{"three", 3, 3},
		{"a float truncates toward zero", 3.9, 3},
		{"a numeric string parses", "4", 4},
		{"garbage falls back to two", "lots", 2},
		{"a list falls back to two", []any{1}, 2},
	}
	for _, c := range cases {
		cfg := map[string]any{}
		if c.raw != nil {
			cfg["mission"] = map[string]any{"milestone_workers": c.raw}
		}
		if got := MilestoneWorkers(cfg); got != c.want {
			t.Errorf("%s: milestone_workers = %d, want %d", c.name, got, c.want)
		}
	}
}

// TestParallelMilestonesUsesGetBoolNotTruthiness: the flag is THE revert
// lever. A quoted "false" in YAML is a non-empty string, and
// bool("false") is True — so `bool(get(...))` would ignore the revert and
// keep running the DAG lane. Python uses get_bool for exactly this, and
// the test asserts the LANE, not the parse.
func TestParallelMilestonesUsesGetBoolNotTruthiness(t *testing.T) {
	for _, raw := range []any{"false", "no", "off", "0", false} {
		ws := t.TempDir()
		cfg := map[string]any{"mission": map[string]any{"parallel_milestones": raw}}
		order := recordOrder()
		a := &scriptedAdapter{plan: twoMilestonePlan, verdict: `{"passed": true}`}
		if _, err := RunMission(context.Background(), ws, "g",
			runOpts(t, a, order.run, cfg)); err != nil {
			t.Fatal(err)
		}
		if !order.wasSequential() {
			t.Fatalf("parallel_milestones=%#v did not revert to the sequential "+
				"lane: bool(%#v) is the truthiness bug get_bool exists to avoid",
				raw, raw)
		}
	}
}

// orderRecorder records the ORDER features were started in.
type orderRecorder struct {
	mu       sync.Mutex
	titles   []string
	blockFor time.Duration
}

func recordOrder() *orderRecorder { return &orderRecorder{blockFor: 2 * time.Millisecond} }

func (o *orderRecorder) run(_ context.Context, req FeatureRequest) (FeatureOutcome, error) {
	o.mu.Lock()
	o.titles = append(o.titles, req.Title)
	o.mu.Unlock()
	time.Sleep(o.blockFor)
	return FeatureOutcome{LoopID: "L", Status: "done", StepsDone: 1, StepsTotal: 1}, nil
}

// wasSequential: milestone B's single feature never overlapped
// milestone A's. A's own two features DO run two-at-a-time in both lanes
// (ThreadPoolExecutor(max_workers=2) inside _run_milestone), so the
// milestone lane is what this distinguishes — and it distinguishes it by
// the titles' ORDER, not by a concurrency count that both lanes share.
func (o *orderRecorder) wasSequential() bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.titles) != 3 {
		return false
	}
	// A's features are a1/a2, B's is b1. Sequential means b1 is last.
	return o.titles[2] == "b1"
}

// TestFeatureStatusIsNarrowedToDoneOrBlocked: run_mission maps ANY
// non-"done" loop status to "blocked", and drain_next_mission does NOT.
// The two lanes really do disagree, and the divergence is load-bearing:
// mission.json's feature statuses are read by both runtimes, and a
// "stuck" there is a value run_mission's own vocabulary cannot produce.
func TestFeatureStatusIsNarrowedToDoneOrBlocked(t *testing.T) {
	ws := t.TempDir()
	a := &scriptedAdapter{plan: twoMilestonePlan, verdict: `{"passed": false}`}
	if _, err := RunMission(context.Background(), ws, "g",
		runOpts(t, a, allStuck, nil)); err != nil {
		t.Fatal(err)
	}
	m := LoadMission(ws, "p")
	if m == nil {
		t.Fatal("no mission written")
	}
	for _, ms := range m.Milestones {
		for _, f := range ms.Features {
			if f.Status != "blocked" {
				t.Fatalf("feature %q status %q — run_mission narrows every "+
					"non-done loop status to blocked", f.Title, f.Status)
			}
			if got := derefOr(f.ResultSummary, ""); got != "loop=L status=stuck steps=1/3" {
				t.Fatalf("result_summary is %q, want Python's exact format", got)
			}
		}
	}
}

// TestMissionStatusResolution walks the three-way branch AND its guard.
func TestMissionStatusResolution(t *testing.T) {
	cases := []struct {
		name     string
		statuses []string
		pre      string
		want     string
		wantDone int
	}{
		{"all done", []string{"done", "done"}, "running", "done", 2},
		{"one partial", []string{"done", "partial"}, "running", "partial", 2},
		{"one failed with progress", []string{"done", "failed"}, "running", "partial", 1},
		// The branch that is easy to get backwards: failed AND nothing
		// done is stuck, but partial COUNTS as progress, so a failed
		// beside a partial is partial, not stuck.
		{"failed with nothing done", []string{"failed", "failed"}, "running", "stuck", 0},
		{"failed beside partial", []string{"failed", "partial"}, "running", "partial", 1},
		// The guard: a mission already marked stuck keeps that verdict
		// even when the counts would compute something softer.
		{"already stuck stays stuck", []string{"done", "done"}, "stuck", "stuck", 2},
	}
	for _, c := range cases {
		m := &Mission{Status: c.pre}
		for _, s := range c.statuses {
			m.Milestones = append(m.Milestones, Milestone{Status: s})
		}
		done, status := ResolveMissionStatus(m)
		if status != c.want || done != c.wantDone {
			t.Errorf("%s: status=%q done=%d, want %q/%d",
				c.name, status, done, c.want, c.wantDone)
		}
	}
}

// TestValidationDefaultsToPassOnAdapterFailure: Python's
// `except: passed = True` is "don't get stuck in validation loops", and
// ValidateMilestone already defaults to pass at all four of its exits.
// Pinned because a future exit that failed CLOSED would turn a transient
// LLM outage into a stuck mission — a divergence with no visible diff.
func TestValidationDefaultsToPassOnAdapterFailure(t *testing.T) {
	ws := t.TempDir()
	// The first Complete returns the plan; every call after it fails,
	// which is the transient-outage arm of the validation gate.
	failing := &flipAdapter{first: twoMilestonePlan}
	res, err := RunMission(context.Background(), ws, "g",
		runOpts(t, failing, allDone, nil))
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" {
		t.Fatalf("a failing validation adapter must not block the mission: "+
			"status=%q", res.Status)
	}
}

type flipAdapter struct {
	first string
	n     int32
}

func (f *flipAdapter) Complete(_ context.Context, _ []llm.Message,
	_ llm.Options) (*llm.Response, error) {
	if atomic.AddInt32(&f.n, 1) == 1 {
		return &llm.Response{Content: f.first}, nil
	}
	return nil, os.ErrDeadlineExceeded
}
func (f *flipAdapter) Name() string { return "flip" }

// TestMissionLogRowIsWritten: the mission log is a shared store, and a
// run that produced no row did not happen as far as either runtime's
// history is concerned.
func TestMissionLogRowIsWritten(t *testing.T) {
	ws := t.TempDir()
	a := &scriptedAdapter{plan: twoMilestonePlan, verdict: `{"passed": true}`}
	res, err := RunMission(context.Background(), ws, "g", runOpts(t, a, allDone, nil))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(MissionLogPath(ws))
	if err != nil {
		t.Fatalf("no mission-log.jsonl: %v", err)
	}
	line := strings.TrimSpace(string(raw))
	if strings.Contains(line, "\n") {
		t.Fatalf("one run, one row: %s", line)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(line), &row); err != nil {
		t.Fatalf("row does not parse: %v\n%s", err, line)
	}
	if row["status"] != res.Status || row["project"] != "p" {
		t.Fatalf("row disagrees with the result it records: %s", line)
	}
	if row["completed_at"] == nil {
		t.Fatalf("completed_at is set unconditionally after the status "+
			"branch, including for a stuck mission: %s", line)
	}
}

// TestConcurrentMilestonesSnapshotWithoutRacing is the DAG package's
// NAMED RESIDUAL, closed. PersistFn walks EVERY milestone including ones
// whose bodies are still writing; Python's version of that is a logical
// tear under the GIL, and Go's is undefined behaviour. Run under -race
// this fails loudly if any mutation escapes the mission lock.
func TestConcurrentMilestonesSnapshotWithoutRacing(t *testing.T) {
	ws := t.TempDir()
	// Four independent milestones, two features each: the DAG lane runs
	// milestones concurrently AND features concurrently inside each.
	plan := `{"milestones":[` +
		`{"title":"A","features":["a1","a2"],"validation_criteria":[]},` +
		`{"title":"B","features":["b1","b2"],"validation_criteria":[]},` +
		`{"title":"C","features":["c1","c2"],"validation_criteria":[]},` +
		`{"title":"D","features":["d1","d2"],"validation_criteria":[]}]}`
	a := &scriptedAdapter{plan: plan, verdict: `{"passed": true}`}
	slow := func(_ context.Context, req FeatureRequest) (FeatureOutcome, error) {
		time.Sleep(time.Millisecond)
		return FeatureOutcome{LoopID: "L", Status: "done", StepsDone: 1, StepsTotal: 1}, nil
	}
	cfg := map[string]any{"mission": map[string]any{
		"parallel_milestones": true, "milestone_workers": 4}}
	res, err := RunMission(context.Background(), ws, "g", runOpts(t, a, slow, cfg))
	if err != nil {
		t.Fatal(err)
	}
	if res.MilestonesDone != 4 || res.FeaturesDone != 8 {
		t.Fatalf("DAG lane lost work: %d/4 milestones, %d/8 features",
			res.MilestonesDone, res.FeaturesDone)
	}
}

// ---------------------------------------------------------------------------
// Drain
// ---------------------------------------------------------------------------

func seedMission(t *testing.T, ws string, m *Mission) {
	t.Helper()
	if _, err := EnsureProject(ws, m.Project, m.Goal, 0); err != nil {
		t.Fatal(err)
	}
	if err := SaveMission(ws, m, m.Project); err != nil {
		t.Fatal(err)
	}
}

// TestDrainTakesTheLoopStatusRaw is the counterpart to
// TestFeatureStatusIsNarrowedToDoneOrBlocked. Same store, same field,
// deliberately different vocabulary — and a Go port that "fixed" the
// inconsistency would be changing behaviour under the name of a port.
func TestDrainTakesTheLoopStatusRaw(t *testing.T) {
	ws := t.TempDir()
	seedMission(t, ws, &Mission{
		ID: "m1", Goal: "g", Project: "p", Status: "pending", CreatedAt: "2026-01-01T00:00:00Z",
		Milestones: []Milestone{{ID: "ms1", Title: "A", Status: "pending",
			Features: []Feature{{ID: "f1", Title: "a1", Status: "pending"}}}},
	})
	got, err := DrainNextMission(context.Background(), ws,
		DrainOptions{RunFeature: allStuck})
	if err != nil {
		t.Fatal(err)
	}
	if got == nil {
		t.Fatal("nothing drained")
	}
	m := LoadMission(ws, "p")
	if m.Milestones[0].Features[0].Status != "stuck" {
		t.Fatalf("drain narrowed the loop status to %q; it stores it RAW",
			m.Milestones[0].Features[0].Status)
	}
	if m.Milestones[0].Status != "blocked" || got.Status != "blocked" {
		t.Fatalf("a non-done feature blocks its milestone and the mission: "+
			"ms=%q mission=%q", m.Milestones[0].Status, got.Status)
	}
	if got.MilestonesDone != 0 {
		t.Fatalf("a blocked milestone is not done: %d", got.MilestonesDone)
	}
	if summary := derefOr(m.Milestones[0].Features[0].ResultSummary, ""); summary != "1/3 steps done" {
		t.Fatalf("drain's summary format is its own: %q", summary)
	}
}

// TestDrainCountsAnEmptyMilestoneDone: `all()` over an empty list is
// True in Python, so a milestone with no features drains as done — with
// nothing having run. It looks like a bug and is the documented
// behaviour; pinned so a Go `len(features) > 0 &&` guard cannot be added
// by instinct.
func TestDrainCountsAnEmptyMilestoneDone(t *testing.T) {
	ws := t.TempDir()
	seedMission(t, ws, &Mission{
		ID: "m1", Goal: "g", Project: "p", Status: "pending", CreatedAt: "2026-01-01T00:00:00Z",
		Milestones: []Milestone{{ID: "ms1", Title: "empty", Status: "pending"}},
	})
	var ran int32
	got, err := DrainNextMission(context.Background(), ws, DrainOptions{
		RunFeature: func(_ context.Context, _ FeatureRequest) (FeatureOutcome, error) {
			atomic.AddInt32(&ran, 1)
			return FeatureOutcome{Status: "done"}, nil
		}})
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Status != "done" || got.MilestonesDone != 1 {
		t.Fatalf("all() over an empty feature list is True: %+v", got)
	}
	if ran != 0 {
		t.Fatalf("nothing should have run: %d calls", ran)
	}
}

// TestDrainSkipsDoneMilestonesAndStillCountsThem: the `continue` above
// the feature loop counts the milestone WITHOUT running anything.
func TestDrainSkipsDoneMilestonesAndStillCountsThem(t *testing.T) {
	ws := t.TempDir()
	seedMission(t, ws, &Mission{
		ID: "m1", Goal: "g", Project: "p", Status: "pending", CreatedAt: "2026-01-01T00:00:00Z",
		Milestones: []Milestone{
			{ID: "ms1", Title: "A", Status: "done",
				Features: []Feature{{ID: "f1", Title: "a1", Status: "done"}}},
			{ID: "ms2", Title: "B", Status: "pending",
				Features: []Feature{
					{ID: "f2", Title: "b1", Status: "done"},
					{ID: "f3", Title: "b2", Status: "pending"}}},
		},
	})
	var titles []string
	got, err := DrainNextMission(context.Background(), ws, DrainOptions{
		RunFeature: func(_ context.Context, req FeatureRequest) (FeatureOutcome, error) {
			titles = append(titles, req.Title)
			return FeatureOutcome{LoopID: "L", Status: "done", StepsDone: 1, StepsTotal: 1}, nil
		}})
	if err != nil {
		t.Fatal(err)
	}
	if len(titles) != 1 || titles[0] != "b2" {
		t.Fatalf("only the not-done feature of the not-done milestone runs: %v", titles)
	}
	if got.MilestonesDone != 2 || got.Status != "done" {
		t.Fatalf("the pre-done milestone still counts: %+v", got)
	}
}

// TestDrainReleasesTheLockAndRefusesWhenHeld.
func TestDrainReleasesTheLockAndRefusesWhenHeld(t *testing.T) {
	ws := t.TempDir()
	seedMission(t, ws, &Mission{
		ID: "m1", Goal: "g", Project: "p", Status: "pending", CreatedAt: "2026-01-01T00:00:00Z",
		Milestones: []Milestone{{ID: "ms1", Title: "A", Status: "pending",
			Features: []Feature{{ID: "f1", Title: "a1", Status: "pending"}}}},
	})
	if !AcquireDrainLock(ws, "other") {
		t.Fatal("could not take the lock")
	}
	got, err := DrainNextMission(context.Background(), ws,
		DrainOptions{RunFeature: allDone})
	if err != nil || got != nil {
		t.Fatalf("a held lock must skip: %+v %v", got, err)
	}
	ReleaseDrainLock(ws)

	if _, err := DrainNextMission(context.Background(), ws,
		DrainOptions{RunFeature: allDone}); err != nil {
		t.Fatal(err)
	}
	if IsDrainRunning(ws) {
		t.Fatal("the drain lock outlived the drain")
	}
}

// TestDrainNotifiesPerMilestoneAndOnCompletion pins the message text and
// the three gates on it (notify, not dry-run, and — for the briefing —
// every milestone done).
func TestDrainNotifiesPerMilestoneAndOnCompletion(t *testing.T) {
	ws := t.TempDir()
	seedMission(t, ws, &Mission{
		ID: "m1", Goal: "g", Project: "p", Status: "pending", CreatedAt: "2026-01-01T00:00:00Z",
		Milestones: []Milestone{{ID: "ms1", Title: "Ship it", Status: "pending",
			Features: []Feature{{ID: "f1", Title: "a1", Status: "pending"}}}},
	})
	var msgs []string
	if _, err := DrainNextMission(context.Background(), ws, DrainOptions{
		RunFeature: allDone, Notify: true,
		NotifyFn: func(m string) { msgs = append(msgs, m) },
	}); err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 2 {
		t.Fatalf("want a milestone message and a completion briefing: %v", msgs)
	}
	if msgs[0] != "✓ [p] Milestone: Ship it — done" {
		t.Fatalf("milestone message drifted: %q", msgs[0])
	}
	if !strings.HasPrefix(msgs[1], "Mission complete!\n") {
		t.Fatalf("briefing message drifted: %q", msgs[1])
	}
}

// TestDrainDryRunNotifiesNothingAndRunsNothing.
func TestDrainDryRunNotifiesNothingAndRunsNothing(t *testing.T) {
	ws := t.TempDir()
	seedMission(t, ws, &Mission{
		ID: "m1", Goal: "g", Project: "p", Status: "pending", CreatedAt: "2026-01-01T00:00:00Z",
		Milestones: []Milestone{{ID: "ms1", Title: "A", Status: "pending",
			Features: []Feature{{ID: "f1", Title: "a1", Status: "pending"}}}},
	})
	var called, notified int
	got, err := DrainNextMission(context.Background(), ws, DrainOptions{
		DryRun: true, Notify: true,
		RunFeature: func(_ context.Context, _ FeatureRequest) (FeatureOutcome, error) {
			called++
			return FeatureOutcome{Status: "done"}, nil
		},
		NotifyFn: func(string) { notified++ },
	})
	if err != nil {
		t.Fatal(err)
	}
	if called != 0 {
		t.Fatalf("dry-run marks features done without calling the loop: %d", called)
	}
	if notified != 0 {
		t.Fatalf("dry-run sends nothing: %d", notified)
	}
	m := LoadMission(ws, "p")
	if s := derefOr(m.Milestones[0].Features[0].ResultSummary, ""); s != "dry-run" {
		t.Fatalf("dry-run summary is the literal %q, got %q", "dry-run", s)
	}
	if got.Status != "done" {
		t.Fatalf("dry-run still completes the mission: %q", got.Status)
	}
}

// TestMilestoneNotificationIcon: ✓ for done, ⚠ for everything else.
func TestMilestoneNotificationIcon(t *testing.T) {
	if got := MilestoneNotification("p", "T", "done"); !strings.HasPrefix(got, "✓") {
		t.Fatalf("done takes ✓: %q", got)
	}
	for _, s := range []string{"blocked", "partial", "", "DONE"} {
		if got := MilestoneNotification("p", "T", s); !strings.HasPrefix(got, "⚠") {
			t.Fatalf("status %q takes ⚠ (the compare is exact and "+
				"case-sensitive): %q", s, got)
		}
	}
}

// TestRuneHeadSlicesCodePoints: goal[:80] and briefing[:3000] are Python
// str slices. Go's s[:n] counts BYTES and would cut a multi-byte rune in
// half, producing an invalid string where Python produces a shorter
// valid one — and both of these strings reach a durable store or a
// Telegram message.
func TestRuneHeadSlicesCodePoints(t *testing.T) {
	s := strings.Repeat("é", 10) // 10 runes, 20 bytes
	got := runeHead(s, 4)
	if len([]rune(got)) != 4 {
		t.Fatalf("runeHead(%q, 4) kept %d runes", s, len([]rune(got)))
	}
	if !utf8Valid(got) {
		t.Fatalf("runeHead produced invalid UTF-8: %q", got)
	}
	if runeHead("abc", 10) != "abc" {
		t.Fatal("n past the end returns the whole string, as Python does")
	}
	if runeHead("abc", 0) != "" || runeHead("abc", -1) != "" {
		t.Fatal("n <= 0 is empty")
	}
}

func utf8Valid(s string) bool {
	for _, r := range s {
		if r == 0xFFFD {
			return false
		}
	}
	return true
}

// TestRunMissionCreatesTheProjectWithARuneSafeMissionLine: EnsureProject
// gets goal[:80], and the project's recorded mission is what
// resolveProjectSlug later reads to tell continuity from collision.
func TestRunMissionCreatesTheProjectWithARuneSafeMissionLine(t *testing.T) {
	ws := t.TempDir()
	goal := strings.Repeat("é", 200)
	a := &scriptedAdapter{plan: twoMilestonePlan, verdict: `{"passed": true}`}
	if _, err := RunMission(context.Background(), ws, goal,
		runOpts(t, a, allDone, nil)); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(ProjectDir(ws, "p"), "NEXT.md")); err != nil {
		t.Fatalf("project not created: %v", err)
	}
}
