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

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/loop"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) < 1 || args[0] != "run" {
		return fmt.Errorf("usage: maro run \"goal\" [-max-steps N] [-backend auto|subprocess|anthropic|dry] [-model X]")
	}
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	maxSteps := fs.Int("max-steps", 8, "maximum decomposed steps")
	backend := fs.String("backend", "auto", "auto|subprocess|anthropic|dry")
	model := fs.String("model", "", "model alias or id (backend default when empty)")
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

	rec := record.New(ws)
	res, err := loop.Run(context.Background(), adapter, rec, goal, *model, *maxSteps)
	if err != nil {
		return err
	}
	for _, w := range res.Warnings {
		fmt.Fprintln(os.Stderr, "warn:", w)
	}

	fmt.Printf("\n=== %s (%s, %d steps, %d in / %d out tokens, %s) ===\n",
		strings.ToUpper(res.Status), res.LoopID, len(res.Steps),
		res.TokensIn, res.TokensOut, res.Elapsed.Round(1e8))
	for i, s := range res.Steps {
		// Deliberately unclipped: the terminal is the delivery surface and
		// the full result is the deliverable — caps bound prompts and
		// records, never the only copy the operator sees
		// (artifacts-over-streams decree).
		fmt.Printf("\n[%d] %s — %s\n%s\n", i, s.Status, s.Step, s.Result)
	}
	return nil
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
		// Box order: subprocess first, anthropic second — same as the
		// Python backend_order on this machine.
		if a, err := llm.NewSubprocess(); err == nil {
			return a, nil
		}
		if a, err := llm.NewAnthropic(); err == nil {
			return a, nil
		}
		return nil, fmt.Errorf("no backend available (claude CLI not found, ANTHROPIC_API_KEY unset)")
	default:
		return nil, fmt.Errorf("unknown backend %q", kind)
	}
}
