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
	"unicode/utf8"
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

// ClipInfo is Clip plus an honest provenance bit: the bool is true only
// when THIS call cut s and appended the marker. Callers that later
// strip the marker must gate on it — marker-shaped text in un-cut
// input (under the limit, or an over-limit string whose forged tail
// rides the idempotency short-circuit) is the AUTHOR's content, not
// Clip's (adversarial director r6, both lenses: true-end position
// alone is forgeable by any input Clip passes through unchanged).
func (b Budget) ClipInfo(s string) (string, bool) {
	out := b.Clip(s)
	return out, out != s
}

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

	// StepContextTotal bounds the TOTAL prior-step evidence in one
	// worker prompt — the per-entry clip alone is "unbounded in the
	// dimension that actually grows" (context_budget.py docstring; the
	// quadratic-growth case ContextBudget exists for). Oldest entries
	// are evicted, and the eviction is marked (adversarial r2
	// 2026-08-22, Skeptic: only the per-entry half was ported).
	StepContextTotal = Budget{
		Name:  "step-context-total",
		Limit: 24000,
		Why: "matches Python ContextBudget DEFAULT_TOTAL_BUDGET (~6k tokens; " +
			"sized 2026-08 so five p99 step results fit whole)",
	}

	// VerdictProse bounds closure/verdict prose fields on stored rows.
	VerdictProse = Budget{
		Name:  "verdict-prose",
		Limit: 2000,
		Why: "matches Python VERDICT_PROSE_CAP (measured over 156 metadata " +
			"stamps + 50 closure rows, 2026-08-13) so rows interoperate",
	}

	// LessonInject bounds the TOTAL rendered lesson block reaching the
	// decompose prompt. It is a line BREAKER, never a truncator: the
	// render stops adding whole lines at the bound (and only rendered
	// lines are cited), it never cuts mid-line — recall caps are
	// circuit-breakers, not truncators (caps decree 2026-08-21, the
	// day the arbitrary 600-char recall cap died).
	LessonInject = Budget{
		Name:  "lesson-inject",
		Limit: 1200,
		Why: "matches Python memory._MAX_LESSON_INJECT_CHARS — bounds token " +
			"spikes as lessons accumulate; enforced as a whole-line breaker " +
			"with cited-only-if-rendered, never a mid-line cut",
	}

	// RecallContext bounds the assembled recall context block riding a
	// planning prompt.
	RecallContext = Budget{
		Name:  "recall-context",
		Limit: 4000,
		Why: "same bound as Python RecallResult.as_context_block " +
			"max_chars=4000 (widened from 1200 in the 2026-08-13 STORE " +
			"review: briefs were severed mid-instruction); clip reserves " +
			"64 marker chars like the Python site. Coverage differs: the " +
			"Go v1 block is attempts-only, a strict subset of the " +
			"substrates Python folds under this number (see PORT.md)",
	}

	// PanicTrace bounds a recovered panic's value+stack riding an
	// instrumentation row (recall's error_recall_panic).
	PanicTrace = Budget{
		Name:  "panic-trace",
		Limit: 4000,
		Why: "debug.Stack() for a shallow call tree runs 1-3k chars; 4000 " +
			"keeps the frames that locate the bug while bounding a " +
			"captain's-log event row (adversarial recall r2 2026-08-22: a " +
			"bare panic VALUE with no trace is un-actionable)",
	}

	// PanicValue bounds the recovered panic's VALUE half before it is
	// joined with the stack under PanicTrace — value-first ordering with
	// one shared budget let a runaway payload-carrying value crowd out
	// the stack entirely (adversarial recall r3 2026-08-22).
	PanicValue = Budget{
		Name:  "panic-value",
		Limit: 500,
		Why: "panic values are usually one line; 500 chars shows any sane " +
			"value whole while guaranteeing ~3.4k of PanicTrace's 4000 " +
			"remains for the stack — the actionable half of the record",
	}

	// WorkerJudgeWindow bounds one worker result inside the director's
	// review and compile prompts — a judge window, so the cut is MARKED
	// and the echo check judges against the same window the compiler
	// actually saw (adversarial director r1: echoing the full result
	// against a report compiled from the first 4000 chars produced
	// false DROPPED verdicts). Python uses a bare 4000 literal at both
	// sites (_review_worker_output, _compile_report); registering it
	// here is the caps-live-in-the-registry decree.
	WorkerJudgeWindow = Budget{
		Name:  "worker-judge-window",
		Limit: 4000,
		Why: "Python's truncation audit picked 4000 as keeping ~99% of " +
			"worker outputs whole for the review verdict; the compile " +
			"window shares it so echo checks compare like against like",
	}

	// InjectedStep bounds one worker-injected step's text before it
	// becomes the next prompt's step line.
	InjectedStep = Budget{
		Name:  "injected-step",
		Limit: 500,
		Why: "inject_steps is model-authored text spliced into later prompt " +
			"headers unreviewed; the schema asks for <20 words, so 500 chars " +
			"is a runaway bound, not a working cap (adversarial exec-tranche " +
			"review 2026-08-22, Architect: uncapped injected text was the one " +
			"unclipped prompt input)",
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
	StepContextTotal,
	VerdictProse,
	LessonInject,
	RecallContext,
	PanicTrace,
	PanicValue,
	InjectedStep,
	WorkerJudgeWindow,
}

// markerRe recognizes a clip marker at end-of-string. Format is
// byte-identical to Python context_budget.clip so mixed-runtime records
// stay parseable by one tool. Digit runs are bounded and the match is
// position-guarded below, mirroring Python's 2026-08-14 fixpoint fix:
// with an unbounded \d+ and a bare suffix match, any text merely ENDING
// in a marker-shaped string passed through every cap unbounded
// (adversarial round on this port, all four lenses, 2026-08-22).
var markerRe = regexp.MustCompile(` … \[truncated: first \d{1,9} of \d{1,9} characters\]$`)

// StripMarker removes Clip's own marker from the true END of s — the
// only position Clip ever writes it. Interior or line-final
// marker-shaped text is CONTENT (possibly forged by whatever authored
// s) and passes through untouched (adversarial director r5, both
// lenses: a multiline anchor let any line ending in a forged marker be
// stripped). Position alone is a one-directional guarantee — genuine
// markers are always at the true end, but a true-end marker is only
// genuine when Clip actually cut — so callers MUST gate on ClipInfo's
// bit before stripping (adversarial director r6, both lenses).
func StripMarker(s string) string {
	return markerRe.ReplaceAllString(s, "")
}

// markerMax is the longest legitimate marker: fixed text plus two
// 9-digit counts (Python _CLIP_MARKER_MAX).
const markerMax = 64

// Clip bounds s at limit characters (runes, matching Python's len()
// semantics), appending an honest marker when it cuts. Idempotent at the
// same or a wider limit: text already carrying a clip marker passes
// through unchanged, so re-clipping on a read path cannot eat the marker
// or double-cut (the stacked-cut failure the Python audit kept finding).
// A strictly TIGHTER re-clip still cuts — the payload genuinely doesn't
// fit — nesting the old marker inside the new payload, exactly as the
// Python docstring specifies. limit <= 0 disables the bound (a breaker
// that is off is off — never a silent zero-width cut).
//
// The two guards on the marker match keep the invariant honest even
// against marker-SHAPED payload text: the result is never longer than
// limit + markerMax runes. (The counts themselves are advisory prose — a
// payload that fakes them fakes only its own provenance note, never the
// bound.)
func Clip(s string, limit int) string {
	if limit <= 0 {
		return s
	}
	r := []rune(s)
	if len(r) <= limit {
		return s
	}
	if loc := markerRe.FindStringIndex(s); loc != nil {
		markerStart := utf8.RuneCountInString(s[:loc[0]])
		if markerStart <= limit && len(r)-markerStart <= markerMax {
			return s
		}
	}
	return fmt.Sprintf("%s … [truncated: first %d of %d characters]",
		string(r[:limit]), limit, len(r))
}
