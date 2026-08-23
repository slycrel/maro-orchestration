package scans

// VERIFY_LEARN_ARC V2/V3 — cadence verdicts + authority-aware auto-revert
// (evolver_scans.verify_applied_suggestions and its helpers). Every applied
// suggestion gets the lifecycle discipline lessons already have: an
// expectation at birth (V1 expected_signal), a verdict at cadence, and
// demotion (revert) when contradicted.
//
//	confirmed    → terminal stamp + positive calibration outcome.
//	degraded     → symmetric authority (§3): auto-applied rows are reverted
//	               (or stamped degraded_revert_failed + BLOCKING notify when
//	               the change cannot be behaviorally undone); human-applied
//	               rows are NEVER auto-reverted — degraded_needs_review +
//	               BLOCKING notify.
//	inconclusive → verify_extensions bump; past max_extensions the row parks
//	               "unverifiable" (an honest unverifiable beats an eternal
//	               pending).
//
// V3: a row that declares a failure_class_rate expected_signal (graduation
// templates do) is verdicted on that class's rate over timestamped-diagnosis
// windows — the metric it actually targets; the class-neutral stuck-rate is
// the fallback whenever class data is thin. Diagnosis timestamps come from
// each row's recorded_at stamp or the events-log join on loop_id — both
// SHARED data (Python writes diagnoses.jsonl and events.jsonl; Go reads
// them), so the class path is live on a shared workspace and honestly empty
// on a Go-only one.
//
// No LLM calls; rides the evolver cadence hook (no daemon).

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// classifyCadenceVerdict ports _classify_cadence_verdict. The expected
// direction for every stamped change is "rate down".
func classifyCadenceVerdict(nBefore, nAfter int, srBefore, srAfter float64,
	minPostApply, minBaseline int, deltaThreshold float64) string {
	if nAfter < minPostApply {
		return "inconclusive" // not enough post-apply evidence yet
	}
	if nBefore < minBaseline {
		return "inconclusive" // no baseline to compare against
	}
	if math.IsNaN(srBefore) || math.IsNaN(srAfter) {
		return "inconclusive"
	}
	delta := srAfter - srBefore
	if delta <= -deltaThreshold {
		return "confirmed"
	}
	if delta >= deltaThreshold {
		return "degraded"
	}
	return "inconclusive" // flat: expected movement, saw none
}

// loopTSIndex ports _loop_ts_index: loop_id → latest event ts from
// memory/events.jsonl (byte-bounded tail; the diagnosis moment is its loop's
// finalize, recoverable from the event stream for pre-V3 rows).
func loopTSIndex(ws string, limit int) map[string]string {
	idx := map[string]string{}
	for _, e := range readJSONLTail(filepath.Join(memoryDir(ws), "events.jsonl"), limit) {
		lid, _ := e["loop_id"].(string)
		ts, _ := e["ts"].(string)
		if ts == "" {
			ts, _ = e["timestamp"].(string)
		}
		if lid == "" || ts == "" {
			continue
		}
		if prev, seen := idx[lid]; !seen || ts > prev {
			idx[lid] = ts
		}
	}
	return idx
}

type datedDiagnosis struct {
	t  time.Time
	fc string
}

// loadDatedDiagnoses ports _load_dated_diagnoses: (when, failure_class) per
// diagnosis, ascending. recorded_at stamp first (V3 go-forward), events-log
// join second; a row with neither is EXCLUDED and counted (no-silent-caps).
func loadDatedDiagnoses(ws string, limit int) []datedDiagnosis {
	rows := readJSONLTail(filepath.Join(memoryDir(ws), "diagnoses.jsonl"), limit)
	if len(rows) == 0 {
		return nil
	}
	var tsIndex map[string]string
	var out []datedDiagnosis
	droppedNoTS := 0
	for _, d := range rows {
		fc, _ := d["failure_class"].(string)
		if fc == "" {
			continue
		}
		ts, _ := d["recorded_at"].(string)
		if ts == "" {
			if tsIndex == nil { // built lazily — only pay when a row needs it
				tsIndex = loopTSIndex(ws, 50000)
			}
			lid, _ := d["loop_id"].(string)
			ts = tsIndex[lid]
		}
		if ts == "" {
			droppedNoTS++
			continue
		}
		if t, ok := parseISO(ts); ok {
			out = append(out, datedDiagnosis{t, fc})
		}
	}
	if droppedNoTS > 0 {
		fmt.Fprintf(os.Stderr,
			"[evolver] dated-diagnosis load: %d/%d classed diagnoses excluded "+
				"(no recorded_at stamp and no events-log join)\n",
			droppedNoTS, droppedNoTS+len(out))
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].t.Before(out[j].t) })
	return out
}

// expectedClass ports _expected_class: the failure class a suggestion's V1
// expected_signal targets, or "" (rows without one keep the stuck-rate metric).
func expectedClass(s evolver.Suggestion) string {
	for _, item := range s.ExpectedSignal {
		if m, _ := item["metric"].(string); m == "failure_class_rate" {
			if cls, _ := item["class"].(string); cls != "" {
				return cls
			}
		}
	}
	return ""
}

// classRateWindows ports _class_rate_windows: the last minN stamped diagnoses
// before apply, the FIRST minN after; a hit is a diagnosis of class fc.
// Bounding `after` keeps a later, unrelated failure cluster out of this row's
// verdict.
func classRateWindows(diags []datedDiagnosis, fc string, tApply time.Time, minN int) (nBefore, hitsBefore, nAfter, hitsAfter int) {
	var before, after []string
	for _, d := range diags {
		if d.t.Before(tApply) {
			before = append(before, d.fc)
		} else if len(after) < minN {
			after = append(after, d.fc)
		}
	}
	if len(before) > minN {
		before = before[len(before)-minN:]
	}
	for _, c := range before {
		if c == fc {
			hitsBefore++
		}
	}
	for _, c := range after {
		if c == fc {
			hitsAfter++
		}
	}
	return len(before), hitsBefore, len(after), hitsAfter
}

// VerifySummary is verify_applied_suggestions' summary dict, typed.
type VerifySummary struct {
	Enabled        bool
	Skipped        string // "", "disabled", "load_failed"
	Candidates     int
	Confirmed      int
	Reverted       int
	RevertFailed   int
	ReviewQueued   int
	Unverifiable   int
	Pending        int
	SkippedNoStamp int
}

// VerifyOptions override the config-resolved knobs (zero = resolve from cfg).
type VerifyOptions struct {
	DryRun  bool
	Verbose bool
	NowISO  string // test seam; empty = wall clock
}

// VerifyAppliedSuggestions ports verify_applied_suggestions. Config knobs
// (data, not code — evolver.verify_* in the two-tier YAML):
// verify_cadence_verdicts (default true), verify_min_post_apply (10),
// verify_max_extensions (3), verify_delta_threshold (0.05),
// verify_use_class_signal (true).
func VerifyAppliedSuggestions(ws string, rec *record.Recorder, cfg map[string]any,
	runID string, o VerifyOptions) VerifySummary {
	if !config.Get(cfg, "evolver.verify_cadence_verdicts", true) {
		return VerifySummary{Skipped: "disabled"}
	}
	minPostApply := config.Get(cfg, "evolver.verify_min_post_apply", 10)
	if minPostApply <= 0 {
		minPostApply = 10
	}
	maxExtensions := config.Get(cfg, "evolver.verify_max_extensions", 3)
	if maxExtensions <= 0 {
		maxExtensions = 3
	}
	deltaThreshold := config.Get(cfg, "evolver.verify_delta_threshold", 0.05)
	if deltaThreshold <= 0 {
		deltaThreshold = 0.05
	}
	useClassSignal := config.Get(cfg, "evolver.verify_use_class_signal", true)
	// Baseline floor: at least half the post-apply window, floor 3, capped at
	// minPostApply — never auto-revert off a statistically thin baseline.
	minBaseline := minPostApply / 2
	if minBaseline < 3 {
		minBaseline = 3
	}
	if minBaseline > minPostApply {
		minBaseline = minPostApply
	}

	summary := VerifySummary{Enabled: true}

	outcomes, err := record.LoadOutcomes(ws, 5000)
	if err != nil {
		summary.Skipped = "load_failed"
		return summary
	}
	// Trusted-only, dated, ascending — computed once, reused per candidate.
	// The pre-filter keeps the count-based windows symmetric: a fixed count
	// of *trusted* rows on each side, keyed to THIS row's apply.
	type dated struct {
		t time.Time
		o map[string]any
	}
	var datedRows []dated
	for _, oc := range outcomes {
		t, ok := outcomeTS(oc)
		if !ok {
			continue
		}
		bucket := record.VerdictTrust(oc)
		if bucket == record.VerdictTrustExcluded || bucket == record.VerdictTrustDirectional {
			continue
		}
		datedRows = append(datedRows, dated{t, oc})
	}
	sort.SliceStable(datedRows, func(i, j int) bool { return datedRows[i].t.Before(datedRows[j].t) })

	var datedDiags []datedDiagnosis
	if useClassSignal {
		datedDiags = loadDatedDiagnoses(ws, 5000)
	}

	// Newest-1000 window (load_suggestions convention): applied-unverified
	// rows are few and terminal within max_extensions cadences; acceptable at
	// this box's scale.
	suggestions := evolver.LoadSuggestions(ws, 1000)
	var candidates []evolver.Suggestion
	for _, s := range suggestions {
		if s.Applied && s.VerifiedAt == "" {
			candidates = append(candidates, s)
		}
	}
	summary.Candidates = len(candidates)
	if len(candidates) == 0 {
		return summary
	}

	now := o.NowISO
	if now == "" {
		now = nowISO()
	}

	for _, s := range candidates {
		tApply, ok := parseISO(s.AppliedAt)
		if !ok {
			// A legacy applied row with no stamp can't be windowed — leave it
			// (don't force-park; there's nothing to compare).
			summary.SkippedNoStamp++
			continue
		}

		var before, after []map[string]any
		for _, d := range datedRows {
			if d.t.Before(tApply) {
				before = append(before, d.o)
			} else if len(after) < minPostApply {
				after = append(after, d.o)
			}
		}
		if len(before) > minPostApply {
			before = before[len(before)-minPostApply:]
		}
		nBefore, stuckBefore := verifyCounts(before)
		nAfter, stuckAfter := verifyCounts(after)
		srBefore, srAfter := math.NaN(), math.NaN()
		if nBefore > 0 {
			srBefore = float64(stuckBefore) / float64(nBefore)
		}
		if nAfter > 0 {
			srAfter = float64(stuckAfter) / float64(nAfter)
		}
		metricLabel := "stuck_rate"

		// V3: prefer the row's own expected_signal when BOTH class windows
		// have enough stamped-diagnosis data; a sparse class parks honestly
		// on the stuck-rate fallback instead of verdicting off noise.
		if useClassSignal {
			if fc := expectedClass(s); fc != "" {
				cb, hb, ca, ha := classRateWindows(datedDiags, fc, tApply, minPostApply)
				if ca >= minPostApply && cb >= minBaseline {
					nBefore, nAfter = cb, ca
					srBefore = float64(hb) / float64(cb)
					srAfter = float64(ha) / float64(ca)
					metricLabel = "failure_class_rate:" + fc
				}
			}
		}

		verdict := classifyCadenceVerdict(nBefore, nAfter, srBefore, srAfter,
			minPostApply, minBaseline, deltaThreshold)

		manual := s.AppliedManually
		rates := map[string]any{
			"stuck_rate_before": nanToNil(srBefore),
			"stuck_rate_after":  nanToNil(srAfter),
			"n_before":          nBefore,
			"n_after":           nAfter,
			"metric":            metricLabel,
		}

		switch {
		case verdict == "confirmed":
			if !o.DryRun {
				// Terminal stamps are first-writer-wins; the side-effect
				// appends (calibration outcome, EVOLVER_VERDICT) belong to
				// whichever pass actually landed the stamp — otherwise two
				// overlapping cadences double-count the denominator the
				// calibration scanner reads (r1 QA review).
				_, changed := evolver.StampVerificationChanged(ws, s.SuggestionID,
					evolver.VerificationStamp{Verdict: strPtr("confirmed"), VerifiedAt: &now})
				if !changed {
					continue
				}
				RecordSuggestionOutcomes(ws, []string{s.SuggestionID}, true, runID)
				logVerdictEvent(rec, s, "confirmed", "confirmed", manual, rates)
			}
			summary.Confirmed++

		case verdict == "degraded" && manual:
			// Authority asymmetry: a human applied it — surface, never revert.
			if !o.DryRun {
				_, changed := evolver.StampVerificationChanged(ws, s.SuggestionID,
					evolver.VerificationStamp{Verdict: strPtr("degraded_needs_review"), VerifiedAt: &now})
				if !changed {
					continue
				}
				RecordSuggestionOutcomes(ws, []string{s.SuggestionID}, false, runID)
				logVerdictEvent(rec, s, "degraded", "review_required", manual, rates)
				notifyVerdict(ws, s, "review_required", true, rates)
			}
			summary.ReviewQueued++

		case verdict == "degraded":
			// System applied it — try to undo its own mess. Re-read first: an
			// IRREVERSIBLE auto-revert must not fire off stale authority state
			// (narrows, does not fully close, the TOCTOU — a CAS inside the
			// revert lock is over-built for a single-box cadence system).
			fresh := evolver.GetSuggestion(ws, s.SuggestionID)
			if fresh == nil {
				fresh = &s
			}
			if fresh.VerifiedAt != "" || !fresh.Applied {
				continue // already terminal / reverted by another pass
			}
			if fresh.AppliedManually {
				// A human took authority since the snapshot. Same changed-
				// gate as every other arm — this fifth one was missed in r1
				// and still double-emitted under cadence overlap (r2 MED-1).
				if !o.DryRun {
					_, changed := evolver.StampVerificationChanged(ws, s.SuggestionID,
						evolver.VerificationStamp{Verdict: strPtr("degraded_needs_review"), VerifiedAt: &now})
					if !changed {
						continue
					}
					RecordSuggestionOutcomes(ws, []string{s.SuggestionID}, false, runID)
					logVerdictEvent(rec, s, "degraded", "review_required", true, rates)
					notifyVerdict(ws, s, "review_required", true, rates)
				}
				summary.ReviewQueued++
				continue
			}
			if o.DryRun {
				summary.Reverted++
				continue
			}
			rv := evolver.Revert(ws, rec, s.SuggestionID)
			switch {
			case rv.Behavioral:
				_, changed := evolver.StampVerificationChanged(ws, s.SuggestionID,
					evolver.VerificationStamp{Verdict: strPtr("degraded"), VerifiedAt: &now})
				if !changed {
					continue
				}
				RecordSuggestionOutcomes(ws, []string{s.SuggestionID}, false, runID)
				withRev := merged(rates, map[string]any{"reverted": true})
				logVerdictEvent(rec, s, "degraded", "reverted", manual, withRev)
				notifyVerdict(ws, s, "reverted", false, withRev)
				summary.Reverted++
			case rv.NothingToRevert:
				// A concurrent cadence got there first: its revert flipped
				// applied before ours ran. That pass owns the stamp and the
				// record — stamping degraded_revert_failed here would falsify
				// a revert that SUCCEEDED and fire a false BLOCKING alarm
				// (r1 QA review's HIGH, repro'd on the first attempt).
				continue
			default:
				// Genuinely could NOT behaviorally undo it. Stamp terminal
				// (an impossible revert isn't retried every cadence) but
				// surface it BLOCKING — never claimed as success, never
				// silently invisible. First-writer-wins like every terminal.
				_, changed := evolver.StampVerificationChanged(ws, s.SuggestionID,
					evolver.VerificationStamp{Verdict: strPtr("degraded_revert_failed"), VerifiedAt: &now})
				if !changed {
					continue
				}
				RecordSuggestionOutcomes(ws, []string{s.SuggestionID}, false, runID)
				withRev := merged(rates, map[string]any{
					"reverted": rv.Reverted, "revert_detail": rv.Detail})
				logVerdictEvent(rec, s, "degraded", "revert_failed", manual, withRev)
				notifyVerdict(ws, s, "revert_failed", true, withRev)
				summary.RevertFailed++
			}

		default: // inconclusive
			if o.DryRun {
				if s.VerifyExtensions+1 >= maxExtensions {
					summary.Unverifiable++
				} else {
					summary.Pending++
				}
				continue
			}
			// Atomic bump: the increment and the possible park happen in one
			// locked write off the CURRENT stored value — an absolute stamp
			// computed from the pre-lock snapshot lost concurrent bumps
			// (both passes stamping 1; r1 QA review).
			_, parked, changed := evolver.BumpExtensionOrPark(
				ws, s.SuggestionID, maxExtensions, now)
			if !changed {
				continue
			}
			if parked {
				logVerdictEvent(rec, s, "unverifiable", "parked", manual, rates)
				summary.Unverifiable++
			} else {
				summary.Pending++
			}
		}
	}

	if o.Verbose {
		fmt.Fprintf(os.Stderr,
			"[evolver] verify→learn cadence: %d confirmed, %d reverted, %d revert-failed, "+
				"%d review-queued, %d unverifiable, %d pending (of %d applied-unverified)\n",
			summary.Confirmed, summary.Reverted, summary.RevertFailed,
			summary.ReviewQueued, summary.Unverifiable, summary.Pending, summary.Candidates)
	}
	return summary
}

func strPtr(s string) *string { return &s }

func nanToNil(f float64) any {
	if math.IsNaN(f) {
		return nil
	}
	return roundN(f, 1e3)
}

func merged(a, b map[string]any) map[string]any {
	out := make(map[string]any, len(a)+len(b))
	for k, v := range a {
		out[k] = v
	}
	for k, v := range b {
		out[k] = v
	}
	return out
}

// logVerdictEvent ports _log_verdict_event: one EVOLVER_VERDICT captain's-log
// row per cadence verdict.
func logVerdictEvent(rec *record.Recorder, s evolver.Suggestion,
	verdict, action string, manual bool, rates map[string]any) {
	if rec == nil {
		return
	}
	ctx := merged(rates, map[string]any{
		"suggestion_id": s.SuggestionID, "category": s.Category,
		"verdict": verdict, "action": action, "applied_manually": manual,
	})
	// pyVal: rates can be nil (parked/insufficient-data rows) — the shared
	// captains_log prose must read "None→None" as Python writes it, not
	// Go's "<nil>" (r1 parity review).
	_ = rec.Event("EVOLVER_VERDICT", s.SuggestionID,
		fmt.Sprintf("Cadence verdict %s (%s) for %s '%s': stuck %s→%s over %s post-apply runs.",
			verdict, action, s.Category, s.Target,
			pyVal(rates["stuck_rate_before"]), pyVal(rates["stuck_rate_after"]),
			pyVal(rates["n_after"])),
		ctx, "")
}
