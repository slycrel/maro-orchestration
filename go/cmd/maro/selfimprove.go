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
	"github.com/slycrel/maro-orchestration/go/internal/graduation"
	"github.com/slycrel/maro-orchestration/go/internal/inspector"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/scans"
	"github.com/slycrel/maro-orchestration/go/internal/selfimprove"
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
	impact := fs.Bool("impact", false, "print longitudinal impact of applied suggestions and exit")
	verifyOnly := fs.Bool("verify", false, "render V2 cadence verdicts (dry-run report) and exit")
	verifyApply := fs.Bool("verify-apply", false, "with -verify: actually stamp/revert/park")
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
	case *impact:
		records := scans.ScanEvolverImpact(ws, scans.ImpactOptions{})
		fmt.Println(scans.FormatImpactSummary(records))
		return nil

	case *verifyOnly:
		summary := scans.VerifyAppliedSuggestions(ws, rec, cfg, "cli",
			scans.VerifyOptions{DryRun: !*verifyApply, Verbose: true})
		if summary.Skipped == "disabled" {
			fmt.Println("Cadence verdicts disabled (evolver.verify_cadence_verdicts=false).")
			return nil
		}
		mode := "dry-run (use -verify-apply to act)"
		if *verifyApply {
			mode = "APPLIED"
		}
		fmt.Printf("Cadence verdicts [%s]: %d confirmed, %d reverted, %d revert-failed, "+
			"%d review-queued, %d unverifiable, %d pending (of %d applied-unverified).\n",
			mode, summary.Confirmed, summary.Reverted, summary.RevertFailed,
			summary.ReviewQueued, summary.Unverifiable, summary.Pending, summary.Candidates)
		return nil

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
	// without LLM spend or writes. selfimprove.Cycle wraps the core in
	// the run_evolver order: graduation verification → propose+scans+
	// auto-apply → graduation propose → cadence verdicts.
	var adapter llm.Adapter
	if !*dry {
		a, err := buildAdapter(*backend)
		if err != nil {
			return err
		}
		adapter = a
		fmt.Printf("backend: %s\n", adapter.Name())
	}
	report, verify := selfimprove.Cycle(context.Background(), ws, rec, cfg, adapter,
		selfimprove.CycleOptions{
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
	if verify.Enabled && verify.Candidates > 0 {
		fmt.Printf("cadence verdicts: %d confirmed, %d reverted, %d revert-failed, "+
			"%d review-queued, %d unverifiable, %d pending\n",
			verify.Confirmed, verify.Reverted, verify.RevertFailed,
			verify.ReviewQueued, verify.Unverifiable, verify.Pending)
	}
	return nil
}

// runGraduate is the `maro graduate` subcommand (Python: python3 -m
// graduation) — scan diagnoses for repeated failure classes, propose
// pending rules, or run the structural verify pass.
func runGraduate(args []string) error {
	fs := flag.NewFlagSet("graduate", flag.ContinueOnError)
	minCount := fs.Int("min-count", 3, "occurrences to trigger graduation")
	lookback := fs.Int("lookback", 100, "recent diagnoses to scan")
	dry := fs.Bool("dry", false, "scan only, do not write suggestions")
	verify := fs.Bool("verify", false, "run the structural verify_pattern pass and report")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if len(fs.Args()) > 0 {
		return fmt.Errorf("graduate takes no positional arguments (got %q)", fs.Args()[0])
	}

	ws := config.Workspace()
	fmt.Printf("workspace: %s\n", ws)
	cfg, warnings := config.Load()
	for _, w := range warnings {
		fmt.Fprintln(os.Stderr, "config warning:", w)
	}

	if *verify {
		repoRoot := selfimprove.RepoRoot(cfg)
		if repoRoot == "" {
			fmt.Println("structural verify skipped: no repo root " +
				"(set MARO_REPO_ROOT or graduation.repo_root — the shipped " +
				"grep patterns need a maro source tree to check)")
			return nil
		}
		fmt.Println("Verifying graduated rules (running verify_pattern for each):")
		results := graduation.VerifyGraduationRules(ws, repoRoot, 200)
		if len(results) == 0 {
			fmt.Println("  (no applied graduated rules with a shipped verify_pattern found)")
			return nil
		}
		passCount := 0
		for _, r := range results {
			icon := "FAIL"
			if r.Passed {
				icon = "PASS"
				passCount++
			}
			fmt.Printf("  [%s] %s\n", icon, r.FailureClass)
			if r.Output != "" {
				fmt.Printf("         → %s\n", r.Output)
			}
		}
		fmt.Printf("\n%d/%d verified rules passing\n", passCount, len(results))
		return nil
	}

	candidates := graduation.ScanCandidates(ws, *minCount, *lookback)
	fmt.Printf("Graduation candidates (min_count=%d, lookback=%d):\n", *minCount, *lookback)
	if len(candidates) == 0 {
		fmt.Println("  (none)")
	}
	for _, c := range candidates {
		tag := ""
		if graduation.AlreadyProposed(ws, c.FailureClass, 200) {
			tag = " [already proposed]"
		}
		last := c.LoopIDs
		if len(last) > 3 {
			last = last[len(last)-3:]
		}
		fmt.Printf("  %s: %dx — loops %s%s\n", c.FailureClass, c.Count,
			strings.Join(last, ", "), tag)
	}

	rec := record.New(ws)
	n := graduation.RunGraduation(ws, rec, *minCount, *lookback, *dry, true)
	if !*dry {
		fmt.Printf("\nWrote %d new graduation suggestion(s).\n", n)
	}
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
