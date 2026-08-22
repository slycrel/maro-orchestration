package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/inspector"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// runInspect is the `maro inspect` subcommand — one read-only quality
// pass over recent outcomes (Python: `python3 -m inspector`, minus the
// --loop daemon, which is not ported).
//
// Deliberate divergence from the Python CLI default, named: Python's
// standalone entry builds a MODEL_CHEAP adapter unless --dry-run, which
// makes an inspection of N outcomes cost up to 2N LLM calls silently.
// The no-silent-spend decree (config scope_generation ships OFF for the
// same reason) puts the judge passes behind an explicit -llm flag here;
// the heuristics — the production-norm path in Python too — run by
// default with zero spend.
func runInspect(args []string) error {
	fs := flag.NewFlagSet("inspect", flag.ContinueOnError)
	limit := fs.Int("limit", 50, "number of recent outcomes to inspect")
	dry := fs.Bool("dry", false, "run without saving results (heuristics only)")
	useLLM := fs.Bool("llm", false,
		"judge alignment + notes + cross-session patterns with the LLM (real spend: up to 2 calls per outcome)")
	backend := fs.String("backend", "auto", "auto|subprocess|anthropic|dry (only used with -llm)")
	format := fs.String("format", "text", "text|json")
	summary := fs.Bool("summary", false, "print the latest inspection's friction summary and exit")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if len(fs.Args()) > 0 {
		return fmt.Errorf("inspect takes no positional arguments (got %q)", fs.Args()[0])
	}
	if *limit < 1 || *limit > inspector.DeepPassLimit {
		return fmt.Errorf("-limit %d out of range [1,%d]", *limit, inspector.DeepPassLimit)
	}

	// Live-store discipline: the resolved workspace is printed before
	// any write.
	ws := config.Workspace()
	fmt.Printf("workspace: %s\n", ws)
	cfg, warnings := config.Load()
	for _, w := range warnings {
		fmt.Fprintln(os.Stderr, "config warning:", w)
	}

	if *summary {
		s := inspector.FrictionSummary(ws)
		if s == "" {
			fmt.Println("no inspection recorded yet")
			return nil
		}
		fmt.Println(s)
		return nil
	}

	var adapter llm.Adapter
	if *useLLM && !*dry {
		a, err := buildAdapter(*backend)
		if err != nil {
			return fmt.Errorf("-llm requested but no backend available: %w", err)
		}
		adapter = a
		fmt.Printf("backend: %s\n", adapter.Name())
	}

	th := inspector.LoadThresholds(cfg)
	report := inspector.Run(context.Background(), ws, adapter, *limit, *dry, th)
	if *format == "json" {
		raw, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			return err
		}
		fmt.Println(string(raw))
		return nil
	}
	fmt.Println(report.Summary())
	return nil
}

// runEvolve is the `maro evolve` subcommand — the meta-evolution cycle
// plus the suggestion review verbs (Python cli.py's evolver surface:
// --list/--apply/--dismiss/--revert).
func runEvolve(args []string) error {
	fs := flag.NewFlagSet("evolve", flag.ContinueOnError)
	window := fs.Int("window", 50, "recent outcomes to analyze")
	minOutcomes := fs.Int("min-outcomes", 3, "skip if fewer outcomes exist")
	dry := fs.Bool("dry", false, "analyze without writing suggestions or applying")
	backend := fs.String("backend", "auto", "auto|subprocess|anthropic|dry")
	format := fs.String("format", "text", "text|json")
	list := fs.Bool("list", false, "list pending suggestions and exit")
	applyID := fs.String("apply", "", "manually apply one suggestion by id (the review IS the gate)")
	dismissID := fs.String("dismiss", "", "dismiss one suggestion by id")
	reason := fs.String("reason", "", "reason recorded with -dismiss")
	revertID := fs.String("revert", "", "revert one applied suggestion by id (change_log trail)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if len(fs.Args()) > 0 {
		return fmt.Errorf("evolve takes no positional arguments (got %q)", fs.Args()[0])
	}

	ws := config.Workspace()
	fmt.Printf("workspace: %s\n", ws)
	cfg, warnings := config.Load()
	for _, w := range warnings {
		fmt.Fprintln(os.Stderr, "config warning:", w)
	}
	rec := record.New(ws)

	switch {
	case *list:
		pending := evolver.ListPending(ws, 20)
		if len(pending) == 0 {
			fmt.Println("no pending suggestions")
			return nil
		}
		for _, s := range pending {
			status := s.Status
			if status == "" {
				status = "pending"
			}
			fmt.Printf("%s [%s/%s] conf=%.2f %s\n    %s\n",
				s.SuggestionID, s.Category, status, s.Confidence, s.Target,
				clipLine(s.Suggestion, 160))
			if s.BlockReason != "" {
				fmt.Printf("    held: %s\n", clipLine(s.BlockReason, 160))
			}
		}
		return nil

	case *applyID != "":
		found, err := evolver.Apply(ws, rec, cfg, *applyID, true)
		if err != nil {
			return err
		}
		if !found {
			return fmt.Errorf("no suggestion with id %q", *applyID)
		}
		if evolver.IsApplied(ws, *applyID) {
			fmt.Printf("applied %s\n", *applyID)
		} else if s := evolver.GetSuggestion(ws, *applyID); s != nil {
			// Found but not applied: the gate said no — report which.
			fmt.Printf("NOT applied %s: status=%s %s\n", *applyID, s.Status, s.BlockReason)
		}
		return nil

	case *dismissID != "":
		found, err := evolver.Dismiss(ws, *dismissID, *reason)
		if err != nil {
			return err
		}
		if !found {
			return fmt.Errorf("no pending suggestion with id %q", *dismissID)
		}
		fmt.Printf("dismissed %s\n", *dismissID)
		return nil

	case *revertID != "":
		res := evolver.Revert(ws, rec, *revertID)
		fmt.Printf("reverted=%v behavioral=%v category=%s: %s\n",
			res.Reverted, res.Behavioral, res.Category, res.Detail)
		if !res.Reverted {
			return fmt.Errorf("revert did not run")
		}
		return nil
	}

	// Full cycle: the proposer needs an adapter (that IS the command);
	// -dry still loads outcomes and prints the skip/summary shape
	// without LLM spend or writes.
	var adapter llm.Adapter
	if !*dry {
		a, err := buildAdapter(*backend)
		if err != nil {
			return err
		}
		adapter = a
		fmt.Printf("backend: %s\n", adapter.Name())
	}
	report := evolver.Run(context.Background(), ws, rec, cfg, adapter, evolver.RunOptions{
		OutcomesWindow: *window,
		MinOutcomes:    *minOutcomes,
		DryRun:         *dry,
		Verbose:        true,
	})
	if *format == "json" {
		fmt.Println(evolver.MarshalReport(report))
		return nil
	}
	fmt.Println(report.Summary())
	return nil
}

func clipLine(s string, n int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	r := []rune(s)
	if len(r) > n {
		return string(r[:n]) + "…"
	}
	return s
}
