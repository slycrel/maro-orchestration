package contracts

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The provider takes both chairs: it emits, and it reads its own payload as a
// client must. Forward compatibility: a NEWER payload (extra fields, an unknown
// vocabulary value) still reads. Backward compatibility: an OLDER payload
// (absent optionals) still reads, with the declared absence semantics.

// ForwardRead proves a newer payload reads: encode the sample, inject an
// unknown top-level field and an unknown value into a string field the
// declared file marks accepted-unchanged, decode into the registered type, and
// re-encode; the known fields must survive and the unknown ones must not
// break the read.
func ForwardRead(spec record.Spec, sample any, dec *Declared) error {
	raw, err := json.Marshal(sample)
	if err != nil {
		return err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return err
	}
	m["__future_field"] = map[string]any{"unknown": true}
	if dec != nil {
		for name, d := range dec.Fields {
			if d.UnknownValue == "accepted-unchanged" && !strings.Contains(name, ".") {
				if _, ok := m[name].(string); ok {
					m[name] = "future-value"
				}
			}
		}
	}
	newer, _ := json.Marshal(m)
	v := reflect.New(spec.Type).Interface()
	if err := json.Unmarshal(newer, v); err != nil {
		return fmt.Errorf("forward read of %s failed on a newer payload: %w", spec.Kind, err)
	}
	back, _ := json.Marshal(v)
	var m2 map[string]any
	_ = json.Unmarshal(back, &m2)
	for k := range m {
		if k == "__future_field" {
			continue
		}
		if _, ok := m2[k]; !ok {
			if d, ok := dec.Fields[k]; ok && d.Absence == "omitted" {
				continue
			}
			return fmt.Errorf("forward read of %s dropped known field %q", spec.Kind, k)
		}
	}
	return nil
}

// BackwardRead proves an older payload reads: remove every omittable field
// from the encoded sample and decode; the declared on_absence must be
// tolerated or default for each removed field, else this is the test that
// fails.
func BackwardRead(spec record.Spec, sample any, gen *Generated, dec *Declared) error {
	raw, err := json.Marshal(sample)
	if err != nil {
		return err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return err
	}
	removed := 0
	for _, f := range gen.Fields {
		if !f.Omittable || f.FromHeader {
			continue
		}
		if _, ok := m[f.Wire]; ok {
			delete(m, f.Wire)
			removed++
			if dec != nil {
				if d, ok := dec.Fields[f.Wire]; ok && d.OnAbsence == "rejected" {
					return fmt.Errorf("backward read of %s: field %q declared on_absence=rejected — an older payload without it is by declaration unreadable; the declared line must say what the reader does", spec.Kind, f.Wire)
				}
			}
		}
	}
	older, _ := json.Marshal(m)
	v := reflect.New(spec.Type).Interface()
	if err := json.Unmarshal(older, v); err != nil {
		return fmt.Errorf("backward read of %s failed with %d optionals absent: %w", spec.Kind, removed, err)
	}
	return nil
}
