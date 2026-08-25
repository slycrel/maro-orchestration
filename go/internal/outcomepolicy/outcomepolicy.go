// Package outcomepolicy ports outcome_policy.py — the shared rule for which
// completed outcomes may seed learning.
//
// Two durable representations reach the learning pipeline and this leaf owns
// their common eligibility test so no caller translates one into the other:
// curated run cards carry `success_class`, raw ledger rows carry `status`
// and `goal_achieved`.
//
// Almost every line here is a Python operator whose Go spelling is NOT the
// obvious one, which is why the port is a package rather than four inline
// conditions:
//
//   - `outcome.get("audit_incomplete")` is TRUTHINESS. A row carrying the
//     string "false" is audit-incomplete; a row carrying 0 is not.
//   - `... in (OUT_OF_BUDGET, EXTERNAL_INTERRUPT)` is `in` over a TUPLE,
//     which is `==` against each element and never hashes. An unhashable
//     stop_verdict answers False here rather than raising — unlike the
//     frozenset one line below.
//   - `is True` / `is False` / `is None` are IDENTITY against the
//     singletons. The numeric 1 is not True, and 0 is not False, so a
//     foreign writer that spells its booleans as numbers takes a different
//     branch than one that spells them as booleans.
//   - `"success_class" in outcome` is key PRESENCE, so a key present and
//     null is a curated row whose class is None — which then answers False
//     against the frozenset, rather than falling through to the raw path.
package outcomepolicy

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/stopverdicts"
)

// learnableSuccessClasses is Python's frozenset. Membership over it HASHES
// the candidate, which is the one place in this file where an unhashable
// value raises instead of answering False.
var learnableSuccessClasses = map[string]bool{
	"success":           true,
	"done-unverified":   true,
	"achieved-not-done": true,
}

// Outcome is the mapping side of the two shapes Python accepts. The port
// takes pyval.Obj rather than a Go struct because every caller reads these
// rows off disk, and the distinction between "key absent" and "key present
// and null" decides two separate branches below.
type Outcome = pyval.Obj

// IsVerdictPending reports whether a deferred row is still waiting for its
// goal verdict.
//
// Agenda outcomes are written before closure verification and carry
// `lesson_extraction_status=deferred` until that contract finishes. An
// unjudged row in that state is not success evidence — it may be the residue
// of a failed verdict write.
//
// Python accepts a Mapping OR a ledger dataclass, and the two arms differ in
// their default for the extraction field: `.get` yields None for a missing
// key while `getattr(..., "")` yields the empty string. Both compare
// unequal to "deferred", so the arms agree; the difference is recorded here
// because it is the kind of asymmetry a later edit turns into a divergence.
func IsVerdictPending(o Outcome) bool {
	achieved, hasAchieved := o.Get("goal_achieved")
	extraction, _ := o.Get("lesson_extraction_status")
	// `achieved is None` — true for a key that is ABSENT (the .get default)
	// and for one PRESENT and null. The two are the same value here.
	isNone := !hasAchieved || achieved == nil
	return isNone && pyval.Eq(extraction, "deferred")
}

// IsLearnable reports whether an outcome is safe as a successful learning
// example.
//
// A record containing `success_class` is treated as curated and must carry a
// recognized learnable class. Unknown or empty classifications FAIL CLOSED;
// they never fall back to a possibly-stale raw process status.
//
// The error return is not decoration. `outcome.get("success_class") in
// frozenset(...)` hashes its left operand, so a row whose success_class is a
// list raises TypeError in CPython — inside a function whose callers do not
// wrap it. A port that answered false there would quietly learn from a row
// CPython refuses to classify at all.
func IsLearnable(o Outcome) (bool, error) {
	// Truthiness on both, and either one alone disqualifies.
	if ai, _ := o.Get("audit_incomplete"); pyval.Truthy(ai) {
		return false, nil
	}
	if ar, _ := o.Get("audit_repair_required"); pyval.Truthy(ar) {
		return false, nil
	}

	// A cap-bounded ending with no positive goal verdict is not success
	// evidence: budget-pressure landing synthesis flips status to "done" at
	// the cap (stop-path survey 2026-07-23), and the typed stop verdict is
	// what keeps the cap-hit visible. Same fail-closed shape for an
	// interrupt: a run cut down by infra carries no goal evidence even
	// though its status may still read "done".
	//
	// `in` over a TUPLE, so pyval.Eq against each element rather than a
	// hash lookup — an unhashable stop_verdict answers false here.
	sv, _ := o.Get("stop_verdict")
	capped := pyval.Eq(sv, stopverdicts.OutOfBudget) ||
		pyval.Eq(sv, stopverdicts.ExternalInterrupt)
	achieved, _ := o.Get("goal_achieved")
	// `is not True`: identity, so a numeric 1 does NOT rescue a capped run.
	if capped && !isTrueSingleton(achieved) {
		return false, nil
	}

	// PRESENCE, not truthiness: a curated row with a null success_class
	// takes this branch and answers false, rather than falling through to
	// the raw status path below.
	if sc, ok := o.Get("success_class"); ok {
		if _, hashable := pyval.HashKey(sc); !hashable {
			// The 3.12+ sentence, measured on 3.14.3 — `in` over a SET
			// reports the set-element form, not the bare "unhashable
			// type" one. Getting this wrong is the M3 defect from round
			// 8, and this is its third appearance in code written AFTER
			// that fix: the short spelling is what comes to mind, and
			// only the interpreter corrects it.
			return false, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
				"cannot use '%s' as a set element (unhashable type: '%s')",
				pyval.TypeName(sc), pyval.TypeName(sc))}
		}
		// The frozenset holds three str keys, so only a str can match. A
		// number never collides with one — measured: `1 in S` and `True in
		// S` are both False.
		s, isStr := sc.(string)
		return isStr && learnableSuccessClasses[s], nil
	}

	// Verdict-preferred raw path (SF-2, the mirror of achieved-not-done): a
	// judged goal_achieved=True is success evidence regardless of process
	// status. The budget/interrupt check above already ran, and it only
	// fires when achieved is NOT True, so the two cannot both apply.
	if isTrueSingleton(achieved) {
		return true, nil
	}

	status, _ := o.Get("status")
	// `is not False` — identity again, so a numeric 0 does NOT disqualify.
	if !pyval.Eq(status, "done") || isFalseSingleton(achieved) {
		return false, nil
	}
	return !IsVerdictPending(o), nil
}

// isTrueSingleton is Python's `x is True`: a real bool carrying true, and
// nothing else. json.Number("1") and the string "true" both fail it.
func isTrueSingleton(v any) bool {
	b, ok := v.(bool)
	return ok && b
}

// isFalseSingleton is `x is False`, the same identity from the other side.
func isFalseSingleton(v any) bool {
	b, ok := v.(bool)
	return ok && !b
}
