package pyval

import (
	"encoding/json"
	"os/exec"
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
