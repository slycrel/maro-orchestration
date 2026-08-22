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
	"github.com/slycrel/maro-orchestration/go/internal/closure"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/planner"
	"github.com/slycrel/maro-orchestration/go/internal/recall"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
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
	// StuckReason/Attempted mirror Python's separate blocked-outcome
	// fields (stuck_reason and result — for flag_stuck, result IS the
	// attempted text, step_exec.py:1948-49). The ladder's fingerprint
	// and missing-input checks read the typed pair; Result keeps the
	// folded human-readable form for the failure chain. Folding both
	// into Result gave reason+attempted ONE shared 200-char fingerprint
	// head instead of Python's independent 200+200 (adversarial ladder
	// review 2026-08-22 — three lenses independently).
	StuckReason string
	Attempted   string
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
	// SeedTokensIn/Out carry spend the caller made BEFORE the loop on
	// this goal's behalf (the routing classify call) so the outcome row
	// reports full real cost, not just the loop's share.
	SeedTokensIn  int
	SeedTokensOut int
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
	TokensIn   int
	TokensOut  int
	Elapsed    time.Duration
	// Warnings carries non-fatal oddities (a captain's-log write that
	// failed, adapter parse suspects) to the CALLER instead of dying in a
	// print inside the loop. Honest scope (adversarial r3): the CLI
	// relays them to stderr; there is no durable sink, because the only
	// durable stores available live in the same workspace whose write
	// just failed — a store-write failure cannot be reliably recorded in
	// that store. Surfacing to the caller is the v0 ceiling; see PORT.md.
	Warnings []string
	// StopVerdict/StuckReason are the typed terminal columns (Python
	// stop_verdict / stuck_reason). The failure-chain TEXT was their only
	// carrier, and the per-entry clip could truncate the verdict tag and
	// the do-not-fabricate instruction on long reasons — the exact
	// guarantee the MISSING_INPUT branch exists to make (adversarial
	// ladder r2 2026-08-22, Skeptic + Expert QA HIGH, independently).
	StopVerdict string
	StuckReason string
	// Closure is the goal-level completion verdict (closure tranche) —
	// nil when closure did not run (tool-less lane, dry-run, stuck run,
	// or no run dir). done ≠ successful: Status says the steps drained;
	// Closure says whether the GOAL was achieved.
	Closure *closure.Verdict
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

	// Run dir + metadata (closure tranche): the writer half of the
	// contract recall.FindPriorAttempts reads — created BEFORE recall so
	// a crash mid-run still leaves a scannable "running" attempt, and
	// excluded from recall below so the run never reads its own
	// seconds-old metadata as a prior attempt. Best-effort: a run-dir
	// failure degrades to the pre-tranche world (no metadata, no
	// closure persistence), never blocks the run.
	runDir, rdErr := runs.Create(rec.WorkspaceDir, loopID, goal)
	var recallWarns []string
	if rdErr != nil {
		runDir = ""
		recallWarns = append(recallWarns, "run-dir setup failed: "+rdErr.Error())
	}

	// Memory recall before planning (recall tranche): ranked tiered
	// lessons + prior-attempt context ride the decompose prompt, the
	// same two extras channels Python's loop feeds decompose
	// (lessons_context via loop_planning, ancestry_context via
	// handle.py's as_context_block). The seam is read-only and every
	// failure degrades to "knows nothing" — a broken memory store must
	// never block a run. project is "" until the loop threads a project
	// concept pre-decompose (the exec lane resolves its slug later).
	rr := recall.RecallExcluding(rec.WorkspaceDir, goal, "", loopID)
	// Instrumented from day one, like Python's RECALL_PERFORMED — the
	// emission lives at this call site rather than inside the seam
	// (recall keeps no recorder handle by design; named in PORT.md).
	recallCtx := map[string]any{"goal_preview": budget.Clip(goal, 200)}
	for k, v := range rr.Sources {
		recallCtx[k] = v
	}
	if evErr := rec.Event("RECALL_PERFORMED", "recall",
		fmt.Sprintf("recall slice=loop: %d prior attempts.", len(rr.PriorAttempts)),
		recallCtx, loopID); evErr != nil {
		// Event-log failure must not kill the run, but it must not
		// vanish either — held here until a carrier exists (Result on
		// the happy path, the failure chain when decompose dies).
		recallWarns = append(recallWarns,
			"captain's log write failed (RECALL_PERFORMED): "+evErr.Error())
	}

	steps, planUse, err := planner.Decompose(ctx, a, rec.WorkspaceDir, goal, maxSteps, rr.DecomposeExtras()...)
	// Seed spend (the routing classify call) is real cost this run
	// caused — fold it in HERE so both exits (decompose-failed row and
	// the normal totals) report it (adversarial routing r1 2026-08-22:
	// classify usage vanished from every record).
	planUse.TokensIn += opts.SeedTokensIn
	planUse.TokensOut += opts.SeedTokensOut
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
		for _, w := range recallWarns {
			chain = append(chain, budget.FailureChainEntry.Clip(w))
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
		if runDir != "" {
			_ = runs.Finalize(runDir, "stuck")
		}
		return nil, err
	}

	res := &Result{Goal: goal, LoopID: loopID,
		// Planning tokens are real spend; start the totals with them —
		// and the planning turn's warnings are diagnostics like any
		// step's (adversarial r4: they were dropped on both paths).
		TokensIn: planUse.TokensIn, TokensOut: planUse.TokensOut,
		Warnings: append(recallWarns, planUse.Warnings...)}

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
			if runDir != "" {
				_ = runs.Finalize(runDir, "stuck")
			}
			return nil, setupErr
		}
	}

	// Python shapes the INITIAL plan too (_prepare_execution →
	// _shape_steps label="initial-plan", loop_planning.py:87, called
	// unconditionally in agent_loop before any lane branch) — without
	// this a combined exec+analyze step burns a full worker call and
	// records a blocked outcome before the ladder's reactive split ever
	// sees it (adversarial ladder r1 2026-08-22, Minimalist HIGH).
	// Unconditional, both lanes: the r1 fix gated this to exec mode as
	// an unnamed narrowing; Python splits combined steps on the
	// tool-less path as well (r2, Skeptic).
	steps = shapeSteps(steps)

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
		injected bool   // worker-added via inject_steps, not planner-authored
		hint     string // blocked-retry hint, injected into the retry prompt
	}
	queue := make([]queuedStep, 0, len(steps))
	for _, s := range steps {
		queue = append(queue, queuedStep{text: s})
	}
	// Blocked-ladder state (blocked.go): per-step retry counts and error
	// fingerprints, run-wide replan budget, consecutive-timeout streak.
	stepRetries := map[string]int{}
	stepFingerprints := map[string][]string{}
	replanCount := 0
	consecTimeouts := 0
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
stepLoop:
	for len(queue) > 0 {
		if len(res.Steps) >= capTotal {
			haltedEarly = true
			// The FOURTH terminal site — Python types this exact halt too
			// (loop_execute.py:492-503, stamp_stop("out-of-budget", "hit
			// max_iterations=...")). r2 stamped the three verdict
			// terminals and missed this one; a runaway-inject halt
			// persisted stop_verdict:"" and read as unstamped
			// (adversarial ladder r3 2026-08-22, Expert QA HIGH).
			res.StopVerdict = "out-of-budget"
			res.StuckReason = fmt.Sprintf(
				"step budget exhausted: %d executed (cap %d) with %d step(s) remaining",
				len(res.Steps), capTotal, len(queue))
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
			out, injected = executeExecStep(ctx, a, goal, step.text, step.hint,
				len(res.Steps)+1, len(res.Steps)+1+len(queue), res.Steps, res.ProjectDir)
		} else {
			out = executeStep(ctx, a, goal, step.text, res.Steps)
		}
		out.WasInjected = step.injected
		res.Steps = append(res.Steps, out)
		res.TokensIn += out.TokensIn
		res.TokensOut += out.TokensOut
		res.Warnings = append(res.Warnings, out.Warnings...)
		if out.Status == "done" {
			// Successful step — the adapter is healthy. Python resets the
			// streak on EVERY done step (loop_execute.py:1884); resetting
			// only on non-timeout splits let non-consecutive timeouts
			// accumulate to a false adapter-hung bail (adversarial ladder
			// review 2026-08-22, Expert QA HIGH).
			consecTimeouts = 0
		}
		if out.Status != "done" {
			// The reason travels whole (marked breaker, not a truncator).
			failChain = append(failChain, budget.FailureChainEntry.Clip(
				fmt.Sprintf("step %d blocked: %s", len(res.Steps)-1, out.Result)))
			if execMode {
				// The blocked ladder (blocked.go, Python _handle_blocked_
				// step): retry / split / re-decompose on evidence, and HALT
				// on its terminal verdict — the exec lane must never keep a
				// live Bash-capable agent acting past a verdict that the
				// plan's premise failed (adversarial exec review
				// 2026-08-22, Skeptic HIGH; the flat first-block halt was
				// the ladder's stand-in until this tranche). The tool-less
				// lane keeps v0's run-through: its steps are single LLM
				// calls whose prior-evidence filter already excludes
				// blocked results.
				// Python hashes (stuck_reason, result) as independent
				// 200-char heads — result IS the attempted text for a
				// flag_stuck block. Feeding the folded Result gave both
				// one shared head and truncated `attempted` away on long
				// reasons (adversarial ladder review 2026-08-22, three
				// lenses).
				fpReason := out.StuckReason
				if fpReason == "" {
					fpReason = out.Result
				}
				fps := append(stepFingerprints[step.text],
					errorFingerprint(fpReason, out.Attempted))
				stepFingerprints[step.text] = fps
				retries := stepRetries[step.text]
				// Siblings exclude the CURRENT attempt: Python decides
				// before appending to step_outcomes (loop_execute.py:1929
				// and the recovery-branch appends inside
				// _process_blocked_step) — self-counting inflated the
				// sibling failure rate and fired premature redecompose on
				// small plans (adversarial ladder review 2026-08-22,
				// Skeptic + Expert QA HIGH). Prior attempts of this same
				// step DO stay in — that is Python's behavior too.
				// The slice ALIASES res.Steps' backing array: safe only
				// because handleBlockedStep reads it synchronously and
				// returns before the next append — never let it escape
				// the call (r2, Skeptic).
				d := handleBlockedStep(ctx, a, step.text, out, retries, fps,
					res.Steps[:len(res.Steps)-1], replanCount)
				res.TokensIn += d.tokensIn
				res.TokensOut += d.tokensOut
				action := "stuck"
				switch {
				case d.retry:
					action = "retry"
				case d.redecompose:
					action = "redecompose"
				case len(d.splitInto) > 0:
					action = "split"
				}
				// Mirrors Python's captain's-log METACOGNITIVE_DECISION
				// payload (subject=step head, context carries the evidence).
				lastFps := fps
				if len(lastFps) > 3 {
					lastFps = lastFps[len(lastFps)-3:]
				}
				if evErr := rec.Event("METACOGNITIVE_DECISION",
					budget.Clip(step.text, 80), d.metaReason,
					map[string]any{
						"step_idx": len(res.Steps) - 1, "retries": retries,
						"fingerprints": lastFps, "replan_count": replanCount,
						"action": action,
					}, loopID); evErr != nil {
					res.Warnings = append(res.Warnings, "metacognitive event write failed: "+evErr.Error())
				}
				switch {
				case d.retry:
					stepRetries[step.text] = retries + 1
					failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
						"step %d retry %d with hint (%s)",
						len(res.Steps)-1, retries+1, d.metaReason)))
					queue = append([]queuedStep{{text: step.text,
						injected: step.injected, hint: d.hint}}, queue...)
				case len(d.splitInto) > 0:
					// Adapter-hung bail: N consecutive DIFFERENT steps all
					// dying at the timeout ceiling is a transport failure,
					// not a step-size problem (Python parity, incl. the
					// reset only on non-timeout splits).
					if strings.Contains(strings.ToLower(out.Result), "timed out") ||
						strings.Contains(strings.ToLower(out.Result), "timeout") {
						consecTimeouts++
						if consecTimeouts >= maxConsecutiveTimeouts {
							haltedEarly = true
							res.StopVerdict = "external-interrupt"
							res.StuckReason = fmt.Sprintf(
								"adapter appears hung: %d consecutive steps timed out at the ceiling — "+
									"transport failure, not step size", consecTimeouts)
							failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
								"adapter appears hung: %d consecutive steps timed out at the ceiling — "+
									"transport failure, not step size [stop: external-interrupt]; "+
									"%d step(s) not executed: ", consecTimeouts, len(queue))+nameRemainder()))
							break stepLoop
						}
					} else {
						consecTimeouts = 0
					}
					shaped := shapeSteps(d.splitInto)
					failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
						"step %d split into %d parts (%s)",
						len(res.Steps)-1, len(shaped), d.metaReason)))
					add := make([]queuedStep, len(shaped))
					for i, s := range shaped {
						// Children of a worker-injected step keep the
						// injected audit mark — splitting must not launder
						// scope creep (adversarial ladder review
						// 2026-08-22, Architect).
						add[i] = queuedStep{text: s, injected: step.injected}
					}
					queue = append(add, queue...)
					replanCount++
				case d.redecompose:
					subs, use, derr := planner.Decompose(ctx, a, rec.WorkspaceDir, step.text, 5)
					res.TokensIn += use.TokensIn
					res.TokensOut += use.TokensOut
					res.Warnings = append(res.Warnings, use.Warnings...)
					if derr != nil || len(subs) < 2 {
						// Recovery tooling broke, not the goal — Python
						// stamps external-interrupt to keep the two apart.
						haltedEarly = true
						res.StopVerdict = "external-interrupt"
						res.StuckReason = fmt.Sprintf(
							"re-decompose failed after %d retries (%v)", retries, derr)
						failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
							"re-decompose failed after %d retries (%v) [stop: external-interrupt]; "+
								"%d step(s) not executed: ", retries, derr, len(queue))+nameRemainder()))
						break stepLoop
					}
					shaped := shapeSteps(subs)
					failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
						"step %d re-decomposing into %d sub-steps (%s)",
						len(res.Steps)-1, len(shaped), d.metaReason)))
					add := make([]queuedStep, len(shaped))
					for i, s := range shaped {
						// Children of a worker-injected step keep the
						// injected audit mark — splitting must not launder
						// scope creep (adversarial ladder review
						// 2026-08-22, Architect).
						add[i] = queuedStep{text: s, injected: step.injected}
					}
					queue = append(add, queue...)
					replanCount++
				default:
					// Terminal verdict: halt with the reason, the verdict,
					// and the unexecuted remainder NAMED.
					haltedEarly = true
					stuck := d.stuckReason
					if stuck == "" {
						stuck = out.Result
					}
					// The typed columns carry the verdict and the WHOLE
					// reason (bounded by the inner BlockReason clip); the
					// chain entry is the prose view. Clip the prose ONCE,
					// then append the verdict tag AFTER the clip — the tag
					// is marker-class, like the clip marker itself, and
					// the pre-fix ordering let the 600-char entry budget
					// eat it (with the do-not-fabricate tail) on exactly
					// the long-reason runs that need them (adversarial
					// ladder r2 2026-08-22, both lenses' HIGH).
					res.StopVerdict = d.stopVerdict
					res.StuckReason = stuck
					entry := budget.FailureChainEntry.Clip(fmt.Sprintf(
						"halted on terminal verdict: %s (%s)", stuck, d.metaReason))
					if d.stopVerdict != "" {
						entry += " [stop: " + d.stopVerdict + "]"
					}
					failChain = append(failChain, entry)
					if len(queue) > 0 {
						failChain = append(failChain, budget.FailureChainEntry.Clip(fmt.Sprintf(
							"%d step(s) not executed: ", len(queue))+nameRemainder()))
					}
					break stepLoop
				}
			}
		}
		if len(injected) > 0 {
			// Python shapes ALL four plan-mutation surfaces — initial,
			// split, redecompose, AND inject (loop_post_step.py:1011,
			// label="inject"). Go ported three of four; a worker-injected
			// combined exec+analyze step burned a call in its broken form
			// before the reactive split saw it (adversarial ladder r2
			// 2026-08-22, Skeptic HIGH).
			shaped := shapeSteps(injected)
			add := make([]queuedStep, len(shaped))
			for i, s := range shaped {
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
	if !haltedEarly {
		if execMode {
			// A recovered run legitimately carries blocked ATTEMPTS beside
			// the done retries (Python parity: loop_status stays clean
			// unless a terminal verdict fired). Every ladder decision
			// either re-queues work or halts, so a drained queue without a
			// halt means every piece's final attempt completed.
			res.Status = "done"
		} else if done == len(res.Steps) {
			res.Status = "done"
		}
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
		StopVerdict: res.StopVerdict, StuckReason: res.StuckReason,
	}); err != nil {
		// Outcome recording failed but the run itself finished — finalize
		// the run dir and name the gap before surfacing the error, or the
		// metadata lies "running" forever and closure's absence is
		// indistinguishable from a crash (adversarial closure r1
		// 2026-08-22, Skeptic: every OTHER early return got a Finalize;
		// this one, directly above the closure block, had none).
		if runDir != "" {
			_ = runs.AppendVerdictRow(runDir, map[string]any{
				"skipped": "outcome_record_failed", "skip_detail": err.Error()})
			_ = runs.Finalize(runDir, res.Status)
		}
		return res, fmt.Errorf("run finished (%s) but outcome recording failed: %w",
			res.Status, err)
	}
	if err := rec.Event("LOOP_FINISHED", loopID,
		fmt.Sprintf("Go loop %s: %d/%d steps done", res.Status, done, len(res.Steps)),
		map[string]any{"status": res.Status, "tokens_in": res.TokensIn,
			"tokens_out": res.TokensOut}, loopID); err != nil {
		res.Warnings = append(res.Warnings, "captain's log write failed: "+err.Error())
	}

	// Closure verification (closure tranche): done ≠ successful — the
	// drain above says the STEPS completed; only closure says the GOAL
	// was achieved, and the two stamps stay separate. Exec-lane only in
	// v0: the tool-less lane structurally writes no files, so its
	// closure would burn two LLM calls to reach env-unresolved-unjudged
	// — the skip is recorded instead (Go-stricter divergence, PORT.md;
	// Python judges all lanes because every lane there has a cwd).
	if runDir != "" {
		// Eligibility is "did any step actually run", NOT terminal status:
		// Python's _closure_eligible_statuses spans done/partial/stuck/
		// restart because a stuck run that wrote real files still deserves
		// the honest "what got delivered" signal — and it's exactly the
		// run where closure catches the most false negatives (adversarial
		// closure r1 2026-08-22, three lenses independently; handle.py's
		// comment: "the CLOSURE_VERDICT event makes the recovery paths
		// observable"). Only the restart-escalation DECISION is
		// done-gated in Python, and that consumer is unported.
		ranAnyStep := done > 0
		if execMode && !opts.DryRun && ranAnyStep {
			v := closure.Verify(ctx, a, goal, closureSteps(res.Steps), closure.Options{
				WorkspacePath: res.ProjectDir,
				LoopID:        loopID,
				DryRun:        opts.DryRun,
				PersistRow: func(row map[string]any) {
					if perr := runs.AppendVerdictRow(runDir, row); perr != nil {
						res.Warnings = append(res.Warnings,
							"closure verdict row write failed: "+perr.Error())
					}
				},
			})
			res.Closure = &v
			// goal_achieved is TRI-STATE: an unjudged verdict stamps
			// nothing — absence means "not judged", and a false here
			// demotes the run everywhere the stamp is read (recall's
			// prior-attempt icons included).
			var achieved *bool
			var confPtr *float64
			if v.Judged {
				achieved = &v.Complete
				// Confidence gated with the verdict: an unjudged closure
				// measured nothing, and nil POPS the key — writing its
				// zero-valued Confidence would fabricate "verified with
				// zero confidence" (adversarial routing r2, Architect;
				// Go-stricter than Python, which writes 0.0 there).
				confPtr = &v.Confidence
			}
			if serr := runs.StampVerdict(runDir, achieved, record.SourceClosure,
				v.Summary, confPtr, v.DowngradeReason, v.Gaps); serr != nil {
				res.Warnings = append(res.Warnings, "verdict stamp failed: "+serr.Error())
			}
			// The outcome ROW was written before closure judged (the loop
			// records at finalization; closure runs after) — land the
			// verdict on the row post-hoc, or every closure-judged loop
			// run reads as permanently unjudged on the one ledger the NOW
			// lane made verdict-bearing (Python memory_ledger.
			// stamp_outcome_verdict; adversarial routing r2, both lenses).
			if soErr := rec.StampOutcomeVerdict(loopID, achieved,
				record.SourceClosure, confPtr); soErr != nil {
				res.Warnings = append(res.Warnings,
					"outcome-row verdict stamp failed: "+soErr.Error())
				// Durable marker beside the warning: a failed row stamp
				// recreates the round's headline bug (row reads unjudged
				// forever) behind a rarer trigger — it must be
				// discoverable off-terminal (adversarial routing r3).
				if merr := runs.AppendVerdictRow(runDir, map[string]any{
					"skipped":     "outcome_row_stamp_failed",
					"skip_detail": budget.Clip(soErr.Error(), 300)}); merr != nil {
					// The fallback channel failing too must be NAMED —
					// otherwise the doubly-failed case silently degrades
					// to pre-marker behavior (adversarial routing r5).
					res.Warnings = append(res.Warnings,
						"outcome-row stamp marker write also failed: "+merr.Error())
				}
			}
			if evErr := rec.Event("CLOSURE_VERDICT", "closure_verdict",
				budget.Clip(v.Summary, 200),
				map[string]any{
					"complete": v.Complete, "judged": v.Judged,
					"confidence": v.Confidence, "checks_run": v.ChecksRun,
					"checks_passed":      v.ChecksPassed,
					"inconclusive_count": v.InconclusiveCount,
					"gap_count":          len(v.Gaps),
					"downgrade_reason":   v.DowngradeReason,
					"fingerprint":        closure.Fingerprint(v),
					"skip_reason":        v.SkipReason,
				}, loopID); evErr != nil {
				res.Warnings = append(res.Warnings, "captain's log write failed (CLOSURE_VERDICT): "+evErr.Error())
			}
		} else if !opts.DryRun {
			// The named skip row keeps "closure never ran" distinguishable
			// from "closure ran and produced nothing" in the run dir —
			// for EVERY terminal status, or a stuck run's empty jsonl is
			// indistinguishable from a crash before the finish path
			// (persist-the-artifacts decree; adversarial closure r1).
			reason := "tool_less_lane"
			if execMode && !ranAnyStep {
				reason = "no_steps_ran"
			}
			if perr := runs.AppendVerdictRow(runDir, map[string]any{
				"skipped": reason}); perr != nil {
				res.Warnings = append(res.Warnings,
					"closure verdict row write failed: "+perr.Error())
			}
		}
		if ferr := runs.Finalize(runDir, res.Status); ferr != nil {
			res.Warnings = append(res.Warnings, "run metadata finalize failed: "+ferr.Error())
		}
	}
	return res, nil
}

// closureSteps maps loop step outcomes into closure's view.
func closureSteps(steps []StepOutcome) []closure.StepView {
	out := make([]closure.StepView, 0, len(steps))
	for _, s := range steps {
		out = append(out, closure.StepView{Text: s.Step, Result: s.Result})
	}
	return out
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
