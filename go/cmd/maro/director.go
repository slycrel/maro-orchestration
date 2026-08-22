// maro director — the plan/delegate/review lane (ports director.py's
// run_director surface; Python's maro-director CLI sibling).
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/director"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/scrub"
)

func runDirector(args []string) error {
	fs := flag.NewFlagSet("director", flag.ContinueOnError)
	backend := fs.String("backend", "auto", "auto|subprocess|anthropic|dry")
	model := fs.String("model", "", "model alias or id (backend default when empty)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	// Same flags-after-goal refusal as `maro run` — stdlib flag stops at
	// the first positional, and a silently swallowed "-backend dry" runs
	// the real backend.
	for _, a := range fs.Args() {
		if strings.HasPrefix(a, "-") {
			return fmt.Errorf("flag %q after the directive is not parsed — "+
				"put flags before it: maro director -backend dry \"directive\"", a)
		}
	}
	directive := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if directive == "" {
		return fmt.Errorf("no directive given")
	}

	// Live-store discipline: the resolved workspace is printed before
	// any write.
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

	// The acceptance gate is printed, not enforced — the approval
	// surface is unported with the escalation machinery, and a silent
	// "inferred" on a deploy-shaped directive would fake consent.
	if director.RequiresExplicitAcceptance(directive) {
		fmt.Println("plan acceptance: EXPLICIT required (directive names an outward-facing " +
			"or destructive action) — this port has no approval surface; review the plan output")
	}

	rec := record.New(ws)
	res, err := director.Run(context.Background(), adapter, rec, directive, *backend == "dry")
	if err != nil {
		return err
	}
	for _, w := range res.Warnings {
		fmt.Fprintln(os.Stderr, "warn:", w)
	}

	fmt.Printf("\n=== %s (director %s, %d tickets, %d in / %d out tokens, %s) ===\n",
		strings.ToUpper(res.Status), res.DirectorID, len(res.Tickets),
		res.TokensIn, res.TokensOut, res.Elapsed.Round(1e8))
	fmt.Println(res.Summary())
	// ReviewDecisions is an append-shaped audit trail (revision rounds
	// add entries), NOT worker-aligned — pairing decision[i] with
	// worker[i] misattributes verdicts after any revision. Render the
	// trails separately.
	for i, w := range res.WorkerResults {
		digest := fmt.Sprintf("%d chars", len(w.Result))
		if w.Status == "blocked" {
			// LLM-authored free text — scrubbed like the two file sinks:
			// stdout is routinely tee'd/piped somewhere durable
			// (adversarial director r2, QA).
			digest = fmt.Sprintf("blocked(%s): %s", w.BlockedOrigin, scrub.Secrets(w.StuckReason))
		}
		fmt.Printf("\n[%d] %s %s — %s\n", i, w.WorkerType, w.Status, digest)
	}
	for i, d := range res.ReviewDecisions {
		verdict := "accepted"
		if !d.Accepted {
			verdict = "not accepted"
		}
		// TicketID printed so the trail correlates where a human reads
		// it; reason scrubbed like the file sinks (r2).
		fmt.Printf("review[%d] (ticket %s): %s — %s\n", i, d.TicketID, verdict, scrub.Secrets(d.Reason))
	}
	// Deliberately unclipped — the terminal is the delivery surface.
	fmt.Printf("\n%s\n", res.Report)
	return nil
}
