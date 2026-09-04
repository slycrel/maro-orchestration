// Package contracts is the contracts foundation (design note §1c; practice in
// planning/contract-testing-input.md): for every registered record kind, a
// GENERATED file derived from the Go type, a DECLARED file a human edits
// where every line drives a test, a three-state report (derived / declared /
// undefined → warning, never silenced), an answer key, the record census,
// and a reference reader proving forward and backward compatibility.
package contracts

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// Lifecycle is the one non-executable declaration: it gates whether tests
// should exist at all.
type Lifecycle string

const (
	Stable         Lifecycle = "stable"
	Transitional   Lifecycle = "transitional"
	InternalLoose  Lifecycle = "internal-loose"
	HardenedLegacy Lifecycle = "hardened-legacy"
	DesignPending  Lifecycle = "design-pending"
)

// Provenance: stated by the owner, or read off the implementation.
type Provenance string

const (
	Supplied Provenance = "supplied"
	Inferred Provenance = "inferred"
)

// Constraint tri-state for a field's VALUE: defined / unconstrained (someone
// looked; any value of the type is legal) / undefined (nobody looked).
type Constraint string

const (
	Defined       Constraint = "defined"
	Unconstrained Constraint = "unconstrained"
	Undefined     Constraint = "undefined"
)

// Closed vocabularies. A declared value outside its vocabulary is an error at
// load time — a typo is never silently "undefined".
var (
	lifecycles  = set(Stable, Transitional, InternalLoose, HardenedLegacy, DesignPending)
	provenances = set(Supplied, Inferred)
	constraints = set(Defined, Unconstrained, Undefined)
	absences    = set("omitted", "null", "empty", "never")
	onAbsences  = set("tolerated", "rejected")          // or default:<v>
	unknownVals = set("accepted-unchanged", "rejected") // or replaced-with:<v>
	usedFors    = set("display-only", "routing", "authorization", "money", "storage-key", "identity")
)

func set[T ~string](vs ...T) map[string]bool {
	m := map[string]bool{}
	for _, v := range vs {
		m[string(v)] = true
	}
	return m
}

// FormatVersion is the answer-key revision every contract file is emitted
// against (contract input §18). A file without one reads as the founding
// date.
const FormatVersion = "2026-09-04"

// Generated is derived from the Go type; never hand-edited. If regeneration
// changes it, the contract changed: the diff is the review.
type Generated struct {
	FormatVersion string           `json:"format_version"`
	Kind          string           `json:"kind"`
	Envelope      string           `json:"envelope"`
	Schema        string           `json:"schema"`
	GoType        string           `json:"go_type"`
	Fields        []GeneratedField `json:"fields"`
	SourceRef     string           `json:"source_ref"`
	Generator     string           `json:"generator"`
}

// GeneratedField is what reflection can know.
type GeneratedField struct {
	Name       string `json:"name"`
	Wire       string `json:"wire"`
	GoType     string `json:"go_type"`
	Omittable  bool   `json:"omittable"`
	IsThought  bool   `json:"is_thought"`
	Embedded   bool   `json:"embedded,omitempty"`
	FromHeader bool   `json:"from_header,omitempty"`
}

// Declared is the human file. Every line is executable except Lifecycle,
// which gates whether tests exist. Header fields are declared ONCE in
// _header.declared.json and merged into every kind.
type Declared struct {
	FormatVersion string                   `json:"format_version,omitempty"`
	Kind          string                   `json:"kind"`
	Lifecycle     Lifecycle                `json:"lifecycle"`
	Provenance    Provenance               `json:"provenance"`
	DesignFlag    string                   `json:"design_flag,omitempty"`
	MeasuredBy    string                   `json:"measured_by,omitempty"` // "<pkg>:<TestName>" resolvable in the tree; "not-re-runnable-here" is legal
	Fields        map[string]DeclaredField `json:"fields"`
	// Improvised registers every `x-` key this file uses, with the search
	// that was done for a prior name (contract input §20). A bare key that is
	// neither vocabulary nor x- is a vocabulary warning; the committed-pair
	// test pins zero of them.
	Improvised map[string]Improvised `json:"improvised,omitempty"`

	// Unknown keys found while reading, at any nesting: warned, never fatal.
	Unknown []UnknownKey `json:"-"`
}

// Improvised is one register row.
type Improvised struct {
	UsedAt string `json:"used_at"` // "field:<name>" or "top"
	Search string `json:"search"`  // what was searched for a prior name
}

// UnknownKey is a vocabulary warning: a key the reader did not recognise.
type UnknownKey struct {
	Path string // e.g. "fields.hash.on_absense"
	Key  string
}

// DeclaredField carries the dimensions no generator can derive.
type DeclaredField struct {
	Absence      string                     `json:"absence,omitempty"`
	OnAbsence    string                     `json:"on_absence,omitempty"`
	UnknownValue string                     `json:"unknown_value,omitempty"`
	UsedFor      string                     `json:"used_for,omitempty"`
	Constraint   Constraint                 `json:"constraint,omitempty"`
	Pattern      string                     `json:"pattern,omitempty"`
	MeasuredBy   string                     `json:"measured_by,omitempty"`
	Rejects      string                     `json:"rejects,omitempty"` // a value that must NOT match Pattern (the negative fixture); required when Pattern is set
	Extra        map[string]json.RawMessage `json:"-"`                 // x- keys, kept verbatim
}

var (
	ErrDeclaredFieldUnknown = errors.New("contracts: declared field not in generated contract")
	ErrIllegal              = errors.New("contracts: illegal combination")
	ErrLifecycle            = errors.New("contracts: bad lifecycle")
	ErrVocabulary           = errors.New("contracts: value outside its closed vocabulary")
	ErrStrict               = errors.New("contracts: file does not decode strictly")
	ErrPattern              = errors.New("contracts: pattern")
)

// Dir is the committed contracts directory.
type Dir string

const headerDeclared = "_header"

func (d Dir) genPath(kind string) string { return filepath.Join(string(d), kind+".generated.json") }
func (d Dir) decPath(kind string) string { return filepath.Join(string(d), kind+".declared.json") }

// decodeStrict rejects unknown keys and trailing content. Used for GENERATED
// files (machine-written: an unknown key there is a hand edit).
func decodeStrict(path string, raw []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return fmt.Errorf("%w: %s: %v", ErrStrict, path, err)
	}
	if dec.More() {
		return fmt.Errorf("%w: %s: trailing content", ErrStrict, path)
	}
	return nil
}

// ReadGenerated loads a committed generated file strictly.
func (d Dir) ReadGenerated(kind string) (*Generated, error) {
	raw, err := os.ReadFile(d.genPath(kind))
	if err != nil {
		return nil, err
	}
	var g Generated
	if err := decodeStrict(d.genPath(kind), raw, &g); err != nil {
		return nil, err
	}
	return &g, nil
}

// ReadDeclared loads a kind's declared file, validates every vocabulary,
// compiles every pattern, and merges the shared header declaration. Absent
// is normal and returns (nil, nil).
func (d Dir) ReadDeclared(kind string) (*Declared, error) {
	raw, err := os.ReadFile(d.decPath(kind))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	dcp, err := decodeDeclared(d.decPath(kind), raw)
	if err != nil {
		return nil, err
	}
	dc := *dcp
	if err := dc.validate(d.decPath(kind)); err != nil {
		return nil, err
	}
	if kind != headerDeclared {
		hdr, err := d.readHeaderDeclared()
		if err != nil {
			return nil, err
		}
		if dc.Fields == nil {
			dc.Fields = map[string]DeclaredField{}
		}
		for name, f := range hdr {
			if _, own := dc.Fields[name]; !own {
				dc.Fields[name] = f
			}
		}
	}
	return &dc, nil
}

func (d Dir) readHeaderDeclared() (map[string]DeclaredField, error) {
	raw, err := os.ReadFile(d.decPath(headerDeclared))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	hp, err := decodeDeclared(d.decPath(headerDeclared), raw)
	if err != nil {
		return nil, err
	}
	h := *hp
	if err := h.validate(d.decPath(headerDeclared)); err != nil {
		return nil, err
	}
	for name := range h.Fields {
		if !strings.HasPrefix(name, "header.") {
			return nil, fmt.Errorf("%w: %s declares non-header field %q", ErrVocabulary, d.decPath(headerDeclared), name)
		}
	}
	return h.Fields, nil
}

// declaredTop and declaredField are the vocabularies a reader recognises.
var (
	declaredTop   = set("format_version", "kind", "lifecycle", "provenance", "design_flag", "measured_by", "fields", "improvised")
	declaredField = set("absence", "on_absence", "unknown_value", "used_for", "constraint", "pattern", "measured_by", "rejects")
)

// decodeDeclared reads a declared file TOLERANTLY (contract input §19): the
// JSON must parse and be a single object, but unknown keys at any nesting are
// recorded as vocabulary warnings, `x-` keys are kept verbatim, and nothing
// is refused for a key the reader does not know.
func decodeDeclared(path string, raw []byte) (*Declared, error) {
	var top map[string]json.RawMessage
	dec := json.NewDecoder(bytes.NewReader(raw))
	if err := dec.Decode(&top); err != nil {
		return nil, fmt.Errorf("%w: %s: %v", ErrStrict, path, err)
	}
	if dec.More() {
		return nil, fmt.Errorf("%w: %s: trailing content", ErrStrict, path)
	}
	var dc Declared
	if err := json.Unmarshal(raw, &dc); err != nil {
		return nil, fmt.Errorf("%w: %s: %v", ErrStrict, path, err)
	}
	for k := range top {
		if declaredTop[k] || strings.HasPrefix(k, "x-") {
			continue
		}
		dc.Unknown = append(dc.Unknown, UnknownKey{Path: k, Key: k})
	}
	if rawFields, ok := top["fields"]; ok {
		var fields map[string]map[string]json.RawMessage
		if err := json.Unmarshal(rawFields, &fields); err != nil {
			return nil, fmt.Errorf("%w: %s: fields: %v", ErrStrict, path, err)
		}
		for name, keys := range fields {
			f := dc.Fields[name]
			for k, v := range keys {
				switch {
				case declaredField[k]:
				case strings.HasPrefix(k, "x-"):
					if f.Extra == nil {
						f.Extra = map[string]json.RawMessage{}
					}
					f.Extra[k] = v
				default:
					dc.Unknown = append(dc.Unknown, UnknownKey{Path: "fields." + name + "." + k, Key: k})
				}
			}
			dc.Fields[name] = f
		}
	}
	sort.Slice(dc.Unknown, func(i, j int) bool { return dc.Unknown[i].Path < dc.Unknown[j].Path })
	if dc.FormatVersion == "" {
		dc.FormatVersion = FormatVersion
	}
	return &dc, nil
}

// UnregisteredImprovised lists x- keys used without a register row — the
// guard behind contract input §20.
func (dc *Declared) UnregisteredImprovised() []string {
	var out []string
	for name := range dc.Fields {
		for k := range dc.Fields[name].Extra {
			if _, ok := dc.Improvised[k]; !ok {
				out = append(out, "fields."+name+"."+k)
			}
		}
	}
	sort.Strings(out)
	return out
}

func (dc *Declared) validate(path string) error {
	if !lifecycles[string(dc.Lifecycle)] {
		return fmt.Errorf("%w: %q in %s", ErrLifecycle, dc.Lifecycle, path)
	}
	if dc.Lifecycle == HardenedLegacy && dc.DesignFlag == "" {
		return fmt.Errorf("%w: hardened-legacy requires design_flag in %s", ErrLifecycle, path)
	}
	if !provenances[string(dc.Provenance)] {
		return fmt.Errorf("%w: provenance %q in %s", ErrVocabulary, dc.Provenance, path)
	}
	for name, f := range dc.Fields {
		where := fmt.Sprintf("%s field %q", path, name)
		if f.Absence != "" && !absences[f.Absence] {
			return fmt.Errorf("%w: absence %q (%s)", ErrVocabulary, f.Absence, where)
		}
		if f.OnAbsence != "" && !onAbsences[f.OnAbsence] && !strings.HasPrefix(f.OnAbsence, "default:") {
			return fmt.Errorf("%w: on_absence %q (%s)", ErrVocabulary, f.OnAbsence, where)
		}
		if f.UnknownValue != "" && !unknownVals[f.UnknownValue] && !strings.HasPrefix(f.UnknownValue, "replaced-with:") {
			return fmt.Errorf("%w: unknown_value %q (%s)", ErrVocabulary, f.UnknownValue, where)
		}
		if f.UsedFor != "" && !usedFors[f.UsedFor] {
			return fmt.Errorf("%w: used_for %q (%s)", ErrVocabulary, f.UsedFor, where)
		}
		if f.Constraint != "" && !constraints[string(f.Constraint)] {
			return fmt.Errorf("%w: constraint %q (%s)", ErrVocabulary, f.Constraint, where)
		}
		if f.Pattern != "" {
			re, err := regexp.Compile(f.Pattern)
			if err != nil {
				return fmt.Errorf("%w: %q does not compile (%s): %v", ErrPattern, f.Pattern, where, err)
			}
			if f.Rejects == "" {
				return fmt.Errorf("%w: %q has no `rejects` negative fixture (%s) — a pattern nothing can fail is not executable", ErrPattern, f.Pattern, where)
			}
			if re.MatchString(f.Rejects) {
				return fmt.Errorf("%w: `rejects` value %q MATCHES %q (%s) — the negative fixture must fail the pattern", ErrPattern, f.Rejects, f.Pattern, where)
			}
		}
	}
	return nil
}

// CompiledPattern returns the field's compiled pattern or nil.
func (f DeclaredField) CompiledPattern() *regexp.Regexp {
	if f.Pattern == "" {
		return nil
	}
	return regexp.MustCompile(f.Pattern) // validated at load
}

func writeJSON(path string, v any) error {
	raw, err := canonical(v)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// canonical is the one serialization of a contract file: indented JSON, a
// trailing newline. Drift compares these bytes.
func canonical(v any) ([]byte, error) {
	raw, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(raw, '\n'), nil
}

func sortedKeys[V any](m map[string]V) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}
