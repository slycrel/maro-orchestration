package contracts

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// State is the three-state severity model.
type State string

const (
	Derived    State = "derived"
	DeclaredS  State = "declared"
	UndefinedS State = "undefined"
)

// Finding is one report line.
type Finding struct {
	Kind     string
	Field    string
	Dim      string
	State    State
	Severity string // error | warning | info
	Msg      string
}

// Report computes the three-state report from the committed pair for every
// kind. Errors: a declared field the generated contract lacks; an illegal
// combination; design-pending with generation present; a thought field not
// declared unconstrained; a defined constraint without a pattern; a
// measured_by that does not resolve (when repoRoot is given). Everything
// underivable-and-undeclared is a warning — header fields included.
func Report(dir Dir, gens []Generated, repoRoot string) ([]Finding, error) {
	var out []Finding
	for _, g := range gens {
		dc, err := dir.ReadDeclared(g.Kind)
		if err != nil {
			return nil, err
		}
		if dc == nil {
			out = append(out, Finding{Kind: g.Kind, Dim: "lifecycle", State: UndefinedS, Severity: "warning", Msg: "no declared file: lifecycle, provenance and every field dimension are undefined"})
			for _, f := range g.Fields {
				out = append(out, undefinedField(g.Kind, f)...)
			}
			continue
		}
		if dc.Kind != g.Kind {
			out = append(out, Finding{Kind: g.Kind, Dim: "generated", Severity: "error", Msg: fmt.Sprintf("declared file names kind %q", dc.Kind)})
		}
		switch dc.Lifecycle {
		case DesignPending:
			out = append(out, Finding{Kind: g.Kind, Dim: "lifecycle", State: DeclaredS, Severity: "error", Msg: "design-pending: shape unowned or contested — generation must be suppressed and an escalation file written beside the pair"})
		case HardenedLegacy:
			out = append(out, Finding{Kind: g.Kind, Dim: "lifecycle", State: DeclaredS, Severity: "warning", Msg: "hardened-legacy: " + dc.DesignFlag})
		}
		if dc.Provenance == Inferred {
			out = append(out, Finding{Kind: g.Kind, Dim: "provenance", State: DeclaredS, Severity: "info", Msg: "inferred from the implementation, not supplied by the owner — evidence of what IS, not what was MEANT"})
		}
		if dc.MeasuredBy != "" && repoRoot != "" {
			if err := MeasuredByResolves(repoRoot, dc.MeasuredBy); err != nil {
				out = append(out, Finding{Kind: g.Kind, Dim: "measured_by", State: DeclaredS, Severity: "error", Msg: err.Error()})
			}
		}
		byWire := map[string]GeneratedField{}
		for _, f := range g.Fields {
			byWire[f.Wire] = f
		}
		for _, name := range sortedKeys(dc.Fields) {
			d := dc.Fields[name]
			f, ok := byWire[name]
			if !ok {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "generated", State: DeclaredS, Severity: "error", Msg: ErrDeclaredFieldUnknown.Error()})
				continue
			}
			if d.UsedFor == "authorization" && d.UnknownValue == "accepted-unchanged" {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "unknown_value", State: DeclaredS, Severity: "error", Msg: ErrIllegal.Error() + ": an unknown value may not flow into an authorization decision"})
			}
			if f.IsThought && d.Constraint != Unconstrained {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "constraint", State: DeclaredS, Severity: "error", Msg: ErrIllegal.Error() + ": thought fields are declared unconstrained by decree (D16)"})
			}
			if d.Constraint == Defined && d.Pattern == "" {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "constraint", State: DeclaredS, Severity: "error", Msg: "constraint defined without a pattern — a defined constraint must be executable"})
			}
			if d.MeasuredBy != "" && repoRoot != "" {
				if err := MeasuredByResolves(repoRoot, d.MeasuredBy); err != nil {
					out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "measured_by", State: DeclaredS, Severity: "error", Msg: err.Error()})
				}
			}
			if f.Omittable && d.Absence == "" {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "absence", State: UndefinedS, Severity: "warning", Msg: "absence representable on the wire but its wire form is undeclared"})
			}
			if f.Omittable && d.OnAbsence == "" {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "on_absence", State: UndefinedS, Severity: "warning", Msg: "observable behaviour on absence undeclared"})
			}
			if d.UnknownValue == "" && strings.HasSuffix(f.GoType, "string") && d.UsedFor != "" && d.UsedFor != "display-only" {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "unknown_value", State: UndefinedS, Severity: "warning", Msg: "string field with a use but no unknown-value handling"})
			}
			if d.Constraint == "" || d.Constraint == Undefined {
				out = append(out, Finding{Kind: g.Kind, Field: name, Dim: "constraint", State: UndefinedS, Severity: "warning", Msg: "nobody looked: a latent seam, not a bug today"})
			}
		}
		for _, f := range g.Fields {
			if _, declared := dc.Fields[f.Wire]; !declared {
				out = append(out, undefinedField(g.Kind, f)...)
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Kind != out[j].Kind {
			return out[i].Kind < out[j].Kind
		}
		if out[i].Field != out[j].Field {
			return out[i].Field < out[j].Field
		}
		return out[i].Dim < out[j].Dim
	})
	return out, nil
}

func undefinedField(kind string, f GeneratedField) []Finding {
	fs := []Finding{{Kind: kind, Field: f.Wire, Dim: "constraint", State: UndefinedS, Severity: "warning", Msg: "field has no declared line — every dimension undefined"}}
	if f.IsThought {
		fs = append(fs, Finding{Kind: kind, Field: f.Wire, Dim: "constraint", State: UndefinedS, Severity: "error", Msg: "thought field must be declared unconstrained (D16); undeclared is not the same as unconstrained"})
	}
	return fs
}

// MeasuredByResolves checks that a measured_by claim points at something
// runnable: "<dir>:<TestName>" where <dir>/*_test.go declares func TestName.
// "not-re-runnable-here" is the one legal non-pointer.
var testDecl = regexp.MustCompile(`(?m)^func (Test[A-Za-z0-9_]+)\(`)

func MeasuredByResolves(repoRoot, claim string) error {
	if claim == "not-re-runnable-here" {
		return nil
	}
	dir, name, ok := strings.Cut(claim, ":")
	if !ok || name == "" || !strings.HasPrefix(name, "Test") {
		return fmt.Errorf("measured_by %q is not \"<dir>:<TestName>\" or \"not-re-runnable-here\"", claim)
	}
	matches, _ := filepath.Glob(filepath.Join(repoRoot, dir, "*_test.go"))
	for _, m := range matches {
		raw, err := os.ReadFile(m)
		if err != nil {
			continue
		}
		for _, sm := range testDecl.FindAllStringSubmatch(string(raw), -1) {
			if sm[1] == name {
				return nil
			}
		}
	}
	return fmt.Errorf("measured_by %q does not resolve to a test function in %s/*_test.go", claim, dir)
}

// Errors filters a report to its errors.
func Errors(fs []Finding) []Finding {
	var e []Finding
	for _, f := range fs {
		if f.Severity == "error" {
			e = append(e, f)
		}
	}
	return e
}

// Warnings filters a report to its warnings.
func Warnings(fs []Finding) []Finding {
	var w []Finding
	for _, f := range fs {
		if f.Severity == "warning" {
			w = append(w, f)
		}
	}
	return w
}

// Render prints a report as text.
func Render(fs []Finding) string {
	var b strings.Builder
	for _, f := range fs {
		fmt.Fprintf(&b, "%-7s %-16s %-24s %-13s %s\n", f.Severity, f.Kind, f.Field, f.Dim, f.Msg)
	}
	return b.String()
}
