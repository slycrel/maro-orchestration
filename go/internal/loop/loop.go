// Package loop is the spine: decompose → execute steps → record.
//
// Scope, stated honestly: steps execute either tool-less (single LLM
// call, the v0 path) or through the tool-bearing executor lane (exec.go,
// Opts.Exec — the worker agent's own tools do real work in the run's
// project dir, reporting via complete_step/flag_stuck, with inject_steps
// plan mutation). Still unported: retry ladder, re-decomposition,
// closure verification, memory recall — later tranches (see PORT.md).
// What the loop DOES carry from day one are the invariants the Python
// runtime paid to learn:
//
//   - evidence windows are budgeted and marked, never bare-sliced
//     (budget package);
//   - a blocked step's reason travels WHOLE to the failure chain and the
//     outcome row (the caps sweep's flagship fix);
//   - every error is recorded or returned — a step failure becomes a
//     blocked StepOutcome with the real error text, not an empty string.
package loop

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/planner"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

const stepSystem = `You are a capable worker executing one step of a larger
plan. Do the step's work directly in your reply: produce the analysis,
text, list, or answer the step asks for. Be concrete and complete; no
preamble about what you would do.`

type StepOutcome struct {
	Step      string
	Status    string // "done" | "blocked"
	Result    string
	TokensIn  int
	TokensOut int
	// Summary/Confidence come from the executor lane's complete_step
	// call (empty on the tool-less path).
	Summary    string
	Confidence string
	// Injected lists steps this step added to the plan (inject_steps).
	Injected []string
	// WasInjected marks a step the WORKER added mid-run (vs planner-
	// authored) — the audit trail an operator needs to spot scope creep
	// (adversarial exec review 2026-08-22, Expert QA).
	WasInjected bool
	// Warnings are adapter-reported oddities for this step (folded into
	// Result.Warnings by Run).
	Warnings []string
}

// Opts parameterizes a run. Goal is required; zero values elsewhere mean
// defaults. DryRun MUST be set when the adapter is canned/scripted —
// the row's dry_run field is what excludes synthetic rows from the
// Python learning funnel (adversarial r2 2026-08-22, Expert QA: a dry
// row stamped dry_run:false is a fabricated production record).
type Opts struct {
	Goal     string
	Model    string // "" = backend default
	MaxSteps int    // <=0 = 8
	DryRun   bool
	// Exec turns on the tool-bearing executor lane (exec.go): worker
	// steps run with the backend agent's own tools bound to the run's
	// project dir. Honored only when the backend reports
	// SupportsAgentTools — otherwise the run stays on the tool-less v0
	// path and says so in a warning (no silent mode change).
	Exec bool
}

type Result struct {
	Goal   string
	LoopID string
	Status string // "done" | "stuck"
	// ProjectDir is the executor lane's working directory (empty on the
	// tool-less path) — the path is part of the result, per the
	// data-retention doctrine.
	ProjectDir string
	Steps      []StepOutcome
	TokensIn  int
	TokensOut int
	Elapsed   time.Duration
	// Warnings carries non-fatal oddities (a captain's-log write that
	// failed, adapter parse suspects) to the CALLER instead of dying in a
	// print inside the loop. Honest scope (adversarial r3): the CLI
	// relays them to stderr; there is no durable sink, because the only
	// durable stores available live in the same workspace whose write
	// just failed — a store-write failure cannot be reliably recorded in
	// that store. Surfacing to the caller is the v0 ceiling; see PORT.md.
	Warnings []string
}

// Run executes the goal end-to-end and records the run. rec may not be
// nil — a run that leaves no record did not happen, per the delivery
// doctrine. opts.Model lands in the outcome row so cross-runtime
// analyses don't misattribute spend to a backend name (adversarial
// round 2026-08-22, Expert QA).
func Run(ctx context.Context, a llm.Adapter, rec *record.Recorder, opts Opts) (*Result, error) {
	if rec == nil {
		return nil, fmt.Errorf("nil recorder — runs must leave records")
	}
	goal, model, maxSteps := opts.Goal, opts.Model, opts.MaxSteps
	start := time.Now()
	// Random join key, same generator as outcome ids — a wall-clock
	// modulus collides across runs at sub-second cadence and loop_id is
	// the outcomes↔captains-log join key (adversarial round 2026-08-22).
	loopID := "go-" + record.NewID()
	recModel := model
	if recModel == "" {
		// The backend picks its own default; say so rather than passing
		// the backend name off as a model.
		recModel = a.Name() + "-default"
	}

	steps, planUse, err := planner.Decompose(ctx, a, rec.WorkspaceDir, goal, maxSteps)
	if err != nil {
		// Decompose failing IS an outcome; record it before returning —
		// including whatever the failed planning turn still spent. The
		// planning turn's parse-suspect diagnostics ride the failure
		// chain: this path returns no Result to carry Warnings, and the
		// chain is the stuck row's diagnostic surface (adversarial r4).
		chain := []string{budget.FailureChainEntry.Clip("decompose: " + err.Error())}
		for _, w := range planUse.Warnings {
			chain = append(chain, budget.FailureChainEntry.Clip("decompose warning: "+w))
		}
		_, recErr := rec.WriteOutcome(record.Outcome{
			Goal: goal, Status: "stuck", LoopID: loopID,
			Summary:   budget.FailureChainEntry.Clip("decompose failed: " + err.Error()),
			TaskType:  "loop",
			Model:     recModel,
			DryRun:    opts.DryRun,
			TokensIn:  planUse.TokensIn,
			TokensOut: planUse.TokensOut,
			ElapsedMS: time.Since(start).Milliseconds(),
			FailChain: chain,
		})
		if recErr != nil {
			return nil, fmt.Errorf("decompose failed (%v) AND recording failed: %w", err, recErr)
		}
		return nil, err
	}

	res := &Result{Goal: goal, LoopID: loopID,
		// Planning tokens are real spend; start the totals with them —
		// and the planning turn's warnings are diagnostics like any
		// step's (adversarial r4: they were dropped on both paths).
		TokensIn: planUse.TokensIn, TokensOut: planUse.TokensOut,
		Warnings: planUse.Warnings}

	// Executor lane: requested AND structurally available. A backend
	// that can't run agent tools degrades to the tool-less path with a
	// warning — degrading silently would fabricate "tool-bearing" runs.
	execMode := opts.Exec
	if execMode && !agentToolCapable(a) {
		execMode = false
		res.Warnings = append(res.Warnings, fmt.Sprintf(
			"exec mode requested but backend %q cannot run agent tools — using the tool-less step path",
			a.Name()))
	}
	if execMode {
		// Disambiguated resolution (project.go): a generic-opening goal
		// must not inherit an unrelated prior run's directory — with live
		// tools the second run would read and overwrite the first's files
		// (adversarial exec review 2026-08-22, all four lenses).
		projectsRoot := filepath.Join(rec.WorkspaceDir, "projects")
		slug, slugWarn := resolveProjectSlug(projectsRoot, goal)
		if slugWarn != "" {
			res.Warnings = append(res.Warnings, slugWarn)
		}
		res.ProjectDir = filepath.Join(projectsRoot, slug)
		dirExisted := false
		if _, statErr := os.Stat(res.ProjectDir); statErr == nil {
			dirExisted = true
		}
		err := os.MkdirAll(res.ProjectDir, 0o755)
		holdingSlot := false
		if err == nil {
			// Admission gate BEFORE the first project write: two runs
			// racing the same slug both pass resolveProjectSlug (its Stat
			// is check-then-act); the flock is what makes exactly one of
			// them proceed (adversarial exec r2 2026-08-22, Skeptic HIGH —
			// Python acquire_project_slot, refuse-immediately parity).
			release, gateWarn, gateErr := acquireProjectSlot(
				filepath.Join(rec.WorkspaceDir, "memory"), slug, loopID, goal)
			if gateWarn != "" {
				res.Warnings = append(res.Warnings, gateWarn)
			}
			if gateErr != nil {
				err = gateErr
			} else if release != nil {
				holdingSlot = true
				defer release()
			}
		}
		if err == nil {
			err = recordProjectMission(res.ProjectDir, goal)
		}
		if err != nil {
			// A dir THIS run created and never wrote into is removed
			// (os.Remove refuses non-empty dirs, so pre-existing work is
			// structurally safe — data-retention doctrine): a persistently
			// failing setup would otherwise accumulate mission-less
			// project dirs that read as work that never happened
			// (adversarial exec r2 2026-08-22, Expert QA). ONLY while
			// holding the slot: a busy-refused loser that raced the
			// winner past MkdirAll would otherwise rmdir the still-empty
			// dir the winner just won the right to use, failing BOTH runs
			// with a record that blames dir setup (adversarial exec r3
			// 2026-08-22, Expert QA HIGH — the r2 cleanup undermined the
			// r2 flock; Python never deletes on LoopBusy).
			if !dirExisted && holdingSlot {
				_ = os.Remove(res.ProjectDir)
			}
			// Tool-bearing steps with no bound workspace would write to
			// an arbitrary inherited cwd — refuse, don't drift. But the
			// refusal itself is an outcome: planning already spent real
			// tokens, and a run that leaves no record did not happen
			// (adversarial exec review 2026-08-22, Expert QA — the
			// decompose branch above records, this path didn't).
			setupErr := fmt.Errorf("exec mode: cannot set up project dir %s: %w",
				res.ProjectDir, err)
			if _, recErr := rec.WriteOutcome(record.Outcome{
				Goal: goal, Status: "stuck", LoopID: loopID,
				Summary:   budget.FailureChainEntry.Clip("project dir setup failed: " + err.Error()),
				TaskType:  "loop",
				Model:     recModel,
				DryRun:    opts.DryRun,
				TokensIn:  res.TokensIn,
				TokensOut: res.TokensOut,
				ElapsedMS: time.Since(start).Milliseconds(),
				FailChain: []string{budget.FailureChainEntry.Clip(setupErr.Error())},
			}); recErr != nil {
				return nil, fmt.Errorf("%v AND recording failed: %w", setupErr, recErr)
			}
			return nil, setupErr
		}
	}

	if evErr := rec.Event("LOOP_STARTED", loopID,
		fmt.Sprintf("Go loop started: %d steps for %s", len(steps), budget.Clip(goal, 200)),
		map[string]any{"steps": len(steps), "backend": a.Name(),
			"exec": execMode, "project_dir": res.ProjectDir}, loopID); evErr != nil {
		// Event-log failure must not kill the run, but it must not vanish
		// either — it rides the result so the caller can surface it.
		res.Warnings = append(res.Warnings, "captain's log write failed: "+evErr.Error())
	}
	var failChain []string

	// Mutable step queue: inject_steps splice at the FRONT (Python
	// remaining_steps[:0] = injected). Total executed is capped at 2x the
	// planned count — an honest stand-in for Python's max_iterations
	// machinery (adaptive bumping not ported); hitting it marks the run
	// stuck with the remainder NAMED (contents, not just a count) in the
	// failure chain.
	type queuedStep struct {
		text     string
		injected bool // worker-added via inject_steps, not planner-authored
	}
	queue := make([]queuedStep, 0, len(steps))
	for _, s := range steps {
		queue = append(queue, queuedStep{text: s})
	}
	// Returns the RAW join — the caller clips the fully-assembled entry
	// exactly once. Clip(prefix)+Clip(remainder) concatenated after
	// clipping produced single failure_chain entries up to ~2× the
	// documented per-entry budget (adversarial exec r2 2026-08-22,
	// Expert QA — the budget doctrine is "bounds ONE entry").
	nameRemainder := func() string {
		texts := make([]string, len(queue))
		for i, q := range queue {
			texts[i] = q.text
		}
		return strings.Join(texts, "; ")
	}
	capTotal := 2 * len(steps)
	haltedEarly := false
	for len(queue) > 0 {
		if len(res.Steps) >= capTotal {
			haltedEarly = true
			failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
				"step budget exhausted: %d executed (cap %d) with %d step(s) remaining: ",
				len(res.Steps), capTotal, len(queue))+nameRemainder()))
			break
		}
		step := queue[0]
		queue = queue[1:]
		var out StepOutcome
		var injected []string
		if execMode {
			out, injected = executeExecStep(ctx, a, goal, step.text,
				len(res.Steps)+1, len(res.Steps)+1+len(queue), res.Steps, res.ProjectDir)
		} else {
			out = executeStep(ctx, a, goal, step.text, res.Steps)
		}
		out.WasInjected = step.injected
		res.Steps = append(res.Steps, out)
		res.TokensIn += out.TokensIn
		res.TokensOut += out.TokensOut
		res.Warnings = append(res.Warnings, out.Warnings...)
		if out.Status != "done" {
			// The reason travels whole (marked breaker, not a truncator).
			failChain = append(failChain, budget.FailureChainEntry.Clip(
				fmt.Sprintf("step %d blocked: %s", len(res.Steps)-1, out.Result)))
			if execMode {
				// EXEC MODE HALTS on the first non-done step. Python routes
				// a blocked step through loop_blocked's retry/re-decompose
				// ladder and breaks on its terminal verdict; none of that
				// ladder is ported, and "no retry ladder" must not silently
				// become "no stop conditions" — later steps hold a live
				// Bash-capable agent that would keep acting on a plan whose
				// premise just failed (adversarial exec review 2026-08-22,
				// Skeptic HIGH). The tool-less lane keeps v0's run-through:
				// its steps are single LLM calls whose prior-evidence
				// filter already excludes blocked results, and partial
				// completion still yields a useful summary.
				if len(queue) > 0 {
					haltedEarly = true
					failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
						"halted after blocked step: %d step(s) not executed: ",
						len(queue))+nameRemainder()))
					break
				}
			}
		}
		if len(injected) > 0 {
			add := make([]queuedStep, len(injected))
			for i, s := range injected {
				add[i] = queuedStep{text: s, injected: true}
			}
			queue = append(add, queue...)
		}
	}

	done := 0
	for _, s := range res.Steps {
		if s.Status == "done" {
			done++
		}
	}
	res.Status = "stuck"
	if done == len(res.Steps) && !haltedEarly {
		res.Status = "done"
	}
	res.Elapsed = time.Since(start)

	summary := fmt.Sprintf("Completed %d/%d steps.", done, len(res.Steps))
	if last := lastResult(res.Steps); last != "" {
		summary += " " + budget.StepResult.Clip(last)
	}
	if _, err := rec.WriteOutcome(record.Outcome{
		Goal: goal, Status: res.Status, Summary: summary,
		TaskType: "loop", Model: recModel, LoopID: loopID,
		DryRun:   opts.DryRun,
		TokensIn: res.TokensIn, TokensOut: res.TokensOut,
		ElapsedMS: res.Elapsed.Milliseconds(), FailChain: failChain,
	}); err != nil {
		return res, fmt.Errorf("run finished (%s) but outcome recording failed: %w",
			res.Status, err)
	}
	if err := rec.Event("LOOP_FINISHED", loopID,
		fmt.Sprintf("Go loop %s: %d/%d steps done", res.Status, done, len(res.Steps)),
		map[string]any{"status": res.Status, "tokens_in": res.TokensIn,
			"tokens_out": res.TokensOut}, loopID); err != nil {
		res.Warnings = append(res.Warnings, "captain's log write failed: "+err.Error())
	}
	return res, nil
}

func executeStep(ctx context.Context, a llm.Adapter, goal, step string, prior []StepOutcome) StepOutcome {
	var sb strings.Builder
	fmt.Fprintf(&sb, "Overall goal: %s\n\nYour step: %s\n", goal, step)
	sb.WriteString(renderPrior(prior))
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: stepSystem},
		{Role: "user", Content: sb.String()},
	}, llm.Options{MaxTokens: 2048, Temperature: 0.3, Purpose: "execute-step"})
	if err != nil {
		out := StepOutcome{Step: step, Status: "blocked", Result: err.Error()}
		// A failed turn still spent tokens; salvage the usage the adapter
		// carried on the typed error so blocked steps don't under-report
		// spend (adversarial round 2026-08-22, Expert QA).
		var re *llm.ResultError
		if errors.As(err, &re) {
			out.TokensIn, out.TokensOut = re.TokensIn, re.TokensOut
			out.Warnings = re.Warnings
		}
		return out
	}
	if strings.TrimSpace(resp.Content) == "" {
		return StepOutcome{Step: step, Status: "blocked",
			Result: "worker produced no output", Warnings: resp.Warnings}
	}
	return StepOutcome{Step: step, Status: "done", Result: resp.Content,
		TokensIn: resp.TokensIn, TokensOut: resp.TokensOut,
		Warnings: resp.Warnings}
}

// renderPrior renders earlier step results for the next prompt: DONE
// steps only — a blocked step's Result is process diagnostics (CLI
// crash text, whatever a failing subprocess echoed), and serving that
// as "results from earlier steps" both confuses the worker and funnels
// untrusted failure output into a live LLM call (adversarial r3
// 2026-08-22, Skeptic; Python's director gates identically:
// `result.status == "done" and result.result`). Failure information
// still reaches the failure chain and the outcome row — records, not
// worker prompts. Each kept entry rides the per-entry StepResult
// window, and the WHOLE block is bounded by StepContextTotal with
// oldest-first eviction, marked, never silent (r2).
func renderPrior(prior []StepOutcome) string {
	var done []StepOutcome
	doneIdx := make([]int, 0, len(prior))
	for i, p := range prior {
		if p.Status == "done" && strings.TrimSpace(p.Result) != "" {
			done = append(done, p)
			doneIdx = append(doneIdx, i)
		}
	}
	if len(done) == 0 {
		return ""
	}
	prior = done
	blocks := make([]string, len(prior))
	total := 0
	keepFrom := len(prior)
	for i := len(prior) - 1; i >= 0; i-- {
		b := fmt.Sprintf("--- step %d [%s] ---\n%s\n", doneIdx[i], prior[i].Status,
			budget.StepResult.Clip(prior[i].Result))
		n := len([]rune(b))
		// The newest entry always rides (a clipped entry fits by
		// construction); older ones ride until the total budget is spent.
		if i < len(prior)-1 && total+n > budget.StepContextTotal.Limit {
			break
		}
		blocks[i] = b
		total += n
		keepFrom = i
	}
	var sb strings.Builder
	sb.WriteString("\nResults from earlier steps:\n")
	if keepFrom > 0 {
		fmt.Fprintf(&sb,
			"[%d earlier step result(s) evicted to fit the %d-char context budget — oldest first]\n",
			keepFrom, budget.StepContextTotal.Limit)
	}
	for i := keepFrom; i < len(prior); i++ {
		sb.WriteString(blocks[i])
	}
	return sb.String()
}

func lastResult(steps []StepOutcome) string {
	for i := len(steps) - 1; i >= 0; i-- {
		if steps[i].Status == "done" && strings.TrimSpace(steps[i].Result) != "" {
			return steps[i].Result
		}
	}
	return ""
}
