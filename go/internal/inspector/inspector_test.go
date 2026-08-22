package inspector

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

func defaultThresholds() Thresholds {
	return Thresholds{
		BreachThreshold:    0.30,
		EscalationMinHits:  3,
		ContextChurnTokens: 10000,
		AlignmentGood:      0.7,
		AlignmentPoor:      0.4,
	}
}

func seedOutcomes(t *testing.T, ws string, lines ...string) {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	content := ""
	for _, l := range lines {
		content += l + "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "outcomes.jsonl"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestThresholdPriorityEnvOverConfigOverDefault(t *testing.T) {
	cfg := map[string]any{"inspector": map[string]any{"breach_threshold": 0.5, "escalation_min_hits": 7}}
	th := LoadThresholds(cfg)
	if th.BreachThreshold != 0.5 || th.EscalationMinHits != 7 {
		t.Fatalf("config layer not read: %+v", th)
	}
	if th.ContextChurnTokens != 10000 || th.AlignmentGood != 0.7 || th.AlignmentPoor != 0.4 {
		t.Fatalf("defaults not applied for unset keys: %+v", th)
	}
	t.Setenv("INSPECTOR_BREACH_THRESHOLD", "0.9")
	t.Setenv("INSPECTOR_ESCALATION_MIN_HITS", "2")
	th = LoadThresholds(cfg)
	if th.BreachThreshold != 0.9 || th.EscalationMinHits != 2 {
		t.Fatalf("env must beat config: %+v", th)
	}
	// A garbage env value falls through to the next layer, not to zero.
	t.Setenv("INSPECTOR_BREACH_THRESHOLD", "not-a-number")
	if th = LoadThresholds(cfg); th.BreachThreshold != 0.5 {
		t.Fatalf("bad env must fall back to config: %v", th.BreachThreshold)
	}
}

func TestDetectFrictionSignalsAllSix(t *testing.T) {
	th := defaultThresholds()
	cases := []struct {
		name    string
		outcome map[string]any
		want    string
		sev     string
	}{
		{"error_events", map[string]any{"status": "stuck", "summary": "LLM call failed: rate limit hit", "outcome_id": "o1"}, SignalErrorEvents, "high"},
		{"backtracking", map[string]any{"status": "stuck", "summary": "already tried this approach twice", "outcome_id": "o2"}, SignalBacktracking, "medium"},
		{"escalation_tone", map[string]any{"status": "stuck", "summary": "critical failure: step failed, then failed again", "outcome_id": "o3"}, SignalEscalationTone, "medium"},
		{"context_churn", map[string]any{"status": "stuck", "summary": "no progress at all", "tokens_in": float64(20000), "outcome_id": "o4"}, SignalContextChurn, "low"},
		{"platform_confusion", map[string]any{"status": "done", "summary": "operation not supported on this host", "outcome_id": "o5"}, SignalPlatformConfusion, "medium"},
		{"abandoned_tool_flow", map[string]any{"status": "stuck", "summary": "tool call left incomplete mid-way", "outcome_id": "o6"}, SignalAbandonedToolFlow, "low"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			sigs := DetectFrictionSignals(c.outcome, th)
			found := false
			for _, s := range sigs {
				if s.SignalType == c.want {
					found = true
					if s.Severity != c.sev {
						t.Fatalf("severity %s, want %s", s.Severity, c.sev)
					}
					if s.SessionID == "" {
						t.Fatal("session_id not carried")
					}
					if s.Evidence == "" {
						t.Fatal("evidence empty")
					}
				}
			}
			if !found {
				t.Fatalf("signal %s not detected in %v", c.want, sigs)
			}
		})
	}
}

// Stuck-gated detectors must NOT fire on done rows — only
// platform_confusion inspects every status (Python parity).
func TestDetectFrictionStuckGate(t *testing.T) {
	th := defaultThresholds()
	done := map[string]any{
		"status":     "done",
		"summary":    "LLM call failed early but we already tried the retry and it worked, tool call incomplete then fixed",
		"tokens_in":  float64(50000),
		"outcome_id": "ok1",
	}
	for _, s := range DetectFrictionSignals(done, th) {
		if s.SignalType != SignalPlatformConfusion {
			t.Fatalf("stuck-gated signal %s fired on a done row", s.SignalType)
		}
	}
}

// The escalation count gate: hits below EscalationMinHits stay silent;
// at the gate the signal carries the count.
func TestEscalationToneCountGate(t *testing.T) {
	th := defaultThresholds()
	two := map[string]any{"status": "stuck", "summary": "critical error then failed", "outcome_id": "e1"}
	for _, s := range DetectFrictionSignals(two, th) {
		if s.SignalType == SignalEscalationTone {
			t.Fatal("2 hits fired below min_hits=3")
		}
	}
	three := map[string]any{"status": "stuck", "summary": "critical: failed and failed", "outcome_id": "e2"}
	found := false
	for _, s := range DetectFrictionSignals(three, th) {
		if s.SignalType == SignalEscalationTone {
			found = true
			if s.Count != 3 {
				t.Fatalf("count %d, want 3", s.Count)
			}
		}
	}
	if !found {
		t.Fatal("3 hits did not fire")
	}
}

// Fail-open census fix, pinned: an adapter-less inspection displays 0.7
// but CANNOT earn "good", and the alignment-gated delight signal stays
// off (M74 target: dropping the unjudged cap).
func TestInspectSessionUnjudgedCapsAtFair(t *testing.T) {
	sq := InspectSession(context.Background(), map[string]any{
		"outcome_id": "u1", "goal": "ship the thing", "status": "done",
		"summary": "shipped cleanly",
	}, nil, defaultThresholds())
	if sq.GoalAlignmentScore != 0.7 {
		t.Fatalf("display default drifted: %v", sq.GoalAlignmentScore)
	}
	if sq.AlignmentJudged {
		t.Fatal("adapter-less inspection must be unjudged")
	}
	if sq.OverallQuality != "fair" {
		t.Fatalf("unjudged done run graded %q — the exact fail-open bug", sq.OverallQuality)
	}
	for _, d := range sq.DelightSignals {
		if d == "task_completed_successfully" {
			t.Fatal("unjudged run earned the completion delight signal")
		}
	}
}

// A judged goal-NOT-achieved run caps at "fair" no matter how aligned
// the narrative reads (M75 sibling; SF-2 verdict preference).
func TestInspectSessionJudgedNotAchievedCapsAtFair(t *testing.T) {
	fake := &llm.Fake{Script: []string{"0.95", "fine work. VERDICT: **PROCEED**"}}
	sq := InspectSession(context.Background(), map[string]any{
		"outcome_id": "j1", "goal": "do it", "status": "done",
		"summary": "narrative says perfect", "goal_achieved": false,
	}, fake, defaultThresholds())
	if !sq.AlignmentJudged || sq.GoalAlignmentScore != 0.95 {
		t.Fatalf("judge score not taken: %+v", sq)
	}
	if sq.OverallQuality != "fair" {
		t.Fatalf("judged-not-achieved graded %q, want fair", sq.OverallQuality)
	}
	for _, d := range sq.DelightSignals {
		if d == "task_completed_successfully" || d == "goal_verified_achieved" {
			t.Fatalf("not-achieved run earned delight signal %s", d)
		}
	}
}

// A deferred row still waiting on its closure verdict is not success
// evidence (outcome_policy.is_verdict_pending, ported) — caps at fair
// and suppresses the completion delight (M75 target).
func TestInspectSessionVerdictPendingCapsAtFair(t *testing.T) {
	fake := &llm.Fake{Script: []string{"0.9", "ok. VERDICT: **PROCEED**"}}
	sq := InspectSession(context.Background(), map[string]any{
		"outcome_id": "p1", "goal": "agenda goal", "status": "done",
		"summary": "loop finished", "lesson_extraction_status": "deferred",
	}, fake, defaultThresholds())
	if sq.OverallQuality != "fair" {
		t.Fatalf("verdict-pending graded %q, want fair", sq.OverallQuality)
	}
	for _, d := range sq.DelightSignals {
		if d == "task_completed_successfully" {
			t.Fatal("verdict-pending run earned the completion delight signal")
		}
	}
}

func TestInspectSessionJudgedAchievedEarnsGood(t *testing.T) {
	fake := &llm.Fake{Script: []string{"0.9", "solid. VERDICT: **PROCEED**"}}
	sq := InspectSession(context.Background(), map[string]any{
		"outcome_id": "g1", "goal": "deliver", "status": "done",
		"summary": "delivered and verified", "goal_achieved": true,
	}, fake, defaultThresholds())
	if sq.OverallQuality != "good" {
		t.Fatalf("judged-achieved 0.9 graded %q, want good", sq.OverallQuality)
	}
	wantDelight := map[string]bool{"task_completed_successfully": false, "goal_verified_achieved": false}
	for _, d := range sq.DelightSignals {
		wantDelight[d] = true
	}
	for k, seen := range wantDelight {
		if !seen {
			t.Fatalf("delight signal %s missing: %v", k, sq.DelightSignals)
		}
	}
	if sq.InspectorNotes == "" || !strings.Contains(sq.InspectorNotes, "VERDICT") {
		t.Fatalf("notes not captured: %q", sq.InspectorNotes)
	}
}

func TestInspectSessionPoorPaths(t *testing.T) {
	// Low judged alignment → poor.
	fake := &llm.Fake{Script: []string{"0.2", "off target. VERDICT: **RETRY**"}}
	sq := InspectSession(context.Background(), map[string]any{
		"outcome_id": "b1", "goal": "x", "status": "done", "summary": "wandered off",
	}, fake, defaultThresholds())
	if sq.OverallQuality != "poor" {
		t.Fatalf("0.2 judged graded %q, want poor", sq.OverallQuality)
	}
	// High friction → poor even with a high judged score.
	fake2 := &llm.Fake{Script: []string{"0.9", "errored. VERDICT: **RETRY**"}}
	sq2 := InspectSession(context.Background(), map[string]any{
		"outcome_id": "b2", "goal": "y", "status": "stuck",
		"summary": "LLM call failed: connection error", "goal_achieved": true,
	}, fake2, defaultThresholds())
	if sq2.OverallQuality != "poor" {
		t.Fatalf("high-friction session graded %q, want poor", sq2.OverallQuality)
	}
}

// Alignment judge fallback: unparseable judge reply scores 0.5, judged.
func TestAssessGoalAlignmentParseFallback(t *testing.T) {
	if got := AssessGoalAlignment(context.Background(), nil, "g", "s"); got != nil {
		t.Fatalf("nil adapter must return nil, got %v", *got)
	}
	fake := &llm.Fake{Script: []string{"certainly a nine out of ten"}}
	got := AssessGoalAlignment(context.Background(), fake, "g", "s")
	if got == nil || *got != 0.5 {
		t.Fatalf("parse failure must score 0.5, got %v", got)
	}
}

// Breach detection is a SESSION-PRESENCE fraction, not a raw count
// fraction (M73 target): one loud session with count=10 must not flag
// a fleet-wide breach.
func TestRunBreachIsSessionFractionNotCount(t *testing.T) {
	ws := t.TempDir()
	// 5 sessions; ONE has escalation tone with count 10 (10 "failed"s);
	// presence fraction 1/5 = 0.2 < 0.3 → no breach.
	loud := `{"outcome_id": "L", "status": "stuck", "goal": "a", "summary": "failed failed failed failed failed failed failed failed failed failed"}`
	quiet := `{"outcome_id": "Q%d", "status": "done", "goal": "b", "summary": "fine"}`
	seedOutcomes(t, ws,
		strings.Replace(quiet, "%d", "1", 1),
		strings.Replace(quiet, "%d", "2", 1),
		strings.Replace(quiet, "%d", "3", 1),
		strings.Replace(quiet, "%d", "4", 1),
		loud,
	)
	report := Run(context.Background(), ws, nil, 50, true, defaultThresholds())
	for _, b := range report.ThresholdBreaches {
		if b == SignalEscalationTone {
			t.Fatal("count-heavy single session flagged a fleet-wide breach")
		}
	}
	// And 2/5 sessions (0.4 > 0.3) DOES breach.
	seedOutcomes(t, ws,
		strings.Replace(quiet, "%d", "1", 1),
		strings.Replace(quiet, "%d", "2", 1),
		strings.Replace(quiet, "%d", "3", 1),
		`{"outcome_id": "S1", "status": "stuck", "goal": "c", "summary": "LLM call failed: timeout"}`,
		`{"outcome_id": "S2", "status": "stuck", "goal": "d", "summary": "api timeout again"}`,
	)
	report = Run(context.Background(), ws, nil, 50, true, defaultThresholds())
	found := false
	for _, b := range report.ThresholdBreaches {
		if b == SignalErrorEvents {
			found = true
		}
	}
	if !found {
		t.Fatalf("2/5 presence did not breach: %v", report.ThresholdBreaches)
	}
}

func TestRunAggregatesAndPersists(t *testing.T) {
	ws := t.TempDir()
	seedOutcomes(t, ws,
		`{"outcome_id": "a", "status": "done", "goal": "g1", "summary": "ok"}`,
		`{"outcome_id": "b", "status": "stuck", "goal": "g2", "summary": "LLM call failed: rate limit"}`,
		`{"outcome_id": "c", "status": "stuck", "goal": "g3", "summary": "already tried the same result"}`,
	)
	report := Run(context.Background(), ws, nil, 50, false, defaultThresholds())
	if report.InspectedSessions != 3 {
		t.Fatalf("sessions %d, want 3", report.InspectedSessions)
	}
	total := report.QualityDistribution["good"] + report.QualityDistribution["fair"] + report.QualityDistribution["poor"]
	if total != 3 {
		t.Fatalf("distribution doesn't sum: %v", report.QualityDistribution)
	}
	if len(report.TopFrictionSignals) == 0 {
		t.Fatal("top friction signals empty despite stuck rows")
	}
	// Persisted: the report row is on disk and GetLatestInspection
	// round-trips it.
	latest := GetLatestInspection(ws)
	if latest == nil || latest.RunID != report.RunID {
		t.Fatalf("report not persisted/round-tripped: %+v", latest)
	}
	if s := FrictionSummary(ws); !strings.Contains(s, report.RunID) {
		t.Fatalf("friction summary missing run id: %q", s)
	}
}

// dry run: nothing lands on disk.
func TestRunDryRunWritesNothing(t *testing.T) {
	ws := t.TempDir()
	seedOutcomes(t, ws, `{"outcome_id": "a", "status": "done", "goal": "g", "summary": "ok"}`)
	_ = Run(context.Background(), ws, nil, 50, true, defaultThresholds())
	if _, err := os.Stat(inspectionLogPath(ws)); !os.IsNotExist(err) {
		t.Fatal("dry run persisted a report")
	}
}

// LLM path: pattern analysis suggestions land in suggestions.jsonl with
// the Python 9-field row schema (M77 target), and LLM breaches merge
// with heuristic ones deduped.
func TestRunLLMSuggestionsSchema(t *testing.T) {
	ws := t.TempDir()
	seedOutcomes(t, ws,
		`{"outcome_id": "a", "status": "stuck", "goal": "g1", "summary": "LLM call failed: timeout"}`,
		`{"outcome_id": "b", "status": "stuck", "goal": "g2", "summary": "api connection error"}`,
	)
	// Both sessions get judged (2 calls each: alignment + notes), then
	// one pattern-analysis call. Fake repeats the last script entry, so
	// put the pattern JSON last and let the numeric score repeat first…
	// scores repeat "0.5"? No: script order is consumed sequentially —
	// alignment a, notes a, alignment b, notes b, patterns.
	fake := &llm.Fake{Script: []string{
		"0.5", "meh. VERDICT: **RETRY**",
		"0.5", "meh. VERDICT: **RETRY**",
		`{"patterns": ["timeouts cluster on api steps"], "suggestions": ["usually add a retry to api steps"], "threshold_breaches": ["error_events"]}`,
	}}
	report := Run(context.Background(), ws, fake, 50, false, defaultThresholds())
	if len(report.Suggestions) != 1 || len(report.Patterns) != 1 {
		t.Fatalf("LLM analysis not folded in: %+v", report)
	}
	// error_events breaches BOTH heuristically (2/2 sessions) and via
	// LLM — the union must dedup.
	n := 0
	for _, b := range report.ThresholdBreaches {
		if b == SignalErrorEvents {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("breach union not deduped: %v", report.ThresholdBreaches)
	}
	raw, err := os.ReadFile(suggestionsPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	var row map[string]any
	if jerr := json.Unmarshal([]byte(strings.TrimSpace(string(raw))), &row); jerr != nil {
		t.Fatal(jerr)
	}
	if !strings.HasPrefix(row["suggestion_id"].(string), "insp-") {
		t.Fatalf("suggestion_id format drifted: %v", row["suggestion_id"])
	}
	for _, key := range []string{"suggestion_id", "category", "target", "suggestion",
		"failure_pattern", "confidence", "outcomes_analyzed", "generated_at", "applied"} {
		if _, ok := row[key]; !ok {
			t.Fatalf("Python schema key %q missing from inspector suggestion row", key)
		}
	}
	if row["category"] != "inspection_finding" || row["confidence"] != 0.7 || row["applied"] != false {
		t.Fatalf("schema values drifted: %v", row)
	}
}

// Cadence: locked RMW returning none/normal/deep, with corrupt-field
// self-heal (string counter) and negative clamp (M76 target).
func TestCadenceTickModesAndSelfHeal(t *testing.T) {
	ws := t.TempDir()
	want := []string{"none", "normal", "none", "deep"}
	for i, w := range want {
		got, err := CadenceTick(ws, 2, 2)
		if err != nil {
			t.Fatal(err)
		}
		if got != w {
			t.Fatalf("tick %d: got %q want %q", i, got, w)
		}
	}
	// Corrupt: string counter self-heals instead of wedging the lane.
	if err := os.WriteFile(cadencePath(ws), []byte(`{"runs_since_inspect": "bad", "firings_since_deep": "worse"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, err := CadenceTick(ws, 2, 2); err != nil || got != "none" {
		t.Fatalf("corrupt counter did not self-heal: %q %v", got, err)
	}
	// Negative: clamps to 0, so the next tick counts 1, not -999999.
	if err := os.WriteFile(cadencePath(ws), []byte(`{"runs_since_inspect": -1000000, "firings_since_deep": -5}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, _ := CadenceTick(ws, 2, 2); got != "none" {
		t.Fatalf("negative clamp: first tick after clamp should be count=1 → none, got %q", got)
	}
	if got, _ := CadenceTick(ws, 2, 2); got == "none" {
		t.Fatal("negative counter deferred the lane — clamp missing")
	}
}

// Empty store: a report still lands (Python writes the zero-session
// report) and says zero sessions.
func TestRunEmptyStoreWritesZeroReport(t *testing.T) {
	ws := t.TempDir()
	report := Run(context.Background(), ws, nil, 50, false, defaultThresholds())
	if report.InspectedSessions != 0 {
		t.Fatalf("sessions %d, want 0", report.InspectedSessions)
	}
	if latest := GetLatestInspection(ws); latest == nil {
		t.Fatal("zero-session report not persisted")
	}
	if s := FrictionSummary(ws); s != "Inspector: no sessions inspected yet." {
		t.Fatalf("empty-store summary drifted: %q", s)
	}
}
