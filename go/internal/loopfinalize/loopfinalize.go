// Package loopfinalize ports loop_finalize.py's Phase G —
// _build_result_and_finalize, the function that turns a finished run into
// the LoopResult its caller returns and writes the artifacts a human or a
// later run reads.
//
// # What this tranche takes, and what it leaves
//
// Ported here: the whole body of _build_result_and_finalize — the ordering
// of the terminal writes, the pre-flight calibration row, the loop_done
// event payload, the LoopResult construction, the RESULT/PARTIAL transcript
// render, the scratchpad dump, the two merge-back blocks and the shape they
// force onto a result, and the release/wake tail.
//
// NOT ported, and named rather than left to be inferred:
//
//   - _finalize_loop (loop_finalize.py:595) — the learning tail: outcome
//     ledger, reflection, introspection, diagnosis, Telegram. It is reached
//     through Deps.FinalizeLoop, which records the call.
//   - _write_plan_manifest / _write_run_report / _write_loop_log /
//     _write_runs_index — loop_artifacts and loop_report, both their own
//     tranches (loop_report alone is 2705 lines). Written WITHOUT the .py
//     suffix on purpose: tools/port-status.py reads a filename in a
//     production comment as a DECLARATION, and the first draft of this
//     paragraph moved two modules into the declared column by saying it
//     had not ported them.
//   - world_facts.land_facts, observe.write_event, memory_ledger and runs
//     stamps, the orch accessor, interrupt/heartbeat — all Deps.
//
// Three helpers of loop_finalize.py were ported in an earlier tranche and
// live in internal/loop: StepEvidence (_step_evidence), AutoPruneDays and
// CleanupStepArtifacts. This package does not duplicate them; the cleanup
// call is a Deps entry so the two type families do not have to meet.
//
// # Everything here is wrapped in try/except in Python, except what is not
//
// The exception discipline is load-bearing and it is NOT uniform. Four
// calls sit outside any try: _write_loop_log, o.append_decision,
// o.write_operator_status, and the LoopResult construction. An exception
// from those escapes _build_result_and_finalize and the loop dies without
// finalizing. Every other call is swallowed with a log line.
//
// So a nil Deps func means "the import failed" and takes the same except
// branch — for a swallowed call that is a log line and a continue, and for
// one of the four it is an error out of BuildResultAndFinalize. Making all
// of them swallow would be the friendlier port and the wrong one: a run
// whose loop log never got written must not return a LoopResult pointing at
// a file that is not there.
package loopfinalize

import (
	"fmt"
	"sort"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// MergeVerdictEvidenceCap is the clip() bound on the stop_evidence the two
// merge-back blocks write. Python passes the literal 800 at both sites.
const MergeVerdictEvidenceCap = 800

// TranscriptStepTextHead is Python's s.text[:100] in the transcript step
// heading, and TranscriptStepResultCap is s.result[:2000] in its body.
// Both count CODE POINTS, not bytes — see renderTranscript.
const (
	TranscriptStepTextHead  = 100
	TranscriptStepResultCap = 2000
)

// PreFlightFlag is the one field of a pre_flight flag this phase reads.
type PreFlightFlag struct{ Kind string }

// PreFlightReview is the slice of a pre_flight review that the
// calibration row reads. That module is not ported; a nil *PreFlightReview
// is Python's `pf_review is None`, which skips the row entirely.
type PreFlightReview struct {
	Scope                string
	MilestoneStepIndices []int
	Flags                []PreFlightFlag
}

// MergeResult is the shape both worktree.merge_back and
// worktree.merge_back_clone return. The port's own worktree package has a
// richer MergeResult; this is the three fields this phase reads, so the
// container lane (which reads only Ok/Detail) and the worktree lane (which
// also names the Branch) can share one Deps signature.
type MergeResult struct {
	Ok     bool
	Detail string
	Branch string
}

// CloneRef is what the container block prints when merge_back_clone itself
// RAISES: getattr(_clone, "path", "?") and getattr(_clone, "branch", "?").
// The getattr defaults are why these are plain strings with a Missing flag
// rather than a pointer — a clone object that lacks the attribute renders
// "?" and does not crash the handler that is already handling a crash.
type CloneRef struct {
	Path   string
	Branch string
}

// In carries the keyword arguments _build_result_and_finalize takes
// alongside ctx.
type In struct {
	StepOutcomes       []looptypes.StepOutcome
	LoopStatus         string
	StuckReason        *string
	TotalTokensIn      int
	TotalTokensOut     int
	InterruptsApplied  int
	MarchOfNinesAlert  bool
	PFReview           *PreFlightReview
	ManifestSteps      []string
	ReplanCount        int
	StartTS            string
	MilestoneExpanded  map[int]bool
	HadNoMatchingSkill bool
	FailureChain       []string
	RecoveryStepCount  int
	// Scratchpad is the run's scratchpad dict. Insertion order is the
	// order index.json lists, so it is an ordered Obj and not a map.
	Scratchpad     pyval.Obj
	ScratchpadLock sync.Locker
}

// Deps are the stateful surfaces this phase reaches. A nil func is the
// import having failed; see the package doc for which of those are fatal.
type Deps struct {
	// Monotonic is time.monotonic() in seconds; elapsed is measured from
	// ctx.StartedAt.
	Monotonic func() float64
	// NowISO is datetime.now(timezone.utc).isoformat(), for the
	// calibration row's ts.
	NowISO func() string

	WritePlanManifest func(ManifestIn) error
	WriteRunReport    func(ReportIn) error
	// WriteLoopLog returns the log path. NOT wrapped in Python.
	WriteLoopLog   func(LogIn) (string, error)
	WriteRunsIndex func(force bool) error

	// AppendDecision and WriteOperatorStatus are the orch accessor's two
	// calls. NOT wrapped in Python.
	AppendDecision      func(project string, lines []string) error
	WriteOperatorStatus func() error

	// MemoryDir is orch_items.memory_dir; LockedAppend is
	// file_lock.locked_append. Both feed preflight_calibration.jsonl.
	MemoryDir    func() (string, error)
	LockedAppend func(path, line string) error

	// WriteEvent is observe.write_event("loop_done", **kw).
	WriteEvent func(name string, kw pyval.Obj) error

	// ArtifactDir is runs.artifact_dir(project, project_root_fn=...). An
	// error falls back to ProjectDirRoot()/project/"artifacts" AND creates
	// it — the fallback mkdirs, the primary does not.
	ArtifactDir    func(project string) (string, error)
	ProjectDirRoot func() string

	// WriteFile / MakeDirs are the transcript and scratchpad writes.
	// MakeDirs takes parents so one func covers both the fallback's
	// mkdir(parents=True, exist_ok=True) and the scratchpad's
	// mkdir(exist_ok=True) — which differ, and the difference is a real
	// divergence: a scratchpad dir under a MISSING artifacts dir raises in
	// Python, and the surrounding except swallows the whole transcript.
	MakeDirs  func(dir string, parents bool) error
	WriteFile func(path, content string) error

	// LandFacts is world_facts.land_facts; the counts feed one log line.
	LandFacts func() (anecdotal, hypotheses int, err error)

	// FinalizeLoop is _finalize_loop — the learning tail, its own tranche.
	FinalizeLoop func(FinalizeIn) error

	// CleanupStepArtifacts is loop.CleanupStepArtifacts, reached through
	// Deps because that port lives in a package with its own StepOutcome.
	CleanupStepArtifacts func(project, excludeLoopID string) int

	// The container-clone lane. MergeBackClone returning an error is
	// Python's merge_back_clone RAISING, which is a different branch from
	// it returning ok=False.
	MergeBackClone func(message string) (MergeResult, error)
	CleanupClone   func(keepOnFailure bool) error
	CloneRef       func() CloneRef

	// The run-worktree lane.
	MergeBack     func(message string) (MergeResult, error)
	CleanupWT     func(keepOnFailure bool) error
	PruneWT       func() error
	StampOutcome  func(loopID, verdict, evidence string) error
	StampRunStop  func(verdict, evidence, pauseReason string) error
	ReleaseSlot   func() error
	ReleaseLease  func() error
	ClearRunning  func() error
	PostHeartbeat func(eventType, payload string) error

	// Verbose print target: `print(f"[maro] {result.summary()}",
	// file=sys.stderr)`.
	Stderr func(line string)

	// Warn and Debug take the message ALREADY formatted, matching what a
	// logging handler sees after %-interpolation.
	Warn  func(msg string)
	Debug func(msg string)
	Info  func(msg string)
}

// ManifestIn / ReportIn / LogIn / FinalizeIn are the keyword sets the four
// unported writers are called with. They exist so a differential can assert
// the ARGUMENTS, not just that a call happened — a manifest written with
// the wrong status is the failure this phase is most likely to have.
type ManifestIn struct {
	Project      string
	LoopID       string
	Goal         string
	PlannedSteps []string
	StartTS      string
	StepOutcomes []looptypes.StepOutcome
	Status       string
	ElapsedMS    int
	ReplanCount  int
}

type ReportIn struct {
	ManifestIn
	Injections []pyval.Obj
}

type LogIn struct {
	Project     string
	LoopID      string
	Goal        string
	Status      string
	Steps       []looptypes.StepOutcome
	StartTS     string
	ElapsedMS   int
	StuckReason *string
	Injections  []pyval.Obj
}

type FinalizeIn struct {
	LoopID           string
	Goal             string
	Project          string
	LoopStatus       string
	StepOutcomes     []looptypes.StepOutcome
	DryRun           bool
	Verbose          bool
	TotalTokensIn    int
	TotalTokensOut   int
	ElapsedMS        int
	HadNoMatching    bool
	FailureChain     []string
	RecoverySteps    int
	DeferLearning    bool
	DeferMaintenance bool
	MeasurementClass string
	HandleID         string
	StopVerdict      string
	StopEvidence     string
	PauseReason      string
}

func (d Deps) warn(format string, a ...any) {
	if d.Warn != nil {
		d.Warn(fmt.Sprintf(format, a...))
	}
}

func (d Deps) debug(format string, a ...any) {
	if d.Debug != nil {
		d.Debug(fmt.Sprintf(format, a...))
	}
}

func (d Deps) info(format string, a ...any) {
	if d.Info != nil {
		d.Info(fmt.Sprintf(format, a...))
	}
}

// errImport is what a nil Deps func reports, standing in for the
// ImportError Python's inline `from X import Y` raises when the module is
// missing. It only ever reaches a log line.
var errImport = fmt.Errorf("no module")

// BuildResultAndFinalize is _build_result_and_finalize: build the final
// LoopResult, write the terminal artifacts, run the finalize side effects.
func BuildResultAndFinalize(ctx *looptypes.LoopContext, in In, d Deps) (*looptypes.LoopResult, error) {
	elapsedTotal := 0
	if d.Monotonic != nil {
		elapsedTotal = int((d.Monotonic() - ctx.StartedAt) * 1000)
	}

	// Write final plan manifest with terminal status and elapsed time.
	if ctx.Project != "" && len(in.ManifestSteps) > 0 {
		mi := ManifestIn{
			Project:      ctx.Project,
			LoopID:       ctx.LoopID,
			Goal:         ctx.Goal,
			PlannedSteps: in.ManifestSteps,
			StartTS:      in.StartTS,
			StepOutcomes: in.StepOutcomes,
			Status:       in.LoopStatus,
			ElapsedMS:    elapsedTotal,
			ReplanCount:  in.ReplanCount,
		}
		if err := call(d.WritePlanManifest, mi); err != nil {
			d.warn("plan manifest write failed (affects replay/debugging): %s", err)
		}
		ri := ReportIn{ManifestIn: mi, Injections: cloneInjections(ctx.Injections)}
		if err := call(d.WriteRunReport, ri); err != nil {
			d.warn("run report final write failed: %s", err)
		}
	}

	// NOT wrapped in Python: a loop log that cannot be written must not
	// produce a LoopResult claiming a log_path.
	if d.WriteLoopLog == nil {
		return nil, errImport
	}
	logPath, err := d.WriteLoopLog(LogIn{
		Project:     ctx.Project,
		LoopID:      ctx.LoopID,
		Goal:        ctx.Goal,
		Status:      in.LoopStatus,
		Steps:       in.StepOutcomes,
		StartTS:     in.StartTS,
		ElapsedMS:   elapsedTotal,
		StuckReason: in.StuckReason,
		Injections:  cloneInjections(ctx.Injections),
	})
	if err != nil {
		return nil, err
	}

	// 2026-07-08 adversarial review (finding #3): the index reads totals
	// from this run's build/loop-*-log.json, so the forced write has to
	// happen AFTER the loop log above, not before it — otherwise the
	// just-finished run's own totals are missing from its own index entry.
	if d.WriteRunsIndex == nil {
		d.warn("runs index write failed: %s", errImport)
	} else if err := d.WriteRunsIndex(true); err != nil {
		d.warn("runs index write failed: %s", err)
	}

	// Both NOT wrapped in Python.
	if d.AppendDecision == nil {
		return nil, errImport
	}
	if err := d.AppendDecision(ctx.Project, []string{fmt.Sprintf(
		"[loop:%s] finished status=%s steps=%d tokens=%d+%d",
		ctx.LoopID, in.LoopStatus, len(in.StepOutcomes),
		in.TotalTokensIn, in.TotalTokensOut)}); err != nil {
		return nil, err
	}
	if d.WriteOperatorStatus == nil {
		return nil, errImport
	}
	if err := d.WriteOperatorStatus(); err != nil {
		return nil, err
	}

	if in.PFReview != nil && !ctx.DryRun {
		if err := writeCalibration(ctx, in, d); err != nil {
			d.debug("pre-flight calibration feedback write failed: %s", err)
		}
	}

	// Phase 36: emit loop_done event.
	if d.WriteEvent == nil {
		d.debug("loop_done observe event failed: %s", errImport)
	} else {
		ev := pyval.Obj{}
		ev.Set("goal", ctx.Goal)
		ev.Set("project", ctx.Project)
		ev.Set("loop_id", ctx.LoopID)
		ev.Set("status", in.LoopStatus)
		ev.Set("tokens_in", in.TotalTokensIn)
		ev.Set("tokens_out", in.TotalTokensOut)
		ev.Set("elapsed_ms", elapsedTotal)
		ev.Set("detail", derefOr(in.StuckReason, ""))
		if err := d.WriteEvent("loop_done", ev); err != nil {
			d.debug("loop_done observe event failed: %s", err)
		}
	}

	result := &looptypes.LoopResult{
		LoopID:             ctx.LoopID,
		Project:            ctx.Project,
		Goal:               ctx.Goal,
		Status:             in.LoopStatus,
		Steps:              in.StepOutcomes,
		InterruptsApplied:  in.InterruptsApplied,
		Injections:         cloneInjections(ctx.Injections),
		StuckReason:        in.StuckReason,
		StopVerdict:        ctx.StopVerdict,
		StopEvidence:       ctx.StopEvidence,
		PauseReason:        ctx.PauseReason,
		TotalTokensIn:      in.TotalTokensIn,
		TotalTokensOut:     in.TotalTokensOut,
		ElapsedMS:          elapsedTotal,
		LogPath:            &logPath,
		MarchOfNinesAlert:  in.MarchOfNinesAlert,
		PreFlightReview:    in.PFReview,
		HadNoMatchingSkill: in.HadNoMatchingSkill,
	}

	writeTranscript(ctx, in, d, result, elapsedTotal)

	if ctx.Verbose && d.Stderr != nil {
		d.Stderr(fmt.Sprintf("[maro] %s", result.Summary()))
	}

	// World-facts slice 2: land the run's declared facts before
	// FinalizeLoop so the bridge's extraction pass sees any node the facts
	// minted and dedups against it. Verdict-independent on purpose — a
	// failed run's "archive X is blocked" is still a fact.
	if d.LandFacts == nil {
		d.debug("world-facts landing failed (non-critical): %s", errImport)
	} else if anec, hyp, err := d.LandFacts(); err != nil {
		d.debug("world-facts landing failed (non-critical): %s", err)
	} else if anec != 0 || hyp != 0 {
		d.info("world_facts landed: %d anecdotal, %d hypothesis", anec, hyp)
	}

	// NOT wrapped in Python.
	if d.FinalizeLoop == nil {
		return nil, errImport
	}
	if err := d.FinalizeLoop(FinalizeIn{
		LoopID:           ctx.LoopID,
		Goal:             ctx.Goal,
		Project:          ctx.Project,
		LoopStatus:       in.LoopStatus,
		StepOutcomes:     in.StepOutcomes,
		DryRun:           ctx.DryRun,
		Verbose:          ctx.Verbose,
		TotalTokensIn:    in.TotalTokensIn,
		TotalTokensOut:   in.TotalTokensOut,
		ElapsedMS:        elapsedTotal,
		HadNoMatching:    in.HadNoMatchingSkill,
		FailureChain:     in.FailureChain,
		RecoverySteps:    in.RecoveryStepCount,
		DeferLearning:    ctx.DeferLearning,
		DeferMaintenance: ctx.DeferMaintenance,
		MeasurementClass: ctx.MeasurementClass,
		HandleID:         ctx.HandleID,
		StopVerdict:      ctx.StopVerdict,
		StopEvidence:     ctx.StopEvidence,
		PauseReason:      ctx.PauseReason,
	}); err != nil {
		return nil, err
	}

	// Artifact retention (decree, 2026-07-10): per-step artifacts are KEPT
	// by default. Pruning is a user opt-in, and even then the
	// just-finished loop's files are never touched — the closure/goal
	// verdict is judged AFTER the loop returns.
	if !ctx.DryRun && ctx.Project != "" && d.CleanupStepArtifacts != nil {
		d.CleanupStepArtifacts(ctx.Project, ctx.LoopID)
	}

	// The outcome-ledger row is already written; if a merge-back block
	// below adds a verdict, re-stamp that row post-hoc so ledger consumers
	// do not read a merge-failed run as a clean pre-merge ending.
	preMergeVerdict := result.StopVerdict

	mergeBackClone(ctx, d, result)
	mergeBackWorktree(ctx, d, result)

	// EQUIVALENT MUTANT: dropping the != "" clause changes nothing that
	// any input can observe. Nothing in this phase CLEARS a verdict — the
	// two merge lanes only ever set one on a result that had none — so
	// StopVerdict differing from the pre-merge snapshot already implies it
	// is non-empty. The clause is defensive in Python and stays defensive
	// here rather than being tidied into a claim about what cannot happen.
	if result.StopVerdict != "" && result.StopVerdict != preMergeVerdict {
		if d.StampOutcome == nil {
			d.debug("post-merge ledger stop-verdict stamp failed: %s", errImport)
		} else if err := d.StampOutcome(ctx.LoopID, result.StopVerdict, result.StopEvidence); err != nil {
			d.debug("post-merge ledger stop-verdict stamp failed: %s", err)
		}
	}

	// Persist the typed stop verdict where run_curation reads. Stamped
	// unconditionally: an empty verdict CLEARS a stale one from an earlier
	// restarted loop, so metadata always reflects THIS ending.
	if d.StampRunStop == nil {
		d.debug("stop-verdict metadata stamp failed: %s", errImport)
	} else if err := d.StampRunStop(result.StopVerdict, result.StopEvidence, result.PauseReason); err != nil {
		d.debug("stop-verdict metadata stamp failed: %s", err)
	}

	// Release loop lock — the admission slot first (per-project flock),
	// then the global informational lockfile.
	// Python clears the field only AFTER release() returns: a release that
	// raises leaves the slot on the context, so a caller can see it was
	// never given back. Clearing first would hide a stuck flock.
	if ctx.ProjectSlot != nil {
		if err := releaseOrImport(d.ReleaseSlot); err != nil {
			d.debug("project slot release failed: %s", err)
		} else {
			ctx.ProjectSlot = nil
		}
	}
	if ctx.RunLease != nil {
		if err := releaseOrImport(d.ReleaseLease); err != nil {
			d.debug("run lease release failed: %s", err)
		} else {
			ctx.RunLease = nil
		}
	}
	if d.ClearRunning == nil {
		d.debug("clear_loop_running failed: %s", errImport)
	} else if err := d.ClearRunning(); err != nil {
		d.debug("clear_loop_running failed: %s", err)
	}

	// Signal heartbeat to wake immediately — pick up the next queued task
	// without waiting for the full interval tick.
	if d.PostHeartbeat == nil {
		d.debug("post_heartbeat_event(loop_done) failed: %s", errImport)
	} else if err := d.PostHeartbeat("loop_done", ctx.Project); err != nil {
		d.debug("post_heartbeat_event(loop_done) failed: %s", err)
	}

	return result, nil
}

func releaseOrImport(fn func() error) error {
	if fn == nil {
		return errImport
	}
	return fn()
}

// call runs one optional Deps func, reporting a nil as the import failure.
func call[T any](fn func(T) error, arg T) error {
	if fn == nil {
		return errImport
	}
	return fn(arg)
}

// writeCalibration is the Phase 58 pre-flight calibration row.
func writeCalibration(ctx *looptypes.LoopContext, in In, d Deps) error {
	if d.MemoryDir == nil || d.LockedAppend == nil || d.NowISO == nil {
		return errImport
	}
	pf := in.PFReview
	predictedWide := pf.Scope == "wide" || pf.Scope == "deep"
	actualStuck := in.LoopStatus == "stuck"
	stepsDone := countDone(in.StepOutcomes)

	// Per-kind firing counts (claims-audit round 2026-08-16: the class-gap
	// dimension's residue deferred efficacy to this lane, whose schema
	// recorded only the aggregate). Firing counts make per-kind
	// adjudication POSSIBLE; accuracy judgment stays adjudication-side.
	//
	// `for k in sorted({f.kind for f in flags})` — a SET, so duplicate
	// kinds collapse before sorting, and the sort is over the distinct
	// kinds. JSON object order follows that sort, which is why this builds
	// an ordered Obj rather than a map.
	seen := map[string]int{}
	for _, f := range pf.Flags {
		seen[f.Kind]++
	}
	kinds := make([]string, 0, len(seen))
	for k := range seen {
		kinds = append(kinds, k)
	}
	sort.Strings(kinds)
	flagKinds := pyval.Obj{}
	for _, k := range kinds {
		flagKinds.Set(k, seen[k])
	}

	entry := pyval.Obj{}
	entry.Set("ts", d.NowISO())
	entry.Set("loop_id", ctx.LoopID)
	entry.Set("scope_predicted", pf.Scope)
	entry.Set("milestone_candidates", len(pf.MilestoneStepIndices))
	entry.Set("milestones_expanded", len(in.MilestoneExpanded))
	entry.Set("flag_count", len(pf.Flags))
	entry.Set("flag_kinds", flagKinds)
	entry.Set("actual_status", in.LoopStatus)
	entry.Set("steps_done", stepsDone)
	entry.Set("steps_total", len(in.StepOutcomes))
	entry.Set("true_positive", predictedWide && actualStuck)
	entry.Set("false_positive", predictedWide && !actualStuck)
	entry.Set("false_negative", !predictedWide && actualStuck)
	entry.Set("true_negative", !predictedWide && !actualStuck)

	line, err := pyval.DumpsCompactPy(entry)
	if err != nil {
		return err
	}
	dir, err := d.MemoryDir()
	if err != nil {
		return err
	}
	if err := d.LockedAppend(dir+"/preflight_calibration.jsonl", line); err != nil {
		return err
	}
	d.info("pre-flight calibration: scope=%s actual=%s tp=%s fp=%s fn=%s",
		pf.Scope, in.LoopStatus,
		pyBool(predictedWide && actualStuck),
		pyBool(predictedWide && !actualStuck),
		pyBool(!predictedWide && actualStuck))
	return nil
}

// pyBool renders a bool the way %s does in Python's logging call, which
// takes str(True) — not Go's "true".
func pyBool(b bool) string {
	if b {
		return "True"
	}
	return "False"
}

// RenderTranscript is the RESULT/PARTIAL body. Exported because it is the
// user-facing artifact of a run and worth testing without the twelve Deps
// around it. Returns the kind ("RESULT" or "PARTIAL") and the file body.
//
// The step heading numbers by POSITION (_pos, 1-based) and reports s.index
// separately as "ledger #N": s.index is the NEXT.md ledger line, not the
// plan position — it starts wherever the project ledger left off, so
// rendering it as the step number read as "Step 11 of a 4-step plan".
func RenderTranscript(goal, loopStatus string, outcomes []looptypes.StepOutcome,
	stuckReason *string, tokensIn, tokensOut, elapsedMS int) (string, string) {
	kind := "PARTIAL"
	head := "Partial result"
	if loopStatus == "done" {
		kind = "RESULT"
		head = "Result"
	}
	doneSteps := countDone(outcomes)

	// Each entry is one element of a list joined with "\n", and several
	// entries carry their OWN trailing "\n" — so the blank lines in the
	// rendered file are the join and the embedded newline together, not a
	// paragraph style anyone chose. Reproducing the list exactly is the
	// only way to reproduce the spacing.
	var lines []string
	lines = append(lines, fmt.Sprintf("# %s: %s\n", head, goal))
	lines = append(lines, fmt.Sprintf(
		"Status: %s | %d/%d steps done | tokens: %d | elapsed: %dms\n",
		loopStatus, doneSteps, len(outcomes), tokensIn+tokensOut, elapsedMS))
	if derefOr(stuckReason, "") != "" {
		lines = append(lines, fmt.Sprintf("Stuck reason: %s\n", *stuckReason))
	}
	lines = append(lines, "---\n")
	for i, s := range outcomes {
		icon := "BLOCKED"
		if s.Status == "done" {
			icon = "Done"
		}
		lines = append(lines, fmt.Sprintf("\n## Step %d/%d (ledger #%d): %s",
			i+1, len(outcomes), s.Index, runeHead(s.Text, TranscriptStepTextHead)))
		lines = append(lines, fmt.Sprintf("*[%s]*\n", icon))
		if s.Result != "" {
			lines = append(lines, runeHead(s.Result, TranscriptStepResultCap))
			// len() on a str is code points, so the "chars total" figure
			// and the cut are counted in the same unit. Reporting bytes
			// here would tell a reader 4,000 characters were cut from a
			// 2,000-character result.
			if n := len([]rune(s.Result)); n > TranscriptStepResultCap {
				lines = append(lines, fmt.Sprintf(
					"\n... (truncated, %d chars total)", n))
			}
		}
		lines = append(lines, "")
	}
	return kind, strings.Join(lines, "\n")
}

// writeTranscript is the transcript + scratchpad block. The whole thing is
// one try/except in Python, so a failure ANYWHERE in it — including the
// scratchpad dump at the end — loses the transcript's log line and leaves
// no other trace than one debug entry.
func writeTranscript(ctx *looptypes.LoopContext, in In, d Deps,
	result *looptypes.LoopResult, elapsedTotal int) {
	doneSteps := countDone(in.StepOutcomes)
	// Gated on a DONE step existing, not on the run being done: a stuck
	// run that finished two of five steps still writes PARTIAL.md.
	if doneSteps == 0 {
		return
	}

	if err := writeTranscriptInner(ctx, in, d, elapsedTotal, doneSteps); err != nil {
		d.debug("partial result write failed: %s", err)
	}
}

// countDone is `[s for s in step_outcomes if s.status == "done"]` — three
// sites needed it and each had its own loop, which is three places for the
// status literal to drift to.
func countDone(outcomes []looptypes.StepOutcome) int {
	n := 0
	for _, s := range outcomes {
		if s.Status == "done" {
			n++
		}
	}
	return n
}

func writeTranscriptInner(ctx *looptypes.LoopContext, in In, d Deps,
	elapsedTotal, doneSteps int) error {
	kind, body := RenderTranscript(ctx.Goal, in.LoopStatus, in.StepOutcomes,
		in.StuckReason, in.TotalTokensIn, in.TotalTokensOut, elapsedTotal)

	// runs.artifact_dir first; its failure falls back to the project dir
	// AND creates it. The primary path does not mkdir here because
	// artifact_dir already did.
	var artDir string
	resolved := false
	if d.ArtifactDir != nil {
		// The fallback is Python's `except`, and only that: an
		// artifact_dir that RETURNS something takes the primary path even
		// if what it returned is empty. Testing `artDir == ""` instead
		// would fold a returned "" into the failure branch, where Python
		// would go on to use Path("") — which is `.`, not an error.
		//
		// EQUIVALENT MUTANT BY CORPUS, deliberately left that way:
		// adding `&& dir != ""` survives the battery because nothing
		// makes artifact_dir answer "". A fixture for it would have to
		// make the PYTHON side write its transcript relative to the
		// process CWD — outside the test's temp root, into the checkout —
		// and a test that can leave files in the repository is a worse
		// trade than an unpinned line. The reason it matters at all is
		// recorded in the finalize-helpers tranche: Path("") is `.`.
		if dir, err := d.ArtifactDir(ctx.Project); err == nil {
			artDir, resolved = dir, true
		}
	}
	if !resolved {
		if d.ProjectDirRoot == nil || d.MakeDirs == nil {
			return errImport
		}
		artDir = d.ProjectDirRoot() + "/" + ctx.Project + "/artifacts"
		if err := d.MakeDirs(artDir, true); err != nil {
			return err
		}
	}

	if d.WriteFile == nil {
		return errImport
	}
	name := fmt.Sprintf("loop-%s-%s.md", ctx.LoopID, kind)
	if err := d.WriteFile(artDir+"/"+name, body); err != nil {
		return err
	}
	d.info("wrote loop transcript: %s (%d steps)", name, doneSteps)

	// Persist scratchpad. mkdir(exist_ok=True) — NOT parents=True, so a
	// missing artifacts dir raises here rather than being created.
	scratchDir := fmt.Sprintf("%s/loop-%s-scratchpad", artDir, ctx.LoopID)
	if d.MakeDirs == nil {
		return errImport
	}
	// EQUIVALENT MUTANT: passing parents=true here is unobservable. The
	// artifacts dir is guaranteed to exist by this point — the transcript
	// was just written INTO it — so mkdir-one-level and mkdir-p agree on
	// every reachable input. The false is still the honest spelling: it
	// is what Python wrote, and if a caller ever hands this phase an
	// artifact dir it did not create, the two stop agreeing.
	if err := d.MakeDirs(scratchDir, false); err != nil {
		return err
	}
	if in.ScratchpadLock != nil {
		in.ScratchpadLock.Lock()
		defer in.ScratchpadLock.Unlock()
	}
	keys := make([]any, 0, len(in.Scratchpad))
	for _, f := range in.Scratchpad {
		keys = append(keys, f.Key)
		// json.dumps(v, indent=2, default=str). The default= arm has no
		// Go twin and needs none: it is reached only by a value json
		// cannot encode, and a scratchpad that holds one is a different
		// finding from a rendering difference. DumpsIndent2 errors there
		// instead, which takes the same outer except as Python's own
		// TypeError would if default= were absent.
		text, err := pyval.DumpsIndent2(f.Val)
		if err != nil {
			return err
		}
		if err := d.WriteFile(fmt.Sprintf("%s/%s.json", scratchDir, f.Key), text); err != nil {
			return err
		}
	}
	idx := pyval.Obj{}
	idx.Set("keys", pyval.List(keys))
	text, err := pyval.DumpsIndent2(idx)
	if err != nil {
		return err
	}
	return d.WriteFile(scratchDir+"/index.json", text)
}

// mergeBackClone is the containerized self-dev block (C3,
// CONTAINER_EXECUTOR_DESIGN §4): merge the worker's scratch clone back into
// the fence repo FIRST — the clone's parent is the fence dir, so
// clone->fence must land before any fence->project merge below.
func mergeBackClone(ctx *looptypes.LoopContext, d Deps, result *looptypes.LoopResult) {
	if ctx.ContainerClone == nil {
		return
	}
	if d.MergeBackClone == nil {
		d.warn("container scratch-clone finalize error: %s", errImport)
		ctx.ContainerClone = nil
		return
	}
	m, err := d.MergeBackClone(fmt.Sprintf("container: run %s", ctx.LoopID))
	if err == nil {
		if d.CleanupClone == nil {
			err = errImport
		} else {
			err = d.CleanupClone(!m.Ok)
		}
	}
	if err != nil {
		// A merge-back exception must NOT be reported as a clean 'done' —
		// the worker's clone work never reached the fence. Downgrade and
		// name the retained clone/branch so nothing is silently lost
		// (adversarial-review 2026-07-13, finding A6). The clone is left
		// on disk: no cleanup here.
		d.warn("container scratch-clone finalize error: %s", err)
		ref := CloneRef{Path: "?", Branch: "?"}
		if d.CloneRef != nil {
			ref = d.CloneRef()
		}
		downgrade(result, fmt.Sprintf(
			"container clone merge errored — work preserved in %s (branch %s): %s",
			ref.Path, ref.Branch, err),
			fmt.Sprintf("container clone merge errored: %s", err))
		ctx.ContainerClone = nil
		return
	}
	if !m.Ok {
		d.warn("container scratch-clone merge failed: %s", m.Detail)
		downgrade(result,
			"container clone merge failed — work preserved: "+m.Detail,
			"container clone merge failed: "+m.Detail)
	}
	ctx.ContainerClone = nil
}

// mergeBackWorktree is busy_policy=worktree: merge the run's isolated
// worktree back into the project checkout before releasing the slot.
// Conflict never drops work — the branch is preserved and the run
// downgrades to "partial" naming it.
func mergeBackWorktree(ctx *looptypes.LoopContext, d Deps, result *looptypes.LoopResult) {
	if ctx.RunWorktree == nil {
		return
	}
	if err := mergeBackWorktreeInner(ctx, d, result); err != nil {
		// Python logs and moves on WITHOUT downgrading — see PORT.md.
		d.warn("run worktree finalize error: %s", err)
	}
	ctx.RunWorktree = nil
}

func mergeBackWorktreeInner(ctx *looptypes.LoopContext, d Deps, result *looptypes.LoopResult) error {
	if d.MergeBack == nil || d.CleanupWT == nil || d.PruneWT == nil {
		return errImport
	}
	m, err := d.MergeBack(fmt.Sprintf("wt: run %s", ctx.LoopID))
	if err != nil {
		return err
	}
	if err := d.CleanupWT(!m.Ok); err != nil {
		return err
	}
	if err := d.PruneWT(); err != nil {
		return err
	}
	if !m.Ok {
		d.warn("run worktree merge failed: %s", m.Detail)
		msg := fmt.Sprintf("worktree merge failed — work preserved on %s: %s",
			m.Branch, m.Detail)
		downgrade(result, msg, msg)
	}
	return nil
}

// downgrade applies the shape both merge-back failures force onto a result:
// done becomes partial, the reason is APPENDED with "; " rather than
// replacing what the run already said, and a run with no verdict yet gets
// external-interrupt — landing machinery failed after the goal work, which
// is infra, not a mid-goal shortfall (the survey merged them under
// "partial").
//
// The verdict and evidence are set together and only when stop_verdict is
// empty, so a run that already stamped its own verdict keeps BOTH halves.
// Setting the evidence unconditionally would leave a verdict describing one
// ending next to evidence describing another.
func downgrade(result *looptypes.LoopResult, reason, evidence string) {
	if result.Status == "done" {
		result.Status = "partial"
	}
	prev := derefOr(result.StuckReason, "")
	if prev != "" {
		prev += "; "
	}
	joined := prev + reason
	result.StuckReason = &joined
	if result.StopVerdict == "" {
		result.StopVerdict = "external-interrupt"
		result.StopEvidence = budget.Clip(evidence, MergeVerdictEvidenceCap)
	}
}

// runeHead is Python s[:n] on a str: n CODE POINTS, not bytes. The port has
// pyval.Clip for the same cut; this is the marker-free twin, and the
// transcript writes its own truncation note in a different format.
func runeHead(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

func derefOr(p *string, def string) string {
	if p == nil {
		return def
	}
	return *p
}

func cloneInjections(v []pyval.Obj) []pyval.Obj {
	// Python writes list(ctx.injections) at three sites — a SHALLOW copy,
	// so the list is snapshotted and the objects in it are not.
	out := make([]pyval.Obj, len(v))
	copy(out, v)
	return out
}
