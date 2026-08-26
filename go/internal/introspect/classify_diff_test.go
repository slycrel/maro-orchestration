package introspect

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyClassifySrc drives introspect.classify_tool_pathologies over events the
// test writes as JSON LINES.
//
// Lines, not literals. A fixture spelled as Python (or Go) values arrives
// with types the real caller never produces — the events reach this
// function off a step_done event that was decoded from disk, so `1` is
// whatever json.loads makes of the literal `1`, and the whole subject here
// is what str() does to it. The Go side decodes the SAME text.
const pyClassifySrc = `
import json, sys
import introspect

_argv = json.loads(sys.argv[1])
events = [json.loads(ln) for ln in _argv["lines"]]
out = introspect.classify_tool_pathologies(events, _argv["step_status"])
print(json.dumps(out))
`

// goClassify decodes the same lines the probe decodes and runs the port.
func goClassify(t *testing.T, lines []string, status string) []map[string]any {
	t.Helper()
	var events []pyval.Obj
	for _, ln := range lines {
		o, err := jsonx.ObjectOrdered(ln)
		if err != nil {
			t.Fatalf("fixture line is not a JSON object: %v (%s)", err, ln)
		}
		events = append(events, o)
	}
	raw, err := json.Marshal(ClassifyToolPathologies(events, status))
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got []map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return got
}

// TestClassifyToolPathologiesMatchesCPython is the differential.
//
// Every assertion here is about a rendered SENTENCE, not a class name. The
// class alone is the easy half — it is a fixed string on both sides. The
// evidence is where a port drifts, because it runs Python's str(), repr(),
// lower() and a RUNE slice over values a model produced, and each of those
// has a Go spelling that is close enough to look right.
func TestClassifyToolPathologiesMatchesCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "introspect.py"}

	cases := []struct {
		name   string
		lines  []string
		status string
	}{
		{name: "no events at all", lines: []string{}, status: "done"},
		{name: "every call succeeded", status: "done", lines: []string{
			`{"name": "bash", "is_error": false, "output": "ok"}`,
			`{"name": "read", "is_error": false, "output": "ok"}`,
		}},

		// --- tool_hallucination -------------------------------------------
		{name: "a hallucinated tool", status: "done", lines: []string{
			`{"name": "Bash", "is_error": false, "output": "ok"}`,
			`{"name": "bash", "is_error": true, "output": "Error: No such tool available"}`,
		}},
		// The match is against the LOWERCASED output, so a shouted error
		// must still be found.
		{name: "the hallucination match is case-folded", status: "done", lines: []string{
			`{"name": "bash", "is_error": true, "output": "ERROR: NO SUCH TOOL AVAILABLE"}`,
		}},
		// {name!r}: a string name comes back QUOTED, and Python's repr picks
		// its own quote character when the value contains one.
		{name: "a hallucinated name containing a quote", status: "done", lines: []string{
			`{"name": "it's-a-tool", "is_error": true, "output": "No such tool available"}`,
		}},
		// A PRESENT null is a value, and repr(None) is a bare None with no
		// quotes — the one case where !r and str() agree, and the reason
		// the port cannot route both through one helper.
		{name: "a hallucinated null name", status: "done", lines: []string{
			`{"name": null, "is_error": true, "output": "No such tool available"}`,
		}},
		// An ABSENT name takes the '?' default, which repr QUOTES.
		{name: "a hallucinated absent name", status: "done", lines: []string{
			`{"is_error": true, "output": "No such tool available"}`,
		}},
		// An integer name is repr'd as a bare number — and json.loads makes
		// it an int, not a float, which is why the Go side must decode
		// ordered rather than through the all-float64 default.
		{name: "a hallucinated numeric name", status: "done", lines: []string{
			`{"name": 7, "is_error": true, "output": "No such tool available"}`,
		}},
		// The is_error gate comes FIRST in the generator, so a SUCCESSFUL
		// call whose output happens to contain the phrase is not a
		// hallucination — a worker printing a help text that quotes the
		// error, or echoing a previous failure, is the realistic shape.
		// Without this fixture, deleting the gate entirely survives the
		// whole battery.
		{name: "the phrase in a successful call is ignored", status: "done", lines: []string{
			`{"name": "echo", "is_error": false, "output": "No such tool available"}`,
			`{"name": "ok", "is_error": false, "output": "fine"}`,
		}},
		// ...and the same phrase in a clean call must not shadow a real one
		// later in the transcript.
		{name: "a clean phrase does not shadow a real hallucination", status: "done", lines: []string{
			`{"name": "echo", "is_error": false, "output": "No such tool available"}`,
			`{"name": "real", "is_error": true, "output": "No such tool available"}`,
		}},
		// next() takes the FIRST match, not the last.
		{name: "two hallucinations report the first", status: "done", lines: []string{
			`{"name": "first", "is_error": true, "output": "No such tool available"}`,
			`{"name": "second", "is_error": true, "output": "No such tool available"}`,
		}},

		// --- is_error truthiness ------------------------------------------
		// Truthiness, not `is True`. A stamp of the STRING "no" is truthy in
		// Python; a Go port reading it through a bool assertion would call
		// this transcript clean.
		{name: "a truthy non-bool is_error", status: "done", lines: []string{
			`{"name": "a", "is_error": "no", "output": "boom"}`,
			`{"name": "b", "is_error": 1, "output": "boom"}`,
			`{"name": "c", "is_error": [0], "output": "boom"}`,
		}},
		// ...and the falsy spellings that must NOT count: empty string,
		// zero, empty list, an explicit null, and absence.
		{name: "the falsy is_error spellings", status: "done", lines: []string{
			`{"name": "a", "is_error": "", "output": "boom"}`,
			`{"name": "b", "is_error": 0, "output": "boom"}`,
			`{"name": "c", "is_error": [], "output": "boom"}`,
			`{"name": "d", "is_error": null, "output": "boom"}`,
			`{"name": "e", "output": "boom"}`,
		}},
		// 0.0 and an empty dict are the two falsy shapes a naive port most
		// often gets wrong in the other direction.
		{name: "zero-float and empty-object is_error", status: "done", lines: []string{
			`{"name": "a", "is_error": 0.0, "output": "boom"}`,
			`{"name": "b", "is_error": {}, "output": "boom"}`,
			`{"name": "c", "is_error": {"k": false}, "output": "boom"}`,
		}},

		// --- tool_recovery_failure ----------------------------------------
		{name: "a streak of two does not fire", status: "done", lines: []string{
			`{"name": "a", "is_error": true, "output": "x"}`,
			`{"name": "b", "is_error": true, "output": "x"}`,
			`{"name": "c", "is_error": false, "output": "x"}`,
		}},
		// The limit's OWN boundary, not its neighbours.
		{name: "a streak of exactly three fires", status: "blocked", lines: []string{
			`{"name": "a", "is_error": true, "output": "x"}`,
			`{"name": "b", "is_error": true, "output": "x"}`,
			`{"name": "c", "is_error": true, "output": "x"}`,
		}},
		// worst_names is `list(streak_names)` — a COPY. If the port aliased
		// the slice, the later, shorter streak's appends would rewrite the
		// recorded run in place and the names here would be wrong while the
		// count stayed right.
		{name: "an earlier longer streak keeps its own names", status: "done", lines: []string{
			`{"name": "a1", "is_error": true, "output": "x"}`,
			`{"name": "a2", "is_error": true, "output": "x"}`,
			`{"name": "a3", "is_error": true, "output": "x"}`,
			`{"name": "a4", "is_error": true, "output": "x"}`,
			`{"name": "ok", "is_error": false, "output": "x"}`,
			`{"name": "b1", "is_error": true, "output": "x"}`,
			`{"name": "b2", "is_error": true, "output": "x"}`,
			`{"name": "b3", "is_error": true, "output": "x"}`,
		}},
		// worst_names[:6] — the count reported is the FULL streak, while
		// only six names are shown. A port that reported len(names) would
		// agree with Python on every streak of six or fewer.
		{name: "a streak of eight shows six names", status: "done", lines: []string{
			`{"name": "n1", "is_error": true, "output": "x"}`,
			`{"name": "n2", "is_error": true, "output": "x"}`,
			`{"name": "n3", "is_error": true, "output": "x"}`,
			`{"name": "n4", "is_error": true, "output": "x"}`,
			`{"name": "n5", "is_error": true, "output": "x"}`,
			`{"name": "n6", "is_error": true, "output": "x"}`,
			`{"name": "n7", "is_error": true, "output": "x"}`,
			`{"name": "n8", "is_error": true, "output": "x"}`,
		}},
		// str(name) here, not repr — the names join UNQUOTED, and a null
		// one renders as None.
		{name: "streak names are str not repr", status: "done", lines: []string{
			`{"name": null, "is_error": true, "output": "x"}`,
			`{"name": 12, "is_error": true, "output": "x"}`,
			`{"is_error": true, "output": "x"}`,
		}},

		// --- tool_feedback_neglect ----------------------------------------
		{name: "a done step whose last call errored", status: "done", lines: []string{
			`{"name": "read", "is_error": false, "output": "fine"}`,
			`{"name": "write", "is_error": true, "output": "permission denied"}`,
		}},
		// The gate is the STATUS. Same transcript, different verdict.
		{name: "a blocked step whose last call errored", status: "blocked", lines: []string{
			`{"name": "write", "is_error": true, "output": "permission denied"}`,
		}},
		// [:120] is a RUNE slice in Python. The output here is 200 CJK
		// characters — 600 bytes — so a byte slice would cut mid-rune and
		// hand the operator a mojibake tail.
		{name: "the neglect clip is runes not bytes", status: "done", lines: []string{
			`{"name": "w", "is_error": true, "output": "` +
				strings.Repeat("\u6f22", 200) + `"}`},
		},
		// An output of exactly 120 runes is the clip's own boundary.
		{name: "an output of exactly the clip length", status: "done", lines: []string{
			`{"name": "w", "is_error": true, "output": "` +
				strings.Repeat("\u00e9", 120) + `"}`},
		},
		// str() over a non-string output. A dict renders through Python's
		// own repr rules — single quotes, ", " separators, insertion order,
		// True/False/None — which is why the events must be decoded ordered.
		{name: "a structured output in the neglect evidence", status: "done", lines: []string{
			`{"name": "w", "is_error": true, ` +
				`"output": {"b": 1, "a": [true, null], "c": "s"}}`},
		},
		// A float output: str(1.0) is "1.0" and str(1) is "1", and
		// json.Unmarshal's all-float64 default would make both the former.
		{name: "a numeric output in the neglect evidence", status: "done", lines: []string{
			`{"name": "w", "is_error": true, "output": 1}`,
			`{"name": "x", "is_error": true, "output": 1.0}`,
		}},

		// --- tool_arg_malformed -------------------------------------------
		{name: "an argument-shape failure", status: "done", lines: []string{
			`{"name": "grep", "is_error": true, "output": "usage: grep [OPTION]..."}`,
		}},
		// The signature list is a TUPLE and next() takes the first member
		// that matches — not the earliest match in the text. Here
		// "invalid argument" appears first in the OUTPUT and "usage:" first
		// in the LIST, so the reported signature is "usage:".
		{name: "the signature order is the list not the text", status: "done", lines: []string{
			`{"name": "g", "is_error": true, ` +
				`"output": "invalid argument -- q; usage: g [OPTS]"}`},
		},
		// Matched against the LOWERCASED output, but the evidence quotes
		// the ORIGINAL. A port that clipped out_text would lose the case.
		{name: "the match folds but the evidence does not", status: "done", lines: []string{
			`{"name": "py", "is_error": true, ` +
				`"output": "TypeError: f() missing 1 required positional argument"}`},
		},
		// One specimen per step: the loop breaks after the first errored
		// event with a signature, even though a later one also matches.
		{name: "only the first specimen is reported", status: "blocked", lines: []string{
			`{"name": "one", "is_error": true, "output": "unknown flag: --z"}`,
			`{"name": "two", "is_error": true, "output": "usage: two"}`,
		}},
		// A clean event carrying a signature is not a specimen — the
		// is_error gate comes first.
		{name: "a signature in a successful call is ignored", status: "done", lines: []string{
			`{"name": "help", "is_error": false, "output": "usage: help"}`,
			`{"name": "ok", "is_error": false, "output": "fine"}`,
		}},
		// The deliberate exclusions: an environment gap is not the
		// model-tool edge, and must produce no arg_malformed row.
		{name: "environment gaps are not argument failures", status: "blocked", lines: []string{
			`{"name": "jq", "is_error": true, "output": "jq: command not found"}`,
			`{"name": "cat", "is_error": true, "output": "No such file or directory"}`,
		}},
		// Every remaining signature, one event each, to pin the list itself
		// rather than a sample of it. The FIRST is what gets reported, so
		// each is checked in its own case below as well.
		{name: "a later signature member still matches", status: "done", lines: []string{
			`{"name": "z", "is_error": true, "output": "invalid choice: 'q'"}`,
		}},

		// --- all four at once ---------------------------------------------
		// The output list's ORDER is fixed by the order the checks run, not
		// by the transcript. A caller stamps this list on a step outcome and
		// renders the first entry.
		{name: "every class fires at once", status: "done", lines: []string{
			`{"name": "nope", "is_error": true, "output": "No such tool available"}`,
			`{"name": "grep", "is_error": true, "output": "usage: grep"}`,
			`{"name": "w", "is_error": true, "output": "boom"}`,
		}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			arg := pyprobe.Arg(t, map[string]any{
				"lines":       tc.lines,
				"step_status": tc.status,
			})
			var want []map[string]any
			p.RunJSON(t, pyClassifySrc, &want, arg)
			got := goClassify(t, tc.lines, tc.status)
			// No nil-vs-empty normalization. json.dumps([]) is "[]" and a nil
			// Go slice marshals to "null" — a caller stamping this list onto
			// a step outcome writes one or the other into the shared events
			// store, and Python's own reader (`list(e.get(...) or [])`)
			// tolerating null is not a reason for the port to write it. An
			// earlier draft normalized here, and the mutant that returns nil
			// for a clean transcript survived the whole battery because of it.
			if !reflect.DeepEqual(want, got) {
				t.Fatalf("classification differs\ncpython: %#v\n     go: %#v", want, got)
			}
		})
	}
}

// TestEverySignatureIsReachable walks the signature list one member at a
// time, because the differential above can only ever exercise the members
// its fixtures happen to name — and a list is not a class (lens 2). A
// member that was mistyped in the port would be invisible to every case
// that matches an earlier one.
func TestEverySignatureIsReachable(t *testing.T) {
	p := pyprobe.Probe{Marker: "introspect.py"}
	const pySigsSrc = `
import json
import introspect
print(json.dumps(list(introspect._TOOL_ARG_ERROR_SIGNATURES)))
`
	var want []string
	p.RunJSON(t, pySigsSrc, &want)
	if !reflect.DeepEqual(want, toolArgErrorSignatures) {
		t.Fatalf("signature list differs\ncpython: %q\n     go: %q",
			want, toolArgErrorSignatures)
	}
	// Reachability is a separate claim from equality: a member that is a
	// SUBSTRING of an earlier member could never be the reported signature,
	// and the two lists would still compare equal.
	for _, sig := range toolArgErrorSignatures {
		got := ClassifyToolPathologies([]pyval.Obj{
			{{Key: "name", Val: "t"}, {Key: "is_error", Val: true},
				{Key: "output", Val: "prefix " + strings.ToUpper(sig) + " suffix"}},
		}, "blocked")
		if len(got) != 1 || got[0].Class != "tool_arg_malformed" {
			t.Fatalf("signature %q produced %#v, want one tool_arg_malformed", sig, got)
		}
		if !strings.Contains(got[0].Evidence, "'"+sig+"'") {
			t.Fatalf("signature %q reported as %q", sig, got[0].Evidence)
		}
	}
}

// TestFailureClassesMatchCPython pins the taxonomy AND its prose.
//
// The descriptions are not decoration: `maro-introspect` prints them and
// graduation stamps a class into a lesson an operator reads. Two runtimes
// describing the same class differently is the content-key PROSE
// divergence this port keeps finding, one store further along.
func TestFailureClassesMatchCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "introspect.py"}
	const pyClassesSrc = `
import json
import introspect
print(json.dumps(introspect.FAILURE_CLASSES))
`
	var want map[string]string
	p.RunJSON(t, pyClassesSrc, &want)
	if !reflect.DeepEqual(want, FailureClasses) {
		for k, v := range want {
			if g, ok := FailureClasses[k]; !ok {
				t.Errorf("missing class %q", k)
			} else if g != v {
				t.Errorf("class %q:\ncpython: %q\n     go: %q", k, v, g)
			}
		}
		for k := range FailureClasses {
			if _, ok := want[k]; !ok {
				t.Errorf("extra class %q not in CPython", k)
			}
		}
		t.FailNow()
	}
}

// TestThresholdsMatchCPython reads the named constants out of the module
// rather than restating them, so a calibration change on the Python side
// fails here instead of silently making the two runtimes disagree about
// what "too broad" means.
func TestThresholdsMatchCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "introspect.py"}
	const pyThreshSrc = `
import json
import introspect
print(json.dumps({
    "broad_tokens": introspect._BROAD_STEP_TOKEN_LIMIT,
    "broad_ms": introspect._BROAD_STEP_ELAPSED_MS,
    "broad_min_tokens": introspect._BROAD_STEP_ELAPSED_MIN_TOKENS,
    "explosion_ratio": introspect._TOKEN_EXPLOSION_RATIO,
    "retry_churn": introspect._RETRY_CHURN_LIMIT,
    "cost_fraction": introspect._COST_SPIKE_FRACTION,
    "step_usd": introspect._STEP_COST_WARN_USD,
    "loop_usd": introspect._LOOP_COST_WARN_USD,
    "streak": introspect._TOOL_ERROR_STREAK_LIMIT,
}))
`
	var want map[string]float64
	p.RunJSON(t, pyThreshSrc, &want)
	got := map[string]float64{
		"broad_tokens":     BroadStepTokenLimit,
		"broad_ms":         BroadStepElapsedMS,
		"broad_min_tokens": BroadStepElapsedMinTokens,
		"explosion_ratio":  TokenExplosionRatio,
		"retry_churn":      RetryChurnLimit,
		"cost_fraction":    CostSpikeFraction,
		"step_usd":         StepCostWarnUSD,
		"loop_usd":         LoopCostWarnUSD,
		"streak":           ToolErrorStreakLimit,
	}
	if !reflect.DeepEqual(want, got) {
		t.Fatalf("thresholds differ\ncpython: %v\n     go: %v", want, got)
	}
}
