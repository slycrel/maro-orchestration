// Package inspector ports src/inspector.py (Phase 12) — post-hoc
// quality oversight over recorded outcomes.
//
// Role distinction (Python module doc): heartbeat = health (is the
// system running?); inspector = quality (is it producing the right
// outcomes?). The inspector is a READ-ONLY observer of
// memory/outcomes.jsonl — it never modifies running loops. Its
// suggestions feed memory/suggestions.jsonl, which the evolver reads.
//
// Ported lessons carried in the shapes below:
//   - Six friction signals, not seven: repeated_rephrasing was declared
//     for months with no detector and no comparison site (2026-08-06
//     census) — "a threshold returns with its comparison site or not at
//     all". This port starts at six.
//   - Escalation keywords split tautological vs informative (session 20
//     adversarial review 3.5): "stuck"/"error"/"failed"/"cannot" appear
//     in every stuck_reason by construction and carry no signal. NOTE
//     the detector itself counts "critical"+"failed" occurrences — the
//     split list is the vocabulary contract; the fork-point detector is
//     ported as-is, count gate at EscalationMinHits.
//   - assess-alignment returns nil without an adapter (fail-open census
//     2026-07-31: the old 0.7 default equaled the "good" bar exactly,
//     so every adapter-less inspection wore a grade it never earned).
//     inspect_session keeps 0.7 as DISPLAY value but caps unjudged
//     quality at "fair".
//   - A judged goal-NOT-achieved run and a verdict-pending deferred row
//     also cap at "fair" (not "poor": closure verdicts are noisy on
//     build goals, 2026-07-09 dogfood).
//   - Cadence tick is one locked read-modify-write with corrupt-field
//     self-heal and negative clamp (2026-08-08 review: a type-corrupt
//     counter used to wedge the lane on every finalize; a negative one
//     deferred inspection for a million finalizations).
//
// Named as NOT ported from the fork-point module: inspector_loop (the
// systemd daemon — Go has no daemon surface yet), attribution
// enrichment (src/attribution.py has no Go port), and the legacy
// _ESCALATION_KEYWORDS union export (no Go importer to stay compatible
// with).
package inspector

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Friction signal constants (Factory AI Signals research).
const (
	SignalErrorEvents       = "error_events"
	SignalEscalationTone    = "escalation_tone"
	SignalPlatformConfusion = "platform_confusion"
	SignalAbandonedToolFlow = "abandoned_tool_flow"
	SignalBacktracking      = "backtracking"
	SignalContextChurn      = "context_churn"
)

// AllSignals enumerates the six detectors, in Python's ALL_SIGNALS order.
var AllSignals = []string{
	SignalErrorEvents,
	SignalEscalationTone,
	SignalPlatformConfusion,
	SignalAbandonedToolFlow,
	SignalBacktracking,
	SignalContextChurn,
}

// Escalation keyword split (session 20 adversarial review finding 3.5).
// Tautological words appear in every stuck_reason by construction and
// carry no signal on their own; informative words indicate genuine
// escalation beyond "yep, stuck". Kept as the vocabulary contract even
// though the fork-point heuristic detector below matches its own pair —
// the split is what any future keyword change must be judged against.
var (
	EscalationKeywordsTautological = []string{"stuck", "error", "failed", "cannot"}
	EscalationKeywordsInformative  = []string{
		"broken", "impossible",
		"doesn't work", "not working", "won't work", "can't",
	}
)

// Thresholds are the inspector's tunables. Priority per value: env var
// > config.yml > hardcoded default (Python _cfg_float/_cfg_int).
type Thresholds struct {
	BreachThreshold    float64 // fraction of sessions with a signal that flags a breach
	EscalationMinHits  int     // escalation-language occurrences before the tone signal fires
	ContextChurnTokens int     // tokens_in above which stuck = churn
	AlignmentGood      float64
	AlignmentPoor      float64
}

// LoadThresholds resolves the five thresholds. cfg is the loaded config
// map (config.Load()); pass nil to skip the config layer.
func LoadThresholds(cfg map[string]any) Thresholds {
	return Thresholds{
		BreachThreshold:    cfgFloat(cfg, "inspector.breach_threshold", "INSPECTOR_BREACH_THRESHOLD", 0.30),
		EscalationMinHits:  cfgInt(cfg, "inspector.escalation_min_hits", "INSPECTOR_ESCALATION_MIN_HITS", 3),
		ContextChurnTokens: cfgInt(cfg, "inspector.context_churn_tokens", "INSPECTOR_CONTEXT_CHURN_TOKENS", 10000),
		AlignmentGood:      cfgFloat(cfg, "inspector.alignment_good", "INSPECTOR_ALIGNMENT_GOOD", 0.7),
		AlignmentPoor:      cfgFloat(cfg, "inspector.alignment_poor", "INSPECTOR_ALIGNMENT_POOR", 0.4),
	}
}

func cfgFloat(cfg map[string]any, cfgKey, envKey string, def float64) float64 {
	if v := os.Getenv(envKey); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	if cfg != nil {
		// config.Get with a float default also coerces JSON/YAML ints.
		return config.Get(cfg, cfgKey, def)
	}
	return def
}

func cfgInt(cfg map[string]any, cfgKey, envKey string, def int) int {
	if v := os.Getenv(envKey); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	if cfg != nil {
		return config.Get(cfg, cfgKey, def)
	}
	return def
}

// FrictionSignal mirrors Python's dataclass; evidence is an anonymized
// snippet — never raw goal content, max ~80 chars of the summary.
type FrictionSignal struct {
	SignalType string `json:"signal_type"`
	Severity   string `json:"severity"` // "low" | "medium" | "high"
	Count      int    `json:"count"`
	Evidence   string `json:"evidence"`
	SessionID  string `json:"session_id"`
}

// SessionQuality is one outcome's assessment.
type SessionQuality struct {
	SessionID          string           `json:"session_id"`
	SessionType        string           `json:"session_type"` // "loop" | "mission"
	Goal               string           `json:"goal"`         // truncated to 80 (privacy)
	Project            string           `json:"project"`
	Status             string           `json:"status"`
	GoalAlignmentScore float64          `json:"goal_alignment_score"`
	FrictionSignals    []FrictionSignal `json:"friction_signals"`
	DelightSignals     []string         `json:"delight_signals"`
	OverallQuality     string           `json:"overall_quality"` // "good" | "fair" | "poor"
	InspectorNotes     string           `json:"inspector_notes"`
	InspectedAt        string           `json:"inspected_at"`

	// AlignmentJudged is Go-internal (not serialized — Python tracks it
	// as a local): whether the score came from a judge or is the 0.7
	// display default.
	AlignmentJudged bool `json:"-"`
}

// InspectionReport is the aggregate written to memory/inspection-log.jsonl.
type InspectionReport struct {
	RunID               string           `json:"run_id"`
	InspectedSessions   int              `json:"inspected_sessions"`
	QualityDistribution map[string]int   `json:"quality_distribution"`
	TopFrictionSignals  []map[string]any `json:"top_friction_signals"`
	AlignmentScoreAvg   float64          `json:"alignment_score_avg"`
	Patterns            []string         `json:"patterns"`
	Suggestions         []string         `json:"suggestions"`
	ThresholdBreaches   []string         `json:"threshold_breaches"`
	ElapsedMS           int64            `json:"elapsed_ms"`
	GeneratedAt         string           `json:"generated_at"`
}

// Summary renders the operator-facing text block (Python summary()).
func (r InspectionReport) Summary() string {
	d := r.QualityDistribution
	lines := []string{
		"inspector run_id=" + r.RunID,
		fmt.Sprintf("sessions=%d", r.InspectedSessions),
		fmt.Sprintf("quality: good=%d fair=%d poor=%d", d["good"], d["fair"], d["poor"]),
		fmt.Sprintf("alignment_avg=%.2f", r.AlignmentScoreAvg),
		fmt.Sprintf("elapsed_ms=%d", r.ElapsedMS),
	}
	if len(r.Patterns) > 0 {
		lines = append(lines, "patterns:")
		for _, p := range head(r.Patterns, 3) {
			lines = append(lines, "  - "+p)
		}
	}
	if len(r.Suggestions) > 0 {
		lines = append(lines, "suggestions:")
		for _, s := range head(r.Suggestions, 3) {
			lines = append(lines, "  - "+s)
		}
	}
	if len(r.ThresholdBreaches) > 0 {
		lines = append(lines, "threshold_breaches: "+strings.Join(r.ThresholdBreaches, ", "))
	}
	return strings.Join(lines, "\n")
}

func head(s []string, n int) []string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func inspectionLogPath(ws string) string {
	return filepath.Join(ws, "memory", "inspection-log.jsonl")
}

func cadencePath(ws string) string {
	return filepath.Join(ws, "memory", "inspector_cadence.json")
}

func suggestionsPath(ws string) string {
	return filepath.Join(ws, "memory", "suggestions.jsonl")
}

// DeepPassLimit is the outcome window for the periodic deeper pass.
const DeepPassLimit = 200

// CadenceTick counts one run finalization toward the inspector cadence.
// Returns "none", "normal", or "deep" (every deepEvery-th firing — the
// larger cleanup pass; same hook, lower frequency, bigger limit, still
// no daemon). Single locked read-modify-write so concurrent
// finalizations can't both trigger; corrupt counter fields self-heal to
// 0 and negatives clamp (2026-08-08 review, both rounds). Callers must
// short-circuit on cadence <= 0 and must not count dry runs (Python
// contract — loop wiring, when a Go loop grows a finalize hook).
func CadenceTick(workspaceDir string, cadence, deepEvery int) (string, error) {
	mode := "none"
	err := record.LockedRMW(cadencePath(workspaceDir), func(old string) string {
		var state map[string]any
		if json.Unmarshal([]byte(old), &state) != nil || state == nil {
			state = map[string]any{}
		}
		count := clampCounter(state["runs_since_inspect"]) + 1
		firings := clampCounter(state["firings_since_deep"])
		if cadence > 0 && count >= cadence {
			count = 0
			firings++
			if deepEvery > 0 && firings >= deepEvery {
				mode = "deep"
				firings = 0
			} else {
				mode = "normal"
			}
		}
		out, _ := json.Marshal(map[string]any{
			"runs_since_inspect": count,
			"firings_since_deep": firings,
			"updated_at":         time.Now().UTC().Format(time.RFC3339Nano),
		})
		return string(out)
	})
	return mode, err
}

// clampCounter self-heals a corrupt cadence field: non-numeric resets to
// 0, negatives clamp to 0 (as corrupt as a string — see package doc).
func clampCounter(v any) int {
	switch n := v.(type) {
	case float64:
		if n < 0 {
			return 0
		}
		return int(n)
	case int:
		if n < 0 {
			return 0
		}
		return n
	}
	return 0
}

// DetectFrictionSignals runs the six heuristic detectors over one
// outcome row (no LLM). Evidence snippets clip the summary at 80 chars.
func DetectFrictionSignals(outcome map[string]any, th Thresholds) []FrictionSignal {
	status, _ := outcome["status"].(string)
	summary, _ := outcome["summary"].(string)
	sessionID := firstString(outcome, "outcome_id", "loop_id")
	tokensIn := clampCounter(outcome["tokens_in"])

	lower := strings.ToLower(summary)
	ev := func(prefix string) string { return prefix + clip80(summary) }
	var signals []FrictionSignal

	// error_events: stuck + LLM/API error mentioned
	if status == "stuck" && containsAny(lower,
		"llm call failed", "api", "timeout", "connection error", "rate limit") {
		signals = append(signals, FrictionSignal{
			SignalType: SignalErrorEvents, Severity: "high", Count: 1,
			Evidence: ev("stuck+error: "), SessionID: sessionID,
		})
	}

	// backtracking: stuck + repeated/same-outcome language
	if status == "stuck" && containsAny(lower,
		"repeated", "same outcome", "already tried", "same result", "loop detected") {
		signals = append(signals, FrictionSignal{
			SignalType: SignalBacktracking, Severity: "medium", Count: 1,
			Evidence: ev("stuck+repeated: "), SessionID: sessionID,
		})
	}

	// escalation_tone: stuck + "critical"/"failed" appearing N+ times
	if status == "stuck" {
		failCount := strings.Count(lower, "critical") + strings.Count(lower, "failed")
		if failCount >= th.EscalationMinHits {
			signals = append(signals, FrictionSignal{
				SignalType: SignalEscalationTone, Severity: "medium", Count: failCount,
				Evidence:  fmt.Sprintf("escalated language (%dx): %s", failCount, clip80(summary)),
				SessionID: sessionID,
			})
		}
	}

	// context_churn: lots of input tokens + stuck = too much context, no progress
	if status == "stuck" && tokensIn > th.ContextChurnTokens {
		signals = append(signals, FrictionSignal{
			SignalType: SignalContextChurn, Severity: "low", Count: 1,
			Evidence:  fmt.Sprintf("stuck with tokens_in=%d: %s", tokensIn, clip80(summary)),
			SessionID: sessionID,
		})
	}

	// platform_confusion: language about wrong context/environment
	// (deliberately NOT gated on stuck — Python fires this on any row)
	if containsAny(lower, "wrong platform", "not supported", "platform confusion", "wrong context") {
		signals = append(signals, FrictionSignal{
			SignalType: SignalPlatformConfusion, Severity: "medium", Count: 1,
			Evidence: ev("platform confusion: "), SessionID: sessionID,
		})
	}

	// abandoned_tool_flow: language about incomplete tool chains
	if status == "stuck" && containsAny(lower,
		"tool call", "abandoned", "incomplete", "tool chain", "mid-way") {
		signals = append(signals, FrictionSignal{
			SignalType: SignalAbandonedToolFlow, Severity: "low", Count: 1,
			Evidence: ev("abandoned tool flow: "), SessionID: sessionID,
		})
	}

	return signals
}

func containsAny(haystack string, needles ...string) bool {
	for _, n := range needles {
		if strings.Contains(haystack, n) {
			return true
		}
	}
	return false
}

func clip80(s string) string {
	r := []rune(s)
	if len(r) > 80 {
		r = r[:80]
	}
	return string(r)
}

func firstString(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if s, ok := m[k].(string); ok && s != "" {
			return s
		}
	}
	return ""
}

// AssessGoalAlignment scores goal-vs-result on 0.0-1.0. nil adapter →
// nil score: NO judgment happened (the fail-open census fix — callers
// pick their own display default and must track judged-ness). Parse
// failures and adapter errors return 0.5 (Python's both except arms).
func AssessGoalAlignment(ctx context.Context, adapter llm.Adapter, goal, resultSummary string) *float64 {
	if adapter == nil {
		return nil
	}
	half := 0.5
	// The summary window is the equal-or-above backstop over per-lane
	// upstream bounds, not a fresh measurement (see the budget's Why);
	// the goal clip is an identity preview, ported at Python's literal.
	prompt := fmt.Sprintf(
		"Goal: %s\nResult: %s\n\n"+
			"On a scale of 0.0 to 1.0, how well does this result match the stated goal? "+
			"Reply ONLY with a number.",
		budget.Clip(goal, 200), budget.SummaryJudgeWindow.Clip(resultSummary))
	resp, err := adapter.Complete(ctx, []llm.Message{{Role: "user", Content: prompt}},
		llm.Options{MaxTokens: 16, Temperature: 0.0, Purpose: "goal alignment score"})
	if err != nil || resp == nil {
		return &half
	}
	// Python is `float(resp.content.strip())` inside a try that returns
	// 0.5 on ValueError/TypeError. ParseFloat(TrimSpace(...)) is not that
	// function, three ways, all measured:
	//
	//	"\u001c0.8"  py 0.8  (str.strip covers U+001C..U+001F)  go was 0.5
	//	"٠.٨"        py 0.8  (float() takes any Unicode digit)   go was 0.5
	//	"1e400"      py inf  (no exception)                      go was 0.5
	//
	// and one that is worse than a wrong number: ParseFloat ACCEPTS
	// "nan"/"inf"/"-inf" with a nil error, so a judge reply of "nan"
	// became the report's AlignmentScoreAvg, and saveReport's
	// json.Marshal then returns "json: unsupported value: NaN" — the
	// whole batch's inspection row never written. That is verbatim the
	// mission-r5 HIGH (StampVerdict + NaN) at a site the r5 sweep did not
	// reach (adversarial mission-r6 MEDIUM).
	//
	// SafeFloat is float()-parity plus this port's standing non-finite
	// stance (pyjson.RefuseNonFinite's). "Every FINITE input now agrees
	// with CPython exactly" is what this comment said for one round, and
	// mission-r7 falsified it with "0x1p-2" — finite, accepted by
	// ParseFloat, refused by float(). The hex rejection now lives in
	// pyval.toFloat, so the claim holds, but it is a MEASUREMENT and the
	// next round should re-run it rather than read it. The non-finite case is a NAMED divergence:
	// CPython stores NaN/Infinity in inspection-log.jsonl and its own
	// reader accepts them, Go stores the 0.5 default. Owed to the Python
	// side, where a safe_float here would make both runtimes agree — and
	// far better than losing the row.
	f := pyval.SafeFloat(pytext.Strip(resp.Content), 0.5, nil, nil)
	return &f
}

// inspectorNotesSystem is Python _INSPECTOR_NOTES_SYSTEM verbatim.
const inspectorNotesSystem = `You are a quality inspector for an autonomous AI system. Provide a brief one-sentence
quality assessment of this agent session. Be specific and factual. No fluff.

You MUST end your response with exactly one of these verdicts on its own line:
VERDICT: **PROCEED** — output meets quality bar, no rework needed
VERDICT: **RETRY** — output has fixable issues, rework the last step
VERDICT: **ABORT** — output is fundamentally wrong, escalate to human
No hedging. No "it depends". Commit to a verdict.
`

// isVerdictPending ports outcome_policy.is_verdict_pending: an agenda
// row written before closure verification carries
// lesson_extraction_status="deferred" until that contract finishes; an
// unjudged row in that state is not success evidence — it may be the
// residue of a failed closure-verdict write.
func isVerdictPending(outcome map[string]any) bool {
	_, judged := outcome["goal_achieved"]
	if judged && outcome["goal_achieved"] != nil {
		return false
	}
	status, _ := outcome["lesson_extraction_status"].(string)
	return status == "deferred"
}

// goalAchieved returns the row's tri-state verdict: (value, judged).
// Key-presence ≈ Python's `is not None` because every writer pops nulls;
// an explicit JSON null still normalizes to unjudged (record.go r5).
//
// A present-but-non-bool value (a corrupt or foreign-writer row carrying
// a string "false", a number, etc.) is treated as judged-NOT-achieved,
// NOT as unjudged. This is the safe direction for a quality gate: a
// malformed verdict must not let a run clear the fair-caps and read
// "good" (r1 review Architect #4). Python's `achieved is False` identity
// check lets such a value slip to unjudged — a HARDENING DIVERGENCE here,
// flagged as a backport-correction candidate in PORT.md.
func goalAchieved(outcome map[string]any) (bool, bool) {
	v, present := outcome["goal_achieved"]
	if !present || v == nil {
		return false, false
	}
	b, ok := v.(bool)
	if !ok {
		return false, true // malformed verdict → judged-false (conservative)
	}
	return b, true
}

// InspectSession assesses one outcome row. Read-only; adapter optional
// (heuristics work without one — the production norm).
func InspectSession(ctx context.Context, outcome map[string]any, adapter llm.Adapter, th Thresholds) SessionQuality {
	sessionID := firstString(outcome, "outcome_id", "loop_id")
	if sessionID == "" {
		sessionID = record.NewID()[:8]
	}
	goal, _ := outcome["goal"].(string)
	project, _ := outcome["project"].(string)
	status, _ := outcome["status"].(string)
	if status == "" {
		status = "done"
	}
	summary, _ := outcome["summary"].(string)

	// Python truthiness: an empty loop_id (which Go's own NOW-lane rows
	// can carry) does not make the session a "loop" by evidence — but
	// the fallback is "loop" anyway, so only a mission_id row differs.
	sessionType := "loop"
	if lid, _ := outcome["loop_id"].(string); lid == "" {
		if mid, _ := outcome["mission_id"].(string); mid != "" {
			sessionType = "mission"
		}
	}

	frictionSignals := DetectFrictionSignals(outcome, th)

	// Alignment: nil = never judged (adapter-less). Keep 0.7 as display
	// value but track judgment separately so an unjudged score can't
	// clear the AlignmentGood bar.
	rawAlignment := AssessGoalAlignment(ctx, adapter, goal, summary)
	alignmentJudged := rawAlignment != nil
	alignmentScore := 0.7
	if alignmentJudged {
		alignmentScore = *rawAlignment
	}

	// Delight signals — verdict-preferred (SF-2): goal_achieved
	// true/false is the judged verdict; absent = unjudged. A judged
	// goal-NOT-achieved run must never count as a success.
	achieved, achievedJudged := goalAchieved(outcome)
	verdictPending := isVerdictPending(outcome)
	var delight []string
	if status == "done" && alignmentJudged && alignmentScore >= th.AlignmentGood &&
		!(achievedJudged && !achieved) && !verdictPending {
		delight = append(delight, "task_completed_successfully")
	}
	if achievedJudged && achieved {
		delight = append(delight, "goal_verified_achieved")
	}

	// Overall quality with the three fair-caps (see package doc).
	hasHighFriction := false
	for _, s := range frictionSignals {
		if s.Severity == "high" {
			hasHighFriction = true
			break
		}
	}
	quality := "fair"
	switch {
	case alignmentScore >= th.AlignmentGood && !hasHighFriction:
		quality = "good"
	case alignmentScore < th.AlignmentPoor || hasHighFriction:
		quality = "poor"
	}
	if achievedJudged && !achieved && quality == "good" {
		quality = "fair"
	}
	if verdictPending && quality == "good" {
		quality = "fair"
	}
	if !alignmentJudged && quality == "good" {
		quality = "fair"
	}

	// Optional brief LLM notes.
	notes := ""
	if adapter != nil {
		verdictTag := ""
		if achievedJudged {
			if achieved {
				verdictTag = " (goal verified achieved)"
			} else {
				verdictTag = " (goal judged NOT achieved)"
			}
		}
		sigTypes := make([]string, 0, len(frictionSignals))
		for _, s := range frictionSignals {
			sigTypes = append(sigTypes, s.SignalType)
		}
		notePrompt := fmt.Sprintf(
			"Session status: %s%s\nGoal: %s\nResult: %s\nFriction signals: [%s]\nAlignment score: %.2f",
			status, verdictTag, budget.Clip(goal, 100),
			budget.SummaryJudgeWindow.Clip(summary),
			strings.Join(sigTypes, " "), alignmentScore)
		resp, err := adapter.Complete(ctx, []llm.Message{
			{Role: "system", Content: inspectorNotesSystem},
			{Role: "user", Content: notePrompt},
		}, llm.Options{MaxTokens: 128, Temperature: 0.2, Purpose: "session quality notes"})
		if err == nil && resp != nil {
			notes = strings.TrimSpace(resp.Content)
			if r := []rune(notes); len(r) > 300 {
				notes = string(r[:300])
			}
		}
	}

	return SessionQuality{
		SessionID:          sessionID,
		SessionType:        sessionType,
		Goal:               clip80(goal), // privacy: truncate goal
		Project:            project,
		Status:             status,
		GoalAlignmentScore: alignmentScore,
		FrictionSignals:    frictionSignals,
		DelightSignals:     delight,
		OverallQuality:     quality,
		InspectorNotes:     notes,
		InspectedAt:        time.Now().UTC().Format(time.RFC3339Nano),
		AlignmentJudged:    alignmentJudged,
	}
}

// patternSystem is Python _PATTERN_SYSTEM verbatim.
const patternSystem = `You are a quality inspector for an autonomous AI system.
Analyze these session quality results and identify:
1. Cross-session patterns (what keeps going wrong?)
2. Improvement suggestions (concrete, actionable)
3. Any signals that have crossed a threshold (appearing in >30% of sessions)

Output JSON: {"patterns": [...], "suggestions": [...], "threshold_breaches": [...]}
`

// analyzePatternsWithLLM asks for cross-session patterns. Returns
// (patterns, suggestions, breaches) — all empty without an adapter or
// on any failure (analysis is enhancement, never a gate).
func analyzePatternsWithLLM(ctx context.Context, qualities []SessionQuality,
	signalCounts map[string]int, adapter llm.Adapter) ([]string, []string, []string) {
	if adapter == nil || len(qualities) == 0 {
		return nil, nil, nil
	}
	dist := map[string]int{"good": 0, "fair": 0, "poor": 0}
	for _, sq := range qualities {
		dist[sq.OverallQuality]++
	}
	counts, _ := json.Marshal(signalCounts)
	lines := []string{
		fmt.Sprintf("Total sessions inspected: %d", len(qualities)),
		fmt.Sprintf("Quality: good=%d fair=%d poor=%d", dist["good"], dist["fair"], dist["poor"]),
		"Signal counts: " + string(counts),
		"",
		"Sample poor sessions:",
	}
	poor := 0
	for _, sq := range qualities {
		if sq.OverallQuality != "poor" || poor >= 5 {
			continue
		}
		poor++
		sigs := make([]string, 0, len(sq.FrictionSignals))
		for _, s := range sq.FrictionSignals {
			sigs = append(sigs, s.SignalType)
		}
		// Python renders alignment=<overall_quality> here — a fork-point
		// quirk ported as-is (the label is wrong, the value is the
		// quality bucket; fixing it would diverge the prompt).
		lines = append(lines, fmt.Sprintf("  - [%s] alignment=%s friction=[%s]",
			sq.Status, sq.OverallQuality, strings.Join(sigs, ",")))
	}
	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: patternSystem},
		{Role: "user", Content: strings.Join(lines, "\n")},
	}, llm.Options{MaxTokens: 1024, Temperature: 0.2, Purpose: "cross-session patterns"})
	if err != nil || resp == nil {
		return nil, nil, nil
	}
	data, jerr := jsonx.Object(resp.Content)
	if jerr != nil {
		return nil, nil, nil
	}
	return stringList(data["patterns"]), stringList(data["suggestions"]),
		stringList(data["threshold_breaches"])
}

func stringList(v any) []string {
	items, ok := v.([]any)
	if !ok {
		return nil
	}
	var out []string
	for _, it := range items {
		if s, ok := it.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// Run executes one inspection cycle over recent outcomes and persists
// the report (plus any LLM suggestions) unless dryRun. Read-only over
// the loops it observes; the only writes are its own report and
// suggestion rows.
func Run(ctx context.Context, workspaceDir string, adapter llm.Adapter,
	limit int, dryRun bool, th Thresholds) InspectionReport {
	runID := record.NewID()[:8]
	started := time.Now()

	outcomes, err := record.LoadOutcomes(workspaceDir, limit)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[inspector] failed to load outcomes: %v\n", err)
	}
	report := InspectionReport{
		RunID:               runID,
		QualityDistribution: map[string]int{"good": 0, "fair": 0, "poor": 0},
		GeneratedAt:         time.Now().UTC().Format(time.RFC3339Nano),
	}
	if len(outcomes) == 0 {
		report.ElapsedMS = time.Since(started).Milliseconds()
		if !dryRun {
			if serr := saveReport(workspaceDir, report); serr != nil {
				fmt.Fprintf(os.Stderr, "[inspector] failed to save report: %v\n", serr)
			}
		}
		return report
	}

	sessionAdapter := adapter
	if dryRun {
		sessionAdapter = nil // dry run: heuristics only, no LLM spend
	}
	var qualities []SessionQuality
	for _, o := range outcomes {
		qualities = append(qualities, InspectSession(ctx, o, sessionAdapter, th))
	}

	for _, sq := range qualities {
		report.QualityDistribution[sq.OverallQuality]++
	}

	// Aggregate friction signals: total count + max severity per type.
	sevRank := map[string]int{"low": 0, "medium": 1, "high": 2}
	signalCounts := map[string]int{}
	signalSevMax := map[string]string{}
	for _, sq := range qualities {
		for _, sig := range sq.FrictionSignals {
			signalCounts[sig.SignalType] += sig.Count
			if sevRank[sig.Severity] > sevRank[signalSevMax[sig.SignalType]] {
				signalSevMax[sig.SignalType] = sig.Severity
			}
		}
	}
	type sigRow struct {
		name  string
		count int
	}
	var rows []sigRow
	for k, v := range signalCounts {
		rows = append(rows, sigRow{k, v})
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].count != rows[j].count {
			return rows[i].count > rows[j].count
		}
		return rows[i].name < rows[j].name // deterministic tie-break (map order isn't)
	})
	for i, r := range rows {
		if i >= 5 {
			break
		}
		sev := signalSevMax[r.name]
		if sev == "" {
			sev = "low"
		}
		report.TopFrictionSignals = append(report.TopFrictionSignals, map[string]any{
			"signal_type": r.name, "count": r.count, "severity": sev,
		})
	}

	total := 0.0
	for _, sq := range qualities {
		total += sq.GoalAlignmentScore
	}
	report.InspectedSessions = len(qualities)
	report.AlignmentScoreAvg = round3(total / float64(len(qualities)))

	// Heuristic threshold breaches: signal present in more than
	// BreachThreshold of sessions (session-presence fraction, not raw
	// count — one loud session must not flag a fleet-wide breach).
	n := len(qualities)
	var breaches []string
	for sigType := range signalCounts {
		with := 0
		for _, sq := range qualities {
			for _, s := range sq.FrictionSignals {
				if s.SignalType == sigType {
					with++
					break
				}
			}
		}
		if float64(with)/float64(n) > th.BreachThreshold {
			breaches = append(breaches, sigType)
		}
	}

	if !dryRun && adapter != nil {
		patterns, suggestions, llmBreaches := analyzePatternsWithLLM(ctx, qualities, signalCounts, adapter)
		report.Patterns = patterns
		report.Suggestions = suggestions
		breaches = dedupeStrings(append(breaches, llmBreaches...))
	}
	sort.Strings(breaches) // Python set() order is arbitrary; ours is stable
	report.ThresholdBreaches = breaches
	report.ElapsedMS = time.Since(started).Milliseconds()

	if !dryRun {
		if serr := saveReport(workspaceDir, report); serr != nil {
			fmt.Fprintf(os.Stderr, "[inspector] failed to save report: %v\n", serr)
		}
		if len(report.Suggestions) > 0 {
			if serr := saveSuggestions(workspaceDir, report.Suggestions); serr != nil {
				fmt.Fprintf(os.Stderr, "[inspector] failed to save suggestions: %v\n", serr)
			}
		}
	}
	return report
}

func dedupeStrings(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

// round3 was float64(int64(f*1000+0.5))/1000 — round half-UP, which is
// not even Python's round-half-to-even, let alone its exact-value
// rounding. Measured: round(0.6675, 3) is 0.667 in CPython and was 0.668
// here; same for 0.0625, 0.8885, 0.1235. The value is written as
// alignment_score_avg into inspection-log.jsonl, which both runtimes
// read (adversarial mission-r6 MEDIUM).
func round3(f float64) float64 { return pyval.Round(f, 3) }

// reportRow is InspectionReport as json.dumps writes it: to_dict()'s key
// order, `", "`/`": "` separators, no HTML escaping, ensure_ascii on.
// json.Marshal of the struct gets the ORDER right (declaration order
// matches to_dict) and the other three wrong — and quality_distribution
// is a Go map, whose "good"/"fair"/"poor" came back sorted as
// fair/good/poor. Both runtimes read inspection-log.jsonl (adversarial
// mission-r7 HIGH).
func reportRow(report InspectionReport) pyval.Obj {
	d := report.QualityDistribution
	return pyval.Obj{
		{Key: "run_id", Val: report.RunID},
		{Key: "inspected_sessions", Val: report.InspectedSessions},
		{Key: "quality_distribution", Val: pyval.Obj{
			{Key: "good", Val: d["good"]},
			{Key: "fair", Val: d["fair"]},
			{Key: "poor", Val: d["poor"]},
		}},
		{Key: "top_friction_signals", Val: pyval.FromPlain(report.TopFrictionSignals)},
		{Key: "alignment_score_avg", Val: report.AlignmentScoreAvg},
		{Key: "patterns", Val: pyval.FromPlain(report.Patterns)},
		{Key: "suggestions", Val: pyval.FromPlain(report.Suggestions)},
		{Key: "threshold_breaches", Val: pyval.FromPlain(report.ThresholdBreaches)},
		{Key: "elapsed_ms", Val: report.ElapsedMS},
		{Key: "generated_at", Val: report.GeneratedAt},
	}
}

func saveReport(workspaceDir string, report InspectionReport) error {
	line, err := pyval.DumpsCompactPy(reportRow(report))
	if err != nil {
		return err
	}
	raw := []byte(line)
	path := inspectionLogPath(workspaceDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return record.AppendRawLine(path, raw)
}

// saveSuggestions feeds inspector findings into the evolver pipeline as
// minimal suggestion rows (the Python 9-field schema, byte-compatible
// keys). One locked batch append — suggestions.jsonl is shared with the
// evolver, and interleaving with its writes tore lines.
func saveSuggestions(workspaceDir string, suggestions []string) error {
	if len(suggestions) == 0 {
		return nil
	}
	p := suggestionsPath(workspaceDir)
	now := time.Now().UTC().Format(time.RFC3339Nano)
	var lines []string
	for i, text := range suggestions {
		row := pyval.Obj{
			{Key: "suggestion_id", Val: fmt.Sprintf("insp-%s-%02d", record.NewID()[:6], i)},
			{Key: "category", Val: "inspection_finding"},
			{Key: "target", Val: "all"},
			{Key: "suggestion", Val: text},
			{Key: "failure_pattern", Val: "inspector cross-session analysis"},
			{Key: "confidence", Val: 0.7},
			{Key: "outcomes_analyzed", Val: 0},
			{Key: "generated_at", Val: now},
			{Key: "applied", Val: false},
		}
		// The evolver reads this file in BOTH runtimes; a suggestion
		// containing "->" or a non-ASCII character was written here in a
		// spelling no CPython writer produces (mission-r7 HIGH).
		line, err := pyval.DumpsCompactPy(row)
		if err != nil {
			return err
		}
		lines = append(lines, line)
	}
	// The dir must exist before Locked can open the lock file beside p.
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	return record.Locked(p, func() error {
		f, err := os.OpenFile(p, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return err
		}
		defer f.Close()
		for _, l := range lines {
			if _, err := f.WriteString(l + "\n"); err != nil {
				return err
			}
		}
		return nil
	})
}

// GetLatestInspection returns the newest report row, or nil.
func GetLatestInspection(workspaceDir string) *InspectionReport {
	raw, err := os.ReadFile(inspectionLogPath(workspaceDir))
	if err != nil {
		return nil
	}
	lines := strings.Split(string(raw), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		var r InspectionReport
		if json.Unmarshal([]byte(line), &r) == nil {
			return &r
		}
		return nil // newest row is torn — same as Python's parse-fail → None
	}
	return nil
}

// FrictionSummary renders a brief human-readable summary of the latest
// inspection (Python get_friction_summary — heartbeat diagnosis context
// in Python; here the CLI's status surface until a heartbeat exists).
func FrictionSummary(workspaceDir string) string {
	report := GetLatestInspection(workspaceDir)
	if report == nil {
		return ""
	}
	if report.InspectedSessions == 0 {
		return "Inspector: no sessions inspected yet."
	}
	d := report.QualityDistribution
	lines := []string{fmt.Sprintf(
		"Inspector (%s): %d sessions — good=%d fair=%d poor=%d alignment_avg=%.2f",
		report.RunID, report.InspectedSessions, d["good"], d["fair"], d["poor"],
		report.AlignmentScoreAvg)}
	if len(report.TopFrictionSignals) > 0 {
		top := report.TopFrictionSignals[0]
		lines = append(lines, fmt.Sprintf("Top friction: %v (count=%v severity=%v)",
			top["signal_type"], top["count"], top["severity"]))
	}
	if len(report.ThresholdBreaches) > 0 {
		lines = append(lines, "Threshold breaches: "+strings.Join(report.ThresholdBreaches, ", "))
	}
	return strings.Join(lines, "\n")
}
