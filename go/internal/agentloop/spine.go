package agentloop

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The orchestration spine: `src/agent_loop.py:434-712`, the region between
// the execution fence and the return.
//
// Almost none of the work is here. Phases B through G are one call each
// into a module this package does not own, and what the spine contributes
// is the ORDER of those calls, the gates that skip them, the ARGUMENTS it
// computes for them, and where each failure is caught. That is exactly the
// part of a port that goes wrong silently, so all six phase functions are
// injected and the differential compares the calls and their keywords.
//
// Two shapes are worth naming before the code:
//
//   - Three of the values the spine passes on are REBOUND from the context
//     twenty lines above it (`project = ctx.project`, `adapter`,
//     `interrupt_queue`). A port that reads the caller's keyword there is
//     wrong in a way no type checker sees — the same bug the fence had with
//     `project`, and it appears here three more times, in the arguments of
//     the auto-recovery re-run.
//   - `goal` and `max_iterations` are rebound AGAIN, out of the execute
//     phase's result dict. The auto-recovery re-run passes those, not the
//     caller's. A run whose executor rewrote the goal recovers on the
//     REWRITTEN one.
//
// The dict unpacks are modelled as dict reads rather than as struct fields
// on purpose: Python reads thirteen keys out of `_pf` and seventeen out of
// `_ex` in a fixed order, and a missing one is a KeyError naming the FIRST
// key that was absent. A struct cannot be missing a field.

// SpineArgs is the part of run_agent_loop's signature the spine reads.
//
// Project, Adapter and InterruptQueue are deliberately ABSENT: those three
// keywords are rebound from the context before the spine starts, so the
// spine reads ctx and the recursion passes ctx's value.
type SpineArgs struct {
	PresetSteps          any
	MaxSteps             any
	KnowledgeSubGoals    any
	PermissionContext    any
	ResumeFromLoopID     any
	ParallelFanOut       any
	Model                any
	HookRegistry         any
	AncestryContextExtra any
	StepCallback         any
	TokenBudget          any
	// DryRun and Verbose are the caller's KEYWORDS and are never rebound
	// from the context, unlike Project/Adapter/InterruptQueue.
	// _initialize_loop copies dry_run onto ctx, so the two agree in
	// production — and the auto-recovery gate reads the ARGUMENT.
	DryRun  bool
	Verbose any
	// RecoveryInProgress is the `_recovery_in_progress` guard. It is a
	// call-stack-local argument and NOT shared state, so concurrent
	// run_agent_loop calls cannot race each other out of a recovery.
	RecoveryInProgress bool
	// TierOrder is `_TIER_ORDER`, built above the fence from the three llm
	// model constants. It is threaded into the execute phase unchanged.
	TierOrder any
}

// SpineDeps is every module boundary the spine crosses.
//
// A nil function is Python's ImportError at the statement that would have
// bound it — which is not the same failure everywhere: the parallel trace
// block logs at DEBUG, the parallel report block logs at WARNING, and the
// auto-recovery block swallows ImportError SILENTLY while logging any
// other exception at DEBUG.
type SpineDeps struct {
	Info  func(string)
	Warn  func(string)
	Debug func(string)

	// PhaseTrace is what ctx.set_phase records through.
	PhaseTrace looptypes.TraceFunc

	// GetToolsForRole is `_get_tools_for_role`, re-queried on EVERY call so
	// a tool registered at runtime is visible to the step that follows it.
	// ExecuteTools is the `_EXECUTE_TOOLS` fallback for a run with no
	// permission context.
	GetToolsForRole func(role, denyPatterns any) ([]any, error)
	ExecuteTools    []any

	// The six phase functions. Each takes its keywords as an ordered Obj so
	// the differential can compare what the spine computed, and returns
	// what Python returns: a tuple for the three that return tuples, a dict
	// for the two that return dicts, a LoopResult for finalize.
	DecomposeGoal          func(kw pyval.Obj) ([]any, error)
	PreflightChecks        func(kw pyval.Obj) ([]any, error)
	RunParallelPath        func(kw pyval.Obj) (*looptypes.LoopResult, error)
	PrepareExecution       func(kw pyval.Obj) ([]any, error)
	ExecuteMainLoop        func(kw pyval.Obj) (map[string]any, error)
	BuildResultAndFinalize func(kw pyval.Obj) (*looptypes.LoopResult, error)

	// run_trace.record_edge, imported separately in two places.
	RecordEdge func(from, to string, kw pyval.Obj) error

	// loop_report's two writers, imported together inside one try;
	// WriteLoopLog is a MODULE-LEVEL import (loop_artifacts) and is
	// therefore never the thing that is missing.
	WriteRunReport func(kw pyval.Obj) error
	WriteRunsIndex func(force bool) error
	WriteLoopLog   func(kw pyval.Obj) error

	ConfigGet    func(key string, def any) (any, error)
	ArmCostMeter func(ceiling float64) (func() error, error)

	DiagnoseLoop func(loopID string) (any, error)
	PlanRecovery func(diag any) (any, error)
	LogEvent     func(kw pyval.Obj) error
	RunAgentLoop func(kw pyval.Obj) (*looptypes.LoopResult, error)

	// AbsentModules distinguishes a missing MODULE from a missing NAME. A
	// nil dep alone cannot say which, and the two raise different messages
	// into the same handler — and for the auto-recovery block they reach
	// two DIFFERENT handlers, because only ImportError is swallowed there.
	AbsentModules map[string]bool

	// Recovery reads the plan through these rather than through an
	// interface, because Python reads ATTRIBUTES and a missing one is an
	// AttributeError inside the block that swallows.
	RecoveryAutoApply    func(rec any) (bool, error)
	RecoveryRisk         func(rec any) (string, error)
	RecoveryAction       func(rec any) (string, error)
	RecoveryParams       func(rec any) (pyval.Obj, error)
	DiagFailureClass     func(diag any) (string, error)
	AutoRecoveryEventKey string
}

func (d SpineDeps) info(format string, a ...any) {
	if d.Info != nil {
		d.Info(fmt.Sprintf(format, a...))
	}
}

func (d SpineDeps) warnf(format string, a ...any) {
	if d.Warn != nil {
		d.Warn(fmt.Sprintf(format, a...))
	}
}

func (d SpineDeps) debugf(format string, a ...any) {
	if d.Debug != nil {
		d.Debug(fmt.Sprintf(format, a...))
	}
}

// importErr is the failure of a `from X import y` the spine performs. The
// CLASS matters: the auto-recovery block catches ImportError and lets
// everything else fall to a different handler, and ModuleNotFoundError is
// a SUBCLASS of ImportError, so a missing module lands in the same arm as a
// missing name.
func (d SpineDeps) importErr(module string) error {
	if d.AbsentModules[module] {
		return moduleNotFound(module)
	}
	// The same controlled ImportError the fence uses. CPython's real
	// message for a missing NAME interpolates the module's FILE path,
	// which no differential can reproduce, so the probe raises this one
	// from a stub's __getattr__ and the port spells it the same way.
	return errImportFence
}

// keyError is `_pf["levels"]` against a dict that has no such key. str() of
// a KeyError is the repr of its argument, which is why the message carries
// the quotes.
func keyError(key string) error {
	return &pyval.PyErr{Class: "KeyError", Msg: pytext.Repr(key)}
}

// unpack is tuple assignment: `a, b, c = f()`. CPython reports the two
// failures differently, and both name the expected count.
func unpack(vals []any, n int) error {
	if len(vals) < n {
		return &pyval.PyErr{Class: "ValueError",
			Msg: fmt.Sprintf(
				"not enough values to unpack (expected %d, got %d)",
				n, len(vals))}
	}
	if len(vals) > n {
		// 3.11+ names the actual count in BOTH directions; older CPython
		// stopped at "(expected %d)" for this arm only.
		return &pyval.PyErr{Class: "ValueError",
			Msg: fmt.Sprintf("too many values to unpack (expected %d, got %d)",
				n, len(vals))}
	}
	return nil
}

// dictGet is `d[key]` — the read order is what a missing key reports.
func dictGet(m map[string]any, key string) (any, error) {
	v, ok := m[key]
	if !ok {
		return nil, keyError(key)
	}
	return v, nil
}

// toFloat is `float(x)`. The two failures carry different classes and
// different messages, and both land in the runaway circuit's warning.
func toFloat(v any) (float64, error) {
	switch t := v.(type) {
	case float64:
		return t, nil
	case int:
		return float64(t), nil
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case string:
		var f float64
		if _, err := fmt.Sscanf(t, "%g", &f); err != nil {
			return 0, &pyval.PyErr{Class: "ValueError",
				Msg: "could not convert string to float: " +
					pytext.Repr(t)}
		}
		return f, nil
	}
	return 0, &pyval.PyErr{Class: "TypeError",
		Msg: "float() argument must be a string or a real number, not " +
			pytext.Repr(pyval.TypeName(v))}
}

// RunSpine is `run_agent_loop` from the end of the execution fence to its
// return. A non-nil error is an exception leaving run_agent_loop: nothing
// below wraps the whole body, so an InvalidTransitionError from set_phase,
// a KeyError from a phase result, or anything a phase function raises is
// the run's ending.
func RunSpine(ctx *looptypes.LoopStateMachine, a SpineArgs,
	d SpineDeps) (*looptypes.LoopResult, error) {
	// Re-queried on EVERY call, which is the point: a tool registered at
	// runtime is invisible to a list captured once. The closure is passed
	// to the parallel path and to the execute phase, and neither of them
	// may cache it.
	resolveTools := func() ([]any, error) {
		if ctx.PermCtx == nil {
			return append([]any{}, d.ExecuteTools...), nil
		}
		role, deny := permParts(ctx.PermCtx)
		return d.GetToolsForRole(role, deny)
	}

	// --- Phase B: decompose ------------------------------------------------
	if err := ctx.SetPhase(looptypes.PhaseDecompose, d.PhaseTrace); err != nil {
		return nil, err
	}
	dec, err := d.DecomposeGoal(pyval.Obj{
		{Key: "ctx", Val: ctx},
		{Key: "preset_steps", Val: a.PresetSteps},
		{Key: "max_steps", Val: a.MaxSteps},
		{Key: "knowledge_sub_goals", Val: a.KnowledgeSubGoals},
		{Key: "permission_context", Val: a.PermissionContext},
	})
	if err != nil {
		return nil, err
	}
	if err := unpack(dec, 6); err != nil {
		return nil, err
	}
	steps := dec[0]
	prereqContext := dec[1]
	hadNoMatchingSkill := dec[5]
	// dec[2:5] are the lessons / skills / cost contexts, bound and never
	// read again in this function. They are unpacked because the tuple has
	// six values and unpacking is all-or-nothing.

	// --- Phase C: pre-flight -----------------------------------------------
	//
	// EQUIVALENT MUTANT (kept, marked `equivalent`): swallowing this
	// error. The FIRST transition is the only one that can be refused,
	// because the phase it starts from is the caller's; every transition
	// after it starts from a phase this function just set, and each of
	// those pairs is in the table. The check is kept because Python's
	// set_phase raises here too — the difference is only that no input can
	// make it.
	if err := ctx.SetPhase(looptypes.PhasePreFlight, d.PhaseTrace); err != nil {
		return nil, err
	}
	pfv, err := d.PreflightChecks(pyval.Obj{
		{Key: "ctx", Val: ctx},
		{Key: "steps", Val: steps},
		{Key: "resume_from_loop_id", Val: a.ResumeFromLoopID},
		{Key: "parallel_fan_out", Val: a.ParallelFanOut},
	})
	if err != nil {
		return nil, err
	}
	if err := unpack(pfv, 3); err != nil {
		return nil, err
	}
	steps = pfv[0]
	pf, _ := pfv[1].(map[string]any)
	// `if _pf_early_return is not None` — a pre-flight that refuses returns
	// the run, and nothing below it happens: no prepare, no execute, no
	// finalize, no auto-recovery.
	if early, ok := pfv[2].(*looptypes.LoopResult); ok && early != nil {
		return early, nil
	}

	// Thirteen reads in source order. A dict that is missing one names the
	// FIRST absent key, so the order is observable and not decoration.
	var pfVals [13]any
	for i, k := range []string{
		"resume_completed", "resume_executor_session", "pf_review",
		"clean_steps", "deps", "levels", "parallel_levels",
		"manifest_steps", "replan_count", "loop_shared_ctx",
		"proj_fanout_dir", "use_dag", "use_fanout",
	} {
		v, err := dictGet(pf, k)
		if err != nil {
			return nil, err
		}
		pfVals[i] = v
	}
	resumeCompleted, resumeExecutorSession := pfVals[0], pfVals[1]
	pfReview, cleanSteps, deps := pfVals[2], pfVals[3], pfVals[4]
	levels, parallelLevels := pfVals[5], pfVals[6]
	manifestSteps, replanCount := pfVals[7], pfVals[8]
	loopSharedCtx, projFanoutDir := pfVals[9], pfVals[10]
	useDAG, useFanout := pfVals[11], pfVals[12]

	// --- Phase D: parallel fan-out, which may or may not be an exit --------
	if pyval.Truthy(useDAG) || pyval.Truthy(useFanout) {
		if err := ctx.SetPhase(looptypes.PhaseParallel,
			d.PhaseTrace); err != nil {
			return nil, err
		}
		par, err := d.RunParallelPath(pyval.Obj{
			{Key: "ctx", Val: ctx},
			{Key: "steps", Val: steps},
			{Key: "clean_steps", Val: cleanSteps},
			{Key: "deps", Val: deps},
			{Key: "levels", Val: levels},
			{Key: "parallel_levels", Val: parallelLevels},
			{Key: "parallel_fan_out", Val: a.ParallelFanOut},
			{Key: "proj_fanout_dir", Val: projFanoutDir},
			{Key: "loop_shared_ctx", Val: loopSharedCtx},
			{Key: "use_dag", Val: useDAG},
			{Key: "resolve_tools_fn", Val: resolveTools},
		})
		if err != nil {
			return nil, err
		}
		// A parallel path that returns None falls THROUGH to prepare and
		// runs serially. The phase has already moved to parallel and stays
		// there until prepare, which the transition table allows.
		if par != nil {
			parallelTrace(ctx, d, par, useDAG)
			parallelReport(ctx, d, par, manifestSteps, replanCount)
			return par, nil
		}
	}

	// --- Phase E: prepare --------------------------------------------------
	if err := ctx.SetPhase(looptypes.PhasePrepare, d.PhaseTrace); err != nil {
		return nil, err
	}
	prep, err := d.PrepareExecution(pyval.Obj{
		{Key: "ctx", Val: ctx},
		{Key: "steps", Val: steps},
		{Key: "manifest_steps", Val: manifestSteps},
	})
	if err != nil {
		return nil, err
	}
	if err := unpack(prep, 3); err != nil {
		return nil, err
	}
	steps, stepIndices, manifestSteps := prep[0], prep[1], prep[2]

	// --- Phase F: execute, under a runaway cost circuit --------------------
	if err := ctx.SetPhase(looptypes.PhaseExecute, d.PhaseTrace); err != nil {
		return nil, err
	}
	disarm := armRunaway(ctx, d)
	ex, err := func() (map[string]any, error) {
		out, err := d.ExecuteMainLoop(pyval.Obj{
			{Key: "ctx", Val: ctx},
			{Key: "steps", Val: steps},
			{Key: "step_indices", Val: stepIndices},
			{Key: "resume_completed", Val: resumeCompleted},
			{Key: "resume_executor_session", Val: resumeExecutorSession},
			{Key: "prereq_context", Val: prereqContext},
			{Key: "pf_review", Val: pfReview},
			{Key: "levels", Val: levels},
			{Key: "manifest_steps", Val: manifestSteps},
			{Key: "replan_count", Val: replanCount},
			{Key: "loop_shared_ctx", Val: loopSharedCtx},
			{Key: "resolve_tools_fn", Val: resolveTools},
			{Key: "tier_order", Val: a.TierOrder},
			{Key: "parallel_fan_out", Val: a.ParallelFanOut},
		})
		// `finally` — the disarm runs whether the execute returned or
		// raised, and a disarm that raises REPLACES the in-flight
		// exception rather than adding to it.
		if disarm != nil {
			if derr := disarm(); derr != nil {
				return nil, derr
			}
		}
		return out, err
	}()
	if err != nil {
		return nil, err
	}

	// Sixteen reads, same rule as the pre-flight dict. Two of them —
	// `goal` and `max_iterations` — REBIND names the auto-recovery re-run
	// passes on, so an executor that rewrote the goal recovers on the
	// rewritten one.
	var exVals [16]any
	for i, k := range []string{
		"step_outcomes", "loop_status", "stuck_reason", "total_tokens_in",
		"total_tokens_out", "interrupts_applied", "march_of_nines_alert",
		"manifest_steps", "replan_count", "milestone_expanded",
		"failure_chain", "recovery_step_count", "scratchpad",
		"scratchpad_lock", "goal", "max_iterations",
	} {
		v, err := dictGet(ex, k)
		if err != nil {
			return nil, err
		}
		exVals[i] = v
	}

	// --- Phase G: finalize -------------------------------------------------
	if err := ctx.SetPhase(looptypes.PhaseFinalize, d.PhaseTrace); err != nil {
		return nil, err
	}
	result, err := d.BuildResultAndFinalize(pyval.Obj{
		{Key: "ctx", Val: ctx},
		{Key: "step_outcomes", Val: exVals[0]},
		{Key: "loop_status", Val: exVals[1]},
		{Key: "stuck_reason", Val: exVals[2]},
		{Key: "total_tokens_in", Val: exVals[3]},
		{Key: "total_tokens_out", Val: exVals[4]},
		{Key: "interrupts_applied", Val: exVals[5]},
		{Key: "march_of_nines_alert", Val: exVals[6]},
		{Key: "pf_review", Val: pfReview},
		{Key: "manifest_steps", Val: exVals[7]},
		{Key: "replan_count", Val: exVals[8]},
		// start_ts is ctx.start_ts, rebound above the fence.
		{Key: "start_ts", Val: ctx.StartTS},
		{Key: "milestone_expanded", Val: exVals[9]},
		{Key: "had_no_matching_skill", Val: hadNoMatchingSkill},
		{Key: "failure_chain", Val: exVals[10]},
		{Key: "recovery_step_count", Val: exVals[11]},
		{Key: "scratchpad", Val: exVals[12]},
		{Key: "scratchpad_lock", Val: exVals[13]},
	})
	if err != nil {
		return nil, err
	}

	// --- Phase 45: auto-recovery, at most one level deep -------------------
	if result != nil && result.Status == "stuck" && !a.DryRun &&
		!a.RecoveryInProgress {
		result = autoRecover(ctx, a, d, result, exVals[14], exVals[15])
	}
	return result, nil
}

// permParts reads `_perm_ctx.role` and `_perm_ctx.deny_patterns`. The
// context is Python's `Any`; a probe hands it an ordered Obj.
func permParts(pc any) (role, deny any) {
	o, ok := pc.(pyval.Obj)
	if !ok {
		return nil, nil
	}
	r, _ := o.Get("role")
	dp, _ := o.Get("deny_patterns")
	return r, dp
}

// parallelTrace records the fan-out and the terminal it returns from.
//
// Without it the phase never leaves "parallel" and none of the execute /
// finalize / verify edges exist, so a fan-out run's trace ends at
// phase.parallel and reads exactly like a crashed serial run (2026-08-18
// edge census). Best effort: a trace failure is a DEBUG line, because the
// run itself succeeded.
func parallelTrace(ctx *looptypes.LoopStateMachine, d SpineDeps,
	par *looptypes.LoopResult, useDAG any) {
	err := func() error {
		if d.RecordEdge == nil {
			return d.importErr("run_trace")
		}
		blocked := 0
		for _, s := range par.Steps {
			if s.Status == "blocked" {
				blocked++
			}
		}
		mode := "fanout"
		if pyval.Truthy(useDAG) {
			mode = "dag"
		}
		if err := d.RecordEdge("phase.parallel", "exec.parallel", pyval.Obj{
			{Key: "loop_id", Val: ctx.LoopID},
			{Key: "steps", Val: len(par.Steps)},
			{Key: "blocked", Val: blocked},
			{Key: "mode", Val: mode},
		}); err != nil {
			return err
		}
		// The terminal an operator reads: a fan-out that did not finish
		// "done" ends at fin.partial, and either way finalize was BYPASSED.
		to := "fin.result"
		if par.Status != "done" {
			to = "fin.partial"
		}
		reason := ""
		if par.StuckReason != nil {
			reason = *par.StuckReason
		}
		return d.RecordEdge("exec.parallel", to, pyval.Obj{
			{Key: "loop_id", Val: ctx.LoopID},
			{Key: "status", Val: par.Status},
			{Key: "bypassed_finalize", Val: true},
			{Key: "stuck_reason", Val: reason},
		})
	}()
	if err != nil {
		d.debugf("edge trace for parallel path failed: %s", err)
	}
}

// parallelReport is the run-visibility half of the same early return.
//
// 2026-07-08 adversarial review (finding #1): this exit bypasses
// _build_result_and_finalize entirely — true for every finalize side
// effect (telegram notify, introspection, Reflexion memory), not just the
// report. This narrowly ensures the report and index reach a TERMINAL
// state instead of being stuck "running" forever.
//
// The import is the first statement, so a missing loop_report costs the
// loop LOG too — which is the file write_runs_index reads its token and
// step totals from, and the reason a parallel run's index row used to show
// a report link beside a "-" forever (2026-07-08 review, round 2).
func parallelReport(ctx *looptypes.LoopStateMachine, d SpineDeps,
	par *looptypes.LoopResult, manifestSteps, replanCount any) {
	err := func() error {
		if d.WriteRunReport == nil || d.WriteRunsIndex == nil {
			return d.importErr("loop_report")
		}
		if err := d.WriteLoopLog(pyval.Obj{
			{Key: "project", Val: ctx.Project},
			{Key: "loop_id", Val: ctx.LoopID},
			{Key: "goal", Val: ctx.Goal},
			{Key: "status", Val: par.Status},
			{Key: "steps", Val: par.Steps},
			{Key: "start_ts", Val: ctx.StartTS},
			{Key: "elapsed_ms", Val: par.ElapsedMS},
			// RAW, not `or ""`. The edge two functions up normalises its
			// stuck_reason and this one does not, so a clean parallel run
			// writes a loop log whose stuck_reason is None and an edge
			// whose stuck_reason is the empty string.
			{Key: "stuck_reason", Val: par.StuckReason},
		}); err != nil {
			return err
		}
		// A project-less run has nowhere to put a report, and a run with
		// no manifest has nothing to say in one.
		if ctx.Project != "" && pyval.Truthy(manifestSteps) {
			if err := d.WriteRunReport(pyval.Obj{
				{Key: "project", Val: ctx.Project},
				{Key: "loop_id", Val: ctx.LoopID},
				{Key: "goal", Val: ctx.Goal},
				{Key: "planned_steps", Val: manifestSteps},
				{Key: "start_ts", Val: ctx.StartTS},
				{Key: "step_outcomes", Val: par.Steps},
				{Key: "status", Val: par.Status},
				{Key: "elapsed_ms", Val: par.ElapsedMS},
				{Key: "replan_count", Val: replanCount},
				// EDGE 7: only loop_finalize passed this, so an operator
				// who injected a note and opened the LIVE report saw an
				// empty panel until the run finished.
				{Key: "injections", Val: append([]pyval.Obj{},
					ctx.Injections...)},
			}); err != nil {
				return err
			}
		}
		return d.WriteRunsIndex(true)
	}()
	if err != nil {
		d.warnf("run report write failed for parallel loop %s: %s",
			ctx.LoopID, err)
	}
}

// armRunaway is the runaway cost circuit (BACKLOG #23e), armed for the
// EXECUTE phase only — finalize, closure and the quality gate must never
// be refused a call (budget-breaker demotion lesson, 8f8344a). The ceiling
// is a multiple of the cost budget and sits ABOVE the between-step hard
// stop, so legitimate long work under budget never sees it.
//
// A nil return is "not armed", which is also what every failure produces:
// the circuit is an extra, and a run that cannot arm it still runs.
func armRunaway(ctx *looptypes.LoopStateMachine, d SpineDeps) func() error {
	// `if ctx.cost_budget:` — TRUTHINESS. No budget and a budget of zero
	// both skip, for different reasons that agree here.
	if ctx.CostBudget == nil || *ctx.CostBudget == 0 {
		return nil
	}
	var disarm func() error
	err := func() error {
		if d.ConfigGet == nil {
			return d.importErr("config")
		}
		raw, err := d.ConfigGet("budget.runaway_multiplier", 1.5)
		if err != nil {
			return err
		}
		// `float(_rc_mult) if _rc_mult is not None else 0.0` — an explicit
		// null in config disarms the circuit; a non-numeric one raises and
		// lands on the warning below.
		mult := 0.0
		if raw != nil {
			if mult, err = toFloat(raw); err != nil {
				return err
			}
		}
		if mult > 0 {
			if d.ArmCostMeter == nil {
				return d.importErr("llm")
			}
			disarm, err = d.ArmCostMeter(*ctx.CostBudget * mult)
			return err
		}
		return nil
	}()
	if err != nil {
		d.warnf("runaway cost circuit not armed: %s", err)
		// Python leaves _disarm_runaway at whatever it held: the raise can
		// only come from before the assignment, so that is None.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): returning `disarm`
		// here. It is assigned only by a SUCCESSFUL ArmCostMeter, so it is
		// nil on every path that reaches this line.
		return nil
	}
	return disarm
}

// autoRecover is Phase 45: a stuck run whose diagnosis has a LOW-risk,
// auto-applicable recovery is re-run once with adjusted parameters.
//
// The guard against infinite recursion is `_recovery_in_progress`, passed
// as a call-stack-local argument rather than shared mutable state, so
// concurrent run_agent_loop calls (run_parallel_loops) cannot race each
// other out of it.
//
// Three nested failure regimes, and they are not the same:
//
//   - the OUTER block swallows ImportError SILENTLY and logs anything else
//     at DEBUG. A box without introspect recovers nothing and says nothing.
//   - the captain's-log write logs its own failure at DEBUG.
//   - the trace edge swallows EVERYTHING silently, which is the only place
//     in the spine that does.
//
// A failure anywhere here returns the ORIGINAL stuck result: recovery is
// an improvement on the ending, never a replacement for having one.
func autoRecover(ctx *looptypes.LoopStateMachine, a SpineArgs, d SpineDeps,
	result *looptypes.LoopResult, goal, maxIterations any,
) *looptypes.LoopResult {
	out := result
	err := func() error {
		if d.DiagnoseLoop == nil || d.PlanRecovery == nil {
			return d.importErr("introspect")
		}
		// `_diag_fn(loop_id)` — the local rebound from ctx above the fence.
		diag, err := d.DiagnoseLoop(ctx.LoopID)
		if err != nil {
			return err
		}
		rec, err := d.PlanRecovery(diag)
		if err != nil {
			return err
		}
		// `if _recovery and _recovery.auto_apply and _recovery.risk ==
		// "low"` — short-circuit, so a None plan never reads an attribute
		// and a plan that is not auto-apply never reads its risk.
		// EQUIVALENT MUTANT (kept, marked `equivalent`): spelling this as
		// `rec == nil`. plan_recovery returns a RecoveryPlan dataclass or
		// None, and a dataclass without __bool__ is always truthy, so the
		// two agree on every reachable input. The truthiness spelling is
		// what Python WRITES, and a probe object that could be falsy would
		// be testing a state production has not got.
		if !pyval.Truthy(rec) {
			return nil
		}
		auto, err := d.RecoveryAutoApply(rec)
		if err != nil {
			return err
		}
		if !auto {
			return nil
		}
		risk, err := d.RecoveryRisk(rec)
		if err != nil {
			return err
		}
		if risk != "low" {
			return nil
		}
		// Format arguments evaluate left to right, so a diagnosis whose
		// failure_class raises does so AFTER the action has been read.
		action, err := d.RecoveryAction(rec)
		if err != nil {
			return err
		}
		fclass, err := d.DiagFailureClass(diag)
		if err != nil {
			return err
		}
		d.info("auto-recovery: %s (class=%s)", action, fclass)

		if cerr := captainsLogRecovery(ctx, d, diag, rec); cerr != nil {
			d.debugf("auto-recovery captain's log write failed: %s", cerr)
		}

		// dict(params) is a COPY, and pop() mutates the copy. The
		// remainder is then never read: _new_params is dead after these
		// two lines, so a recovery plan carrying any other parameter
		// silently drops it.
		params, err := d.RecoveryParams(rec)
		if err != nil {
			return err
		}
		newMaxSteps := popOr(&params, "max_steps", a.MaxSteps)
		newMaxIter := popOr(&params, "max_iterations", maxIterations)

		// The child re-run inherits this run-dir, so its rows interleave
		// into the same trace. Mark the hand-off or the second loop's
		// edges look like a continuation of the first. Silent on failure.
		func() {
			if d.RecordEdge == nil {
				return
			}
			_ = d.RecordEdge("fin.diagnose", "fin.auto_recovery", pyval.Obj{
				{Key: "loop_id", Val: ctx.LoopID},
				{Key: "parent_loop_id", Val: ctx.LoopID},
				{Key: "max_steps", Val: newMaxSteps},
			})
		}()

		res, err := d.RunAgentLoop(pyval.Obj{
			// goal is the EXECUTE phase's rebinding, not the caller's.
			{Key: "goal", Val: goal},
			// project, adapter and interrupt_queue are the CONTEXT's,
			// rebound above the fence.
			{Key: "project", Val: ctx.Project},
			{Key: "model", Val: a.Model},
			{Key: "adapter", Val: ctx.Adapter},
			{Key: "max_steps", Val: newMaxSteps},
			{Key: "max_iterations", Val: newMaxIter},
			{Key: "dry_run", Val: a.DryRun},
			{Key: "verbose", Val: a.Verbose},
			{Key: "interrupt_queue", Val: ctx.InterruptQueue},
			{Key: "hook_registry", Val: a.HookRegistry},
			{Key: "ancestry_context_extra", Val: a.AncestryContextExtra},
			{Key: "step_callback", Val: a.StepCallback},
			{Key: "parallel_fan_out", Val: a.ParallelFanOut},
			{Key: "token_budget", Val: a.TokenBudget},
			{Key: "measurement_class", Val: ctx.MeasurementClass},
			{Key: "handle_id", Val: ctx.HandleID},
			// Inherit the caller's deferral contracts (review of 707a541):
			// without these the recovery re-run learned verdict-blind
			// inline AND ran maintenance inline before closure, while the
			// original loop's registered maintenance still drained later —
			// double work plus the exact pre-decree latency.
			{Key: "defer_learning", Val: ctx.DeferLearning},
			{Key: "defer_maintenance", Val: ctx.DeferMaintenance},
			{Key: "_recovery_in_progress", Val: true},
		})
		if err != nil {
			return err
		}
		out = res
		status := ""
		if res != nil {
			status = res.Status
		}
		d.info("auto-recovery result: status=%s", status)
		return nil
	}()
	if err != nil {
		// `except ImportError: pass` comes FIRST and says nothing.
		// ModuleNotFoundError is a subclass, so a missing introspect is
		// silent too.
		if !isImportError(err) {
			d.debugf("auto-recovery failed: %s", err)
		}
	}
	return out
}

// captainsLogRecovery writes the AUTO_RECOVERY event. The keyword order is
// the evaluation order, and every one of these reads can raise: subject
// reads the failure class, summary reads the action and the class again,
// and context reads the action, the risk and the params.
func captainsLogRecovery(ctx *looptypes.LoopStateMachine, d SpineDeps,
	diag, rec any) error {
	if d.LogEvent == nil {
		return d.importErr("captains_log")
	}
	subject, err := d.DiagFailureClass(diag)
	if err != nil {
		return err
	}
	action, err := d.RecoveryAction(rec)
	if err != nil {
		return err
	}
	fclass, err := d.DiagFailureClass(diag)
	if err != nil {
		return err
	}
	summary := fmt.Sprintf("Auto-recovery triggered: %s. Class: %s.",
		action, fclass)
	cAction, err := d.RecoveryAction(rec)
	if err != nil {
		return err
	}
	risk, err := d.RecoveryRisk(rec)
	if err != nil {
		return err
	}
	params, err := d.RecoveryParams(rec)
	if err != nil {
		return err
	}
	return d.LogEvent(pyval.Obj{
		{Key: "event_type", Val: d.AutoRecoveryEventKey},
		{Key: "subject", Val: subject},
		{Key: "summary", Val: summary},
		{Key: "context", Val: pyval.Obj{
			{Key: "action", Val: cAction},
			{Key: "risk", Val: risk},
			{Key: "params", Val: params},
		}},
		{Key: "loop_id", Val: ctx.LoopID},
	})
}

// popOr is `d.pop(key, default)` over the ordered copy.
//
// EQUIVALENT MUTANT (kept, marked `equivalent`): the removal itself. The
// copy is dead after the two pops — Python never reads _new_params again —
// and a dict cannot hold the same key twice, so removing max_steps cannot
// change what the max_iterations pop finds. The removal is written because
// pop() is what Python calls, and the day someone reads the remainder is
// the day it starts mattering.
func popOr(o *pyval.Obj, key string, def any) any {
	for i, f := range *o {
		if f.Key == key {
			v := f.Val
			*o = append((*o)[:i:i], (*o)[i+1:]...)
			return v
		}
	}
	return def
}

// isImportError is the `except ImportError` arm. ModuleNotFoundError is a
// SUBCLASS of ImportError, so both are caught and both are silent.
func isImportError(err error) bool {
	var pe *pyval.PyErr
	if !asPyErr(err, &pe) {
		return false
	}
	return pe.Class == "ImportError" || pe.Class == "ModuleNotFoundError"
}
