// Package graduation ports src/graduation.py (Phase 46 — intervention
// graduation): scan recent diagnoses for repeated failure classes and,
// past the bar, write a pending suggestion for HUMAN review. Graduation
// rules stay advisor-gated (a human applies them) per the VERIFY_LEARN_ARC
// V3 owner call — nothing here auto-applies a standing rule.
//
// LESSONS-ARE-DATA (Jeremy 2026-08-22): Python reifies the graduation
// templates as a code dict — learning-shaped content (per-failure-class
// remedies, confidences, structural verify greps) frozen into source. The
// Go port moves them to DATA: graduation_templates.json ships embedded as
// the default, and <workspace>/graduation-templates.json overrides per
// failure class. Backport candidate: Python reads the same file.
//
// SECURITY DIVERGENCE (Go stricter, named): verify_pattern is executed via
// the shell. In Python the pattern executed comes from the SUGGESTION ROW
// (suggestions.jsonl — runtime-writable by the LLM pipeline), which lets
// any writer of that ledger stage a row with failure_pattern
// "graduation:<x>" + applied=true + an arbitrary verify_pattern and have
// the next cadence shell-exec it. Go executes ONLY the compiled-in
// (embedded) template's pattern for the row's failure class — provenance
// is the producer's bit (the guard-tranche lesson), and a workspace
// override or ledger row can retune prose/confidence but can never inject
// shell. Backport-correction candidate for Python.
package graduation

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

//go:embed graduation_templates.json
var embeddedTemplates []byte

// Template is one failure class's graduation rule — data, not code.
type Template struct {
	Category      string  `json:"category"`
	Suggestion    string  `json:"suggestion"` // {count} {loop_ids} {evidence} placeholders
	Confidence    float64 `json:"confidence"`
	VerifyPattern string  `json:"verify_pattern"`
	// ExpectedSignal is derived from the class key when absent (Python's
	// setdefault loop): "this failure class should occur less often once
	// the fix lands" — the key can never drift from the declaration.
	ExpectedSignal []map[string]any `json:"expected_signal"`
}

type templateFile struct {
	Templates map[string]Template `json:"templates"`
}

// workspaceTemplatesPath is the override location — workspace wins per
// failure class, the shipped file is the default (the skills/personas
// resolution-order precedent).
func workspaceTemplatesPath(ws string) string {
	return filepath.Join(ws, "graduation-templates.json")
}

// loadEmbedded parses the shipped default set. The embedded file is
// compiled in; a parse failure is a build defect, surfaced loudly.
func loadEmbedded() map[string]Template {
	var tf templateFile
	if err := json.Unmarshal(embeddedTemplates, &tf); err != nil {
		fmt.Fprintf(os.Stderr, "[graduation] embedded template file unparseable: %v\n", err)
		return map[string]Template{}
	}
	return withDerivedSignals(tf.Templates)
}

// LoadTemplates resolves the effective template set: embedded defaults,
// then the workspace override merged per failure class. An unreadable or
// unparseable override is IGNORED with a named warning — a corrupt data
// file degrades to the shipped defaults, never to an empty rule set and
// never to a hard failure of the cadence.
func LoadTemplates(ws string) map[string]Template {
	out := loadEmbedded()
	raw, err := os.ReadFile(workspaceTemplatesPath(ws))
	if err != nil {
		return out // no override — the normal state
	}
	var tf templateFile
	if err := json.Unmarshal(raw, &tf); err != nil {
		fmt.Fprintf(os.Stderr,
			"[graduation] workspace template override unparseable (using shipped defaults): %v\n", err)
		return out
	}
	for fc, t := range withDerivedSignals(tf.Templates) {
		base, isShipped := out[fc]
		if isShipped && t.VerifyPattern != base.VerifyPattern && t.VerifyPattern != "" {
			// Prose/confidence retune rides; shell does not (package doc).
			fmt.Fprintf(os.Stderr,
				"[graduation] override for %q changes verify_pattern — ignored "+
					"(execution stays anchored to the shipped copy)\n", fc)
		}
		if isShipped {
			t.VerifyPattern = base.VerifyPattern
		} else {
			// A class the shipped set doesn't know: proposals allowed, but
			// there is no compiled pattern to execute for it.
			t.VerifyPattern = ""
		}
		out[fc] = t
	}
	return out
}

// executablePattern returns the shell pattern Go may run for a failure
// class: the EMBEDDED template's, regardless of any override or ledger row.
func executablePattern(fc string) string {
	return loadEmbedded()[fc].VerifyPattern
}

func withDerivedSignals(in map[string]Template) map[string]Template {
	out := make(map[string]Template, len(in))
	for fc, t := range in {
		if len(t.ExpectedSignal) == 0 {
			t.ExpectedSignal = []map[string]any{
				{"metric": "failure_class_rate", "class": fc, "direction": "down"},
			}
		}
		out[fc] = t
	}
	return out
}

// TemplateClasses returns the known failure classes, sorted (test surface +
// deterministic iteration for proposal order).
func TemplateClasses(ws string) []string {
	t := LoadTemplates(ws)
	classes := make([]string, 0, len(t))
	for fc := range t {
		classes = append(classes, fc)
	}
	sort.Strings(classes)
	return classes
}
