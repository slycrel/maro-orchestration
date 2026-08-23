package orch

import (
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// Every pin here is a DIFFERENTIAL: CPython's own decompose_mission is
// driven with the same stubbed model answer and the two plans are
// compared. Reconstructing the expectation in Go would only pin my
// reading of the Python, which is the failure mode this port keeps
// producing.
//
// The ids are random UUIDs on both sides, so they are normalised away:
// depends_on is compared as milestone POSITIONS, which is what the edge
// actually means.

func srcDirOrch(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// plan is a decomposition with the random ids replaced by positions.
type plan struct {
	Raised   string     `json:"raised"`
	Titles   []string   `json:"titles"`
	Features [][]string `json:"features"`
	Criteria [][]string `json:"criteria"`
	Deps     [][]int    `json:"deps"`
	Chain    bool       `json:"chain"`
}

// pyDecomposeSnippet drives CPython's decompose_mission against a stub
// adapter that returns argv[1] verbatim, and prints the normalised plan.
// An exception is REPORTED, not swallowed — three malformed payloads
// raise out of decompose_mission in Python (measured), and a Go port that
// smoothed them into a heuristic fallback would produce a working mission
// where Python produces none.
const pyDecomposeSnippet = `
import json, sys, mission
from dataclasses import dataclass

@dataclass
class Resp:
    content: str

class Stub:
    def __init__(self, payload): self.payload = payload
    def complete(self, messages, **kw):
        Stub.seen = (messages, kw)
        return Resp(self.payload)

payload = json.loads(sys.argv[1])
try:
    m = mission.decompose_mission(json.loads(sys.argv[2]), Stub(payload),
                                  int(sys.argv[3]), int(sys.argv[4]))
except Exception as e:
    print(json.dumps({'raised': type(e).__name__}))
    sys.exit(0)

pos = {ms.id: i for i, ms in enumerate(m.milestones)}
print(json.dumps({
    'raised': '',
    'titles': [ms.title for ms in m.milestones],
    'features': [[f.title for f in ms.features] for ms in m.milestones],
    'criteria': [list(ms.validation_criteria) for ms in m.milestones],
    'deps': [[pos[d] for d in ms.depends_on] for ms in m.milestones],
    'chain': mission._is_chain_shaped(m),
}))
`

func pyDecompose(t *testing.T, payload, goal string, maxMS, maxF int) plan {
	t.Helper()
	goalJSON, err := json.Marshal(goal)
	if err != nil {
		t.Fatal(err)
	}
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyDecomposeSnippet,
		string(payloadJSON), string(goalJSON),
		strconv.Itoa(maxMS), strconv.Itoa(maxF))
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDirOrch(t))
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
	var p plan
	if err := json.Unmarshal(out, &p); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}
	return p
}

// goDecompose runs the port and normalises the same way.
func goDecompose(t *testing.T, payload, goal string, maxMS, maxF int) plan {
	t.Helper()
	n := 0
	newID := func() string { n++; return "id" + strconv.Itoa(n) }
	m, err := DecomposeMission(context.Background(), goal,
		&recordingAdapter{reply: payload}, maxMS, maxF,
		func() string { return "2026-08-23T00:00:00+00:00" }, newID)
	if err != nil {
		return plan{Raised: goErrName(err)}
	}
	pos := map[string]int{}
	for i := range m.Milestones {
		pos[m.Milestones[i].ID] = i
	}
	p := plan{Chain: IsChainShaped(m)}
	for i := range m.Milestones {
		ms := &m.Milestones[i]
		p.Titles = append(p.Titles, ms.Title)
		ft := []string{}
		for j := range ms.Features {
			ft = append(ft, ms.Features[j].Title)
		}
		p.Features = append(p.Features, ft)
		cr := []string{}
		cr = append(cr, ms.ValidationCriteria...)
		p.Criteria = append(p.Criteria, cr)
		d := []int{}
		for _, id := range ms.DependsOn {
			d = append(d, pos[id])
		}
		p.Deps = append(p.Deps, d)
	}
	return p
}

// goErrName maps the port's one error back to the Python exception type
// the same payload raises, so the two sides are comparable at all. The
// mapping is in the ERROR TEXT rather than in separate sentinels because
// the distinction Python draws is between subscripting a None and
// slicing a dict — the same `ErrMalformedPlan`, different spelling.
func goErrName(err error) string {
	msg := err.Error()
	switch {
	case strings.Contains(msg, "KeyError"):
		return "KeyError"
	case strings.Contains(msg, "not subscriptable"),
		strings.Contains(msg, "not iterable"):
		return "TypeError"
	}
	return "error"
}

func assertPlansAgree(t *testing.T, got, want plan) {
	t.Helper()
	g, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	w, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if string(g) != string(w) {
		t.Errorf("plans differ\n go: %s\n py: %s", g, w)
	}
}

// The corpus is one payload per measured hazard, not happy-path plans.
func TestDecomposeMatchesCPython(t *testing.T) {
	for _, tc := range []struct{ name, payload string }{
		{"a well-formed two-milestone plan",
			`{"milestones":[{"title":"A","features":["f1","f2"],"validation_criteria":["c1"]},{"title":"B","features":["g1"],"validation_criteria":[]}]}`},

		// str() is not a cast. Each of these lands in a shared store as
		// the literal text a human reads.
		{"a null title becomes the four characters None",
			`{"milestones":[{"title":null,"features":["f"]}]}`},
		{"an absent title becomes Milestone",
			`{"milestones":[{"features":["f"]}]}`},
		{"a dict title becomes its Python repr",
			`{"milestones":[{"title":{"a":1},"features":["f"]}]}`},
		{"an out-of-order dict title keeps insertion order",
			`{"milestones":[{"title":{"b":1,"a":2},"features":["f"]}]}`},
		{"a list title becomes its Python repr",
			`{"milestones":[{"title":["x","y"],"features":["f"]}]}`},
		{"a float title renders as Python floats do",
			`{"milestones":[{"title":1.0,"features":["f"]}]}`},
		{"a bool title becomes True",
			`{"milestones":[{"title":true,"features":["f"]}]}`},
		{"a numeric feature becomes its digits",
			`{"milestones":[{"title":"A","features":[1.0,2]}]}`},
		{"a null feature becomes the string None and is KEPT",
			`{"milestones":[{"title":"A","features":[null,"f"]}]}`},
		{"a title is stripped with Python's whitespace set",
			"{\"milestones\":[{\"title\":\"\\u001c A \\u001f\",\"features\":[\"f\"]}]}"},

		// The two slice orders, four lines apart in the Python.
		{"features slice FIRST and filter second",
			`{"milestones":[{"title":"A","features":["  ","a","b","c"]}]}`},
		{"safe_list filters FIRST and slices second",
			`{"milestones":["nope",{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"]},{"title":"D","features":["f"]},{"title":"E","features":["f"]}]}`},

		// A string is subscriptable and iterable, so it does not raise —
		// it becomes N one-character features.
		{"a STRING features value becomes one feature per character",
			`{"milestones":[{"title":"A","features":"abcde"}]}`},
		{"a string validation_criteria becomes one criterion per character",
			`{"milestones":[{"title":"A","features":["f"],"validation_criteria":"xy"}]}`},

		// Milestones with no usable features are dropped, which shifts
		// every later depends_on index.
		{"a feature-less milestone is dropped",
			`{"milestones":[{"title":"A","features":[]},{"title":"B","features":["f"]}]}`},
		{"a milestone whose features are all blank is dropped",
			`{"milestones":[{"title":"A","features":["  ",""]},{"title":"B","features":["f"]}]}`},

		// depends_on resolution.
		{"absent depends_on chains to the predecessor",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"]}]}`},
		{"an explicit empty list is an independent root",
			`{"milestones":[{"title":"A","features":["f"],"depends_on":[]},{"title":"B","features":["f"],"depends_on":[]},{"title":"C","features":["f"],"depends_on":[]}]}`},
		{"a partial dependency skips a milestone",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[0]}]}`},
		{"two dependencies both resolve",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[0,1]}]}`},
		{"a duplicate ref resolves once",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[0,0]}]}`},
		{"a self ref falls back to the predecessor",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[2]}]}`},
		{"a forward ref falls back to nothing at position 0",
			`{"milestones":[{"title":"A","features":["f"],"depends_on":[1]},{"title":"B","features":["f"]},{"title":"C","features":["f"]}]}`},
		{"an out-of-range ref falls back to the predecessor",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[99]}]}`},
		{"true is not an index, even though a bool is an int",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[true]}]}`},
		{"false is not an index either",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[false]}]}`},
		// 0.0 rather than 1.0 on purpose: with [1.0] the two verdicts
		// COINCIDE — skipping it chains C to B, and accepting it resolves
		// to B as well — so the case could not see the rule at all. [0.0]
		// separates them: accepted it points at A, skipped it chains to B.
		{"0.0 is not an index but 0 is",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[0.0]}]}`},
		{"a numeric string is not an index",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":["0"]}]}`},
		{"a non-list depends_on chains to the predecessor",
			`{"milestones":[{"title":"A","features":["f"]},{"title":"B","features":["f"],"depends_on":"x"},{"title":"C","features":["f"]}]}`},
		{"a ref to a DROPPED milestone falls back to the predecessor",
			`{"milestones":[{"title":"A","features":[]},{"title":"B","features":["f"]},{"title":"C","features":["f"],"depends_on":[0]}]}`},

		// The three payloads that RAISE out of decompose_mission.
		{"a null features value raises TypeError",
			`{"milestones":[{"title":"A","features":null}]}`},
		{"a null validation_criteria raises TypeError",
			`{"milestones":[{"title":"A","features":["f"],"validation_criteria":null}]}`},
		{"a dict features value raises KeyError",
			`{"milestones":[{"title":"A","features":{"k":"v"}}]}`},

		// Fallthrough to the heuristic.
		{"a non-list milestones value falls through", `{"milestones":"nope"}`},
		{"an absent milestones key falls through", `{}`},
		{"an empty milestones list falls through", `{"milestones":[]}`},
		{"unparseable content falls through", `not json at all`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			goal := "build a thing that works"
			want := pyDecompose(t, tc.payload, goal, 4, 3)
			assertPlansAgree(t, goDecompose(t, tc.payload, goal, 4, 3), want)
		})
	}
}

// The heuristic fallback has its own arithmetic — `len(words) // 2 or 1`
// with a zero guard — and it is reached by four corpus entries above,
// all with the same goal. This sweeps the goal instead.
func TestTheHeuristicFallbackMatchesCPython(t *testing.T) {
	for _, goal := range []string{
		"", "   ", "one", "one two", "a b c", "a b c d",
		"  spaced   out  ",
		"a\u001cb", // U+001C: Python splits here, strings.Fields does not
		strings.Repeat("x", 100),
		strings.Repeat("é", 100),           // 60 CODE POINTS, not 60 bytes
		strings.Repeat("日", 45) + " tail",  // multi-byte across the clip
		"word " + strings.Repeat("y", 100), // the second half is the long one
	} {
		t.Run(strconv.Quote(goal), func(t *testing.T) {
			want := pyDecompose(t, `{"milestones":[]}`, goal, 4, 3)
			assertPlansAgree(t, goDecompose(t, `{"milestones":[]}`, goal, 4, 3), want)
		})
	}
}

// The corpus above is only worth its runtime if it reaches every outcome.
// A corpus that never raised, or never fell through, would pass against a
// port missing that whole branch.
func TestTheDecomposeCorpusReachesEveryOutcome(t *testing.T) {
	goal := "build a thing that works"
	counts := map[string]int{}
	for _, payload := range []string{
		`{"milestones":[{"title":"A","features":["f"]}]}`,
		`{"milestones":[{"title":"A","features":null}]}`,
		`{"milestones":[{"title":"A","features":{"k":"v"}}]}`,
		`{"milestones":[]}`,
	} {
		p := pyDecompose(t, payload, goal, 4, 3)
		switch {
		case p.Raised != "":
			counts[p.Raised]++
		case len(p.Titles) == 2 && strings.HasPrefix(p.Titles[0], "Phase 1:"):
			counts["heuristic"]++
		default:
			counts["parsed"]++
		}
	}
	for _, want := range []string{"parsed", "heuristic", "TypeError", "KeyError"} {
		if counts[want] == 0 {
			t.Errorf("the corpus never reaches the %q outcome; a port missing "+
				"that branch entirely would pass", want)
		}
	}
	t.Logf("outcomes: %v", counts)
}

// The prompt is sent to a paid API and recorded in the run log, so a
// reflowed line is a real difference even though it reads the same. This
// compares the bytes of both messages and the request fields.
func TestTheDecomposePromptIsByteIdenticalToPythons(t *testing.T) {
	var want struct {
		System string         `json:"system"`
		User   string         `json:"user"`
		Kwargs map[string]any `json:"kwargs"`
	}
	cmd := exec.Command("python3", "-c", `
import json, sys, mission
from dataclasses import dataclass

@dataclass
class Resp:
    content: str

class Stub:
    def complete(self, messages, **kw):
        self.messages, self.kw = messages, kw
        return Resp('{"milestones":[]}')

s = Stub()
mission.decompose_mission(json.loads(sys.argv[1]), s, 4, 3)
print(json.dumps({'system': s.messages[0].content,
                  'user': s.messages[1].content,
                  'kwargs': s.kw}))
`, `"GOAL HERE"`)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDirOrch(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe FAILED (exit %d):\n%s", ee.ExitCode(), ee.Stderr)
		}
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatal(err)
	}
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}

	rec := &recordingAdapter{reply: `{"milestones":[]}`}
	if _, err := DecomposeMission(context.Background(), "GOAL HERE", rec, 4, 3,
		func() string { return "t" }, func() string { return "i" }); err != nil {
		t.Fatal(err)
	}
	if len(rec.msgs) != 2 {
		t.Fatalf("want 2 messages, got %d", len(rec.msgs))
	}
	if rec.msgs[0].Role != "system" || rec.msgs[0].Content != want.System {
		t.Errorf("system prompt differs\n go %q\n py %q",
			rec.msgs[0].Content, want.System)
	}
	if rec.msgs[1].Role != "user" || rec.msgs[1].Content != want.User {
		t.Errorf("user prompt differs\n go %q\n py %q",
			rec.msgs[1].Content, want.User)
	}
	if rec.opts.MaxTokens != int(want.Kwargs["max_tokens"].(float64)) {
		t.Errorf("max_tokens: go %d, py %v", rec.opts.MaxTokens, want.Kwargs["max_tokens"])
	}
	if rec.opts.Temperature != want.Kwargs["temperature"].(float64) {
		t.Errorf("temperature: go %v, py %v", rec.opts.Temperature, want.Kwargs["temperature"])
	}
	if rec.opts.Purpose != want.Kwargs["purpose"].(string) {
		t.Errorf("purpose: go %q, py %v", rec.opts.Purpose, want.Kwargs["purpose"])
	}
	// no_tools=True on the Python side is the absence of both tool lanes
	// here. A port that enabled the agent tool set would let a planning
	// call ACT on the goal it was asked to merely decompose.
	if len(rec.opts.Tools) != 0 || rec.opts.AgentTools {
		t.Errorf("the decompose call is not in the utility lane: tools=%d agent=%v",
			len(rec.opts.Tools), rec.opts.AgentTools)
	}
}

// recordingAdapter keeps the RAW messages. llm.Fake records what its own
// prompt builder produced, which is not what was asked for here.
type recordingAdapter struct {
	reply string
	// fail is the transient-outage arm: Python's stub raises and
	// _validate_milestone's blanket except swallows it into a PASS.
	fail bool
	msgs []llm.Message
	opts llm.Options
}

func (r *recordingAdapter) Complete(_ context.Context, msgs []llm.Message,
	opts llm.Options) (*llm.Response, error) {
	r.msgs, r.opts = msgs, opts
	if r.fail {
		return nil, errors.New("adapter down")
	}
	return &llm.Response{Content: r.reply}, nil
}

func (r *recordingAdapter) Name() string { return "recording" }
