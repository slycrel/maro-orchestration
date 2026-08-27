package intent

import (
	"context"
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// heuristicClassify decides the EXECUTION LANE when the LLM classify
// call fails or returns unparseable JSON, and it used the stdlib's
// ToLower/TrimSpace/Fields where Python uses str.lower/.strip/.split.
// All three differ, and the difference is not a field but a lane: one
// runtime writes a task_type:"now" outcome row, the other writes an
// agenda run dir and a mission (adversarial mission-r5 MEDIUM).
//
// Driven against the real _heuristic_classify, so no arm is argued from
// a reading of the regexes.

func srcDirIntent(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

var heuristicCorpus = []struct{ name, msg string }{
	// THE r5 MEDIUM. U+0130 lowers to TWO code points in Python
	// ("i" + U+0307) and to one in Go, so the ASCII-keyed agenda regex
	// matches on one runtime and misses on the other.
	{"a dotted capital I inside an agenda keyword", "BUİLD a new dashboard"},
	{"a dotted capital I inside a now keyword", "WHAT İS the time"},
	{"a dotted capital I elsewhere", "build a new dashboard for İstanbul"},

	// str.strip() covers U+001C..U+001F; strings.TrimSpace does not.
	// A leading separator changes neither the regexes nor the word
	// count here, but the SAME text through .split() can.
	{"a leading separator", "\x1cbuild a new dashboard"},
	{"separators as the only word break", "build\x1ca\x1cnew\x1cdashboard"},
	{"a trailing separator", "what is the time\x1f"},
	{"separators around the whole thing", "\x1cwhat is the time\x1f"},

	// The word-count threshold, which the split set feeds directly.
	{"exactly at the short threshold",
		"one two three four five six seven eight"},
	{"one past the short threshold",
		"one two three four five six seven eight nine"},
	{"at the threshold, separator-broken",
		"one\x1ctwo\x1cthree\x1cfour\x1cfive\x1csix\x1cseven\x1ceight"},

	// The NOW question-mark lane. r5's corpus contained no "?" at all,
	// so nowPatterns[0] — the one r7 rewrote — was never exercised by
	// this differential: a corpus that cannot reach a pattern cannot
	// separate on it (adversarial mission-r7 MEDIUM).
	{"a bare interrogative", "what?"},
	{"an interrogative with no window", "who?"},
	{"one character of window", "what x?"},
	{"a window at the cap", "what " + strings.Repeat("x", 59) + "?"},
	{"a window one past the cap", "what " + strings.Repeat("x", 60) + "?"},
	{"a newline inside the window", "what is\nthe time?"},
	{"a non-ASCII letter right after the keyword", "what研究 is the time?"},

	// The live-data lane, whose ONLY `i` is the ` is` alternative.
	// re.IGNORECASE folds U+0131 onto `i` and Go's (?i) does not, so
	// before pytext.PyFoldI this row scored no agenda point and fell to
	// the short-message NOW bonus: CPython agenda 0.65, Go now 0.65.
	{"the dotless i in the live-data pattern", "what ıs the current price"},
	{"the same with a keyword carrying no i", "what ıs the latest news"},
	{"the ASCII control", "what is the current price"},

	// U+0130 in the same slot is NOT a divergence here, and the reason is
	// worth pinning: heuristicClassify lowercases FIRST, and both
	// str.lower() and pytext.Lower expand U+0130 to "i" + U+0307. The
	// combining dot then sits between the `i` and the `s`, so the ` is`
	// alternative misses on BOTH engines and the message routes NOW.
	// Fold coverage and the lowercase pass interact; a fixture written
	// from the pattern alone would have asserted agenda here and been
	// wrong for a reason no reading of liveDataRe could show.
	{"a dotted capital I, eaten by the lowercase pass",
		"WHAT İS THE CURRENT PRICE"},

	// The ordinary ASCII lanes, so the corpus is not all edge cases.
	{"a plain now question", "what is the time"},
	{"a plain agenda task", "build a dashboard and deploy it to production"},
	{"a research task", "research the best approach and write a report"},
	{"empty", ""},
	{"whitespace only", "   "},
	{"a single word", "hello"},
	{"an ideographic space break", "build　a　dashboard"},
	{"a non-breaking space break", "what is the time"},
}

func TestHeuristicClassifyMatchesCPython(t *testing.T) {
	msgs := make([]string, len(heuristicCorpus))
	for i, c := range heuristicCorpus {
		msgs[i] = c.msg
	}
	in, err := json.Marshal(msgs)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import intent\n"+
			"r=[]\n"+
			"for m in json.loads(sys.argv[1]):\n"+
			"    lane, conf, reason = intent._heuristic_classify(m)\n"+
			"    r.append([str(lane), conf, reason])\n"+
			"print(json.dumps(r))",
		string(in), srcDirIntent(t)).Output()
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

	var nowLane, agendaLane int
	for i, c := range heuristicCorpus {
		t.Run(c.name, func(t *testing.T) {
			lane, conf, reason := heuristicClassify(c.msg, nil)
			pyLane, _ := want[i][0].(string)
			pyConf, _ := want[i][1].(float64)
			pyReason, _ := want[i][2].(string)

			if lane != pyLane {
				t.Errorf("LANE diverges — this is a different execution "+
					"path, not a field\n  in %q\n  go %s\n  py %s",
					c.msg, lane, pyLane)
			}
			if conf != pyConf {
				t.Errorf("confidence diverges for %q\n  go %v\n  py %v",
					c.msg, conf, pyConf)
			}
			if reason != pyReason {
				t.Errorf("reason diverges for %q\n  go %q\n  py %q",
					c.msg, reason, pyReason)
			}
		})
		switch want[i][0] {
		case "now":
			nowLane++
		case "agenda":
			agendaLane++
		}
	}
	// A corpus that only ever produced one lane could not have caught
	// the finding, which is precisely a lane flip.
	if nowLane == 0 || agendaLane == 0 {
		t.Fatalf("corpus reaches only one lane: now=%d agenda=%d",
			nowLane, agendaLane)
	}
}

// The lane field and the two boolean overrides read the model's own
// answer. Python is safe_str(...).lower() and raw.strip().lower(), both
// of which strip the 29-point set; the stdlib equivalents do not, so
// `{"lane": "now\u001c"}` matched neither arm and fell back to the
// WRONG lane from a well-formed verdict (adversarial mission-r5 LOW).
//
// Driven through the real llmClassify with a stub adapter, so the parse
// this pins is the production one.
type classifyStub struct{ reply string }

func (c classifyStub) Name() string { return "classify-stub" }

func (c classifyStub) Complete(_ context.Context, _ []llm.Message, _ llm.Options) (*llm.Response, error) {
	return &llm.Response{Content: c.reply}, nil
}

func TestClassifyFieldsStripLikeCPython(t *testing.T) {
	// The JSON ESCAPE, not a raw byte. A literal control character
	// inside a JSON string is illegal and BOTH parsers reject the
	// document, so a raw-byte fixture agrees trivially and pins nothing
	// — the exact trap the mission-r4 battery caught, written up in
	// PORT.md as "a fixture both sides refuse is not a differential".
	// It bit again here, three files later.
	const sep = `\u001c`
	cases := []struct {
		name       string
		reply      string
		wantLane   string
		wantLive   bool
		wantIntros bool
	}{
		{"a clean now verdict",
			`{"lane":"now","confidence":0.9,"reason":"r"}`, "now", false, false},
		{"a lane with a trailing separator",
			`{"lane":"now` + sep + `","confidence":0.9,"reason":"r"}`, "now", false, false},
		{"a lane with leading whitespace",
			`{"lane":"  now  ","confidence":0.9,"reason":"r"}`, "now", false, false},
		{"a lane in caps",
			`{"lane":"NOW","confidence":0.9,"reason":"r"}`, "now", false, false},
		{"needs_live_data as a separator-padded string",
			`{"lane":"now","confidence":0.9,"reason":"r","needs_live_data":"true` + sep + `"}`,
			"now", true, false},
		{"introspects_self as a separator-padded string",
			`{"lane":"now","confidence":0.9,"reason":"r","introspects_self":"` + sep + `true"}`,
			"now", false, true},
		{"a genuinely false string stays false",
			`{"lane":"now","confidence":0.9,"reason":"r","needs_live_data":"false"}`,
			"now", false, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			res, ok := llmClassify(context.Background(), classifyStub{c.reply}, "a goal")
			if !ok {
				t.Fatalf("the reply did not classify: %+v", res)
			}
			if res.Lane != c.wantLane {
				t.Errorf("lane = %q, want %q — Python's safe_str strips "+
					"before the comparison", res.Lane, c.wantLane)
			}
			if res.NeedsLiveData != c.wantLive {
				t.Errorf("needs_live_data = %v, want %v", res.NeedsLiveData, c.wantLive)
			}
			if res.IntrospectsSelf != c.wantIntros {
				t.Errorf("introspects_self = %v, want %v",
					res.IntrospectsSelf, c.wantIntros)
			}
		})
	}
}
