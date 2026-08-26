package introspect

import (
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/metrics"
	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// StepProfile is introspect.StepProfile — one step's execution shape, built
// from a step_done or step_stuck event.
//
// NAMED DIVERGENCE (numeric stamps). Python's fields are whatever
// `e.get("tokens_in", 0)` returned, so a stamp of `1.5` makes `tokens` a
// float and renders as "1.5" in evidence, and a stamp of a string makes
// `tokens_in + tokens_out` a CONCATENATION that later crashes `sum()`.
// The port coerces integral numbers and treats everything else as the
// default. Every real writer (`step_exec`) stamps integers; the divergence
// is pinned by a test rather than left implicit, because the crash paths
// are the kind of thing that only shows up on a corrupt store.
type StepProfile struct {
	StepIdx         int
	Text            string
	Status          string
	Tokens          int // total volume (input incl. cache reads + output)
	ElapsedMS       int
	EventType       string
	CacheReadTokens int // input tokens served from cache (~0.1x cost)
	TokensIn        int // total input volume (incl. cache reads)
	TokensOut       int
	Model           string
	// ToolPathologies are the model—tool edge classifications stamped at
	// execution time and carried on the step_done event. Ordered, because
	// they are rendered through Python's str() into evidence.
	ToolPathologies []pyval.Obj
}

// FreshTokens is the cost-relevant token count: total minus cache hits.
// This is what the bloat/growth alarms compare — a worker re-reading a
// cached file bills at ~0.1x and must not read as a spike.
func (p StepProfile) FreshTokens() int {
	if n := p.Tokens - p.CacheReadTokens; n > 0 {
		return n
	}
	return 0
}

// CostUSD is the cache-aware dollar cost of this step — the right unit for
// absolute spend guards. Python passes `self.model or None`, which reaches
// the same "" lookup miss.
func (p StepProfile) CostUSD() float64 {
	return metrics.EstimateCost(p.TokensIn, p.TokensOut, p.Model, p.CacheReadTokens)
}

// LoopDiagnosis is introspect.LoopDiagnosis.
type LoopDiagnosis struct {
	LoopID         string
	FailureClass   string
	Severity       string // "info" | "warning" | "critical"
	Evidence       []string
	Recommendation string
	TokenProfile   []pyval.Obj
	TimingProfile  []pyval.Obj
	TotalTokens    int
	TotalElapsedMS int
	StepsDone      int
	StepsBlocked   int
	StepsTotal     int
	Project        string
	// RecordedAt is stamped at PERSISTENCE, not construction — the axis the
	// per-class rate windows sort on. Empty until saved.
	RecordedAt string
}

// Summary is LoopDiagnosis.summary().
func (d LoopDiagnosis) Summary() string {
	return fmt.Sprintf("[%s] severity=%s steps=%d/%ddone tokens=%d elapsed=%dms | %s",
		d.FailureClass, d.Severity, d.StepsDone, d.StepsTotal,
		d.TotalTokens, d.TotalElapsedMS, d.Recommendation)
}

// DiagnosisKeys is to_dict()'s key order — the order a row lands in
// diagnoses.jsonl, because Python's json.dumps writes a dict in insertion
// order and this store is READ by the Python runtime.
var DiagnosisKeys = []string{
	"loop_id", "failure_class", "severity", "evidence", "recommendation",
	"total_tokens", "total_elapsed_ms", "steps_done", "steps_blocked",
	"steps_total", "project", "recorded_at",
}

// ToDict is LoopDiagnosis.to_dict().
//
// It deliberately omits token_profile and timing_profile: those are
// in-memory response detail, and the Python writer does not persist them.
func (d LoopDiagnosis) ToDict() map[string]any {
	// Evidence is passed through even when nil. pyjson.Ordered renders a
	// nil slice as `[]` and not `null`, pinned by a test whose whole stated
	// purpose is to let call sites stop re-checking it. A local `if ev ==
	// nil` here would be a second guard for the same thing, and a second
	// guard is what makes a wrong bound unobservable: the mutant that
	// removes it survived a 45-mutant battery precisely because pyjson was
	// already correct underneath.
	ev := d.Evidence
	return map[string]any{
		"loop_id":          d.LoopID,
		"failure_class":    d.FailureClass,
		"severity":         d.Severity,
		"evidence":         ev,
		"recommendation":   d.Recommendation,
		"total_tokens":     d.TotalTokens,
		"total_elapsed_ms": d.TotalElapsedMS,
		"steps_done":       d.StepsDone,
		"steps_blocked":    d.StepsBlocked,
		"steps_total":      d.StepsTotal,
		"project":          d.Project,
		"recorded_at":      d.RecordedAt,
	}
}

// MarshalRow renders the persisted line the way Python's json.dumps does —
// key order, separators, float spelling and ensure_ascii included.
func (d LoopDiagnosis) MarshalRow() (string, error) {
	return pyjson.Ordered(d.ToDict(), DiagnosisKeys)
}

// evGet is `e.get(key, default)` — PRESENCE, so a present null is None.
func evGet(e pyval.Obj, key string, def any) any {
	if v, ok := e.Get(key); ok {
		return v
	}
	return def
}

// evInt coerces an event field the way the numeric divergence above
// describes: an integral number becomes an int, everything else becomes 0.
func evInt(v any) int {
	switch n := pyval.Plain(v).(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		if n == float64(int(n)) {
			return int(n)
		}
	}
	return 0
}

func evStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// BuildStepProfiles is introspect._build_step_profiles.
func BuildStepProfiles(events []pyval.Obj) []StepProfile {
	var profiles []StepProfile
	for _, e := range events {
		et := evStr(evGet(e, "event_type", nil))
		if et != "step_done" && et != "step_stuck" {
			continue
		}
		tin := evInt(evGet(e, "tokens_in", 0))
		tout := evInt(evGet(e, "tokens_out", 0))
		var paths []pyval.Obj
		// `list(e.get("tool_pathologies") or [])` — a TRUTHINESS gate, so a
		// present null, an empty list and absence all mean the same thing.
		if raw, ok := evGet(e, "tool_pathologies", nil).(pyval.List); ok {
			for _, item := range raw {
				if o, ok := item.(pyval.Obj); ok {
					paths = append(paths, o)
				}
			}
		}
		profiles = append(profiles, StepProfile{
			StepIdx:         evInt(evGet(e, "step_idx", 0)),
			Text:            evStr(evGet(e, "step", "")),
			Status:          evStr(evGet(e, "status", "")),
			Tokens:          tin + tout,
			ElapsedMS:       evInt(evGet(e, "elapsed_ms", 0)),
			EventType:       et,
			CacheReadTokens: evInt(evGet(e, "cache_read_tokens", 0)),
			TokensIn:        tin,
			TokensOut:       tout,
			Model:           evStr(evGet(e, "model", "")),
			ToolPathologies: paths,
		})
	}
	return profiles
}

// researchKeywords gate the token_explosion exemption. Substring matches on
// the LOWERCASED goal, not word boundaries — "researching" counts.
var researchKeywords = []string{"research", "summarize", "fetch", "analyze", "extract"}

// Diagnose is the pure half of introspect.diagnose_loop: everything from
// the loaded events to the LoopDiagnosis, with no I/O.
//
// The heuristics run MOST SPECIFIC FIRST and the order is load-bearing in a
// way that is easy to lose. Two of them are deliberately NOT gated on the
// class still being "healthy" — adapter_timeout (2) can overwrite
// setup_failure (1), and budget_exhaustion (7) appends its evidence
// whatever the class is. A port that gated everything uniformly would look
// tidier and answer differently.
func Diagnose(events []pyval.Obj, loopID, project string) LoopDiagnosis {
	if len(events) == 0 {
		return LoopDiagnosis{
			LoopID:       loopID,
			FailureClass: "artifact_missing",
			Severity:     "warning",
			Evidence:     []string{"No events found for this loop_id"},
			Recommendation: "Check that events.jsonl is being written " +
				"(observe.write_event)",
			Project: project,
		}
	}

	profiles := BuildStepProfiles(events)
	var done, blocked []StepProfile
	totalTokens, totalElapsed := 0, 0
	for _, p := range profiles {
		if p.Status == "done" {
			done = append(done, p)
		}
		if p.Status == "stuck" || p.Status == "blocked" {
			blocked = append(blocked, p)
		}
		totalTokens += p.Tokens
		totalElapsed += p.ElapsedMS
	}

	// `loop_status` is read in Python and never used; not carried.
	stuckReason := ""
	for _, e := range events {
		if evStr(evGet(e, "event_type", nil)) == "loop_done" {
			// NAMED DIVERGENCE: Python takes `.get("detail","")` and later
			// runs `"max_iterations" in stuck_reason`, which raises
			// TypeError when detail is None or a number. The port reads a
			// non-string detail as absent. CPython crashes; the port
			// diagnoses. Pinned rather than replicated.
			stuckReason = evStr(evGet(e, "detail", ""))
			break
		}
	}

	var evidence []string
	failureClass, severity, recommendation := "healthy", "info", ""

	// 1. Setup failure: first step blocks with zero tokens, fast.
	if len(profiles) > 0 &&
		(profiles[0].Status == "stuck" || profiles[0].Status == "blocked") &&
		profiles[0].Tokens == 0 && profiles[0].ElapsedMS < 5000 {
		failureClass, severity = "setup_failure", "critical"
		evidence = append(evidence,
			fmt.Sprintf("Step 1 blocked with 0 tokens in %dms", profiles[0].ElapsedMS),
			fmt.Sprintf("Step text: %s", pyval.Clip(profiles[0].Text, 100)))
		recommendation = "Check adapter resolution, import errors, or constraint false positives"
	}

	// 2. Adapter timeout. NOT gated on healthy — it overwrites (1).
	for _, p := range blocked {
		if p.Tokens == 0 && p.ElapsedMS > 60000 {
			failureClass, severity = "adapter_timeout", "critical"
			evidence = append(evidence, fmt.Sprintf(
				"Step %d blocked with 0 tokens after %dms", p.StepIdx, p.ElapsedMS))
			recommendation = "Subprocess adapter likely timed out. Consider API adapter or smaller step scope."
			break
		}
	}

	// 3. Constraint false positive: blocked with 0 tokens but fast.
	if failureClass == "healthy" {
		var fp []StepProfile
		for _, p := range blocked {
			if p.Tokens == 0 && p.ElapsedMS < 1000 {
				fp = append(fp, p)
			}
		}
		if len(fp) >= 2 {
			failureClass, severity = "constraint_false_positive", "warning"
			evidence = append(evidence, fmt.Sprintf(
				"%d steps blocked with 0 tokens in < 1s each", len(fp)))
			for i, p := range fp {
				if i >= 3 {
					break
				}
				evidence = append(evidence, fmt.Sprintf("  Step %d: %s",
					p.StepIdx, pyval.Clip(p.Text, 80)))
			}
			recommendation = "Review constraint tier patterns — likely overly broad for natural language steps"
		}
	}

	// 4. Decomposition too broad — FRESH tokens, or wall clock with a
	// token floor under it.
	if failureClass == "healthy" {
		for _, p := range profiles {
			if p.FreshTokens() > BroadStepTokenLimit {
				failureClass, severity = "decomposition_too_broad", "warning"
				evidence = append(evidence, fmt.Sprintf(
					"Step %d did %d fresh tokens (%dms; cache reads excluded)",
					p.StepIdx, p.FreshTokens(), p.ElapsedMS),
					fmt.Sprintf("Step text: %s", pyval.Clip(p.Text, 100)))
				recommendation = "Decompose further — cap code review at 3-5 files / ~2000 lines per step"
				break
			}
			if p.ElapsedMS > BroadStepElapsedMS && p.FreshTokens() > BroadStepElapsedMinTokens {
				failureClass, severity = "decomposition_too_broad", "warning"
				evidence = append(evidence, fmt.Sprintf(
					"Step %d took %dms with %d fresh tokens",
					p.StepIdx, p.ElapsedMS, p.FreshTokens()),
					fmt.Sprintf("Step text: %s", pyval.Clip(p.Text, 100)))
				recommendation = "Step is too large — split into smaller focused substeps"
				break
			}
		}
	}

	// 5. Token explosion between consecutive steps, with a research exemption.
	if failureClass == "healthy" && len(profiles) >= 3 {
		loopGoal := ""
		for _, e := range events {
			if evStr(evGet(e, "event_type", nil)) == "loop_start" {
				// Python calls .lower() on the raw value, so a non-string
				// goal raises AttributeError. Same named divergence as
				// `detail` above.
				loopGoal = pytext.Lower(evStr(evGet(e, "goal", "")))
				break
			}
		}
		isResearch := false
		for _, kw := range researchKeywords {
			if strings.Contains(loopGoal, kw) {
				isResearch = true
				break
			}
		}
		for i := 1; i < len(profiles); i++ {
			prev := profiles[i-1].FreshTokens()
			curr := profiles[i].FreshTokens()
			if !(prev > 1000 && float64(curr) > float64(prev)*TokenExplosionRatio) {
				continue
			}
			ratio := float64(curr) / float64(prev)
			if isResearch && len(blocked) == 0 && float64(curr) < float64(prev)*6 {
				// Noted, NOT classified — and it breaks, so a later real
				// explosion in the same loop is never reached.
				evidence = append(evidence, fmt.Sprintf(
					"Step %d: %d fresh tokens (vs %d for step %d — %.1fx growth, "+
						"expected for research task)",
					profiles[i].StepIdx, curr, prev, profiles[i-1].StepIdx, ratio))
				break
			}
			failureClass, severity = "token_explosion", "warning"
			evidence = append(evidence, fmt.Sprintf(
				"Step %d: %d fresh tokens (vs %d for step %d — %.1fx growth; "+
					"cache reads excluded)",
				profiles[i].StepIdx, curr, prev, profiles[i-1].StepIdx, ratio))
			recommendation = "Fresh-token growth is real compute, not cache re-reads. The cost is " +
				"inside the worker subprocess's own tool loop (re-reading/rewriting a " +
				"growing artifact). Steer the worker toward patch/diff edits + slice " +
				"reads, or accept it as intrinsic for file-heavy build steps."
			break
		}
	}

	// 5b. Cost spike (absolute, cache-aware).
	if failureClass == "healthy" && len(profiles) > 0 {
		// `max(profiles, key=...)` keeps the FIRST maximum on a tie.
		costliest := profiles[0]
		loopCost := 0.0
		for i, p := range profiles {
			c := p.CostUSD()
			loopCost += c
			if i > 0 && c > costliest.CostUSD() {
				costliest = p
			}
		}
		if costliest.CostUSD() >= StepCostWarnUSD || loopCost >= LoopCostWarnUSD {
			failureClass, severity = "cost_spike", "warning"
			if costliest.CostUSD() >= StepCostWarnUSD {
				model := costliest.Model
				if model == "" {
					model = "default"
				}
				evidence = append(evidence, fmt.Sprintf(
					"Step %d cost $%.2f (model=%s, %s in / %s cached / %s out)",
					costliest.StepIdx, costliest.CostUSD(), model,
					pyval.Grouped(int64(costliest.TokensIn)),
					pyval.Grouped(int64(costliest.CacheReadTokens)),
					pyval.Grouped(int64(costliest.TokensOut))))
			}
			if loopCost >= LoopCostWarnUSD {
				evidence = append(evidence, fmt.Sprintf(
					"Loop cache-aware cost $%.2f across %d steps", loopCost, len(profiles)))
			}
			recommendation = "High absolute spend, not a growth/bloat issue. Even cache reads bill at " +
				"~0.1x and add up on pricey models. Consider routing this step class to a " +
				"cheaper model, or reducing the cached context the worker carries per turn."
		}
	}

	// 6. Empty model output: tokens spent but blocked, and quickly.
	if failureClass == "healthy" {
		empty := 0
		for _, p := range blocked {
			if p.Tokens > 0 && p.ElapsedMS < 30000 {
				empty++
			}
		}
		if empty >= 2 {
			failureClass, severity = "empty_model_output", "warning"
			evidence = append(evidence, fmt.Sprintf(
				"%d steps blocked despite receiving tokens", empty))
			recommendation = "Model may not be calling tools — check tool_choice='required' and response parsing"
		}
	}

	// 7. Budget exhaustion. NOT gated on healthy: the evidence rides along
	// whatever else was diagnosed, and the severity only moves if this is
	// the class that won.
	if strings.Contains(stuckReason, "max_iterations") {
		if failureClass == "healthy" {
			failureClass = "budget_exhaustion"
		}
		if failureClass == "budget_exhaustion" {
			severity = "warning"
		}
		evidence = append(evidence, fmt.Sprintf("Loop stuck: %s", stuckReason))
		if recommendation == "" {
			recommendation = "Increase max_iterations or enable budget-aware landing"
		}
	}

	// 8. Retry churn: the same blocked step text seen more than once.
	// Python's dict preserves INSERTION order and the evidence is emitted
	// in that order, so the port cannot use a bare Go map here.
	var churnKeys []string
	churnCount := map[string]int{}
	for _, p := range blocked {
		key := pyval.Clip(p.Text, 60)
		if _, seen := churnCount[key]; !seen {
			churnKeys = append(churnKeys, key)
		}
		churnCount[key]++
	}
	var churned []string
	for _, k := range churnKeys {
		if churnCount[k] >= RetryChurnLimit {
			churned = append(churned, k)
		}
	}
	if len(churned) > 0 && failureClass == "healthy" {
		failureClass, severity = "retry_churn", "warning"
		for _, k := range churned {
			evidence = append(evidence, fmt.Sprintf("Step retried %dx: %s", churnCount[k], k))
		}
		recommendation = "Step is consistently failing — skip and note as partial, or decompose differently"
	}

	// Model—tool edge pathologies. Weaker than the structural classes, so
	// they only CLAIM the diagnosis when nothing else did — but the
	// evidence is appended regardless, so a budget_exhaustion diagnosis
	// still shows the tool thrash underneath it.
	type pathAt struct {
		p  StepProfile
		tp pyval.Obj
	}
	var pathSteps []pathAt
	for _, p := range profiles {
		for _, tp := range p.ToolPathologies {
			pathSteps = append(pathSteps, pathAt{p, tp})
		}
	}
	if len(pathSteps) > 0 {
		for _, ps := range pathSteps {
			evidence = append(evidence, fmt.Sprintf("Step %d %s: %s",
				ps.p.StepIdx,
				pyval.Str(evGet(ps.tp, "cls", "?")),
				pyval.Str(evGet(ps.tp, "evidence", ""))))
		}
		if failureClass == "healthy" {
			// Most causal/actionable first.
			order := []string{"tool_hallucination", "tool_feedback_neglect",
				"tool_recovery_failure", "tool_arg_malformed"}
			seen := map[string]bool{}
			for _, ps := range pathSteps {
				if s, ok := evGet(ps.tp, "cls", nil).(string); ok {
					seen[s] = true
				}
			}
			// `next((c for c in _order if c in _seen_cls), "tool_arg_malformed")`
			// — the fallback fires when the stamped class is not one of the
			// four, which is how a class added on the Python side reaches a
			// port that has not learned it yet.
			failureClass = "tool_arg_malformed"
			for _, c := range order {
				if seen[c] {
					failureClass = c
					break
				}
			}
			severity = "warning"
			recommendation = FailureClasses[failureClass]
		}
	}

	tokenProfile := make([]pyval.Obj, 0, len(profiles))
	timingProfile := make([]pyval.Obj, 0, len(profiles))
	for _, p := range profiles {
		tokenProfile = append(tokenProfile, pyval.Obj{
			{Key: "step", Val: p.StepIdx},
			{Key: "tokens", Val: p.Tokens},
			{Key: "status", Val: p.Status},
		})
		timingProfile = append(timingProfile, pyval.Obj{
			{Key: "step", Val: p.StepIdx},
			{Key: "elapsed_ms", Val: p.ElapsedMS},
			{Key: "status", Val: p.Status},
		})
	}

	return LoopDiagnosis{
		LoopID:         loopID,
		FailureClass:   failureClass,
		Severity:       severity,
		Evidence:       evidence,
		Recommendation: recommendation,
		TokenProfile:   tokenProfile,
		TimingProfile:  timingProfile,
		TotalTokens:    totalTokens,
		TotalElapsedMS: totalElapsed,
		StepsDone:      len(done),
		StepsBlocked:   len(blocked),
		StepsTotal:     len(profiles),
		Project:        project,
	}
}
