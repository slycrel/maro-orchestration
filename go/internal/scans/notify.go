package scans

// Minimal notify port for the cadence-verdict escalation surface. Python's
// notify.emit does two things: (1) ALWAYS appends a structured row to
// memory/events.jsonl via observe.write_event — the durable half a polling
// substrate reads; (2) runs a configured hook command when notify.command +
// notify.events allow. The Go port carries (1); the hook COMMAND is a
// spend/exec surface deferred with the heartbeat tranche (named in the
// package doc). Never raises — a notify must not take the cadence down.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// notifyVerdict ports _notify_verdict: a self_improvement_verdict event with
// a human-readable reason (the delivery-loop decree: the user must hear the
// outcome in plain words where they poll).
func notifyVerdict(ws string, s evolver.Suggestion, action string, blocking bool, rates map[string]any) {
	var reason string
	switch action {
	case "reverted":
		reason = fmt.Sprintf(
			"Auto-reverted a degraded self-applied change (%s '%s'): stuck-rate "+
				"rose %v→%v. The system cleaned up its own mess.",
			s.Category, s.Target, rates["stuck_rate_before"], rates["stuck_rate_after"])
	case "revert_failed":
		detail, _ := rates["revert_detail"].(string)
		if detail == "" {
			detail = "no behavioral rollback"
		}
		reason = fmt.Sprintf(
			"A degraded self-applied change (%s '%s') could NOT be auto-reverted "+
				"(%s) — stuck-rate rose %v→%v. Manual repair needed: the change is "+
				"still live.",
			s.Category, s.Target, detail,
			rates["stuck_rate_before"], rates["stuck_rate_after"])
	default:
		reason = fmt.Sprintf(
			"A human-applied change (%s '%s') degraded behavior (stuck %v→%v) "+
				"and was NOT auto-reverted (authority asymmetry). Review: revert or keep.",
			s.Category, s.Target, rates["stuck_rate_before"], rates["stuck_rate_after"])
	}
	// Python notify._emit projects the payload into write_event(goal=reason,
	// detail=summary) — the row carries prose, not the payload keys; the
	// suggestion_id detail lives in the EVOLVER_VERDICT captain's-log row.
	// The blocking flag likewise reaches only the (unported) hook command.
	writeEvent(ws, "self_improvement_verdict", reason, reason)
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
	raw, err := json.Marshal(entry)
	if err != nil {
		return
	}
	_ = record.AppendRawLine(filepath.Join(dir, "events.jsonl"), raw)
}
