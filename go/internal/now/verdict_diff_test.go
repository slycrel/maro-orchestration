package now

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"testing"
)

// verdictRationale is a transcription of handle._now_verdict_rationale,
// and it has now had TWO Go-only hardenings removed from it in
// consecutive rounds — a <think> pre-strip (r2) and a string-aware,
// bail-on-unbalanced JSON scan (r3), the second sitting four lines below
// the first. Hand-written expectations are what let both stand, so this
// drives the real CPython over a corpus and compares exact strings.
//
// The output is durable: res.VerdictSummary goes into the outcome row and
// is stamped into the run dir. A fork here is a fork in the store.

func srcDirNow(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

const pyRationaleSnippet = `
import json, sys
sys.path.insert(0, sys.argv[2])
import handle
print(json.dumps([handle._now_verdict_rationale(r) for r in json.loads(sys.argv[1])]))
`

var rationaleCorpus = []struct{ name, raw string }{
	// The shape the function exists for.
	{"bare JSON then prose", `{"fulfilled": false} the file was never written`},
	{"nested JSON then prose", `{"fulfilled": false, "d": {"x": 1}} nope`},

	// THE r3 HIGH. A `}` inside a string value: Python's counter is naive
	// and closes the object EARLY, keeping the tail of the JSON as part
	// of the "rationale". A string-aware scan skips the whole object.
	{"a brace inside a string value closes the object early",
		`{"fulfilled": false, "why": "missing } brace in file"} the file was never created`},
	{"a brace inside a string, nothing after",
		`{"fulfilled": false, "why": "a } here"}`},
	{"an open brace inside a string never balances",
		`{"fulfilled": false, "why": "a { here"} tail`},
	// ...and truncation, the documented failure mode the 160-token budget
	// produces. Python's loop just ends and the whole blob survives.
	{"truncated mid-JSON", `{"fulfilled": false, "why": "no write call`},
	{"truncated mid-string with a brace", `{"fulfilled": false, "why": "no } write`},

	// The r2 fork: a think trace is NOT stripped, and because the text
	// starts with `<` the JSON skip never fires either.
	{"a think trace ahead of the JSON",
		"<think>maybe it failed?</think> " + `{"fulfilled": false} the file was never written`},

	// The fence branch.
	{"a fenced verdict then prose", "```json\n{\"fulfilled\": false}\n```\nthe file was never written"},
	{"a fence with no closing pair", "```json\n{\"fulfilled\": false}"},

	// The whitespace set: str.strip()/str.split() cover U+001C..U+001F
	// and Go's TrimSpace/Fields do not. These four cases are the pin for
	// the r3 MEDIUM on the three stdlib call sites.
	{"a separator inside the prose", "the file\x1cwas never created"},
	{"a separator after the JSON", `{"fulfilled": false}` + "\x1cok then"},
	{"a separator around the whole thing", "\x1cthe file was never created\x1f"},
	{"a separator after a fence", "```json\n{\"a\":1}\n```\x1cprose here"},
	{"runs of whitespace collapse", "the   file\n\nwas\tnever   created"},

	// Degenerate.
	{"empty", ""},
	{"whitespace only", "   \n  "},
	{"prose only", "the file was never created"},
	{"an object and nothing else", `{"fulfilled": false}`},
}

func TestVerdictRationaleMatchesCPython(t *testing.T) {
	raws := make([]string, len(rationaleCorpus))
	for i, c := range rationaleCorpus {
		raws[i] = c.raw
	}
	in, err := json.Marshal(raws)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c", pyRationaleSnippet,
		string(in), srcDirNow(t)).Output()
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
	if len(want) != len(rationaleCorpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(rationaleCorpus))
	}
	for i, c := range rationaleCorpus {
		t.Run(c.name, func(t *testing.T) {
			if got := verdictRationale(c.raw); got != want[i] {
				t.Errorf("rationale diverges\n  input %q\n     go %q\n     py %q",
					c.raw, got, want[i])
			}
		})
	}
}

// A corpus where every case returns the input unchanged would prove only
// that both runtimes can copy a string. Demand that all three branches
// fire, and that at least one case has the JSON skip land somewhere OTHER
// than the end of a well-formed object — which is the r3 HIGH's whole
// shape.
func TestTheRationaleCorpusReachesEveryBranch(t *testing.T) {
	var fenced, jsonSkip, plain, partialSkip int
	for _, c := range rationaleCorpus {
		got := verdictRationale(c.raw)
		switch {
		case len(c.raw) > 2 && c.raw[0] == '`':
			fenced++
		case len(c.raw) > 0 && c.raw[0] == '{':
			jsonSkip++
			// The naive counter closing early leaves JSON punctuation in
			// the result; a string-aware one never would.
			for _, r := range got {
				if r == '"' || r == '}' {
					partialSkip++
					break
				}
			}
		default:
			plain++
		}
	}
	if fenced == 0 || jsonSkip == 0 || plain == 0 {
		t.Fatalf("corpus misses a branch: fenced=%d json=%d plain=%d",
			fenced, jsonSkip, plain)
	}
	if partialSkip == 0 {
		t.Fatal("no case where the naive counter closes the object EARLY: " +
			"the r3 HIGH is not actually pinned")
	}
}
