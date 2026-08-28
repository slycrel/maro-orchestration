package agentloop

// run_agent_loop's head: `src/agent_loop.py:119-244`, everything from the
// signature to the execution fence's first statement.
//
// It computes nothing. What it does is BIND — one call into
// _initialize_loop with twenty-six keywords, an early return, an ambient
// loop_id scope, and six locals rebound out of the context that every
// later phase reads instead of the caller's arguments. The port's job here
// is to get an assignment list right, which sounds like the part that
// cannot go wrong and is the part that already did: the fence read the
// `project` KEYWORD where Python reads `ctx.project`, and picked a
// different directory for the whole run.
//
// Three orderings in this span are observable and none of them are
// obvious:
//
//   - `from captains_log import loop_id_scope` runs AFTER the early
//     return. A run that _initialize_loop turns away never imports it, so
//     a missing captains_log is invisible on that path and fatal on the
//     other.
//   - `from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER` runs INSIDE
//     the scope. Its failure has to leave the scope the way any other
//     exception does.
//   - The tier map is built from those three constants as KEYS. Two
//     constants with the same value collapse into one entry, and the
//     LAST of the colliding pairs wins the index — a property of the
//     dict literal, not of the tier logic.

import (
	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// HeadArgs is run_agent_loop's signature, in source order.
//
// Everything is `any` that Python passes through untouched, because this
// span never inspects those values — it hands them to _initialize_loop and
// to the phases. Typing them here would be the port claiming to know
// something about them that the function does not.
type HeadArgs struct {
	Goal string

	Project              any
	RepoPath             string
	Model                any
	Backend              any
	Adapter              any
	KnowledgeSubGoals    bool
	MaxSteps             int
	MaxIterations        int
	DryRun               bool
	Verbose              bool
	InterruptQueue       any
	HookRegistry         any
	AncestryContextExtra string
	StepCallback         any
	ParallelFanOut       int
	TokenBudget          any
	CostBudget           any
	RalphVerify          bool
	ResumeFromLoopID     any
	PermissionContext    any
	ContinuationDepth    int
	PresetSteps          any
	Channel              any
	LoopReason           string
	ParentLoopID         any
	AdmissionWaitS       any
	DeferLearning        bool
	DeferMaintenance     bool
	MeasurementClass     string
	HandleID             string
	IntrospectionAccess  bool
	RecoveryInProgress   bool
}

// HeadCtx is the slice of the context this span reads and writes.
//
// Six of these fields are the SOURCE of the six rebinds. They are listed
// here rather than read through an interface because the rebinding is the
// subject: `project = ctx.project` overwrites the caller's keyword, and a
// port that keeps two names for one value will eventually use the wrong
// one.
type HeadCtx struct {
	LoopID         string
	StartTS        any
	Project        any
	Adapter        any
	InterruptQueue any
	PermCtx        any

	// Channel is ASSIGNED here — Phase 64C's mid-loop escalation channel
	// arrives as a keyword and is parked on the context for the phases
	// that read it.
	Channel any
}

// Bound is what the head hands to the rest of run_agent_loop: the context,
// the tier map, and the six values that are NOT the caller's arguments any
// more.
//
// It exists because the fence and the spine are two other chunks of the
// same Python function, and the thing they must agree on is exactly this
// set. A struct makes the disagreement a compile error.
type Bound struct {
	Ctx *HeadCtx

	LoopID         string
	StartTS        any
	Project        any
	Adapter        any
	InterruptQueue any
	PermCtx        any

	// TierOrder is the session-level tier floor ordering (Phase 57).
	TierOrder map[string]int
}

// HeadDeps is everything the span reaches outside itself.
type HeadDeps struct {
	// InitializeLoop is _initialize_loop. It returns a context and an
	// OPTIONAL early return; Python's `if _early_return is not None` is a
	// None check and nothing else, so a falsy-but-present result still
	// ends the run.
	InitializeLoop func(goal string, kw pyval.Obj) (*HeadCtx, *looptypes.LoopResult, error)

	// LoopIDScope is captains_log.loop_id_scope, imported after the early
	// return. It returns the exit half of the `with`, which runs however
	// the body leaves.
	//
	// The import and the call are separate fields because they fail in
	// separate places: a missing captains_log raises at the import, above
	// the scope, and a loop_id_scope that raises when CALLED does so
	// inside `with`'s setup, before the body — and in neither case does
	// the exit half run.
	ImportLoopIDScope func() error
	LoopIDScope       func(loopID string) (func() error, error)

	// ImportTierConstants is `from llm import MODEL_CHEAP, MODEL_MID,
	// MODEL_POWER`, INSIDE the scope. Returning the three values rather
	// than reading them from a package keeps the failure at the import,
	// which is where Python's is.
	ImportTierConstants func() (cheap, mid, power string, err error)

	// Body is the rest of run_agent_loop — the execution fence and the
	// orchestration spine, which are ported as their own chunks. Whatever
	// it returns is what the function returns, and however it leaves, the
	// scope exits.
	Body func(b Bound) (*looptypes.LoopResult, error)
}

// RunAgentLoopHead runs the span.
func RunAgentLoopHead(a HeadArgs, d HeadDeps) (*looptypes.LoopResult, error) {
	// Phase A: Initialize loop state.
	//
	// `goal` is positional and the other twenty-six are keywords, in this
	// order. Seven of run_agent_loop's own parameters are NOT among them —
	// knowledge_sub_goals, parallel_fan_out, preset_steps, channel,
	// introspection_access, resume_from_loop_id and _recovery_in_progress
	// — because they belong to phases further down. A port that "tidied"
	// this into "pass the whole signature" would change what
	// _initialize_loop sees.
	ctx, early, err := d.InitializeLoop(a.Goal, pyval.Obj{
		{Key: "project", Val: a.Project},
		{Key: "repo_path", Val: a.RepoPath},
		{Key: "model", Val: a.Model},
		{Key: "backend", Val: a.Backend},
		{Key: "adapter", Val: a.Adapter},
		{Key: "dry_run", Val: a.DryRun},
		{Key: "verbose", Val: a.Verbose},
		{Key: "interrupt_queue", Val: a.InterruptQueue},
		{Key: "hook_registry", Val: a.HookRegistry},
		{Key: "ancestry_context_extra", Val: a.AncestryContextExtra},
		{Key: "permission_context", Val: a.PermissionContext},
		{Key: "continuation_depth", Val: a.ContinuationDepth},
		{Key: "cost_budget", Val: a.CostBudget},
		{Key: "token_budget", Val: a.TokenBudget},
		{Key: "ralph_verify", Val: a.RalphVerify},
		{Key: "max_steps", Val: a.MaxSteps},
		{Key: "max_iterations", Val: a.MaxIterations},
		{Key: "step_callback", Val: a.StepCallback},
		{Key: "loop_reason", Val: a.LoopReason},
		{Key: "parent_loop_id", Val: a.ParentLoopID},
		{Key: "admission_wait_s", Val: a.AdmissionWaitS},
		{Key: "defer_learning", Val: a.DeferLearning},
		{Key: "defer_maintenance", Val: a.DeferMaintenance},
		{Key: "measurement_class", Val: a.MeasurementClass},
		{Key: "handle_id", Val: a.HandleID},
	})
	if err != nil {
		return nil, err
	}
	// `is not None`. A LoopResult carries no __bool__, so this can never
	// be anything but a nil check — but the port spells it as one rather
	// than as truthiness, because the day the result grows a __bool__ the
	// two stop agreeing.
	if early != nil {
		return early, nil
	}

	// BACKLOG #17 sub-item 1: scope the ambient loop_id for the duration
	// of this run so log_event() calls deep in the execution call stack
	// (skills.py, evolver.py, knowledge_lens.py, ...) get attributed
	// without threading loop_id through every signature.
	//
	// Below the early return, so a run that never starts never imports it.
	if err := d.ImportLoopIDScope(); err != nil {
		return nil, err
	}
	exit, err := d.LoopIDScope(ctx.LoopID)
	if err != nil {
		// `with`'s setup raised: there is no context manager yet, so
		// nothing to exit.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): calling exit()
		// here anyway. A context manager whose __enter__ raised does not
		// exist, so the dep cannot hand back both an error and an exit
		// half, and no input reaches a state where the extra call does
		// anything.
		return nil, err
	}
	res, bodyErr := d.runScoped(ctx, a)
	// The `with` exits however the body left — and an exception from the
	// exit itself REPLACES the body's, which is why this is not spelled
	// as a plain defer swallowing its error.
	if exitErr := exit(); exitErr != nil {
		return nil, exitErr
	}
	if bodyErr != nil {
		return nil, bodyErr
	}
	return res, nil
}

// runScoped is the body of the `with` block. It is separate so that every
// path out of it — including a raise — passes through the exit above.
func (d HeadDeps) runScoped(ctx *HeadCtx, a HeadArgs) (*looptypes.LoopResult,
	error) {
	ctx.Channel = a.Channel // Phase 64C: mid-loop escalation channel

	// Model constants for the session-level tier floor ordering (Phase 57).
	cheap, mid, power, err := d.ImportTierConstants()
	if err != nil {
		return nil, err
	}
	// A dict literal, so a repeated key keeps the LAST index rather than
	// the first, and three constants that are not distinct produce a map
	// with fewer than three entries. Neither is hypothetical: the tiers
	// are configuration strings.
	tierOrder := map[string]int{}
	tierOrder[cheap] = 0
	tierOrder[mid] = 1
	tierOrder[power] = 2

	// Unpack ctx into the locals the orchestrator still threads into phase
	// calls and the auto-recovery re-run. Five of the six shadow a
	// parameter of the same name, and the context's value is the one that
	// wins from here down.
	return d.Body(Bound{
		Ctx:            ctx,
		LoopID:         ctx.LoopID,
		StartTS:        ctx.StartTS,
		Project:        ctx.Project,
		Adapter:        ctx.Adapter,
		InterruptQueue: ctx.InterruptQueue,
		PermCtx:        ctx.PermCtx,
		TierOrder:      tierOrder,
	})
}
