package jsonx

import (
	"encoding/json"
	"os/exec"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
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
out = []
for text in json.loads(sys.argv[1]):
    out.append([
        llm_parse.strip_markdown_fences(text),
        llm_parse.strip_markdown_fences(llm_parse.strip_think_blocks(text)),
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

	// Degenerate fences: too few backticks, unterminated, bare.
	{"only two backticks is not a fence", "``json\n[\"a\"]\n``"},
	{"an unterminated fence", "```json\n[\"a\", \"b\"]"},
	{"a bare fence marker", "```"},
	{"two bare fence markers", "```\n```"},
}

func pyFences(t *testing.T, texts []string) [][]string {
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
	var got [][]string
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
			if got := stripMarkdownFences(c.text); got != want[i][0] {
				t.Errorf("strip_markdown_fences diverges\n input %q\n    go %q\n    py %q",
					c.text, got, want[i][0])
			}
			// The composed preamble, in extract_json's order: think
			// blocks, then fences. Column 1 is the whole reason the
			// ordering cases above are in the corpus.
			if got := stripMarkdownFences(stripThinkBlocks(c.text)); got != want[i][1] {
				t.Errorf("strip(think(x)) diverges\n input %q\n    go %q\n    py %q",
					c.text, got, want[i][1])
			}
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
