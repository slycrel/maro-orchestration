// Package budget is the caps discipline made structural.
//
// The Python runtime enforces truncation discipline with tripwires: an
// AST scanner that censuses bare [:N] slices against a ledger
// (test_truncation_discipline.py) and a registry of call-site budget
// overrides that each require a written rationale
// (test_budget_override_discipline.py). Those exist because the language
// cannot stop a silent cut at the point of writing — only catch it later.
//
// Here the same rules are the shape of the code:
//
//   - A Budget cannot be constructed meaningfully without a Why; the
//     package test walks Registry and fails on any empty rationale — the
//     override registry moved into the source, one hop from the cap.
//   - Clip is the only sanctioned way to shorten prompt-bound or
//     record-bound text, and it MARKS every cut. The marker is
//     byte-identical to Python context_budget.clip's, so records written
//     by either runtime stay parseable by the same tooling.
//   - There is no bare-slice idiom to police: reviewers grep for "[:",
//     here they grep for string slicing of Evidence-carrying values and
//     find none, because the helpers below are cheaper to use.
//
// Decree lineage (GOAL_BRAIN Decisions, 2026-08-21): "caps are
// data-driven or they go" — a cap needs a measured distribution, a
// documented field contract, or a store decision, or it gets removed.
package budget

import (
	"fmt"
	"regexp"
)

// Budget is a named cap with its written reason attached. The Why is not
// documentation garnish: TestEveryBudgetCarriesItsRationale fails the
// build's test run if any registered budget ships without one.
type Budget struct {
	Name  string
	Limit int
	Why   string
}

// Clip bounds s at b.Limit, marking any cut.
func (b Budget) Clip(s string) string { return Clip(s, b.Limit) }

// Registered budgets. Values and rationales carry over from the Python
// audit's measured distributions (2026-08 truncation audit + caps sweep).
var (
	// StepResult is the evidence window for a single step's result text.
	StepResult = Budget{
		Name:  "step-result",
		Limit: 4000,
		Why: "step results run median 1,180 / p99 4,671 chars " +
			"(2026-08 audit of runs/*/build); 4000 shows ~95% of payloads whole",
	}

	// BlockReason bounds a stuck/block reason reaching a retry prompt or
	// escalation surface.
	BlockReason = Budget{
		Name:  "block-reason",
		Limit: 1000,
		Why: "stuck/block reasons run median 291 / p99 594 / max 913 " +
			"(n=184, caps sweep 2026-08-21); 1000 is a runaway bound above max",
	}

	// FailureChainEntry bounds one failure_chain entry landing in the
	// memory ledger.
	FailureChainEntry = Budget{
		Name:  "failure-chain-entry",
		Limit: 600,
		Why:   "measured p99 of stuck/block reasons (594, n=184, caps sweep 2026-08-21)",
	}

	// OperatorDoc bounds a hand-written operator doc (GOALS/CONTEXT/
	// SIGNALS.md) riding a planning prompt.
	OperatorDoc = Budget{
		Name:  "operator-doc",
		Limit: 4000,
		Why: "live operator docs run ~1k chars (925-1161 measured 2026-08-21); " +
			"4k is a runaway bound for a hand-written file, not a working cap",
	}

	// VerdictProse bounds closure/verdict prose fields on stored rows.
	VerdictProse = Budget{
		Name:  "verdict-prose",
		Limit: 2000,
		Why: "matches Python VERDICT_PROSE_CAP (measured over 156 metadata " +
			"stamps + 50 closure rows, 2026-08-13) so rows interoperate",
	}
)

// Registry enumerates every registered budget for the rationale test and
// for tooling that renders the ledger. Add new budgets HERE — an
// unregistered Budget literal elsewhere is the Go-side equivalent of the
// unregistered-override bug class and the test cannot see it, so the
// convention is: budgets live in this package, callers import them.
var Registry = []Budget{
	StepResult,
	BlockReason,
	FailureChainEntry,
	OperatorDoc,
	VerdictProse,
}

// markerRe recognizes a clip marker at end-of-string. Format is
// byte-identical to Python context_budget.clip so mixed-runtime records
// stay parseable by one tool.
var markerRe = regexp.MustCompile(`… \[truncated: first \d+ of \d+ characters\]$`)

// Clip bounds s at limit characters (runes, matching Python's len()
// semantics), appending an honest marker when it cuts. Idempotent: text
// already carrying a clip marker passes through unchanged, so re-clipping
// on a read path cannot eat the marker or double-cut (the stacked-cut
// failure the Python audit kept finding). limit <= 0 disables the bound
// (a breaker that is off is off — never a silent zero-width cut).
func Clip(s string, limit int) string {
	if limit <= 0 {
		return s
	}
	if markerRe.MatchString(s) {
		return s
	}
	r := []rune(s)
	if len(r) <= limit {
		return s
	}
	return fmt.Sprintf("%s … [truncated: first %d of %d characters]",
		string(r[:limit]), limit, len(r))
}
