package orch

// run_mission and drain_next_mission — mission.py's execution half.
//
// WHAT IS AND IS NOT HERE (read this before assuming a lane is missing by
// accident). Python's run_mission reaches five subsystems this port does
// not have: `hooks`, `sprint_contract`, `boot_protocol`, `ancestry` and
// `loop_artifacts`. Four of the five are ALREADY optional over there —
// sprint_contract and boot_protocol behind `try/except ImportError`, and
// every `run_hooks` / ancestry call inside a bare `except Exception:
// pass`. So a Python run on a box where those imports fail takes exactly
// the path this port takes, and that path is a supported one rather than
// a degraded one.
//
// They are seams here, not silence: each is a nil-able field on
// RunOptions, and nil means "the import failed", which is the branch
// Python documents. What that costs is written down in PORT.md; do not
// read the seams' presence as evidence anything is wired to them.
//
// The one thing that is NOT optional in Python is the agent loop itself,
// so RunFeature is REQUIRED. A mission that silently ran no features and
// reported a status would be worse than an error, and Go cannot do
// Python's lazy `from agent_loop import run_agent_loop` — the import
// direction is what keeps `orch` free of `loop`. The wiring lives in
// internal/missionrun.

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// FeatureRequest is one call to run_agent_loop.
type FeatureRequest struct {
	Title   string
	Project string
	DryRun  bool
	// AncestryExtra is Python's ancestry_context_extra: the concatenation
	// of hook-injected context, the boot-protocol block and the sprint
	// contract's criteria. Empty whenever those seams are nil, which is
	// the ImportError path.
	AncestryExtra string
}

// FeatureOutcome is the slice of LoopResult run_mission actually reads.
type FeatureOutcome struct {
	LoopID     string
	Status     string
	StepsDone  int
	StepsTotal int
}

// RunFeatureFn executes one feature. An error here is Python's `except
// Exception as exc` around run_agent_loop — the feature is blocked and
// the mission continues.
type RunFeatureFn func(ctx context.Context, req FeatureRequest) (FeatureOutcome, error)

// HookResult is one entry of hooks.run_hooks' return list.
type HookResult struct {
	Output      string
	ShouldBlock bool
	// Injected marks a NOTIFICATION hook whose output feeds
	// get_injected_context. Kept separate from Output because a blocking
	// hook's output goes somewhere else entirely.
	Injected bool
}

// HookFn is hooks.run_hooks. scope is one of the SCOPE_* constants,
// fireOn is "before" or "after". nil is the ImportError path.
type HookFn func(scope string, hctx map[string]string, fireOn string) []HookResult

// Hook scopes, spelled as Python spells them.
const (
	ScopeMission   = "mission"
	ScopeMilestone = "milestone"
	ScopeFeature   = "feature"
)

// RunOptions carries run_mission's keyword arguments and the seams that
// stand in for the unported subsystems.
type RunOptions struct {
	// Project is the `project` kwarg. Empty resolves through ResolveSlug.
	Project string
	Adapter llm.Adapter
	DryRun  bool
	Verbose bool

	// RunFeature is REQUIRED — see the package note above.
	RunFeature RunFeatureFn

	// ResolveSlug is loop_artifacts.resolve_project_slug. Required only
	// when Project is empty.
	ResolveSlug func(goal string) string

	// Hooks is hooks.run_hooks; nil = the ImportError path.
	Hooks HookFn
	// Ancestry is ancestry.build_ancestry_prompt over
	// get_project_ancestry; nil = the `except Exception: pass` path,
	// which leaves mission.ancestry_context "".
	Ancestry func(projectDir, goal string) string

	// LogFn is the verbose-gated operator line. nil + Verbose writes
	// Python's `[maro:mission] ...` to stderr.
	LogFn func(string)

	// Cfg is the merged config. nil loads it, which is what Python's
	// module-level `from config import get` resolves to.
	Cfg map[string]any

	// Now and NewID are the clock and id seams DecomposeMission already
	// takes; nil gets the real ones.
	Now   func() time.Time
	NewID func() string

	// MaxMilestones / MaxFeaturesPerMilestone are decompose_mission's
	// defaults (4 and 3). Zero means the default, not zero.
	MaxMilestones           int
	MaxFeaturesPerMilestone int
}

// ErrNoFeatureRunner is returned before anything is written when
// RunFeature is nil.
var ErrNoFeatureRunner = errors.New(
	"mission: RunFeature is required — a mission that runs no features " +
		"and reports a status is worse than an error")

// ErrNoSlugResolver is returned before anything is written when Project
// is empty and no resolver was supplied.
var ErrNoSlugResolver = errors.New(
	"mission: ResolveSlug is required when Project is empty " +
		"(loop_artifacts.resolve_project_slug has no fallback in Python)")

// missionRun is one run's mutable state plus the lock that makes a
// mid-flight snapshot safe.
//
// This is where the DAG package's NAMED RESIDUAL closes. Python's
// `_save_lock` guards only save_mission, so a snapshot can capture a
// sibling milestone mid-mutation — a LOGICAL tear that self-heals at the
// next persist. Under the GIL that is all it is; in Go the same shape is
// a data race, which is undefined behaviour rather than a stale field.
//
// So the lock is held by BOTH sides here: every mutation of a Milestone
// or Feature goes through mu, and so does the snapshot. The feature and
// validation WORK stays outside it, which is the whole point — the
// critical sections are field assignments, so the concurrency the DAG
// buys is untouched.
type missionRun struct {
	mu sync.Mutex
	m  *Mission
	ws string
	pj string
}

// lock runs fn with the mission lock held. Used for every field write.
func (r *missionRun) lock(fn func()) {
	r.mu.Lock()
	defer r.mu.Unlock()
	fn()
}

// persist is run_mission's `_persist`: a whole-mission snapshot, errors
// swallowed exactly as Python's `except Exception: pass` swallows them.
func (r *missionRun) persist() {
	r.mu.Lock()
	defer r.mu.Unlock()
	_ = SaveMission(r.ws, r.m, r.pj)
}

// RunMission is run_mission: decompose into milestones + features,
// execute them, validate, and record.
//
// It returns a MissionResult and an error. Python returns only the
// result and raises nothing this side can see — the error here is
// reserved for the two REQUIRED seams and for a decompose that returned
// nothing, i.e. states in which Python would have raised on the caller's
// behalf (ErrNoAdapter is already that shape in DecomposeMission).
func RunMission(ctx context.Context, ws, goal string, opts RunOptions) (MissionResult, error) {
	if opts.RunFeature == nil {
		return MissionResult{}, ErrNoFeatureRunner
	}
	if opts.Project == "" && opts.ResolveSlug == nil {
		return MissionResult{}, ErrNoSlugResolver
	}

	started := time.Now()
	logf := opts.LogFn
	if logf == nil {
		verbose := opts.Verbose
		logf = func(msg string) {
			if verbose {
				fmt.Fprintf(os.Stderr, "[maro:mission] %s\n", msg)
			}
		}
	}
	nowFn := opts.Now
	if nowFn == nil {
		nowFn = func() time.Time { return time.Now().UTC() }
	}
	// pyval.NowISO, not RFC3339Nano: Python spells every one of these
	// datetime.now(timezone.utc).isoformat() ("+00:00", six digits, no
	// fraction at zero). The injected clock stays — only the rendering
	// was wrong.
	nowISO := func() string { return pyval.NowISO(nowFn().UTC()) }
	newID := opts.NewID
	cfg := opts.Cfg
	if cfg == nil {
		cfg, _ = config.LoadFor(ws)
	}

	maxMS := opts.MaxMilestones
	if maxMS == 0 {
		maxMS = 4
	}
	maxFeat := opts.MaxFeaturesPerMilestone
	if maxFeat == 0 {
		maxFeat = 3
	}

	// Resolve project.
	project := opts.Project
	if project == "" {
		project = opts.ResolveSlug(goal)
	}
	if _, err := os.Stat(ProjectDir(ws, project)); os.IsNotExist(err) {
		// goal[:80] is a Python str slice — RUNES, not bytes.
		if _, err := EnsureProject(ws, project, runeHead(goal, 80), 0); err == nil {
			logf("created project=" + project)
		}
	}

	// Ancestry context. Python wraps the whole block in `except
	// Exception: pass`, so a nil seam and a raising one are the same.
	ancestry := ""
	if opts.Ancestry != nil {
		ancestry = opts.Ancestry(ProjectDir(ws, project), goal)
	}

	logf(fmt.Sprintf("decomposing goal=%s", pytext.Repr(goal)))
	mission, err := DecomposeMission(ctx, goal, opts.Adapter, maxMS, maxFeat,
		nowISO, newID)
	if err != nil {
		return MissionResult{}, err
	}
	mission.Project = project
	mission.AncestryContext = ancestry
	mission.Status = "running"
	logf(fmt.Sprintf("decomposed: %d milestones", len(mission.Milestones)))

	// Both of these are `except Exception: pass` in Python.
	if _, err := GenerateFeatureManifest(ws, mission, project); err == nil {
		logf("feature manifest created")
	}
	_ = SaveMission(ws, mission, project)

	run := &missionRun{m: mission, ws: ws, pj: project}

	runHooks := func(scope string, hctx map[string]string, fireOn string) []HookResult {
		if opts.Hooks == nil {
			return nil
		}
		return opts.Hooks(scope, hctx, fireOn)
	}

	runHooks(ScopeMission, map[string]string{
		"goal": goal, "mission_id": mission.ID, "project": project,
	}, "before")

	runFeature := func(milestone *Milestone, feature *Feature) {
		run.lock(func() { feature.Status = "running" })
		featStart := time.Now()

		beforeCtx := map[string]string{
			"goal":            goal,
			"project":         project,
			"feature_title":   feature.Title,
			"milestone_title": milestone.Title,
			"mission_id":      mission.ID,
		}
		extra := injectedContext(runHooks(ScopeFeature, beforeCtx, "before"))

		// The boot-protocol and sprint-contract blocks sit here in
		// Python, between the before-hooks and the loop call, each
		// appending to extra. Unported with their modules (PORT.md).

		out, err := opts.RunFeature(ctx, FeatureRequest{
			Title:         feature.Title,
			Project:       project,
			DryRun:        opts.DryRun,
			AncestryExtra: extra,
		})
		run.lock(func() {
			if err != nil {
				feature.Status = "blocked"
				feature.ResultSummary = strPtr(fmt.Sprintf("error: %v", err))
				return
			}
			feature.WorkerSessionID = strPtr(out.LoopID)
			// Anything that is not "done" is blocked — the loop's own
			// vocabulary is wider than the feature's, and this is the
			// narrowing. drain_next_mission deliberately does NOT do
			// this; see DrainNextMission.
			if out.Status == "done" {
				feature.Status = "done"
			} else {
				feature.Status = "blocked"
			}
			feature.ResultSummary = strPtr(fmt.Sprintf(
				"loop=%s status=%s steps=%d/%d",
				out.LoopID, out.Status, out.StepsDone, out.StepsTotal))
		})

		afterCtx := map[string]string{}
		for k, v := range beforeCtx {
			afterCtx[k] = v
		}
		run.lock(func() {
			afterCtx["feature_status"] = feature.Status
			afterCtx["feature_result_summary"] = derefOr(feature.ResultSummary, "")
			afterCtx["feature_result"] = derefOr(feature.ResultSummary, "")
		})
		if after := runHooks(ScopeFeature, afterCtx, "after"); anyBlocking(after) {
			var blocked []string
			for _, r := range after {
				if r.ShouldBlock {
					blocked = append(blocked, r.Output)
				}
			}
			if len(blocked) > 2 {
				blocked = blocked[:2]
			}
			run.lock(func() {
				feature.Status = "blocked"
				feature.ResultSummary = strPtr(derefOr(feature.ResultSummary, "") +
					" [BLOCKED by hook: " + strings.Join(blocked, "; ") + "]")
			})
		}

		// The sprint-contract GRADING block sits here in Python, and it
		// is the one that calls MarkFeaturePassing. Unported with its
		// module — which means feature_list.json's `passing` flags are
		// never set by this port. Named in PORT.md, because a manifest
		// that never advances reads exactly like a mission that never
		// passed anything.

		elapsed := int(time.Since(featStart).Milliseconds())
		run.lock(func() {
			feature.ElapsedMS = elapsed
			// Setting the carried literal too: this feature has a NEW
			// elapsed, so the value loaded from disk is no longer what
			// should be written back.
			feature.elapsedRaw = elapsed
		})
	}

	runMilestone := func(msIdx int, milestone *Milestone) error {
		logf(fmt.Sprintf("milestone %d/%d: %s",
			msIdx+1, len(mission.Milestones), pytext.Repr(milestone.Title)))
		run.lock(func() { milestone.Status = "running" })

		// Python wraps the whole feature-execution block in try/except
		// and blocks whatever is still pending or running on the way
		// out. The Go equivalent of "the executor itself failed" is a
		// panic in the pool plumbing, so the recover is the same
		// backstop the DAG's runWithRecover is.
		func() {
			defer func() {
				if r := recover(); r != nil {
					logf(fmt.Sprintf("  feature execution error: %v", r))
					run.lock(func() {
						for i := range milestone.Features {
							f := &milestone.Features[i]
							if f.Status == "pending" || f.Status == "running" {
								f.Status = "blocked"
							}
						}
					})
				}
			}()
			if len(milestone.Features) <= 1 {
				for i := range milestone.Features {
					f := &milestone.Features[i]
					runFeature(milestone, f)
					logf(fmt.Sprintf("  feature done: %s status=%s",
						pytext.Repr(f.Title), f.Status))
				}
				return
			}
			// ThreadPoolExecutor(max_workers=2), and the literal 2 is
			// Python's — it is NOT mission.milestone_workers, which
			// governs the milestone pool one level up.
			sem := make(chan struct{}, 2)
			var wg sync.WaitGroup
			for i := range milestone.Features {
				f := &milestone.Features[i]
				wg.Add(1)
				go func() {
					defer wg.Done()
					sem <- struct{}{}
					defer func() { <-sem }()
					// A panic inside one feature is that FEATURE's
					// failure in Python (the Future captures it and
					// fut.result() re-raises into the per-future
					// except), not the pool's.
					defer func() {
						if r := recover(); r != nil {
							run.lock(func() {
								f.Status = "blocked"
								f.ResultSummary = strPtr(fmt.Sprintf("executor error: %v", r))
							})
						}
						logf(fmt.Sprintf("  feature done: %s status=%s",
							pytext.Repr(f.Title), f.Status))
					}()
					runFeature(milestone, f)
				}()
			}
			wg.Wait()
		}()

		run.lock(func() { milestone.Status = "validating" })
		logf(fmt.Sprintf("  validating milestone: %s", pytext.Repr(milestone.Title)))
		// "Don't get stuck in validation": Python's except sets passed =
		// True. ValidateMilestone already defaults to pass at all four of
		// its exits, so there is no error to swallow — but the intent is
		// worth keeping visible, because a future exit that fails closed
		// would be a divergence.
		passed := ValidateMilestone(ctx, milestone, project, opts.Adapter, opts.DryRun)

		msCtx := map[string]string{
			"goal":                goal,
			"project":             project,
			"milestone_title":     milestone.Title,
			"mission_id":          mission.ID,
			"validation_criteria": bulletList(milestone.ValidationCriteria),
			"features_total":      fmt.Sprintf("%d", len(milestone.Features)),
			"features_done":       "",
			"features_summary":    "",
		}
		var featuresOK int
		run.lock(func() {
			var lines []string
			for i := range milestone.Features {
				f := &milestone.Features[i]
				if f.Status == "done" {
					featuresOK++
				}
				lines = append(lines, "- "+f.Title+": "+f.Status)
			}
			msCtx["features_done"] = fmt.Sprintf("%d", featuresOK)
			msCtx["features_summary"] = strings.Join(lines, "\n")
		})
		if anyBlocking(runHooks(ScopeMilestone, msCtx, "after")) {
			passed = false
			logf(fmt.Sprintf("  milestone BLOCKED by hook: %s", pytext.Repr(milestone.Title)))
		}

		featuresTotal := len(milestone.Features)
		switch {
		case passed:
			run.lock(func() {
				milestone.Status = "done"
				milestone.ValidationResult = strPtr("passed")
			})
			logf(fmt.Sprintf("  milestone passed: %s", pytext.Repr(milestone.Title)))
		case featuresOK > 0:
			run.lock(func() {
				milestone.Status = "partial"
				milestone.ValidationResult = strPtr("partial")
			})
			logf(fmt.Sprintf("  milestone PARTIAL: %s (%d/%d features)",
				pytext.Repr(milestone.Title), featuresOK, featuresTotal))
		default:
			run.lock(func() {
				milestone.Status = "failed"
				milestone.ValidationResult = strPtr("failed")
			})
			logf(fmt.Sprintf("  milestone FAILED: %s (0/%d features)",
				pytext.Repr(milestone.Title), featuresTotal))
			// No break: later milestones may have independent sub-goals.
		}

		run.persist()
		return nil
	}

	// get_bool, NOT bool(get(...)): this flag is THE revert lever, and a
	// quoted "false" in YAML must actually revert (bool("false") is True).
	parallel, _ := config.GetBool(cfg, "mission.parallel_milestones", true)
	msWorkers := MilestoneWorkers(cfg)

	if parallel && len(mission.Milestones) > 1 && !IsChainShaped(mission) {
		RunMilestoneDAG(ctx, mission, func(c context.Context, idx int, ms *Milestone) error {
			return runMilestone(idx, ms)
		}, DAGOptions{
			MaxWorkers: msWorkers,
			LogFn:      logf,
			PersistFn:  run.persist,
			WarnFn:     func(string) {},
		})
	} else {
		// Chain-shaped, flag off, or a single milestone: the literal
		// pre-DAG path — main goroutine, unchanged error propagation.
		for i := range mission.Milestones {
			_ = runMilestone(i, &mission.Milestones[i])
		}
	}

	milestonesDone, status := ResolveMissionStatus(mission)
	mission.Status = status
	mission.CompletedAt = strPtr(nowISO())

	runHooks(ScopeMission, map[string]string{
		"goal": goal, "mission_id": mission.ID, "project": project,
		"mission_status": mission.Status,
	}, "after")

	featuresDone, featuresTotal := 0, 0
	for i := range mission.Milestones {
		for j := range mission.Milestones[i].Features {
			featuresTotal++
			if mission.Milestones[i].Features[j].Status == "done" {
				featuresDone++
			}
		}
	}

	result := MissionResult{
		MissionID:       mission.ID,
		Project:         project,
		Goal:            goal,
		Status:          mission.Status,
		MilestonesDone:  milestonesDone,
		MilestonesTotal: len(mission.Milestones),
		FeaturesDone:    featuresDone,
		FeaturesTotal:   featuresTotal,
		ElapsedMS:       int(time.Since(started).Milliseconds()),
	}

	_ = SaveMission(ws, mission, project)
	_ = WriteMissionLog(ws, result, mission)
	logf("mission complete: " + result.Summary())
	return result, nil
}

// MilestoneWorkers is run_mission's
//
//	try:    _ms_workers = max(1, int(_cfg_get("mission.milestone_workers", 2)))
//	except (TypeError, ValueError): _ms_workers = 2
//
// A configured 0 becomes ONE (the floor) and garbage becomes TWO (the
// except). Those are different numbers: a Go port that read the zero
// value as "unset" would collapse them and silently double the
// concurrency an operator asked to turn off.
//
// Exported so the arithmetic is testable without running a mission per
// case — a replay in a test file would be a second implementation, which
// is the defect the one-dict-one-reading rule is about.
func MilestoneWorkers(cfg map[string]any) int {
	w := 2
	if raw, ok := config.Lookup(cfg, "mission.milestone_workers"); ok {
		if n, ok := pyIntLike(raw); ok {
			w = n
		}
	}
	if w < 1 {
		w = 1
	}
	return w
}

// ResolveMissionStatus is run_mission's terminal branch: the count of
// milestones that made progress, and the mission status that follows
// from it.
//
// Two things here are easy to get backwards and both are load-bearing:
//
//   - "done" AND "partial" both count as progress (partial counted
//     pre-DAG too), so a failed milestone beside a partial one is
//     partial, not stuck.
//   - The whole branch is GUARDED by `mission.status != "stuck"`. A
//     mission the scheduler already marked stuck keeps that verdict even
//     when the counts would compute something softer.
func ResolveMissionStatus(m *Mission) (milestonesDone int, status string) {
	anyFailed, anyPartial := false, false
	for i := range m.Milestones {
		switch m.Milestones[i].Status {
		case "done":
			milestonesDone++
		case "partial":
			milestonesDone++
			anyPartial = true
		case "failed":
			anyFailed = true
		}
	}
	if m.Status == "stuck" {
		return milestonesDone, "stuck"
	}
	switch {
	case anyFailed && milestonesDone == 0:
		return milestonesDone, "stuck"
	case anyFailed || anyPartial:
		return milestonesDone, "partial"
	default:
		return milestonesDone, "done"
	}
}

// ---------------------------------------------------------------------------
// Drain
// ---------------------------------------------------------------------------

// NotifyFn is _send_milestone_notification's telegram_notify. nil is the
// ImportError path — Python `return`s before formatting anything.
type NotifyFn func(message string)

// MilestoneNotification is _send_milestone_notification's message, kept
// as a function so the exact string is testable without a transport.
func MilestoneNotification(project, milestoneTitle, status string) string {
	icon := "⚠"
	if status == "done" {
		icon = "✓"
	}
	return fmt.Sprintf("%s [%s] Milestone: %s — %s",
		icon, project, milestoneTitle, status)
}

// DrainOptions carries drain_next_mission's keyword arguments.
type DrainOptions struct {
	DryRun     bool
	Verbose    bool
	Notify     bool
	NotifyFn   NotifyFn
	RunFeature RunFeatureFn
	LogFn      func(string)
	Now        func() time.Time
	// MaxBriefingMissions is morning_briefing's kwarg default (5).
	MaxBriefingMissions int
}

// DrainResult is the dict drain_next_mission returns.
type DrainResult struct {
	Project         string
	MissionID       string
	Goal            string
	Status          string
	MilestonesDone  int
	MilestonesTotal int
	ElapsedMS       int
}

// DrainNextMission is drain_next_mission: pick the oldest pending mission
// and run it synchronously. nil means nothing was drained.
//
// DAG-NAIVE BY DESIGN, carried over verbatim: this lane walks milestones
// in list order and ignores depends_on, because decompose only emits
// earlier-index refs so list order is always a valid topological order.
//
// It is ALSO not run_mission, in four ways that look like bugs and are
// not:
//
//   - no validation gate and no hooks;
//   - its own status vocabulary — a milestone is "done" or "blocked",
//     never "partial" or "failed", and the mission is "done" or
//     "blocked", never "stuck";
//   - a feature takes the loop's status RAW, so a loop that came back
//     "stuck" leaves a feature marked "stuck" here where run_mission
//     would have narrowed it to "blocked";
//   - `all()` over an EMPTY feature list is True, so a milestone with no
//     features drains as done.
//
// Reconciling that divergence is its own piece of work; a Go port that
// quietly fixed it here would be changing behaviour under the name of a
// port.
func DrainNextMission(ctx context.Context, ws string, opts DrainOptions) (*DrainResult, error) {
	if opts.RunFeature == nil {
		return nil, ErrNoFeatureRunner
	}
	logf := opts.LogFn
	if logf == nil {
		verbose := opts.Verbose
		logf = func(msg string) {
			if verbose {
				fmt.Fprintf(os.Stderr, "[mission:drain] %s\n", msg)
			}
		}
	}
	nowFn := opts.Now
	if nowFn == nil {
		nowFn = func() time.Time { return time.Now().UTC() }
	}

	if IsDrainRunning(ws) {
		logf("drain already running, skipping")
		return nil, nil
	}
	pending := PendingMissions(ws)
	if len(pending) == 0 {
		return nil, nil
	}
	target := pending[0]
	project, goal := target.Project, target.Goal
	missionID := target.MissionID
	if missionID == "" {
		missionID = "?"
	}
	// Checked BEFORE the lock is taken, so a malformed row never leaves a
	// lock file behind.
	if project == "" || goal == "" {
		return nil, nil
	}
	if !AcquireDrainLock(ws, missionID) {
		return nil, nil
	}
	defer ReleaseDrainLock(ws)

	logf(fmt.Sprintf("draining %s: %s", pytext.Repr(project), pytext.Repr(runeHead(goal, 60))))

	mission := LoadMission(ws, project)
	if mission == nil {
		return nil, nil
	}

	started := time.Now()
	milestonesDone := 0

	for i := range mission.Milestones {
		ms := &mission.Milestones[i]
		if ms.Status == "done" {
			milestonesDone++
			continue
		}
		logf(fmt.Sprintf("milestone: %s", pytext.Repr(ms.Title)))

		for j := range ms.Features {
			f := &ms.Features[j]
			if f.Status == "done" {
				continue
			}
			if opts.DryRun {
				f.Status = "done"
				f.ResultSummary = strPtr("dry-run")
				continue
			}
			out, err := opts.RunFeature(ctx, FeatureRequest{
				Title:   f.Title,
				Project: project,
				DryRun:  false,
			})
			if err != nil {
				// Python does NOT guard run_agent_loop here — the
				// exception escapes the loop, unwinds through the
				// `finally`, releases the lock and propagates to the
				// caller. Returning the error is that shape.
				return nil, err
			}
			// RAW status, deliberately — see the doc comment.
			f.Status = out.Status
			f.ResultSummary = strPtr(fmt.Sprintf("%d/%d steps done",
				out.StepsDone, out.StepsTotal))
		}

		allDone := true
		for j := range ms.Features {
			if ms.Features[j].Status != "done" {
				allDone = false
				break
			}
		}
		if allDone {
			ms.Status = "done"
			milestonesDone++
		} else {
			ms.Status = "blocked"
		}

		_ = SaveMission(ws, mission, project)

		if opts.Notify && !opts.DryRun && opts.NotifyFn != nil {
			opts.NotifyFn(MilestoneNotification(project, ms.Title, ms.Status))
		}
	}

	elapsedMS := int(time.Since(started).Milliseconds())
	allMilestonesDone := true
	for i := range mission.Milestones {
		if mission.Milestones[i].Status != "done" {
			allMilestonesDone = false
			break
		}
	}
	missionStatus := "blocked"
	if allMilestonesDone {
		missionStatus = "done"
	}
	mission.Status = missionStatus
	if allMilestonesDone {
		mission.CompletedAt = strPtr(pyval.NowISO(nowFn().UTC()))
	}
	_ = SaveMission(ws, mission, project)

	if opts.Notify && !opts.DryRun && allMilestonesDone && opts.NotifyFn != nil {
		maxB := opts.MaxBriefingMissions
		if maxB == 0 {
			maxB = 5
		}
		// briefing[:3000] is a Python str slice — RUNES.
		opts.NotifyFn("Mission complete!\n" + runeHead(MorningBriefing(ws, maxB), 3000))
	}

	return &DrainResult{
		Project:         project,
		MissionID:       missionID,
		Goal:            goal,
		Status:          missionStatus,
		MilestonesDone:  milestonesDone,
		MilestonesTotal: len(mission.Milestones),
		ElapsedMS:       elapsedMS,
	}, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func strPtr(s string) *string { return &s }

func derefOr(p *string, def string) string {
	if p == nil {
		return def
	}
	return *p
}

// runeHead is a Python str slice `s[:n]` — code points, not bytes. Go's
// s[:n] would cut mid-rune and, on a multi-byte boundary, produce an
// invalid string where Python produces a shorter valid one.
func runeHead(s string, n int) string {
	if n <= 0 {
		return ""
	}
	count := 0
	for i := range s {
		if count == n {
			return s[:i]
		}
		count++
	}
	return s
}

func bulletList(items []string) string {
	lines := make([]string, len(items))
	for i, c := range items {
		lines[i] = "- " + c
	}
	return strings.Join(lines, "\n")
}

func anyBlocking(results []HookResult) bool {
	for _, r := range results {
		if r.ShouldBlock {
			return true
		}
	}
	return false
}

// injectedContext is hooks.get_injected_context: the outputs of the
// NOTIFICATION hooks, joined. Empty when the seam is nil.
func injectedContext(results []HookResult) string {
	var parts []string
	for _, r := range results {
		if r.Injected && r.Output != "" {
			parts = append(parts, r.Output)
		}
	}
	return strings.Join(parts, "\n\n")
}

// pyIntLike is `int(x)` over a YAML scalar, reporting TypeError and
// ValueError as a single false. int(3.9) truncates TOWARD ZERO, which is
// neither Go's round nor its floor for negatives.
func pyIntLike(v any) (int, bool) {
	switch t := v.(type) {
	case int:
		return t, true
	case int64:
		return int(t), true
	case float64:
		return int(t), true
	case bool:
		// Python's int(True) is 1. YAML rarely produces this here, but
		// bool is an int subclass over there and silently dropping it
		// would be a divergence.
		if t {
			return 1, true
		}
		return 0, true
	case string:
		var n int
		if _, err := fmt.Sscanf(strings.TrimSpace(t), "%d", &n); err == nil {
			return n, true
		}
	}
	return 0, false
}
