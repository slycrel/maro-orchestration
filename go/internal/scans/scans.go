// Package scans ports src/evolver_scans.py — the pure/statistical scanners
// the evolver cycle fans out to, plus the VERIFY_LEARN_ARC V2/V3 cadence
// lifecycle (verify.go) and the longitudinal apply-impact analysis
// (impact.go).
//
// LESSONS-ARE-DATA (Jeremy 2026-08-22): everything this package produces or
// consumes is shared workspace DATA — calibration.jsonl, evolver-baselines
// .jsonl, suggestion_outcomes.jsonl, step-costs.jsonl, canon_stats.jsonl,
// diagnoses.jsonl, events.jsonl, suggestions.jsonl. Rows written by either
// runtime rehydrate in the other; no learned value (threshold outcome,
// verdict, calibration reading) is ever reified into Go code. The engine
// mechanisms are ported; the learning stays in the store.
//
// Ported from the fork point:
//   - ScanCalibrationLog  — systematic miscalibration in escalation decisions
//     (memory/calibration.jsonl; override-rate + mean-confidence findings).
//   - ScanStepCosts       — high-burn step patterns from memory/step-costs
//     .jsonl (metrics.analyze_step_costs ported inline: lower-median, 2x rule).
//   - ScanQualityDrift    — this cycle vs rolling baseline (memory/evolver-
//     baselines.jsonl, locked append), N-consecutive-drop alert.
//   - ScanCanonCandidates — Stage 2→3 identity-promotion surface over
//     canon_stats.jsonl + the long-tier lesson store (human-gated; Δ-gate
//     and quarantine/contest exclusions honored).
//   - ScanSuggestionOutcomes — empirical confidence vs self-reported, per
//     category, from suggestion_outcomes.jsonl.
//   - RunStatisticalScans — the five-scanner fan-out (run_statistical_scans),
//     each scanner isolated: one failing scanner loses its findings, never
//     the cycle.
//
// NAMED AS NOT PORTED (do not mistake absence for coverage): the LLM-backed
// business-signal scan (sub_mission rows need a goal queue), harness-friction
// / persona-gap / skill-candidate / island passes (their subsystems have no
// Go port), Telegram notify, and the notify hook COMMAND (notify.go writes
// the durable events.jsonl row; running a configured shell hook is a
// spend/exec surface deferred with the heartbeat tranche).
package scans

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func memoryDir(ws string) string { return filepath.Join(ws, "memory") }

func nowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")
}

// parseISO accepts both runtimes' timestamp spellings (Python isoformat
// "+00:00", Go RFC3339Nano "Z", date-only prefixes are NOT accepted — a
// timestamp, not a day, keys every window here).
func parseISO(ts string) (time.Time, bool) {
	ts = strings.TrimSpace(ts)
	if ts == "" {
		return time.Time{}, false
	}
	for _, layout := range []string{
		time.RFC3339Nano,
		"2006-01-02T15:04:05.999999-07:00",
		"2006-01-02T15:04:05-07:00",
		"2006-01-02T15:04:05.999999",
		"2006-01-02T15:04:05",
	} {
		if t, err := time.Parse(layout, ts); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// readJSONLTail reads up to limit parsed rows from the END of a JSONL file,
// returned in FILE ORDER (oldest of the tail first). The read is byte-bounded
// (maxTailBytes) before parsing — the jsonl_utils.read_jsonl_tail lesson
// (adversarial D1 2026-07-15): a multi-GB events.jsonl must cost a bounded
// read, not a whole-file load. A row that fails to parse is skipped (one torn
// byte costs one row, never the corpus). Missing file → nil.
func readJSONLTail(path string, limit int) []map[string]any {
	const maxTailBytes = 8 << 20
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil
	}
	off := int64(0)
	if st.Size() > maxTailBytes {
		off = st.Size() - maxTailBytes
	}
	buf := make([]byte, st.Size()-off)
	if _, err := f.ReadAt(buf, off); err != nil && len(buf) > 0 {
		return nil
	}
	lines := strings.Split(string(buf), "\n")
	if off > 0 && len(lines) > 0 {
		lines = lines[1:] // first line is (probably) a torn fragment
	}
	var rows []map[string]any
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			continue
		}
		rows = append(rows, m)
	}
	if limit > 0 && len(rows) > limit {
		rows = rows[len(rows)-limit:]
	}
	return rows
}

func floatField(m map[string]any, key string) (float64, bool) {
	switch v := m[key].(type) {
	case float64:
		return v, true
	case int:
		return float64(v), true
	}
	return 0, false
}

func clipRunes(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// ---------------------------------------------------------------------------
// Calibration scan
// ---------------------------------------------------------------------------

// CalibrationFinding mirrors evolver_scans.CalibrationFinding.
type CalibrationFinding struct {
	DecisionClass  string
	EntryCount     int
	OverrideCount  int
	OverrideRate   float64
	MeanConfidence float64 // 1–10 scale
	Suggestion     string
}

// CalibrationOptions carry the fork-point defaults; zero values resolve to
// them (a zero-value options struct must scan, never silently scan-nothing).
type CalibrationOptions struct {
	MinEntries             int     // default 5
	HighOverrideThreshold  float64 // default 0.4
	LowConfidenceThreshold float64 // default 6.0
}

// ScanCalibrationLog ports scan_calibration_log: systematic miscalibration
// patterns in memory/calibration.jsonl, per decision_class.
func ScanCalibrationLog(ws string, o CalibrationOptions) []CalibrationFinding {
	if o.MinEntries <= 0 {
		o.MinEntries = 5
	}
	if o.HighOverrideThreshold <= 0 {
		o.HighOverrideThreshold = 0.4
	}
	if o.LowConfidenceThreshold <= 0 {
		o.LowConfidenceThreshold = 6.0
	}
	entries := readJSONLTail(filepath.Join(memoryDir(ws), "calibration.jsonl"), 0)
	if len(entries) == 0 {
		return nil
	}
	byClass := map[string][]map[string]any{}
	var order []string
	for _, e := range entries {
		dc, _ := e["decision_class"].(string)
		if dc == "" {
			dc = "unknown"
		}
		if _, seen := byClass[dc]; !seen {
			order = append(order, dc)
		}
		byClass[dc] = append(byClass[dc], e)
	}
	var findings []CalibrationFinding
	for _, dc := range order {
		classEntries := byClass[dc]
		if len(classEntries) < o.MinEntries {
			continue
		}
		overrides := 0
		var confidences []float64
		for _, e := range classEntries {
			// Python compares the raw values (`!=` on whatever the row holds);
			// string-compare the JSON forms so a numeric-vs-string action pair
			// still diffs the way Python's != would on decoded values.
			rawA, _ := json.Marshal(e["action_raw"])
			rawB, _ := json.Marshal(e["action_final"])
			if string(rawA) != string(rawB) {
				overrides++
			}
			if c, ok := floatField(e, "confidence"); ok {
				confidences = append(confidences, c)
			}
		}
		overrideRate := float64(overrides) / float64(len(classEntries))
		meanConf := 5.0
		if len(confidences) > 0 {
			sum := 0.0
			for _, c := range confidences {
				sum += c
			}
			meanConf = sum / float64(len(confidences))
		}
		var reasons []string
		if overrideRate > o.HighOverrideThreshold {
			reasons = append(reasons, fmt.Sprintf(
				"override rate %.0f%% (>%.0f%%) — LLM action is being overridden by "+
					"guardrails too often; add clearer %s examples to the escalation prompt",
				overrideRate*100, o.HighOverrideThreshold*100, pyRepr(dc)))
		}
		if meanConf < o.LowConfidenceThreshold {
			// pyVal, not %g: Python interpolates the raw float, so the
			// default renders "6.0" where %g gives "6". This reason IS
			// the suggestion text, and suggestion is a third of
			// contentKey — a spelling difference mints one duplicate row
			// per runtime on a shared store (r5 LOW, same family as the
			// pyRepr apostrophe and the canon target).
			reasons = append(reasons, fmt.Sprintf(
				"mean confidence %.1f/10 (<%s) — LLM is systematically uncertain on "+
					"%s decisions; consider adding explicit criteria or worked examples",
				meanConf, pyVal(o.LowConfidenceThreshold), pyRepr(dc)))
		}
		if len(reasons) > 0 {
			findings = append(findings, CalibrationFinding{
				DecisionClass:  dc,
				EntryCount:     len(classEntries),
				OverrideCount:  overrides,
				OverrideRate:   overrideRate,
				MeanConfidence: meanConf,
				Suggestion:     strings.Join(reasons, "; "),
			})
		}
	}
	return findings
}

// ---------------------------------------------------------------------------
// Step-cost scan (metrics.analyze_step_costs ported inline)
// ---------------------------------------------------------------------------

type stepTypeStats struct {
	count     int
	avgTokens int
	avgCost   float64
}

// ScanStepCosts ports scan_step_costs: step types whose average token cost
// exceeds 2x the lower-median, proposed as cost_optimization rows (which the
// apply gate HOLDS for human review — these are proposals, never actions).
func ScanStepCosts(ws string, minEntries int) []evolver.Suggestion {
	if minEntries <= 0 {
		minEntries = 5
	}
	entries := readJSONLTail(filepath.Join(memoryDir(ws), "step-costs.jsonl"), 200)
	if len(entries) < minEntries {
		return nil
	}

	byType := map[string][]map[string]any{}
	var order []string
	for _, e := range entries {
		st, _ := e["step_type"].(string)
		if st == "" {
			st = "general"
		}
		if _, seen := byType[st]; !seen {
			order = append(order, st)
		}
		byType[st] = append(byType[st], e)
	}
	stats := map[string]stepTypeStats{}
	var avgs []int
	for _, st := range order {
		typeEntries := byType[st]
		totalTok, totalCost := 0.0, 0.0
		for _, e := range typeEntries {
			if t, ok := floatField(e, "total_tokens"); ok {
				totalTok += t
			}
			if c, ok := floatField(e, "cost_usd"); ok {
				totalCost += c
			}
		}
		s := stepTypeStats{
			count:     len(typeEntries),
			avgTokens: int(totalTok) / len(typeEntries),
			avgCost:   totalCost / float64(len(typeEntries)),
		}
		stats[st] = s
		if s.avgTokens > 0 {
			avgs = append(avgs, s.avgTokens)
		}
	}
	if len(avgs) == 0 {
		return nil
	}
	// Lower median: floor((n-1)/2) — expensive types are above 2x the
	// cheaper half (metrics.py parity).
	sort.Ints(avgs)
	median := avgs[(len(avgs)-1)/2]
	if median <= 0 {
		return nil
	}

	var out []evolver.Suggestion
	for _, st := range order {
		s := stats[st]
		if s.avgTokens <= 2*median || s.count < 2 {
			continue
		}
		text := fmt.Sprintf(
			"Step type '%s' averages %s tokens across %d steps (~$%.6f/step). "+
				"Consider adding a token-budget constraint in the step prompt. "+
				"(Execution floor is MID by decree — do not suggest cheap-tier "+
				"routing for agentic steps.)",
			st, commaInt(s.avgTokens), s.count, s.avgCost)
		out = append(out, evolver.Suggestion{
			SuggestionID:     "cost-" + clipRunes(st, 12),
			Category:         "cost_optimization",
			Target:           st,
			Suggestion:       text,
			FailurePattern:   fmt.Sprintf("high_burn_step: %s avg=%dtok", st, s.avgTokens),
			Confidence:       0.70,
			OutcomesAnalyzed: s.count,
			GeneratedAt:      nowISO(),
		})
	}
	return out
}

// commaInt renders 12345 as "12,345" (Python's {:,} format — the on-disk
// suggestion text is shared data, keep the spelling identical).
func commaInt(n int) string {
	s := fmt.Sprintf("%d", n)
	neg := strings.HasPrefix(s, "-")
	if neg {
		s = s[1:]
	}
	var parts []string
	for len(s) > 3 {
		parts = append([]string{s[len(s)-3:]}, parts...)
		s = s[:len(s)-3]
	}
	parts = append([]string{s}, parts...)
	out := strings.Join(parts, ",")
	if neg {
		out = "-" + out
	}
	return out
}

// ---------------------------------------------------------------------------
// Quality drift
// ---------------------------------------------------------------------------

// QualityDriftFinding mirrors evolver_scans.QualityDriftFinding.
type QualityDriftFinding struct {
	Metric           string
	CurrentValue     float64
	BaselineValue    float64
	DeltaPct         float64
	ConsecutiveDrops int
	Suggestion       string
}

func baselinesPath(ws string) string {
	return filepath.Join(memoryDir(ws), "evolver-baselines.jsonl")
}

func loadBaselines(ws string, limit int) []map[string]any {
	rows := readJSONLTail(baselinesPath(ws), limit)
	// newest first (Python [-limit:][::-1])
	for i, j := 0, len(rows)-1; i < j; i, j = i+1, j-1 {
		rows[i], rows[j] = rows[j], rows[i]
	}
	return rows
}

// ScanQualityDrift ports scan_quality_drift: append this cycle's snapshot to
// the baselines ledger, then compare against the rolling average of prior
// cycles; flag a metric only after consecutiveAlert consecutive breaches.
func ScanQualityDrift(ws string, outcomes []map[string]any,
	dropThresholdPct float64, consecutiveAlert int) []QualityDriftFinding {
	if dropThresholdPct <= 0 {
		dropThresholdPct = 15.0
	}
	if consecutiveAlert <= 0 {
		consecutiveAlert = 3
	}
	if len(outcomes) == 0 {
		return nil
	}
	total := len(outcomes)
	done := 0
	var costs []float64
	for _, o := range outcomes {
		if s, _ := o["status"].(string); s == "done" {
			done++
		}
		if c, ok := floatField(o, "cost_usd"); ok {
			costs = append(costs, c)
		}
	}
	currentSuccess := float64(done) / float64(total)
	currentAvgCost := 0.0
	if len(costs) > 0 {
		sum := 0.0
		for _, c := range costs {
			sum += c
		}
		currentAvgCost = sum / float64(len(costs))
	}

	now := nowISO()
	snapshot := map[string]any{
		"ts":             now,
		"success_rate":   round4(currentSuccess),
		"avg_cost_usd":   round6(currentAvgCost),
		"outcomes_count": total,
	}
	if raw, err := json.Marshal(snapshot); err == nil {
		// Locked append (file_lock.locked_append parity); a failed save
		// never blocks the scan (Python swallows it too).
		_ = os.MkdirAll(memoryDir(ws), 0o755)
		_ = record.AppendRawLine(baselinesPath(ws), raw)
	}

	prior := loadBaselines(ws, 20)
	if len(prior) > 0 {
		if ts, _ := prior[0]["ts"].(string); ts == now {
			prior = prior[1:] // skip the snapshot we just wrote
		}
	}
	if len(prior) < 3 {
		return nil // not enough history to detect drift
	}

	var findings []QualityDriftFinding
	for _, m := range []struct {
		key            string
		current        float64
		higherIsBetter bool
	}{
		{"success_rate", currentSuccess, true},
		{"avg_cost_usd", currentAvgCost, false},
	} {
		var priorValues []float64
		for _, p := range prior {
			if v, ok := floatField(p, m.key); ok {
				priorValues = append(priorValues, v)
			}
		}
		if len(priorValues) == 0 {
			continue
		}
		baseline := 0.0
		for _, v := range priorValues {
			baseline += v
		}
		baseline /= float64(len(priorValues))
		if baseline == 0 {
			continue
		}

		frac := dropThresholdPct / 100
		var deltaPct float64
		var isWorse bool
		if m.higherIsBetter {
			deltaPct = (baseline - m.current) / baseline * 100
			isWorse = m.current < baseline*(1-frac)
		} else {
			deltaPct = (m.current - baseline) / baseline * 100
			isWorse = m.current > baseline*(1+frac)
		}
		if !isWorse {
			continue
		}

		consecutive := 1
		for _, pv := range priorValues {
			var breach bool
			if m.higherIsBetter {
				breach = pv < baseline*(1-frac)
			} else {
				breach = pv > baseline*(1+frac)
			}
			if breach {
				consecutive++
			} else {
				break
			}
		}
		if consecutive < consecutiveAlert {
			continue
		}

		direction := "dropped"
		if !m.higherIsBetter {
			direction = "risen"
		}
		findings = append(findings, QualityDriftFinding{
			Metric:           m.key,
			CurrentValue:     m.current,
			BaselineValue:    baseline,
			DeltaPct:         deltaPct,
			ConsecutiveDrops: consecutive,
			// Observation form (2026-08-02 guidance decree): the reading and
			// what it is consistent with — never a command.
			Suggestion: fmt.Sprintf(
				"%s has %s %.1f%% from baseline (%.4f vs %.4f) for %d consecutive "+
					"cycles. The window overlaps recent auto-applied evolver changes.",
				m.key, direction, deltaPct, m.current, baseline, consecutive),
		})
	}
	return findings
}

func round4(f float64) float64 { return pyval.Round(f, 4) }
func round6(f float64) float64 { return pyval.Round(f, 6) }

// roundN keeps its old signature for verify.go's one caller. The comment
// it used to carry claimed math.RoundToEven(f*scale)/scale "rounds
// half-to-even, matching Python round()" — a comment that STATES a
// measurement, and re-measuring falsified it: 682 divergences over
// round4(done/total) for every total <= 2000, with 1/160 giving 0.0063
// in CPython and 0.0062 here. These values land in evolver-baselines.jsonl
// and the drift detector compares current against baseline, so a
// mixed-runtime series produces fabricated deltas (mission-r6 MEDIUM).
func roundN(f, scale float64) float64 {
	return pyval.Round(f, int(math.Round(math.Log10(scale))))
}

// pyRepr quotes a string the way Python repr does. Calibration prose
// embeds decision classes with !r, and the text is a cross-runtime dedup
// key: divergent quoting mints duplicate suggestion rows.
//
// Delegates rather than reimplementing. There were THREE copies of the
// two-replacement version, all carrying the same two defects (adversarial
// r5, MEDIUM: the double-quote branch escaped nothing, and neither branch
// escaped control characters), and fixing one would have left the other
// two writing the old spelling into the same shared files.
func pyRepr(s string) string { return pytext.Repr(s) }

// pyVal renders a rate value the way a Python f-string does: nil → None,
// whole floats keep their ".0". Shared-ledger prose parity.
func pyVal(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case float64:
		s := strconv.FormatFloat(t, 'g', -1, 64)
		if !strings.ContainsAny(s, ".e") {
			s += ".0"
		}
		return s
	}
	return fmt.Sprintf("%v", v)
}

// ---------------------------------------------------------------------------
// Canon candidate scan (Stage 2→3 promotion surface)
// ---------------------------------------------------------------------------

// ScanCanonCandidates ports scan_canon_candidates + knowledge_web.
// get_canon_candidates: long-tier lessons with times-applied and task-type
// spread past the bar, surfaced as crystallization rows for HUMAN review —
// nothing here writes, and nothing auto-applies (the apply gate holds the
// category). Exclusions honored from the lesson row itself (lessons are
// data): quarantined, contested, Δ-gate demoted/inert, already-canon.
func ScanCanonCandidates(ws string, minHits, minTaskTypes int) []evolver.Suggestion {
	if minHits <= 0 {
		minHits = 10
	}
	if minTaskTypes <= 0 {
		minTaskTypes = 3
	}
	stats := loadCanonStats(ws)
	if len(stats) == 0 {
		return nil
	}
	store := knowledge.NewStore(ws)
	longLessons, _, err := store.LoadTieredLessons(knowledge.TierLong,
		knowledge.LoadOptions{MinScore: 0.0, Limit: 200})
	if err != nil || len(longLessons) == 0 {
		return nil
	}
	lessonByID := map[string]knowledge.TieredLesson{}
	for _, l := range longLessons {
		lessonByID[l.LessonID] = l
	}

	var lids []string
	for lid := range stats {
		lids = append(lids, lid)
	}
	// Python sorts candidates by times_applied desc (stable over insertion
	// order); lesson_id ascending is our deterministic tiebreak in place of
	// insertion order — same row set, matching primary order.
	sort.Slice(lids, func(i, j int) bool {
		hi, hj := stats[lids[i]].totalHits, stats[lids[j]].totalHits
		if hi != hj {
			return hi > hj
		}
		return lids[i] < lids[j]
	})

	var out []evolver.Suggestion
	for _, lid := range lids {
		s := stats[lid]
		if s.tier != knowledge.TierLong || s.totalHits < minHits || len(s.taskTypes) < minTaskTypes {
			continue
		}
		lesson, ok := lessonByID[lid]
		if !ok {
			continue
		}
		if knowledge.IsQuarantined(lesson) || knowledge.IsContested(lesson) {
			continue // stale canon hits from before the stamp never earn identity
		}
		if route, _ := lesson.DeltaEvidence["route"].(string); route == "effect-demote" || route == "effect-inert" {
			continue // Δ-gate: measured harmful or measured redundant
		}
		if len(lesson.Canon) > 0 {
			continue // door already walked through
		}
		types := make([]string, 0, len(s.taskTypes))
		for tt := range s.taskTypes {
			types = append(types, tt)
		}
		sort.Strings(types)
		shown := types
		if len(shown) > 4 {
			shown = shown[:4]
		}
		conf := 0.5 + float64(s.totalHits)*0.03 + float64(len(types))*0.05
		if conf > 0.95 {
			conf = 0.95
		}
		out = append(out, evolver.Suggestion{
			SuggestionID: "canon-" + record.NewID(),
			Category:     "crystallization",
			// Verbatim, NOT defaulted: Python's `c.get("task_type",
			// "general")` default is dead code — get_canon_candidates
			// always emits the key — so an empty task_type stays "" in
			// the target, and target feeds contentKey; defaulting here
			// would mint a second row per runtime on a shared store
			// (r4 LOW).
			Target: lesson.TaskType,
			Suggestion: fmt.Sprintf(
				"PROMOTE TO IDENTITY (Stage 3): '%s' — applied %dx across %d task "+
					"types (%s). Door: maro-memory canon-promote %s (writes playbook "+
					"Canon — always-active).",
				clipRunes(lesson.Lesson, 200), s.totalHits, len(types),
				strings.Join(shown, ", "), lid),
			FailurePattern: fmt.Sprintf("lesson_id=%s times_applied=%d task_types=%d",
				lid, s.totalHits, len(types)),
			Confidence:       conf,
			OutcomesAnalyzed: s.totalHits,
			GeneratedAt:      nowISO(),
		})
	}
	return out
}

type canonStat struct {
	totalHits int
	taskTypes map[string]bool
	tier      string
}

func loadCanonStats(ws string) map[string]canonStat {
	rows := readJSONLTail(filepath.Join(memoryDir(ws), "canon_stats.jsonl"), 0)
	stats := map[string]canonStat{}
	for _, e := range rows {
		lid, _ := e["lesson_id"].(string)
		if lid == "" {
			continue // drifted row (Python warns; the count is not load-bearing)
		}
		s, seen := stats[lid]
		if !seen {
			tier, _ := e["tier"].(string)
			if tier == "" {
				tier = knowledge.TierLong
			}
			s = canonStat{taskTypes: map[string]bool{}, tier: tier}
		}
		s.totalHits++
		tt, _ := e["task_type"].(string)
		if tt == "" {
			tt = "general"
		}
		s.taskTypes[tt] = true
		stats[lid] = s
	}
	return stats
}

// ---------------------------------------------------------------------------
// Suggestion-outcome calibration
// ---------------------------------------------------------------------------

func suggestionOutcomesPath(ws string) string {
	return filepath.Join(memoryDir(ws), "suggestion_outcomes.jsonl")
}

// RecordSuggestionOutcomes ports _record_suggestion_outcomes: per-suggestion
// verification outcomes, joined to change_log.jsonl for category/confidence.
// Feeds ScanSuggestionOutcomes' empirical-confidence readings.
func RecordSuggestionOutcomes(ws string, suggestionIDs []string, passed bool, runID string) {
	if len(suggestionIDs) == 0 {
		return
	}
	clByID := map[string]map[string]any{}
	for _, e := range readJSONLTail(filepath.Join(memoryDir(ws), "change_log.jsonl"), 0) {
		if sid, _ := e["suggestion_id"].(string); sid != "" {
			clByID[sid] = e // last row wins (Python dict overwrite)
		}
	}
	now := nowISO()
	for _, sid := range suggestionIDs {
		cl := clByID[sid]
		category := "unknown"
		confidence := 0.5
		if cl != nil {
			if c, _ := cl["category"].(string); c != "" {
				category = c
			}
			if f, ok := floatField(cl, "confidence"); ok {
				confidence = f
			}
		}
		raw, err := json.Marshal(map[string]any{
			"suggestion_id": sid,
			"category":      category,
			"confidence":    confidence,
			"verified":      passed,
			"run_id":        runID,
			"verified_at":   now,
		})
		if err != nil {
			continue
		}
		_ = os.MkdirAll(memoryDir(ws), 0o755)
		_ = record.AppendRawLine(suggestionOutcomesPath(ws), raw)
	}
}

// ScanSuggestionOutcomes ports scan_suggestion_outcomes: per-category
// empirical pass rate vs self-reported confidence; a category running well
// below its own claimed confidence is surfaced as ONE alarm (playbook_key
// "calibration:<cat>" — re-read in place, expires when it stops firing).
func ScanSuggestionOutcomes(ws string, minSamples int, overconfidenceRatio float64) []evolver.Suggestion {
	if minSamples <= 0 {
		minSamples = 3
	}
	if overconfidenceRatio <= 0 {
		overconfidenceRatio = 0.6
	}
	rows := readJSONLTail(suggestionOutcomesPath(ws), 0)
	if len(rows) == 0 {
		return nil
	}
	type catAgg struct {
		passed, failed int
		confidences    []float64
	}
	agg := map[string]*catAgg{}
	var order []string
	for _, e := range rows {
		cat, _ := e["category"].(string)
		if cat == "" {
			cat = "unknown"
		}
		a := agg[cat]
		if a == nil {
			a = &catAgg{}
			agg[cat] = a
			order = append(order, cat)
		}
		conf := 0.5
		if f, ok := floatField(e, "confidence"); ok {
			conf = f
		}
		a.confidences = append(a.confidences, conf)
		if b, _ := e["verified"].(bool); b {
			a.passed++
		} else {
			a.failed++
		}
	}

	var out []evolver.Suggestion
	for _, cat := range order {
		a := agg[cat]
		total := a.passed + a.failed
		if total < minSamples {
			continue
		}
		empirical := float64(a.passed) / float64(total)
		meanConf := 0.5
		if len(a.confidences) > 0 {
			sum := 0.0
			for _, c := range a.confidences {
				sum += c
			}
			meanConf = sum / float64(len(a.confidences))
		}
		if meanConf <= 0 || empirical >= overconfidenceRatio*meanConf {
			continue
		}
		out = append(out, evolver.Suggestion{
			SuggestionID: "calibration-" + record.NewID(),
			Category:     "observation",
			Target:       cat,
			// Observation form, not command form (2026-08-02 guidance decree).
			Suggestion: fmt.Sprintf(
				"Suggestions in category '%s' are running overconfident: "+
					"self-reported %.2f against an empirical pass rate of %.2f "+
					"(%d/%d verified).",
				cat, meanConf, empirical, a.passed, total),
			FailurePattern:   "overconfident:" + cat,
			Confidence:       0.8,
			OutcomesAnalyzed: total,
			GeneratedAt:      nowISO(),
			PlaybookKey:      "calibration:" + cat,
		})
	}
	return out
}

// ---------------------------------------------------------------------------
// Fan-out (run_statistical_scans)
// ---------------------------------------------------------------------------

// StatScanOptions gate individual scanners (all default ON, Python parity).
type StatScanOptions struct {
	SkipCalibration           bool
	SkipCosts                 bool
	SkipCanon                 bool
	SkipSuggestionCalibration bool
	SkipDrift                 bool
	Verbose                   bool
}

// RunStatisticalScans ports run_statistical_scans: the five non-LLM scanners,
// wrapped into Suggestion rows. No LLM calls, no auto-apply — callers decide
// whether to persist. Each scanner is isolated; a panic or failure in one
// loses that scanner's findings, never the cycle (Python's per-scanner
// try/except).
func RunStatisticalScans(ws string, outcomes []map[string]any, o StatScanOptions) []evolver.Suggestion {
	var suggestions []evolver.Suggestion
	report := func(tag string, n int) {
		if o.Verbose && n > 0 {
			fmt.Fprintf(os.Stderr, "[evolver] %s: %d finding(s)\n", tag, n)
		}
	}
	guard := func(name string, fn func()) {
		defer func() {
			if r := recover(); r != nil {
				fmt.Fprintf(os.Stderr, "[evolver] %s scan failed (non-fatal): %v\n", name, r)
			}
		}()
		fn()
	}

	if !o.SkipCalibration {
		guard("calibration", func() {
			findings := ScanCalibrationLog(ws, CalibrationOptions{})
			for _, cf := range findings {
				suggestions = append(suggestions, evolver.Suggestion{
					SuggestionID: "cal-" + record.NewID(),
					Category:     "prompt_tweak",
					Target:       "escalation",
					Suggestion:   cf.Suggestion,
					FailurePattern: fmt.Sprintf(
						"calibration: class=%s override_rate=%.0f%% mean_confidence=%.1f/10 n=%d",
						pyRepr(cf.DecisionClass), cf.OverrideRate*100, cf.MeanConfidence, cf.EntryCount),
					Confidence:       0.75,
					OutcomesAnalyzed: cf.EntryCount,
					GeneratedAt:      nowISO(),
				})
			}
			report("calibration_scan", len(findings))
		})
	}

	if !o.SkipCosts {
		guard("cost", func() {
			cost := ScanStepCosts(ws, 5)
			suggestions = append(suggestions, cost...)
			report("cost_scan", len(cost))
		})
	}

	if !o.SkipCanon {
		guard("canon", func() {
			canon := ScanCanonCandidates(ws, 10, 3)
			suggestions = append(suggestions, canon...)
			report("canon_scan", len(canon))
		})
	}

	if !o.SkipSuggestionCalibration {
		guard("suggestion calibration", func() {
			cal := ScanSuggestionOutcomes(ws, 3, 0.6)
			suggestions = append(suggestions, cal...)
			report("suggestion_calibration", len(cal))
		})
	}

	if !o.SkipDrift {
		guard("quality drift", func() {
			findings := ScanQualityDrift(ws, outcomes, 15.0, 3)
			for _, df := range findings {
				conf := 0.6 + float64(df.ConsecutiveDrops)*0.1
				if conf > 0.9 {
					conf = 0.9
				}
				suggestions = append(suggestions, evolver.Suggestion{
					SuggestionID: "drift-" + record.NewID(),
					Category:     "observation",
					Target:       df.Metric,
					Suggestion:   df.Suggestion,
					FailurePattern: fmt.Sprintf("quality_drift: %s delta=%.1f%% consecutive=%d",
						df.Metric, df.DeltaPct, df.ConsecutiveDrops),
					Confidence:       conf,
					OutcomesAnalyzed: len(outcomes),
					GeneratedAt:      nowISO(),
					// A drift reading, not a durable insight: one alarm per
					// metric, re-read in place, expires when the drift stops.
					PlaybookKey: "drift:" + df.Metric,
				})
			}
			report("drift_scan", len(findings))
		})
	}

	return suggestions
}
