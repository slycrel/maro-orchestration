// Package contracts is the contracts foundation (design note §1c; practice in
// planning/contract-testing-input.md): for every registered record kind, a
// GENERATED file derived from the Go type, a DECLARED file a human edits
// where every line drives a test, a three-state report (derived / declared /
// undefined → warning, never silenced), an answer key, the record census,
// and a reference reader proving forward and backward compatibility.
package contracts

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

// Lifecycle is the one non-executable declaration: it gates whether tests
// should exist at all.
type Lifecycle string

const (
	Stable         Lifecycle = "stable"          // a new consumer could adopt as-is
	Transitional   Lifecycle = "transitional"    // meant to die; tripwire only
	InternalLoose  Lifecycle = "internal-loose"  // deliberately unformalized; warnings stand
	HardenedLegacy Lifecycle = "hardened-legacy" // wrong-but-shipping; guard like stable + design flag
	DesignPending  Lifecycle = "design-pending"  // shape contested; suppress generation, escalate
)

var lifecycles = map[Lifecycle]bool{Stable: true, Transitional: true, InternalLoose: true, HardenedLegacy: true, DesignPending: true}

// Provenance says whether a declaration was stated by the owner or read off
// the implementation.
type Provenance string

const (
	Supplied Provenance = "supplied"
	Inferred Provenance = "inferred"
)

// Constraint tri-state for a field's VALUE (the third flavour): defined /
// unconstrained (someone looked; any value of the type is legal — executable)
// / undefined (nobody looked → warning). Thought fields are declared
// unconstrained on purpose (D16).
type Constraint string

const (
	Defined       Constraint = "defined"
	Unconstrained Constraint = "unconstrained"
	Undefined     Constraint = "undefined"
)

// Generated is derived from the Go type; never hand-edited. If regeneration
// changes it, the contract changed: the diff is the review.
type Generated struct {
	Kind     string           `json:"kind"`
	Envelope string           `json:"envelope"`
	Schema   string           `json:"schema"` // "<kind>/<version>"
	GoType   string           `json:"go_type"`
	Fields   []GeneratedField `json:"fields"`
	// SourceRef is the git ref the file was generated at; "unknown" when git is unavailable.
	SourceRef string `json:"source_ref"`
	Generator string `json:"generator"` // "maro-go contracts gen v1"
}

// GeneratedField is what reflection can know: name, wire name, Go type,
// whether omission is representable (omitempty / pointer / slice / map),
// and whether the field is a thought reference (→ unconstrained by decree).
type GeneratedField struct {
	Name       string `json:"name"`
	Wire       string `json:"wire"`
	GoType     string `json:"go_type"`
	Omittable  bool   `json:"omittable"`  // absence representable on the wire
	IsThought  bool   `json:"is_thought"` // thought.Ref or a thought hash field
	Embedded   bool   `json:"embedded,omitempty"`
	FromHeader bool   `json:"from_header,omitempty"`
}

// Declared is the human file. Every line is executable — it drives a test
// that can fail — except Lifecycle, which gates whether tests exist.
type Declared struct {
	Kind       string                   `json:"kind"`
	Lifecycle  Lifecycle                `json:"lifecycle"`
	Provenance Provenance               `json:"provenance"`
	DesignFlag string                   `json:"design_flag,omitempty"` // required for hardened-legacy
	MeasuredBy string                   `json:"measured_by,omitempty"` // required on any measured claim; "not-re-runnable-here" is legal
	Fields     map[string]DeclaredField `json:"fields"`
}

// DeclaredField carries the dimensions no generator can derive.
type DeclaredField struct {
	Absence      string     `json:"absence,omitempty"`       // omitted | null | empty | never
	OnAbsence    string     `json:"on_absence,omitempty"`    // tolerated | default:<v> | rejected
	UnknownValue string     `json:"unknown_value,omitempty"` // accepted-unchanged | rejected | replaced-with:<v>
	UsedFor      string     `json:"used_for,omitempty"`      // display-only | authorization | routing | money | storage-key | identity
	Constraint   Constraint `json:"constraint,omitempty"`    // defined | unconstrained | undefined
	Pattern      string     `json:"pattern,omitempty"`       // when defined
	MeasuredBy   string     `json:"measured_by,omitempty"`
}

var (
	ErrDeclaredFieldUnknown = errors.New("contracts: declared field not in generated contract")
	ErrIllegal              = errors.New("contracts: illegal combination")
	ErrLifecycle            = errors.New("contracts: bad lifecycle")
)

// Dir is the committed contracts directory: <dir>/<kind>.generated.json,
// <dir>/<kind>.declared.json, <dir>/README.md (answer key), <dir>/CENSUS.md.
type Dir string

func (d Dir) genPath(kind string) string { return filepath.Join(string(d), kind+".generated.json") }
func (d Dir) decPath(kind string) string { return filepath.Join(string(d), kind+".declared.json") }

// ReadGenerated loads a committed generated file.
func (d Dir) ReadGenerated(kind string) (*Generated, error) {
	raw, err := os.ReadFile(d.genPath(kind))
	if err != nil {
		return nil, err
	}
	var g Generated
	if err := json.Unmarshal(raw, &g); err != nil {
		return nil, fmt.Errorf("contracts: %s: %w", d.genPath(kind), err)
	}
	return &g, nil
}

// ReadDeclared loads a declared file; absent is normal and returns (nil, nil):
// "nothing declared yet", every underivable dimension is a warning.
func (d Dir) ReadDeclared(kind string) (*Declared, error) {
	raw, err := os.ReadFile(d.decPath(kind))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var dc Declared
	if err := json.Unmarshal(raw, &dc); err != nil {
		return nil, fmt.Errorf("contracts: %s: %w", d.decPath(kind), err)
	}
	if !lifecycles[dc.Lifecycle] {
		return nil, fmt.Errorf("%w: %q in %s", ErrLifecycle, dc.Lifecycle, d.decPath(kind))
	}
	if dc.Lifecycle == HardenedLegacy && dc.DesignFlag == "" {
		return nil, fmt.Errorf("%w: hardened-legacy requires design_flag in %s", ErrLifecycle, d.decPath(kind))
	}
	if dc.Provenance != Supplied && dc.Provenance != Inferred {
		return nil, fmt.Errorf("contracts: provenance must be supplied|inferred in %s", d.decPath(kind))
	}
	return &dc, nil
}

func writeJSON(path string, v any) error {
	raw, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func sortedKeys[V any](m map[string]V) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}
