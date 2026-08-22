// maro (Go port) — spine-first CLI.
//
//	maro run "your goal here" [-max-steps N] [-backend auto|subprocess|anthropic|dry] [-model X]
//
// Exploration branch go-port; the Python runtime remains production.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/closure"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/intent"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/loop"
	"github.com/slycrel/maro-orchestration/go/internal/now"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) >= 1 && args[0] == "pack" {
		return runPack(args[1:])
	}
	if len(args) >= 1 && args[0] == "director" {
		return runDirector(args[1:])
	}
	if len(args) >= 1 && args[0] == "inspect" {
		return runInspect(args[1:])
	}
	if len(args) >= 1 && args[0] == "evolve" {
		return runEvolve(args[1:])
	}
	if len(args) < 1 || args[0] != "run" {
		return fmt.Errorf("usage: maro run \"goal\" [flags] | maro director \"directive\" [flags] | maro pack export|seal|import|adopt [flags] | maro inspect [flags] | maro evolve [flags]")
	}
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	maxSteps := fs.Int("max-steps", 8, "maximum decomposed steps")
	backend := fs.String("backend", "auto", "auto|subprocess|anthropic|dry")
	model := fs.String("model", "", "model alias or id (backend default when empty)")
	safe := fs.Bool("safe", false,
		"force tool-less utility mode (worker steps get no agent tools)")
	laneFlag := fs.String("lane", "auto",
		"auto|now|agenda — auto classifies (NOW = single call, AGENDA = loop)")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	// stdlib flag stops at the first positional arg, so flags AFTER the
	// goal would silently become goal text — and a "-backend dry" the
	// user thought they passed would run the real backend. Refuse the
	// ambiguous shape instead of guessing (first smoke run hit exactly
	// this).
	for _, a := range fs.Args() {
		if strings.HasPrefix(a, "-") {
			return fmt.Errorf("flag %q after the goal is not parsed — "+
				"put flags before the goal: maro run -backend dry \"goal\"", a)
		}
	}
	goal := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if goal == "" {
		return fmt.Errorf("no goal given")
	}
	// Hard ceiling: each step re-sends (budgeted) prior evidence, so an
	// absurd step count is quadratic prompt volume and real spend
	// (adversarial r2 2026-08-22, Skeptic — the flag was unbounded).
	if *maxSteps < 1 || *maxSteps > 32 {
		return fmt.Errorf("-max-steps %d out of range [1,32]", *maxSteps)
	}

	// Assert the resolved store BEFORE any write — the resolved path is
	// part of the result (live-store discipline, 2026-08-16 incident).
	ws := config.Workspace()
	fmt.Printf("workspace: %s\n", ws)
	_, warnings := config.Load()
	for _, w := range warnings {
		fmt.Fprintln(os.Stderr, "config warning:", w)
	}

	adapter, err := buildAdapter(*backend)
	if err != nil {
		return err
	}
	if *model != "" {
		adapter = withModel{Adapter: adapter, model: *model}
	}
	fmt.Printf("backend: %s\n", adapter.Name())

	// Exec is decided ONCE, from the constructed adapter's actual
	// capability (never a backend-name string that can drift from it —
	// adversarial exec review 2026-08-22, Architect), and the entered
	// mode is printed plainly: default-on tool-bearing execution is the
	// run's highest-blast-radius property and must be visible on stderr-
	// watching alone (same review, Skeptic). The loop re-checks the
	// capability structurally — defense in depth for non-CLI callers.
	capable := false
	if c, ok := adapter.(llm.AgentToolsCapable); ok {
		capable = c.SupportsAgentTools()
	}
	execMode := !*safe && capable
	switch {
	case execMode:
		fmt.Printf("exec mode: ON — worker agent tools, uncontained "+
			"(--dangerously-skip-permissions), project work under %s/projects/\n", ws)
	case *safe:
		fmt.Println("exec mode: off (-safe — tool-less utility steps)")
	default:
		fmt.Printf("exec mode: off (backend %s cannot run agent tools — tool-less utility steps)\n",
			adapter.Name())
	}

	rec := record.New(ws)

	// Routing (director tranche slice 1): NOW vs AGENDA, decided before
	// the loop and PRINTED — the lane is a run-shaping decision the
	// operator must see, same doctrine as the exec-mode line above.
	lane, clsIn, clsOut, laneErr := routeLane(adapter, *laneFlag, goal, *backend == "dry")
	if laneErr != nil {
		return laneErr
	}
	if lane == "now" {
		nres, nerr := now.Run(context.Background(), adapter, rec, goal,
			*backend == "dry", *model, clsIn, clsOut)
		if nerr != nil {
			return nerr
		}
		for _, w := range nres.Warnings {
			fmt.Fprintln(os.Stderr, "warn:", w)
		}
		fmt.Printf("\n=== %s (%s, now lane, %d in / %d out tokens, %s) ===\n",
			strings.ToUpper(nres.Status), nres.LoopID,
			nres.TokensIn, nres.TokensOut, nres.Elapsed.Round(1e8))
		if nres.GoalAchieved != nil && !*nres.GoalAchieved {
			fmt.Printf("goal: Not achieved: %s\n", nres.VerdictSummary)
		}
		if nres.NowVerifyError != "" {
			fmt.Printf("goal: not judged (verify errored: %s)\n", nres.NowVerifyError)
		}
		// Deliberately unclipped — the terminal is the delivery surface.
		fmt.Printf("\n%s\n", nres.Answer)
		return nil
	}

	res, err := loop.Run(context.Background(), adapter, rec, loop.Opts{
		Goal:     goal,
		Model:    *model,
		MaxSteps: *maxSteps,
		// dry_run is the field Python's learning funnel keys on to
		// exclude synthetic rows; a canned run recorded as real is a
		// fabricated record (adversarial r2 2026-08-22, Expert QA).
		DryRun:        *backend == "dry",
		Exec:          execMode,
		SeedTokensIn:  clsIn,
		SeedTokensOut: clsOut,
	})
	if err != nil {
		return err
	}
	for _, w := range res.Warnings {
		fmt.Fprintln(os.Stderr, "warn:", w)
	}
	if res.ProjectDir != "" {
		fmt.Printf("project dir: %s\n", res.ProjectDir)
	}

	fmt.Printf("\n=== %s (%s, %d steps, %d in / %d out tokens, %s) ===\n",
		strings.ToUpper(res.Status), res.LoopID, len(res.Steps),
		res.TokensIn, res.TokensOut, res.Elapsed.Round(1e8))
	// done ≠ successful must reach the operator, not just metadata.json:
	// the DONE banner says the steps drained; only this line says whether
	// the GOAL was judged achieved (adversarial closure r1 2026-08-22,
	// Skeptic — the tranche's headline guarantee was write-only).
	if line := closureLine(res.Closure); line != "" {
		fmt.Println(line)
	}
	for i, s := range res.Steps {
		// Deliberately unclipped: the terminal is the delivery surface and
		// the full result is the deliverable — caps bound prompts and
		// records, never the only copy the operator sees
		// (artifacts-over-streams decree). Worker-injected steps are
		// tagged: plan mutation must be visible in the delivered output,
		// not only in tests (adversarial exec review 2026-08-22, QA).
		tag := ""
		if s.WasInjected {
			tag = " (worker-injected)"
		}
		fmt.Printf("\n[%d] %s%s — %s\n%s\n", i, s.Status, tag, s.Step, s.Result)
	}
	return nil
}

// routeLane resolves the goal's lane and PRINTS the routing decision —
// a run-shaping choice the operator must see, same doctrine as the
// exec-mode line. Extracted so the classify-usage extraction (clsIn/
// clsOut) is unit-testable with a token-bearing adapter: the CLI-level
// dry test can never distinguish correct wiring from dropped wiring,
// because dry classify is heuristic-only and 0 == 0 either way
// (adversarial routing r2, Architect). Classify spend is real cost on
// the goal's behalf — the caller seeds it into whichever lane runs so
// the outcome row carries the FULL number (r1, all four lenses).
func routeLane(adapter llm.Adapter, laneFlag, goal string, dry bool) (lane string, clsIn, clsOut int, err error) {
	lane = laneFlag
	switch lane {
	case "now", "agenda":
		fmt.Printf("lane: %s (forced by -lane)\n", strings.ToUpper(lane))
	case "auto":
		cls := intent.Classify(context.Background(), adapter, goal, dry)
		lane = cls.Lane
		clsIn, clsOut = cls.TokensIn, cls.TokensOut
		fmt.Printf("lane: %s (%.2f) — %s\n", strings.ToUpper(cls.Lane),
			cls.Confidence, cls.Reason)
	default:
		return "", 0, 0, fmt.Errorf("unknown -lane %q (auto|now|agenda)", laneFlag)
	}
	return lane, clsIn, clsOut, nil
}

// withModel forces a default model onto every call that doesn't name one.
type withModel struct {
	llm.Adapter
	model string
}

func (w withModel) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	if opts.Model == "" {
		opts.Model = w.model
	}
	return w.Adapter.Complete(ctx, msgs, opts)
}

// SupportsAgentTools forwards the wrapped backend's capability — struct
// embedding of an interface promotes only the interface's declared
// methods, so without this the -model wrapper would silently strip the
// executor lane from a capable backend.
func (w withModel) SupportsAgentTools() bool {
	c, ok := w.Adapter.(llm.AgentToolsCapable)
	return ok && c.SupportsAgentTools()
}

func buildAdapter(kind string) (llm.Adapter, error) {
	switch kind {
	case "subprocess":
		return llm.NewSubprocess()
	case "anthropic":
		return llm.NewAnthropic()
	case "dry":
		return &llm.Fake{Script: []string{
			`["inspect the input", "produce the answer"]`,
			"dry-run step result",
		}}, nil
	case "auto":
		// Order source: this box's ~/.maro/config.yml model.backend_order
		// lists subprocess, anthropic (checked 2026-08-22). Python's
		// SHIPPED default is anthropic-first (llm.py DEFAULT_BACKEND_ORDER)
		// — the box config overrides it; wiring auto-order to the config
		// file belongs to the config tranche.
		subErr, anthErr := error(nil), error(nil)
		if a, err := llm.NewSubprocess(); err == nil {
			return a, nil
		} else {
			subErr = err
		}
		if a, err := llm.NewAnthropic(); err == nil {
			return a, nil
		} else {
			anthErr = err
		}
		// Both real reasons travel — a hardcoded summary goes stale the
		// moment a constructor grows a second failure mode (adversarial
		// r2 2026-08-22, Expert QA).
		return nil, fmt.Errorf("no backend available (subprocess: %v; anthropic: %v)",
			subErr, anthErr)
	default:
		return nil, fmt.Errorf("unknown backend %q", kind)
	}
}

// closureLine renders the goal-verdict line for the terminal. Every
// skip path names its reason — "closure crashed" must not print
// identically to "closure ran clean" (adversarial closure r2
// 2026-08-22, Architect: nullVerdict's constant summary made the
// recovered-panic path indistinguishable from a legitimate no-checks
// run). Summary/gaps arrive already scrubbed at closure.Verify's
// return boundary.
func closureLine(v *closure.Verdict) string {
	if v == nil {
		return ""
	}
	if v.SkipReason != "" {
		return fmt.Sprintf("goal: %s [skipped: %s]", v.Summary, v.SkipReason)
	}
	return fmt.Sprintf("goal: %s (confidence %.2f, %d/%d checks passed)",
		v.Summary, v.Confidence, v.ChecksPassed, v.ChecksRun)
}
