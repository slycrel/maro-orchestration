package agentloop

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The spine's whole content is call ORDER, the gates that skip a call, and
// the keywords it computes — so the differential records every call with
// its keyword list in evaluation order, every log line with its level, the
// phase the context ends in, the result, and the exception if one left
// run_agent_loop.
//
// Values that only pass THROUGH the spine are markers, so a keyword that
// arrives at the wrong callee is a diff and not a coincidence. Anything
// the spec passes through is a scalar or a list: a Python dict would
// render in its insertion order and a Go map cannot reproduce that, which
// is why the recovery plan's params are spelled as ordered pairs.

type resSpec struct {
	LoopID      string           `json:"loop_id"`
	Status      string           `json:"status"`
	StuckReason *string          `json:"stuck_reason"`
	ElapsedMS   int              `json:"elapsed_ms"`
	Steps       []map[string]any `json:"steps"`
}

type permSpec struct {
	Role         any `json:"role"`
	DenyPatterns any `json:"deny_patterns"`
}

type spineSpec struct {
	Name string `json:"name"`

	Goal    string `json:"goal"`
	LoopID  string `json:"loop_id"`
	Project any    `json:"project"`
	// CtxProject is ctx.project. Project is the run_agent_loop keyword,
	// which the spine never reads — the recursion passes the CONTEXT's.
	CtxProject string `json:"ctx_project"`
	MaxSteps   any    `json:"max_steps"`
	// DryRun is the KEYWORD the auto-recovery gate reads; CtxDryRun is
	// ctx.dry_run, which it does not.
	DryRun             bool `json:"dry_run"`
	CtxDryRun          bool `json:"ctx_dry_run"`
	RecoveryInProgress bool `json:"recovery_in_progress"`

	CostBudget        *float64  `json:"cost_budget"`
	RunawayMultiplier any       `json:"runaway_multiplier"`
	DeferLearning     bool      `json:"defer_learning"`
	DeferMaintenance  bool      `json:"defer_maintenance"`
	MeasurementClass  string    `json:"measurement_class"`
	HandleID          string    `json:"handle_id"`
	Injections        bool      `json:"injections"`
	PermCtx           *permSpec `json:"perm_ctx"`
	// ResolveToolsCalls is how many times the parallel path calls the
	// closure. Two calls, with a registry that answers differently, is
	// what makes "re-queried on every call" falsifiable.
	ResolveToolsCalls int `json:"resolve_tools_calls"`
	// StartPhase forces ctx.phase before the run. _initialize_loop always
	// returns "init", so this is the only way to reach set_phase's refusal.
	StartPhase string `json:"start_phase"`

	DecomposeReturn []any          `json:"decompose_return"`
	PreflightSteps  any            `json:"preflight_steps"`
	PF              map[string]any `json:"pf"`
	PreflightEarly  *resSpec       `json:"preflight_early"`
	ParallelResult  *resSpec       `json:"parallel_result"`
	PrepareReturn   []any          `json:"prepare_return"`
	EX              map[string]any `json:"ex"`
	FinalizeResult  *resSpec       `json:"finalize_result"`
	RecursionResult *resSpec       `json:"recursion_result"`

	// Diagnosis and Plan are attribute BAGS: a name that is absent raises
	// AttributeError inside the block that swallows, and a raises key of
	// "plan.risk" makes the read itself raise.
	Diagnosis map[string]any `json:"diagnosis"`
	Plan      map[string]any `json:"plan"`

	Raises      map[string][]string `json:"raises"`
	DropNames   []string            `json:"drop_names"`
	DeadModules []string            `json:"dead_modules"`
}

// marker is a value the spine only carries. Its rendering names it, so a
// keyword that arrives at the wrong callee is visible.
type marker struct{ tag string }

func defaultPF() map[string]any {
	return map[string]any{
		"resume_completed": 0, "resume_executor_session": nil,
		"pf_review": "review", "clean_steps": []any{"c1"},
		"deps": []any{}, "levels": []any{"L"}, "parallel_levels": []any{},
		"manifest_steps": []any{"m1"}, "replan_count": 0,
		"loop_shared_ctx": "shared", "proj_fanout_dir": "/fan",
		"use_dag": false, "use_fanout": false,
	}
}

func defaultEX() map[string]any {
	return map[string]any{
		"step_outcomes": []any{"so"}, "loop_status": "done",
		"stuck_reason": nil, "total_tokens_in": 10,
		"total_tokens_out": 20, "interrupts_applied": 0,
		"march_of_nines_alert": false, "manifest_steps": []any{"m2"},
		"replan_count": 1, "milestone_expanded": []any{},
		"failure_chain": []any{}, "recovery_step_count": 0,
		"scratchpad": "pad", "scratchpad_lock": "lock",
		"goal": "the executor's goal", "max_iterations": 40,
	}
}

func spineScenarios() []spineSpec {
	var out []spineSpec
	add := func(s spineSpec) {
		if s.Goal == "" {
			s.Goal = "ship the thing"
		}
		if s.LoopID == "" {
			s.LoopID = "L1"
		}
		if s.MaxSteps == nil {
			s.MaxSteps = 8
		}
		if s.PF == nil {
			s.PF = defaultPF()
		}
		if s.EX == nil {
			s.EX = defaultEX()
		}
		if s.DecomposeReturn == nil {
			s.DecomposeReturn = []any{[]any{"s1", "s2"}, "prereq",
				"lessons", "skills", "cost", false}
		}
		if s.PreflightSteps == nil {
			s.PreflightSteps = []any{"s1"}
		}
		if s.PrepareReturn == nil {
			s.PrepareReturn = []any{[]any{"p1"}, []any{0}, []any{"m3"}}
		}
		if s.FinalizeResult == nil {
			s.FinalizeResult = &resSpec{LoopID: "L1", Status: "done"}
		}
		if s.Raises == nil {
			s.Raises = map[string][]string{}
		}
		if s.DropNames == nil {
			s.DropNames = []string{}
		}
		if s.DeadModules == nil {
			s.DeadModules = []string{}
		}
		out = append(out, s)
	}
	raise := func(k, cls, msg string) map[string][]string {
		return map[string][]string{k: {cls, msg}}
	}
	pf := func(edit func(m map[string]any)) map[string]any {
		m := defaultPF()
		edit(m)
		return m
	}
	ex := func(edit func(m map[string]any)) map[string]any {
		m := defaultEX()
		edit(m)
		return m
	}
	stuck := func() *resSpec {
		return &resSpec{LoopID: "L1", Status: "stuck"}
	}
	lowPlan := map[string]any{
		"auto_apply": true, "risk": "low", "action": "retry-smaller",
		"params": []any{[]any{"max_steps", 3}, []any{"other", "x"}},
	}
	diag := map[string]any{"failure_class": "timeout"}

	// --- the straight line -------------------------------------------------
	add(spineSpec{Name: "the-serial-spine-runs-every-phase-in-order",
		CtxProject: "proj"})
	add(spineSpec{Name: "a-project-less-run-still-runs"})
	add(spineSpec{Name: "the-executor-rebinds-the-goal-and-the-iteration-cap",
		EX: ex(func(m map[string]any) {
			m["goal"] = "rewritten"
			m["max_iterations"] = 7
		})})

	// --- phase C's early return --------------------------------------------
	add(spineSpec{Name: "a-preflight-refusal-returns-before-prepare",
		PreflightEarly: &resSpec{LoopID: "L1", Status: "stuck",
			ElapsedMS: 5}})
	add(spineSpec{Name: "a-preflight-refusal-that-is-done-still-returns",
		PreflightEarly: &resSpec{LoopID: "L1", Status: "done"}})

	// --- the unpacks and the dict reads ------------------------------------
	add(spineSpec{Name: "a-five-value-decompose-is-a-value-error",
		DecomposeReturn: []any{[]any{"s1"}, "prereq", "lessons", "skills",
			"cost"}})
	add(spineSpec{Name: "a-seven-value-decompose-is-a-different-value-error",
		DecomposeReturn: []any{1, 2, 3, 4, 5, 6, 7}})
	add(spineSpec{Name: "a-preflight-dict-missing-levels-names-levels",
		PF: pf(func(m map[string]any) { delete(m, "levels") })})
	add(spineSpec{Name: "a-preflight-dict-missing-two-names-the-first",
		PF: pf(func(m map[string]any) {
			delete(m, "clean_steps")
			delete(m, "levels")
		})})
	add(spineSpec{Name: "an-execute-dict-missing-the-goal-names-the-goal",
		EX: ex(func(m map[string]any) { delete(m, "goal") })})

	// --- phase D -----------------------------------------------------------
	add(spineSpec{Name: "a-dag-run-returns-through-the-parallel-path",
		CtxProject: "proj",
		PF:         pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done",
			ElapsedMS: 12, Steps: []map[string]any{
				{"status": "done"}, {"status": "blocked"}}}})
	add(spineSpec{Name: "a-fanout-run-records-the-other-mode",
		CtxProject: "proj",
		PF:         pf(func(m map[string]any) { m["use_fanout"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done",
			Steps: []map[string]any{{"status": "done"}}}})
	add(spineSpec{Name: "a-stuck-parallel-run-ends-at-fin-partial",
		CtxProject: "proj",
		PF:         pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "stuck",
			StuckReason: sPtr("nope")}})
	add(spineSpec{Name: "a-parallel-path-that-returns-none-falls-through",
		PF: pf(func(m map[string]any) { m["use_dag"] = true })})
	add(spineSpec{Name: "a-project-less-parallel-run-writes-no-report",
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "an-empty-manifest-writes-no-report",
		CtxProject: "proj",
		PF: pf(func(m map[string]any) {
			m["use_dag"] = true
			m["manifest_steps"] = []any{}
		}),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "the-report-carries-the-injections",
		CtxProject: "proj", Injections: true,
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "a-dead-run-trace-costs-the-parallel-edges",
		CtxProject: "proj", DeadModules: []string{"run_trace"},
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "a-missing-record-edge-name-is-the-same-debug-line",
		CtxProject: "proj", DropNames: []string{"record_edge"},
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "a-second-edge-that-raises-is-a-debug-line",
		CtxProject:     "proj",
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"},
		Raises: map[string][]string{
			"record_edge": {"RuntimeError", "boom", "2"}}})
	// The import is the first statement in the report block, so a missing
	// loop_report costs the loop LOG too — the file the index reads its
	// totals from.
	add(spineSpec{Name: "a-dead-loop-report-costs-the-loop-log-as-well",
		CtxProject: "proj", DeadModules: []string{"loop_report"},
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "a-loop-log-that-raises-skips-the-report-and-index",
		CtxProject:     "proj",
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"},
		Raises:         raise("_write_loop_log", "OSError", "disk")})
	add(spineSpec{Name: "a-report-that-raises-skips-the-index",
		CtxProject:     "proj",
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"},
		Raises:         raise("write_run_report", "OSError", "disk")})
	add(spineSpec{Name: "a-parallel-path-that-raises-is-not-caught",
		PF:     pf(func(m map[string]any) { m["use_dag"] = true }),
		Raises: raise("_run_parallel_path", "RuntimeError", "boom")})

	// --- the resolve-tools closure -----------------------------------------
	add(spineSpec{Name: "a-run-without-a-permission-context-gets-the-defaults",
		ResolveToolsCalls: 1,
		PF:                pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult:    &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "a-permission-context-re-queries-the-registry",
		ResolveToolsCalls: 1,
		PermCtx:           &permSpec{Role: "worker", DenyPatterns: []any{"rm"}},
		PF:                pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult:    &resSpec{LoopID: "L1", Status: "done"}})

	// --- the runaway cost circuit ------------------------------------------
	add(spineSpec{Name: "no-budget-arms-nothing"})
	add(spineSpec{Name: "a-zero-budget-arms-nothing",
		CostBudget: fPtr(0)})
	add(spineSpec{Name: "a-budget-arms-the-circuit-at-the-multiple",
		CostBudget: fPtr(4), RunawayMultiplier: 1.5})
	add(spineSpec{Name: "a-null-multiplier-disarms-the-circuit",
		CostBudget: fPtr(4), RunawayMultiplier: nil})
	add(spineSpec{Name: "a-zero-multiplier-arms-nothing",
		CostBudget: fPtr(4), RunawayMultiplier: 0})
	add(spineSpec{Name: "a-negative-multiplier-arms-nothing",
		CostBudget: fPtr(4), RunawayMultiplier: -1})
	add(spineSpec{Name: "a-string-multiplier-converts",
		CostBudget: fPtr(4), RunawayMultiplier: "2"})
	add(spineSpec{Name: "an-unconvertible-multiplier-is-a-warning",
		CostBudget: fPtr(4), RunawayMultiplier: "two"})
	add(spineSpec{Name: "a-list-multiplier-is-a-type-error",
		CostBudget: fPtr(4), RunawayMultiplier: []any{1}})
	add(spineSpec{Name: "a-config-read-that-raises-is-a-warning",
		CostBudget: fPtr(4), RunawayMultiplier: 1.5,
		Raises: raise("config_get", "RuntimeError", "boom")})
	add(spineSpec{Name: "a-missing-arm-cost-meter-is-a-warning",
		CostBudget: fPtr(4), RunawayMultiplier: 1.5,
		DropNames: []string{"arm_cost_meter"}})
	add(spineSpec{Name: "an-arm-that-raises-is-a-warning",
		CostBudget: fPtr(4), RunawayMultiplier: 1.5,
		Raises: raise("arm_cost_meter", "RuntimeError", "boom")})
	add(spineSpec{Name: "the-circuit-is-disarmed-when-execute-raises",
		CostBudget: fPtr(4), RunawayMultiplier: 1.5,
		Raises: raise("_execute_main_loop", "RuntimeError", "boom")})
	add(spineSpec{Name: "a-disarm-that-raises-replaces-the-execute-error",
		CostBudget: fPtr(4), RunawayMultiplier: 1.5,
		Raises: map[string][]string{
			"_execute_main_loop": {"RuntimeError", "boom"},
			"disarm_cost_meter":  {"OSError", "stuck open"}}})
	add(spineSpec{Name: "an-unbudgeted-run-has-nothing-to-disarm",
		Raises: raise("_execute_main_loop", "RuntimeError", "boom")})

	// --- Phase 45 ----------------------------------------------------------
	add(spineSpec{Name: "a-done-run-never-diagnoses",
		Diagnosis: diag, Plan: lowPlan})
	add(spineSpec{Name: "a-stuck-run-recovers-once",
		CtxProject: "proj", FinalizeResult: stuck(),
		Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	add(spineSpec{Name: "a-dry-run-never-recovers",
		DryRun: true, FinalizeResult: stuck(),
		Diagnosis: diag, Plan: lowPlan})
	add(spineSpec{Name: "the-context-dry-run-flag-does-not-gate-recovery",
		CtxDryRun: true, FinalizeResult: stuck(),
		Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	add(spineSpec{Name: "a-recovery-re-run-does-not-recover-again",
		RecoveryInProgress: true, FinalizeResult: stuck(),
		Diagnosis: diag, Plan: lowPlan})
	add(spineSpec{Name: "a-dead-introspect-is-silent",
		FinalizeResult: stuck(), DeadModules: []string{"introspect"},
		Diagnosis: diag, Plan: lowPlan})
	add(spineSpec{Name: "a-missing-plan-recovery-name-is-also-silent",
		FinalizeResult: stuck(), DropNames: []string{"plan_recovery"},
		Diagnosis: diag, Plan: lowPlan})
	add(spineSpec{Name: "a-diagnose-that-raises-is-a-debug-line",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		Raises: raise("diagnose_loop", "RuntimeError", "boom")})
	add(spineSpec{Name: "no-plan-recovers-nothing",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: nil})
	add(spineSpec{Name: "a-plan-that-is-not-auto-apply-never-reads-its-risk",
		FinalizeResult: stuck(), Diagnosis: diag,
		Plan: map[string]any{"auto_apply": false, "risk": "low",
			"action": "x", "params": []any{}}})
	add(spineSpec{Name: "a-high-risk-plan-never-reads-its-action",
		FinalizeResult: stuck(), Diagnosis: diag,
		Plan: map[string]any{"auto_apply": true, "risk": "high",
			"action": "x", "params": []any{}}})
	add(spineSpec{Name: "a-plan-with-no-auto-apply-attribute-is-a-debug-line",
		FinalizeResult: stuck(), Diagnosis: diag,
		Plan: map[string]any{"risk": "low", "action": "x",
			"params": []any{}}})
	add(spineSpec{Name: "a-failure-class-that-raises-is-a-debug-line",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		Raises: raise("diag.failure_class", "RuntimeError", "boom")})
	add(spineSpec{Name: "the-second-failure-class-read-is-in-the-captains-log",
		CtxProject: "proj", FinalizeResult: stuck(), Diagnosis: diag,
		Plan:            lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"},
		Raises: map[string][]string{
			"diag.failure_class": {"RuntimeError", "boom", "2"}}})
	// captains_log itself can never be MISSING here: run_agent_loop
	// imports loop_id_scope from it at the top, outside every handler, so
	// a dead module raises before the spine starts (L59, amendment 3).
	// Only a missing NAME is reachable.
	add(spineSpec{Name: "a-missing-log-event-name-does-not-stop-the-recovery",
		FinalizeResult: stuck(), DropNames: []string{"log_event"},
		Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	add(spineSpec{Name: "a-log-event-that-raises-does-not-stop-the-recovery",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"},
		Raises:          raise("log_event", "OSError", "disk")})
	add(spineSpec{Name: "a-plan-without-max-steps-keeps-the-callers",
		FinalizeResult: stuck(), Diagnosis: diag, MaxSteps: 11,
		Plan: map[string]any{"auto_apply": true, "risk": "low",
			"action": "x", "params": []any{[]any{"other", 1}}},
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	add(spineSpec{Name: "a-plan-with-both-caps-overrides-both",
		FinalizeResult: stuck(), Diagnosis: diag, MaxSteps: 11,
		EX: ex(func(m map[string]any) { m["max_iterations"] = 7 }),
		Plan: map[string]any{"auto_apply": true, "risk": "low",
			"action": "x",
			"params": []any{[]any{"max_steps", 3},
				[]any{"max_iterations", 99}}},
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	add(spineSpec{Name: "the-recovery-edge-is-silent-when-it-raises",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"},
		Raises:          raise("record_edge", "RuntimeError", "boom")})
	add(spineSpec{Name: "a-recursion-that-raises-keeps-the-original-result",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		Raises: raise("run_agent_loop", "RuntimeError", "boom")})
	add(spineSpec{Name: "the-recursion-inherits-the-deferral-contracts",
		DeferLearning: true, DeferMaintenance: true,
		MeasurementClass: "organic", HandleID: "H9",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	add(spineSpec{Name: "a-recovery-that-is-still-stuck-is-the-result",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "stuck",
			StuckReason: sPtr("again")}})

	// --- failures that leave run_agent_loop --------------------------------
	// --- fixtures the first battery round asked for ------------------------
	add(spineSpec{Name: "a-context-that-cannot-leave-finalize-refuses",
		StartPhase: "finalize"})
	add(spineSpec{Name: "a-four-value-prepare-is-a-value-error",
		PrepareReturn: []any{[]any{"p1"}, []any{0}, []any{"m3"}, "extra"}})
	// Four record_edge calls precede this one: three phase transitions and
	// the fan-out's own first edge.
	add(spineSpec{Name: "a-first-parallel-edge-failure-skips-the-second",
		CtxProject:     "proj",
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"},
		Raises: map[string][]string{
			"record_edge": {"RuntimeError", "boom", "4"}}})
	add(spineSpec{Name: "a-missing-index-writer-is-an-import-failure-too",
		CtxProject: "proj", DropNames: []string{"write_runs_index"},
		PF:             pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult: &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "the-registry-is-re-queried-on-every-call",
		ResolveToolsCalls: 2,
		PermCtx:           &permSpec{Role: "worker", DenyPatterns: []any{"rm"}},
		PF:                pf(func(m map[string]any) { m["use_dag"] = true }),
		ParallelResult:    &resSpec{LoopID: "L1", Status: "done"}})
	add(spineSpec{Name: "a-plan-without-an-iteration-cap-keeps-the-executes",
		FinalizeResult: stuck(), Diagnosis: diag,
		EX: ex(func(m map[string]any) { m["max_iterations"] = 7 }),
		Plan: map[string]any{"auto_apply": true, "risk": "low",
			"action": "x", "params": []any{}},
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"}})
	// auto_apply is read FIRST and short-circuits, so a risk that raises
	// is never reached.
	add(spineSpec{Name: "a-plan-that-is-not-auto-apply-never-raises-on-risk",
		FinalizeResult: stuck(), Diagnosis: diag,
		Plan: map[string]any{"auto_apply": false, "risk": "low",
			"action": "x", "params": []any{}},
		Raises: raise("plan.risk", "RuntimeError", "risk-boom")})
	// Both reads raise, with different messages: the debug line names
	// whichever ran first.
	add(spineSpec{Name: "the-action-raises-before-the-failure-class-does",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		Raises: map[string][]string{
			"plan.action":        {"RuntimeError", "action-boom"},
			"diag.failure_class": {"OSError", "class-boom"}}})
	// The THIRD read of failure_class is the captain's log summary; the
	// first two are the info line and the event subject.
	add(spineSpec{Name: "the-summary-reads-the-failure-class-a-third-time",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"},
		Raises: map[string][]string{
			"diag.failure_class": {"RuntimeError", "boom", "3"}}})
	// The THIRD read of action is the event context; the first two are the
	// info line and the summary.
	add(spineSpec{Name: "the-event-context-reads-the-action-a-third-time",
		FinalizeResult: stuck(), Diagnosis: diag, Plan: lowPlan,
		RecursionResult: &resSpec{LoopID: "L2", Status: "done"},
		Raises: map[string][]string{
			"plan.action": {"RuntimeError", "boom", "3"}}})

	add(spineSpec{Name: "a-decompose-that-raises-is-not-caught",
		Raises: raise("_decompose_goal", "RuntimeError", "boom")})
	add(spineSpec{Name: "a-finalize-that-raises-is-not-caught",
		Raises: raise("_build_result_and_finalize", "RuntimeError", "boom")})

	return out
}

func sPtr(s string) *string   { return &s }
func fPtr(f float64) *float64 { return &f }

// canonVal is `pv` in Go. Dicts render as ordered pairs on both sides
// because the ORDER a spine builds keywords in is part of what is compared.
func canonVal(v any) any {
	switch t := v.(type) {
	case nil:
		return nil
	case bool, string, int, float64:
		return t
	case marker:
		return "<" + t.tag + ">"
	case attrBag:
		return "<Attrs>"
	case *string:
		if t == nil {
			return nil
		}
		return *t
	case *looptypes.LoopStateMachine:
		return "<ctx>"
	case *looptypes.LoopResult:
		if t == nil {
			return nil
		}
		return map[string]any{"__result__": []any{t.LoopID, t.Status,
			t.ElapsedMS, strPtr(t.StuckReason)}}
	case pyval.Obj:
		out := []any{}
		for _, f := range t {
			out = append(out, []any{f.Key, canonVal(f.Val)})
		}
		return out
	case []any:
		out := []any{}
		for _, x := range t {
			out = append(out, canonVal(x))
		}
		return out
	case []looptypes.StepOutcome:
		out := []any{}
		for range t {
			out = append(out, "<StepOutcome>")
		}
		return out
	case []pyval.Obj:
		out := []any{}
		for _, x := range t {
			out = append(out, canonVal(x))
		}
		return out
	case []string:
		out := []any{}
		for _, x := range t {
			out = append(out, x)
		}
		return out
	case func() ([]any, error):
		return "<fn>"
	}
	return "<" + pyval.TypeName(v) + ">"
}

func canonKW(kw pyval.Obj) any { return canonVal(kw) }

// excPair is `[type(e).__name__, str(e)]`.
func excPair(err error) []any {
	if err == nil {
		return nil
	}
	var pe *pyval.PyErr
	if asPyErr(err, &pe) {
		return []any{pe.Class, pe.Msg}
	}
	var ite *looptypes.InvalidTransitionError
	if errors.As(err, &ite) {
		return []any{"InvalidTransitionError", ite.Error()}
	}
	return []any{"Exception", err.Error()}
}

func goSpineRecord(s spineSpec) map[string]any {
	calls := []map[string]any{}
	logs := []map[string]any{}
	rec := func(name string, kw pyval.Obj) {
		calls = append(calls, map[string]any{"call": name,
			"kw": canonKW(kw)})
	}
	at := func(level string) func(string) {
		return func(m string) {
			logs = append(logs, map[string]any{"level": level, "msg": m})
		}
	}
	counts := map[string]int{}
	raise := func(key string) error {
		spec := s.Raises[key]
		if len(spec) == 0 {
			return nil
		}
		counts[key]++
		if len(spec) > 2 && spec[2] != strconv.Itoa(counts[key]) {
			return nil
		}
		return fenceErr(spec)
	}
	dropped := map[string]bool{}
	for _, n := range s.DropNames {
		dropped[n] = true
	}
	dead := map[string]bool{}
	for _, n := range s.DeadModules {
		dead[n] = true
	}

	ctx := looptypes.NewLoopStateMachine()
	if s.StartPhase != "" {
		ctx.Phase = s.StartPhase
	}
	ctx.LoopID = s.LoopID
	ctx.Goal = s.Goal
	ctx.Project = s.CtxProject
	ctx.StartTS = "2026-01-01T00:00:00Z"
	ctx.StartedAt = 100.0
	ctx.CostBudget = s.CostBudget
	ctx.DryRun = s.CtxDryRun
	ctx.DeferLearning = s.DeferLearning
	ctx.DeferMaintenance = s.DeferMaintenance
	ctx.MeasurementClass = s.MeasurementClass
	ctx.HandleID = s.HandleID
	ctx.Adapter = marker{"adapter"}
	ctx.InterruptQueue = marker{"interrupt_queue"}
	if s.Injections {
		ctx.Injections = []pyval.Obj{{{Key: "kind", Val: "note"}}}
	}
	if s.PermCtx != nil {
		ctx.PermCtx = pyval.Obj{
			{Key: "role", Val: s.PermCtx.Role},
			{Key: "deny_patterns", Val: s.PermCtx.DenyPatterns},
		}
	}

	a := SpineArgs{
		MaxSteps: s.MaxSteps, DryRun: s.DryRun,
		RecoveryInProgress: s.RecoveryInProgress,
		Verbose:            false, AncestryContextExtra: "",
		ParallelFanOut: 0, KnowledgeSubGoals: false,
		TierOrder: pyval.Obj{
			{Key: "cheap", Val: 0}, {Key: "mid", Val: 1},
			{Key: "power", Val: 2}},
	}

	mkres := func(r *resSpec) *looptypes.LoopResult {
		if r == nil {
			return nil
		}
		out := &looptypes.LoopResult{LoopID: r.LoopID, Goal: s.Goal,
			Project: s.CtxProject, Status: r.Status,
			StuckReason: r.StuckReason, ElapsedMS: r.ElapsedMS}
		for _, st := range r.Steps {
			sv, _ := st["status"].(string)
			out.Steps = append(out.Steps,
				looptypes.StepOutcome{Status: sv})
		}
		return out
	}

	toolCalls := 0
	traceOK := !dead["run_trace"] && !dropped["record_edge"]
	d := SpineDeps{
		Info: at("info"), Warn: at("warning"), Debug: at("debug"),
		AbsentModules: dead, AutoRecoveryEventKey: "auto_recovery",
		ExecuteTools: []any{"default-tool"},
		GetToolsForRole: func(role, deny any) ([]any, error) {
			toolCalls++
			rec("_get_tools_for_role", pyval.Obj{
				{Key: "role", Val: role}, {Key: "deny", Val: deny}})
			if err := raise("_get_tools_for_role"); err != nil {
				return nil, err
			}
			return []any{"role-tool-" + strconv.Itoa(toolCalls)}, nil
		},
		DecomposeGoal: func(kw pyval.Obj) ([]any, error) {
			rec("_decompose_goal", kw)
			if err := raise("_decompose_goal"); err != nil {
				return nil, err
			}
			return s.DecomposeReturn, nil
		},
		PreflightChecks: func(kw pyval.Obj) ([]any, error) {
			rec("_preflight_checks", kw)
			if err := raise("_preflight_checks"); err != nil {
				return nil, err
			}
			return []any{s.PreflightSteps, s.PF,
				mkres(s.PreflightEarly)}, nil
		},
		PrepareExecution: func(kw pyval.Obj) ([]any, error) {
			rec("_prepare_execution", kw)
			if err := raise("_prepare_execution"); err != nil {
				return nil, err
			}
			return s.PrepareReturn, nil
		},
		ExecuteMainLoop: func(kw pyval.Obj) (map[string]any, error) {
			rec("_execute_main_loop", kw)
			if err := raise("_execute_main_loop"); err != nil {
				return nil, err
			}
			return s.EX, nil
		},
		BuildResultAndFinalize: func(kw pyval.Obj) (*looptypes.LoopResult,
			error) {
			rec("_build_result_and_finalize", kw)
			if err := raise("_build_result_and_finalize"); err != nil {
				return nil, err
			}
			return mkres(s.FinalizeResult), nil
		},
		WriteLoopLog: func(kw pyval.Obj) error {
			rec("_write_loop_log", kw)
			return raise("_write_loop_log")
		},
	}
	d.RunParallelPath = func(kw pyval.Obj) (*looptypes.LoopResult, error) {
		rec("_run_parallel_path", kw)
		if err := raise("_run_parallel_path"); err != nil {
			return nil, err
		}
		var fn func() ([]any, error)
		for _, f := range kw {
			if f.Key == "resolve_tools_fn" {
				fn, _ = f.Val.(func() ([]any, error))
			}
		}
		for i := 0; i < s.ResolveToolsCalls; i++ {
			tools, err := fn()
			if err != nil {
				return nil, err
			}
			rec("resolve_tools_result", pyval.Obj{
				{Key: "tools", Val: tools}})
		}
		return mkres(s.ParallelResult), nil
	}
	if traceOK {
		d.RecordEdge = func(from, to string, kw pyval.Obj) error {
			out := pyval.Obj{{Key: "from", Val: from},
				{Key: "to", Val: to}}
			out = append(out, kw...)
			rec("record_edge", out)
			return raise("record_edge")
		}
		d.PhaseTrace = func(from, to, loopID string) {
			rec("record_edge", pyval.Obj{{Key: "from", Val: from},
				{Key: "to", Val: to}, {Key: "loop_id", Val: loopID}})
			// set_phase's own `except Exception: pass`.
			_ = raise("record_edge")
		}
	}
	if !dead["loop_report"] {
		if !dropped["write_run_report"] {
			d.WriteRunReport = func(kw pyval.Obj) error {
				rec("write_run_report", kw)
				return raise("write_run_report")
			}
		}
		if !dropped["write_runs_index"] {
			d.WriteRunsIndex = func(force bool) error {
				rec("write_runs_index", pyval.Obj{
					{Key: "force", Val: force}})
				return raise("write_runs_index")
			}
		}
	}
	if !dropped["config_get"] {
		d.ConfigGet = func(key string, def any) (any, error) {
			rec("config_get", pyval.Obj{{Key: "key", Val: key},
				{Key: "default", Val: def}})
			if err := raise("config_get"); err != nil {
				return nil, err
			}
			return s.RunawayMultiplier, nil
		}
	}
	if !dropped["arm_cost_meter"] {
		d.ArmCostMeter = func(ceiling float64) (func() error, error) {
			rec("arm_cost_meter", pyval.Obj{
				{Key: "ceiling", Val: ceiling}})
			if err := raise("arm_cost_meter"); err != nil {
				return nil, err
			}
			return func() error {
				rec("disarm_cost_meter", pyval.Obj{})
				return raise("disarm_cost_meter")
			}, nil
		}
	}
	if !dead["introspect"] {
		if !dropped["diagnose_loop"] {
			d.DiagnoseLoop = func(loopID string) (any, error) {
				rec("diagnose_loop", pyval.Obj{
					{Key: "loop_id", Val: loopID}})
				if err := raise("diagnose_loop"); err != nil {
					return nil, err
				}
				if s.Diagnosis == nil {
					return nil, nil
				}
				return attrBag{s.Diagnosis, "diag"}, nil
			}
		}
		if !dropped["plan_recovery"] {
			d.PlanRecovery = func(diag any) (any, error) {
				rec("plan_recovery", pyval.Obj{
					{Key: "diag", Val: canonVal(diag)}})
				if err := raise("plan_recovery"); err != nil {
					return nil, err
				}
				if s.Plan == nil {
					return nil, nil
				}
				return attrBag{s.Plan, "plan"}, nil
			}
		}
	}
	if !dead["captains_log"] && !dropped["log_event"] {
		d.LogEvent = func(kw pyval.Obj) error {
			rec("log_event", kw)
			return raise("log_event")
		}
	}
	d.RunAgentLoop = func(kw pyval.Obj) (*looptypes.LoopResult, error) {
		rec("run_agent_loop", kw)
		if err := raise("run_agent_loop"); err != nil {
			return nil, err
		}
		return mkres(s.RecursionResult), nil
	}
	attr := func(bag any, name string) (any, error) {
		b, ok := bag.(attrBag)
		if !ok {
			return nil, &pyval.PyErr{Class: "AttributeError", Msg: name}
		}
		if err := raise(b.prefix + "." + name); err != nil {
			return nil, err
		}
		v, ok := b.m[name]
		if !ok {
			return nil, &pyval.PyErr{Class: "AttributeError", Msg: name}
		}
		return v, nil
	}
	d.RecoveryAutoApply = func(r any) (bool, error) {
		v, err := attr(r, "auto_apply")
		if err != nil {
			return false, err
		}
		return pyval.Truthy(v), nil
	}
	d.RecoveryRisk = func(r any) (string, error) {
		v, err := attr(r, "risk")
		if err != nil {
			return "", err
		}
		sv, _ := v.(string)
		return sv, nil
	}
	d.RecoveryAction = func(r any) (string, error) {
		v, err := attr(r, "action")
		if err != nil {
			return "", err
		}
		sv, _ := v.(string)
		return sv, nil
	}
	d.RecoveryParams = func(r any) (pyval.Obj, error) {
		v, err := attr(r, "params")
		if err != nil {
			return nil, err
		}
		out := pyval.Obj{}
		for _, p := range toAnyList(v) {
			pair := toAnyList(p)
			k, _ := pair[0].(string)
			out = append(out, pyval.Field{Key: k, Val: pair[1]})
		}
		return out, nil
	}
	d.DiagFailureClass = func(dg any) (string, error) {
		v, err := attr(dg, "failure_class")
		if err != nil {
			return "", err
		}
		sv, _ := v.(string)
		return sv, nil
	}

	res, err := RunSpine(ctx, a, d)
	out := map[string]any{"name": s.Name, "calls": calls, "logs": logs,
		"phase": ctx.Phase, "exc": excPair(err)}
	if err != nil {
		out["result"] = nil
	} else {
		out["result"] = canonVal(res)
	}
	return out
}

// attrBag is the probe's Attrs: an object whose attribute reads can raise
// and whose missing names are AttributeErrors.
type attrBag struct {
	m      map[string]any
	prefix string
}

func toAnyList(v any) []any {
	if l, ok := v.([]any); ok {
		return l
	}
	return nil
}

func runSpineProbe(t *testing.T, dir string,
	scs []spineSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "spine-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("spine_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// Through pyprobe, not exec.Command: this probe WRITES, and
	// running it by hand is how it ended up outside the sandbox,
	// the live-workspace refusal and the one shared module
	// Blocker. Two hand-rolled runners is how the last eight
	// started.
	out := pyprobe.Probe{Marker: "agent_loop.py",
		Workspace: t.TempDir()}.Run(t, string(src), srcDirAL(t),
		specPath)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestSpineMatchesCPython(t *testing.T) {
	scs := spineScenarios()
	pyRecs := runSpineProbe(t, t.TempDir(), scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got := canonFence(goSpineRecord(s))
			want := canonFence(pyRecs[i])
			if want["name"] != s.Name {
				t.Fatalf("record %d is %v, want %s", i, want["name"], s.Name)
			}
			a, _ := json.Marshal(got)
			b, _ := json.Marshal(want)
			if string(a) != string(b) {
				t.Errorf("GO %s\nPY %s", a, b)
			}
		})
	}
}

func TestSpineScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range spineScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}
