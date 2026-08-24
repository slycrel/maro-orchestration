package pyjson

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

// TestOrderedMatchesCPythonByteForByte is the test this package did not
// have, and not having it is why two of the five forks lived here for
// months while every store that routes through the package inherited them.
//
// Every expectation in pyjson_test.go was TRANSCRIBED — a Go literal
// asserting what the author believed json.dumps produced. A transcription
// cannot disagree with its author. This one runs CPython.
//
// The corpus is chosen so each fork is load-bearing at least once, and the
// anti-vacuity guard at the bottom requires encoding/json to LOSE on every
// case: a corpus where the stdlib already agrees would report success
// while testing nothing (the r6 lens, applied to the emitter itself).
func TestOrderedMatchesCPythonByteForByte(t *testing.T) {
	cases := []struct {
		name    string
		d       map[string]any
		modeled []string
	}{
		{
			// Insertion order that is NOT alphabetical, so sorting shows.
			"key order", map[string]any{"z": 1.0, "a": 2.0, "m": 3.0},
			[]string{"z", "a", "m"},
		},
		{
			// The arrow every lesson this system mints contains.
			"html chars", map[string]any{"lesson": `prefer a > b & not c < d`},
			[]string{"lesson"},
		},
		{
			// ensure_ascii, across the BMP and the astral plane. CPython
			// spells an astral rune as a surrogate PAIR.
			"non ascii", map[string]any{"a": "café", "b": "日本語", "c": "smile 😀"},
			[]string{"a", "b", "c"},
		},
		{
			// Whole floats vs ints — different JSON types after a reparse.
			"numbers", map[string]any{"whole": 1.0, "int": 1, "frac": 0.25,
				"neg": -0.0, "big": 1234567.8},
			[]string{"whole", "int", "frac", "neg", "big"},
		},
		{
			// Nested containers: every rule must still apply one level down.
			"nested", map[string]any{
				"list":  []any{1.0, "a > b", "café"},
				"empty": []any{},
				"obj":   map[string]any{"k": 1.0},
			},
			[]string{"list", "empty", "obj"},
		},
		{
			// The escapes encoding/json and json.dumps agree on, kept in the
			// corpus so a future rewrite of the escaper cannot break them
			// quietly.
			"control chars", map[string]any{"s": "a\tb\nc\"d\\e\u0001f"},
			[]string{"s"},
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := Ordered(c.d, c.modeled)
			if err != nil {
				t.Fatal(err)
			}
			want := cpythonDumps(t, c.d, c.modeled)
			if got != want {
				t.Errorf("Ordered is not json.dumps' bytes:\n go %s\n py %s", got, want)
			}

			// Anti-vacuity: the stdlib, required to lose on THIS case.
			old, err := json.Marshal(c.d)
			if err != nil {
				t.Fatal(err)
			}
			if string(old) == want {
				t.Errorf("encoding/json already produces json.dumps' bytes for "+
					"%q, so this case tests nothing:\n%s", c.name, old)
			}
		})
	}
}

// TestStringMatchesCPython covers the scalar arm on its own, because
// Ordered composes it and a composed test can hide which half is wrong.
func TestStringMatchesCPython(t *testing.T) {
	for _, s := range []string{
		"plain", "a > b & c < d", "café", "日本語", "😀", "tab\there",
		"quote\"backslash\\", "\u0001\u001f", "surrogate-free \uFFFD",
		"mixed café > 😀",
		// The ensure_ascii BOUNDARY, walked one code point at a time.
		// The corpus above stopped at \u001f and the next case anyone
		// thinks of is a non-ASCII rune — which skips 0x7F entirely.
		// CPython's ESCAPE_ASCII is `[^\ -~]`, i.e. outside 0x20..0x7E,
		// so DEL is escaped despite BEING ASCII while Go's encoding/json
		// emits it raw. This package's doc comment named that case from
		// the start; the code used utf8.RuneSelf and shipped a raw byte
		// no CPython writer produces (mission-r9).
		"~",      // 0x7E — the last unescaped code point
		"\u007f", // 0x7F — DEL, escaped by Python, raw in Go until r9
		"\u0080", // 0x80 — the first code point Go would have caught
		"a\u007fb",
		"tail\u007f",
		"\u007f\u007f",
		// The two control bytes with SHORT escapes on both sides. A
		// cross-review called these a divergence; measured, they agree —
		// Go's encoder special-cases them exactly as ESCAPE_DCT does.
		// Pinned because "we checked and they match" is only durable as a
		// test (verify-before-fix, mission-r9).
		"a\bc", "a\fc",
		// U+2028/U+2029: Go's encoder escapes these unconditionally even
		// with SetEscapeHTML(false), and ensure_ascii escapes them on the
		// Python side, so the two agree by two different routes. Pinned
		// because an agreement reached by different routes is the fragile
		// kind — a future rewrite of either route breaks it silently.
		"line\u2028sep", "para\u2029sep",
	} {
		got, err := String(s)
		if err != nil {
			t.Fatalf("String(%q): %v", s, err)
		}
		want := cpythonDumpsScalar(t, s)
		if got != want {
			t.Errorf("String(%q) is not json.dumps' bytes:\n go %s\n py %s", s, got, want)
		}
	}
}

// cpythonDumps rebuilds the dict in the modeled order and renders it with
// the real json.dumps.
//
// The transport is a list of [key, value] PAIRS, not an object: an object
// would arrive at Python in whatever order Go's encoder emitted, and the
// whole point of the comparison is the order. Floats are tagged, because
// encoding/json hands a whole float over as `1` and Python would answer
// `1` — which reads exactly like a real divergence and is not one (the
// transport hazard r7 named and r8 keeps re-earning).
func cpythonDumps(t *testing.T, d map[string]any, modeled []string) string {
	t.Helper()
	pairs := make([]any, 0, len(modeled))
	for _, k := range modeled {
		v, ok := d[k]
		if !ok {
			continue
		}
		pairs = append(pairs, []any{k, tagged(v)})
	}
	in, err := json.Marshal(pairs)
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimRight(runCPython(t, untagSrc+
		"pairs = json.loads(sys.argv[1])\n"+
		"print(json.dumps({k: untag(v) for k, v in pairs}))", string(in)), "\n")
}

func cpythonDumpsScalar(t *testing.T, s string) string {
	t.Helper()
	in, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimRight(runCPython(t,
		"import json,sys\nprint(json.dumps(json.loads(sys.argv[1])))", string(in)), "\n")
}

// untagSrc restores the tagged floats and ints on the Python side.
const untagSrc = "import json,sys\n" +
	"def untag(v):\n" +
	"    if isinstance(v, dict) and '__f__' in v:\n" +
	"        return float(v['__f__'])\n" +
	"    if isinstance(v, dict) and '__i__' in v:\n" +
	"        return int(v['__i__'])\n" +
	"    if isinstance(v, dict) and '__o__' in v:\n" +
	"        return {k: untag(x) for k, x in v['__o__']}\n" +
	"    if isinstance(v, list):\n" +
	"        return [untag(x) for x in v]\n" +
	"    return v\n"

// tagged wraps numbers so their Go TYPE survives the JSON transport, and
// recurses so a nested whole float is not flattened on the way over.
func tagged(v any) any {
	switch t := v.(type) {
	case float64:
		return map[string]any{"__f__": FloatRepr(t)}
	case int:
		return map[string]any{"__i__": t}
	case []any:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = tagged(e)
		}
		return out
	case map[string]any:
		// A nested object also needs its order carried, and pyjson SORTS
		// nested maps (a Go map has no order) — so the pairs are sorted
		// here too, which is the accepted nested-order divergence stated
		// explicitly rather than hidden in a normalizer.
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sortStrings(keys)
		pairs := make([]any, 0, len(keys))
		for _, k := range keys {
			pairs = append(pairs, []any{k, tagged(t[k])})
		}
		return map[string]any{"__o__": pairs}
	}
	return v
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

func runCPython(t *testing.T, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	out, err := exec.Command("python3", append([]string{"-c", src}, args...)...).Output()
	if err != nil {
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	return string(out)
}
