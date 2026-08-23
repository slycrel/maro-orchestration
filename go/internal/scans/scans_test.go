package scans

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/evolver"
)

func writeJSONL(t *testing.T, path string, rows []map[string]any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for _, r := range rows {
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		b.Write(raw)
		b.WriteByte('\n')
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}

func memPath(ws, name string) string { return filepath.Join(ws, "memory", name) }

// --- calibration ---

func TestScanCalibrationLogFindsOverrideAndLowConfidence(t *testing.T) {
	ws := t.TempDir()
	var rows []map[string]any
	// Class "escalate": 6 entries, 3 overridden (rate 0.5 > 0.4), confident.
	for i := 0; i < 6; i++ {
		final := "pause"
		if i < 3 {
			final = "continue" // raw stays "pause" → override
		}
		rows = append(rows, map[string]any{
			"decision_class": "escalate", "confidence": 8,
			"action_raw": "pause", "action_final": final,
		})
	}
	// Class "resume": 5 entries, no overrides, mean confidence 4 (< 6).
	for i := 0; i < 5; i++ {
		rows = append(rows, map[string]any{
			"decision_class": "resume", "confidence": 4,
			"action_raw": "go", "action_final": "go",
		})
	}
	// Class "thin": below min_entries — never reported.
	rows = append(rows, map[string]any{
		"decision_class": "thin", "confidence": 1,
		"action_raw": "a", "action_final": "b",
	})
	writeJSONL(t, memPath(ws, "calibration.jsonl"), rows)

	findings := ScanCalibrationLog(ws, CalibrationOptions{})
	if len(findings) != 2 {
		t.Fatalf("want 2 findings, got %d: %+v", len(findings), findings)
	}
	byClass := map[string]CalibrationFinding{}
	for _, f := range findings {
		byClass[f.DecisionClass] = f
	}
	esc := byClass["escalate"]
	if esc.OverrideCount != 3 || esc.OverrideRate != 0.5 {
		t.Fatalf("escalate override stats wrong: %+v", esc)
	}
	if !strings.Contains(esc.Suggestion, "override rate 50%") {
		t.Fatalf("escalate suggestion prose: %q", esc.Suggestion)
	}
	res := byClass["resume"]
	if !strings.Contains(res.Suggestion, "mean confidence 4.0/10") {
		t.Fatalf("resume suggestion prose: %q", res.Suggestion)
	}
}

func TestScanCalibrationLogMissingFileIsEmpty(t *testing.T) {
	if got := ScanCalibrationLog(t.TempDir(), CalibrationOptions{}); got != nil {
		t.Fatalf("missing file must scan empty, got %+v", got)
	}
}

// --- step costs ---

func TestScanStepCostsFlagsExpensiveType(t *testing.T) {
	ws := t.TempDir()
	var rows []map[string]any
	// 4 cheap types keep the lower median small; "research" burns 10x.
	for _, st := range []string{"a", "b", "c", "d"} {
		for i := 0; i < 2; i++ {
			rows = append(rows, map[string]any{
				"step_type": st, "total_tokens": 1000, "cost_usd": 0.001})
		}
	}
	for i := 0; i < 3; i++ {
		rows = append(rows, map[string]any{
			"step_type": "research", "total_tokens": 12000, "cost_usd": 0.02})
	}
	writeJSONL(t, memPath(ws, "step-costs.jsonl"), rows)

	got := ScanStepCosts(ws, 5)
	if len(got) != 1 {
		t.Fatalf("want 1 suggestion, got %d: %+v", len(got), got)
	}
	s := got[0]
	if s.SuggestionID != "cost-research" || s.Category != "cost_optimization" {
		t.Fatalf("id/category: %+v", s)
	}
	if !strings.Contains(s.Suggestion, "averages 12,000 tokens across 3 steps") {
		t.Fatalf("prose (comma format is shared-ledger parity): %q", s.Suggestion)
	}
	if s.FailurePattern != "high_burn_step: research avg=12000tok" {
		t.Fatalf("failure_pattern: %q", s.FailurePattern)
	}
}

func TestScanStepCostsBelowMinEntriesIsEmpty(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, memPath(ws, "step-costs.jsonl"), []map[string]any{
		{"step_type": "x", "total_tokens": 999999, "cost_usd": 9.0},
	})
	if got := ScanStepCosts(ws, 5); got != nil {
		t.Fatalf("below min entries must be empty, got %+v", got)
	}
}

// --- quality drift ---

func TestScanQualityDriftFlagsConsecutiveDrop(t *testing.T) {
	ws := t.TempDir()
	// Rolling history: three good cycles then two bad ones. Baseline is the
	// mean of ALL prior values; the consecutive counter walks newest-first.
	prior := []map[string]any{
		{"ts": "2026-08-20T00:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-20T01:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-20T02:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-21T00:00:00+00:00", "success_rate": 0.2, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-21T01:00:00+00:00", "success_rate": 0.2, "avg_cost_usd": 0.01, "outcomes_count": 10},
	}
	writeJSONL(t, baselinesPath(ws), prior)

	// Current cycle: 1/10 done — far below the ~0.62 baseline.
	var outcomes []map[string]any
	for i := 0; i < 10; i++ {
		status := "stuck"
		if i == 0 {
			status = "done"
		}
		outcomes = append(outcomes, map[string]any{"status": status})
	}
	findings := ScanQualityDrift(ws, outcomes, 15.0, 3)
	if len(findings) != 1 {
		t.Fatalf("want 1 drift finding, got %d: %+v", len(findings), findings)
	}
	f := findings[0]
	if f.Metric != "success_rate" || f.ConsecutiveDrops < 3 {
		t.Fatalf("finding: %+v", f)
	}
	if !strings.Contains(f.Suggestion, "consecutive cycles") {
		t.Fatalf("prose: %q", f.Suggestion)
	}

	// The scan must have appended this cycle's snapshot (the ledger is how
	// the NEXT cycle sees this one).
	after := loadBaselines(ws, 50)
	if len(after) != len(prior)+1 {
		t.Fatalf("snapshot not persisted: %d rows", len(after))
	}
}

// A single bad cycle after a healthy history must stay quiet — the whole
// point of the N-consecutive requirement is that one bad interval is noise,
// not drift.
func TestScanQualityDriftSingleBreachStaysQuiet(t *testing.T) {
	ws := t.TempDir()
	prior := []map[string]any{
		{"ts": "2026-08-20T00:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-20T01:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-20T02:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
		{"ts": "2026-08-20T03:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.01, "outcomes_count": 10},
	}
	writeJSONL(t, baselinesPath(ws), prior)
	// Current cycle breaches; every prior cycle is healthy → consecutive=1.
	var outcomes []map[string]any
	for i := 0; i < 10; i++ {
		status := "stuck"
		if i == 0 {
			status = "done"
		}
		outcomes = append(outcomes, map[string]any{"status": status})
	}
	if got := ScanQualityDrift(ws, outcomes, 15.0, 3); got != nil {
		t.Fatalf("one breaching cycle must not alert, got %+v", got)
	}
}

func TestScanQualityDriftNeedsHistory(t *testing.T) {
	ws := t.TempDir()
	outcomes := []map[string]any{{"status": "stuck"}}
	if got := ScanQualityDrift(ws, outcomes, 15.0, 3); got != nil {
		t.Fatalf("no history must yield no findings, got %+v", got)
	}
	// But the snapshot still lands (baseline accrual starts immediately).
	if rows := loadBaselines(ws, 10); len(rows) != 1 {
		t.Fatalf("snapshot must persist even with no history: %d", len(rows))
	}
}

// --- canon candidates ---

func canonLesson(id, text string, extra map[string]any) map[string]any {
	row := map[string]any{
		"lesson_id": id, "task_type": "research", "outcome": "done",
		"lesson": text, "confidence": 0.8, "tier": "long", "score": 2.0,
		"last_reinforced": "2026-08-20", "recorded_at": "2026-07-01T00:00:00+00:00",
	}
	for k, v := range extra {
		row[k] = v
	}
	return row
}

func canonHits(lid string, n int, taskTypes []string) []map[string]any {
	var rows []map[string]any
	for i := 0; i < n; i++ {
		rows = append(rows, map[string]any{
			"lesson_id": lid, "tier": "long",
			"task_type": taskTypes[i%len(taskTypes)],
		})
	}
	return rows
}

func TestScanCanonCandidatesSurfacesAndExcludes(t *testing.T) {
	ws := t.TempDir()
	var stats []map[string]any
	stats = append(stats, canonHits("good", 12, []string{"research", "build", "ops"})...)
	stats = append(stats, canonHits("quarantined", 12, []string{"research", "build", "ops"})...)
	stats = append(stats, canonHits("demoted", 12, []string{"research", "build", "ops"})...)
	stats = append(stats, canonHits("inert", 12, []string{"research", "build", "ops"})...)
	stats = append(stats, canonHits("already", 12, []string{"research", "build", "ops"})...)
	stats = append(stats, canonHits("thin", 2, []string{"research"})...)
	writeJSONL(t, memPath(ws, "canon_stats.jsonl"), stats)

	writeJSONL(t, filepath.Join(ws, "memory", "long", "lessons.jsonl"), []map[string]any{
		canonLesson("good", "always cite sources", nil),
		canonLesson("quarantined", "prompt-derived", map[string]any{"minted_from": "prompt"}),
		canonLesson("demoted", "measured harmful", map[string]any{
			"delta_evidence": map[string]any{"route": "effect-demote"}}),
		canonLesson("inert", "measured redundant", map[string]any{
			"delta_evidence": map[string]any{"route": "effect-inert"}}),
		canonLesson("already", "promoted", map[string]any{
			"canon": map[string]any{"promoted_at": "2026-08-01"}}),
	})

	got := ScanCanonCandidates(ws, 10, 3)
	if len(got) != 1 {
		t.Fatalf("want exactly the clean candidate, got %d: %+v", len(got), got)
	}
	s := got[0]
	if s.Category != "crystallization" {
		t.Fatalf("category: %+v", s)
	}
	if !strings.Contains(s.Suggestion, "PROMOTE TO IDENTITY (Stage 3): 'always cite sources'") ||
		!strings.Contains(s.Suggestion, "canon-promote good") {
		t.Fatalf("prose: %q", s.Suggestion)
	}
	if s.FailurePattern != "lesson_id=good times_applied=12 task_types=3" {
		t.Fatalf("failure_pattern: %q", s.FailurePattern)
	}
	// confidence = min(.95, .5 + 12*.03 + 3*.05) = .95 cap? .5+.36+.15=1.01 → .95
	if s.Confidence != 0.95 {
		t.Fatalf("confidence cap: %v", s.Confidence)
	}
}

// --- suggestion-outcome calibration ---

func TestScanSuggestionOutcomesFlagsOverconfidentCategory(t *testing.T) {
	ws := t.TempDir()
	var rows []map[string]any
	// prompt_tweak: self-reported 0.9, empirical 1/4 = 0.25 < 0.6*0.9.
	for i := 0; i < 4; i++ {
		rows = append(rows, map[string]any{
			"category": "prompt_tweak", "confidence": 0.9, "verified": i == 0})
	}
	// observation: 3/3 pass — calibrated, never flagged.
	for i := 0; i < 3; i++ {
		rows = append(rows, map[string]any{
			"category": "observation", "confidence": 0.8, "verified": true})
	}
	writeJSONL(t, suggestionOutcomesPath(ws), rows)

	got := ScanSuggestionOutcomes(ws, 3, 0.6)
	if len(got) != 1 {
		t.Fatalf("want 1, got %d: %+v", len(got), got)
	}
	s := got[0]
	if s.Target != "prompt_tweak" || s.PlaybookKey != "calibration:prompt_tweak" {
		t.Fatalf("target/playbook_key (one alarm per category): %+v", s)
	}
	if !strings.Contains(s.Suggestion, "self-reported 0.90 against an empirical pass rate of 0.25") {
		t.Fatalf("prose: %q", s.Suggestion)
	}
	if s.FailurePattern != "overconfident:prompt_tweak" {
		t.Fatalf("failure_pattern: %q", s.FailurePattern)
	}
}

func TestRecordSuggestionOutcomesJoinsChangeLog(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, memPath(ws, "change_log.jsonl"), []map[string]any{
		{"suggestion_id": "s1", "category": "prompt_tweak", "confidence": 0.85},
	})
	RecordSuggestionOutcomes(ws, []string{"s1", "unknown-id"}, true, "run-x")
	rows := readJSONLTail(suggestionOutcomesPath(ws), 0)
	if len(rows) != 2 {
		t.Fatalf("want 2 rows, got %d", len(rows))
	}
	if rows[0]["category"] != "prompt_tweak" || rows[0]["confidence"].(float64) != 0.85 {
		t.Fatalf("change_log join: %+v", rows[0])
	}
	if rows[1]["category"] != "unknown" || rows[1]["confidence"].(float64) != 0.5 {
		t.Fatalf("missing change_log entry must default, not drop: %+v", rows[1])
	}
	if rows[0]["verified"] != true || rows[0]["run_id"] != "run-x" {
		t.Fatalf("row fields: %+v", rows[0])
	}
}

// --- fan-out ---

func TestRunStatisticalScansWrapsFindings(t *testing.T) {
	ws := t.TempDir()
	// Calibration fixture (one finding) + suggestion-outcomes fixture.
	var cal []map[string]any
	for i := 0; i < 5; i++ {
		cal = append(cal, map[string]any{
			"decision_class": "escalate", "confidence": 3,
			"action_raw": "a", "action_final": "a"})
	}
	writeJSONL(t, memPath(ws, "calibration.jsonl"), cal)

	got := RunStatisticalScans(ws, nil, StatScanOptions{})
	if len(got) != 1 {
		t.Fatalf("want 1 wrapped suggestion, got %d: %+v", len(got), got)
	}
	s := got[0]
	if s.Category != "prompt_tweak" || s.Target != "escalation" {
		t.Fatalf("calibration wrapper: %+v", s)
	}
	if !strings.HasPrefix(s.SuggestionID, "cal-") || len(s.SuggestionID) != len("cal-")+8 {
		t.Fatalf("id shape: %q", s.SuggestionID)
	}
	if !strings.Contains(s.FailurePattern, "calibration: class='escalate'") ||
		!strings.Contains(s.FailurePattern, "mean_confidence=3.0/10 n=5") {
		t.Fatalf("failure_pattern: %q", s.FailurePattern)
	}
	if s.GeneratedAt == "" {
		t.Fatal("scanner rows must carry generated_at (shared-ledger field set)")
	}
}

func TestRunStatisticalScansSkipGates(t *testing.T) {
	ws := t.TempDir()
	var cal []map[string]any
	for i := 0; i < 5; i++ {
		cal = append(cal, map[string]any{
			"decision_class": "escalate", "confidence": 3,
			"action_raw": "a", "action_final": "a"})
	}
	writeJSONL(t, memPath(ws, "calibration.jsonl"), cal)
	got := RunStatisticalScans(ws, nil, StatScanOptions{SkipCalibration: true, SkipDrift: true})
	if len(got) != 0 {
		t.Fatalf("skip gate ignored: %+v", got)
	}
}

// Drift wrapper carries the alarm key (one alarm per metric).
func TestRunStatisticalScansDriftWrapper(t *testing.T) {
	ws := t.TempDir()
	prior := []map[string]any{}
	for i := 0; i < 5; i++ {
		prior = append(prior, map[string]any{
			"ts": "2026-08-20T00:00:00+00:00", "success_rate": 0.9, "avg_cost_usd": 0.0})
	}
	// The NEWEST two prior cycles also breach so the consecutive walk
	// (newest-first) reaches the alert count with the current cycle.
	prior[3]["success_rate"] = 0.1
	prior[4]["success_rate"] = 0.1
	writeJSONL(t, baselinesPath(ws), prior)
	outcomes := []map[string]any{{"status": "stuck"}, {"status": "stuck"}}

	got := RunStatisticalScans(ws, outcomes, StatScanOptions{
		SkipCalibration: true, SkipCosts: true, SkipCanon: true, SkipSuggestionCalibration: true})
	var drift []evolver.Suggestion
	for _, s := range got {
		if strings.HasPrefix(s.SuggestionID, "drift-") {
			drift = append(drift, s)
		}
	}
	if len(drift) != 1 {
		t.Fatalf("want 1 drift suggestion, got %d (all: %+v)", len(drift), got)
	}
	s := drift[0]
	if s.Category != "observation" || s.PlaybookKey != "drift:success_rate" {
		t.Fatalf("drift wrapper (one alarm per metric rides playbook_key): %+v", s)
	}
	if !strings.Contains(s.FailurePattern, "quality_drift: success_rate") {
		t.Fatalf("failure_pattern: %q", s.FailurePattern)
	}
}

// --- r1 fix-layer pins (2026-08-22) ---

// Python-parity string helpers: repr quoting (the text is a cross-runtime
// dedup key) and f-string value rendering in shared prose.
func TestPyReprAndPyVal(t *testing.T) {
	if got := pyRepr("escalate"); got != "'escalate'" {
		t.Fatalf("pyRepr plain: %q", got)
	}
	// Python repr switches to double quotes on an apostrophe.
	if got := pyRepr("don't_know"); got != `"don't_know"` {
		t.Fatalf("pyRepr apostrophe: %q", got)
	}
	if got := pyVal(nil); got != "None" {
		t.Fatalf("pyVal nil: %q", got)
	}
	if got := pyVal(0.0); got != "0.0" {
		t.Fatalf("pyVal whole float: %q", got)
	}
	if got := pyVal(0.75); got != "0.75" {
		t.Fatalf("pyVal: %q", got)
	}
	if got := pyVal(7); got != "7" {
		t.Fatalf("pyVal int: %q", got)
	}
}

// Exact binary ties round half-to-even like Python round() — these values
// land in shared files (r1 parity F4: 5/32 wrote 0.1563 Go / 0.1562 Py).
func TestRoundHalfEvenTieParity(t *testing.T) {
	if got := round4(5.0 / 32.0); got != 0.1562 {
		t.Fatalf("round4(5/32) = %v, want 0.1562", got)
	}
	if got := roundN(1.0/16.0, 1e3); got != 0.062 {
		t.Fatalf("round3(1/16) = %v, want 0.062", got)
	}
}

// An apostrophe-bearing decision class must produce the same suggestion
// text both runtimes write, or the content-key dedup mints duplicates.
func TestCalibrationApostropheClassParity(t *testing.T) {
	ws := t.TempDir()
	var cal []map[string]any
	for i := 0; i < 5; i++ {
		cal = append(cal, map[string]any{
			"decision_class": "don't_know", "confidence": 3,
			"action_raw": "a", "action_final": "a"})
	}
	writeJSONL(t, memPath(ws, "calibration.jsonl"), cal)
	findings := ScanCalibrationLog(ws, CalibrationOptions{})
	if len(findings) != 1 {
		t.Fatalf("findings: %+v", findings)
	}
	if !strings.Contains(findings[0].Suggestion, `"don't_know" decisions`) {
		t.Fatalf("prose must use Python repr quoting: %q", findings[0].Suggestion)
	}
}

// r4 LOW pin: the canon row's target is the lesson's task_type VERBATIM —
// Python's `.get("task_type", "general")` default is dead code (the key is
// always emitted), so an empty task_type must stay "" here too. Target
// feeds contentKey; defaulting would mint a duplicate row per runtime on
// a shared store.
func TestScanCanonCandidatesEmptyTaskTypeTargetVerbatim(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, memPath(ws, "canon_stats.jsonl"),
		canonHits("blank", 12, []string{"research", "build", "ops"}))
	writeJSONL(t, filepath.Join(ws, "memory", "long", "lessons.jsonl"),
		[]map[string]any{canonLesson("blank", "lesson text",
			map[string]any{"task_type": ""})})

	got := ScanCanonCandidates(ws, 10, 3)
	if len(got) != 1 {
		t.Fatalf("want one candidate, got %d: %+v", len(got), got)
	}
	if got[0].Target != "" {
		t.Fatalf("empty task_type must stay verbatim, got %q", got[0].Target)
	}
}
