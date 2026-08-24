// Package stopverdicts ports stop_verdicts.py: the typed vocabulary for
// WHY a run stopped short of clean success, and the typed reasons a run
// is paused.
//
// This is a vocabulary module, which makes it a different kind of port
// from the ones before it. There is almost no logic here — the value is
// entirely in the SETS being the same sets. A Go writer that stamps a
// verdict outside VALID_STOP_VALUES does not crash and does not diverge
// visibly; it writes a row that memory_ledger.stamp_outcome_stop_verdict
// would have REFUSED, and every Python consumer that switches on the
// vocabulary silently falls through to its "unknown" arm. The failure is
// a value quietly not being counted, months later, in an aggregate
// nobody is watching at the time.
//
// So the tests here are set-difference tests against the Python module,
// not behaviour tests. A missing member and an invented member are both
// caught, in both directions.
//
// The one piece of real logic is PauseReasonForErrorClass, and the one
// piece of real doctrine is that EXTERNAL_INTERRUPT is deliberately NOT
// a member of GoalVerdicts: Jeremy's decree (GOAL_BRAIN 2026-07-27 item
// 5) is that "the four verdicts are observations about the map; an
// interrupt is an event about the run". A row carrying the interrupt
// marker holds no evidence about the goal, and a learning consumer that
// treats it as failure evidence is learning from a power cut.
package stopverdicts

// The four goal-directed verdicts — observations about the goal itself,
// each with a type-derived reopen condition (a dead end does not stay a
// dead end).
const (
	// OutOfBudget: a preset cap ended the run (daily spend, max
	// iterations, token/cost budget, wall clock, restart depth). The
	// possibility is UNTESTED — this says nothing about whether the goal
	// was reachable.
	OutOfBudget = "out-of-budget"
	// ThesisRefuted: avenues exhausted WITH convergence evidence —
	// repeated attempts stopped changing the outcome.
	ThesisRefuted = "thesis-refuted"
	// NotWorthIt: a path exists but its discovered cost exceeds the
	// value. The director's escalation "close" is the one seam that
	// decides this.
	NotWorthIt = "reachable-but-not-worth-it"
	// LostThePlot: work completed coherently but does not serve the
	// original ask (closure demotion, provenance guard).
	LostThePlot = "lost-the-plot"
)

// ExternalInterrupt is an event marker, NOT a verdict. Kill switch,
// operator stop, backend death, merge/infra failure, awaiting-human
// hold. It appears only when NO map observation existed at interrupt
// time; the reason travels in stop_evidence.
const ExternalInterrupt = "external-interrupt"

// GoalVerdicts is the taxonomy: observations about the goal, admissible
// as learning evidence subject to verdict trust. ExternalInterrupt is
// deliberately absent (see the package doc).
var GoalVerdicts = map[string]bool{
	OutOfBudget:   true,
	ThesisRefuted: true,
	NotWorthIt:    true,
	LostThePlot:   true,
}

// ValidStopValues is every value the stop_verdict FIELD may legally
// hold: the four verdicts plus the interrupt marker. Named this way in
// Python to avoid implying five verdicts, and the name is kept.
var ValidStopValues = union(GoalVerdicts, set(ExternalInterrupt))

// IsGoalVerdict reports whether a value is one of the four map
// observations — the question a learning consumer must ask before
// treating a stopped run as evidence about its goal.
func IsGoalVerdict(v string) bool { return GoalVerdicts[v] }

// IsValidStopValue reports whether a value may legally be stamped into
// the stop_verdict field. Every writer checks this; an off-vocabulary
// verdict fails to UNSTAMPED so status fallbacks apply, never to a
// phantom value.
func IsValidStopValue(v string) bool { return ValidStopValues[v] }

// InterruptStatuses are the statuses that already encode the
// external-interrupt family distinctly at the STATUS level (the stop-path
// survey's "taxonomy hole" rows). classify_outcome derives a fallback
// verdict from these for runs whose stop site predates the verdict
// wiring or never reached a stamp.
var InterruptStatuses = set("interrupted", "stranded", "refused_busy", "clarification_needed")

// PausedStatuses is the same set under its decree name (§13e,
// 2026-07-31): these statuses ARE the paused state — a run that "may or
// may not ever be finished".
//
// Both names are kept, and they deliberately alias rather than being two
// literals: the stop_verdict field's interrupt marker (2026-07-27
// decree) and the paused lifecycle state (2026-07-31 decree) are
// different layers over the SAME rows, and writing the membership twice
// is how the two layers drift apart. Python does the same thing with a
// bare assignment.
var PausedStatuses = InterruptStatuses

// ExecutionFinishedStatuses mean the run actually FINISHED EXECUTING and
// could therefore have been judged — the population a missing closure
// verdict is a gap in.
//
// Deliberately excludes PausedStatuses (the run is not over, so no
// verdict is owed yet) and "error" (backend death; closure never got a
// chance, and labelling that "closure never stamped" blames the wrong
// layer).
var ExecutionFinishedStatuses = set("done", "stuck")

// The explicitly-unverdicted markers. Absence WITH a reason is a fact
// you can count; absence alone is a hole that looks identical to "not
// judged yet". All four carry goal_achieved=None — we do not know, and
// claiming otherwise is the fabrication these guards exist to prevent.
const (
	// VerdictSourceNeverStamped: closure was owed a verdict and did not
	// deliver one. That is a closure bug.
	VerdictSourceNeverStamped = "closure_never_stamped"
	// VerdictSourceRunErrored: the run died before a verdict was
	// possible — backend death, crash, no LoopResult at all. Distinct
	// from NeverStamped on purpose: nothing was owed here, because there
	// was no finished work to judge. Collapsing the two sends someone
	// hunting a closure bug that is really a backend outage.
	VerdictSourceRunErrored = "run_errored"
	// VerdictSourceNoStepsCompleted: closure declined to judge because NO
	// step completed. The member that turned out to dominate — measured
	// 2026-08-06, 259 of the 345 unverdicted agenda runs on the box (75%)
	// are this shape, not a closure gap. Without it the
	// finished-without-closure tripwire is mostly false positives, which
	// is how a tripwire trains people to ignore it.
	VerdictSourceNoStepsCompleted = "closure_skipped_no_steps"
	// VerdictSourcePendingOrphaned: a verdict WAS owed and the process
	// owing it is gone — the answer went out with the verdict pending and
	// the owning process died past the sweep grace.
	VerdictSourcePendingOrphaned = "verdict_pending_orphaned"
)

// Typed pause reasons (§13e): WHY the run is paused, machine-readable.
// Operator-class means a human is in the loop.
const (
	PauseOpManual        = "manual-intervention"
	PauseOpClarification = "awaiting-clarification"
	// PauseOpBudgetDecision: the budget extension ladder is exhausted
	// (Jeremy 2026-08-02) — two one-run extensions were granted and a
	// third breach arrived, so the run pauses for the user's call rather
	// than concluding out-of-budget. Distinct from PauseErrNoTokens: the
	// environment CAN continue; the chosen envelope was outgrown.
	PauseOpBudgetDecision = "budget-decision"
)

// Error-class pause reasons: the substrate cannot continue.
const (
	PauseErrBusy       = "box-busy"    // run lease / admission contention
	PauseErrWriterDied = "writer-died" // stranded sweep: crash/power loss, stamped post-hoc
	// The last three are vocabulary RESERVED in Python — the constants
	// exist and the stamp sites are an upgrade edge. They are ported at
	// full strength anyway: a reserved value that only one runtime knows
	// about is exactly how the two vocabularies drift, and
	// PauseReasonForErrorClass below already makes two of them real.
	PauseErrLLMUnreachable = "llm-unreachable"
	PauseErrNoTokens       = "no-tokens"
	PauseErrDiskFull       = "disk-full"
)

// PauseReasonsOperator and PauseReasonsError partition the valid pause
// reasons by who has to act.
var (
	PauseReasonsOperator = set(PauseOpManual, PauseOpClarification, PauseOpBudgetDecision)
	PauseReasonsError    = set(PauseErrBusy, PauseErrWriterDied,
		PauseErrLLMUnreachable, PauseErrNoTokens, PauseErrDiskFull)
	ValidPauseReasons = union(PauseReasonsOperator, PauseReasonsError)
)

// PauseReasonByStatus is the status-derived fallback for rows whose
// writer predates reason stamping.
//
// Only UNAMBIGUOUS statuses appear. "interrupted" is deliberately absent:
// it covers kill switch, wall-clock timeout, and backend death at once,
// and guessing among them would fabricate provenance. A lookup miss here
// is the honest answer, not a gap to fill.
var PauseReasonByStatus = map[string]string{
	"clarification_needed": PauseOpClarification,
	"refused_busy":         PauseErrBusy,
	"stranded":             PauseErrWriterDied,
}

// ReopenConditions is §13b made queryable: the type-derived reopen
// condition as DATA, not docstring prose. Consumers (map lens, run
// report, revisit machinery) read the condition from here so the prose
// can never drift from the data.
//
// Evidence-SPECIFIC reopen payloads — which budget, which cost estimate
// — are a separate thing recorded at stamp time (runs.StampRunStopVerdict's
// reopenPayload), not here.
var ReopenConditions = map[string]string{
	OutOfBudget:       "budget restored",
	ThesisRefuted:     "new connection evidence (a new landmark or vantage)",
	NotWorthIt:        "the cost or value estimate moves",
	LostThePlot:       "re-anchor against the original ask",
	ExternalInterrupt: "the interruption clears",
}

// ReopenCondition is the type-derived reopen condition for a stop
// verdict ("" if unknown).
func ReopenCondition(verdict string) string { return ReopenConditions[verdict] }

// PauseReasonForErrorClass maps a terminal step failure's llm_errors
// class to its typed pause.
//
// §13e decree (Jeremy 2026-08-02): environmental exhaustion — out of
// tokens, provider down — is a PAUSE ("one is a pause (out of
// tokens/error, not budget)"); a chosen budget ceiling is a CONCLUSION
// ("a conclusion that can be restarted by the user/orchestrator", the
// out-of-budget STOP verdict). This is the pause half.
//
// Everything non-environmental returns "": auth stays terminal-surfaced
// (credentials need a human, and the reserved vocabulary has no auth
// value — deliberate), budget_runaway stamps its stop verdict at its own
// break site, and ordinary step failures ride the blocked/recovery
// machinery unchanged.
func PauseReasonForErrorClass(errorClass string) string {
	switch errorClass {
	case "billing_actionable":
		// Credits/quota gone — "never retry"; tokens may return later.
		return PauseErrNoTokens
	case "retry_at":
		// Rate/usage limit with a stated reset — tokens WILL return.
		return PauseErrNoTokens
	case "failover":
		// The failover chain exhausted every backend.
		return PauseErrLLMUnreachable
	}
	return ""
}

// PauseFamily is "operator" | "error" for a valid pause reason, ""
// otherwise.
func PauseFamily(reason string) string {
	if PauseReasonsOperator[reason] {
		return "operator"
	}
	if PauseReasonsError[reason] {
		return "error"
	}
	return ""
}

func set(vals ...string) map[string]bool {
	m := make(map[string]bool, len(vals))
	for _, v := range vals {
		m[v] = true
	}
	return m
}

func union(sets ...map[string]bool) map[string]bool {
	out := map[string]bool{}
	for _, s := range sets {
		for v := range s {
			out[v] = true
		}
	}
	return out
}
