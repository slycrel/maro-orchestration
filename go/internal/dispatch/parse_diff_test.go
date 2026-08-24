package dispatch

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The probe reports the SAME four shapes the Go side builds below, in the
// same key order, so the two can be compared as bytes. A structural compare
// would let a field drift in type — `1` against `1.0`, `"None"` against
// null — which is the divergence class this whole port is about.
const pyParseSrc = `
import json, sys
from dispatch_envelope import parse_dispatch_payload, EnvelopeError

out = []
for p in json.loads(sys.argv[1]):
    try:
        e = parse_dispatch_payload(p)
    except EnvelopeError as exc:
        out.append({"outcome": "refused", "message": str(exc)})
        continue
    if e is None:
        out.append({"outcome": "prose"})
        continue
    out.append({
        "outcome": "parsed",
        "user_ask": e.user_ask,
        "operator_context": e.operator_context,
        "operator_constraints": list(e.operator_constraints),
        "attached_artifacts": list(e.attached_artifacts),
    })
sys.stdout.write(json.dumps(out))
`

// TestParseDispatchPayloadMatchesCPython drives every branch of the parser
// and every refusal message, plus the shapes that decide which branch runs.
//
// The interesting cases are not the well-formed ones:
//
//   - a `{`-leading payload that NAMES the version and fails to parse is the
//     only parse failure that refuses rather than falling back to prose;
//   - NaN is a bare token CPython's json accepts and Go's rejects, and a
//     rejection here would turn a valid dispatch into prose — which runs the
//     JSON soup as the goal;
//   - a duplicated key is last-wins in CPython, and a port that kept the
//     first would run a different ask than the dispatcher sent;
//   - the artifact checks run as a SECOND pass, so a list holding one
//     non-object and one nameless object reports the LIST error.
func TestParseDispatchPayloadMatchesCPython(t *testing.T) {
	payloads := []any{
		// Not a string at all — Python's first line is an isinstance check.
		nil, 42, []any{"a"}, map[string]any{"envelope": Version},
		// Prose and near-misses.
		"", "   ", "just prose", "prose mentioning maro-dispatch/v1",
		`["a"]`, `{"a":1}`, `{"envelope":"other"}`, `{"envelope":42}`,
		`{not json}`,
		// Declared and broken.
		`{"envelope":"maro-dispatch/v1"`,
		`{"envelope": "maro-dispatch/v1", "user_ask": "x"}{}`,
		`{"envelope":"maro-dispatch/v1"}`,
		`{"envelope":"maro-dispatch/v1","user_ask":""}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"   "}`,
		`{"envelope":"maro-dispatch/v1","user_ask":5}`,
		`{"envelope":"maro-dispatch/v1","user_ask":null}`,
		// Well-formed, minimal and full.
		`{"envelope":"maro-dispatch/v1","user_ask":"  ship it  "}`,
		`  {"envelope":"maro-dispatch/v1","user_ask":"ship it"}  `,
		`{"envelope":"maro-dispatch/v1","user_ask":"ship it","operator_context":"  framing  "}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_context":""}`,
		// operator_context type refusals.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_context":null}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_context":5}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_context":["a"]}`,
		// operator_constraints.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_constraints":["  a  ","","   ","b"]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_constraints":[]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_constraints":"abc"}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_constraints":[1]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","operator_constraints":null}`,
		// attached_artifacts.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":{}}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[1]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{}]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":"  "}]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":5,"content":"c"}]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":"a.txt"}]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":"a.txt","content":5}]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":"a.txt","content":null}]}`,
		// The name lands in the message through repr(), so its escaping is
		// part of the contract.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":"it's \"a\"\nfile"}]}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":"café.txt"}]}`,
		// A list holding a non-object AND a nameless object: the list check
		// is a whole pass earlier, so it wins.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"name":""},7]}`,
		// Well-formed artifacts, extra keys and key ORDER preserved.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","attached_artifacts":[{"content":"c","name":"a.txt","source":"https://x","extra":{"k":[1,2]}}]}`,
		// CPython's json accepts these bare tokens; Go's rejects them, and a
		// rejection would silently demote a valid dispatch to prose.
		`{"envelope":"maro-dispatch/v1","user_ask":"x","n":NaN}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"x","n":Infinity}`,
		// Last key wins in CPython.
		`{"envelope":"maro-dispatch/v1","user_ask":"first","user_ask":"second"}`,
		// Unicode and whitespace CPython's strip() covers and Go's does not
		// by default.
		`{"envelope":"maro-dispatch/v1","user_ask":"ship"}`,
		`{"envelope":"maro-dispatch/v1","user_ask":"日本語の依頼"}`,
	}

	want := pyprobe.Probe{Marker: "dispatch_envelope.py"}.
		Run(t, pyParseSrc, pyprobe.Arg(t, payloads))

	// A differential that agrees proves nothing until the fixture is known
	// to reach every lane. These counts are the fixture's own guard: a case
	// list that drifted into all-prose would still agree with CPython and
	// still be testing nothing (the r6 lens).
	for lane, min := range map[string]int{
		"refused": 18, "parsed": 12, "prose": 9,
	} {
		if n := strings.Count(want, `"outcome": "`+lane+`"`); n < min {
			t.Fatalf("the fixture reaches the %q lane only %d times (want >= %d) "+
				"— the case list has drifted and this differential is weaker "+
				"than it reads", lane, n, min)
		}
	}

	var got pyval.List
	for _, p := range payloads {
		env, err := ParseDispatchPayload(p)
		switch {
		case err != nil:
			got = append(got, pyval.Obj{
				{Key: "outcome", Val: "refused"},
				{Key: "message", Val: err.Error()},
			})
		case env == nil:
			got = append(got, pyval.Obj{{Key: "outcome", Val: "prose"}})
		default:
			arts := pyval.List{}
			for _, a := range env.AttachedArtifacts {
				arts = append(arts, a)
			}
			cons := pyval.List{}
			for _, c := range env.OperatorConstraints {
				cons = append(cons, c)
			}
			got = append(got, pyval.Obj{
				{Key: "outcome", Val: "parsed"},
				{Key: "user_ask", Val: env.UserAsk},
				{Key: "operator_context", Val: env.OperatorContext},
				{Key: "operator_constraints", Val: cons},
				{Key: "attached_artifacts", Val: arts},
			})
		}
	}
	rendered, err := pyval.DumpsCompactPy(got)
	if err != nil {
		t.Fatalf("rendering the Go result: %v", err)
	}
	if rendered != want {
		// Report the first differing case rather than two 6KB blobs.
		t.Errorf("parse results diverge from CPython\n got: %s\nwant: %s",
			firstDiff(rendered, want), firstDiff(want, rendered))
	}
}

// firstDiff returns a 200-byte window of a starting at the first byte where
// it differs from b.
func firstDiff(a, b string) string {
	i := 0
	for i < len(a) && i < len(b) && a[i] == b[i] {
		i++
	}
	start := i - 60
	if start < 0 {
		start = 0
	}
	end := i + 140
	if end > len(a) {
		end = len(a)
	}
	return a[start:end]
}
