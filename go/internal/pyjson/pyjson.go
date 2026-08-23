// Package pyjson emits JSON the way CPython's json module does.
//
// It exists because "the same value" is not the contract across this port —
// the same BYTES are. Two runtimes write into one shared workspace, and
// several consumers key on the serialized text: Python's doctor dedup
// re-serializes a parsed row and compares strings, and every cross-runtime
// audit diffs files. encoding/json differs from json.dumps in three ways
// that all produce a byte-different row for an identical value: it sorts
// map keys, it escapes < > and & as <-style sequences, and it spells a
// whole float without its ".0".
//
// Emitters were being written per-package, which is how those three
// differences kept reappearing one file at a time.
package pyjson

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// IsCleanText reports whether a string may be written: valid UTF-8 with no
// surrogate code points. Go strings can carry arbitrary bytes, so this is
// the writer-side twin of a reader's decode check.
func IsCleanText(s string) bool {
	if !utf8.ValidString(s) {
		return false
	}
	for _, r := range s {
		if r >= 0xD800 && r <= 0xDFFF {
			return false
		}
	}
	return true
}

// String encodes one string as a JSON literal with Python's escaping —
// HTML escaping OFF, since json.dumps does not escape < > or &.
func String(s string) (string, error) {
	if !IsCleanText(s) {
		return "", fmt.Errorf("byte-tainted text refused")
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(s); err != nil {
		return "", err
	}
	return strings.TrimSuffix(buf.String(), "\n"), nil
}

// Raw is an already-rendered fragment, emitted verbatim. It exists so a
// caller can compose a nested ARRAY of ordered objects — Go structs and
// slices reach the generic encoder, which spells a whole float without its
// ".0", so a nested match_score of 2.0 came out as 2 even with key order
// and escaping already handled.
type Raw string

// Value renders one value.
func Value(v any) (string, error) {
	if r, ok := v.(Raw); ok {
		return string(r), nil
	}
	if s, ok := v.(string); ok {
		return String(s)
	}
	if n, ok := v.(json.Number); ok {
		return n.String(), nil // keep the source literal (7 stays 7)
	}
	if f, ok := v.(float64); ok {
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return "", fmt.Errorf("non-finite number refused")
		}
		// json.dumps spells a float with float.__repr__, so a whole-valued
		// one keeps its ".0" where Go's encoder writes a bare "1". Both
		// readers admit both spellings, but success_rate is IN Python's
		// doctor dedup identity, so a Go-written row and a Python-written
		// row describing the same skill stopped comparing equal and the
		// dedup quietly stopped collapsing them. Narrow known gap: at
		// magnitudes where Go's shortest-'g' and Python's repr pick
		// different exponent thresholds the spellings still differ; every
		// field emitted through here is a rate or a small counter.
		out := strconv.FormatFloat(f, 'g', -1, 64)
		if !strings.ContainsAny(out, ".e") {
			out += ".0"
		}
		return out, nil
	}
	// Nested structures go through the generic encoder — but its non-finite
	// refusal only ever sees the TOP level, so a nested 1e400 was emitted
	// verbatim while Python's json.dumps(..., allow_nan=False) refused the
	// whole write.
	if err := RefuseNonFinite(v); err != nil {
		return "", err
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	return strings.TrimSuffix(buf.String(), "\n"), nil
}

// RefuseNonFinite walks a decoded value for a number Python would have
// parsed to inf or nan.
//
// An INTEGER literal is never non-finite however long it is: Python's ints
// are unbounded, json.loads keeps it exact and json.dumps re-emits it. Only
// a float literal — one carrying a point or an exponent — can overflow, so
// the range check is scoped to those. Checking every literal with
// ParseFloat would refuse a large integer counter both runtimes handle
// perfectly well.
func RefuseNonFinite(v any) error {
	switch t := v.(type) {
	case float64:
		if math.IsNaN(t) || math.IsInf(t, 0) {
			return fmt.Errorf("non-finite number refused")
		}
	case json.Number:
		lit := t.String()
		if !strings.ContainsAny(lit, ".eE") {
			return nil // integer literal: exact in Python, whatever its size
		}
		f, err := t.Float64()
		if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
			return fmt.Errorf("non-finite number refused: %s", lit)
		}
	case []any:
		for _, e := range t {
			if err := RefuseNonFinite(e); err != nil {
				return err
			}
		}
	case map[string]any:
		for _, e := range t {
			if err := RefuseNonFinite(e); err != nil {
				return err
			}
		}
	}
	return nil
}

// Array renders a list of objects, each with the same key order.
func Array(items []map[string]any, modeled []string) (Raw, error) {
	var parts []string
	for _, it := range items {
		one, err := Ordered(it, modeled)
		if err != nil {
			return "", err
		}
		parts = append(parts, one)
	}
	return Raw("[" + strings.Join(parts, ",") + "]"), nil
}

// Ordered emits an object with the model's key ORDER rather than Go's
// alphabetical map ordering, so a row rewritten by either runtime reads the
// same way. Unknown keys — an operator's note, a forward-version field —
// ride AFTER the modeled ones, sorted, so they survive a rewrite
// deterministically. A modeled key absent from d is skipped.
func Ordered(d map[string]any, modeled []string) (string, error) {
	seen := map[string]bool{}
	var extras []string
	for k := range d {
		seen[k] = true
	}
	for _, k := range modeled {
		delete(seen, k)
	}
	for k := range seen {
		extras = append(extras, k)
	}
	sort.Strings(extras)

	// A fresh slice: appending onto the caller's `modeled` would write into
	// its backing array whenever it has spare capacity, corrupting a shared
	// key-order literal across concurrent marshals.
	keys := make([]string, 0, len(modeled)+len(extras))
	keys = append(keys, modeled...)
	keys = append(keys, extras...)

	var sb strings.Builder
	sb.WriteByte('{')
	first := true
	for _, k := range keys {
		v, present := d[k]
		if !present {
			continue
		}
		if !first {
			sb.WriteByte(',')
		}
		first = false
		key, err := String(k)
		if err != nil {
			return "", err
		}
		val, err := Value(v)
		if err != nil {
			return "", err
		}
		sb.WriteString(key)
		sb.WriteByte(':')
		sb.WriteString(val)
	}
	sb.WriteByte('}')
	return sb.String(), nil
}
