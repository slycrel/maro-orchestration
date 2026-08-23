package record

import (
	"strconv"
	"strings"
)

// Verdict-trust policy (Python memory_ledger.verdict_trust — VERIFY_LEARN_ARC
// §4): classify how much a run's goal-verdict may be trusted by learning
// consumers. The single policy function, consumed by the V2 cadence windows
// (internal/scans) and any future crystallization gate.
//
// Ported semantics, with ONE named divergence carried over from the
// inspector/evolver/recall twins (r3 fix, backport-candidate #9): a MALFORMED
// goal_achieved (present but not a bool) is graded judged-NOT-achieved, where
// fork-point Python's `is False` / `is None` checks let it read as unjudged.
// The safe direction for a trust policy is the same as for the fair-cap: a
// value someone wrote but nobody can read must not earn the trusted-good path.

const (
	// VerdictConfidenceFloor mirrors memory_ledger.VERDICT_CONFIDENCE_FLOOR.
	VerdictConfidenceFloor = 0.7

	VerdictTrustFull        = "full"
	VerdictTrustDirectional = "directional"
	VerdictTrustNeutral     = "neutral"
	VerdictTrustExcluded    = "excluded"
)

// GoalAchieved reads a row's goal_achieved with the hardened tri-state the
// port's readers share (inspector.triState/evolver.triState/recall):
// (judged, achieved). Absent or nil → unjudged. A bool → judged, its value.
// Any other type → judged, NOT achieved (malformed values never read good).
func GoalAchieved(row map[string]any) (judged, achieved bool) {
	v, present := row["goal_achieved"]
	if !present || v == nil {
		return false, false
	}
	if b, ok := v.(bool); ok {
		return true, b
	}
	return true, false
}

// VerdictTrust classifies one outcomes.jsonl row (ledger rehydration shape).
//
//	full        — judged, confidence >= floor (or no confidence attached:
//	              deterministic judges are authoritative), not env-capped.
//	directional — judged but an explicit confidence below the floor.
//	neutral     — verdict absent (not judged ≠ failed).
//	excluded    — closure_unverifiable: the verifier's own failure, excluded
//	              from ALL learning consumers.
func VerdictTrust(row map[string]any) string {
	if s, _ := row["goal_verdict_source"].(string); s == "closure_unverifiable" {
		return VerdictTrustExcluded
	}
	judged, _ := GoalAchieved(row)
	if !judged {
		return VerdictTrustNeutral
	}
	if conf, ok := coerceFloat(row["goal_verdict_confidence"]); ok && conf < VerdictConfidenceFloor {
		return VerdictTrustDirectional
	}
	return VerdictTrustFull
}

// coerceFloat accepts float64/int/json.Number-ish values the tolerant JSONL
// readers can produce, PLUS numeric strings — Python's `float(conf)` parses
// "0.5", and this is the single §4 policy site both runtimes consume, so a
// string-typed confidence must classify identically (r1 parity review: Go
// read "0.5" as unparseable → FULL where Python reads DIRECTIONAL). Only a
// NON-numeric value is (0, false) — that is Python's TypeError/ValueError
// pass, and an unreadable confidence never downgrades a judged verdict.
func coerceFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case string:
		t := strings.TrimSpace(n)
		// Go ParseFloat accepts hex floats ("0x1p-2"); Python float()
		// raises on them and classifies FULL via the error pass — reject
		// hex so both runtimes agree (r2 review LOW-1).
		low := strings.TrimLeft(strings.ToLower(t), "+-")
		if strings.HasPrefix(low, "0x") {
			return 0, false
		}
		f, err := strconv.ParseFloat(t, 64)
		return f, err == nil
	case interface{ Float64() (float64, error) }: // json.Number
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}
