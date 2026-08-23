package jsonx

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"testing"
)

// carve is a TRANSCRIPTION of llm_parse._find_json_bounds, so the only
// honest test of it is the transcription itself: drive CPython's function
// and Go's over the same corpus and compare the SPAN, not a hand-written
// expectation. Two divergences hid behind hand-written expectations
// before this file existed — the string-literal tracking, and an
// IndexByte start that disagreed with Python's depth bookkeeping whenever
// a stray close bracket came first.

func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

const pyBoundsSnippet = `
import json, sys, llm_parse
out = []
for text, open_c, close_c in json.loads(sys.argv[1]):
    start, end = llm_parse._find_json_bounds(text, open_c, close_c)
    out.append(text[start:end] if start >= 0 else None)
print(json.dumps(out))
`

type carveCase struct {
	name string
	text string
	open byte
}

// carveCorpus is a package var so the anti-vacuity guard below reads the
// SAME list the differential does.
var carveCorpus = []carveCase{
	{"a plain object", `{"a":1}`, '{'},
	{"a plain array", `[1,2]`, '['},
	{"prose on both sides", `sure: {"a":1} hope that helps`, '{'},
	{"nesting", `{"a":{"b":[1,{"c":2}]}}`, '{'},

	// The string-literal blindness. A BALANCED pair inside a string is
	// carved identically by a naive and a quote-aware scan, which is how
	// the divergence hid; only an UNBALANCED one separates them.
	{"a balanced bracket pair inside a string", `["use x[0] to index"]`, '['},
	{"an UNBALANCED close inside a string ends the span early",
		`{"note":"has } inside"}`, '{'},
	{"an unbalanced open inside a string never closes",
		`{"note":"has { inside"}`, '{'},

	// The depth bookkeeping. A stray CLOSE ahead of the payload drives
	// depth negative and CPython finds NO bounds at all — an IndexByte
	// start would carve the payload out and fork the record.
	{"a stray close before the payload kills the whole search",
		`x } y {"b":2} z`, '{'},
	{"a stray close before an array likewise", `]] [1,2]`, '['},
	{"two strays, then a payload", `} } {"a":1}`, '{'},
	{"a stray close AFTER the payload is harmless", `{"a":1} }`, '{'},
	{"a stray open before the payload swallows it",
		`{ then {"a":1}`, '{'},

	// Absent / unbalanced.
	{"no bracket at all", `no json here`, '{'},
	{"an unterminated object", `{"a":1`, '{'},
	{"only a close bracket", `}`, '{'},
	{"the empty string", ``, '{'},

	// Multi-byte text: CPython indexes RUNES and slices runes, Go indexes
	// bytes and slices bytes. The substring must still agree.
	{"multi-byte prose around the payload", `日本語 {"a":"語"} 語`, '{'},
	{"a multi-byte string containing a brace", `{"a":"日}語"}`, '{'},
}

func pyBounds(t *testing.T, cases []carveCase) []*string {
	t.Helper()
	type triple = [3]string
	req := make([]triple, 0, len(cases))
	for _, c := range cases {
		open, close := "{", "}"
		if c.open == '[' {
			open, close = "[", "]"
		}
		req = append(req, triple{c.text, open, close})
	}
	b, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyBoundsSnippet, string(b))
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe FAILED (exit %d):\n%s",
				ee.ExitCode(), ee.Stderr)
		}
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("python3 is present but the probe could not run: %v", err)
	}
	var spans []*string
	if err := json.Unmarshal(out, &spans); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}
	if len(spans) != len(cases) {
		t.Fatalf("CPython returned %d spans for %d cases", len(spans), len(cases))
	}
	return spans
}

func TestCarveMatchesFindJSONBounds(t *testing.T) {
	want := pyBounds(t, carveCorpus)
	for i, tc := range carveCorpus {
		t.Run(tc.name, func(t *testing.T) {
			close := byte('}')
			if tc.open == '[' {
				close = ']'
			}
			got, err := carve(tc.text, tc.open, close)
			if want[i] == nil {
				if err == nil {
					t.Fatalf("go carved %q; CPython found no bounds", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("go found no bounds (%v); CPython carved %q",
					err, *want[i])
			}
			if got != *want[i] {
				t.Fatalf("span differs\n go %q\n py %q", got, *want[i])
			}
		})
	}
}

// The corpus is only worth having if CPython actually reaches BOTH
// outcomes on it, and reaches the found outcome with more than one span.
// A corpus that only ever produced (-1, -1) would pass against a carve
// that is `return "", err`.
func TestTheCarveCorpusReachesBothOutcomes(t *testing.T) {
	spans := pyBounds(t, carveCorpus)
	found, missing := map[string]bool{}, 0
	for _, s := range spans {
		if s == nil {
			missing++
			continue
		}
		found[*s] = true
	}
	if missing == 0 || len(found) < 2 {
		t.Fatalf("CPython reached %d distinct spans and %d no-bounds cases; "+
			"the corpus cannot discriminate", len(found), missing)
	}
}
