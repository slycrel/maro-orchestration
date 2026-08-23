package scans

import (
	"fmt"
	"math"
	"strings"
	"testing"
)

func TestVerifyCountsTrustFiltering(t *testing.T) {
	outcomes := []map[string]any{
		// Counted, failing: stuck.
		{"status": "stuck"},
		// Counted, failing: full-trust judged-false.
		{"status": "done", "goal_achieved": false, "goal_verdict_confidence": 0.9},
		// Counted, passing: full-trust judged-true.
		{"status": "done", "goal_achieved": true},
		// Counted, passing: neutral (unjudged done — absence ≠ failure).
		{"status": "done"},
		// Dropped BOTH tallies: directional (explicit low confidence).
		{"status": "done", "goal_achieved": false, "goal_verdict_confidence": 0.3},
		// Dropped BOTH tallies: excluded (verifier's own failure).
		{"status": "stuck", "goal_verdict_source": "closure_unverifiable"},
		// Counted, failing: malformed goal_achieved reads judged-false
		// (hardened twin semantics, backport-candidate #9).
		{"status": "done", "goal_achieved": "yes"},
	}
	counted, failing := verifyCounts(outcomes)
	if counted != 5 || failing != 3 {
		t.Fatalf("counted=%d failing=%d (want 5/3)", counted, failing)
	}
}

func TestScanEvolverImpactVerdicts(t *testing.T) {
	ws := t.TempDir()
	applyAt := "2026-08-20T12:00:00+00:00"
	writeJSONL(t, memPath(ws, "suggestions.jsonl"), []map[string]any{
		{"suggestion_id": "imp1", "category": "prompt_tweak", "target": "all",
			"suggestion": "x", "confidence": 0.9, "applied": true,
			"applied_at": applyAt},
	})
	// Before window (12h back): 4 outcomes, 3 stuck (rate .75).
	// After window: 4 outcomes, 0 stuck (rate 0) → improved.
	var rows []map[string]any
	for i := 0; i < 4; i++ {
		status := "stuck"
		if i == 3 {
			status = "done"
		}
		rows = append(rows, map[string]any{
			"loop_id": fmt.Sprintf("b%d", i), "goal": "g", "status": status,
			"recorded_at": fmt.Sprintf("2026-08-20T0%d:00:00+00:00", i+1),
		})
	}
	for i := 0; i < 4; i++ {
		rows = append(rows, map[string]any{
			"loop_id": fmt.Sprintf("a%d", i), "goal": "g", "status": "done",
			"recorded_at": fmt.Sprintf("2026-08-20T1%d:00:00+00:00", i+3),
		})
	}
	writeJSONL(t, memPath(ws, "outcomes.jsonl"), rows)

	records := ScanEvolverImpact(ws, ImpactOptions{})
	if len(records) != 1 {
		t.Fatalf("want 1 record, got %d", len(records))
	}
	r := records[0]
	if r.Verdict != "improved" {
		t.Fatalf("verdict: %+v", r)
	}
	if r.OutcomesBefore != 4 || r.StuckBefore != 3 || r.OutcomesAfter != 4 || r.StuckAfter != 0 {
		t.Fatalf("windows: %+v", r)
	}
	if math.Abs(r.Delta-(-0.75)) > 1e-9 {
		t.Fatalf("delta: %v", r.Delta)
	}

	out := FormatImpactSummary(records)
	if !strings.Contains(out, "improved=1") || !strings.Contains(out, "stuck 75%→0%") {
		t.Fatalf("summary: %q", out)
	}
}

func TestScanEvolverImpactInsufficientData(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, memPath(ws, "suggestions.jsonl"), []map[string]any{
		{"suggestion_id": "imp1", "category": "observation", "target": "all",
			"suggestion": "x", "applied": true,
			"applied_at": "2026-08-20T12:00:00+00:00"},
	})
	// Only 1 outcome per window — below the per-window minimum of 3: a
	// 1-sample baseline must never verdict.
	writeJSONL(t, memPath(ws, "outcomes.jsonl"), []map[string]any{
		{"status": "stuck", "recorded_at": "2026-08-20T11:00:00+00:00"},
		{"status": "done", "recorded_at": "2026-08-20T13:00:00+00:00"},
	})
	records := ScanEvolverImpact(ws, ImpactOptions{})
	if len(records) != 1 || records[0].Verdict != "insufficient_data" {
		t.Fatalf("records: %+v", records)
	}
	if !math.IsNaN(records[0].Delta) {
		t.Fatalf("insufficient_data delta must be NaN: %v", records[0].Delta)
	}
}

func TestScanEvolverImpactCaptainsLogFallback(t *testing.T) {
	ws := t.TempDir()
	// No applied_at-stamped suggestion — only a historical captain's-log
	// EVOLVER_APPLIED event. The fallback must surface it.
	writeJSONL(t, memPath(ws, "captains_log.jsonl"), []map[string]any{
		{"event_type": "EVOLVER_APPLIED", "subject": "legacy1",
			"timestamp": "2026-08-20T12:00:00+00:00",
			"context":   map[string]any{"suggestion_id": "legacy1", "category": "prompt_tweak"}},
	})
	writeJSONL(t, memPath(ws, "outcomes.jsonl"), []map[string]any{
		{"status": "done", "recorded_at": "2026-08-20T11:00:00+00:00"},
	})
	records := ScanEvolverImpact(ws, ImpactOptions{})
	if len(records) != 1 || records[0].SuggestionID != "legacy1" {
		t.Fatalf("captains-log fallback: %+v", records)
	}
}

// A NUMERIC STRING confidence must classify like Python float() parses it:
// "0.5" < floor → directional → dropped from both tallies (r1 parity F3 —
// Go read it as unparseable and counted the row full-trust).
func TestVerifyCountsStringConfidenceDirectional(t *testing.T) {
	outcomes := []map[string]any{
		{"status": "done", "goal_achieved": false, "goal_verdict_confidence": "0.5"},
		// Non-numeric string keeps the TypeError/ValueError pass → full.
		{"status": "done", "goal_achieved": false, "goal_verdict_confidence": "high"},
	}
	counted, failing := verifyCounts(outcomes)
	if counted != 1 || failing != 1 {
		t.Fatalf("counted=%d failing=%d (want 1/1)", counted, failing)
	}
}

// Hex-float strings must classify like Python float() (which raises →
// TypeError/ValueError pass → FULL); Go ParseFloat would have read
// "0x1p-2"=0.25 → directional → row dropped (r2 review LOW-1).
func TestVerifyCountsHexConfidenceStaysFull(t *testing.T) {
	outcomes := []map[string]any{
		{"status": "done", "goal_achieved": false, "goal_verdict_confidence": "0x1p-2"},
	}
	counted, failing := verifyCounts(outcomes)
	if counted != 1 || failing != 1 {
		t.Fatalf("counted=%d failing=%d (want 1/1 — hex is unparseable, full trust)", counted, failing)
	}
}
