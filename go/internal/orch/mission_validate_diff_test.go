package orch

import (
	"context"
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

// The validation gate DEFAULTS TO PASS at every one of its four exits —
// no criteria, dry run, no adapter, unparseable answer — because Python's
// comment says so: "don't get stuck in validation loops". A port that
// failed closed on an adapter error would turn a transient outage into a
// stuck mission, and every test of the happy path would still be green.
//
// It also reads `bool(data.get("passed", True))`, which is TRUTHINESS and
// not a bool cast: the string "false" passes, and so does "0".

// pyValidateSnippet drives CPython's _validate_milestone with a stub
// adapter whose reply is argv[2], and reports both the verdict and the
// prompt it was handed.
const pyValidateSnippet = `
import json, sys, mission
from dataclasses import dataclass

@dataclass
class Resp:
    content: str

class Stub:
    def complete(self, messages, **kw):
        self.messages, self.kw = messages, kw
        if reply == '<<error>>':
            raise RuntimeError('adapter down')
        return Resp(reply)

spec = json.loads(sys.argv[1])
reply = sys.argv[2]

feats = []
for f in spec['features']:
    feats.append(mission.Feature(id='f', title=f['title'], status=f['status'],
                                 result_summary=f.get('summary')))
msn = mission.Milestone(id='m', title=spec['title'], features=feats,
                        validation_criteria=spec['criteria'], status='validating')

stub = None if spec['no_adapter'] else Stub()
passed = mission._validate_milestone(msn, 'proj', stub, dry_run=spec['dry_run'])
out = {'passed': passed, 'called': stub is not None and hasattr(stub, 'messages')}
if out['called']:
    out['system'] = stub.messages[0].content
    out['user'] = stub.messages[1].content
    out['kwargs'] = stub.kw
print(json.dumps(out))
`

type validateSpec struct {
	Title    string   `json:"title"`
	Criteria []string `json:"criteria"`
	Features []struct {
		Title   string  `json:"title"`
		Status  string  `json:"status"`
		Summary *string `json:"summary"`
	} `json:"features"`
	DryRun    bool `json:"dry_run"`
	NoAdapter bool `json:"no_adapter"`
}

type validateResult struct {
	Passed bool           `json:"passed"`
	Called bool           `json:"called"`
	System string         `json:"system"`
	User   string         `json:"user"`
	Kwargs map[string]any `json:"kwargs"`
}

func pyValidate(t *testing.T, spec validateSpec, reply string) validateResult {
	t.Helper()
	b, err := json.Marshal(spec)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyValidateSnippet, string(b), reply)
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
	var r validateResult
	if err := json.Unmarshal(out, &r); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}
	return r
}

type validateCase struct {
	name  string
	spec  validateSpec
	reply string
}

// validateCorpus is hoisted out of the test so the anti-vacuity guard
// below can READ it. It used to be an inline literal and the guard drove
// its own private five-reply list, so it could not notice the corpus
// losing its only false-verdict case — the single thing it exists to
// catch (adversarial mission-r1 HIGH).
func validateCorpus() []validateCase {
	sum := func(s string) *string { return &s }
	type feat = struct {
		Title   string  `json:"title"`
		Status  string  `json:"status"`
		Summary *string `json:"summary"`
	}

	base := validateSpec{
		Title:    "First milestone",
		Criteria: []string{"it works", "it is fast"},
		Features: []feat{
			{Title: "Feature one", Status: "done", Summary: sum("all good")},
			{Title: "Feature two", Status: "blocked"},
			{Title: "Feature three", Status: "done", Summary: sum("")},
		},
	}

	return []validateCase{
		{"an explicit pass", base, `{"passed": true, "reason": "ok"}`},
		{"an explicit fail", base, `{"passed": false, "reason": "no"}`},

		// bool(), not a cast.
		{"the STRING false is truthy and PASSES", base, `{"passed": "false"}`},
		{"the string 0 is truthy and passes", base, `{"passed": "0"}`},
		{"the empty string is falsy", base, `{"passed": ""}`},
		{"null is falsy", base, `{"passed": null}`},
		{"zero is falsy", base, `{"passed": 0}`},
		{"0.0 is falsy", base, `{"passed": 0.0}`},
		{"an empty list is falsy", base, `{"passed": []}`},
		{"a non-empty list is truthy", base, `{"passed": [0]}`},
		{"an empty dict is falsy", base, `{"passed": {}}`},
		{"a non-empty dict is truthy", base, `{"passed": {"a":1}}`},

		// The non-finite family, which Go's decoder rejects outright and
		// CPython's accepts. Rejection defaults to PASS at the
		// unparseable exit, so `NaN` alone cannot discriminate — the
		// zero and the string cases below are what separate "parsed and
		// truthy" from "did not parse".
		{"NaN is truthy and passes", base, `{"passed": NaN}`},
		{"Infinity is truthy and passes", base, `{"passed": Infinity}`},
		{"-Infinity is truthy and passes", base, `{"passed": -Infinity}`},
		{"an overflowing literal is truthy and passes", base,
			`{"passed": 1e309}`},
		{"a document with NaN ELSEWHERE still reads passed", base,
			`{"passed": false, "score": NaN}`},
		{"a document with Infinity elsewhere still reads passed", base,
			`{"passed": 0, "score": [Infinity]}`},
		{"the STRING NaN is not a float", base, `{"passed": "NaN"}`},

		// The four default-to-pass exits.
		{"an absent passed key defaults to true", base, `{"reason": "shrug"}`},
		{"an empty object defaults to true", base, `{}`},
		{"unparseable content defaults to true", base, `not json at all`},
		{"prose around the JSON is still carved out", base,
			"Here you go:\n{\"passed\": false}\nhope that helps"},
		{"an adapter that RAISES defaults to true", base, "<<error>>"},

		// The two short-circuits, which never call the model at all.
		{"no criteria short-circuits before the adapter", validateSpec{
			Title: "T", Criteria: nil,
			Features: []feat{{Title: "f", Status: "done"}}},
			`{"passed": false}`},
		{"an empty criteria list short-circuits", validateSpec{
			Title: "T", Criteria: []string{},
			Features: []feat{{Title: "f", Status: "done"}}},
			`{"passed": false}`},
		{"dry_run short-circuits", validateSpec{
			Title: "T", Criteria: []string{"c"}, DryRun: true,
			Features: []feat{{Title: "f", Status: "done"}}},
			`{"passed": false}`},
		{"a nil adapter short-circuits", validateSpec{
			Title: "T", Criteria: []string{"c"}, NoAdapter: true,
			Features: []feat{{Title: "f", Status: "done"}}},
			`{"passed": false}`},

		// The prompt's own shape.
		{"a feature with no summary omits the em-dash clause", validateSpec{
			Title: "T", Criteria: []string{"c"},
			Features: []feat{{Title: "f", Status: "done"}}},
			`{"passed": true}`},
		{"a long summary is clipped to 200 CODE POINTS", validateSpec{
			Title: "T", Criteria: []string{"c"},
			Features: []feat{{Title: "f", Status: "done",
				Summary: sum(strings.Repeat("日", 260))}}},
			`{"passed": true}`},
		{"no features at all", validateSpec{
			Title: "T", Criteria: []string{"c"}, Features: []feat{}},
			`{"passed": true}`},
	}
}

func TestValidateMilestoneMatchesCPython(t *testing.T) {
	for _, tc := range validateCorpus() {
		t.Run(tc.name, func(t *testing.T) {
			want := pyValidate(t, tc.spec, tc.reply)

			ms := &Milestone{Title: tc.spec.Title,
				ValidationCriteria: tc.spec.Criteria, Status: "validating"}
			for _, f := range tc.spec.Features {
				ms.Features = append(ms.Features, Feature{
					Title: f.Title, Status: f.Status, ResultSummary: f.Summary})
			}

			rec := &recordingAdapter{reply: tc.reply,
				fail: tc.reply == "<<error>>"}
			var got bool
			if tc.spec.NoAdapter {
				got = ValidateMilestone(context.Background(), ms, "proj", nil,
					tc.spec.DryRun)
			} else {
				got = ValidateMilestone(context.Background(), ms, "proj", rec,
					tc.spec.DryRun)
			}

			if got != want.Passed {
				t.Errorf("verdict: go %v, py %v", got, want.Passed)
			}
			called := rec.msgs != nil
			if !tc.spec.NoAdapter && called != want.Called {
				t.Errorf("the model was called: go %v, py %v", called, want.Called)
			}
			if !want.Called {
				return
			}
			if rec.msgs[0].Content != want.System {
				t.Errorf("system prompt differs\n go %q\n py %q",
					rec.msgs[0].Content, want.System)
			}
			if rec.msgs[1].Content != want.User {
				t.Errorf("user prompt differs\n go %q\n py %q",
					rec.msgs[1].Content, want.User)
			}
			if rec.opts.MaxTokens != int(want.Kwargs["max_tokens"].(float64)) {
				t.Errorf("max_tokens: go %d, py %v",
					rec.opts.MaxTokens, want.Kwargs["max_tokens"])
			}
			if rec.opts.Temperature != want.Kwargs["temperature"].(float64) {
				t.Errorf("temperature: go %v, py %v",
					rec.opts.Temperature, want.Kwargs["temperature"])
			}
			if rec.opts.Purpose != want.Kwargs["purpose"].(string) {
				t.Errorf("purpose: go %q, py %v",
					rec.opts.Purpose, want.Kwargs["purpose"])
			}
			if len(rec.opts.Tools) != 0 || rec.opts.AgentTools {
				t.Errorf("the validation call is not in the utility lane")
			}
		})
	}
}

// Every case above asserts agreement, and agreement on `true` is the
// default at four separate exits — so a corpus that never produced a
// FALSE verdict would pass against a ValidateMilestone that is `return
// true`.
func TestTheValidationCorpusProducesBothVerdicts(t *testing.T) {
	// The REAL corpus, not a private reply list. The private list could
	// not see the corpus losing its false-verdict cases, which is the only
	// failure this guard exists to catch.
	passes, fails := 0, 0
	for _, tc := range validateCorpus() {
		if pyValidate(t, tc.spec, tc.reply).Passed {
			passes++
		} else {
			fails++
		}
	}
	if passes == 0 || fails == 0 {
		t.Fatalf("CPython reached only one verdict (pass=%d fail=%d); the "+
			"whole corpus would pass against `return true`", passes, fails)
	}
}
