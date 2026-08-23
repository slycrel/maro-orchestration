package evolver

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// guidanceFormRules is playbook.GUIDANCE_FORM_RULES verbatim — applied
// suggestions become injected guidance, and the wording decree that
// governs them lives with its consumer in Python; here it rides the
// proposer prompt the same way (evolver.py appends it into
// _EVOLVER_SYSTEM).
const guidanceFormRules = `Guidance form — you are writing a prior the run weighs, not a rule it obeys.
This text is injected verbatim into every director and decompose call.
- Write "usually X", "X is often the cheaper first move", "X tends to
  matter when Y" — never "always X", "you must X", "require X", "reject
  any Y". A run that can see a better move must stay free to take it.
- Say what tends to be true, and when. Don't prescribe the shape of the
  answer: "research goals benefit from gather → synthesize → verify",
  not "decompose research goals into exactly three phases".
- No counts, caps, step budgets, or word limits as rules. We don't know
  what a given run will take.
- Name the condition when the prior is narrow ("on goals with no named
  source, usually …") so a run can tell whether it applies.`

// evolverSystem is Python _EVOLVER_SYSTEM with the guidance rules
// spliced at the same position.
const evolverSystem = `You are a meta-evolution agent. You analyze patterns across many completed and failed runs
to identify systemic improvements.

You will receive a summary of recent run outcomes. Identify:
1. Failure patterns (repeated reasons for "stuck" outcomes)
2. Success patterns (what made "done" outcomes succeed)
3. Prompt improvements (changes to agent instructions that would reduce failures)
4. New guardrails (checks or constraints to prevent common failure modes)

Respond ONLY with a JSON object in this format:
{
  "failure_patterns": ["pattern 1", "pattern 2"],
  "suggestions": [
    {
      "category": "prompt_tweak|new_guardrail|skill_pattern|observation",
      "target": "all|research|build|ops|agenda|now",
      "suggestion": "specific improvement text",
      "failure_pattern": "what pattern motivated this",
      "confidence": 0.0-1.0,
      "pattern": "new_guardrail only: a regular expression matching the step text to flag",
      "expected_signal": [
        {"metric": "failure_class_rate|stuck_rate|cost_per_run|<other observable>",
         "class": "the failure class this should reduce, if applicable",
         "direction": "down|up"}
      ]
    }
  ]
}

expected_signal declares which observable this specific change should move and
which direction, so it can be checked later against what actually happened.
Omit it (or leave the list empty) if you can't name a concrete observable.

pattern applies to new_guardrail only, and it is matched as a regular expression
against step text — so it must be a regex, not a sentence ("rm\s+-rf", not
"avoid destructive deletes"). Omit it when the guardrail can't be expressed that
way; the suggestion still lands as guidance, and a guardrail with no matchable
pattern is written nowhere rather than written as a rule that can never fire.

` + guidanceFormRules + `

Be specific and actionable. Suggest at most 5 improvements total. If there are no clear patterns
(e.g., too few outcomes), return {"failure_patterns": [], "suggestions": []}.
`

// BuildOutcomesSummary summarizes outcome rows for the proposer. The
// verdict tri-state (SF-2) discipline is the load-bearing part: "done"
// only says the loop finished — a done-but-goal-NOT-achieved run is
// failure signal, not success, and unjudged is neither. Python also
// enriches stuck rows with full step traces (Meta-Harness steal); the
// Go port has no step-trace store, so that block is absent — named
// here and in the package doc, not silently narrowed.
func BuildOutcomesSummary(outcomes []map[string]any) string {
	if len(outcomes) == 0 {
		return "(no outcomes to analyze)"
	}
	var stuck, done []map[string]any
	for _, o := range outcomes {
		switch stringOr(o["status"]) {
		case "stuck":
			stuck = append(stuck, o)
		case "done":
			done = append(done, o)
		}
	}
	achieved, goalFailed := 0, 0
	var failedRows []map[string]any
	for _, o := range done {
		if v, judged := triState(o); judged {
			if v {
				achieved++
			} else {
				goalFailed++
				failedRows = append(failedRows, o)
			}
		}
	}
	unjudgedDone := len(done) - achieved - goalFailed

	lines := []string{
		fmt.Sprintf("Total outcomes: %d (%d done [%d verified achieved, %d goal-NOT-achieved, %d unjudged], %d stuck)",
			len(outcomes), len(done), achieved, goalFailed, unjudgedDone, len(stuck)),
		"",
		"Recent outcomes:",
	}
	for i, o := range outcomes {
		if i >= 20 {
			break
		}
		tag := ""
		if v, judged := triState(o); judged {
			if v {
				tag = " [goal achieved]"
			} else {
				tag = " [goal NOT achieved]"
			}
		}
		entry := fmt.Sprintf("  [%s]%s [%s] %s", stringOr(o["status"]), tag,
			stringOr(o["task_type"]), clipRunes(stringOr(o["goal"]), 60))
		if s := stringOr(o["summary"]); s != "" {
			entry += " — " + clipRunes(s, 80)
		}
		lines = append(lines, entry)
	}
	if len(failedRows) > 0 {
		lines = append(lines, "\nCompleted-but-goal-NOT-achieved summaries (treat as failures):")
		for i, o := range failedRows {
			if i >= 10 {
				break
			}
			lines = append(lines, "  - "+clipRunes(stringOr(o["summary"]), 120))
		}
	}
	if len(stuck) > 0 {
		lines = append(lines, "\nStuck outcome summaries:")
		for i, o := range stuck {
			if i >= 10 {
				break
			}
			lines = append(lines, "  - "+clipRunes(stringOr(o["summary"]), 120))
		}
	}
	return strings.Join(lines, "\n")
}

// triState reads the row's goal verdict: (value, judged). A present-but-
// non-bool value (corrupt or foreign-writer row) is judged-NOT-achieved,
// NOT unjudged — the same conservative direction as
// inspector.goalAchieved (r2 review: the sibling reader must not diverge,
// or the proposer never sees a failure signal the quality gate would cap
// at fair). Backport-correction candidate, like its inspector twin.
func triState(o map[string]any) (bool, bool) {
	v, present := o["goal_achieved"]
	if !present || v == nil {
		return false, false
	}
	b, ok := v.(bool)
	if !ok {
		return false, true // malformed verdict → judged-false (conservative)
	}
	return b, true
}

// llmAnalyze asks the model for patterns + raw suggestions. Empty on
// dry-run, nil adapter, or any failure (analysis is a proposer, never
// a gate). Python also folds recent captain's-log learning activity
// into the user turn (recall.recent_learning_activity) — the Go recall
// package has no learning-activity bridge yet; absent, named.
func llmAnalyze(ctx context.Context, adapter llm.Adapter, outcomes []map[string]any,
	dryRun bool) ([]string, []map[string]any) {
	if dryRun || adapter == nil || len(outcomes) == 0 {
		return nil, nil
	}
	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: evolverSystem},
		{Role: "user", Content: "Analyze these outcomes:\n\n" + BuildOutcomesSummary(outcomes)},
	}, llm.Options{MaxTokens: 2048, Temperature: 0.2, Purpose: "evolver outcome analysis"})
	if err != nil || resp == nil {
		fmt.Fprintf(os.Stderr, "[evolver] LLM analysis failed: %v\n", err)
		return nil, nil
	}
	data, jerr := jsonx.Object(resp.Content)
	if jerr != nil {
		return nil, nil
	}
	var patterns []string
	if items, ok := data["failure_patterns"].([]any); ok {
		for _, it := range items {
			if s, ok := it.(string); ok {
				patterns = append(patterns, s)
			}
		}
	}
	var raw []map[string]any
	if items, ok := data["suggestions"].([]any); ok {
		for _, it := range items {
			if m, ok := it.(map[string]any); ok {
				raw = append(raw, m)
			}
		}
	}
	return patterns, raw
}

// safeConfidence ports llm_parse.safe_float(default 0.5, clamped 0-1).
func safeConfidence(v any) float64 {
	f, ok := v.(float64)
	if !ok {
		return 0.5
	}
	if f < 0 {
		return 0
	}
	if f > 1 {
		return 1
	}
	return f
}

// expectedSignal ports safe_list(..., element_type=dict).
func expectedSignal(v any) []map[string]any {
	out := []map[string]any{}
	if items, ok := v.([]any); ok {
		for _, it := range items {
			if m, ok := it.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

// RunOptions are run_evolver's Go-slice knobs.
type RunOptions struct {
	OutcomesWindow int  // default 50
	MinOutcomes    int  // default 3
	DryRun         bool // analyze without writing or applying
	Verbose        bool
	// ExtraSuggestions lets the composition layer (internal/selfimprove)
	// inject the statistical scanners' rows into THIS cycle's batch — they
	// persist in the same save, count in the same cycle event, and ride the
	// same auto-apply pass, exactly where run_evolver extends its list. A
	// hook rather than an import: scans depends on this package's store, so
	// the arrow can't point back. Called after LLM analysis with the loaded
	// outcomes; nil = no extras.
	ExtraSuggestions func(outcomes []map[string]any) []Suggestion
}

// Run executes one meta-evolution cycle: load outcomes → LLM analysis →
// merge ExtraSuggestions → persist (content-dedup) → auto-apply
// high-confidence low-risk categories. The advisor gate and post-apply
// test-suite verify are NOT ported — see the package doc;
// medium-confidence (0.6-0.79) suggestions therefore stay pending
// instead of getting an advisor hearing. Graduation and the V2/V3
// cadence-verdict pass live in internal/selfimprove.Cycle, which wraps
// this in the Python run_evolver order.
func Run(ctx context.Context, workspaceDir string, rec *record.Recorder,
	cfg map[string]any, adapter llm.Adapter, opts RunOptions) EvolverReport {
	if opts.OutcomesWindow <= 0 {
		opts.OutcomesWindow = 50
	}
	if opts.MinOutcomes <= 0 {
		opts.MinOutcomes = 3
	}
	runID := record.NewID()[:8]
	started := time.Now()
	if opts.Verbose {
		fmt.Fprintf(os.Stderr, "[evolver] run_id=%s starting...\n", runID)
	}

	outcomes, err := record.LoadOutcomes(workspaceDir, opts.OutcomesWindow)
	if err != nil {
		return EvolverReport{RunID: runID, Skipped: true, SkipReason: err.Error()}
	}
	if len(outcomes) < opts.MinOutcomes {
		return EvolverReport{
			RunID:            runID,
			OutcomesReviewed: len(outcomes),
			Skipped:          true,
			SkipReason:       fmt.Sprintf("only %d outcomes (need %d)", len(outcomes), opts.MinOutcomes),
		}
	}
	if opts.Verbose {
		fmt.Fprintf(os.Stderr, "[evolver] analyzing %d outcomes...\n", len(outcomes))
	}

	patterns, rawSuggestions := llmAnalyze(ctx, adapter, outcomes, opts.DryRun)

	var suggestions []Suggestion
	for i, raw := range rawSuggestions {
		suggestions = append(suggestions, Suggestion{
			SuggestionID:     fmt.Sprintf("%s-%02d", runID, i),
			Category:         stringDefault(raw["category"], "observation"),
			Target:           stringDefault(raw["target"], "all"),
			Suggestion:       stringOr(raw["suggestion"]),
			FailurePattern:   stringOr(raw["failure_pattern"]),
			Confidence:       safeConfidence(raw["confidence"]),
			OutcomesAnalyzed: len(outcomes),
			GeneratedAt:      nowISO(),
			ExpectedSignal:   expectedSignal(raw["expected_signal"]),
			Pattern:          stringOr(raw["pattern"]),
		})
	}

	// Statistical-scan rows join the batch here — same position as
	// run_evolver's suggestions.extend(run_statistical_scans(...)).
	if opts.ExtraSuggestions != nil {
		suggestions = append(suggestions, opts.ExtraSuggestions(outcomes)...)
	}

	report := EvolverReport{
		RunID:            runID,
		OutcomesReviewed: len(outcomes),
		Suggestions:      suggestions,
		FailurePatterns:  patterns,
	}
	if opts.Verbose {
		fmt.Fprintf(os.Stderr, "[evolver] found %d patterns, %d suggestions\n",
			len(patterns), len(suggestions))
	}

	if !opts.DryRun && len(suggestions) > 0 {
		if serr := SaveSuggestions(workspaceDir, suggestions); serr != nil {
			fmt.Fprintf(os.Stderr, "[evolver] failed to save suggestions: %v\n", serr)
		}
	}

	// Auto-apply high-confidence suggestions (closes the feedback
	// loop). Only confidence >= 0.8 auto-applies; the Python advisor
	// gate for 0.6-0.79 is unported, so those rows stay pending for
	// `maro evolve -list` review instead of getting an LLM hearing.
	autoApplied := 0
	if !opts.DryRun {
		for _, s := range suggestions {
			if s.Applied || s.Confidence < 0.8 {
				continue
			}
			found, aerr := Apply(workspaceDir, rec, cfg, s.SuggestionID, false)
			if aerr != nil {
				fmt.Fprintf(os.Stderr, "[evolver] apply %s failed: %v\n", s.SuggestionID, aerr)
				continue
			}
			if found && IsApplied(workspaceDir, s.SuggestionID) {
				autoApplied++
			}
		}
		if opts.Verbose && autoApplied > 0 {
			fmt.Fprintf(os.Stderr, "[evolver] auto-applied %d suggestions\n", autoApplied)
		}
	}
	report.AutoApplied = autoApplied

	// Python runs _verify_post_apply here (full pytest suite, auto-
	// revert on red). Go auto-applies only data rows (lessons,
	// guardrail rows when opted in) — no code mutates, and faking a
	// test-suite verdict would be dishonest. The Revert path exists
	// for the operator; the suite verify is named unported.

	report.ElapsedMS = time.Since(started).Milliseconds()

	// Captain's log: cycle summary (Python EVOLVER_GENERATED /
	// EVOLVER_SKIPPED event pair).
	if len(suggestions) > 0 {
		_ = rec.Event("EVOLVER_GENERATED", "run-"+runID,
			fmt.Sprintf("Generated %d suggestions from %d outcomes. %d auto-applied.",
				len(suggestions), len(outcomes), autoApplied),
			map[string]any{
				"run_id":            runID,
				"outcomes_reviewed": len(outcomes),
				"suggestions":       len(suggestions),
				"auto_applied":      autoApplied,
				"patterns":          len(patterns),
			}, "")
	} else if !report.Skipped {
		_ = rec.Event("EVOLVER_SKIPPED", "run-"+runID,
			fmt.Sprintf("No suggestions from %d outcomes.", len(outcomes)),
			map[string]any{"run_id": runID, "outcomes_reviewed": len(outcomes)}, "")
	}
	return report
}

func stringDefault(v any, def string) string {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return def
}

// MarshalReport renders the report as JSON for the CLI's -format json.
func MarshalReport(r EvolverReport) string {
	if r.Suggestions == nil {
		r.Suggestions = []Suggestion{}
	}
	if r.FailurePatterns == nil {
		r.FailurePatterns = []string{}
	}
	raw, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return "{}"
	}
	return string(raw)
}
