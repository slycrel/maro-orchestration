package contracts

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The provider takes both chairs: it emits, and it reads its own payload as a
// client must. Every declared compatibility rule must be EXERCISED by the
// reference reader; a rule nothing exercised is reported, not passed.

// WireValidator is implemented by record types that reject unknown
// vocabulary values on the wire. A declared `unknown_value: rejected` on a
// field is executable only if the type implements this.
type WireValidator interface{ ValidateWire() error }

// Exercised is what a reference-reader run actually touched, so a fixture
// that touched nothing cannot pass.
type Exercised struct {
	ForwardUnknownField bool
	UnknownValueRules   int // declared unknown_value rules exercised
	PatternRules        int // declared patterns run against the sample (positive) and the rejects fixture (negative)
	RemovedOptionals    int // backward: fields removed
	OnAbsenceRules      int // backward: declared on_absence rules checked
}

// ForwardRead proves a newer payload reads. For every declared unknown_value
// rule on a string field (header fields included, via dotted paths): inject
// an unknown value and assert the declared behaviour exactly —
// accepted-unchanged: the value survives byte-for-byte; rejected: the type's
// ValidateWire refuses it; replaced-with:<v>: the decoded value is v. Then
// inject an unknown top-level key and assert the known fields survive.
func ForwardRead(spec record.Spec, sample any, dec *Declared) (Exercised, error) {
	var ex Exercised
	base, err := toMap(sample)
	if err != nil {
		return ex, err
	}
	if dec != nil {
		for _, name := range sortedKeys(dec.Fields) {
			d := dec.Fields[name]
			if d.UnknownValue == "" {
				continue
			}
			cur, ok := getPath(base, name)
			if !ok {
				continue
			}
			if _, isStr := cur.(string); !isStr {
				continue
			}
			m := clone(base)
			setPath(m, name, "future-value")
			v := reflect.New(spec.Type).Interface()
			raw, _ := json.Marshal(m)
			derr := json.Unmarshal(raw, v)
			switch {
			case d.UnknownValue == "accepted-unchanged":
				if derr != nil {
					return ex, fmt.Errorf("forward %s.%s: accepted-unchanged but decode failed: %v", spec.Kind, name, derr)
				}
				back, _ := toMap(v)
				got, _ := getPath(back, name)
				if got != "future-value" {
					return ex, fmt.Errorf("forward %s.%s: accepted-unchanged but value became %v", spec.Kind, name, got)
				}
			case d.UnknownValue == "rejected":
				wv, ok := v.(WireValidator)
				if !ok {
					return ex, fmt.Errorf("forward %s.%s: declared rejected but %T has no ValidateWire — the rule is not executable", spec.Kind, name, v)
				}
				if derr == nil && wv.ValidateWire() == nil {
					return ex, fmt.Errorf("forward %s.%s: declared rejected but an unknown value was accepted", spec.Kind, name)
				}
			case strings.HasPrefix(d.UnknownValue, "replaced-with:"):
				want := strings.TrimPrefix(d.UnknownValue, "replaced-with:")
				back, _ := toMap(v)
				got, _ := getPath(back, name)
				if derr != nil || got != want {
					return ex, fmt.Errorf("forward %s.%s: declared replaced-with:%s, got %v (%v)", spec.Kind, name, want, got, derr)
				}
			}
			ex.UnknownValueRules++
		}
		// Patterns: the sample must match (positive); `rejects` must not (negative, checked at load).
		for _, name := range sortedKeys(dec.Fields) {
			d := dec.Fields[name]
			re := d.CompiledPattern()
			if re == nil {
				continue
			}
			cur, ok := getPath(base, name)
			if !ok {
				return ex, fmt.Errorf("pattern %s.%s: sample has no value to test", spec.Kind, name)
			}
			if !re.MatchString(fmt.Sprint(cur)) {
				return ex, fmt.Errorf("pattern %s.%s: sample value %v does not match declared %q", spec.Kind, name, cur, d.Pattern)
			}
			ex.PatternRules++
		}
	}
	m := clone(base)
	m["__future_field"] = map[string]any{"unknown": true}
	raw, _ := json.Marshal(m)
	v := reflect.New(spec.Type).Interface()
	if err := json.Unmarshal(raw, v); err != nil {
		return ex, fmt.Errorf("forward read of %s failed on an unknown top-level field: %w", spec.Kind, err)
	}
	back, _ := toMap(v)
	for k := range base {
		if _, ok := back[k]; !ok {
			return ex, fmt.Errorf("forward read of %s dropped known field %q", spec.Kind, k)
		}
	}
	ex.ForwardUnknownField = true
	return ex, nil
}

// BackwardRead proves an older payload reads: remove EVERY omittable field
// (header fields included, via dotted paths), decode, and check each removed
// field's declared on_absence: tolerated → the zero value; default:<v> → v;
// rejected → the type's ValidateWire refuses. A sample from which nothing
// could be removed is an error: the fixture cannot fail, so it proves nothing.
func BackwardRead(spec record.Spec, sample any, gen *Generated, dec *Declared) (Exercised, error) {
	var ex Exercised
	base, err := toMap(sample)
	if err != nil {
		return ex, err
	}
	m := clone(base)
	var removed []GeneratedField
	for _, f := range gen.Fields {
		if !f.Omittable {
			continue
		}
		if _, ok := getPath(m, f.Wire); ok {
			delPath(m, f.Wire)
			removed = append(removed, f)
		}
	}
	if len(removed) == 0 {
		return ex, fmt.Errorf("backward read of %s: the sample has no omittable field present — the fixture cannot fail; give the sample values for its optionals", spec.Kind)
	}
	ex.RemovedOptionals = len(removed)
	older, _ := json.Marshal(m)
	v := reflect.New(spec.Type).Interface()
	derr := json.Unmarshal(older, v)
	back, _ := toMap(v)
	for _, f := range removed {
		var d DeclaredField
		if dec != nil {
			d = dec.Fields[f.Wire]
		}
		switch {
		case d.OnAbsence == "rejected":
			wv, ok := v.(WireValidator)
			if !ok {
				return ex, fmt.Errorf("backward %s.%s: declared on_absence rejected but %T has no ValidateWire", spec.Kind, f.Wire, v)
			}
			if derr == nil && wv.ValidateWire() == nil {
				return ex, fmt.Errorf("backward %s.%s: declared rejected on absence but the reader accepted it", spec.Kind, f.Wire)
			}
			ex.OnAbsenceRules++
		case strings.HasPrefix(d.OnAbsence, "default:"):
			if derr != nil {
				return ex, fmt.Errorf("backward %s: %v", spec.Kind, derr)
			}
			want := strings.TrimPrefix(d.OnAbsence, "default:")
			got, present := getPath(back, f.Wire)
			// An omittable field that decoded to its zero value re-encodes as
			// absent; that IS the zero value, so a declared default equal to
			// the zero spelling ("0", "", "false") is satisfied by absence.
			gotS := fmt.Sprint(got)
			if !present {
				gotS = ""
			}
			zero := map[string]bool{"0": true, "": true, "false": true}
			if gotS != want && !(gotS == "" && zero[want]) {
				return ex, fmt.Errorf("backward %s.%s: declared default %q, reader produced %v", spec.Kind, f.Wire, want, got)
			}
			ex.OnAbsenceRules++
		case d.OnAbsence == "tolerated":
			if derr != nil {
				return ex, fmt.Errorf("backward %s.%s: declared tolerated but decode failed: %v", spec.Kind, f.Wire, derr)
			}
			ex.OnAbsenceRules++
		default:
			if derr != nil {
				return ex, fmt.Errorf("backward read of %s failed with %q absent: %v", spec.Kind, f.Wire, derr)
			}
		}
	}
	return ex, nil
}

func toMap(v any) (map[string]any, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func clone(m map[string]any) map[string]any {
	raw, _ := json.Marshal(m)
	var c map[string]any
	_ = json.Unmarshal(raw, &c)
	return c
}

func getPath(m map[string]any, path string) (any, bool) {
	parts := strings.Split(path, ".")
	var cur any = m
	for _, p := range parts {
		mm, ok := cur.(map[string]any)
		if !ok {
			return nil, false
		}
		cur, ok = mm[p]
		if !ok {
			return nil, false
		}
	}
	return cur, true
}

func setPath(m map[string]any, path string, v any) {
	parts := strings.Split(path, ".")
	cur := m
	for _, p := range parts[:len(parts)-1] {
		next, ok := cur[p].(map[string]any)
		if !ok {
			next = map[string]any{}
			cur[p] = next
		}
		cur = next
	}
	cur[parts[len(parts)-1]] = v
}

func delPath(m map[string]any, path string) {
	parts := strings.Split(path, ".")
	cur := m
	for _, p := range parts[:len(parts)-1] {
		next, ok := cur[p].(map[string]any)
		if !ok {
			return
		}
		cur = next
	}
	delete(cur, parts[len(parts)-1])
}
