package evolver

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// pyMintSrc runs ONE evolver cycle over a canned model reply and hands back
// the suggestion rows it minted.
//
// It drives run_evolver rather than re-spelling the five `raw.get(...)`
// expressions in the probe, because a probe that re-spells them is a test
// of my reading of the source. Every scan is switched off so the batch is
// exactly the LLM mint — the Go side passes no ExtraSuggestions, which is
// the same cut.
const pyMintSrc = `
import json, sys
import evolver

_argv = json.loads(sys.argv[1])

class _Resp:
    def __init__(self, content):
        self.content = content

class _Stub:
    def complete(self, messages, **kw):
        return _Resp(_argv["reply"])

rep = evolver.run_evolver(
    adapter=_Stub(), verbose=False, notify=False,
    scan_signals=False, scan_calibration=False, scan_costs=False,
    scan_drift=False, scan_canon=False, scan_suggestion_calibration=False,
    scan_persona_gaps=False, scan_harness_friction=False,
    scan_skill_candidates=False)

_FOUR = ("category", "failure_pattern", "suggestion", "target")

print(json.dumps({
    # The four str()-rendered fields as a STRING, so a Go decode cannot
    # re-type them on the way in: json.Unmarshal into a Go "any" makes 1.0
    # and 1 the same float64, and telling them apart is the whole subject.
    "verbatim": [json.dumps({k: s.to_dict()[k] for k in _FOUR}, sort_keys=True)
                 for s in rep.suggestions],
    "rows": [s.to_dict() for s in rep.suggestions],
    "patterns": rep.failure_patterns,
    "skipped": rep.skipped,
    "skip_reason": rep.skip_reason,
    "reviewed": rep.outcomes_reviewed,
}))
`

// mintVolatile are the two fields no differential can compare: the id
// embeds a fresh run id and generated_at is a clock read. Everything else
// in the row is a function of the reply, and that is the point of the test.
var mintVolatile = map[string]bool{"suggestion_id": true, "generated_at": true}

// TestMintCoercesLikeCPython pins the five `raw.get(...)` coercions at the
// mint site.
//
// The port spelled the first four as "substitute the default when the value
// is missing OR EMPTY", which is `or`, not `.get`. Python's `.get(k, d)`
// asks about PRESENCE: a model that answers `"target": ""` means the empty
// string and CPython stores it. Measured on the reply below before the fix,
// five of five fields differed on one ordinary suggestion — and because
// category, target and suggestion are the three inputs to _content_key, the
// divergence also changed the row's dedup IDENTITY, so the two runtimes
// disagreed about which findings they already had (adversarial r3, HIGH).
//
// The fifth, `pattern`, is `str(raw.get("pattern","") or "")` — a
// truthiness gate AND a str(). The port asserted `.(string)`, so a
// new_guardrail whose pattern the model answered as `["rm -rf"]` reached
// the store with an EMPTY pattern: recorded as applied, matching nothing.
func TestMintCoercesLikeCPython(t *testing.T) {
	cases := []struct {
		name  string
		reply string
	}{
		// The shape the fix is about: present-and-empty is not absent.
		{name: "present and empty is not absent", reply: `{
			"failure_patterns": ["p"],
			"suggestions": [{"category": "", "target": "", "suggestion": "",
			                 "failure_pattern": "", "confidence": 0.4}]
		}`},
		// All five absent — the defaults, which is the only case the old
		// spelling agreed on and therefore the only one anyone wrote.
		{name: "all absent takes the defaults", reply: `{
			"failure_patterns": [],
			"suggestions": [{"confidence": 0.4}]
		}`},
		// A LIST pattern, which is what a model actually produces when it
		// reads "pattern" as "the things to match".
		{name: "a list pattern renders through str", reply: `{
			"suggestions": [{"category": "new_guardrail", "target": "all",
			                 "suggestion": "no destructive shell",
			                 "pattern": ["rm -rf", "mkfs"], "confidence": 0.4}]
		}`},
		// A DICT pattern. str() over a dict keeps INSERTION order, which
		// is why the reply is decoded ordered rather than into a Go map.
		{name: "a dict pattern keeps insertion order", reply: `{
			"suggestions": [{"category": "new_guardrail",
			                 "pattern": {"b": 1, "a": 2, "z": [3, null]},
			                 "confidence": 0.4}]
		}`},
		// The truthiness half of the pattern gate: 0, false, [] and {} are
		// all "" — NOT "0", "False", "[]", "{}".
		{name: "a falsy pattern is empty", reply: `{
			"suggestions": [
				{"pattern": 0, "confidence": 0.4},
				{"pattern": false, "confidence": 0.4},
				{"pattern": [], "confidence": 0.4},
				{"pattern": {}, "confidence": 0.4},
				{"pattern": null, "confidence": 0.4}
			]
		}`},
		// A truthy NUMBER pattern: str(7) is "7" and str(7.0) is "7.0", and
		// only the source literal tells them apart.
		{name: "a numeric pattern keeps its literal", reply: `{
			"suggestions": [
				{"category": "a", "pattern": 7, "confidence": 0.4},
				{"category": "b", "pattern": 7.0, "confidence": 0.4},
				{"category": "c", "pattern": true, "confidence": 0.4}
			]
		}`},
		// expected_signal is safe_list(element_type=dict): non-dict
		// elements are dropped, and a non-list value yields [].
		{name: "expected signal filters to dicts", reply: `{
			"suggestions": [
				{"category": "a", "expected_signal": [{"metric": "x"}, "no", 3,
				                                      null, {"metric": "y"}],
				 "confidence": 0.4},
				{"category": "b", "expected_signal": "not a list",
				 "confidence": 0.4},
				{"category": "c", "confidence": 0.4}
			]
		}`},
		// A non-dict element of "suggestions" is dropped by
		// safe_list(element_type=dict) before the mint ever sees it.
		{name: "a non-dict suggestion is dropped", reply: `{
			"suggestions": ["a string", 3, null, ["nested"],
			                {"category": "kept", "confidence": 0.4}]
		}`},
		// The reply is not JSON at all. extract_json finds nothing, both
		// runtimes mint nothing, and the cycle is not an error.
		{name: "an unparseable reply mints nothing", reply: "sorry, no."},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			want := runMintOnCPython(t, c.reply)
			got := runMintOnGo(t, c.reply)
			compareMintRows(t, got, want.Rows)
		})
	}
}

// TestTheMintsNonStringDivergenceIsPinned is a KNOWN-GAP pin, not an
// agreement test.
//
// CPython's Suggestion is a dataclass and a dataclass does not enforce its
// annotations, so `suggestion=raw.get("suggestion","")` on a reply of
// `"suggestion": 5` stores the INT and json.dumps writes `5`. The port's
// field is a `string` and cannot hold one, so it renders through str().
// Both stay string-shaped to every reader — the read sites are all
// `str(x or "")` — so this is a byte divergence in a shared store, not a
// behavioural one.
//
// It is pinned rather than left to be rediscovered: if the five fields are
// ever widened to `any`, this test fails and says so. Widening them is a
// 38-call-site change and is not this tranche.
func TestTheMintsNonStringDivergenceIsPinned(t *testing.T) {
	const reply = `{"suggestions": [{"category": {"b": 1, "a": 2},
	                                  "target": ["x", "y"],
	                                  "suggestion": 5, "failure_pattern": null,
	                                  "confidence": 0.4},
	                                 {"category": 1.5, "target": true,
	                                  "suggestion": 10000000000000000000000,
	                                  "failure_pattern": 1.0,
	                                  "confidence": 0.4}]}`
	want := runMintOnCPython(t, reply)
	got := runMintOnGo(t, reply)
	if len(want.Rows) != 2 || len(got) != 2 {
		t.Fatalf("expected two rows each, got py=%d go=%d", len(want.Rows), len(got))
	}

	// CPython stores the decoded value verbatim, containers included — as
	// JSON, the row round-trips to what the model sent.
	pyVerbatim := []string{
		`{"category": {"a": 2, "b": 1}, "failure_pattern": null, "suggestion": 5, "target": ["x", "y"]}`,
		`{"category": 1.5, "failure_pattern": 1.0, "suggestion": 10000000000000000000000, "target": true}`,
	}
	for i, wantJSON := range pyVerbatim {
		if gotJSON := want.Verbatim[i]; gotJSON != wantJSON {
			t.Errorf("row %d: CPython no longer stores the four fields "+
				"verbatim\n got: %s\nwant: %s\nthe divergence this pin "+
				"describes has changed shape", i, gotJSON, wantJSON)
		}
	}

	// The port renders each through str(). The DICT and the LIST are why
	// the reply is decoded ordered: str() over a container uses repr inside
	// it and keeps INSERTION order, so a Go map can produce neither the
	// quoting nor the ordering.
	rendered := []map[string]string{{
		"category": "{'b': 1, 'a': 2}", "target": "['x', 'y']",
		"suggestion": "5", "failure_pattern": "None",
	}, {
		"category": "1.5", "target": "True",
		"suggestion": "10000000000000000000000", "failure_pattern": "1.0",
	}}
	for i, wantFields := range rendered {
		for k, v := range wantFields {
			if got[i][k] != v {
				t.Errorf("row %d: the port's %s = %#v, want the str() "+
					"rendering %q — if the field has been widened to `any`, "+
					"DELETE this pin and fold the case into "+
					"TestMintCoercesLikeCPython", i, k, got[i][k], v)
			}
		}
	}
}

// mintProbe is what pyMintSrc returns.
type mintProbe struct {
	Rows       []map[string]any `json:"rows"`
	Verbatim   []string         `json:"verbatim"`
	Patterns   []string         `json:"patterns"`
	Skipped    bool             `json:"skipped"`
	SkipReason string           `json:"skip_reason"`
	Reviewed   int              `json:"reviewed"`
}

func runMintOnCPython(t *testing.T, reply string) mintProbe {
	t.Helper()
	ws := t.TempDir()
	seedMintOutcomes(t, ws)
	var out mintProbe
	pyprobe.Probe{Marker: "evolver.py", Workspace: ws}.RunJSON(
		t, pyMintSrc, &out, pyprobe.Arg(t, map[string]any{"reply": reply}))
	if out.Skipped {
		t.Fatalf("the CPython cycle skipped (%s) — the fixture is not "+
			"exercising the mint", out.SkipReason)
	}
	if out.Reviewed != 3 {
		t.Fatalf("CPython reviewed %d outcomes, want 3 — the seeded store "+
			"is not the one it read", out.Reviewed)
	}
	return out
}

func runMintOnGo(t *testing.T, reply string) []map[string]any {
	t.Helper()
	// A SEPARATE workspace from CPython's. One shared root would let each
	// runtime's writes seed the other's read, and the outcomes file is
	// exactly the input the mint's `outcomes_analyzed` counts.
	ws := t.TempDir()
	seedMintOutcomes(t, ws)
	rep := Run(context.Background(), ws, record.New(ws), nil,
		&llm.Fake{Script: []string{reply}}, RunOptions{})
	if rep.Skipped {
		t.Fatalf("the Go cycle skipped (%s) — the fixture is not exercising "+
			"the mint", rep.SkipReason)
	}
	var rows []map[string]any
	for _, s := range rep.Suggestions {
		b, err := json.Marshal(s)
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		dec := json.NewDecoder(strings.NewReader(string(b)))
		dec.UseNumber()
		if err := dec.Decode(&m); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, m)
	}
	return rows
}

// seedMintOutcomes writes just over min_outcomes rows, so the cycle runs
// and `outcomes_analyzed` has a value both runtimes must agree on.
func seedMintOutcomes(t *testing.T, ws string) {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	// outcome_id and lessons are REQUIRED positional fields of CPython's
	// Outcome dataclass, and load_outcomes builds one per row: a row
	// without them raises TypeError and is excluded with a warning. The
	// port's loader is looser and returns the row anyway, so a fixture
	// missing them silently gives CPython 0 outcomes and Go 3 — the cycle
	// skips on one side and runs on the other, and the differential
	// compares two empty lists and reports agreement.
	body := `{"outcome_id": "o1", "lessons": [], "status": "stuck", "task_type": "build", "goal": "g1", "summary": "s1"}
{"outcome_id": "o2", "lessons": [], "status": "stuck", "task_type": "build", "goal": "g2", "summary": "s2"}
{"outcome_id": "o3", "lessons": [], "status": "done", "task_type": "build", "goal": "g3", "summary": "s3", "goal_achieved": true}
`
	if err := os.WriteFile(filepath.Join(dir, "outcomes.jsonl"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// compareMintRows compares row for row, field for field, skipping only the
// clock and the id.
//
// Field for field rather than by marshalled bytes: a byte comparison would
// also be failing on the nested-dict KEY ORDER inside expected_signal,
// which is a separate named divergence (Suggestion.ExpectedSignal is
// []map[string]any and a Go map marshals sorted). Comparing decoded values
// measures the coercions this test is about and leaves that one to its own
// pin instead of hiding it inside a diff nobody can read.
func compareMintRows(t *testing.T, got, want []map[string]any) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("minted %d rows, CPython %d\n go: %#v\n py: %#v",
			len(got), len(want), got, want)
	}
	for i := range got {
		keys := map[string]bool{}
		for k := range got[i] {
			keys[k] = true
		}
		for k := range want[i] {
			keys[k] = true
		}
		for k := range keys {
			if mintVolatile[k] {
				continue
			}
			g, gok := got[i][k]
			w, wok := want[i][k]
			if gok != wok {
				t.Errorf("row %d: key %q present go=%v py=%v", i, k, gok, wok)
				continue
			}
			if !sameJSON(g, w) {
				t.Errorf("row %d field %q\n go: %#v\n py: %#v", i, k, g, w)
			}
		}
	}
}

// sameJSON compares two decoded JSON values by their re-marshalled bytes.
//
// Both sides are decoded with UseNumber, so a number compares by its
// LITERAL — which is the point, since 7 and 7.0 are two different Python
// values with two different str() renderings and json.Marshal of a float64
// cannot tell them apart.
func sameJSON(a, b any) bool {
	ab, aerr := json.Marshal(a)
	bb, berr := json.Marshal(b)
	if aerr != nil || berr != nil {
		return false
	}
	return string(ab) == string(bb)
}

// pySuggestionLoadSrc drives the two suggestion readers over a store the
// test writes byte for byte.
const pySuggestionLoadSrc = `
import json, sys
import evolver_store

_argv = json.loads(sys.argv[1])
rows = evolver_store.load_suggestions(limit=_argv["limit"])
got = evolver_store.get_suggestion(_argv["get"])
print(json.dumps({
    "ids": [s.suggestion_id for s in rows],
    "get": None if got is None else got.suggestion_id,
    "get_applied_manually": None if got is None else got.applied_manually,
}))
`

// TestSuggestionReadersMatchCPython pins the SIBLING of load_outcomes'
// schema filter.
//
// load_suggestions is `_rows_as(p, "load_suggestions", Suggestion.from_dict)`
// — the same two-readers-stacked shape, and the port had the same half of
// it. Suggestion has SEVEN fields with no default, so a row missing any of
// them raises TypeError in from_dict and is excluded as schema drift.
//
// get_suggestion is the one that matters most. It catches the TypeError and
// treats the row as ABSENT, and it is the lookup the auto-revert guard uses
// to re-confirm authority just before an irreversible revert. The port
// zero-filled instead, which hands that guard an applied_manually of false
// — a human-applied row routed into the auto-revert branch. Same unsafe
// direction as the r1 security finding at this same function.
func TestSuggestionReadersMatchCPython(t *testing.T) {
	full := func(id string, extra string) string {
		return `{"suggestion_id": "` + id + `", "category": "observation", ` +
			`"target": "all", "suggestion": "s", "failure_pattern": "f", ` +
			`"confidence": 0.5, "outcomes_analyzed": 3` + extra + `}`
	}
	cases := []struct {
		name  string
		lines []string
		get   string
	}{
		{name: "every row complete", get: "s2", lines: []string{
			full("s1", ""), full("s2", ""),
		}},
		// The shape the port got wrong: a row missing outcomes_analyzed is
		// JSON, well-formed, and rejected by the dataclass.
		{name: "a row missing one field is excluded", get: "s2", lines: []string{
			full("s1", ""),
			`{"suggestion_id": "s2", "category": "observation", "target": "all", ` +
				`"suggestion": "s", "failure_pattern": "f", "confidence": 0.5}`,
		}},
		// The dangerous one: the drifted row is APPLIED BY A HUMAN, and the
		// guard asks for it by id. CPython answers "absent"; a zero-filled
		// answer says applied_manually=false, which is permission to revert.
		{name: "a drifted human-applied row is absent, not permissive",
			get: "s1", lines: []string{
				`{"suggestion_id": "s1", "category": "observation", ` +
					`"target": "all", "suggestion": "s", ` +
					`"applied": true, "applied_manually": true}`,
			}},
		{name: "a present null is a value", get: "s1", lines: []string{
			`{"suggestion_id": "s1", "category": null, "target": null, ` +
				`"suggestion": null, "failure_pattern": null, ` +
				`"confidence": null, "outcomes_analyzed": null}`,
		}},
		{name: "an unknown id is absent", get: "nope", lines: []string{full("s1", "")}},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			body := ""
			for _, l := range c.lines {
				body += l + "\n"
			}
			for _, ws := range []string{pyWS, goWS} {
				dir := filepath.Join(ws, "memory")
				if err := os.MkdirAll(dir, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(dir, "suggestions.jsonl"),
					[]byte(body), 0o644); err != nil {
					t.Fatal(err)
				}
			}

			var want struct {
				IDs              []string `json:"ids"`
				Get              *string  `json:"get"`
				GetAppliedManual *bool    `json:"get_applied_manually"`
			}
			pyprobe.Probe{Marker: "evolver_store.py", Workspace: pyWS}.RunJSON(
				t, pySuggestionLoadSrc, &want,
				pyprobe.Arg(t, map[string]any{"limit": 20, "get": c.get}))

			got := LoadSuggestions(goWS, 20)
			if len(got) != len(want.IDs) {
				t.Fatalf("loaded %d suggestions, CPython %d (%v)",
					len(got), len(want.IDs), want.IDs)
			}
			for i := range got {
				if got[i].SuggestionID != want.IDs[i] {
					t.Errorf("row %d id %q, CPython %q", i, got[i].SuggestionID, want.IDs[i])
				}
			}
			one := GetSuggestion(goWS, c.get)
			if (one == nil) != (want.Get == nil) {
				t.Fatalf("get_suggestion(%q): go=%v py=%v — a row CPython "+
					"treats as absent must not come back zero-filled",
					c.get, one, want.Get)
			}
			if one != nil {
				if one.SuggestionID != *want.Get {
					t.Errorf("get id %q, CPython %q", one.SuggestionID, *want.Get)
				}
				if one.AppliedManually != *want.GetAppliedManual {
					t.Errorf("get applied_manually %v, CPython %v",
						one.AppliedManually, *want.GetAppliedManual)
				}
			}
		})
	}
}
