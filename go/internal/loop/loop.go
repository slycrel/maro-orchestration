// Package loop is the spine: decompose → execute steps → record.
//
// v0 scope, stated honestly: steps execute as single tool-less LLM calls
// (the subprocess backend runs --tools ""), there is no retry ladder, no
// re-decomposition, no closure verification, no memory recall — those are
// later port tranches (see PORT.md). What v0 DOES carry from day one are
// the invariants the Python runtime paid to learn:
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
}

type Result struct {
	Goal      string
	LoopID    string
	Status    string // "done" | "stuck"
	Steps     []StepOutcome
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

	if evErr := rec.Event("LOOP_STARTED", loopID,
		fmt.Sprintf("Go loop started: %d steps for %s", len(steps), budget.Clip(goal, 200)),
		map[string]any{"steps": len(steps), "backend": a.Name()}, loopID); evErr != nil {
		// Event-log failure must not kill the run, but it must not vanish
		// either — it rides the result so the caller can surface it.
		res.Warnings = append(res.Warnings, "captain's log write failed: "+evErr.Error())
	}
	var failChain []string

	for i, step := range steps {
		out := executeStep(ctx, a, goal, step, res.Steps)
		res.Steps = append(res.Steps, out)
		res.TokensIn += out.TokensIn
		res.TokensOut += out.TokensOut
		res.Warnings = append(res.Warnings, out.Warnings...)
		if out.Status != "done" {
			// The reason travels whole (marked breaker, not a truncator).
			failChain = append(failChain, budget.FailureChainEntry.Clip(
				fmt.Sprintf("step %d blocked: %s", i, out.Result)))
		}
	}

	done := 0
	for _, s := range res.Steps {
		if s.Status == "done" {
			done++
		}
	}
	res.Status = "stuck"
	if done == len(res.Steps) {
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
