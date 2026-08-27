package scans

// Longitudinal evolver impact analysis (K6 verify→learn gap) — ports the
// scan_evolver_impact half of evolver_scans.py: for each applied suggestion,
// compare the trust-filtered stuck rate in a window before the apply against
// the window after, and surface the delta as evidence for or against the
// verify→learn loop working.

import (
	"fmt"
	"math"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// EvolverImpactRecord mirrors evolver_scans.EvolverImpactRecord.
type EvolverImpactRecord struct {
	SuggestionID    string
	Category        string
	AppliedAt       string
	OutcomesBefore  int
	StuckBefore     int
	OutcomesAfter   int
	StuckAfter      int
	StuckRateBefore float64 // NaN if no data
	StuckRateAfter  float64 // NaN if no data
	Delta           float64 // NaN for insufficient_data
	Verdict         string  // improved | degraded | neutral | insufficient_data
}

// outcomeTS ports _outcome_ts: recorded_at first (the real Outcome field),
// then created_at/timestamp (synthetic shapes). Reading only the fallbacks
// silently excluded every production outcome once (VERIFY_LEARN_ARC V2) —
// order is load-bearing.
func outcomeTS(o map[string]any) (time.Time, bool) {
	for _, key := range []string{"recorded_at", "created_at", "timestamp"} {
		if s, _ := o[key].(string); s != "" {
			return parseISO(s)
		}
	}
	return time.Time{}, false
}

// verifyCounts ports _verify_counts (VERIFY_LEARN_ARC §4): trust-filtered
// (counted, failing) for a window. Directional and excluded verdicts leave
// BOTH tallies — a verifier's own failure must never read as a behavioral
// regression. A counted outcome fails when it stuck, or when a full-trust
// verdict judged the goal unachieved (done ≠ achieved).
func verifyCounts(outcomes []map[string]any) (counted, failing int) {
	for _, o := range outcomes {
		bucket := record.VerdictTrust(o)
		if bucket == record.VerdictTrustExcluded || bucket == record.VerdictTrustDirectional {
			continue
		}
		counted++
		status, _ := o["status"].(string)
		judged, achieved := record.GoalAchieved(o)
		if status == "stuck" || (bucket == record.VerdictTrustFull && judged && !achieved) {
			failing++
		}
	}
	return counted, failing
}

// ImpactOptions carry scan_evolver_impact's keyword defaults.
type ImpactOptions struct {
	LookbackHours  int // default 24
	LookaheadHours int // default 24
	MinOutcomes    int // default 3 — minimum in EACH window
	Limit          int // default 10
}

// ScanEvolverImpact ports scan_evolver_impact. Apply records come from
// suggestions.jsonl (the durable source of truth — applied_at is stamped at
// apply time); captains_log.jsonl EVOLVER_APPLIED events remain only as
// fallback for historical applies that predate the stamp. Named divergence:
// Python's query_log spans rotated captain's-log archives; Go reads only the
// active file — a pre-rotation legacy apply simply doesn't appear.
func ScanEvolverImpact(ws string, o ImpactOptions) []EvolverImpactRecord {
	if o.LookbackHours <= 0 {
		o.LookbackHours = 24
	}
	if o.LookaheadHours <= 0 {
		o.LookaheadHours = 24
	}
	if o.MinOutcomes <= 0 {
		o.MinOutcomes = 3
	}
	if o.Limit <= 0 {
		o.Limit = 10
	}

	type applyRec struct {
		suggestionID, category, appliedAt string
	}
	var applyRecords []applyRec
	seen := map[string]bool{}
	for _, s := range evolver.LoadSuggestions(ws, 1000) {
		if s.Applied && s.AppliedAt != "" {
			applyRecords = append(applyRecords, applyRec{s.SuggestionID, s.Category, s.AppliedAt})
			seen[s.SuggestionID] = true
		}
	}
	for _, e := range readJSONLTail(filepath.Join(memoryDir(ws), "captains_log.jsonl"), 0) {
		if et, _ := e["event_type"].(string); et != "EVOLVER_APPLIED" {
			continue
		}
		ctx, _ := e["context"].(map[string]any)
		sid, _ := ctx["suggestion_id"].(string)
		if sid == "" {
			sid, _ = e["subject"].(string)
		}
		if sid == "" || seen[sid] {
			continue
		}
		category, _ := ctx["category"].(string)
		if category == "" {
			category = "unknown"
		}
		ts, _ := e["timestamp"].(string)
		applyRecords = append(applyRecords, applyRec{sid, category, ts})
	}
	if len(applyRecords) == 0 {
		return nil
	}
	sort.SliceStable(applyRecords, func(i, j int) bool {
		return applyRecords[i].appliedAt > applyRecords[j].appliedAt
	})
	if len(applyRecords) > o.Limit {
		applyRecords = applyRecords[:o.Limit]
	}

	outcomes, err := record.LoadOutcomes(ws, 5000)
	if err != nil {
		return nil
	}
	type dated struct {
		t time.Time
		o map[string]any
	}
	var cache []dated
	for _, oc := range outcomes {
		if t, ok := outcomeTS(oc); ok {
			cache = append(cache, dated{t, oc})
		}
	}

	window := func(center time.Time, hoursBefore, hoursAfter float64) []map[string]any {
		from := center.Add(-time.Duration(hoursBefore * float64(time.Hour)))
		to := center.Add(time.Duration(hoursAfter * float64(time.Hour)))
		var out []map[string]any
		for _, d := range cache {
			if !d.t.Before(from) && d.t.Before(to) {
				out = append(out, d.o)
			}
		}
		return out
	}

	var records []EvolverImpactRecord
	for _, rec := range applyRecords {
		tApply, ok := parseISO(rec.appliedAt)
		if !ok {
			continue
		}
		before := window(tApply, float64(o.LookbackHours), 0)
		after := window(tApply, 0, float64(o.LookaheadHours))
		nBefore, stuckBefore := verifyCounts(before)
		nAfter, stuckAfter := verifyCounts(after)
		srBefore, srAfter := math.NaN(), math.NaN()
		if nBefore > 0 {
			srBefore = float64(stuckBefore) / float64(nBefore)
		}
		if nAfter > 0 {
			srAfter = float64(stuckAfter) / float64(nAfter)
		}

		verdict := "insufficient_data"
		delta := math.NaN()
		// EACH window needs the minimum: with `and`-style laxness a 1-sample
		// baseline against a full after-window would verdict off a single run.
		if nBefore >= o.MinOutcomes && nAfter >= o.MinOutcomes &&
			!math.IsNaN(srBefore) && !math.IsNaN(srAfter) {
			delta = srAfter - srBefore
			switch {
			case math.Abs(delta) < 0.05:
				verdict = "neutral"
			case delta < 0:
				verdict = "improved"
			default:
				verdict = "degraded"
			}
		}

		records = append(records, EvolverImpactRecord{
			SuggestionID:    rec.suggestionID,
			Category:        rec.category,
			AppliedAt:       rec.appliedAt,
			OutcomesBefore:  nBefore,
			StuckBefore:     stuckBefore,
			OutcomesAfter:   nAfter,
			StuckAfter:      stuckAfter,
			StuckRateBefore: srBefore,
			StuckRateAfter:  srAfter,
			Delta:           delta,
			Verdict:         verdict,
		})
	}
	return records
}

// FormatImpactSummary ports format_impact_summary.
func FormatImpactSummary(records []EvolverImpactRecord) string {
	if len(records) == 0 {
		return "No EVOLVER_APPLIED events found (or insufficient outcome data)."
	}
	improved, degraded, neutral, noData := 0, 0, 0, 0
	for _, r := range records {
		switch r.Verdict {
		case "improved":
			improved++
		case "degraded":
			degraded++
		case "neutral":
			neutral++
		default:
			noData++
		}
	}
	lines := []string{
		fmt.Sprintf("Evolver impact analysis: %d applied suggestion(s) analyzed", len(records)),
		fmt.Sprintf("  improved=%d degraded=%d neutral=%d no_data=%d", improved, degraded, neutral, noData),
		"",
	}
	day := func(ts string) string {
		if len(ts) >= 10 {
			return ts[:10]
		}
		return ts
	}
	id12 := func(id string) string { return pytext.Head(id, 12) }
	for _, r := range records {
		if r.Verdict == "insufficient_data" {
			lines = append(lines, fmt.Sprintf(
				"  [%s] %s @ %s — insufficient data (before=%d, after=%d)",
				r.Category, id12(r.SuggestionID), day(r.AppliedAt),
				r.OutcomesBefore, r.OutcomesAfter))
			continue
		}
		deltaStr := "n/a"
		if !math.IsNaN(r.Delta) {
			deltaStr = fmt.Sprintf("%+.1f%%", r.Delta*100)
		}
		lines = append(lines, fmt.Sprintf(
			"  [%s] %s @ %s — %s: stuck %.0f%%→%.0f%% (Δ%s)",
			r.Category, id12(r.SuggestionID), day(r.AppliedAt), r.Verdict,
			r.StuckRateBefore*100, r.StuckRateAfter*100, deltaStr))
	}
	return strings.Join(lines, "\n")
}
