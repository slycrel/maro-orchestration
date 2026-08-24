package now

import (
	"context"
	"encoding/json"
	"os/exec"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// This drives the REAL handle._verify_now_outcome — stub adapter, stub
// response, everything else production Python — and compares the
// resulting goal_verdict_summary against what verifyNow puts in
// res.VerdictSummary.
//
// It exists because three separate forks lived in that one assignment
// and every previous round's tests looked at the HELPERS underneath it
// (verdictRationale had a 19-case differential of its own from r3) while
// nothing compared the field that actually reaches the store:
//
//   - a static "judge gave no rationale" sentence Python never writes
//     (r4 HIGH — the sentence exists in the Python only as a LOG
//     fallback, and `{"fulfilled": false}` is the commonest reply there
//     is);
//   - `obj["why"].(string)`, where Python calls `str()` and a list,
//     an int or a dict is a real rationale (r4 MEDIUM);
//   - strings.TrimSpace where Python calls .strip() (r4 MEDIUM).
//
// The lesson from r3's battery restated: a differential over the helpers
// is not a differential over the pipeline.

const pyVerdictSummarySnippet = `
import json, sys
sys.path.insert(0, sys.argv[2])
import handle

class _Resp:
    def __init__(self, content):
        self.content = content
        self.input_tokens = 111
        self.output_tokens = 222

class _Adapter:
    def __init__(self, content):
        self._content = content
    def complete(self, *a, **k):
        return _Resp(self._content)

out = []
for raw in json.loads(sys.argv[1]):
    base = {"status": "done", "result": "an answer", "tokens_in": 10, "tokens_out": 20}
    res = handle._verify_now_outcome("a goal", base, _Adapter(raw))
    out.append([res.get("status"), res.get("goal_verdict_summary"),
                res.get("goal_achieved"),
                res.get("tokens_in"), res.get("tokens_out")])
print(json.dumps(out))
`

// stubAdapter returns one canned reply for the judge call. verifyNow is
// the only caller here, so a single fixed content is enough.
type verdictStub struct{ content string }

func (s verdictStub) Name() string { return "verdict-stub" }

func (s verdictStub) Complete(_ context.Context, _ []llm.Message, _ llm.Options) (*llm.Response, error) {
	return &llm.Response{Content: s.content, TokensIn: 111, TokensOut: 222}, nil
}

var verdictSummaryCorpus = []struct{ name, raw string }{
	// THE r4 HIGH. The commonest non-fulfilled reply in the lane, and
	// the one the function's own doc comment quotes from run ea4ebe4a.
	// Python stores "" here; Go stored a sentence.
	{"fulfilled false and nothing else", `{"fulfilled": false}`},
	{"an empty why", `{"fulfilled": false, "why": ""}`},
	{"a null why", `{"fulfilled": false, "why": null}`},
	{"a whitespace-only why", `{"fulfilled": false, "why": "   "}`},

	// THE r4 MEDIUM on str(). Python's str() is not a cast; a Go type
	// assertion threw all three of these away and then claimed the
	// judge had given no reason.
	{"a list why", `{"fulfilled": false, "why": ["no write call", "no file"]}`},
	{"an int why", `{"fulfilled": false, "why": 42}`},
	{"a dict why", `{"fulfilled": false, "why": {"missing": "out.txt"}}`},
	{"a float why", `{"fulfilled": false, "why": 1.5}`},
	{"a bool why", `{"fulfilled": false, "why": true}`},
	// `or ""` fires on every FALSEY why, so these must fall through to
	// the rationale recovery rather than render as "0"/"[]"/"{}".
	{"a zero why falls through", `{"fulfilled": false, "why": 0} the file was never written`},
	{"an empty list why falls through", `{"fulfilled": false, "why": []} no file`},
	{"an empty dict why falls through", `{"fulfilled": false, "why": {}} no file`},
	{"a false why falls through", `{"fulfilled": false, "why": false} no file`},

	// THE r4 MEDIUM on .strip(). The separators are written as JSON
	// ESCAPES, not raw bytes: a literal control character inside a JSON
	// string is illegal and BOTH parsers reject the document, so a
	// raw-byte case agrees trivially and pins nothing. That is how the
	// first cut of these survived the falsification battery.
	//
	// U+001C..U+001F are stripped by
	// str.strip() and not by strings.TrimSpace, and the second case
	// decides whether the recovery lane runs at all.
	{"a why wrapped in separators",
		"{\"fulfilled\": false, \"why\": \"\\u001cno write call\\u001f\"}"},
	{"a why that is only a separator",
		"{\"fulfilled\": false, \"why\": \"\\u001c\"} the file was never written"},

	// The recovery lane itself, still reachable and still pinned.
	{"rationale trailing the JSON",
		`{"fulfilled": false} the file was never written`},
	{"a fenced verdict then prose",
		"```json\n{\"fulfilled\": false}\n```\nthe file was never written"},

	// The r1 non-finite class, now reaching Object/StringArray too
	// (r4 HIGH 2): CPython parses this document and Go used to reject
	// the whole thing, losing the verdict entirely.
	{"a non-finite confidence beside the verdict",
		`{"fulfilled": false, "confidence": NaN, "why": "no file"}`},
	{"an infinite confidence beside the verdict",
		`{"fulfilled": false, "confidence": Infinity, "why": "no file"}`},

	// The other two verdict lanes, so the corpus is not all one branch.
	{"fulfilled true", `{"fulfilled": true, "why": "the file exists"}`},
	{"no verdict at all", `there is no json here`},
	{"fulfilled is a string, not a bool", `{"fulfilled": "false"}`},
}

func TestVerdictSummaryMatchesCPython(t *testing.T) {
	raws := make([]string, len(verdictSummaryCorpus))
	for i, c := range verdictSummaryCorpus {
		raws[i] = c.raw
	}
	in, err := json.Marshal(raws)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c", pyVerdictSummarySnippet,
		string(in), srcDirNow(t)).Output()
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
	if len(want) != len(verdictSummaryCorpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(verdictSummaryCorpus))
	}

	for i, c := range verdictSummaryCorpus {
		t.Run(c.name, func(t *testing.T) {
			res := &Result{Status: "done", Answer: "an answer",
				TokensIn: 10, TokensOut: 20}
			verifyNow(context.Background(), verdictStub{c.raw}, "a goal", res)

			// THE r5 HIGH. handle._verify_now_outcome adds the judge's
			// tokens ONLY inside the `fulfilled is False` branch; the
			// True branch and the no-verdict path return without any
			// token arithmetic. Billing them unconditionally put
			// different numbers in the SHARED outcome ledger, which is
			// what cost reporting and the evolver read on both runtimes.
			pyIn := int(want[i][3].(float64))
			pyOut := int(want[i][4].(float64))
			if res.TokensIn != pyIn || res.TokensOut != pyOut {
				t.Errorf("judge token billing diverges\n  reply %q\n"+
					"     go %d/%d\n     py %d/%d",
					c.raw, res.TokensIn, res.TokensOut, pyIn, pyOut)
			}

			pyStatus, _ := want[i][0].(string)
			pySummary, _ := want[i][1].(string) // absent -> "", same as Go's zero
			if got := res.VerdictSummary; got != pySummary {
				t.Errorf("goal_verdict_summary diverges\n  reply %q\n     go %q\n     py %q",
					c.raw, got, pySummary)
			}
			if res.Status != pyStatus {
				t.Errorf("status diverges\n  reply %q\n     go %q\n     py %q",
					c.raw, res.Status, pyStatus)
			}
			// goal_achieved is a tri-state on both sides: absent means
			// "not judged", which is a distinct answer from false.
			var goAchieved any
			if res.GoalAchieved != nil {
				goAchieved = *res.GoalAchieved
			}
			if goAchieved != want[i][2] {
				t.Errorf("goal_achieved diverges\n  reply %q\n     go %v\n     py %v",
					c.raw, goAchieved, want[i][2])
			}
		})
	}
}

// A corpus that never left the `why` lane would prove nothing about the
// two lanes underneath it, and one where every summary came back
// non-empty could not have caught the r4 HIGH at all.
func TestTheVerdictSummaryCorpusReachesEveryLane(t *testing.T) {
	var whyLane, recoveryLane, emptyLane, nonString int
	for _, c := range verdictSummaryCorpus {
		res := &Result{Status: "done", Answer: "an answer"}
		verifyNow(context.Background(), verdictStub{c.raw}, "a goal", res)
		if res.Status != "incomplete" {
			continue
		}
		switch {
		case res.VerdictSummary == "":
			emptyLane++
		case strings.Contains(c.raw, `"why"`):
			whyLane++
			// A why that is not a JSON string, rendered through str().
			if !strings.Contains(c.raw, `"why": "`) {
				nonString++
			}
		default:
			recoveryLane++
		}
	}
	if whyLane == 0 || recoveryLane == 0 {
		t.Fatalf("corpus misses a lane: why=%d recovery=%d", whyLane, recoveryLane)
	}
	if emptyLane == 0 {
		t.Fatal("no case where BOTH lanes come up empty: the r4 HIGH " +
			"(a placeholder sentence CPython never writes) is not pinned")
	}
	if nonString == 0 {
		t.Fatal("no case with a non-string why: the r4 MEDIUM (str() is " +
			"not a cast) is not pinned")
	}
}

// A NAMED DIVERGENCE, not a bug: Go scrubs goal_verdict_summary and
// CPython does not. See verifyNow's doc comment for why converging by
// deleting the scrub is the wrong direction, and why the fix is owed to
// handle._verify_now_outcome instead.
//
// This test asserts the divergence EXISTS. When the Python fix lands it
// will fail, which is the point — the seam gets closed deliberately
// rather than rediscovered.
func TestTheScrubDivergesFromCPythonOnPurpose(t *testing.T) {
	const secret = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
	raw := `{"fulfilled": false, "why": "the call sent Authorization: Bearer ` +
		secret + ` and 401'd"}`

	res := &Result{Status: "done", Answer: "an answer"}
	verifyNow(context.Background(), verdictStub{raw}, "a goal", res)
	if strings.Contains(res.VerdictSummary, secret) {
		t.Fatalf("Go leaked the token into a durable field: %q", res.VerdictSummary)
	}

	in, err := json.Marshal([]string{raw})
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c", pyVerdictSummarySnippet,
		string(in), srcDirNow(t)).Output()
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
	pySummary, _ := want[0][1].(string)
	if !strings.Contains(pySummary, secret) {
		t.Fatalf("CPython no longer writes the token to goal_verdict_summary — "+
			"the Python-side fix has landed, so REMOVE this test and the "+
			"named divergence in verifyNow's doc comment. Got %q", pySummary)
	}
}
