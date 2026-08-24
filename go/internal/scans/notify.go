package scans

// The cadence-verdict escalation surface.
//
// This file used to carry a PARTIAL, PRIVATE copy of notify.emit — its own
// escalation writer, its own events.jsonl writer, and a doc comment saying
// the hook command was "deferred with the heartbeat tranche". Both writers
// have moved to internal/notify, which is the real port, and this file now
// only builds the payload.
//
// That is not tidying. The r8 finding was that a shared emitter with a
// second copy drifts silently, and this WAS the second copy: its
// escalations.jsonl rows went out with alphabetically sorted keys (a Go
// map) while notify's go out in Python's dict-literal order, and the
// missing hook meant a configured substrate lane never saw a verdict at
// all. One writer, one contract, one differential.

import (
	"context"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/notify"
)

// notifyVerdict ports _notify_verdict: a self_improvement_verdict event with
// a human-readable reason (the delivery-loop decree: the user must hear the
// outcome in plain words where they poll).
//
// Never raises — notify.Emit swallows and logs; a notification must not take
// the cadence down.
func notifyVerdict(ws string, s evolver.Suggestion, action string, blocking bool, rates map[string]any) {
	srB, srA := pyVal(rates["stuck_rate_before"]), pyVal(rates["stuck_rate_after"])
	var reason string
	switch action {
	case "reverted":
		reason = fmt.Sprintf(
			"Auto-reverted a degraded self-applied change (%s '%s'): stuck-rate "+
				"rose %s→%s. The system cleaned up its own mess.",
			s.Category, s.Target, srB, srA)
	case "revert_failed":
		// `rates.get('revert_detail', 'no behavioral rollback')` — a DEFAULT,
		// so it fires only on an absent key. A present-but-empty
		// revert_detail interpolates as empty on the Python side, and the
		// earlier port's `if detail == ""` substitution silently produced a
		// different sentence for that case.
		detail := "no behavioral rollback"
		if v, ok := rates["revert_detail"]; ok {
			detail = pyVal(v)
		}
		reason = fmt.Sprintf(
			"A degraded self-applied change (%s '%s') could NOT be auto-reverted "+
				"(%s) — stuck-rate rose %s→%s. Manual repair needed: the change is "+
				"still live.",
			s.Category, s.Target, detail, srB, srA)
	default:
		reason = fmt.Sprintf(
			"A human-applied change (%s '%s') degraded behavior (stuck %s→%s) "+
				"and was NOT auto-reverted (authority asymmetry). Review: revert or keep.",
			s.Category, s.Target, srB, srA)
	}

	// The payload carries neither `ts` nor `event_type`: the escalation
	// writer supplies both, in that leading order. Nor does it pre-clip
	// `reason`/`summary` — the ledger owns its bounds, and a caller that
	// clips first is the per-sender bounding Python's round-14 review found
	// cannot be trusted.
	payload := map[string]any{
		"suggestion_id": s.SuggestionID,
		"category":      s.Category,
		"target":        s.Target,
		"action":        action,
		"blocking":      blocking,
		"reason":        reason,
		"summary":       reason,
	}
	for k, v := range rates {
		payload[k] = v // `**rates` — rates keys override on collision
	}
	notify.Emit(context.Background(), ws, "self_improvement_verdict", payload,
		notify.Options{})
}
