package loopfinalize

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The probe drives the REAL _build_result_and_finalize with every stateful
// surface it reaches replaced by a recorder, and the recording is the
// point: this phase's job is almost entirely WHAT it calls, IN WHAT ORDER,
// WITH WHAT ARGUMENTS, and WHICH failures it swallows. A test that checked
// only the returned LoopResult would pass with the manifest written before
// the loop log, with the runs index never forced, and with a merge-back
// exception silently reported as a clean 'done'.
//
// The four calls Python does NOT wrap in a try are asserted the same way as
// the rest — by driving them into failure and comparing what comes back out
// of the function. Three of them abort it.
//
// The probe writes to its own temp tree and the Go side writes to another,
// and both records carry the resulting file tree keyed by relative path. So
// the transcript and the scratchpad dump are compared as BYTES, produced by
// each runtime's own path joining and mkdir semantics, not as two renderings
// compared in memory.

type step struct {
	Index  int    `json:"index"`
	Text   string `json:"text"`
	Status string `json:"status"`
	Result string `json:"result,omitempty"`
}

type pfSpec struct {
	Scope      string   `json:"scope"`
	Milestones []int    `json:"milestones,omitempty"`
	Flags      []string `json:"flags,omitempty"`
}

type ctxSpec struct {
	LoopID           string     `json:"loop_id"`
	Project          string     `json:"project"`
	Goal             string     `json:"goal"`
	DryRun           bool       `json:"dry_run,omitempty"`
	Verbose          bool       `json:"verbose,omitempty"`
	Injections       []string   `json:"injections,omitempty"`
	StopVerdict      string     `json:"stop_verdict,omitempty"`
	StopEvidence     string     `json:"stop_evidence,omitempty"`
	PauseReason      string     `json:"pause_reason,omitempty"`
	MeasurementClass string     `json:"measurement_class,omitempty"`
	HandleID         string     `json:"handle_id,omitempty"`
	DeferLearning    bool       `json:"defer_learning,omitempty"`
	DeferMaint       bool       `json:"defer_maintenance,omitempty"`
	StartedAt        float64    `json:"started_at,omitempty"`
	HasSlot          bool       `json:"has_slot,omitempty"`
	HasLease         bool       `json:"has_lease,omitempty"`
	ContainerClone   *cloneSpec `json:"container_clone,omitempty"`
	RunWorktree      *wtSpec    `json:"run_worktree,omitempty"`
}

type cloneSpec struct {
	Path   string `json:"path"`
	Branch string `json:"branch"`
}

type wtSpec struct {
	RepoDir string `json:"repo_dir"`
}

type inSpec struct {
	Steps           []step   `json:"steps,omitempty"`
	LoopStatus      string   `json:"loop_status"`
	StuckReason     *string  `json:"stuck_reason,omitempty"`
	TokensIn        int      `json:"tokens_in,omitempty"`
	TokensOut       int      `json:"tokens_out,omitempty"`
	Interrupts      int      `json:"interrupts,omitempty"`
	March           bool     `json:"march,omitempty"`
	PF              *pfSpec  `json:"pf,omitempty"`
	ManifestSteps   []string `json:"manifest_steps,omitempty"`
	Replan          int      `json:"replan,omitempty"`
	StartTS         string   `json:"start_ts,omitempty"`
	MilestoneExpand int      `json:"milestone_expanded,omitempty"`
	HadNoSkill      bool     `json:"had_no_skill,omitempty"`
	FailureChain    []string `json:"failure_chain,omitempty"`
	Recovery        int      `json:"recovery,omitempty"`
	// Scratchpad is an ORDERED list, not a map: index.json lists
	// list(scratchpad.keys()) in insertion order, and a Go map marshals
	// SORTED — which would hand the probe an order the Go side then
	// reproduces by sorting too, so the two would agree without either
	// one preserving anything.
	Scratchpad []kv `json:"scratchpad,omitempty"`
}

type kv struct {
	K string `json:"k"`
	V any    `json:"v"`
}

type mergeSpec struct {
	Ok     bool   `json:"ok"`
	Detail string `json:"detail,omitempty"`
	Branch string `json:"branch,omitempty"`
	Raise  string `json:"raise,omitempty"`
}

type behSpec struct {
	Monotonic         float64    `json:"monotonic,omitempty"`
	Now               string     `json:"now,omitempty"`
	LogPath           string     `json:"log_path,omitempty"`
	ManifestRaise     string     `json:"manifest_raise,omitempty"`
	ReportRaise       string     `json:"report_raise,omitempty"`
	LogLogRaise       string     `json:"loglog_raise,omitempty"`
	RunsIndexRaise    string     `json:"runsindex_raise,omitempty"`
	AppendDecRaise    string     `json:"appenddec_raise,omitempty"`
	OpStatusRaise     string     `json:"opstatus_raise,omitempty"`
	MemDirRaise       string     `json:"memdir_raise,omitempty"`
	LockedAppendRaise string     `json:"lockedappend_raise,omitempty"`
	WriteEventRaise   string     `json:"writeevent_raise,omitempty"`
	ArtifactDirRaise  string     `json:"artifactdir_raise,omitempty"`
	LandFacts         []int      `json:"landfacts,omitempty"`
	LandFactsRaise    string     `json:"landfacts_raise,omitempty"`
	FinalizeRaise     string     `json:"finalize_raise,omitempty"`
	CloneMerge        *mergeSpec `json:"clone_merge,omitempty"`
	CloneCleanupRaise string     `json:"clone_cleanup_raise,omitempty"`
	WTMerge           *mergeSpec `json:"wt_merge,omitempty"`
	WTCleanupRaise    string     `json:"wt_cleanup_raise,omitempty"`
	WTPruneRaise      string     `json:"wt_prune_raise,omitempty"`
	StampOutcomeRaise string     `json:"stampoutcome_raise,omitempty"`
	StampRunRaise     string     `json:"stamprun_raise,omitempty"`
	SlotRaise         string     `json:"slot_raise,omitempty"`
	LeaseRaise        string     `json:"lease_raise,omitempty"`
	ClearRaise        string     `json:"clear_raise,omitempty"`
	HBRaise           string     `json:"hb_raise,omitempty"`
}

type scenario struct {
	Name string   `json:"name"`
	Ctx  ctxSpec  `json:"ctx"`
	In   inSpec   `json:"in"`
	Beh  *behSpec `json:"beh,omitempty"`
}

func ptrS(s string) *string { return &s }

func baseCtx() ctxSpec {
	return ctxSpec{LoopID: "L1", Project: "proj", Goal: "ship the thing",
		StartedAt: 2.5}
}

func baseIn() inSpec {
	return inSpec{
		Steps: []step{
			{Index: 7, Text: "read the file", Status: "done", Result: "read it"},
			{Index: 8, Text: "write the file", Status: "blocked", Result: "no perms"},
		},
		LoopStatus:    "done",
		TokensIn:      100,
		TokensOut:     250,
		ManifestSteps: []string{"read the file", "write the file"},
		StartTS:       "2026-08-27T00:00:00Z",
	}
}

func scenarios() []scenario {
	longResult := strings.Repeat("x", 2500)
	uniResult := strings.Repeat("é", 2500)
	sc := []scenario{}

	add := func(name string, mut func(*scenario)) {
		s := scenario{Name: name, Ctx: baseCtx(), In: baseIn(), Beh: &behSpec{}}
		mut(&s)
		sc = append(sc, s)
	}

	add("happy-done", func(s *scenario) {})
	add("stuck-with-reason", func(s *scenario) {
		s.In.LoopStatus = "stuck"
		s.In.StuckReason = ptrS("ran out of ideas")
	})
	add("no-done-steps-writes-no-transcript", func(s *scenario) {
		s.In.LoopStatus = "stuck"
		s.In.Steps = []step{{Index: 1, Text: "t", Status: "blocked", Result: "r"}}
	})
	add("no-steps-at-all", func(s *scenario) {
		s.In.Steps = nil
		s.In.ManifestSteps = nil
	})
	add("no-project-skips-manifest", func(s *scenario) { s.Ctx.Project = "" })
	add("no-manifest-steps-skips-manifest", func(s *scenario) {
		s.In.ManifestSteps = nil
	})
	add("empty-result-step-body", func(s *scenario) {
		s.In.Steps = []step{{Index: 3, Text: "t", Status: "done"}}
	})
	add("result-exactly-at-cap", func(s *scenario) {
		s.In.Steps = []step{{Index: 1, Text: "t", Status: "done",
			Result: strings.Repeat("y", 2000)}}
	})
	add("result-over-cap", func(s *scenario) {
		s.In.Steps = []step{{Index: 1, Text: "t", Status: "done", Result: longResult}}
	})
	add("result-over-cap-multibyte", func(s *scenario) {
		s.In.Steps = []step{{Index: 1, Text: "t", Status: "done", Result: uniResult}}
	})
	add("step-text-over-head", func(s *scenario) {
		s.In.Steps = []step{{Index: 1, Text: strings.Repeat("é", 140),
			Status: "done", Result: "r"}}
	})
	add("verbose-prints-summary", func(s *scenario) { s.Ctx.Verbose = true })
	add("dry-run-skips-cleanup-and-calibration", func(s *scenario) {
		s.Ctx.DryRun = true
		s.In.PF = &pfSpec{Scope: "wide", Flags: []string{"a"}}
	})
	add("calibration-true-positive", func(s *scenario) {
		s.In.LoopStatus = "stuck"
		s.In.PF = &pfSpec{Scope: "deep", Milestones: []int{1, 2},
			Flags: []string{"scope", "risk", "scope"}}
		s.In.MilestoneExpand = 1
	})
	add("calibration-false-positive", func(s *scenario) {
		s.In.PF = &pfSpec{Scope: "wide", Flags: []string{"b"}}
	})
	add("calibration-false-negative", func(s *scenario) {
		s.In.LoopStatus = "stuck"
		s.In.PF = &pfSpec{Scope: "narrow"}
	})
	add("calibration-true-negative-no-flags", func(s *scenario) {
		s.In.PF = &pfSpec{Scope: "narrow"}
	})
	// A status that is neither "done" nor "stuck". The calibration row's
	// actual_stuck is `== "stuck"`, and the plausible misreading is
	// `!= "done"` — which agrees on every two-valued fixture.
	add("calibration-paused-is-not-stuck", func(s *scenario) {
		s.In.LoopStatus = "paused"
		s.Ctx.PauseReason = "waiting on user"
		s.In.PF = &pfSpec{Scope: "wide", Flags: []string{"a"}}
	})
	add("calibration-partial-is-not-stuck", func(s *scenario) {
		s.In.LoopStatus = "partial"
		s.In.PF = &pfSpec{Scope: "narrow"}
	})
	// stuck_reason is an OPTIONAL string, so "present but empty" is a
	// distinct state from absent, and Python's `if stuck_reason:` treats
	// them the same. A nil-check port agrees everywhere else.
	add("empty-stuck-reason-is-falsy", func(s *scenario) {
		s.In.LoopStatus = "stuck"
		s.In.StuckReason = ptrS("")
	})
	add("calibration-memdir-fails", func(s *scenario) {
		s.In.PF = &pfSpec{Scope: "wide"}
		s.Beh.MemDirRaise = "no memory dir"
	})
	add("scratchpad-written", func(s *scenario) {
		s.In.Scratchpad = []kv{{"notes", map[string]any{"a": 1}}}
	})
	// Keys in an order that is neither alphabetical nor reverse, so a
	// port that sorts either way is visible in index.json.
	add("scratchpad-keeps-insertion-order", func(s *scenario) {
		s.In.Scratchpad = []kv{
			{"mid", []any{1, 2}},
			{"alpha", "text"},
			{"zed", map[string]any{"nested": []any{"a", 1.5, nil, true}}},
			{"beta", nil},
		}
	})
	add("manifest-write-raises", func(s *scenario) {
		s.Beh.ManifestRaise = "manifest exploded"
	})
	add("report-write-raises", func(s *scenario) { s.Beh.ReportRaise = "report exploded" })
	add("loop-log-raises-aborts", func(s *scenario) { s.Beh.LogLogRaise = "log exploded" })
	add("runs-index-raises-is-swallowed", func(s *scenario) {
		s.Beh.RunsIndexRaise = "index exploded"
	})
	add("append-decision-raises-aborts", func(s *scenario) {
		s.Beh.AppendDecRaise = "decision exploded"
	})
	add("operator-status-raises-aborts", func(s *scenario) {
		s.Beh.OpStatusRaise = "status exploded"
	})
	add("observe-event-raises-is-swallowed", func(s *scenario) {
		s.Beh.WriteEventRaise = "observe exploded"
	})
	add("artifact-dir-raises-falls-back", func(s *scenario) {
		s.Beh.ArtifactDirRaise = "no artifact dir"
	})
	add("land-facts-counts", func(s *scenario) { s.Beh.LandFacts = []int{2, 1} })
	add("land-facts-zero-is-silent", func(s *scenario) { s.Beh.LandFacts = []int{0, 0} })
	add("land-facts-raises", func(s *scenario) { s.Beh.LandFactsRaise = "facts exploded" })
	add("finalize-loop-raises-aborts", func(s *scenario) {
		s.Beh.FinalizeRaise = "finalize exploded"
	})
	add("clone-merge-ok", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Beh.CloneMerge = &mergeSpec{Ok: true}
	})
	add("clone-merge-fails-downgrades", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Beh.CloneMerge = &mergeSpec{Ok: false, Detail: "conflict in a.py"}
	})
	add("clone-merge-fails-keeps-existing-verdict", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Ctx.StopVerdict = "goal-met"
		s.Ctx.StopEvidence = "all steps done"
		s.Beh.CloneMerge = &mergeSpec{Ok: false, Detail: "conflict"}
	})
	add("clone-merge-fails-appends-to-stuck-reason", func(s *scenario) {
		s.In.LoopStatus = "stuck"
		s.In.StuckReason = ptrS("step 2 blocked")
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Beh.CloneMerge = &mergeSpec{Ok: false, Detail: "conflict"}
	})
	add("clone-merge-raises", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/scratch/clone", Branch: "wip"}
		s.Beh.CloneMerge = &mergeSpec{Raise: "git blew up"}
	})
	add("clone-cleanup-raises", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/scratch/clone", Branch: "wip"}
		s.Beh.CloneMerge = &mergeSpec{Ok: true}
		s.Beh.CloneCleanupRaise = "cleanup blew up"
	})
	add("clone-evidence-clipped-at-800", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Beh.CloneMerge = &mergeSpec{Ok: false, Detail: strings.Repeat("d", 900)}
	})
	add("worktree-merge-ok", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Ok: true}
	})
	add("worktree-merge-fails-downgrades", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Ok: false, Detail: "conflict", Branch: "run/L1"}
	})
	add("worktree-merge-raises-does-not-downgrade", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Raise: "git blew up"}
	})
	add("worktree-prune-raises", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Ok: true}
		s.Beh.WTPruneRaise = "prune blew up"
	})
	// The downgrade is the LAST statement in the worktree block, after
	// cleanup and prune. An exception from either of those swallows it,
	// and a merge that FAILED then reports "done" — see PORT.md and
	// BACKLOG. Pinned because the port has to reproduce it.
	add("worktree-prune-raises-after-failed-merge", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Ok: false, Detail: "conflict", Branch: "run/L1"}
		s.Beh.WTPruneRaise = "prune blew up"
	})
	add("worktree-cleanup-raises-after-failed-merge", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Ok: false, Detail: "conflict", Branch: "run/L1"}
		s.Beh.WTCleanupRaise = "cleanup blew up"
	})
	// The clone lane has the same three-line shape and survives it: the
	// except branch downgrades anyway, with the exception's message
	// instead of the merge's detail.
	add("clone-cleanup-raises-after-failed-merge", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Beh.CloneMerge = &mergeSpec{Ok: false, Detail: "clone conflict"}
		s.Beh.CloneCleanupRaise = "cleanup blew up"
	})
	add("both-lanes-fail", func(s *scenario) {
		s.Ctx.ContainerClone = &cloneSpec{Path: "/c", Branch: "b"}
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.CloneMerge = &mergeSpec{Ok: false, Detail: "clone conflict"}
		s.Beh.WTMerge = &mergeSpec{Ok: false, Detail: "wt conflict", Branch: "run/L1"}
	})
	add("verdict-unchanged-skips-outcome-stamp", func(s *scenario) {
		s.Ctx.StopVerdict = "goal-met"
	})
	add("stamp-outcome-raises", func(s *scenario) {
		s.Ctx.RunWorktree = &wtSpec{RepoDir: "/repo"}
		s.Beh.WTMerge = &mergeSpec{Ok: false, Detail: "conflict", Branch: "b"}
		s.Beh.StampOutcomeRaise = "stamp blew up"
	})
	add("stamp-run-raises", func(s *scenario) { s.Beh.StampRunRaise = "meta blew up" })
	add("slot-and-lease-released", func(s *scenario) {
		s.Ctx.HasSlot = true
		s.Ctx.HasLease = true
	})
	add("slot-release-raises-keeps-slot", func(s *scenario) {
		s.Ctx.HasSlot = true
		s.Ctx.HasLease = true
		s.Beh.SlotRaise = "flock stuck"
	})
	add("lease-release-raises-keeps-lease", func(s *scenario) {
		s.Ctx.HasLease = true
		s.Beh.LeaseRaise = "lease stuck"
	})
	add("clear-running-raises", func(s *scenario) { s.Beh.ClearRaise = "lockfile gone" })
	add("heartbeat-raises", func(s *scenario) { s.Beh.HBRaise = "no heartbeat" })
	add("pause-reason-carried", func(s *scenario) {
		s.Ctx.PauseReason = "waiting on user"
		s.In.LoopStatus = "paused"
	})
	add("injections-snapshotted", func(s *scenario) {
		s.Ctx.Injections = []string{"i1", "i2"}
	})
	add("elapsed-from-started-at", func(s *scenario) {
		s.Ctx.StartedAt = 1.25
		s.Beh.Monotonic = 100.877
	})
	return sc
}

func srcDirLF(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func runProbe(t *testing.T, dir string, scs []scenario) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	pyRoot := filepath.Join(dir, "py")
	if err := os.MkdirAll(pyRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "probe.py.tpl", srcDirLF(t), pyRoot, specPath)
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Fatalf("probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	var recs []map[string]any
	if err := json.Unmarshal(out, &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

// goRecord runs the Go port over one scenario and builds the same record
// the probe builds.
func goRecord(t *testing.T, root string, s scenario) map[string]any {
	t.Helper()
	beh := s.Beh
	if beh == nil {
		beh = &behSpec{}
	}
	base := filepath.Join(root, s.Name)
	if err := os.MkdirAll(filepath.Join(base, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}

	var calls []map[string]any
	var logs []map[string]any
	var stderr strings.Builder
	rec := func(name string, kv map[string]any) {
		if kv == nil {
			kv = map[string]any{}
		}
		kv["call"] = name
		calls = append(calls, kv)
	}
	boom := func(msg string) error {
		if msg == "" {
			return nil
		}
		return errors.New(msg)
	}
	logAt := func(level string) func(string) {
		return func(msg string) {
			logs = append(logs, map[string]any{"level": level, "msg": msg})
		}
	}

	mono := beh.Monotonic
	if mono == 0 {
		mono = 12.5
	}
	now := beh.Now
	if now == "" {
		now = "2026-08-27T00:00:00+00:00"
	}
	logPath := beh.LogPath
	if logPath == "" {
		logPath = "/logs/loop.json"
	}

	ctx := looptypes.NewLoopContext()
	c := s.Ctx
	ctx.LoopID, ctx.Project, ctx.Goal = c.LoopID, c.Project, c.Goal
	ctx.DryRun, ctx.Verbose = c.DryRun, c.Verbose
	ctx.StopVerdict, ctx.StopEvidence = c.StopVerdict, c.StopEvidence
	ctx.PauseReason = c.PauseReason
	ctx.MeasurementClass, ctx.HandleID = c.MeasurementClass, c.HandleID
	ctx.DeferLearning, ctx.DeferMaintenance = c.DeferLearning, c.DeferMaint
	ctx.StartedAt = c.StartedAt
	ctx.Injections = nil
	for _, s := range c.Injections {
		o := pyval.Obj{}
		o.Set("v", s)
		ctx.Injections = append(ctx.Injections, o)
	}
	if c.HasSlot {
		ctx.ProjectSlot = "slot"
	}
	if c.HasLease {
		ctx.RunLease = "lease"
	}
	if c.ContainerClone != nil {
		ctx.ContainerClone = *c.ContainerClone
	}
	if c.RunWorktree != nil {
		ctx.RunWorktree = *c.RunWorktree
	}

	var outs []looptypes.StepOutcome
	for _, st := range s.In.Steps {
		outs = append(outs, looptypes.StepOutcome{
			Index: st.Index, Text: st.Text, Status: st.Status, Result: st.Result})
	}
	var pf *PreFlightReview
	if s.In.PF != nil {
		pf = &PreFlightReview{Scope: s.In.PF.Scope,
			MilestoneStepIndices: s.In.PF.Milestones}
		for _, k := range s.In.PF.Flags {
			pf.Flags = append(pf.Flags, PreFlightFlag{Kind: k})
		}
	}
	milestones := map[int]bool{}
	for i := 0; i < s.In.MilestoneExpand; i++ {
		milestones[i] = true
	}
	scratch := pyval.Obj{}
	for _, e := range s.In.Scratchpad {
		scratch.Set(e.K, pyval.FromPlain(e.V))
	}

	pyInjections := func() []any {
		out := []any{}
		for _, o := range ctx.Injections {
			out = append(out, o.GetString("v"))
		}
		return out
	}

	d := Deps{
		Monotonic: func() float64 { return mono },
		NowISO:    func() string { return now },
		WritePlanManifest: func(m ManifestIn) error {
			rec("plan_manifest", map[string]any{
				"status": m.Status, "elapsed_ms": m.ElapsedMS,
				"replan": m.ReplanCount, "steps": m.PlannedSteps,
				"start_ts": m.StartTS, "project": m.Project,
				"loop_id": m.LoopID})
			return boom(beh.ManifestRaise)
		},
		WriteRunReport: func(r ReportIn) error {
			rec("run_report", map[string]any{
				"status": r.Status, "elapsed_ms": r.ElapsedMS,
				"injections": pyInjections()})
			return boom(beh.ReportRaise)
		},
		WriteLoopLog: func(l LogIn) (string, error) {
			rec("loop_log", map[string]any{
				"status": l.Status, "elapsed_ms": l.ElapsedMS,
				"stuck": l.StuckReason, "nsteps": len(l.Steps),
				"injections": pyInjections()})
			if err := boom(beh.LogLogRaise); err != nil {
				return "", err
			}
			return logPath, nil
		},
		WriteRunsIndex: func(force bool) error {
			rec("runs_index", map[string]any{"force": force})
			return boom(beh.RunsIndexRaise)
		},
		AppendDecision: func(project string, lines []string) error {
			rec("append_decision", map[string]any{
				"project": project, "lines": lines})
			return boom(beh.AppendDecRaise)
		},
		WriteOperatorStatus: func() error {
			rec("operator_status", nil)
			return boom(beh.OpStatusRaise)
		},
		MemoryDir: func() (string, error) {
			if err := boom(beh.MemDirRaise); err != nil {
				return "", err
			}
			return filepath.Join(base, "memory"), nil
		},
		LockedAppend: func(path, line string) error {
			rec("locked_append", map[string]any{"name": filepath.Base(path)})
			if err := boom(beh.LockedAppendRaise); err != nil {
				return err
			}
			f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
			if err != nil {
				return err
			}
			defer f.Close()
			_, err = f.WriteString(line + "\n")
			return err
		},
		WriteEvent: func(name string, kw pyval.Obj) error {
			rec("write_event", map[string]any{
				"name": name, "kw": pyval.Plain(kw)})
			return boom(beh.WriteEventRaise)
		},
		ArtifactDir: func(project string) (string, error) {
			if err := boom(beh.ArtifactDirRaise); err != nil {
				return "", err
			}
			dir := filepath.Join(base, "art", project, "artifacts")
			if err := os.MkdirAll(dir, 0o755); err != nil {
				return "", err
			}
			return dir, nil
		},
		ProjectDirRoot: func() string { return filepath.Join(base, "projroot") },
		MakeDirs: func(dir string, parents bool) error {
			if parents {
				return os.MkdirAll(dir, 0o755)
			}
			// mkdir(exist_ok=True): one level, existing is fine.
			if err := os.Mkdir(dir, 0o755); err != nil && !os.IsExist(err) {
				return err
			}
			return nil
		},
		WriteFile: func(path, content string) error {
			return os.WriteFile(path, []byte(content), 0o644)
		},
		LandFacts: func() (int, int, error) {
			rec("land_facts", map[string]any{
				"loop_id": ctx.LoopID, "project": ctx.Project,
				"dry_run": ctx.DryRun})
			if err := boom(beh.LandFactsRaise); err != nil {
				return 0, 0, err
			}
			if len(beh.LandFacts) == 2 {
				return beh.LandFacts[0], beh.LandFacts[1], nil
			}
			return 0, 0, nil
		},
		FinalizeLoop: func(f FinalizeIn) error {
			rec("finalize_loop", map[string]any{
				"loop_id": f.LoopID, "goal": f.Goal, "project": f.Project,
				"loop_status": f.LoopStatus, "dry_run": f.DryRun,
				"verbose": f.Verbose, "total_tokens_in": f.TotalTokensIn,
				"total_tokens_out":      f.TotalTokensOut,
				"elapsed_ms":            f.ElapsedMS,
				"had_no_matching_skill": f.HadNoMatching,
				"failure_chain":         orEmpty(f.FailureChain),
				"recovery_steps":        f.RecoverySteps,
				"defer_learning":        f.DeferLearning,
				"defer_maintenance":     f.DeferMaintenance,
				"measurement_class":     f.MeasurementClass,
				"handle_id":             f.HandleID,
				"stop_verdict":          f.StopVerdict,
				"stop_evidence":         f.StopEvidence,
				"pause_reason":          f.PauseReason})
			return boom(beh.FinalizeRaise)
		},
		CleanupStepArtifacts: func(project, exclude string) int {
			rec("cleanup_artifacts", map[string]any{
				"project": project, "exclude": exclude})
			return 0
		},
		MergeBackClone: func(msg string) (MergeResult, error) {
			rec("merge_back_clone", map[string]any{"message": msg})
			m := beh.CloneMerge
			if m == nil {
				m = &mergeSpec{Ok: true}
			}
			if m.Raise != "" {
				return MergeResult{}, errors.New(m.Raise)
			}
			return MergeResult{Ok: m.Ok, Detail: m.Detail, Branch: m.Branch}, nil
		},
		CleanupClone: func(keep bool) error {
			rec("cleanup_clone", map[string]any{"keep": keep})
			return boom(beh.CloneCleanupRaise)
		},
		CloneRef: func() CloneRef {
			if c.ContainerClone == nil {
				return CloneRef{Path: "?", Branch: "?"}
			}
			return CloneRef{Path: c.ContainerClone.Path,
				Branch: c.ContainerClone.Branch}
		},
		MergeBack: func(msg string) (MergeResult, error) {
			rec("merge_back", map[string]any{"message": msg})
			m := beh.WTMerge
			if m == nil {
				m = &mergeSpec{Ok: true}
			}
			if m.Raise != "" {
				return MergeResult{}, errors.New(m.Raise)
			}
			return MergeResult{Ok: m.Ok, Detail: m.Detail, Branch: m.Branch}, nil
		},
		CleanupWT: func(keep bool) error {
			rec("wt_cleanup", map[string]any{"keep": keep})
			return boom(beh.WTCleanupRaise)
		},
		PruneWT: func() error {
			repo := ""
			if c.RunWorktree != nil {
				repo = c.RunWorktree.RepoDir
			}
			rec("wt_prune", map[string]any{"repo_dir": repo})
			return boom(beh.WTPruneRaise)
		},
		StampOutcome: func(loopID, verdict, evidence string) error {
			rec("stamp_outcome", map[string]any{
				"loop_id": loopID, "verdict": verdict, "evidence": evidence})
			return boom(beh.StampOutcomeRaise)
		},
		StampRunStop: func(verdict, evidence, pause string) error {
			rec("stamp_run", map[string]any{
				"verdict": verdict, "evidence": evidence, "pause": pause})
			return boom(beh.StampRunRaise)
		},
		ReleaseSlot: func() error {
			rec("release_slot", nil)
			return boom(beh.SlotRaise)
		},
		ReleaseLease: func() error {
			rec("release_lease", nil)
			return boom(beh.LeaseRaise)
		},
		ClearRunning: func() error {
			rec("clear_running", nil)
			return boom(beh.ClearRaise)
		},
		PostHeartbeat: func(eventType, payload string) error {
			rec("post_heartbeat", map[string]any{
				"event_type": eventType, "payload": payload})
			return boom(beh.HBRaise)
		},
		Stderr: func(line string) { stderr.WriteString(line + "\n") },
		Warn:   logAt("WARNING"),
		Debug:  logAt("DEBUG"),
		Info:   logAt("INFO"),
	}

	out := map[string]any{"name": s.Name}
	res, err := BuildResultAndFinalize(ctx, In{
		StepOutcomes:       outs,
		LoopStatus:         s.In.LoopStatus,
		StuckReason:        s.In.StuckReason,
		TotalTokensIn:      s.In.TokensIn,
		TotalTokensOut:     s.In.TokensOut,
		InterruptsApplied:  s.In.Interrupts,
		MarchOfNinesAlert:  s.In.March,
		PFReview:           pf,
		ManifestSteps:      s.In.ManifestSteps,
		ReplanCount:        s.In.Replan,
		StartTS:            s.In.StartTS,
		MilestoneExpanded:  milestones,
		HadNoMatchingSkill: s.In.HadNoSkill,
		FailureChain:       s.In.FailureChain,
		RecoveryStepCount:  s.In.Recovery,
		Scratchpad:         scratch,
		ScratchpadLock:     &sync.Mutex{},
	}, d)
	if err != nil {
		out["error"] = "Boom: " + err.Error()
	} else {
		out["result"] = map[string]any{
			"loop_id": res.LoopID, "project": res.Project, "goal": res.Goal,
			"status": res.Status, "nsteps": len(res.Steps),
			"interrupts":    res.InterruptsApplied,
			"injections":    pyInjections(),
			"stuck_reason":  nilOrStr(res.StuckReason),
			"stop_verdict":  res.StopVerdict,
			"stop_evidence": res.StopEvidence,
			"pause_reason":  res.PauseReason,
			"tokens_in":     res.TotalTokensIn, "tokens_out": res.TotalTokensOut,
			"elapsed_ms": res.ElapsedMS, "log_path": derefOr(res.LogPath, ""),
			"march":        res.MarchOfNinesAlert,
			"had_no_skill": res.HadNoMatchingSkill,
			"summary":      res.Summary(),
		}
	}
	out["calls"] = orEmptyCalls(calls)
	out["logs"] = orEmptyCalls(logs)
	out["stderr"] = stderr.String()
	out["ctx_after"] = map[string]any{
		"slot": ctx.ProjectSlot != nil, "lease": ctx.RunLease != nil,
		"clone": ctx.ContainerClone != nil, "worktree": ctx.RunWorktree != nil,
	}
	files := map[string]any{}
	err = filepath.Walk(base, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(base, p)
		if err != nil {
			return err
		}
		files[rel] = string(b)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	out["files"] = files
	return out
}

func nilOrStr(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func orEmpty(v []string) []string {
	if v == nil {
		return []string{}
	}
	return v
}

func orEmptyCalls(v []map[string]any) []map[string]any {
	if v == nil {
		return []map[string]any{}
	}
	return v
}

func canon(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var round any
	if err := json.Unmarshal(b, &round); err != nil {
		t.Fatal(err)
	}
	b2, err := json.MarshalIndent(round, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	return string(b2)
}

// firstDiff reports the differing lines with a little context, so a
// twelve-call record does not have to be read twice in full to find the
// one field that moved.
func firstDiff(want, got string) string {
	w := strings.Split(want, "\n")
	g := strings.Split(got, "\n")
	var b strings.Builder
	n := len(w)
	if len(g) > n {
		n = len(g)
	}
	shown := 0
	for i := 0; i < n && shown < 12; i++ {
		wl, gl := "", ""
		if i < len(w) {
			wl = w[i]
		}
		if i < len(g) {
			gl = g[i]
		}
		if wl != gl {
			fmt.Fprintf(&b, "line %d:\n  py: %s\n  go: %s\n", i+1, wl, gl)
			shown++
		}
	}
	return b.String()
}

func TestBuildResultAndFinalizeMatchesCPython(t *testing.T) {
	scs := scenarios()
	dir := t.TempDir()
	py := runProbe(t, dir, scs)
	if len(py) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios", len(py), len(scs))
	}
	goRoot := filepath.Join(dir, "go")
	for i, s := range scs {
		got := canon(t, goRecord(t, goRoot, s))
		want := canon(t, py[i])
		if got != want {
			t.Errorf("scenario %q diverges:\n%s", s.Name, firstDiff(want, got))
		}
	}
}

// TestScenarioNamesAreUnique guards the record-by-index comparison above:
// two scenarios sharing a name would share a temp subdirectory, and the
// second one's file tree would inherit the first one's artifacts.
func TestScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range scenarios() {
		if seen[s.Name] {
			t.Fatalf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}

// TestRenderTranscriptIsRuneHeaded pins the one thing the differential
// cannot see on its own: that both caps count code points. A byte-headed
// port passes every ASCII scenario.
func TestRenderTranscriptIsRuneHeaded(t *testing.T) {
	long := strings.Repeat("é", TranscriptStepResultCap+7)
	_, body := RenderTranscript("g", "done", []looptypes.StepOutcome{
		{Index: 1, Text: "t", Status: "done", Result: long}}, nil, 0, 0, 0)
	want := fmt.Sprintf("... (truncated, %d chars total)",
		TranscriptStepResultCap+7)
	if !strings.Contains(body, want) {
		t.Errorf("missing %q in:\n%s", want, body)
	}
	head := strings.Repeat("é", TranscriptStepResultCap)
	if !strings.Contains(body, head+"\n\n"+"... (truncated") {
		t.Errorf("result not cut at %d code points", TranscriptStepResultCap)
	}
}
