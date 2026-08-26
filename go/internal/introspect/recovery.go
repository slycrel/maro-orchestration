package introspect

import (
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// RecoveryPlan is introspect.RecoveryPlan: a mechanical intervention the
// system can apply without human review.
//
// Params is an ordered pyval.Obj rather than a map because Python's is a
// plain dict, and every consumer that RENDERS a plan does so through
// str()/json.dumps of that dict — where key order is the byte order of the
// output. No plan carries more than one key today, which is exactly why the
// ordered type is cheap insurance rather than an argument.
type RecoveryPlan struct {
	FailureClass string
	Action       string
	AutoApply    bool
	Risk         string // "low" | "medium" | "high"
	Params       pyval.Obj
}

func recoveryParams(pairs ...any) pyval.Obj {
	var o pyval.Obj
	for i := 0; i+1 < len(pairs); i += 2 {
		o.Set(pairs[i].(string), pairs[i+1])
	}
	return o
}

// recoveryTable is introspect._RECOVERY_TABLE, verbatim including the
// prose, which is operator-facing and reaches a captain's log.
//
// Python's comment says the lists are "ordered by preference
// (cheapest/safest first)" and EVERY list has exactly one entry. That is
// not a detail to tidy away: `plan_recovery`'s advisor branch returns
// `plans[1]` when the advisor answers "(b)", so the second slot is a
// reachable code path with no data behind it. The port keeps the lists.
var recoveryTable = map[string][]RecoveryPlan{
	"decomposition_too_broad": {{
		FailureClass: "decomposition_too_broad",
		Action:       "Re-run with tighter max_steps and code-surface cap",
		AutoApply:    true, Risk: "low",
		Params: recoveryParams("max_steps", 12, "hint", "limit to 3-5 files per step"),
	}},
	"constraint_false_positive": {{
		FailureClass: "constraint_false_positive",
		Action:       "Retry blocked steps — constraint patterns may have been updated",
		AutoApply:    true, Risk: "low",
		Params: recoveryParams("retry_from", "first_blocked"),
	}},
	"adapter_timeout": {{
		FailureClass: "adapter_timeout",
		Action:       "Retry with smaller step scope or switch to API adapter",
		AutoApply:    false, Risk: "medium",
		Params: recoveryParams("suggestion",
			"reduce step scope or use ANTHROPIC_API_KEY adapter"),
	}},
	"budget_exhaustion": {{
		FailureClass: "budget_exhaustion",
		Action:       "Increase max_iterations and enable budget-aware landing",
		AutoApply:    true, Risk: "low",
		Params: recoveryParams("max_iterations", 60),
	}},
	"empty_model_output": {{
		FailureClass: "empty_model_output",
		Action:       "Retry with explicit tool-call instruction in step text",
		AutoApply:    true, Risk: "low",
		Params: recoveryParams("hint",
			"You MUST call complete_step or flag_stuck. Do not return bare text."),
	}},
	"token_explosion": {{
		FailureClass: "token_explosion",
		Action: "Distill prior step outputs to key findings before continuing. " +
			"Keep full output in artifacts.",
		AutoApply: false, Risk: "medium",
		Params: recoveryParams("suggestion",
			"summarize completed_context entries to key findings, not raw truncation"),
	}},
	"cost_spike": {{
		FailureClass: "cost_spike",
		Action: "Route the costly step class to a cheaper model tier, or shrink " +
			"the cached context the worker carries per turn.",
		AutoApply: false, Risk: "medium",
		Params: recoveryParams("suggestion",
			"real spend (fresh full-rate + cache reads at 0.1x) — not a cache "+
				"artifact; lower the model tier or per-turn context"),
	}},
	"retry_churn": {{
		FailureClass: "retry_churn",
		Action:       "Skip the churning step and continue with remaining steps",
		AutoApply:    true, Risk: "low",
		Params: recoveryParams("action", "skip_and_continue"),
	}},
	// Model—tool edge classes (MH taxonomy, 2026-08-09). None auto-apply:
	// the pathology is evidence about HOW the step ran, not a mechanical
	// state the loop can safely mutate.
	"tool_feedback_neglect": {{
		FailureClass: "tool_feedback_neglect",
		Action: "Re-verify the step's claimed result against its artifacts — " +
			"it was built past an unresolved tool failure",
		AutoApply: false, Risk: "medium",
		Params: recoveryParams("action", "verify_step_result"),
	}},
	"tool_recovery_failure": {{
		FailureClass: "tool_recovery_failure",
		Action: "Inspect the step transcript for the repeated failure and " +
			"address its cause (missing tool, wrong path, bad approach) " +
			"before retrying similar steps",
		AutoApply: false, Risk: "low",
		Params: recoveryParams("action", "inspect_transcript"),
	}},
	"tool_arg_malformed": {{
		FailureClass: "tool_arg_malformed",
		Action: "Check the tool's actual interface (signature/usage) before " +
			"the next call — the call was shaped wrong, not the tool broken",
		AutoApply: false, Risk: "low",
		Params: recoveryParams("action", "check_tool_interface"),
	}},
	"tool_hallucination": {{
		FailureClass: "tool_hallucination",
		Action: "Reconcile advertised tools with what the backend actually " +
			"offers (see BACKLOG: step prompts advertising tools the " +
			"subprocess backend doesn't have)",
		AutoApply: false, Risk: "low",
		Params: recoveryParams("action", "reconcile_tool_registry"),
	}},
	"setup_failure": {{
		FailureClass: "setup_failure",
		Action:       "Check adapter resolution and import chain; surface real exception",
		AutoApply:    false, Risk: "medium",
		Params: recoveryParams("suggestion",
			"run with MARO_LOG_LEVEL=DEBUG to see the swallowed error"),
	}},
	"integration_drift": {{
		FailureClass: "integration_drift",
		Action:       "Audit import names against actual module exports",
		AutoApply:    false, Risk: "medium",
		Params: recoveryParams("suggestion",
			"run the AST cross-check from the test tightening session"),
	}},
	"artifact_missing": {{
		FailureClass: "artifact_missing",
		Action:       "Re-run with explicit artifact instruction in step hints",
		AutoApply:    true, Risk: "low",
		Params: recoveryParams("hint",
			"You MUST produce a concrete artifact (file, summary, or structured "+
				"data). Do not end a step with only status text."),
	}},
}

// PlanRecovery is introspect.plan_recovery with use_advisor=False.
//
// The advisor path is NOT ported: it calls `llm.advisor_call`, spends
// tokens on an Opus round trip, and is off by default (`use_advisor` is a
// keyword defaulting to False). Two things about it are worth recording
// rather than silently dropping.
//
// It fires only for a plan that is medium-or-high risk AND not auto-apply,
// which is nine of the fifteen classes. And when the advisor's reply
// contains the substring "(b)" it returns `plans[1]` — a second plan that
// no table entry has, so on today's data that branch would IndexError.
// Python's `len(plans) > 1` guard prevents it; the branch is dead by data,
// not by construction, and the first two-plan entry anyone adds wakes it.
func PlanRecovery(diag LoopDiagnosis) (RecoveryPlan, bool) {
	plans := recoveryTable[diag.FailureClass]
	if len(plans) == 0 {
		return RecoveryPlan{}, false
	}
	return plans[0], true
}

// PlanRecoveryAll is introspect.plan_recovery_all: every plan for a class.
//
// Python returns `list(...)` — a COPY — so a caller that mutates the result
// does not edit the module's table. The port copies for the same reason: a
// returned slice header aliases the table's backing array, and one
// `plans[0].Action = ...` from a caller would rewrite the operator-facing
// prose for every later diagnosis in the process.
func PlanRecoveryAll(diag LoopDiagnosis) []RecoveryPlan {
	plans := recoveryTable[diag.FailureClass]
	if len(plans) == 0 {
		return nil
	}
	return append([]RecoveryPlan(nil), plans...)
}

// RecurringPattern is introspect.RecurringPattern.
type RecurringPattern struct {
	FailureClass string
	Occurrences  int
	FirstSeen    string // loop_id of the FIRST occurrence
	LastSeen     string // loop_id of the most recent
	// RecoveryAction is Optional[str] in Python. "" is None here; no plan
	// in the table has an empty action, so the two cannot be confused.
	RecoveryAction string
	// GraduationCandidate is `len(diags) >= min_occurrences`, evaluated
	// AFTER a `continue` that skipped every class failing that same test.
	// It is therefore ALWAYS true. Ported as a field rather than dropped
	// because it is part of the record other code reads, and named here so
	// nobody reads a false one as meaningful — there isn't one.
	GraduationCandidate bool
}

// FindRecurringPatterns is introspect.find_recurring_patterns: which
// failure classes repeat often enough to deserve a durable fix.
//
// THE TIE-BREAK IS INSERTION ORDER, and that is the whole reason this
// cannot be a range over a Go map. Python builds `counts` with
// `setdefault`, so its keys are ordered by each class's FIRST appearance in
// the diagnosis list — which `load_diagnoses` returns NEWEST FIRST. The
// sort key is `-len(x[1])` alone and `list.sort` is stable, so two classes
// with equal counts come back most-recently-seen first. A Go map would
// return them in a different order on every run, and the caller renders
// this list to an operator.
//
// `first_seen` is `diags[-1].loop_id` and `last_seen` is `diags[0].loop_id`
// — reversed relative to how they read, because the list is
// reverse-chronological. Getting these backwards is invisible in any
// fixture whose class appears once.
func FindRecurringPatterns(ws string, minOccurrences, limit int) []RecurringPattern {
	diagnoses := LoadDiagnoses(ws, limit)
	if len(diagnoses) == 0 {
		return nil
	}

	var order []string
	groups := map[string][]LoopDiagnosis{}
	for _, d := range diagnoses {
		if d.FailureClass == "healthy" {
			continue
		}
		if _, seen := groups[d.FailureClass]; !seen {
			order = append(order, d.FailureClass)
		}
		groups[d.FailureClass] = append(groups[d.FailureClass], d)
	}

	sort.SliceStable(order, func(i, j int) bool {
		return len(groups[order[i]]) > len(groups[order[j]])
	})

	var patterns []RecurringPattern
	for _, fc := range order {
		diags := groups[fc]
		if len(diags) < minOccurrences {
			continue
		}
		action := ""
		if plan, ok := PlanRecovery(diags[0]); ok {
			action = plan.Action
		}
		patterns = append(patterns, RecurringPattern{
			FailureClass:        fc,
			Occurrences:         len(diags),
			FirstSeen:           diags[len(diags)-1].LoopID,
			LastSeen:            diags[0].LoopID,
			RecoveryAction:      action,
			GraduationCandidate: len(diags) >= minOccurrences,
		})
	}
	return patterns
}
