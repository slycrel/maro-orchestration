// Package pyjson emits JSON the way CPython's json module does.
//
// It exists because "the same value" is not the contract across this port —
// the same BYTES are. Two runtimes write into one shared workspace, and
// several consumers key on the serialized text: Python's doctor dedup
// re-serializes a parsed row and compares strings, and every cross-runtime
// audit diffs files. encoding/json differs from json.dumps in FIVE ways that each produce a
// byte-different row for an identical value:
//
//	sorted keys      encoding/json orders a map alphabetically; a Python
//	                 dict emits in insertion order
//	HTML escaping    < > & become \u003c-style sequences; json.dumps
//	                 leaves them alone
//	whole floats     json.Marshal(1.0) is "1"; json.dumps(1.0) is "1.0"
//	separators       encoding/json writes `,` and `:`; json.dumps' DEFAULTS
//	                 are `, ` and `: `
//	ensure_ascii     encoding/json writes raw UTF-8; json.dumps escapes
//	                 every non-ASCII rune as \uXXXX
//
// Emitters were being written per-package, which is how those differences
// kept reappearing one file at a time.
//
// This package used to name only the first three, and implemented only
// those three — so every store routed through it (outcomes, skills, runs,
// the playbook) inherited the other two, compact and raw-UTF-8, in files
// CPython writes spaced and escaped. That is the r8 finding in its
// sharpest form: the shared emitter written to END per-package drift was
// itself an incomplete implementation, and being shared is what spread it.
// Verified against the real writers rather than assumed —
// memory_ledger.py:605 and skills.py:271 are bare `json.dumps(row)`, whose
// defaults are spaced and ensure_ascii=True. The three sites in the Python
// tree that DO pass separators=(",", ":") are an LLM API payload and a
// pack content hash, neither of which goes through this package.
package pyjson

import (
	"encoding/json"
	"errors"
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

// String encodes one string as a JSON literal with Python's escaping:
// HTML escaping OFF (json.dumps does not escape < > or &) and
// ensure_ascii ON (json.dumps escapes every non-ASCII rune as \uXXXX,
// which encoding/json never does at any setting).
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
	return ensureASCII(strings.TrimSuffix(buf.String(), "\n")), nil
}

// ensureASCII rewrites an already-valid JSON string literal so every
// non-ASCII rune becomes a \uXXXX escape, which is json.dumps' default.
//
// It runs over the ENCODED literal rather than the raw string so the
// escaping encoding/json already applied (quotes, backslashes, control
// characters) is preserved untouched — re-deriving that here would be a
// second escaper, which is the mistake this package exists to stop.
//
// Astral-plane runes are emitted as a SURROGATE PAIR, because that is what
// CPython does: json.dumps("\U0001F600") is "\ud83d\ude00", not
// "\U0001f600". A lone surrogate cannot reach here — IsCleanText refuses
// those upstream.
func ensureASCII(lit string) string {
	// The threshold is 0x7F, not 0x80. CPython's ESCAPE_ASCII is
	// `[^\ -~]` — anything outside 0x20..0x7E — so DEL is escaped even
	// though it IS ASCII, while Go's encoding/json emits it raw (RFC 8259
	// only requires escaping below 0x20). This package's own doc comment
	// has named that case since it was written; the code used
	// utf8.RuneSelf anyway, and every `\x7f` in a captured tool
	// transcript went to the shared store as a raw byte no CPython writer
	// produces (mission-r9, found by the python-maro cross-review).
	clean := true
	for i := 0; i < len(lit); i++ {
		if lit[i] >= 0x7F {
			clean = false
			break
		}
	}
	if clean {
		return lit // the overwhelmingly common case, untouched
	}
	var sb strings.Builder
	sb.Grow(len(lit) + 8)
	for _, r := range lit {
		if r < 0x7F {
			sb.WriteByte(byte(r))
			continue
		}
		if r > 0xFFFF {
			r -= 0x10000
			fmt.Fprintf(&sb, "\\u%04x\\u%04x",
				0xD800+(r>>10), 0xDC00+(r&0x3FF))
			continue
		}
		fmt.Fprintf(&sb, "\\u%04x", r)
	}
	return sb.String()
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
		lit := n.String()
		if !strings.ContainsAny(lit, ".eE") {
			// An INTEGER literal keeps its source spelling. Python's ints
			// are unbounded, json.loads keeps a 40-digit counter exact and
			// json.dumps re-emits it, so forcing it through int64 would
			// lose digits no round trip should lose. `7` stays `7`.
			return lit, nil
		}
		// A FLOAT literal does NOT keep its spelling, and echoing it was a
		// real divergence in every writer in this port. json.loads produces
		// a float and json.dumps writes `float.__repr__`, so the file is
		// REFORMATTED on every rewrite. Measured on CPython 3.14.3:
		//
		//	2.50   -> 2.5        1E5    -> 100000.0
		//	1e23   -> 1e+23      1.0e2  -> 100.0
		//	1e19   -> 1e+19      1e400  -> Infinity
		//
		// (0.30000000000000004 and -0.0 agree either way, which is why a
		// spot check missed it.) Found by a randomised cross-runtime
		// differential over task_store — one of the fixtures diverged in
		// the ON-DISK BYTES after `fail`, not merely in a returned value
		// (adversarial tasks-r1 HIGH).
		f, ferr := n.Float64()
		if ferr != nil && !errors.Is(ferr, strconv.ErrRange) {
			// Not a float at all. Nothing produces this — json.Number holds
			// what a decoder accepted — so echo rather than invent.
			return lit, nil
		}
		if math.IsNaN(f) || math.IsInf(f, 0) {
			// NAMED DIVERGENCE, deliberately left alone. CPython's
			// json.dumps defaults to allow_nan=True and writes `Infinity`;
			// this arm echoes `1e400`, which is valid JSON that both
			// runtimes read back as inf. The float64 arm below REFUSES
			// non-finite instead, because a Go-constructed inf is a bug in
			// the caller while a foreign literal is a file CPython wrote.
			// Making the two agree is a decision about allow_nan that
			// belongs with the writers, not here — BACKLOG.
			return lit, nil
		}
		return FloatRepr(f), nil
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
		// dedup quietly stopped collapsing them.
		return FloatRepr(f), nil
	}
	if err := RefuseNonFinite(v); err != nil {
		return "", err
	}
	// Containers render RECURSIVELY through this function rather than
	// through the generic encoder. Delegating meant every rule above
	// stopped applying one level down: a nested 1.0 came out as a bare 1,
	// which changes the type json.loads parses, and a nested "a -> b" came
	// out HTML-escaped. The captain's log's whole payload is a nested
	// context map, so "the top level is Python-shaped" was not worth much
	// (adversarial r3 2026-08-23, L2).
	switch t := v.(type) {
	case []any:
		return renderList(len(t), func(i int) any { return t[i] })
	case []string:
		return renderList(len(t), func(i int) any { return t[i] })
	case map[string]any:
		// NAMED DIVERGENCE, same one already accepted for nested
		// `imported`: Python emits a nested dict in its literal insertion
		// order and a Go map has none, so nested keys are SORTED. The two
		// differences that change a parsed VALUE — float spelling and HTML
		// escaping — are fixed; order is byte-level only, and the ordered
		// top level is available to any caller that needs it via Ordered.
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		return Ordered(t, keys)
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	return strings.TrimSuffix(buf.String(), "\n"), nil
}

func renderList(n int, at func(int) any) (string, error) {
	parts := make([]string, 0, n)
	for i := 0; i < n; i++ {
		one, err := Value(at(i))
		if err != nil {
			return "", err
		}
		parts = append(parts, one)
	}
	// json.dumps' default item separator carries a space.
	return "[" + strings.Join(parts, ", ") + "]", nil
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
	return Raw("[" + strings.Join(parts, ", ") + "]"), nil
}

// Ordered emits an object with the model's key ORDER rather than Go's
// alphabetical map ordering, so a row rewritten by either runtime reads the
// same way. Unknown keys — an operator's note, a forward-version field —
// ride AFTER the modeled ones, sorted, so they survive a rewrite
// deterministically. A modeled key absent from d is skipped.
//
// A key named TWICE in `modeled` is emitted ONCE, at its first position.
// The thing being modeled is a Python dict, and a dict cannot hold a key
// twice; emitting it twice produces a row that `json.loads` silently
// collapses but LoadsClean — this port's own admission predicate —
// refuses outright, so the row strands on the Go side while looking fine
// on the Python side. That is not hypothetical: StampOutcomeVerdict
// appends the verdict keys onto the row's on-disk key list, which already
// names them on any row that carries a prior verdict, and the row doubled
// in size on every re-stamp until Go could no longer read it
// (adversarial r4, H1). De-duplicating at the CALLER would have fixed
// that one site; de-duplicating here is what stops the next one.
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
	emitted := make(map[string]bool, len(modeled))
	for _, k := range modeled {
		if emitted[k] {
			continue // first position wins, as a dict's insertion order does
		}
		emitted[k] = true
		keys = append(keys, k)
	}
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
			sb.WriteString(", ") // json.dumps' default item separator
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
		sb.WriteString(": ") // json.dumps' default key separator
		sb.WriteString(val)
	}
	sb.WriteByte('}')
	return sb.String(), nil
}
