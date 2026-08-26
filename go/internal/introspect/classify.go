// Package introspect ports src/introspect.py — the failure taxonomy, the
// mechanical tool-edge classifiers, the diagnosis record and the lenses.
//
// The port has read diagnoses.jsonl since the graduation tranche
// (graduation, scans/verify, notify/escalation_context all consume it) and
// has never WRITTEN one: every row in that shared store was minted by the
// Python runtime. This package is the writing half.
package introspect

import (
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// FailureClasses is introspect.FAILURE_CLASSES — the taxonomy AND its
// one-line descriptions.
//
// The descriptions are carried rather than dropped because they are not
// documentation: `maro-introspect` prints them, the recovery planner keys
// templates off the class names, and graduation stamps a class into a
// lesson an operator reads. A port that kept only the keys would make the
// two runtimes describe the same class differently the first time anyone
// rendered one.
//
// A Go map has no order and Python's dict does; nothing here iterates it
// for output, and the one place order would matter (the CLI listing) sorts.
var FailureClasses = map[string]string{
	"setup_failure":             "Step 1 blocks with adapter/import error before real work starts",
	"adapter_timeout":           "Step blocks with tokens=0 and elapsed > 60s (subprocess timeout)",
	"constraint_false_positive": "Step blocked by constraint with tokens=0 on natural-language step",
	"decomposition_too_broad":   "Single step consumed > 200K fresh tokens (cache reads excluded) or > 120s",
	"empty_model_output":        "Model returned tokens but content < 20 chars with no tool call",
	"retry_churn":               "Same step retried 2+ times with different block reasons",
	"budget_exhaustion":         "max_iterations reached with remaining steps undone",
	"token_explosion":           "Fresh-token growth rate > 3x between consecutive steps (cache reads excluded; research tasks exempt for moderate growth)",
	"cost_spike":                "Single step or whole-loop cache-aware dollar cost exceeds threshold (real spend, not raw token volume)",
	"artifact_missing":          "Loop completed but no readable output in done steps",
	"integration_drift":         "ImportError or AttributeError caught in execution path",
	// Model—tool edge classes (MH taxonomy adopt, 2026-08-09): mechanical
	// classifiers over the inner agent's real tool transcript, stamped on
	// the step outcome at execution time and carried on step_done events.
	"tool_recovery_failure": "Inner agent hit 3+ consecutive tool errors in one step without recovering",
	"tool_feedback_neglect": "Step reported done but its final tool call errored (result built past an unresolved failure)",
	"tool_arg_malformed":    "Tool call errored with an argument-shape failure (usage/unknown-flag/TypeError signature)",
	"tool_hallucination":    "Inner agent called a tool that doesn't exist ('No such tool available' error)",
	"healthy":               "No pathology detected",
}

// Diagnosis thresholds — the named constants, at their Python values.
const (
	// BroadStepTokenLimit: a single step consuming this many tokens
	// indicates the step should have been broken into sub-steps.
	BroadStepTokenLimit = 200_000
	// BroadStepElapsedMS / BroadStepElapsedMinTokens: the wall-clock form
	// of the same signal, gated on the step also being expensive.
	BroadStepElapsedMS        = 120_000
	BroadStepElapsedMinTokens = 50_000
	// TokenExplosionRatio: N× the average step cost is a runaway step.
	TokenExplosionRatio = 3.0
	// RetryChurnLimit: the same step attempted this many times or more.
	RetryChurnLimit = 2
	// CostSpikeFraction: a step that took this share of the loop's tokens.
	CostSpikeFraction = 0.90
	// StepCostWarnUSD / LoopCostWarnUSD: cache-aware DOLLAR thresholds, so
	// they do not fire on cheap cache re-reads.
	StepCostWarnUSD = 0.50
	LoopCostWarnUSD = 2.00
	// ToolErrorStreakLimit: consecutive errored calls with no intervening
	// success — thrash, not recovery.
	ToolErrorStreakLimit = 3
)

// toolArgErrorSignatures are error-output substrings meaning the CALL was
// shaped wrong, as opposed to the environment failing — the model—tool
// edge, not env—tool. Matched against the LOWERCASED output.
//
// Order is load-bearing: the classifier reports the FIRST signature that
// matches, and that string goes into the evidence an operator reads. It is
// Python's tuple order, not sorted.
//
// "command not found" and "no such file" are deliberately absent — those
// are environment gaps (the LT-4 container audit, where workers exit-127'd
// on binaries the image never had).
var toolArgErrorSignatures = []string{
	"usage:",
	"unrecognized option",
	"unknown option",
	"unknown flag",
	"invalid option",
	"invalid argument",
	"typeerror",
	"unexpected keyword argument",
	"missing required",
	"required argument",
	"invalid choice",
}

// ToolPathology is one row of classify_tool_pathologies' output —
// `{"cls": ..., "evidence": ...}`.
type ToolPathology struct {
	Class    string `json:"cls"`
	Evidence string `json:"evidence"`
}

// ClassifyToolPathologies is introspect.classify_tool_pathologies:
// mechanical model—tool edge classifiers over ONE step's real tool
// transcript. Pure heuristics, no LLM, no I/O.
//
// Events arrive as pyval.Obj rather than map[string]any because every
// field they touch goes through Python's `str()`, and `str()` over a dict
// depends on insertion order — a Go map cannot answer it, and pyval.Repr
// refuses to guess. Same reason the evolver's mint decodes ordered.
//
// The four checks run in a FIXED order and the result carries that order,
// because a caller stamps the list onto a step outcome and the first entry
// is what a summary line shows.
func ClassifyToolPathologies(toolEvents []pyval.Obj, stepStatus string) []ToolPathology {
	out := []ToolPathology{}
	if len(toolEvents) == 0 {
		return out
	}

	// `te.get("is_error")` is a TRUTHINESS test, not `is True`. A stamp of
	// the string "no" is truthy in Python and would be false under a
	// `.(bool)` assertion — opposite answers about the same event, and the
	// event is the evidence for a lesson both runtimes read.
	isError := func(te pyval.Obj) bool {
		v, _ := te.Get("is_error")
		return pyval.Truthy(v)
	}
	// `.get(k, default)` — presence. A present null is None, whose str()
	// is "None" and whose repr() is also "None" (no quotes), which is why
	// the two renderings below cannot share one helper.
	get := func(te pyval.Obj, key string, def any) any {
		if v, ok := te.Get(key); ok {
			return v
		}
		return def
	}

	for _, te := range toolEvents {
		if !isError(te) {
			continue
		}
		if strings.Contains(pytext.Lower(pyval.Str(get(te, "output", ""))),
			"no such tool available") {
			// `{name!r}` — repr, so a string name is QUOTED and a null one
			// renders as a bare None.
			out = append(out, ToolPathology{
				Class: "tool_hallucination",
				Evidence: fmt.Sprintf("call to %s answered 'No such tool available'",
					pyval.Repr(get(te, "name", "?"))),
			})
			break
		}
	}

	streak, worstStreak := 0, 0
	var streakNames, worstNames []string
	for _, te := range toolEvents {
		if isError(te) {
			streak++
			streakNames = append(streakNames, pyval.Str(get(te, "name", "?")))
			if streak > worstStreak {
				worstStreak = streak
				// A COPY. Python's `list(streak_names)` is a copy, and
				// aliasing the slice here would let later appends rewrite
				// the recorded worst run in place.
				worstNames = append([]string(nil), streakNames...)
			}
		} else {
			streak = 0
			streakNames = nil
		}
	}
	if worstStreak >= ToolErrorStreakLimit {
		names := worstNames
		if len(names) > 6 {
			names = names[:6]
		}
		out = append(out, ToolPathology{
			Class: "tool_recovery_failure",
			Evidence: fmt.Sprintf("%d consecutive tool errors (%s)",
				worstStreak, strings.Join(names, ", ")),
		})
	}

	last := toolEvents[len(toolEvents)-1]
	if stepStatus == "done" && isError(last) {
		out = append(out, ToolPathology{
			Class: "tool_feedback_neglect",
			Evidence: fmt.Sprintf("final tool call errored (%s: %s) but step reported done",
				pyval.Str(get(last, "name", "?")),
				pyval.Clip(pyval.Str(get(last, "output", "")), 120)),
		})
	}

	for _, te := range toolEvents {
		if !isError(te) {
			continue
		}
		outText := pytext.Lower(pyval.Str(get(te, "output", "")))
		sig := ""
		for _, s := range toolArgErrorSignatures {
			if strings.Contains(outText, s) {
				sig = s
				break
			}
		}
		if sig == "" {
			continue
		}
		out = append(out, ToolPathology{
			Class: "tool_arg_malformed",
			Evidence: fmt.Sprintf("%s errored with %s signature: %s",
				pyval.Str(get(te, "name", "?")), pytext.Repr(sig),
				pyval.Clip(pyval.Str(get(te, "output", "")), 120)),
		})
		// One representative specimen is enough per step.
		break
	}
	return out
}
