package evolver

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
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
	dryRun bool) ([]string, []pyval.Obj) {
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
	// ObjectOrdered, not Object.
	//
	// CPython's extract_json hands back a dict whose key order is the
	// model's and whose numbers are ints or floats by their LITERAL, and
	// the mint below renders four of these fields through str(). Both
	// facts are load-bearing there and json.Unmarshal into `any` throws
	// both away: `"suggestion": 5` becomes float64 and reads back "5.0"
	// where Python writes "5", and a dict-valued field has no order at
	// all, so pyval.Str can only refuse it. Decoding ordered is what makes
	// the coercions in the mint answerable rather than approximate.
	data, jerr := jsonx.ObjectOrdered(resp.Content)
	if jerr != nil {
		return nil, nil
	}
	var patterns []string
	if items, ok := objList(data, "failure_patterns"); ok {
		for _, it := range items {
			if s, ok := it.(string); ok {
				patterns = append(patterns, s)
			}
		}
	}
	var raw []pyval.Obj
	if items, ok := objList(data, "suggestions"); ok {
		for _, it := range items {
			if m, ok := it.(pyval.Obj); ok {
				raw = append(raw, m)
			}
		}
	}
	return patterns, raw
}

// objList reads key as a decoded JSON array. LoadsOrdered spells one as
// pyval.List, so the `[]any` assertion the map-decoded version used never
// fires and every element filter downstream would silently see nothing.
func objList(o pyval.Obj, key string) (pyval.List, bool) {
	v, ok := o.Get(key)
	if !ok {
		return nil, false
	}
	l, ok := v.(pyval.List)
	return l, ok
}

// safeConfidence is llm_parse.safe_float(default 0.5, clamped 0-1).
//
// It used to be a bare `v.(float64)` type assertion, which is neither of
// safe_float's two coercions and neither of its guards (mission-r5
// MEDIUM). Measured against CPython:
//
//	"0.9" -> py 0.9   go 0.5      NaN -> py 0.5   go NaN
//	true  -> py 1.0   go 0.5
//
// All three are durable. `"confidence": "0.9"` auto-applies the
// suggestion on CPython (>= 0.8) and not on Go, so the two runtimes
// write different applied state to the same suggestions.jsonl. A NaN is
// worse: it passes `!(NaN < 0.8)` so Go APPLIES the suggestion, while
// SaveSuggestions then fails with "json: unsupported value: NaN" and the
// whole batch is lost — applied but never persisted.
func safeConfidence(v any) float64 { return pyval.SafeFloatUnit(v, 0.5) }

// expectedSignal ports safe_list(..., element_type=dict).
//
// The rows are flattened to plain maps because Suggestion.ExpectedSignal
// is `[]map[string]any` and that type is on the wire. NAMED DIVERGENCE:
// a Go map marshals with its keys SORTED, so a model reply whose signal
// row is `{"metric": "x", "direction": "down"}` is written back by
// CPython in that order and by this port as `{"direction":...,
// "metric":...}`. The rows compare equal to every reader (both runtimes
// decode by key) and differ only as store BYTES. Closing it means
// carrying pyval.Obj on the struct, which changes the marshalled shape
// for graduation and scans too, and is not this tranche.
func expectedSignal(v any) []map[string]any {
	out := []map[string]any{}
	var items []any
	switch t := v.(type) {
	case pyval.List:
		items = t
	case []any:
		items = t
	default:
		return out
	}
	for _, it := range items {
		switch m := it.(type) {
		case pyval.Obj:
			// Plain() over an Obj is the map with the same pairs.
			if pm, ok := pyval.Plain(m).(map[string]any); ok {
				out = append(out, pm)
			}
		case map[string]any:
			out = append(out, m)
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
			SuggestionID: fmt.Sprintf("%s-%02d", runID, i),
			// `.get(k, default)` — PRESENCE, not truthiness. A model that
			// answers `"target": ""` means the empty string, and CPython
			// stores it; substituting "all" there changed the row AND its
			// dedup identity, because category/target/suggestion are the
			// three inputs to the content key (adversarial r3, HIGH).
			Category:         objGetStr(raw, "category", "observation"),
			Target:           objGetStr(raw, "target", "all"),
			Suggestion:       objGetStr(raw, "suggestion", ""),
			FailurePattern:   objGetStr(raw, "failure_pattern", ""),
			Confidence:       safeConfidence(objGet(raw, "confidence", nil)),
			OutcomesAnalyzed: len(outcomes),
			GeneratedAt:      nowISO(),
			ExpectedSignal:   expectedSignal(objGet(raw, "expected_signal", pyval.List{})),
			// `str(raw.get("pattern","") or "")` — the truthiness gate AND
			// the str(), which is pyval.StrOrEmpty. stringOr was a bare type
			// assertion here, so a list pattern became "" and the guardrail
			// it was meant to install could never match.
			Pattern: pyval.StrOrEmpty(objGet(raw, "pattern", "")),
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

// objGet is Python's `d.get(key, default)` — PRESENCE only. A present
// null is None, not the default.
func objGet(o pyval.Obj, key string, def any) any {
	if v, ok := o.Get(key); ok {
		return v
	}
	return def
}

// objGetStr is `d.get(key, default)` narrowed to this port's string-typed
// Suggestion fields.
//
// # The named divergence this carries
//
// CPython's Suggestion is a dataclass, and a dataclass does not enforce its
// annotations: `suggestion=raw.get("suggestion","")` on a reply of
// `"suggestion": 5` stores the INTEGER 5 and json.dumps writes `5`, not
// `"5"`. The same goes for a present null, which is written as `null`.
//
// Go's field is a `string` and cannot hold either. The port therefore
// renders a non-string through str(), which is where the two disagree:
//
//	reply                     CPython row        this port
//	{"suggestion": 5}         "suggestion": 5    "suggestion": "5"
//	{"failure_pattern": null} ...: null          ...: "None"
//
// Both remain STRING-SHAPED to every reader (`str(x or "")` at the read
// sites collapses them identically), so this is a byte-level divergence in
// a shared store rather than a behavioural one — and it is pinned as such
// by TestTheMintsNonStringDivergenceIsPinned rather than left to be
// rediscovered. Widening the five fields to `any` is what closes it; that
// is a 38-call-site change and is not this tranche.
//
// The DEFAULT is returned unrendered, because Python's default is a literal
// that never passes through str().
func objGetStr(o pyval.Obj, key string, def string) string {
	v, ok := o.Get(key)
	if !ok {
		return def
	}
	return pyval.Str(v)
}

// MarshalReport renders the report as JSON for the CLI's -format json.
func MarshalReport(r EvolverReport) string {
	if r.Suggestions == nil {
		r.Suggestions = []Suggestion{}
	}
	if r.FailurePatterns == nil {
		r.FailurePatterns = []string{}
	}
	// The Python CLI prints json.dumps(..., indent=2). This is a
	// machine-readable surface — scripts parse it — and suggestion_text is
	// LLM prose, so `>` and non-ASCII are routine (mission-r8).
	raw, err := pyval.DumpsStructIndent2(r)
	if err != nil {
		return "{}"
	}
	return raw
}
