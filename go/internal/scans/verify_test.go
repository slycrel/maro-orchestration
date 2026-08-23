package scans

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func TestClassifyCadenceVerdictTable(t *testing.T) {
	cases := []struct {
		name              string
		nBefore, nAfter   int
		srBefore, srAfter float64
		want              string
	}{
		{"confirmed: rate fell past threshold", 10, 10, 0.5, 0.3, "confirmed"},
		{"degraded: rate rose past threshold", 10, 10, 0.2, 0.4, "degraded"},
		{"inconclusive: flat", 10, 10, 0.3, 0.31, "inconclusive"},
		{"inconclusive: thin after-window", 10, 4, 0.5, 0.0, "inconclusive"},
		{"inconclusive: thin baseline", 2, 10, 0.5, 0.0, "inconclusive"},
		{"inconclusive: NaN rate", 10, 10, math.NaN(), 0.1, "inconclusive"},
	}
	for _, c := range cases {
		got := classifyCadenceVerdict(c.nBefore, c.nAfter, c.srBefore, c.srAfter, 5, 3, 0.05)
		if got != c.want {
			t.Errorf("%s: got %s want %s", c.name, got, c.want)
		}
	}
	// Boundary inclusivity (<=/>=), pinned with exactly-representable
	// eighths so float noise can't blur the edge (0.30-0.25 misses -0.05
	// by 3e-17 in BOTH runtimes — that laxness is shared, not a bug).
	if got := classifyCadenceVerdict(10, 10, 0.5, 0.375, 5, 3, 0.125); got != "confirmed" {
		t.Errorf("exact -threshold must confirm, got %s", got)
	}
	if got := classifyCadenceVerdict(10, 10, 0.375, 0.5, 5, 3, 0.125); got != "degraded" {
		t.Errorf("exact +threshold must degrade, got %s", got)
	}
}

// seedOutcomes writes n outcomes on each side of applyAt: stuckBefore of the
// before-window rows stuck, stuckAfter of the after rows stuck.
func seedOutcomes(t *testing.T, ws string, applyAt time.Time, n, stuckBefore, stuckAfter int) {
	t.Helper()
	var rows []map[string]any
	for i := 0; i < n; i++ {
		status := "done"
		if i < stuckBefore {
			status = "stuck"
		}
		rows = append(rows, map[string]any{
			"status":      status,
			"recorded_at": applyAt.Add(-time.Duration(n-i) * time.Minute).UTC().Format("2006-01-02T15:04:05+00:00"),
		})
	}
	for i := 0; i < n; i++ {
		status := "done"
		if i < stuckAfter {
			status = "stuck"
		}
		rows = append(rows, map[string]any{
			"status":      status,
			"recorded_at": applyAt.Add(time.Duration(i+1) * time.Minute).UTC().Format("2006-01-02T15:04:05+00:00"),
		})
	}
	writeJSONL(t, memPath(ws, "outcomes.jsonl"), rows)
}

func seedSuggestion(t *testing.T, ws string, extra map[string]any) {
	t.Helper()
	row := map[string]any{
		"suggestion_id": "v1", "category": "prompt_tweak", "target": "all",
		"suggestion": "test change", "failure_pattern": "x", "confidence": 0.9,
		"applied": true, "applied_at": "2026-08-20T12:00:00+00:00",
	}
	for k, v := range extra {
		row[k] = v
	}
	writeJSONL(t, memPath(ws, "suggestions.jsonl"), []map[string]any{row})
}

func loadSuggestionRow(t *testing.T, ws, id string) map[string]any {
	t.Helper()
	for _, r := range readJSONLTail(memPath(ws, "suggestions.jsonl"), 0) {
		if r["suggestion_id"] == id {
			return r
		}
	}
	t.Fatalf("suggestion %s not found", id)
	return nil
}

var applyTime = time.Date(2026, 8, 20, 12, 0, 0, 0, time.UTC)

func TestVerifyConfirmedStampsAndRecordsCalibration(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, nil)
	// stuck 8/10 before → 1/10 after: fell well past the threshold.
	seedOutcomes(t, ws, applyTime, 10, 8, 1)
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{})
	if sum.Confirmed != 1 || sum.Candidates != 1 {
		t.Fatalf("summary: %+v", sum)
	}
	row := loadSuggestionRow(t, ws, "v1")
	if row["verify_verdict"] != "confirmed" || row["verified_at"] == "" {
		t.Fatalf("terminal stamp missing: %+v", row)
	}
	if row["applied"] != true {
		t.Fatalf("confirm must not touch applied: %+v", row)
	}
	// Positive calibration outcome recorded.
	outs := readJSONLTail(suggestionOutcomesPath(ws), 0)
	if len(outs) != 1 || outs[0]["verified"] != true {
		t.Fatalf("calibration outcome: %+v", outs)
	}
	// EVOLVER_VERDICT captain's-log row landed.
	events := readJSONLTail(memPath(ws, "captains_log.jsonl"), 0)
	found := false
	for _, e := range events {
		if e["event_type"] == "EVOLVER_VERDICT" {
			found = true
			ctx, _ := e["context"].(map[string]any)
			if ctx["verdict"] != "confirmed" || ctx["metric"] != "stuck_rate" {
				t.Fatalf("verdict event context: %+v", ctx)
			}
		}
	}
	if !found {
		t.Fatal("EVOLVER_VERDICT event missing")
	}
}

func TestVerifyDegradedAutoAppliedRevertsBehaviorally(t *testing.T) {
	ws := t.TempDir()
	// An auto-applied new_guardrail with a live constraint row + audit trail:
	// the revert can behaviorally undo it.
	seedSuggestion(t, ws, map[string]any{"category": "new_guardrail"})
	writeJSONL(t, memPath(ws, "change_log.jsonl"), []map[string]any{
		{"suggestion_id": "v1", "category": "new_guardrail", "target": "all",
			"suggestion_text": "test change"},
	})
	writeJSONL(t, memPath(ws, "dynamic-constraints.jsonl"), []map[string]any{
		{"source": "v1", "pattern": "test change"},
	})
	// stuck 1/10 → 8/10: rose hard.
	seedOutcomes(t, ws, applyTime, 10, 1, 8)
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{})
	if sum.Reverted != 1 || sum.RevertFailed != 0 {
		t.Fatalf("summary: %+v", sum)
	}
	row := loadSuggestionRow(t, ws, "v1")
	if row["verify_verdict"] != "degraded" || row["applied"] != false || row["status"] != "reverted" {
		t.Fatalf("degraded auto-applied row: %+v", row)
	}
	// The constraint row is gone (behavioral undo, not bookkeeping).
	if rows := readJSONLTail(memPath(ws, "dynamic-constraints.jsonl"), 0); len(rows) != 0 {
		t.Fatalf("constraint must be removed: %+v", rows)
	}
	// Negative calibration + a durable events.jsonl escalation row.
	outs := readJSONLTail(suggestionOutcomesPath(ws), 0)
	if len(outs) != 1 || outs[0]["verified"] != false {
		t.Fatalf("calibration outcome: %+v", outs)
	}
	evs := readJSONLTail(memPath(ws, "events.jsonl"), 0)
	foundNotify := false
	for _, e := range evs {
		if e["event_type"] == "self_improvement_verdict" {
			foundNotify = true
			detail, _ := e["detail"].(string)
			if !strings.Contains(detail, "Auto-reverted") {
				t.Fatalf("notify detail: %q", detail)
			}
		}
	}
	if !foundNotify {
		t.Fatal("self_improvement_verdict event missing")
	}
}

func TestVerifyDegradedHumanAppliedNeverReverted(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, map[string]any{"applied_manually": true})
	seedOutcomes(t, ws, applyTime, 10, 1, 8)
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{})
	if sum.ReviewQueued != 1 || sum.Reverted != 0 {
		t.Fatalf("summary: %+v", sum)
	}
	row := loadSuggestionRow(t, ws, "v1")
	if row["verify_verdict"] != "degraded_needs_review" {
		t.Fatalf("row: %+v", row)
	}
	if row["applied"] != true {
		t.Fatalf("authority asymmetry violated — human-applied row was reverted: %+v", row)
	}
}

func TestVerifyDegradedRevertFailedIsTerminalAndLoud(t *testing.T) {
	ws := t.TempDir()
	// prompt_tweak: lesson_add is append-only — revert is bookkeeping-only,
	// Behavioral=false → degraded_revert_failed, terminal, BLOCKING notify.
	seedSuggestion(t, ws, nil)
	writeJSONL(t, memPath(ws, "change_log.jsonl"), []map[string]any{
		{"suggestion_id": "v1", "category": "prompt_tweak", "target": "all",
			"suggestion_text": "test change"},
	})
	seedOutcomes(t, ws, applyTime, 10, 1, 8)
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{})
	if sum.RevertFailed != 1 || sum.Reverted != 0 {
		t.Fatalf("summary: %+v", sum)
	}
	row := loadSuggestionRow(t, ws, "v1")
	if row["verify_verdict"] != "degraded_revert_failed" || row["verified_at"] == "" {
		t.Fatalf("row must be terminal (no per-cadence retry of an impossible revert): %+v", row)
	}
	evs := readJSONLTail(memPath(ws, "events.jsonl"), 0)
	loud := false
	for _, e := range evs {
		if d, _ := e["detail"].(string); strings.Contains(d, "could NOT be auto-reverted") {
			loud = true
		}
	}
	if !loud {
		t.Fatal("revert-failed must escalate loudly (still-live change)")
	}
}

func TestVerifyInconclusiveExtendsThenParks(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, nil)
	// Flat rates: 3/10 both sides → inconclusive.
	seedOutcomes(t, ws, applyTime, 10, 3, 3)
	rec := record.New(ws)
	cfg := map[string]any{}

	for i := 1; i <= 2; i++ {
		sum := VerifyAppliedSuggestions(ws, rec, cfg, "run-t", VerifyOptions{})
		if sum.Pending != 1 {
			t.Fatalf("pass %d summary: %+v", i, sum)
		}
		row := loadSuggestionRow(t, ws, "v1")
		if int(row["verify_extensions"].(float64)) != i {
			t.Fatalf("pass %d extensions: %+v", i, row)
		}
		if v, _ := row["verified_at"].(string); v != "" {
			t.Fatalf("interim extension must not stamp terminal: %+v", row)
		}
	}
	// Third pass reaches max_extensions=3 → parked unverifiable, terminal.
	sum := VerifyAppliedSuggestions(ws, rec, cfg, "run-t", VerifyOptions{})
	if sum.Unverifiable != 1 {
		t.Fatalf("park summary: %+v", sum)
	}
	row := loadSuggestionRow(t, ws, "v1")
	if row["verify_verdict"] != "unverifiable" || row["verified_at"] == "" {
		t.Fatalf("parked row: %+v", row)
	}
	// A terminal row is no longer a candidate.
	sum = VerifyAppliedSuggestions(ws, rec, cfg, "run-t", VerifyOptions{})
	if sum.Candidates != 0 {
		t.Fatalf("terminal row re-examined: %+v", sum)
	}
}

func TestVerifyClassSignalPathUsesDiagnosisWindows(t *testing.T) {
	ws := t.TempDir()
	// Graduation-shaped row: expected_signal targets token_explosion.
	seedSuggestion(t, ws, map[string]any{
		"expected_signal": []any{map[string]any{
			"metric": "failure_class_rate", "class": "token_explosion", "direction": "down"}},
	})
	// Global stuck-rate is FLAT (would park inconclusive)…
	seedOutcomes(t, ws, applyTime, 10, 3, 3)
	// …but the class rate fell hard: 8/10 before → 0/10 after.
	var diags []map[string]any
	for i := 0; i < 10; i++ {
		fc := "token_explosion"
		if i >= 8 {
			fc = "other_class"
		}
		diags = append(diags, map[string]any{
			"failure_class": fc,
			"recorded_at":   applyTime.Add(-time.Duration(10-i) * time.Minute).UTC().Format("2006-01-02T15:04:05+00:00"),
		})
	}
	for i := 0; i < 10; i++ {
		diags = append(diags, map[string]any{
			"failure_class": "other_class",
			"recorded_at":   applyTime.Add(time.Duration(i+1) * time.Minute).UTC().Format("2006-01-02T15:04:05+00:00"),
		})
	}
	writeJSONL(t, memPath(ws, "diagnoses.jsonl"), diags)
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{})
	if sum.Confirmed != 1 {
		t.Fatalf("class-signal verdict: %+v", sum)
	}
	// The verdict event names the class metric, not the fallback.
	found := false
	for _, e := range readJSONLTail(memPath(ws, "captains_log.jsonl"), 0) {
		if e["event_type"] == "EVOLVER_VERDICT" {
			ctx, _ := e["context"].(map[string]any)
			if ctx["metric"] == "failure_class_rate:token_explosion" {
				found = true
			}
		}
	}
	if !found {
		t.Fatal("class metric label missing from verdict event")
	}
}

func TestVerifyClassSignalJoinsEventsLogForUndatedDiagnoses(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, map[string]any{
		"expected_signal": []any{map[string]any{
			"metric": "failure_class_rate", "class": "token_explosion", "direction": "down"}},
	})
	seedOutcomes(t, ws, applyTime, 10, 3, 3)
	// Diagnoses have NO recorded_at — only loop_ids that join to events.jsonl
	// (the pre-V3 historical ledger shape; events.jsonl is Python-written
	// shared data).
	var diags, events []map[string]any
	for i := 0; i < 10; i++ {
		fc := "token_explosion"
		if i >= 8 {
			fc = "other_class"
		}
		lid := fmt.Sprintf("loopb%d", i)
		diags = append(diags, map[string]any{"failure_class": fc, "loop_id": lid})
		events = append(events, map[string]any{
			"loop_id": lid,
			"ts":      applyTime.Add(-time.Duration(10-i) * time.Minute).UTC().Format("2006-01-02T15:04:05+00:00"),
		})
	}
	for i := 0; i < 10; i++ {
		lid := fmt.Sprintf("loopa%d", i)
		diags = append(diags, map[string]any{"failure_class": "other_class", "loop_id": lid})
		events = append(events, map[string]any{
			"loop_id": lid,
			"ts":      applyTime.Add(time.Duration(i+1) * time.Minute).UTC().Format("2006-01-02T15:04:05+00:00"),
		})
	}
	writeJSONL(t, memPath(ws, "diagnoses.jsonl"), diags)
	writeJSONL(t, memPath(ws, "events.jsonl"), events)
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{})
	if sum.Confirmed != 1 {
		t.Fatalf("events-log join must date the historical ledger: %+v", sum)
	}
}

func TestVerifyDryRunWritesNothing(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, nil)
	seedOutcomes(t, ws, applyTime, 10, 8, 1)
	before, _ := os.ReadFile(memPath(ws, "suggestions.jsonl"))
	rec := record.New(ws)

	sum := VerifyAppliedSuggestions(ws, rec, map[string]any{}, "run-t", VerifyOptions{DryRun: true})
	if sum.Confirmed != 1 {
		t.Fatalf("dry-run must still report: %+v", sum)
	}
	after, _ := os.ReadFile(memPath(ws, "suggestions.jsonl"))
	if string(before) != string(after) {
		t.Fatal("dry-run mutated the suggestions store")
	}
	if _, err := os.Stat(suggestionOutcomesPath(ws)); !os.IsNotExist(err) {
		t.Fatal("dry-run wrote calibration outcomes")
	}
}

func TestVerifyDisabledByConfig(t *testing.T) {
	ws := t.TempDir()
	cfg := map[string]any{"evolver": map[string]any{"verify_cadence_verdicts": false}}
	sum := VerifyAppliedSuggestions(ws, nil, cfg, "run-t", VerifyOptions{})
	if sum.Enabled || sum.Skipped != "disabled" {
		t.Fatalf("killswitch: %+v", sum)
	}
}

func TestVerifyLegacyRowWithoutStampIsSkippedNotParked(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, map[string]any{"applied_at": ""})
	seedOutcomes(t, ws, applyTime, 10, 8, 1)
	sum := VerifyAppliedSuggestions(ws, record.New(ws), map[string]any{}, "run-t", VerifyOptions{})
	if sum.SkippedNoStamp != 1 || sum.Confirmed != 0 || sum.Unverifiable != 0 {
		t.Fatalf("summary: %+v", sum)
	}
	row := loadSuggestionRow(t, ws, "v1")
	if v, _ := row["verified_at"].(string); v != "" {
		t.Fatalf("unstampable row must be left alone: %+v", row)
	}
}

// StampVerification unit coverage (the store half this lifecycle rides).
func TestStampVerificationKeyedMerge(t *testing.T) {
	ws := t.TempDir()
	tainted := `{"suggestion_id": "broken"` // unparseable — must survive verbatim
	rows := []string{
		`{"suggestion_id":"other","applied":true}`,
		tainted,
		`{"suggestion_id":"v1","applied":true,"category":"prompt_tweak"}`,
	}
	writeJSONL(t, memPath(ws, "suggestions.jsonl"), nil)
	if err := os.WriteFile(memPath(ws, "suggestions.jsonl"),
		[]byte(strings.Join(rows, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	now := "2026-08-22T00:00:00+00:00"
	verdict := "confirmed"
	if !evolver.StampVerification(ws, "v1", evolver.VerificationStamp{
		Verdict: &verdict, VerifiedAt: &now}) {
		t.Fatal("row not found")
	}
	raw, _ := os.ReadFile(memPath(ws, "suggestions.jsonl"))
	text := string(raw)
	if !strings.Contains(text, tainted) {
		t.Fatal("byte-tainted line must be re-emitted verbatim")
	}
	var stamped map[string]any
	for _, line := range strings.Split(text, "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) == nil && m["suggestion_id"] == "v1" {
			stamped = m
		}
	}
	if stamped["verify_verdict"] != "confirmed" || stamped["verified_at"] != now {
		t.Fatalf("stamp: %+v", stamped)
	}
	if _, has := stamped["verify_extensions"]; has {
		t.Fatal("nil Extensions must leave the field absent")
	}
	if !evolver.StampVerification(ws, "other", evolver.VerificationStamp{}) {
		t.Fatal("sibling row lost in merge")
	}
	if evolver.StampVerification(ws, "missing", evolver.VerificationStamp{}) {
		t.Fatal("missing id must report not-found")
	}
}
