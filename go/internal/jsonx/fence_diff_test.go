package jsonx

import (
	"encoding/json"
	"os/exec"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The fence strip is the OTHER transcription in this package, and it was
// the second fork found by the "a hardening is a divergence" rule
// (adversarial mission-r1). Go used to hunt for a ```json block anywhere
// in the reply and carve inside it; llm_parse.strip_markdown_fences only
// unwraps a fence that is the ENTIRE stripped message, and otherwise
// leaves the text completely alone.
//
// Hand-written expectations are what let that survive, so this file does
// what carve_diff_test.go does: drive CPython and Go over one corpus and
// compare EXACT STRINGS. The strip output is compared rather than the
// parsed value on purpose — a regex port can be wrong in ways the JSON
// parser then papers over, and three of the cases below (the digit in the
// language tag, the two-fence document, the think-block ordering) are
// exactly that shape.

const pyFenceSnippet = `
import json, sys, llm_parse

def span(pre, open_c, close_c):
    start, end = llm_parse._find_json_bounds(pre, open_c, close_c)
    return pre[start:end] if start >= 0 else None

out = []
for text in json.loads(sys.argv[1]):
    # extract_json's preamble, in its order.
    pre = llm_parse.strip_markdown_fences(llm_parse.strip_think_blocks(text))
    out.append([
        llm_parse.strip_markdown_fences(text),
        pre,
        span(pre, '[', ']'),
        span(pre, '{', '}'),
    ])
print(json.dumps(out))
`

// fenceCorpus is a package var so the anti-vacuity guard below reads the
// SAME list the differential does.
var fenceCorpus = []struct {
	name string
	text string
}{
	// The plain shapes the strip exists for.
	{"a whole-message fence", "```json\n[\"a\", \"b\"]\n```"},
	{"a fence with no language tag", "```\n[\"a\", \"b\"]\n```"},
	{"a fence with no trailing newline", "```json\n[\"a\", \"b\"]```"},
	{"surrounding whitespace is stripped first", "\n\n  ```json\n[\"a\", \"b\"]\n```   \n"},
	{"a fence around an object", "```json\n{\"real\":1}\n```"},
	{"an empty fence body", "```json\n\n```"},
	{"no fence at all", "[\"a\", \"b\"]"},
	{"prose only", "I could not produce JSON, sorry."},

	// THE FORK. Prose on either side means `.match` fails against the
	// whole text and NOTHING is stripped, so the carve then runs over the
	// prose too and a stray bracket in it wins. Go used to read the fence.
	{"prose before a fence blocks the strip entirely",
		"See the docs [here](url) for context.\n```json\n[\"step one\", \"step two\"]\n```"},
	{"prose after a fence blocks it too",
		"```json\n[\"a\", \"b\"]\n```\nLet me know if that helps."},
	{"prose both sides, with a decoy object",
		"Prose with {a} inline.\n```json\n{\"real\":1}\n```"},
	// ...and the case that made the old Go behaviour look correct: prose
	// either side but no bracket in it, so both runtimes carve the same
	// span by different routes. This is the one that hid the fork.
	{"prose either side but no bracket in it",
		"Sure! Here are the steps:\n```json\n[\"first step\", \"second step\"]\n```\nLet me know."},

	// The regex's exact shape. `[a-zA-Z]*` does not eat the digit, and
	// `\n?` then cannot match it either, so the 5 lands INSIDE the payload.
	{"a digit in the language tag leaks into the body", "```json5\n[\"a\", \"b\"]\n```"},
	{"a hyphen in the language tag", "```json-ld\n[\"a\", \"b\"]\n```"},

	// `(.*?)` is lazy but `$` is anchored at end-of-text, so a document
	// with TWO fences captures everything between the OUTERMOST pair --
	// including the inner backticks. A per-block scan returns ["a"].
	{"two fences capture across both", "```json\n[\"a\"]\n```\n```json\n[\"b\"]\n```"},
	{"backticks inside the payload", "```json\n[\"a ``` b\"]\n```"},

	// Ordering. strip_markdown_fences alone leaves this untouched (the
	// <think> is before the fence), but extract_json strips think FIRST,
	// after which the fence IS the whole message. The two columns this
	// corpus compares are what pin that order.
	{"a think block ahead of a fence", "<think>[bad]</think>\n```json\n[\"a\", \"b\"]\n```"},
	{"a think block ahead of bare json", "<think>{\"bad\":1}</think>\n{\"real\":1}"},
	// The `\s` fix. A non-breaking space inside the CLOSING tag: Python's
	// \s covers it (29 code points), RE2's does not (5), and the old
	// pattern therefore fell through to thinkOpenRe and truncated the
	// whole reply. One of the four separators U+001C..U+001F too, since
	// those also split the two classes.
	{"a non-breaking space in the closing tag",
		"<think>musing {\"decoy\":1}</think\u00a0>\n{\"real\":2}"},
	{"a unit separator in the closing tag",
		"<think>musing {\"decoy\":1}</think\u001f>\n{\"real\":2}"},
	{"an ideographic space in the closing tag",
		"<think>musing {\"decoy\":1}</think\u3000>\n{\"real\":2}"},
	{"an ordinary space in the closing tag still works",
		"<think>musing {\"decoy\":1}</think >\n{\"real\":2}"},
	{"an unclosed think really does truncate",
		"<think>musing {\"decoy\":1}\n{\"real\":2}"},

	// Whitespace, on BOTH sides of the unwrap. Each of these was added
	// because a mutant survived without it: a body left unstripped, a
	// non-match returning the raw text instead of the stripped text, and
	// Go's strings.TrimSpace standing in for Python's str.strip().
	{"a fence body with padding", "```json\n   [\"a\"]   \n```"},
	{"a fence body padded with newlines", "```json\n\n\n[\"a\"]\n\n\n```"},
	{"a padded document with no fence", "   [\"a\"]   "},
	// str.strip() removes U+001C..U+001F; Go's unicode.IsSpace does NOT,
	// so strings.TrimSpace leaves them and pytext.Strip removes them.
	// Inside a fence body and outside one, because the two strip calls
	// are separate lines and a mutant can hit either.
	{"separators around a fence body", "```json\n\x1c[\"a\"]\x1f\n```"},
	{"separators around a bare document", "\x1c[\"a\"]\x1f"},
	{"separators around the whole fence", "\x1c```json\n[\"a\"]\n```\x1f"},

	// Degenerate fences: too few backticks, unterminated, bare.
	{"only two backticks is not a fence", "``json\n[\"a\"]\n``"},
	{"an unterminated fence", "```json\n[\"a\", \"b\"]"},
	{"a bare fence marker", "```"},
	{"two bare fence markers", "```\n```"},
}

func pyFences(t *testing.T, texts []string) [][]*string {
	t.Helper()
	b, err := json.Marshal(texts)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyFenceSnippet, string(b))
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t))
	out, err := cmd.Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("python3 is present but the fence probe could not run: %v", err)
	}
	var got [][]*string
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	return got
}

func TestStripMarkdownFencesMatchesCPython(t *testing.T) {
	texts := make([]string, len(fenceCorpus))
	for i, c := range fenceCorpus {
		texts[i] = c.text
	}
	want := pyFences(t, texts)
	if len(want) != len(fenceCorpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(fenceCorpus))
	}
	for i, c := range fenceCorpus {
		t.Run(c.name, func(t *testing.T) {
			row := want[i]
			eq := func(what string, got string, ok bool, w *string) {
				t.Helper()
				switch {
				case !ok && w != nil:
					t.Errorf("%s: go found nothing, py found %q\n input %q", what, *w, c.text)
				case ok && w == nil:
					t.Errorf("%s: go found %q, py found nothing\n input %q", what, got, c.text)
				case ok && *w != got:
					t.Errorf("%s diverges\n input %q\n    go %q\n    py %q",
						what, c.text, got, *w)
				}
			}
			eq("strip_markdown_fences", stripMarkdownFences(c.text), true, row[0])
			// The composed preamble, in extract_json's order: think
			// blocks, then fences. This column is the whole reason the
			// ordering cases are in the corpus.
			eq("strip(think(x))", stripMarkdownFences(stripThinkBlocks(c.text)), true, row[1])

			// And the composition ITSELF. Pinning the two functions
			// separately does NOT pin extract: a mutant that swapped the
			// two verbs inside extract survived the first version of this
			// file, because nothing here called extract. The pieces being
			// right is not the same claim as the pipeline being right.
			arr, aerr := extract(c.text, '[', ']')
			eq("extract(list)", arr, aerr == nil, row[2])
			obj, oerr := extract(c.text, '{', '}')
			eq("extract(dict)", obj, oerr == nil, row[3])
		})
	}
}

// A corpus that never reaches the strip proves only that both runtimes can
// leave a string alone. Demand that it reaches both lanes, and that at
// least one case is one where think-stripping CHANGES what the fence strip
// then does -- that pair is what pins the ordering.
func TestTheFenceCorpusReachesBothLanes(t *testing.T) {
	var stripped, untouched, orderSensitive int
	for _, c := range fenceCorpus {
		plain := stripMarkdownFences(c.text)
		if plain == pytext.Strip(c.text) {
			untouched++
		} else {
			stripped++
		}
		if plain != stripMarkdownFences(stripThinkBlocks(c.text)) {
			orderSensitive++
		}
	}
	if stripped == 0 || untouched == 0 {
		t.Fatalf("corpus is one-sided: %d stripped, %d untouched", stripped, untouched)
	}
	if orderSensitive == 0 {
		t.Fatal("no case where think-stripping changes the fence strip: " +
			"the think-then-fence ordering is not actually pinned")
	}
}

// KNOWN DIVERGENCE, measured and named rather than silently carried. `\b`
// is ASCII-only in RE2 and Unicode-aware in Python's re, so a letter
// outside ASCII immediately after the tag name splits the two: CPython
// finds no word boundary, matches NEITHER think pattern, leaves the trace
// in place and carves the hypothetical; Go finds a boundary and strips.
//
// Not patched, deliberately. Expressing Python's `\w` needs a word class,
// and Go ships Unicode 15.0 against CPython's 16.0 here — the same skew
// pytext.digitSupplementBody exists for. A class that is NEARLY Python's
// would read as fixed while still forking, which is the failure mode this
// port keeps finding. See the residual note in jsonx.go.
//
// The test asserts the CURRENT divergence in both directions. It fails if
// Go starts matching CPython, which is the signal to delete it and fold
// the case into fenceCorpus.
func TestANonASCIILetterAfterTheTagNameDivergesFromCPython(t *testing.T) {
	const doc = "<think\u00e9>musing {\"decoy\":1}</think>\n{\"real\":2}"

	want := pyFences(t, []string{doc})
	if want[0][3] == nil {
		t.Fatal("CPython found no object at all; the divergence has moved")
	}
	py := *want[0][3]
	if py != `{"decoy":1}` {
		t.Fatalf("CPython no longer carves the hypothetical (%q) — if it now "+
			"strips the trace, delete this test and fold the case into fenceCorpus", py)
	}

	got, err := extract(doc, '{', '}')
	if err != nil {
		t.Fatalf("Go failed to carve anything: %v", err)
	}
	if got != `{"real":2}` {
		t.Fatalf("Go no longer strips the trace (%q) — if it now matches CPython, "+
			"delete this test and fold the case into fenceCorpus", got)
	}
}

// The MIRROR of the test above, and the direction that actually costs
// something. The one above puts the non-ASCII letter AFTER the tag name
// (Go strips a trace Python keeps — Go loses a hypothetical, which is
// harmless). This one reaches a non-ASCII character INSIDE the name
// through case folding: both engines fold `k` to U+212A KELVIN SIGN
// under (?i), and only Python's Unicode-aware `\b` treats U+212A as a
// word character.
//
// Here GO carves the model's hypothetical and CPython carves the real
// answer — the destructive shape the `\s` fix (r2 MEDIUM) was about,
// and the direction the residual note omitted for two rounds
// (adversarial mission-r4 LOW). A residual stated in one direction reads
// as fully understood.
func TestAFoldedNonASCIILetterInsideTheTagNameDivergesDestructively(t *testing.T) {
	const doc = "<thin\u212a>musing {\"decoy\":1}</think>\n{\"real\":2}"

	want := pyFences(t, []string{doc})
	if want[0][3] == nil {
		t.Fatal("CPython found no object at all; the divergence has moved")
	}
	py := *want[0][3]
	if py != `{"real":2}` {
		t.Fatalf("CPython no longer strips the trace (%q) — if it now keeps it, "+
			"delete this test and fold the case into fenceCorpus", py)
	}

	got, err := extract(doc, '{', '}')
	if err != nil {
		t.Fatalf("Go failed to carve anything: %v", err)
	}
	if got != `{"decoy":1}` {
		t.Fatalf("Go no longer carves the hypothetical (%q) — if a measured "+
			"Word class has landed and the two now agree, delete this test, "+
			"fold the case into fenceCorpus, and strike the residual note in "+
			"jsonx.go", got)
	}
}

// jsonx.Object and jsonx.StringArray decode the carved span, and for
// three rounds they did it with encoding/json while ObjectOrdered used
// pyval.LoadsOrdered. Only the latter masks the bare NaN/Infinity
// tokens CPython's json.loads accepts by default, so the two siblings
// REJECTED whole documents CPython parses — and the rejection kills the
// document, not the one field (adversarial mission-r4 HIGH).
//
// The existing fence differential could not see this: it compares the
// STRIP output, deliberately, so the decode step was never handed back
// to CPython at all. This compares the decoded value.
const pyDecodeSnippet = `
import json, sys
sys.path.insert(0, sys.argv[2])
from llm_parse import extract_json

out = []
for text, kind in json.loads(sys.argv[1]):
    v = extract_json(text, dict if kind == 'dict' else list)
    # repr() is the comparable rendering: it spells nan/inf the way
    # pyval.Repr does, and json.dumps cannot carry them at all.
    #
    # NOTHING is normalised here any more. This snippet used to coerce
    # int -> float recursively, because pyval.Plain rendered every JSON
    # number as a float64 the way json.Unmarshal-into-any does. That was
    # a real divergence wearing a normalisation: str(42) is "42" and
    # str(42.0) is "42.0", and both reach persisted rows through any
    # caller that renders a decoded value as text -- which a check
    # description does (adversarial mission-r6). Plain now returns an int
    # for an integral literal, exactly as json.loads does, so the
    # comparison is exact and the coercion is gone.
    ordered = dict(sorted(v.items())) if kind == 'dict' else v
    out.append([sorted(v.keys()) if kind == 'dict' else None,
                repr(ordered), bool(v)])
print(json.dumps(out))
`

var decodeCorpus = []struct{ name, text, kind string }{
	// THE r4 HIGH. Three production prompts ask the model for a float.
	{"a NaN confidence", `{"lane": "now", "confidence": NaN}`, "dict"},
	{"an Infinity confidence", `{"lane": "now", "confidence": Infinity}`, "dict"},
	{"a -Infinity confidence", `{"lane": "now", "confidence": -Infinity}`, "dict"},
	{"a non-finite in a key nobody reads", `{"passed": true, "junk": NaN}`, "dict"},
	{"a non-finite inside a fence",
		"```json\n{\"passed\": false, \"score\": NaN}\n```", "dict"},
	{"a non-finite behind a think trace",
		"<think>hmm {\"decoy\": 1}</think>\n{\"passed\": false, \"score\": Infinity}", "dict"},

	// The ordinary lane, so the corpus is not all one shape.
	{"a plain object", `{"passed": true, "why": "ok"}`, "dict"},
	{"an object with a real float", `{"passed": true, "confidence": 0.5}`, "dict"},
	{"an object with an int", `{"passed": true, "n": 3}`, "dict"},
	{"a nested object", `{"a": {"b": [1, 2]}}`, "dict"},
	{"no object at all", `there is nothing here`, "dict"},
}

func TestObjectDecodesWhatCPythonDecodes(t *testing.T) {
	pairs := make([][]string, len(decodeCorpus))
	for i, c := range decodeCorpus {
		pairs[i] = []string{c.text, c.kind}
	}
	in, err := json.Marshal(pairs)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c", pyDecodeSnippet,
		string(in), srcDir(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want [][]any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var nonFiniteParsed int
	for i, c := range decodeCorpus {
		t.Run(c.name, func(t *testing.T) {
			obj, gerr := Object(c.text)
			pyNonEmpty, _ := want[i][2].(bool)

			// extract_json returns the EMPTY container on failure, so
			// "CPython got something" is `bool(v)`.
			if pyNonEmpty && gerr != nil {
				t.Fatalf("CPython decoded %s but Go refused it: %v\n  input %q",
					want[i][1], gerr, c.text)
			}
			if !pyNonEmpty {
				if gerr == nil && len(obj) > 0 {
					t.Fatalf("Go decoded %v where CPython got the empty default\n  input %q",
						obj, c.text)
				}
				return
			}
			if strings.Contains(c.text, "NaN") || strings.Contains(c.text, "Infinity") {
				nonFiniteParsed++
			}

			var pyKeys []string
			raw, err := json.Marshal(want[i][0])
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, &pyKeys); err != nil {
				t.Fatalf("decoding CPython key list: %v", err)
			}
			gotKeys := make([]string, 0, len(obj))
			for k := range obj {
				gotKeys = append(gotKeys, k)
			}
			sort.Strings(gotKeys)
			if !reflect.DeepEqual(gotKeys, pyKeys) {
				t.Errorf("key sets differ\n  input %q\n     go %v\n     py %v",
					c.text, gotKeys, pyKeys)
			}

			// COMPARE THE VALUES, not just the key set. Comparing keys
			// alone is exactly why the r5 HIGH survived this test:
			// {"confidence": NaN} and {"confidence": 0.7} have identical
			// keys, so the whole non-finite class — the very thing this
			// test was written for — was invisible to it.
			//
			// pyval.Repr renders a Go value the way CPython's repr()
			// renders the Python one, including nan/inf, which is what
			// makes this comparable at all; json.dumps cannot carry them.
			pyRepr, _ := want[i][1].(string)
			if got := pyval.Repr(sortedObj(obj)); got != pyRepr {
				t.Errorf("decoded VALUES differ\n  input %q\n     go %s\n     py %s",
					c.text, got, pyRepr)
			}
		})
	}

	// The whole point of the finding: a corpus with no non-finite
	// document that CPython PARSES cannot separate the two decoders.
	if nonFiniteParsed == 0 {
		t.Fatal("no case where CPython parsed a bare non-finite token: " +
			"the r4 HIGH is not actually pinned")
	}
}

// StringArray took the identical fix, and it is an EQUIVALENT MUTANT
// there — proven, not assumed. Reverting StringArray alone to a decoder
// that rejects bare non-finite tokens survives the whole suite, and no
// document can separate the two under this function's contract:
//
// A bare NaN/Infinity inside the carved [...] span is a NUMBER token. It
// is therefore either an element (so the array is not all-strings) or
// nested inside a sub-object or sub-array (so that element is not a
// string). Either way StringArray returns an error. Measured, CPython
// keeps the value and hands back a mixed list:
//
//	["a", NaN]          -> ['a', nan]
//	["a", {"c": NaN}]   -> ['a', {'c': nan}]
//
// which this port refuses on purpose (a planner step that is not text is
// nothing it can execute). Only the error TEXT moves — "array found but
// unparseable" becomes "array contains non-string elements" — and every
// caller treats both the same, so no byte reaches the store differently.
//
// The change is kept anyway so the package has ONE decoder rather than
// two: the r4 HIGH exists precisely because a fix landed in one of three
// siblings and the split survived three review rounds.
//
// A non-finite in a SIBLING key is not a counter-example either — it
// falls outside the carved span, so neither decoder ever sees it.
func TestStringArrayNonFiniteIsUnreachableByConstruction(t *testing.T) {
	if _, err := StringArray(`["step one", "step two"]`); err != nil {
		t.Fatalf("the ordinary case broke: %v", err)
	}

	// Both spellings of a reachable non-finite, both refused, and the
	// refusal is the SAME one a mixed-type array gets.
	for _, doc := range []string{
		`["a", NaN]`,
		`["a", {"c": NaN}]`,
		`["a", Infinity]`,
	} {
		if _, err := StringArray(doc); err == nil {
			t.Fatalf("%s decoded as a string array; if the contract has "+
				"widened, this equivalence argument no longer holds", doc)
		}
	}

	// The sibling-key case: the span carved is ["a", "b"], so the NaN is
	// never decoded by either implementation.
	arr, err := StringArray(`{"score": NaN, "steps": ["a", "b"]}`)
	if err != nil {
		t.Fatalf("a non-finite OUTSIDE the carved span must not matter: %v", err)
	}
	if len(arr) != 2 || arr[0] != "a" || arr[1] != "b" {
		t.Fatalf("array decoded wrong: %v", arr)
	}
}

// sortedObj renders a Go map through pyval.Obj in sorted key order.
// CPython's repr of a dict follows insertion order and Go's map has
// none, so the two are only comparable once both are sorted — the probe
// sorts on its side too.
func sortedObj(m map[string]any) pyval.Obj {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	o := pyval.Obj{}
	for _, k := range keys {
		v := m[k]
		if nested, ok := v.(map[string]any); ok {
			v = sortedObj(nested)
		}
		o.Set(k, v)
	}
	return o
}

// The one difference TestObjectDecodesWhatCPythonDecodes normalises away,
// pinned on its own so the normalisation cannot quietly grow.
//
// Object now yields a real int for an integral literal, matching
// CPython json.loads. It did not always: pyval.Plain used to flatten
// every number to float64, mimicking encoding/json-into-any, and that
// was carried for two rounds as a named loss. It reached disk the first
// time a decoded number was rendered as TEXT — str(42) vs str(42.0) —
// which is what a non-string check description does (mission-r6).
func TestObjectPreservesIntNessLikeJSONLoads(t *testing.T) {
	obj, err := Object(`{"n": 3, "f": 3.0, "big": 1e400}`)
	if err != nil {
		t.Fatal(err)
	}
	if got := pyval.Repr(obj["n"]); got != "3" {
		t.Errorf("an integral literal must decode as an int, like "+
			"json.loads: got %s (%T)", got, obj["n"])
	}
	if got := pyval.Repr(obj["f"]); got != "3.0" {
		t.Errorf("a decimal literal must stay a float: got %s", got)
	}
	// The residual, stated rather than hidden: CPython's int is
	// arbitrary-precision and Go's is not, so an integer past int64
	// still falls through to float64.
	if got := pyval.Repr(obj["big"]); got != "inf" {
		t.Errorf("1e400 should overflow to inf: got %s", got)
	}

	// ...and the ordered decoder, which is the answer for callers that
	// need the source literal itself, still keeps it.
	o, err := ObjectOrdered(`{"n": 3, "f": 3.0}`)
	if err != nil {
		t.Fatal(err)
	}
	n, _ := o.Get("n")
	f, _ := o.Get("f")
	if pyval.Repr(n) != "3" || pyval.Repr(f) != "3.0" {
		t.Fatalf("ObjectOrdered stopped preserving the source literal: "+
			"n=%s f=%s", pyval.Repr(n), pyval.Repr(f))
	}
}
