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
}

type Result struct {
	Goal      string
	LoopID    string
	Status    string // "done" | "stuck"
	Steps     []StepOutcome
	TokensIn  int
	TokensOut int
	Elapsed   time.Duration
}

// Run executes goal end-to-end and records the run. rec may not be nil —
// a run that leaves no record did not happen, per the delivery doctrine.
func Run(ctx context.Context, a llm.Adapter, rec *record.Recorder, goal string, maxSteps int) (*Result, error) {
	if rec == nil {
		return nil, fmt.Errorf("nil recorder — runs must leave records")
	}
	start := time.Now()
	loopID := fmt.Sprintf("go-%d", start.UnixNano()%1_000_000_00)

	steps, err := planner.Decompose(ctx, a, goal, maxSteps)
	if err != nil {
		// Decompose failing IS an outcome; record it before returning.
		_, recErr := rec.WriteOutcome(record.Outcome{
			Goal: goal, Status: "stuck", LoopID: loopID,
			Summary:   budget.FailureChainEntry.Clip("decompose failed: " + err.Error()),
			TaskType:  "loop",
			Model:     a.Name(),
			ElapsedMS: time.Since(start).Milliseconds(),
			FailChain: []string{budget.FailureChainEntry.Clip("decompose: " + err.Error())},
		})
		if recErr != nil {
			return nil, fmt.Errorf("decompose failed (%v) AND recording failed: %w", err, recErr)
		}
		return nil, err
	}

	if evErr := rec.Event("LOOP_STARTED", loopID,
		fmt.Sprintf("Go loop started: %d steps for %s", len(steps), budget.Clip(goal, 200)),
		map[string]any{"steps": len(steps), "backend": a.Name()}, loopID); evErr != nil {
		// Event-log failure must not kill the run, but it must not vanish
		// either — it rides the result so the caller can surface it.
		fmt.Printf("warn: captain's log write failed: %v\n", evErr)
	}

	res := &Result{Goal: goal, LoopID: loopID}
	var failChain []string

	for i, step := range steps {
		out := executeStep(ctx, a, goal, step, res.Steps)
		res.Steps = append(res.Steps, out)
		res.TokensIn += out.TokensIn
		res.TokensOut += out.TokensOut
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
		TaskType: "loop", Model: a.Name(), LoopID: loopID,
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
		fmt.Printf("warn: captain's log write failed: %v\n", err)
	}
	return res, nil
}

func executeStep(ctx context.Context, a llm.Adapter, goal, step string, prior []StepOutcome) StepOutcome {
	var sb strings.Builder
	fmt.Fprintf(&sb, "Overall goal: %s\n\nYour step: %s\n", goal, step)
	if len(prior) > 0 {
		sb.WriteString("\nResults from earlier steps:\n")
		for i, p := range prior {
			// Prior evidence rides the measured step-result window, marked
			// when cut — the exact discipline the Python audit retrofitted.
			fmt.Fprintf(&sb, "--- step %d [%s] ---\n%s\n", i, p.Status,
				budget.StepResult.Clip(p.Result))
		}
	}
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: stepSystem},
		{Role: "user", Content: sb.String()},
	}, llm.Options{MaxTokens: 2048, Temperature: 0.3, Purpose: "execute-step"})
	if err != nil {
		return StepOutcome{Step: step, Status: "blocked", Result: err.Error()}
	}
	if strings.TrimSpace(resp.Content) == "" {
		return StepOutcome{Step: step, Status: "blocked",
			Result: "worker produced no output"}
	}
	return StepOutcome{Step: step, Status: "done", Result: resp.Content,
		TokensIn: resp.TokensIn, TokensOut: resp.TokensOut}
}

func lastResult(steps []StepOutcome) string {
	for i := len(steps) - 1; i >= 0; i-- {
		if steps[i].Status == "done" && strings.TrimSpace(steps[i].Result) != "" {
			return steps[i].Result
		}
	}
	return ""
}
