// Package selfimprove composes the full meta-evolution cadence in Python
// run_evolver's order. It exists because the arrows can't point backwards:
// internal/scans and internal/graduation read and stamp the evolver's
// suggestion store, so evolver can't import them — the composition lives a
// layer up, and the CLI (and a future heartbeat) calls this one entry.
//
// Cycle order (run_evolver parity; unported passes named in place):
//  1. Graduation STRUCTURAL verification — before the outcome-count early
//     return, so a quiet interval never suppresses it.
//  2. evolver.Run — LLM propose + statistical scanners (via the
//     ExtraSuggestions hook, same batch/save/auto-apply) + cycle events.
//     [unported inside: business-signal scan, harness-friction,
//     persona-gap, advisor gate, post-apply pytest]
//  3. Graduation propose — repeated failure classes → pending rows.
//     [unported between: router retrain, skill-candidate sweep, island
//     cycle — their subsystems have no Go port]
//  4. VERIFY_LEARN_ARC V2/V3 cadence verdicts — confirm / revert / park
//     every applied-unverified row.
//     [unported after: navigator divergence adjudication]
package selfimprove

import (
	"context"
	"os"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/graduation"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/scans"
)

// CycleOptions extends the evolver cycle knobs with the composition-level
// gates.
type CycleOptions struct {
	OutcomesWindow int
	MinOutcomes    int
	DryRun         bool
	Verbose        bool
	SkipScans      bool // statistical scanners off (evolver core only)
	SkipGraduation bool
	SkipVerify     bool // V2/V3 cadence-verdict pass off
}

// RepoRoot resolves where graduation's structural grep patterns run:
// MARO_REPO_ROOT env > config graduation.repo_root > "" (checks skipped —
// a Go binary has no __file__ to walk up from, and shelling greps against
// an unknown cwd would verify nothing).
func RepoRoot(cfg map[string]any) string {
	if v := os.Getenv("MARO_REPO_ROOT"); v != "" {
		return v
	}
	return config.Get(cfg, "graduation.repo_root", "")
}

// Cycle runs one full self-improvement cadence and returns the evolver
// report plus the cadence-verdict summary.
func Cycle(ctx context.Context, ws string, rec *record.Recorder,
	cfg map[string]any, adapter llm.Adapter, o CycleOptions) (evolver.EvolverReport, scans.VerifySummary) {

	// 1. Structural observability for applied graduations — observe/event
	// only, never a verdict (a grep miss ≠ the applied lesson failed).
	if !o.DryRun && !o.SkipGraduation {
		graduation.RunGraduationVerification(ws, RepoRoot(cfg), rec)
	}

	// 2. Core cycle with the scanners riding the batch.
	runOpts := evolver.RunOptions{
		OutcomesWindow: o.OutcomesWindow,
		MinOutcomes:    o.MinOutcomes,
		DryRun:         o.DryRun,
		Verbose:        o.Verbose,
	}
	if !o.SkipScans {
		runOpts.ExtraSuggestions = func(outcomes []map[string]any) []evolver.Suggestion {
			return scans.RunStatisticalScans(ws, outcomes, scans.StatScanOptions{Verbose: o.Verbose})
		}
	}
	report := evolver.Run(ctx, ws, rec, cfg, adapter, runOpts)

	// Python's run_evolver early-returns on a skipped interval (outcomes
	// unreadable, or fewer than min_outcomes) BEFORE the graduation propose
	// and the V2/V3 verify pass — a quiet workspace suppresses both. Without
	// this gate the verify pass renders `inconclusive` off zero window data
	// each cycle and BumpExtensionOrPark walks an applied-unverified row to a
	// TERMINAL "unverifiable" park before any evidence could exist (r4 MED).
	// Step 1 stays above evolver.Run on purpose: Python runs the structural
	// graduation check before its early return too.
	var verify scans.VerifySummary
	if report.Skipped {
		return report, verify
	}

	// 3. Graduation propose (skipped on dry_run, Python parity — the scan
	// is reachable via `maro graduate -dry`).
	if !o.DryRun && !o.SkipGraduation {
		n := graduation.RunGraduation(ws, rec, 3, 100, false, o.Verbose)
		if n > 0 && o.Verbose {
			os.Stderr.WriteString(
				"[evolver] graduation: new permanent rule suggestion(s) proposed\n")
		}
	}

	// 4. Cadence verdicts + authority-aware auto-revert.
	if !o.DryRun && !o.SkipVerify {
		verify = scans.VerifyAppliedSuggestions(ws, rec, cfg, report.RunID,
			scans.VerifyOptions{Verbose: o.Verbose})
	}
	return report, verify
}
