package scans

// Minimal notify port for the cadence-verdict escalation surface. Python's
// notify.emit does THREE things: (1) ALWAYS appends a structured row to
// memory/events.jsonl via observe.write_event — the durable half a polling
// substrate reads; (2) for ESCALATION_FILE_EVENTS (self_improvement_verdict
// is one) appends the full payload to output/escalations.jsonl — the decreed
// "headless, no substrate go-between" escalation surface operators and
// Python-side pollers actually check; (3) runs a configured hook command
// when notify.command + notify.events allow. The Go port carries (1) and
// (2) — r1 parity review: shipping only (1) meant a degraded_needs_review
// verdict never reached the review queue, silently hollowing the authority-
// asymmetry design's "surface for review" half. The hook COMMAND remains a
// spend/exec surface deferred with the heartbeat tranche (named in the
// package doc). Never raises — a notify must not take the cadence down.

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// notifyVerdict ports _notify_verdict: a self_improvement_verdict event with
// a human-readable reason (the delivery-loop decree: the user must hear the
// outcome in plain words where they poll).
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
		detail, _ := rates["revert_detail"].(string)
		if detail == "" {
			detail = "no behavioral rollback"
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
	// Python notify._emit projects the payload into write_event(goal=reason,
	// detail=clip(summary,300)) — the events row carries prose, not the
	// payload keys, and the 300 pre-clip means >300-char reasons get the
	// SAME nested clip markers in both runtimes.
	writeEvent(ws, "self_improvement_verdict", reason, budget.Clip(reason, 300))

	// The durable escalation half: full payload, string fields bounded at
	// 2000 (the escalation ledger owns its bounds — Python round-14 review).
	payload := map[string]any{
		"ts":            nowISO(),
		"event_type":    "self_improvement_verdict",
		"suggestion_id": s.SuggestionID,
		"category":      s.Category,
		"target":        s.Target,
		"action":        action,
		"blocking":      blocking,
		"reason":        budget.Clip(reason, 2000),
		"summary":       budget.Clip(reason, 2000),
	}
	for k, v := range rates {
		if sv, ok := v.(string); ok {
			v = budget.Clip(sv, 2000)
		}
		payload[k] = v
	}
	writeEscalation(ws, payload)
}

// writeEscalation appends one row to output/escalations.jsonl — the durable
// escalation-class ledger that exists whether or not any notify lane is
// configured. A failure here defeats the file's whole purpose ("the thing
// you check when nothing else is configured"), so it is loud, not silent.
func writeEscalation(ws string, entry map[string]any) {
	p := filepath.Join(ws, "output", "escalations.jsonl")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "[notify] escalation file write failed: %v\n", err)
		return
	}
	// json.dumps(entry, default=str) over there. This ledger is the
	// decreed headless escalation surface and Python-side pollers read
	// it, so its bytes are shared bytes (adversarial mission-r8 HIGH).
	// Key order is a named loss here and only here: the entry is a Go
	// map, so Python's insertion order was gone before this function saw
	// it — escaping and ensure_ascii are recovered.
	line, err := pyval.DumpsCompactPy(pyval.FromPlain(entry))
	if err != nil {
		fmt.Fprintf(os.Stderr, "[notify] escalation file write failed: %v\n", err)
		return
	}
	if err := record.AppendRawLine(p, []byte(line)); err != nil {
		fmt.Fprintf(os.Stderr, "[notify] escalation file write failed: %v\n", err)
	}
}

// writeEvent appends one observe.write_event-shaped row to memory/events.jsonl
// (the cross-runtime event feed maro-observe tails). Field set and the
// detail/goal breakers match the Python writer — rows from either runtime
// must rehydrate in the other. Best-effort: failures are swallowed exactly
// like Python's never-raises contract.
func writeEvent(ws, eventType, goal, detail string) {
	dir := memoryDir(ws)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return
	}
	entry := map[string]any{
		"event_type": eventType,
		"ts":         nowISO(),
		// write_event clips goal to 80 with a bare cut (its own contract);
		// the notify caller pre-clips to 200 first — net effect is 80.
		"goal":              clipRunes(goal, 80),
		"project":           "",
		"loop_id":           "",
		"step":              "",
		"step_idx":          0,
		"status":            "",
		"tokens_in":         0,
		"tokens_out":        0,
		"cache_read_tokens": 0,
		"model":             "",
		"elapsed_ms":        0,
		// 200 is load-bearing (PIPE_BUF row atomicity downstream) — kept,
		// and the cut announces itself (budget.Clip is a breaker, not a
		// silent truncator).
		"detail": budget.Clip(detail, 200),
	}
	// observe.write_event's own json.dumps. events.jsonl is the
	// cross-runtime feed maro-observe tails; a detail containing `>` or
	// a non-ASCII character was written here in a spelling no CPython
	// writer produces (adversarial mission-r8 HIGH).
	line, err := pyval.DumpsCompactPy(pyval.FromPlain(entry))
	if err != nil {
		return
	}
	_ = record.AppendRawLine(filepath.Join(dir, "events.jsonl"), []byte(line))
}
