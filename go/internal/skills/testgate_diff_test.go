package skills

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyTestGateSrc drives the Phase-14 gate with a scripted stub adapter and
// reports the answer, every prompt the adapter saw, and the store.
//
// The PROMPTS are reported because they are half the behaviour: the [:5] and
// [:200]/[:300] slices, the f-string joins and the exact system text are only
// observable there, and a port that returned identical results while asking a
// different question would pass a results-only comparison.
//
// `adapter=None` is spelled as an EMPTY script rather than a separate verb,
// because "no adapter" is a distinct code path in all three functions and
// conflating it with "an adapter that fails" is how the fail-open behaviour
// would go untested.
const pyTestGateSrc = `
import json, sys
import skills
from skill_types import dict_to_skill, SkillTestCase

_argv = json.loads(sys.argv[1])
_seen = []

class _Resp:
    def __init__(self, content):
        self.content = content

class _Stub:
    def __init__(self, script):
        self.script = script
        self.calls = 0
    def complete(self, messages, **kw):
        _seen.append({"system": messages[0].content,
                      "user": messages[1].content,
                      "kw": {k: v for k, v in sorted(kw.items())},
                      "n_messages": len(messages)})
        if self.script and self.script[0] == "__RAISE__":
            raise RuntimeError("adapter down")
        i = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return _Resp(self.script[i])

adapter = _Stub(_argv["script"]) if _argv["script"] else None
skill = dict_to_skill(_argv["skill"])

def _tc(t):
    return {"skill_id": t.skill_id, "input_description": t.input_description,
            "expected_keywords": t.expected_keywords,
            "derived_from_failure": t.derived_from_failure}

try:
    verb = _argv["verb"]
    if verb == "generate":
        out = skills.generate_skill_tests(
            skill, _argv["failures"], adapter=adapter)
        res = {"ok": True, "tests": [_tc(t) for t in out]}
    elif verb == "run":
        tests = [SkillTestCase.from_dict(d) for d in _argv["tests"]]
        passed, total = skills.run_skill_tests(
            skill, tests, adapter=adapter, dry_run=_argv["dry_run"])
        res = {"ok": True, "passed": passed, "total": total}
    elif verb == "load":
        # The WARNINGS are the point of half this verb's fixtures: a torn
        # line and a drifted row are both "a shorter list than the store",
        # and the operator's only signal that they differ is the sentence.
        # Captured at the root logger so both jsonl_utils' announcement and
        # skills' own drift warning are seen.
        import logging
        _log_lines = []
        class _Cap(logging.Handler):
            def emit(self, rec):
                # getMessage() already applies rec.args; applying them a
                # second time raises TypeError inside the handler and the
                # probe dies reporting a differential failure for a defect
                # in its own instrumentation.
                _log_lines.append(rec.getMessage())
        _h = _Cap()
        logging.getLogger().addHandler(_h)
        logging.getLogger().setLevel(logging.WARNING)
        try:
            out = skills._load_skill_tests(_argv["skill_id"])
        finally:
            logging.getLogger().removeHandler(_h)
        res = {"ok": True, "tests": [_tc(t) for t in out],
               "warnings": _log_lines}
    else:
        mutated = dict_to_skill(_argv["mutated"])
        r = skills.validate_skill_mutation(skill, mutated, adapter=adapter)
        res = {"ok": True, "tests_run": r.tests_run,
               "tests_passed": r.tests_passed, "blocked": r.blocked,
               "block_reason": r.block_reason, "skill_id": r.skill_id}
except BaseException as e:
    res = {"ok": False, "cls": type(e).__name__, "msg": str(e)}

res["seen"] = _seen
p = skills._skill_tests_path()
# errors="replace", not a strict read: a fixture that SEEDS a byte-tainted
# line (which is the point of several of them) would otherwise kill the
# probe on the way out, reporting a differential failure for a defect in
# the reporting rather than in either runtime. The comparison that uses
# this field is only run by verbs that write the store themselves, and
# those never write an undecodable byte.
res["saved"] = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
print(json.dumps(res, sort_keys=True))
`

// seedSkillTests writes rows into the store both runtimes will read.
func seedSkillTests(t *testing.T, ws string, rows []map[string]any) {
	t.Helper()
	if rows == nil {
		return
	}
	var tests []SkillTestCase
	for _, r := range rows {
		tc, err := SkillTestCaseFromDict(r)
		if err != nil {
			t.Fatalf("seed row %v is not a SkillTestCase: %v", r, err)
		}
		tests = append(tests, tc)
	}
	if err := SaveSkillTests(ws, tests); err != nil {
		t.Fatal(err)
	}
}

func goSkillOf(t *testing.T, d map[string]any) Skill {
	t.Helper()
	s, err := DictToSkill(d)
	if err != nil {
		t.Fatalf("DictToSkill(%v): %v", d, err)
	}
	return s
}

// TestGenerateSkillTestsMatchesCPython pins the generator, both paths.
func TestGenerateSkillTestsMatchesCPython(t *testing.T) {
	skill := func(name string, over map[string]any) map[string]any {
		d := map[string]any{"id": "s1", "name": name,
			"description": "does a thing", "steps_template": []any{"first step", "second"},
			"trigger_patterns": []any{"trigger one"},
			"created_at":       "2026-01-01T00:00:00+00:00"}
		for k, v := range over {
			d[k] = v
		}
		return d
	}
	goodReply := `[{"input_description": "  do the thing  ",
	  "expected_keywords": ["  Alpha ", "", "beta"]},
	 {"input_description": "another", "expected_keywords": ["k"]}]`

	for _, c := range []struct {
		name     string
		skill    map[string]any
		failures []string
		script   []string
	}{
		{"the LLM path", skill("Fetch Pages", nil),
			[]string{"it timed out", "it failed again"}, []string{goodReply}},

		// More than three items, and more than five failures/steps — the
		// slices in the prompt and in the result are different numbers and
		// a port could get one right and the other wrong.
		{"only the first three test cases survive", skill("N", nil),
			[]string{"f1", "f2", "f3", "f4", "f5", "f6", "f7"},
			[]string{`[{"input_description":"a","expected_keywords":["k"]},
			  {"input_description":"b","expected_keywords":["k"]},
			  {"input_description":"c","expected_keywords":["k"]},
			  {"input_description":"d","expected_keywords":["k"]}]`}},
		{"six steps clip to five in the prompt",
			skill("N", map[string]any{"steps_template": []any{
				"s1", "s2", "s3", "s4", "s5", "s6"}}),
			nil, []string{goodReply}},

		// An item with no input_description, or no keywords after the
		// strip, is DROPPED — and if every item drops, `if tests:` is
		// false and the heuristic fallback runs instead. That fall-through
		// is the case a results-only port would get wrong by returning an
		// empty list.
		{"an item with an empty description is dropped", skill("N", nil), nil,
			[]string{`[{"input_description":"   ","expected_keywords":["k"]},
			  {"input_description":"kept","expected_keywords":["k"]}]`}},
		{"every item dropped falls through to the heuristic", skill("N", nil),
			nil, []string{`[{"input_description":"","expected_keywords":["k"]}]`}},
		{"a non-object item is skipped, not fatal", skill("N", nil), nil,
			[]string{`["a string", {"input_description":"kept",
			  "expected_keywords":["k"]}]`}},

		// expected_keywords iterating as something other than a list.
		// A STRING yields its characters; a NUMBER is not iterable at all
		// and the TypeError abandons the whole LLM path.
		{"string keywords iterate as characters", skill("N", nil), nil,
			[]string{`[{"input_description":"d","expected_keywords":"abc"}]`}},
		{"non-iterable keywords abandon the LLM path", skill("N", nil), nil,
			[]string{`[{"input_description":"d","expected_keywords":7}]`}},

		// extract_json's two fallbacks, which StringArray does not have.
		{"an object reply unwraps through a wrapper key", skill("N", nil), nil,
			[]string{`{"items": [{"input_description":"d",
			  "expected_keywords":["k"]}]}`}},
		{"an object reply with no wrapper key falls through", skill("N", nil),
			nil, []string{`{"other": [{"input_description":"d",
			  "expected_keywords":["k"]}]}`}},
		{"a fenced reply is carved", skill("N", nil), nil,
			[]string{"```json\n" + goodReply + "\n```"}},
		{"prose instead of JSON falls through", skill("N", nil), nil,
			[]string{"no json here"}},

		// THE SEPARATORS, on this function's own two strips. `str(...)
		// .strip()` runs on input_description and on every keyword, and
		// both results are STORED — a keyword is later matched against
		// model output, so an unstripped one silently never matches.
		{"separators strip out of an input description", skill("N", nil), nil,
			[]string{`[{"input_description": "` + fsep + `do it` + usep + `",
			  "expected_keywords": ["k"]}]`}},
		{"separators strip out of a keyword", skill("N", nil), nil,
			[]string{`[{"input_description": "d",
			  "expected_keywords": ["` + gsep + `alpha` + rsep + `", "` + fsep + `"]}]`}},
		// A description that is only separators strips to "" and the item
		// is DROPPED, which sends the whole call to the heuristic — the
		// same truthiness-gates-a-write shape as round 10's H1.
		{"a separator-only description drops the item", skill("N", nil), nil,
			[]string{`[{"input_description": "` + fsep + gsep + `",
			  "expected_keywords": ["k"]}]`}},

		// The [:300] description clip, visible ONLY in the prompt. é so
		// the cut is by code point rather than by byte.
		{"a long description clips at three hundred in the prompt",
			skill("N", map[string]any{
				"description": repeatRunes("é", 400)}),
			nil, []string{goodReply}},

		// extract_json's ALT-BRACKET path, which needs a reply with NO
		// balanced [...] but a parseable {...}. An unbalanced "[" inside a
		// prose value does it — and this is the ONLY shape that reaches
		// the dict-unwrap below, because any reply whose object literally
		// contains a list also contains a balanced [...] that the first
		// carve finds directly.
		{"an unbalanced bracket in prose forces the alt-bracket path",
			skill("N", nil), nil,
			[]string{`{"note": "use x[0 to index",
			  "items": [{"input_description":"d","expected_keywords":["k"]}]}`}},
		// Two wrapper keys: "results" comes before "suggestions" in
		// Python's tuple, so the ORDER decides which list is returned.
		{"the first wrapper key in Python's order wins",
			skill("N", nil), nil,
			[]string{`{"note": "x[0",
			  "suggestions": [{"input_description":"from-suggestions",
			    "expected_keywords":["k"]}],
			  "results": [{"input_description":"from-results",
			    "expected_keywords":["k"]}]}`}},
		// A wrapper key whose value is NOT a list does not stop the walk.
		{"a non-list wrapper key is skipped, not fatal",
			skill("N", nil), nil,
			[]string{`{"note": "x[0", "items": "not a list",
			  "results": [{"input_description":"d","expected_keywords":["k"]}]}`}},

		// The adapter itself failing — Python's except, then the heuristic.
		{"an adapter that raises falls through", skill("N", nil),
			[]string{"the reason"}, []string{"__RAISE__"}},

		// THE HEURISTIC PATH, which is not inside the try.
		{"no adapter at all", skill("Fetch Pages", nil),
			[]string{"a failure reason"}, nil},
		{"no trigger patterns", skill("Fetch Pages",
			map[string]any{"trigger_patterns": []any{}}), nil, nil},
		{"no steps template", skill("Fetch Pages",
			map[string]any{"steps_template": []any{}}), nil, nil},
		{"no failures at all", skill("Fetch Pages", nil), nil, nil},
		// An EMPTY name is falsy, so both `if skill.name` guards take their
		// defaults — and the two defaults DIFFER ("result" and "skill"),
		// which is the only case that can tell them apart.
		{"an empty name takes two different defaults",
			skill("", nil), nil, nil},
		// A name that is truthy but splits to nothing: `"  ".split()` is
		// [] and `[0]` is an IndexError that leaves the function. The
		// separator spelling is the one round 10 made reachable.
		{"a whitespace name raises IndexError", skill("   ", nil), nil, nil},
		{"a separator-only name raises IndexError",
			skill("\x1c\x1f", nil), nil, nil},
		// The same shape one level down: the LIST is truthy, its first
		// element is not.
		{"a whitespace first step raises IndexError",
			skill("N", map[string]any{"steps_template": []any{"  ", "s2"}}),
			nil, nil},
		// A long failure reason: the hint is [:100] and derived_from_failure
		// on the LLM path is [:200] — two different cuts of one string.
		// This case has NO script, so it exercises the heuristic [:100]
		// only; the LLM-path [:200]s need their own case below, which the
		// comment used to claim this one covered.
		{"a long failure reason cuts at one hundred", skill("N", nil),
			[]string{repeatRunes("é", 300)}, nil},
		// The two [:200] cuts on the LLM path, at their own boundary: the
		// prompt joins `e[:200]` per failure and derived_from_failure is
		// `failure_examples[0][:200]`. Every other scripted case here has
		// short failures, so before this fixture either constant could be
		// changed to 100 or 300 with the battery still green — a limit
		// with no case at its own boundary is a limit nothing pins.
		// Multi-byte on purpose: a port slicing BYTES rather than runes
		// cuts é in half and the prompt differs at the seam.
		{"the LLM path cuts failures at two hundred", skill("N", nil),
			[]string{repeatRunes("é", 300), repeatRunes("ü", 250)},
			[]string{goodReply}},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			script := c.script
			if script == nil {
				script = []string{}
			}
			// failures NORMALIZED to a list, never nil. A nil Go slice
			// marshals as JSON null, Python then slices None at
			// `failure_examples[:5]`, and the TypeError lands in the try —
			// so the probe took the heuristic path having made zero adapter
			// calls, and eleven cases "disagreed" about a shape production
			// cannot produce (validate_skill_mutation always passes a
			// list). The fixture was wrong, not the port.
			failures := c.failures
			if failures == nil {
				failures = []string{}
			}
			arg := map[string]any{"verb": "generate", "skill": c.skill,
				"failures": failures, "script": script}
			var want testGateWant
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyTestGateSrc, &want, pyprobe.Arg(t, arg))

			var adapter llm.Adapter
			var fake *llm.Fake
			if len(script) > 0 {
				fake = &llm.Fake{Script: script}
				if script[0] == "__RAISE__" {
					fake = &llm.Fake{Script: nil} // empty script => error
				}
				adapter = fake
			}
			got, gotErr := GenerateSkillTests(context.Background(), goWS,
				goSkillOf(t, c.skill), failures, adapter)

			want.assertRaise(t, gotErr)
			if !want.OK {
				return
			}
			cmpTestCases(t, got, want.Tests)
			cmpSavedTests(t, goWS, want.Saved)
			// The CALL COUNT first, then the prompts. Without the count
			// a port that never called the adapter at all would satisfy
			// "prompt 0 matches" vacuously — there would be no prompt 0 to
			// compare and the guarded branch would simply not run.
			calls := 0
			if fake != nil {
				calls = len(fake.Msgs)
			}
			if calls != len(want.Seen) {
				t.Fatalf("the port made %d adapter call(s); CPython made %d",
					calls, len(want.Seen))
			}
			if calls > 0 {
				want.assertPrompt(t, fake.Msgs, fake.Opts, 0)
			}
		})
	}
}

// TestRunSkillTestsMatchesCPython pins the smoke-test runner.
func TestRunSkillTestsMatchesCPython(t *testing.T) {
	skill := map[string]any{"id": "s1", "name": "Fetch",
		"description": "does a thing", "steps_template": []any{"a", "b"},
		"created_at": "2026-01-01T00:00:00+00:00"}
	tc := func(desc string, kws ...any) map[string]any {
		return map[string]any{"skill_id": "s1", "input_description": desc,
			"expected_keywords": kws, "derived_from_failure": ""}
	}

	for _, c := range []struct {
		name   string
		tests  []map[string]any
		script []string
		dryRun bool
		// desc overrides the skill's description for the one case that is
		// about the [:200] clip. Every other case wants the short one, so
		// that the prompt comparison fails on a real difference rather
		// than on four hundred identical characters.
		desc string
	}{
		{"no tests at all", nil, []string{"anything"}, false, ""},
		{"no adapter passes everything",
			rows(tc("a", "x"), tc("b", "y")), nil, false, ""},
		{"dry run passes everything",
			rows(tc("a", "x")), []string{"nothing matches"}, true, ""},

		{"a keyword found", rows(tc("a", "alpha")),
			[]string{"the ALPHA result"}, false, ""},
		{"a keyword missed", rows(tc("a", "alpha")),
			[]string{"nothing here"}, false, ""},
		// The match is case-insensitive on BOTH sides, and it is a
		// SUBSTRING test rather than a word test.
		{"the keyword is lowered too", rows(tc("a", "ALPHA")),
			[]string{"the alpha result"}, false, ""},
		{"a substring counts", rows(tc("a", "lph")),
			[]string{"ALPHABET"}, false, ""},
		// The [:200] description clip in the smoke-test system prompt —
		// a DIFFERENT number from the generator's [:300], and invisible
		// in (passed, total).
		{"a long description clips at two hundred in the prompt",
			rows(tc("a", "hit")), []string{"hit"}, false,
			repeatRunes("é", 400)},
		// any() — one hit out of several is enough, and it stops there.
		{"any keyword is enough", rows(tc("a", "nope", "alpha")),
			[]string{"alpha"}, false, ""},
		// TWO matching keywords in ONE test. any() stops at the first, so
		// the test contributes exactly 1 — a runner that kept counting
		// would report passed=2 out of total=1, which is not merely wrong
		// but impossible, and every other fixture here has at most one hit.
		{"two matching keywords still count once",
			rows(tc("a", "alpha", "bet")), []string{"alphabet"}, false, ""},
		{"no keywords can never pass", rows(tc("a")),
			[]string{"anything"}, false, ""},
		// U+0130 decides a match, but ONLY in one of its two spellings —
		// and the obvious one is the vacuous one. Measured on this box:
		//
		//   kw "İstanbul": CPython matches, strings.ToLower ALSO matches
		//                  ("istanbul" in "visit istanbul today").
		//   kw "i̇stanbul": CPython matches, strings.ToLower does NOT
		//                  ("i̇stanbul" is not in "visit istanbul today").
		//
		// The first row is the one that reads like the interesting case
		// and cannot fail; it is kept as its companion's control, labelled
		// for what it is, because a reader who deletes the second and
		// keeps the first has silently removed the coverage (lens 18).
		{"a dotted capital I in both — agrees either way (control)",
			rows(tc("a", "İstanbul")),
			[]string{"visit İSTANBUL today"}, false, ""},
		{"a PRE-LOWERED dotted i decides the match",
			rows(tc("a", "i̇stanbul")),
			[]string{"visit İSTANBUL today"}, false, ""},
		// A STORED OBJECT. `for kw in {"alpha": 1}` yields the KEYS, so
		// CPython runs the test and passes it. The port raised TypeError
		// and skipped the test instead — the row still counted toward
		// `total`, so the short `passed` blocked a mutation CPython lets
		// through (measured: CPython (1,1), the port (0,1)).
		//
		// It reaches the run gate as a map[string]any and never as a
		// pyval.Obj, because record.LoadsClean decodes through
		// encoding/json — so pyIterate's ordered-mapping arm, which looks
		// exactly like coverage for this case, is dead for the only caller
		// that needs it.
		{"an object's KEYS are the keywords",
			[]map[string]any{{"skill_id": "s1", "input_description": "a",
				"expected_keywords": map[string]any{"alpha": 1}}},
			[]string{"the alpha result"}, false, ""},
		{"an object whose keys do not match",
			[]map[string]any{{"skill_id": "s1", "input_description": "a",
				"expected_keywords": map[string]any{"alpha": 1}}},
			[]string{"nothing here"}, false, ""},
		// A stored JSON null. `for kw in None` is a TypeError in CPython
		// and the test is skipped — the interesting half is not the count
		// but the STORED value, which the load differential compares.
		{"a null keywords value is not iterable",
			[]map[string]any{{"skill_id": "s1", "input_description": "a",
				"expected_keywords": nil}},
			[]string{"anything"}, false, ""},
		// Partial pass across several tests, with the adapter raising on
		// the LAST one — Python's per-test except keeps the earlier count.
		{"two of three, the third raising",
			rows(tc("a", "hit"), tc("b", "hit"), tc("c", "hit")),
			[]string{"hit", "hit", "__RAISE__"}, false, ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			_ = goWS
			script := c.script
			if script == nil {
				script = []string{}
			}
			// Same normalization as the generator's failures: a nil slice
			// is JSON null, and `[... for d in None]` raises before
			// run_skill_tests is even entered.
			testRows := c.tests
			if testRows == nil {
				testRows = []map[string]any{}
			}
			skillRow := skill
			if c.desc != "" {
				skillRow = map[string]any{}
				for k, v := range skill {
					skillRow[k] = v
				}
				skillRow["description"] = c.desc
			}
			arg := map[string]any{"verb": "run", "skill": skillRow,
				"tests": testRows, "script": script, "dry_run": c.dryRun}
			var want testGateWant
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyTestGateSrc, &want, pyprobe.Arg(t, arg))
			want.assertRaise(t, nil)

			var adapter llm.Adapter
			var sf *scriptedFake
			if len(script) > 0 {
				sf = &scriptedFake{script: script}
				adapter = sf
			}
			var tests []SkillTestCase
			for _, r := range testRows {
				tcv, err := SkillTestCaseFromDict(r)
				if err != nil {
					t.Fatal(err)
				}
				tests = append(tests, tcv)
			}
			passed, total := RunSkillTests(context.Background(),
				goSkillOf(t, skillRow), tests, adapter, c.dryRun)
			if passed != want.Passed || total != want.Total {
				t.Errorf("(passed, total) = (%d, %d), CPython says (%d, %d)",
					passed, total, want.Passed, want.Total)
			}
			// The smoke-test PROMPT, per call. (passed, total) is a pair of
			// small integers and would agree for a port that asked an
			// entirely different question — the [:200] description clip and
			// the [:5] step join live only here.
			if sf != nil {
				if len(sf.Msgs) != len(want.Seen) {
					t.Fatalf("the port made %d adapter call(s); CPython made %d",
						len(sf.Msgs), len(want.Seen))
				}
				for i := range sf.Msgs {
					want.assertPrompt(t, sf.Msgs, sf.Opts, i)
				}
			}
		})
	}
}

// TestValidateSkillMutationMatchesCPython pins the gate's decision.
func TestValidateSkillMutationMatchesCPython(t *testing.T) {
	base := map[string]any{"id": "s1", "name": "Fetch Pages",
		"description": "does a thing", "steps_template": []any{"a"},
		"trigger_patterns": []any{"trig"},
		"created_at":       "2026-01-01T00:00:00+00:00"}
	mutated := map[string]any{"id": "s1", "name": "Fetch Pages",
		"description": "does a DIFFERENT thing", "steps_template": []any{"b"},
		"created_at": "2026-01-01T00:00:00+00:00"}
	stored := func(kws ...any) []map[string]any {
		return []map[string]any{{"skill_id": "s1",
			"input_description": "check it", "expected_keywords": kws,
			"derived_from_failure": "old failure"}}
	}

	for _, c := range []struct {
		name   string
		seed   []map[string]any
		script []string
	}{
		// No adapter is a DRY RUN: everything passes and nothing blocks,
		// even with a stored test the mutation could never satisfy.
		{"no adapter never blocks", stored("impossible"), nil},
		{"stored tests, all passing", stored("hit"), []string{"a hit"}},
		{"stored tests, one failing", stored("miss"), []string{"nothing"}},
		// Nothing stored: the gate GENERATES from the (empty) failure list
		// through the same adapter, then runs what it generated. The two
		// calls take different prompts, and the second's reply has to
		// satisfy keywords the first invented.
		{"nothing stored generates first",
			nil, []string{`[{"input_description":"d","expected_keywords":["zz"]}]`,
				"a zz reply"}},
		{"nothing stored, generated test then fails",
			nil, []string{`[{"input_description":"d","expected_keywords":["zz"]}]`,
				"no match at all"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			seedSkillTests(t, pyWS, c.seed)
			seedSkillTests(t, goWS, c.seed)
			script := c.script
			if script == nil {
				script = []string{}
			}
			arg := map[string]any{"verb": "validate", "skill": base,
				"mutated": mutated, "script": script}
			var want testGateWant
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyTestGateSrc, &want, pyprobe.Arg(t, arg))

			var adapter llm.Adapter
			if len(script) > 0 {
				adapter = &scriptedFake{script: script}
			}
			got, gotErr := ValidateSkillMutation(context.Background(), goWS,
				goSkillOf(t, base), goSkillOf(t, mutated), nil, adapter)
			want.assertRaise(t, gotErr)
			if !want.OK {
				return
			}
			if got.TestsRun != want.TestsRun || got.TestsPassed != want.TestsPassed {
				t.Errorf("(run, passed) = (%d, %d), CPython says (%d, %d)",
					got.TestsRun, got.TestsPassed, want.TestsRun, want.TestsPassed)
			}
			if got.Blocked != want.Blocked {
				t.Errorf("blocked = %v, CPython says %v", got.Blocked, want.Blocked)
			}
			// The SENTENCE, not just the flag: this is stored prose an
			// operator reads, and the content-key family is this port's
			// most recurrent bug.
			if got.BlockReason != want.BlockReason {
				t.Errorf("block_reason = %q, CPython says %q",
					got.BlockReason, want.BlockReason)
			}
			if got.SkillID != want.SkillID {
				t.Errorf("skill_id = %q, CPython says %q", got.SkillID, want.SkillID)
			}
			cmpSavedTests(t, goWS, want.Saved)
		})
	}
}

type testGateWant struct {
	OK          bool              `json:"ok"`
	Cls         string            `json:"cls"`
	Msg         string            `json:"msg"`
	Tests       []json.RawMessage `json:"tests"`
	Passed      int               `json:"passed"`
	Total       int               `json:"total"`
	TestsRun    int               `json:"tests_run"`
	TestsPassed int               `json:"tests_passed"`
	Blocked     bool              `json:"blocked"`
	BlockReason string            `json:"block_reason"`
	SkillID     string            `json:"skill_id"`
	Saved       string            `json:"saved"`
	Warnings    []string          `json:"warnings"`
	Seen        []struct {
		System    string         `json:"system"`
		User      string         `json:"user"`
		KW        map[string]any `json:"kw"`
		NMessages int            `json:"n_messages"`
	} `json:"seen"`
}

func (w testGateWant) assertRaise(t *testing.T, gotErr error) {
	t.Helper()
	if w.OK != (gotErr == nil) {
		if gotErr != nil {
			t.Fatalf("the port raised %v; CPython returned normally", gotErr)
		}
		t.Fatalf("the port returned normally; CPython raises %s: %s", w.Cls, w.Msg)
	}
	if !w.OK {
		if cls := pyval.ClassOf(gotErr); cls != w.Cls {
			t.Errorf("raises %s, CPython raises %s (%s)", cls, w.Cls, w.Msg)
		}
	}
}

// assertPrompt compares one call's messages against what CPython's stub saw.
func (w testGateWant) assertPrompt(t *testing.T, allMsgs [][]llm.Message,
	allOpts []llm.Options, i int) {
	t.Helper()
	if i >= len(w.Seen) || i >= len(allMsgs) {
		t.Fatalf("the port made %d adapter call(s); CPython made %d",
			len(allMsgs), len(w.Seen))
	}
	msgs := allMsgs[i]
	if len(msgs) != w.Seen[i].NMessages {
		t.Fatalf("call %d sent %d messages, CPython sent %d",
			i, len(msgs), w.Seen[i].NMessages)
	}
	if msgs[0].Content != w.Seen[i].System {
		t.Errorf("call %d system prompt differs\n go: %q\n py: %q",
			i, msgs[0].Content, w.Seen[i].System)
	}
	if msgs[1].Content != w.Seen[i].User {
		t.Errorf("call %d user prompt differs\n go: %q\n py: %q",
			i, msgs[1].Content, w.Seen[i].User)
	}
	opts := allOpts[i]
	if got, want := float64(opts.MaxTokens), w.Seen[i].KW["max_tokens"]; !sameNum(got, want) {
		t.Errorf("call %d max_tokens = %v, CPython was passed %v", i, got, want)
	}
	if got, want := opts.Temperature, w.Seen[i].KW["temperature"]; !sameNum(got, want) {
		t.Errorf("call %d temperature = %v, CPython was passed %v", i, got, want)
	}
	if got, want := opts.Purpose, w.Seen[i].KW["purpose"]; got != want {
		t.Errorf("call %d purpose = %q, CPython was passed %v", i, got, want)
	}
	// no_tools=True is the SAFETY property of this gate: a keyword smoke
	// test that ran with live tools would let the evolver's own brake take
	// actions on the text it was asked to classify. CPython passes the
	// kwarg; the Go side means the same thing by leaving AgentTools off
	// and injecting no tool protocol, so both halves are asserted — the
	// kwarg's presence there, its equivalent here. Neither alone is the
	// claim.
	if w.Seen[i].KW["no_tools"] != true {
		t.Errorf("call %d: CPython was passed no_tools=%v, not true",
			i, w.Seen[i].KW["no_tools"])
	}
	if opts.AgentTools || len(opts.Tools) > 0 {
		t.Errorf("call %d: the port asked for tools (AgentTools=%v, %d tools) "+
			"where Python passes no_tools=True", i, opts.AgentTools, len(opts.Tools))
	}
}

func sameNum(got float64, want any) bool {
	f, ok := want.(float64)
	return ok && f == got
}

func cmpTestCases(t *testing.T, got []SkillTestCase, want []json.RawMessage) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("returned %d test case(s), CPython returned %d\n go: %+v",
			len(got), len(want), got)
	}
	for i := range got {
		gb, err := json.Marshal(got[i])
		if err != nil {
			t.Fatal(err)
		}
		var g, w any
		if err := json.Unmarshal(gb, &g); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(want[i], &w); err != nil {
			t.Fatal(err)
		}
		renderNamedDivergence(t, w)
		gn, _ := json.Marshal(g)
		wn, _ := json.Marshal(w)
		if string(gn) != string(wn) {
			t.Errorf("test case %d\n go: %s\n py: %s", i, gn, wn)
		}
	}
}

// renderNamedDivergence rewrites the ONE difference this port cannot avoid,
// so the comparison asserts it rather than excusing it.
//
// from_dict does not validate, so a stored input_description or
// derived_from_failure can be any JSON value; CPython holds the object,
// and a Go struct field cannot. The port renders it through pyval.Str and
// says so at the constructor. Applying str() to CPython's answer HERE — and
// only to those two fields, and only when the value is not already a string
// — means the port still has to produce exactly Python's rendering: a wrong
// spelling (Go's "%v" on a bool is "true", Python's str() is "True") fails
// the same as a wrong value would. It is a narrowing of the assertion, not
// a removal of it.
func renderNamedDivergence(t *testing.T, w any) {
	t.Helper()
	m, ok := w.(map[string]any)
	if !ok {
		return
	}
	for _, key := range []string{"input_description", "derived_from_failure"} {
		v, present := m[key]
		if !present {
			continue
		}
		if _, isStr := v.(string); isStr {
			continue
		}
		// The probe's JSON turned every number into a float64; pyval.Str
		// of 7.0 is "7.0" where Python's str(7) is "7". Re-read the
		// literal so the rendering is of the value CPython actually held.
		raw, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		lit, err := pyval.LoadsOrdered(string(raw))
		if err != nil {
			t.Fatal(err)
		}
		m[key] = pyval.Str(lit)
	}
}

// cmpSavedTests compares the appended store BYTE FOR BYTE.
//
// It used to route through normJSONL, which json.Unmarshals each row and
// re-Marshals it (Go sorts map keys) and then sorts the rows. That threw
// away all three properties the writer exists to get right: to_dict's KEY
// ORDER, json.dumps' default `", "` / `": "` separators — which is the only
// reason pyval.DumpsCompactPy exists rather than a compact encoder — and
// the append ORDER. All three were correct; none was pinned, and
// byte-identical stores are this port's stated contract. Normalization is
// right for a store whose row order is genuinely unspecified; this one is
// an append log.
func cmpSavedTests(t *testing.T, goWS, want string) {
	t.Helper()
	got := readFileIfAny(t, skillTestsPath(goWS))
	if got != want {
		t.Errorf("skill-tests.jsonl differs byte for byte.\n go: %q\n py: %q",
			got, want)
	}
}

// scriptedFake is llm.Fake plus a per-call raise, which llm.Fake cannot
// express: its empty-script error fires on EVERY call, and the runner's
// per-test except is only interesting when an earlier call succeeded.
type scriptedFake struct {
	script []string
	calls  int
	// Msgs and Opts mirror llm.Fake's, because the Run test needs the
	// PROMPT comparison as much as the generator does: the smoke-test
	// system message carries a [:200] description clip and a [:5] step
	// join, and neither is visible in (passed, total).
	Msgs [][]llm.Message
	Opts []llm.Options
}

func (s *scriptedFake) Name() string { return "scripted" }

func (s *scriptedFake) Complete(_ context.Context, msgs []llm.Message,
	opts llm.Options) (*llm.Response, error) {
	s.Msgs = append(s.Msgs, msgs)
	s.Opts = append(s.Opts, opts)
	i := s.calls
	if i >= len(s.script) {
		i = len(s.script) - 1
	}
	s.calls++
	if s.script[i] == "__RAISE__" {
		return nil, errAdapterDown
	}
	return &llm.Response{Content: s.script[i]}, nil
}

// rows exists only so a table row reads at the call site; a bare
// []map[string]any{...} literal per case buried the case name under two
// lines of punctuation.
func rows(rs ...map[string]any) []map[string]any { return rs }

var errAdapterDown = errors.New("adapter down")

// repeatRunes is strings.Repeat, named for what the fixture is about: the
// cut is by CODE POINT, so a multi-byte rune is what tells a rune slice
// from a byte slice.
func repeatRunes(s string, n int) string { return strings.Repeat(s, n) }

// readFileIfAny returns a file's contents, or "" when it does not exist —
// the Go side of `p.read_text() if p.exists() else ""`.
func readFileIfAny(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return ""
		}
		t.Fatal(err)
	}
	return string(b)
}

// TestLoadSkillTestsMatchesCPython pins the reader.
//
// It is a differential rather than a unit test because the interesting
// answers are what each runtime REFUSES, and "refuses" is only meaningful
// against the other one. Python reaches this store through _read_store, so
// the admission predicate is loads_clean and the blank rule is `raw == b""`
// — the two things round 10 found hand-rolled one file over.
//
// Rows are written as RAW LINES, not through SaveSkillTests: a store written
// by the writer under test can only contain rows the writer emits, which is
// exactly the corpus that proves nothing about a reader (lens 1). Every
// interesting line here is one no writer would produce and a crash, a
// concurrent append or an older build would.
func TestLoadSkillTestsMatchesCPython(t *testing.T) {
	good := `{"skill_id": "s1", "input_description": "keep me", ` +
		`"expected_keywords": ["k"], "derived_from_failure": ""}`
	other := `{"skill_id": "s2", "input_description": "not mine", ` +
		`"expected_keywords": ["k"], "derived_from_failure": ""}`

	for _, c := range []struct {
		name  string
		lines []string
	}{
		{"a plain row", []string{good}},
		{"another skill's row is skipped", []string{good, other}},
		{"a missing store", nil},

		// FRAMING. An empty line is not a record and not a loss; a line of
		// SPACES is neither blank nor parseable, so it is a torn line —
		// counted, not silently dropped.
		{"an empty line is framing", []string{good, "", good}},
		{"a whitespace-only line is torn, not framing",
			[]string{good, "   ", good}},

		// The loads_clean refusals, each of which encoding/json admits.
		{"a byte-tainted line", []string{good,
			`{"skill_id": "s1", "input_description": "a` +
				string([]byte{0xff}) + `b", "expected_keywords": ["k"]}`}},
		{"an escaped lone surrogate", []string{good,
			`{"skill_id": "s1", "input_description": "a\udcffb", ` +
				`"expected_keywords": ["k"]}`}},
		{"a duplicate name", []string{good,
			`{"skill_id": "s1", "skill_id": "s1", "input_description": "x", ` +
				`"expected_keywords": ["k"]}`}},
		{"trailing data after the object", []string{good, good + good}},
		{"a non-object line", []string{good, `["not", "an", "object"]`}},

		// A NON-STRING FIELD. The row parses, matches the skill_id, and
		// the constructor accepts it — `from_dict` is four `d.get(k,
		// default)` calls and refuses nothing, so CPython holds the int
		// and still COUNTS the row. What these two fixtures exercise is
		// the named divergence that follows: Go renders a non-string
		// scalar through pyval.Str where Python keeps the object.
		//
		// The sentence here used to say the constructor REFUSED these
		// and that Python counted them as drift. Neither is true, and a
		// comment that asserts a mechanism is the thing that later
		// justifies keeping a branch for the wrong reason.
		{"a drifted row for THIS skill", []string{good,
			`{"skill_id": "s1", "input_description": 7, ` +
				`"expected_keywords": ["k"]}`}},
		{"a drifted row for ANOTHER skill is not counted",
			[]string{good, `{"skill_id": "s2", "input_description": 7}`}},
		// A stored JSON null. It is a PRESENT key, so `d.get(..., [])`
		// keeps None rather than defaulting — and the port's first cut
		// used `keywordsRaw != nil` to mean "this row had a proper list",
		// which a null satisfies. It re-emitted `[null]`: a shape neither
		// runtime holds, invented by the port's own storage decision, and
		// the exact thing MarshalJSON exists to prevent.
		{"a null keywords value is kept as null", []string{good,
			`{"skill_id": "s1", "input_description": "x", ` +
				`"expected_keywords": null}`}},
		{"keywords that are not a list", []string{good,
			`{"skill_id": "s1", "input_description": "x", ` +
				`"expected_keywords": "abc"}`}},
		{"a keyword that is not a string", []string{good,
			`{"skill_id": "s1", "input_description": "x", ` +
				`"expected_keywords": [7]}`}},

		// The DEFAULTS: from_dict fills every absent field, so a row with
		// only a skill_id is a valid (empty) test case, not drift.
		{"an almost-empty row is valid", []string{`{"skill_id": "s1"}`}},
		// skill_id itself defaults to "", and "" never equals "s1" — the
		// row is skipped by the id test, not refused.
		{"a row with no skill_id at all", []string{good, `{"input_description": "x"}`}},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			for _, ws := range []string{pyWS, goWS} {
				if c.lines == nil {
					continue
				}
				if err := os.MkdirAll(filepath.Dir(skillTestsPath(ws)),
					0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(skillTestsPath(ws),
					[]byte(strings.Join(c.lines, "\n")+"\n"), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			var want testGateWant
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyTestGateSrc, &want, pyprobe.Arg(t, map[string]any{
					"verb": "load", "skill_id": "s1", "script": []string{},
					// A FULL skill dict: dict_to_skill subscripts
					// d["description"] rather than .get()-ing it, so a
					// minimal dict raises KeyError before the probe reaches
					// its verb at all. The value is irrelevant to this test
					// and the shape is not.
					"skill": map[string]any{"id": "s1", "name": "n",
						"description": "d", "steps_template": []any{"s"},
						"trigger_patterns": []any{},
						"created_at":       "2026-01-01T00:00:00+00:00"}}))
			want.assertRaise(t, nil)
			got, warnings := LoadSkillTests(goWS, "s1")
			cmpTestCases(t, got, want.Tests)

			// THE LOSS SIGNAL, compared sentence for sentence.
			//
			// A reader that silently dropped a line would satisfy the list
			// comparison above and nothing else: the fixtures for the
			// blank-frame rule and for loads_clean's four refusals are
			// only observable here. Comparing the exact sentences — not
			// just "did it say something" — is what pins the BUCKET, and
			// the bucket is the operator-facing claim: `1 non-dict` says a
			// writer is emitting the wrong shape, `1 malformed` says bytes
			// are torn. Both drop the same row.
			//
			// Only the workspace path differs by construction, and it is
			// normalized away.
			norm := func(ws string, ws2 string, in []string) []string {
				out := make([]string, 0, len(in))
				for _, w := range in {
					w = strings.ReplaceAll(w, ws, "<WS>")
					out = append(out, strings.ReplaceAll(w, ws2, "<WS>"))
				}
				return out
			}
			gotW := norm(goWS, pyWS, warnings)
			wantW := norm(pyWS, goWS, want.Warnings)
			if !reflect.DeepEqual(gotW, wantW) {
				t.Errorf("warnings:\n go: %q\n py: %q", gotW, wantW)
			}
		})
	}
}

// TestLoadSkillTestsAtTheEmptySkillID pins the id comparison at its own
// boundary, which the fixture table above cannot reach.
//
// Python compares `d.get("skill_id") != skill_id`, so an absent key is None
// and never equals anything. The port read the field with a bare type
// assertion, turning both an absent key and a non-string value into "" —
// which EQUALS the argument when the argument is "". Every fixture in the
// table queries "s1", where the two spellings agree; the disagreement is
// only at the empty string, and a comparison whose only disagreement is at
// its own boundary is the one nothing reaches by accident.
func TestLoadSkillTestsAtTheEmptySkillID(t *testing.T) {
	lines := []string{
		`{"skill_id": "s1", "input_description": "belongs to s1", ` +
			`"expected_keywords": ["k"]}`,
		`{"input_description": "no skill_id at all", "expected_keywords": ["k"]}`,
		`{"skill_id": "", "input_description": "an empty skill_id", ` +
			`"expected_keywords": ["k"]}`,
		`{"skill_id": 7, "input_description": "a numeric skill_id", ` +
			`"expected_keywords": ["k"]}`,
	}
	pyWS, goWS := t.TempDir(), t.TempDir()
	for _, ws := range []string{pyWS, goWS} {
		if err := os.MkdirAll(filepath.Dir(skillTestsPath(ws)), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(skillTestsPath(ws),
			[]byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	var want testGateWant
	pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
		t, pyTestGateSrc, &want, pyprobe.Arg(t, map[string]any{
			"verb": "load", "skill_id": "", "script": []string{},
			"skill": map[string]any{"id": "s1", "name": "n",
				"description": "d", "steps_template": []any{"s"},
				"trigger_patterns": []any{},
				"created_at":       "2026-01-01T00:00:00+00:00"}}))
	want.assertRaise(t, nil)
	got, _ := LoadSkillTests(goWS, "")
	cmpTestCases(t, got, want.Tests)
}
