package pyval

import (
	"encoding/json"
	"math"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Str and Repr are DIFFERENTIAL-tested: the corpus is JSON documents,
// and CPython is asked what `str(json.loads(doc))` and
// `repr(json.loads(doc))` are. Reconstructing the expectation in Go
// would only pin my reading of Python's repr rules, which is the exact
// failure mode this port keeps producing.
//
// This matters because mission decomposition runs every LLM-supplied
// title, feature and criterion through `str(x).strip()`. A model that
// answers `"title": {"a": 1}` writes the literal eight characters
// `{'a': 1}` into a shared store.

// strCorpus is one JSON document per hazard, not happy-path text.
var strCorpus = []string{
	`null`, `true`, `false`,
	`0`, `-0`, `1`, `-17`, `10000000000000000000000000`,
	`1.0`, `-1.0`, `0.0`, `-0.0`, `1e2`, `1E2`, `1.5e-7`, `100.0`,
	`0.1`, `0.30000000000000004`, `1e308`,
	`""`, `"x"`, `"  padded  "`,
	`"it's"`,       // repr flips to double quotes
	`"say \"hi\""`, // and back to single
	`"both ' and \""`,
	"\"tab\\there\"", `"nl\nhere"`, `"cr\rhere"`,
	`"back\\slash"`,
	"\"\\u0000\"", "\"\\u0007\"", "\"\\u001f\"", // C0 controls
	"\"\\u00a0\"", "\"\\u200b\"", "\"\\u2028\"", // non-printable by Python's rule
	`"héllo"`, `"日本語"`, `"👋"`,
	`[]`, `{}`,
	`[1]`, `[1,2,3]`, `["a","b"]`, `[null,true,1.0]`,
	`{"a":1}`, `{"a":"b"}`, `{"b":1,"a":2}`, // INSERTION order, not sorted
	`{"a":{"b":[1,{"c":null}]}}`,
	`[[],{},[{}]]`,
	`{"'":"'"}`,
	`{"key with space":[1.0,"x"]}`,
	`[{"features":null}]`,
	`{"":""}`,

	// The non-finite family, which crosses the boundary TWICE: CPython's
	// json.loads accepts the bare tokens and Go's decoder rejects them
	// (killing the whole document, not one field), and an out-of-range
	// literal comes back from Number.Float64 as ±Inf TOGETHER WITH a
	// range error. The corpus used to stop at 1e308 and a 3000-document
	// fuzz found 46 mismatches, all of this one family (mission-r1).
	`1e309`, `-1e309`, `-4e323`, `1e-400`, `-1e-400`,
	`NaN`, `Infinity`, `-Infinity`,
	`[NaN,1.0]`, `[Infinity,-Infinity]`,
	`{"a":NaN,"b":1}`,
	`{"a":Infinity}`,

	// ...and the strings that merely LOOK like them. Masking must respect
	// string boundaries in both directions, or a milestone titled "NaN"
	// becomes a float.
	`"NaN"`, `"Infinity"`, `"-Infinity"`,
	`{"NaN":"Infinity"}`,
	`"a NaN inside a sentence"`,
	`"escaped \" then Infinity"`,
	`["NaN",NaN]`,

	// The masking markers themselves, supplied by the input — plainly,
	// and spelled with \uXXXX escapes so that the marker is present in
	// the DECODED string while absent from the raw text. Both must stay
	// strings, including alongside a real masked token in the same
	// document. This is what the two-decode scheme buys.
	`"__pyval_nonfiniteA__NaN"`,
	`"__pyval_nonfiniteB__Infinity"`,
	`{"a":NaN,"b":"__pyval_nonfiniteA__Infinity"}`,
	`["__pyval_nonfiniteA__NaN",NaN,Infinity]`,
	`{"a":NaN,"b":"\u005f\u005fpyval_nonfiniteA__Infinity"}`,
}

func pythonStrRepr(t *testing.T, docs []string) (strs, reprs []string) {
	t.Helper()
	in, err := json.Marshal(docs)
	if err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("python3", "-c",
		"import json,sys\n"+
			"docs=json.loads(sys.argv[1])\n"+
			"vals=[json.loads(d) for d in docs]\n"+
			"print(json.dumps({'str':[str(v) for v in vals],"+
			"'repr':[repr(v) for v in vals]}))", string(in)).Output()
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
	var got struct {
		Str  []string `json:"str"`
		Repr []string `json:"repr"`
	}
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}
	return got.Str, got.Repr
}

func TestStrAndReprMatchCPythonOverTheCorpus(t *testing.T) {
	wantStr, wantRepr := pythonStrRepr(t, strCorpus)
	if len(wantStr) != len(strCorpus) || len(wantRepr) != len(strCorpus) {
		t.Fatalf("CPython returned %d/%d results for %d documents",
			len(wantStr), len(wantRepr), len(strCorpus))
	}

	for i, doc := range strCorpus {
		v, err := LoadsOrdered(doc)
		if err != nil {
			t.Errorf("LoadsOrdered(%s): %v", doc, err)
			continue
		}
		if got := Str(v); got != wantStr[i] {
			t.Errorf("str(%s)\n go %q\n py %q", doc, got, wantStr[i])
		}
		if got := Repr(v); got != wantRepr[i] {
			t.Errorf("repr(%s)\n go %q\n py %q", doc, got, wantRepr[i])
		}
	}
}

// The corpus is only worth its runtime if str and repr actually DISAGREE
// somewhere in it. If they agreed everywhere, Str could `return Repr(v)`
// unconditionally and pass.
func TestTheCorpusSeparatesStrFromRepr(t *testing.T) {
	wantStr, wantRepr := pythonStrRepr(t, strCorpus)
	differ := 0
	for i := range strCorpus {
		if wantStr[i] != wantRepr[i] {
			differ++
		}
	}
	if differ == 0 {
		t.Fatal("str and repr agree on every corpus document; the corpus " +
			"cannot see the one place they differ")
	}
	t.Logf("%d of %d documents separate str from repr", differ, len(strCorpus))
}

// And it must contain a document whose repr depends on KEY ORDER, or
// nothing stops a future Str from sorting keys — which is what a Go map
// would do, and which reads as correct in every alphabetical fixture.
func TestTheCorpusCatchesKeyReordering(t *testing.T) {
	_, wantRepr := pythonStrRepr(t, strCorpus)
	found := false
	for i, doc := range strCorpus {
		if !strings.Contains(doc, `{"b":1,"a":2}`) {
			continue
		}
		found = true
		if !strings.HasPrefix(wantRepr[i], "{'b'") {
			t.Fatalf("CPython no longer keeps insertion order: %s", wantRepr[i])
		}
		v, err := LoadsOrdered(doc)
		if err != nil {
			t.Fatal(err)
		}
		if got := Repr(v); !strings.HasPrefix(got, "{'b'") {
			t.Fatalf("Go sorted or reordered the keys: %s", got)
		}
	}
	if !found {
		t.Fatal("the out-of-order document left the corpus; key reordering " +
			"is now untested")
	}
}

// A plain Go map cannot produce Python's order, so Repr must refuse it
// rather than emit a plausible string in map-iteration order. A caller
// that sees this marker has decoded with the wrong function.
func TestAnUnorderedMapIsRefusedRatherThanGuessedAt(t *testing.T) {
	got := Repr(map[string]any{"b": 1, "a": 2})
	if !strings.Contains(got, "unordered") {
		t.Fatalf("Repr rendered a Go map as %q — it has no honest order to "+
			"render, and a plausible-looking answer is worse than a marker", got)
	}
	// The control: the same data through the honest path renders.
	v, err := LoadsOrdered(`{"b":1,"a":2}`)
	if err != nil {
		t.Fatal(err)
	}
	if Repr(v) != "{'b': 1, 'a': 2}" {
		t.Fatalf("the ordered path is broken too: %q", Repr(v))
	}
}

// KNOWN DIVERGENCE, measured and named. Go's encoding/json replaces a
// lone surrogate escape with U+FFFD; CPython's json.loads keeps it and
// json.dumps writes it back unchanged. See the residual note on
// LoadsOrdered for why this is documented rather than patched.
//
// The test asserts the CURRENT divergence in both directions, so it fails
// the moment Go starts matching — which is the signal to delete it and
// fold the case into strCorpus.
func TestALoneSurrogateDivergesFromCPython(t *testing.T) {
	const doc = `{"title":"\ud800"}`

	v, err := LoadsOrdered(doc)
	if err != nil {
		t.Fatalf("Go now REJECTS a lone surrogate (%v) — that is a third "+
			"behaviour, neither CPython's nor the old one; update this test "+
			"and the residual note on LoadsOrdered", err)
	}
	got, _ := v.(Obj).Get("title")
	s, _ := got.(string)
	if s != "�" {
		t.Fatalf("Go no longer substitutes U+FFFD (%q) — if it now preserves "+
			"the surrogate, delete this test and fold the case into strCorpus", s)
	}

	// And CPython, measured in the same run rather than asserted from memory.
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"v=json.loads(sys.argv[1])['title']\n"+
			"print(json.dumps([len(v), ord(v[0])]))", doc).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var row []any
	if err := json.Unmarshal(out, &row); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if n, _ := row[0].(float64); n != 1 {
		t.Fatalf("CPython no longer keeps the surrogate as one character: %v", row)
	}
	if cp, _ := row[1].(float64); int(cp) != 0xD800 {
		t.Fatalf("CPython no longer keeps U+D800: %v — the divergence has moved", row)
	}
}

// The store IS the contract, so the sharpest test of a writer is not
// "does it look right" but "can the other runtime read it back". This
// round-trips documents through LoadsOrdered -> DumpsIndent2 and then
// hands the RESULT to CPython's json.loads, asserting both that it parses
// and that the values survive.
//
// It exists because emitting Inf instead of Infinity passed every
// existing test in this package while making the whole document
// unreadable to Python (mission-r3 MEDIUM) — a divergence no
// Go-side-only assertion can see.
func TestWhatGoWritesCPythonCanRead(t *testing.T) {
	docs := []struct{ name, in string }{
		{"the non-finite family", `{"a": Infinity, "b": -Infinity, "c": NaN, "d": 1.0}`},
		{"infinities nested in a list", `{"scores": [Infinity, -Infinity, NaN]}`},
		{"a lone infinity", `{"a": Infinity}`},
		{"ordinary values", `{"a": 1, "b": 1.0, "c": "x", "d": null, "e": [1, 2]}`},
		{"a float that overflows to inf", `{"a": 1e309}`},
		{"key order and unicode", `{"z": "\u00e9", "a": "b", "m": {"n": 1}}`},
	}

	type row struct {
		Name string `json:"name"`
		Out  string `json:"out"`
	}
	var rows []row
	for _, d := range docs {
		v, err := LoadsOrdered(d.in)
		if err != nil {
			t.Fatalf("%s: Go could not read its own input %q: %v", d.name, d.in, err)
		}
		out, err := DumpsIndent2(v)
		if err != nil {
			t.Fatalf("%s: Go could not render: %v", d.name, err)
		}
		rows = append(rows, row{d.name, out})
	}

	in, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	// For each document: can CPython parse what Go wrote, and does
	// re-dumping it in Python reproduce the same values? Compare the
	// PARSED forms, not the text, so indentation is not under test here.
	probe, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"out=[]\n"+
			"for r in json.loads(sys.argv[1]):\n"+
			"    try:\n"+
			"        v=json.loads(r['out'])\n"+
			"        out.append([r['name'], None, json.dumps(v)])\n"+
			"    except Exception as e:\n"+
			"        out.append([r['name'], str(e), None])\n"+
			"print(json.dumps(out))", string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var got [][]*string
	if err := json.Unmarshal(probe, &got); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, probe)
	}
	for i, g := range got {
		if g[1] != nil {
			t.Errorf("CPython CANNOT read what Go wrote for %q\n  go wrote: %s\n  error:    %s",
				docs[i].name, rows[i].Out, *g[1])
			continue
		}
		// And the input itself must survive: CPython reading Go's output
		// has to agree with CPython reading the ORIGINAL.
		want, werr := exec.Command("python3", "-c",
			"import json,sys; print(json.dumps(json.loads(sys.argv[1])))", docs[i].in).Output()
		if werr != nil {
			t.Fatalf("%s: probe failed on the original: %v", docs[i].name, werr)
		}
		if got, wanted := *g[2], strings.TrimSpace(string(want)); got != wanted {
			t.Errorf("the values changed on the way through Go for %q\n"+
				"  py(go(x)) %s\n  py(x)     %s", docs[i].name, got, wanted)
		}
	}
}

// Clip's doc comment says it is Python's s[:n], and for three rounds it
// was not: every n <= 0 returned "". Python's negative index counts from
// the END — "abc"[:-1] is "ab" — and internal/orch's pySliceLen, which
// exists BECAUSE the naive version cost two r1 MEDIUMs, gets this right.
// Two implementations of one Python operation disagreeing is the defect
// (adversarial mission-r4 LOW), so this settles it against CPython
// rather than against either implementation.
func TestClipMatchesPythonSliceIncludingNegativeN(t *testing.T) {
	type probe struct {
		S string `json:"s"`
		N int    `json:"n"`
	}
	var cases []probe
	for _, s := range []string{"", "a", "abc", "abcdef", "héllo", "日本語テキスト", "a\x1cb"} {
		for n := -8; n <= 8; n++ {
			cases = append(cases, probe{s, n})
		}
	}
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"print(json.dumps([c['s'][:c['n']] for c in json.loads(sys.argv[1])]))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	var negativeAndNonEmpty int
	for i, c := range cases {
		got := Clip(c.S, c.N)
		if got != want[i] {
			t.Errorf("Clip(%q, %d) diverges\n  go %q\n  py %q", c.S, c.N, got, want[i])
		}
		if c.N < 0 && want[i] != "" {
			negativeAndNonEmpty++
		}
	}
	// The old code returned "" for every negative n, so a corpus whose
	// negative cases all happened to be empty would not separate them.
	if negativeAndNonEmpty == 0 {
		t.Fatal("no case where a NEGATIVE n yields a non-empty string: " +
			"the r4 LOW is not actually pinned")
	}
}

// SafeFloat is llm_parse.safe_float, and it replaced FOUR hand-written
// ports that each dropped a different line of it. The one that dropped
// the isnan/isinf check cost the r5 HIGH: a NaN confidence survived the
// clamp, reached runs.StampVerdict, and encoding/json refused the whole
// document — so the verdict stamp was not wrong, it was ABSENT.
//
// Driven against the real CPython function so no arm can be argued from
// a reading of it.
func TestSafeFloatMatchesCPython(t *testing.T) {
	// argv carries [value, default] pairs as JSON. A JSON document
	// cannot carry NaN/Infinity, so those three ride as sentinel
	// strings and are rebuilt on both sides.
	type sfCase struct {
		Name string  `json:"-"`
		Val  any     `json:"v"`
		Def  float64 `json:"d"`
	}
	cases := []sfCase{
		{"a plain float", 0.9, 0.7},
		{"an integer", 3, 0.7},
		{"zero", 0, 0.7},
		{"negative clamps to 0", -2.5, 0.7},
		{"above one clamps to 1", 4.2, 0.7},
		// float() is a CONVERSION, not a type check.
		{"a numeric string", "0.9", 0.5},
		{"a numeric string with padding", "  0.9  ", 0.5},
		{"a numeric string out of range", "42", 0.5},
		{"a non-numeric string", "high", 0.5},
		{"an empty string", "", 0.5},
		{"a bool true", true, 0.5},
		{"a bool false", false, 0.5},
		{"null", nil, 0.5},
		{"a list", []any{1}, 0.5},
		{"a dict", map[string]any{"a": 1}, 0.5},
		// THE r5 HIGH: non-finite must fall to the default, never
		// survive the clamp.
		{"NaN", "__NAN__", 0.7},
		{"Infinity", "__INF__", 0.7},
		{"negative Infinity", "__NEGINF__", 0.7},
		{"a string spelling of nan", "nan", 0.7},
		{"a string spelling of inf", "inf", 0.7},
		{"a string spelling of Infinity", "Infinity", 0.7},
		{"an overflowing literal", "__INF__", 0.7},
		{"an overflowing string literal", "1e309", 0.7},
	}

	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys,math\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"from llm_parse import safe_float\n"+
			"S={'__NAN__':float('nan'),'__INF__':float('inf'),"+
			"'__NEGINF__':float('-inf')}\n"+
			"r=[]\n"+
			"for c in json.loads(sys.argv[1]):\n"+
			"    v=c['v']\n"+
			"    v=S.get(v,v) if isinstance(v,str) else v\n"+
			"    r.append(safe_float(v, default=c['d'], min_val=0, max_val=1))\n"+
			"print(json.dumps(r))",
		string(in), srcDirPyval(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []float64
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var defaulted, coerced int
	for i, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			v := c.Val
			switch v {
			case "__NAN__":
				v = math.NaN()
			case "__INF__":
				v = math.Inf(1)
			case "__NEGINF__":
				v = math.Inf(-1)
			}
			got := SafeFloatUnit(v, c.Def)
			if got != want[i] {
				t.Errorf("SafeFloat(%#v, %v) diverges\n  go %v\n  py %v",
					c.Val, c.Def, got, want[i])
			}
			if got == c.Def {
				defaulted++
			} else if _, isFloat := c.Val.(float64); !isFloat {
				coerced++
			}
		})
	}
	// The two arms the four hand-written ports kept losing: a value
	// that DEFAULTS (the non-finite guard) and a non-float value that
	// COERCES (the string/bool arm).
	if defaulted == 0 {
		t.Fatal("no case falls back to the default: the non-finite guard is not pinned")
	}
	if coerced == 0 {
		t.Fatal("no non-float value coerces: safe_float's float() conversion is not pinned")
	}
}

func srcDirPyval(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}
