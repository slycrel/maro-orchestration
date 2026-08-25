package skills

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The Phase-14 mutation gate: generate synthetic tests for a skill, run them
// against a proposed mutation, and block the write-back if they fail.
//
// This is the autonomous evolver's only brake. Everything else in this
// package decides which skill to USE; this decides whether a rewritten skill
// is allowed to replace the one on disk. Two properties follow from that and
// are load-bearing everywhere below:
//
//   - It FAILS OPEN, on purpose and in three separate places (no adapter, no
//     tests generated, an LLM call that raises). A gate that blocked on its
//     own outage would freeze skill evolution the first time a model was
//     unreachable, which is worse than letting a mutation through.
//   - Its "test run" is a keyword smoke test with tools disabled, not an
//     execution. Python says so at the call: this runs inside the autonomous
//     mutation gate, where live tools would be a side-effect hazard.

// SkillTestCase ports skill_types.SkillTestCase.
//
// ExpectedKeywords is []any, not []string, because from_dict does NOT
// validate and the dataclass does not enforce — a stored row whose
// expected_keywords is the string "abc" produces a SkillTestCase that
// iterates as three one-character keywords, and one holding a number
// produces keywords whose `.lower()` raises. Both are counted in `total`,
// which is what decides whether a mutation is blocked, so narrowing this
// field to []string does not merely lose a value: it changes the gate's
// denominator. Measured — CPython returns 2 test cases where the first cut
// of this port returned 1.
type SkillTestCase struct {
	SkillID            string `json:"skill_id"`
	InputDescription   string `json:"input_description"`
	ExpectedKeywords   []any  `json:"expected_keywords"`
	DerivedFromFailure string `json:"derived_from_failure"`

	// keywordsRaw holds expected_keywords when it was NOT a list, so
	// RunSkillTests can iterate it the way Python does — a string yields
	// its characters. Unexported: nothing outside this file may treat a
	// drifted row as if it were a normal one.
	//
	// keywordsIsRaw is not redundant with `keywordsRaw != nil`, which is
	// what the first cut tested. A stored JSON `null` IS a drifted value
	// and it is also a nil `any`, so the nil test read it as "this row had
	// a proper list" and re-emitted `[null]` — the exact invented shape
	// MarshalJSON exists to prevent. A zero value that has to mean two
	// things means neither; the flag says which.
	keywordsRaw   any
	keywordsIsRaw bool
}

// SkillMutationResult ports skill_types.SkillMutationResult.
type SkillMutationResult struct {
	SkillID     string
	Original    Skill
	Mutated     Skill
	TestsRun    int
	TestsPassed int
	Blocked     bool
	BlockReason string
}

// ToDict is skill_types.SkillTestCase.to_dict — the four keys, in Python's
// order, because this is what _save_skill_tests appends to a store both
// runtimes read.
func (tc SkillTestCase) ToDict() pyval.Obj {
	return pyval.Obj{
		{Key: "skill_id", Val: tc.SkillID},
		{Key: "input_description", Val: tc.InputDescription},
		// The RAW value when there was one. Python's dataclass holds
		// whatever from_dict put in the field and to_dict hands the same
		// object back, so re-saving a drifted row rewrites it unchanged;
		// emitting `pyval.List(...)` here would launder a stored `7` into
		// `[7]` on the way through.
		//
		// UNREACHABLE TODAY, and labelled rather than claimed: nothing
		// re-saves a LOADED test case. SaveSkillTests is called only by
		// GenerateSkillTests, whose cases always carry real lists, so no
		// mutation can pin this arm. It is here because the divergence is
		// real the moment a caller does round-trip a loaded row, and
		// because the sibling writer (MarshalJSON) needs the same answer
		// for a comparison that IS reachable — one helper, so the two
		// cannot drift.
		{Key: "expected_keywords", Val: tc.keywordsForWrite()},
		{Key: "derived_from_failure", Val: tc.DerivedFromFailure},
	}
}

// keywordsForWrite is the value both writers emit: the raw one for a
// drifted row, the list otherwise.
func (tc SkillTestCase) keywordsForWrite() any {
	if tc.keywordsIsRaw {
		return tc.keywordsRaw
	}
	return pyval.List(tc.ExpectedKeywords)
}

// SkillTestCaseFromDict is from_dict, defaults included.
//
// It NEVER refuses. Every field is a bare `d.get(key, default)` into a
// dataclass with no runtime type checking, so a drifted row is neither
// coerced nor rejected — it is held with the wrong type inside. The first
// cut of this port validated each field and returned an error, on the
// reasonable-sounding grounds that a coerced identity is worse than a
// dropped one. That was the wrong dichotomy: CPython does neither, and the
// differential caught it because the ROW COUNT diverged. `total` is the
// gate's denominator, and dropping a row a mutation could never satisfy
// silently turns a block into a pass.
//
// The one place the port cannot hold Python's value is a non-string
// input_description or derived_from_failure, since a Go struct field has a
// type. NAMED DIVERGENCE: those are rendered through pyval.Str, where
// CPython holds the object. Neither is ever compared or re-serialized — the
// first rides an LLM message and the second is only echoed back — and
// ToDict is called on freshly generated cases only, never on a loaded row,
// so no store ever receives the rendered form.
func SkillTestCaseFromDict(d map[string]any) (SkillTestCase, error) {
	tc := SkillTestCase{}
	str := func(key string) string {
		v, ok := d[key]
		if !ok {
			return "" // `d.get(key, "")`
		}
		if s, isStr := v.(string); isStr {
			return s
		}
		return pyval.Str(v) // the named divergence above
	}
	if s, isStr := d["skill_id"].(string); isStr {
		tc.SkillID = s
	}
	tc.InputDescription = str("input_description")
	tc.DerivedFromFailure = str("derived_from_failure")

	kws, ok := d["expected_keywords"]
	if !ok {
		tc.ExpectedKeywords = []any{} // `d.get(..., [])`
		return tc, nil
	}
	// Held as-is, whatever it is. RunSkillTests iterates it the way Python
	// iterates the stored value, so a string yields characters and a
	// number raises where Python raises.
	switch t := kws.(type) {
	case []any:
		tc.ExpectedKeywords = t
	case pyval.List:
		tc.ExpectedKeywords = []any(t)
	default:
		tc.ExpectedKeywords = []any{kws}
		tc.keywordsRaw = kws
		tc.keywordsIsRaw = true
	}
	return tc, nil
}

// MarshalJSON emits expected_keywords as the value the row actually holds,
// which for a drifted row is NOT a list. Without this the struct's []any
// field renders a non-list value as a one-element list — a shape neither
// runtime ever holds, invented by the port's own storage decision. Nothing
// writes through this path (SaveSkillTests goes through ToDict), so its only
// consumer is a comparison, and a comparison against an invented shape is
// worse than no comparison.
func (tc SkillTestCase) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]any{
		"skill_id":             tc.SkillID,
		"input_description":    tc.InputDescription,
		"expected_keywords":    tc.keywordsForWrite(),
		"derived_from_failure": tc.DerivedFromFailure,
	})
}

func skillTestsPath(ws string) string {
	return filepath.Join(ws, "memory", "skill-tests.jsonl")
}

// SaveSkillTests appends test cases under the store lock.
func SaveSkillTests(ws string, tests []SkillTestCase) error {
	path := skillTestsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return err
	}
	return record.Locked(path, func() error {
		f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY,
			record.NewFileMode())
		if err != nil {
			return err
		}
		defer f.Close()
		for _, t := range tests {
			line, derr := pyval.DumpsCompactPy(t.ToDict())
			if derr != nil {
				return derr
			}
			if _, werr := f.WriteString(line + "\n"); werr != nil {
				return werr
			}
		}
		return nil
	})
}

// LoadSkillTests returns the test cases stored for one skill id, plus a
// warning naming any rows that were JSON but not loadable.
//
// The admission predicate is record.LoadsClean over an untrimmed split, the
// same as every other reader in this package — Python reaches this store
// through _read_store, which is read_jsonl_announced -> _classify ->
// loads_clean. Round 10 found the one reader here that had grown its own
// ladder; this one is written against the shared helper from the start.
//
// The skill_id comparison happens BEFORE the constructor, exactly as Python
// orders it: a drifted row belonging to another skill is skipped silently
// and never counted, so the warning's number is about THIS skill.
func LoadSkillTests(ws, skillID string) ([]SkillTestCase, []string) {
	path := skillTestsPath(ws)
	// read_jsonl_announced, not an inline read. The first cut spelled the
	// scan out here — IsFrameBlank, LoadsClean, skip — and it was correct
	// about which lines are records and silent about the ones it dropped.
	// Python's loader gets the announcement for free by going through the
	// shared reader; a port that copies the ladder inherits the ladder and
	// not the warning, and nothing about the row list makes the difference
	// visible. See record.ReadAllAnnounced.
	rows, warning := record.ReadAllAnnounced(path, "_load_skill_tests")
	var warnings []string
	if warning != "" {
		warnings = append(warnings, warning)
	}
	var tests []SkillTestCase
	drifted := 0
	for _, d := range rows {
		// `d.get("skill_id") != skill_id`. The bare `s, _ :=` spelling
		// this replaces turned BOTH an absent key and a non-string value
		// into "", which equals the argument when the argument is "" —
		// so a row with no skill_id at all was admitted for an empty
		// query, where CPython compares None to "" and skips it. A
		// comparison whose only disagreement is at its own boundary is
		// exactly the one no fixture reaches by accident.
		s, isStr := d["skill_id"].(string)
		if !isStr || s != skillID {
			continue
		}
		tc, cerr := SkillTestCaseFromDict(d)
		if cerr != nil {
			// UNREACHABLE IN BOTH RUNTIMES, on purpose, and kept.
			//
			// Python's `except Exception: drifted += 1` guards
			// `SkillTestCase.from_dict`, which is four `d.get(k, default)`
			// calls on a value the reader has already proved is a dict —
			// there is no input that reaches it and raises. The port's
			// constructor is the same shape and its error is always nil.
			//
			// So this is a drift counter that can never count, and the
			// warning below is a sentence no operator will ever read. That
			// is a fact about the CONSTRUCTOR, not about the loader: the
			// day from_dict grows a coercion or a required field, both
			// runtimes start losing rows here, and the branch that
			// announces it should already exist. Deleting it would make
			// the port quieter than Python about a loss neither can have
			// yet — and a mutation battery cannot pin a branch nothing
			// reaches, so the comment is the pin.
			drifted++
			continue
		}
		tests = append(tests, tc)
	}
	if drifted > 0 {
		warnings = append(warnings, fmt.Sprintf(
			"[skills] _load_skill_tests: %d row(s) for %s "+
				"are JSON but not loadable as SkillTestCase — skipped (%s)",
			drifted, skillID, path))
	}
	return tests, warnings
}

// generateTestsSystem is _GENERATE_TESTS_SYSTEM, byte for byte.
const generateTestsSystem = `You are generating synthetic test cases for an AI skill.
Given the skill description and failure examples, create 2-3 test cases.

Each test case has:
- input_description: a task description to give the skill
- expected_keywords: 2-4 keywords that should appear in a correct response

Return ONLY a JSON array:
[
  {"input_description": "...", "expected_keywords": ["kw1", "kw2"]},
  ...
]
`

// GenerateSkillTests produces 2-3 test cases for a skill and saves them.
//
// # The heuristic fallback can RAISE, and it is not inside the try
//
// Python's try covers the LLM path only. The fallback below it calls
// `skill.name.split()[0]`, and `str.split()` with no argument splits on runs
// of whitespace AND discards empties — so a name that is truthy but contains
// only whitespace yields the EMPTY list and `[0]` is an IndexError that
// leaves this function. `if skill.name else "result"` does not guard it: the
// guard is truthiness, and " " is truthy.
//
// That is not a hypothetical shape. Round 10 established that a name of only
// U+001C..U+001F reaches storage in the Go runtime precisely because
// strings.TrimSpace does not strip them — so the two defects composed:
// mint.go would write such a name, and this function would then raise on it.
// The strip is fixed; this reproduces the raise anyway, because Python still
// does it and a skill row written by an older build is still on disk.
//
// The same applies to `skill.steps_template[0].split()[0]`, guarded by the
// list's truthiness and not by the first element's.
func GenerateSkillTests(ctx context.Context, ws string, skill Skill,
	failureExamples []string, adapter llm.Adapter) ([]SkillTestCase, error) {
	if adapter != nil {
		// Everything to the `if tests:` is inside Python's try, whose except
		// prints to stderr under __debug__ and falls through to the
		// heuristic. The port answers by falling through too.
		saved, err := generateViaLLM(ctx, ws, skill, failureExamples, adapter)
		if err == nil && len(saved) > 0 {
			return saved, nil
		}
	}

	// `failure_examples[0][:100] if failure_examples else "handle errors
	// gracefully"` — a RUNE slice, since len() and slicing are code points.
	failureHint := "handle errors gracefully"
	if len(failureExamples) > 0 {
		failureHint = pyval.Clip(failureExamples[0], 100)
	}

	trigger := "a typical task"
	if len(skill.TriggerPatterns) > 0 {
		trigger = skill.TriggerPatterns[0]
	}
	nameHead, err := firstWord(skill.Name, "result")
	if err != nil {
		return nil, err
	}
	stepHead := "step"
	if len(skill.StepsTemplate) > 0 {
		if stepHead, err = firstWordOf(skill.StepsTemplate[0]); err != nil {
			return nil, err
		}
	}
	// The second case's fallback is "skill", not "result" — the two
	// expressions are spelled differently in Python and a port that shared
	// one helper with one default would silently agree on every skill whose
	// name is non-empty and disagree only on the empty one.
	nameHead2, err := firstWord(skill.Name, "skill")
	if err != nil {
		return nil, err
	}

	heuristic := []SkillTestCase{
		{
			SkillID: skill.ID,
			InputDescription: fmt.Sprintf(
				"Apply the '%s' skill to: %s", skill.Name, trigger),
			ExpectedKeywords:   []any{nameHead, stepHead},
			DerivedFromFailure: failureHint,
		},
		{
			SkillID: skill.ID,
			InputDescription: fmt.Sprintf(
				"Describe how to use the '%s' skill", skill.Name),
			ExpectedKeywords:   []any{"skill", nameHead2},
			DerivedFromFailure: failureHint,
		},
	}
	if err := SaveSkillTests(ws, heuristic); err != nil {
		return nil, err
	}
	return heuristic, nil
}

// firstWord is `s.split()[0] if s else fallback` — including the IndexError.
func firstWord(s, fallback string) (string, error) {
	if !pyval.Truthy(s) {
		return fallback, nil
	}
	return firstWordOf(s)
}

// firstWordOf is `s.split()[0]`, which raises IndexError on a string with no
// non-whitespace run. pytext.Split is str.split()'s no-argument form: it
// splits on Python's whitespace set — which is NOT Go's, the U+001C..U+001F
// difference again — and discards empty fields, so " ".split() is [].
func firstWordOf(s string) (string, error) {
	fields := pytext.Split(s)
	if len(fields) == 0 {
		return "", &pyval.PyErr{Class: "IndexError",
			Msg: "list index out of range"}
	}
	return fields[0], nil
}

// generateViaLLM is the try-block half of GenerateSkillTests. It returns the
// saved tests, or an error standing for Python's `except Exception`.
func generateViaLLM(ctx context.Context, ws string, skill Skill,
	failureExamples []string, adapter llm.Adapter) ([]SkillTestCase, error) {
	var fparts []string
	for i, e := range failureExamples {
		if i == 5 {
			break // failure_examples[:5]
		}
		fparts = append(fparts, "- "+pyval.Clip(e, 200))
	}
	var sparts []string
	for i, s := range skill.StepsTemplate {
		if i == 5 {
			break
		}
		sparts = append(sparts, "- "+s)
	}
	userMsg := fmt.Sprintf("Skill: %s\nDescription: %s\nSteps:\n%s\n\n"+
		"Failure examples:\n%s\n\nGenerate 2-3 test cases.",
		skill.Name, pyval.Clip(skill.Description, 300),
		strings.Join(sparts, "\n"), strings.Join(fparts, "\n"))

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: generateTestsSystem},
		{Role: "user", Content: userMsg},
	}, llm.Options{MaxTokens: 512, Temperature: 0.2,
		Purpose: "skill test generation"})
	if err != nil {
		return nil, err
	}

	raw, jerr := jsonx.ArrayOrdered(llm.ContentOrEmpty(resp))
	if jerr != nil {
		// extract_json returns its default [] rather than raising, so this
		// is NOT the except path — it falls to `if tests:` with tests
		// empty, which is the same place. Spelled as a nil answer so the
		// caller's fall-through is one branch, not two.
		return nil, nil
	}

	var tests []SkillTestCase
	for i, item := range raw {
		if i == 3 {
			break // raw[:3]
		}
		obj, isObj := item.(pyval.Obj)
		if !isObj {
			// `if isinstance(item, dict)` — a non-mapping is skipped, and
			// unlike the mint path this skip does NOT abandon the loop.
			//
			// NO MUTATION CAN PIN THIS BRANCH, and the reason is worth
			// keeping. Delete the guard in PYTHON and a string item makes
			// `item.get(...)` raise AttributeError, which the outer try
			// catches — abandoning the whole LLM path, discarding items
			// already collected, falling to the heuristic. Delete it HERE
			// and `obj` is the zero pyval.Obj: every Get misses, the
			// description is "", and the item is dropped by the
			// `inputDesc != ""` test three lines down. Same answer, so a
			// differential sees nothing.
			//
			// That asymmetry is the point. The guard is not redundant
			// because it is unnecessary; it is unobservable because Go's
			// zero value silently absorbs what Python raises on, and the
			// two paths only agree while the emptiness test below stays
			// exactly where it is. Move that test and the runtimes part
			// company with nothing to announce it.
			continue
		}
		inputDesc := pyStrip(pyval.Str(getOr(obj, "input_description", "")))
		kwRaw, present := obj.Get("expected_keywords")
		if !present {
			kwRaw = pyval.List{}
		}
		keywords, kerr := strippedNonEmpty(kwRaw)
		if kerr != nil {
			// Iterating a non-iterable is a TypeError, which Python's outer
			// try catches — abandoning the whole LLM path, not just this
			// item.
			return nil, kerr
		}
		if inputDesc != "" && len(keywords) > 0 {
			derived := ""
			if len(failureExamples) > 0 {
				derived = pyval.Clip(failureExamples[0], 200)
			}
			tests = append(tests, SkillTestCase{
				SkillID:            skill.ID,
				InputDescription:   inputDesc,
				ExpectedKeywords:   keywords,
				DerivedFromFailure: derived,
			})
		}
	}
	if len(tests) == 0 {
		return nil, nil
	}
	if err := SaveSkillTests(ws, tests); err != nil {
		return nil, err
	}
	return tests, nil
}

// keywordsOrRaw is the value Python would iterate: the raw one when
// expected_keywords was not a list, the list otherwise.
func (tc SkillTestCase) keywordsOrRaw() any { return tc.keywordsForWrite() }

// pyIterate yields what `for x in v` yields: a list's elements, a string's
// characters, a mapping's keys — and a TypeError for anything else.
//
// Extracted from strippedNonEmpty rather than copied: two iterators that
// disagreed about what a dict yields would be two different ports of one
// language rule (lens 4).
func pyIterate(v any) ([]any, error) {
	switch t := v.(type) {
	case pyval.List:
		return []any(t), nil
	case []any:
		return t, nil
	case string:
		var out []any
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	case pyval.Obj:
		var out []any
		for _, kv := range t {
			out = append(out, kv.Key)
		}
		return out, nil
	case map[string]any:
		// The SAME language rule as the pyval.Obj arm, for the other
		// spelling of a mapping — and the only one that can reach the run
		// gate. record.LoadsClean decodes through encoding/json, so a
		// nested object in a stored row is a map[string]any and never a
		// pyval.Obj; the ordered arm above is reachable only from the LLM
		// path. Without this case a stored `"expected_keywords": {...}`
		// raised TypeError, the test was skipped rather than run, and the
		// short `passed` count blocked a mutation CPython lets through
		// (measured: CPython (1,1), the port (0,1)).
		//
		// Sorted, not insertion order: a Go map cannot remember one. The
		// only consumer of this arm is an `any(...)` over the keys, whose
		// answer does not depend on order — and sorted at least makes the
		// port's own answer reproducible. If a caller ever needs the
		// ORDER, it must decode through pyval.LoadsOrdered instead; that
		// is a different fix and this comment is where it starts.
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out := make([]any, 0, len(keys))
		for _, k := range keys {
			out = append(out, k)
		}
		return out, nil
	default:
		return nil, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
			"'%s' object is not iterable", pyval.TypeName(v))}
	}
}

// strippedNonEmpty is `[str(k).strip() for k in v if str(k).strip()]`.
//
// It iterates the way Python does — a string yields its characters, a dict
// its keys — because the value came from a model reply and "expected_keywords"
// arriving as a bare string is exactly the shape that produces one-character
// keywords rather than an error. pyStrip, not strings.TrimSpace: these
// strings are compared against model output and stored.
func strippedNonEmpty(v any) ([]any, error) {
	items, err := pyIterate(v)
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, it := range items {
		if s := pyStrip(pyval.Str(it)); s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// RunSkillTests runs test cases against a skill and returns (passed, total).
//
// # Every failure mode counts as a FAILED test, not as an error
//
// Python wraps each test in its own bare except, so an adapter that raises
// on test 2 leaves tests 1 and 3 counted normally. It also reads
// `resp.content.lower()` DIRECTLY rather than through content_or_empty — so
// a reply with a null content raises AttributeError into that same except
// and the test simply does not pass. The port keeps both: an error here is
// a non-passing test, never a returned error.
//
// pyLower, not strings.ToLower, on both sides of the match: the output and
// each keyword are lowered before a substring test, and U+0130 lowercases to
// two runes in Python and one in Go — enough to decide whether a keyword is
// found, and therefore whether a mutation is blocked.
func RunSkillTests(ctx context.Context, skill Skill, tests []SkillTestCase,
	adapter llm.Adapter, dryRun bool) (int, int) {
	if len(tests) == 0 {
		return 0, 0
	}
	total := len(tests)
	if adapter == nil || dryRun {
		// Fails OPEN: no adapter means every test passes. See the file
		// header — a gate that blocked on its own outage would freeze skill
		// evolution the first time a model was unreachable.
		return total, total
	}

	var sparts []string
	for i, s := range skill.StepsTemplate {
		if i == 5 {
			break
		}
		sparts = append(sparts, "- "+s)
	}
	skillContext := fmt.Sprintf(
		"You are executing the '%s' skill.\nDescription: %s\nSteps:\n%s",
		skill.Name, pyval.Clip(skill.Description, 200),
		strings.Join(sparts, "\n"))

	passed := 0
	for _, test := range tests {
		resp, err := adapter.Complete(ctx, []llm.Message{
			{Role: "system", Content: skillContext},
			{Role: "user", Content: test.InputDescription},
		}, llm.Options{MaxTokens: 256, Temperature: 0.1,
			Purpose: "skill smoke test"})
		if err != nil {
			continue
		}
		if resp == nil {
			// `resp.content.lower()` on a None response is an
			// AttributeError inside the per-test except: the test does not
			// pass, and the loop goes on. Python can also reach that raise
			// with a response whose CONTENT is None; Go's Response.Content
			// is a string, so that half of the shape cannot occur here and
			// the empty string is the only spelling of "nothing came back".
			continue
		}
		// resp.Content RAW, not llm.ContentOrEmpty: Python reads
		// `resp.content.lower()` directly here, with no strip. A stripped
		// copy would change no keyword match today and would be a second
		// reader of this field disagreeing with the one being ported.
		output := pyLower(resp.Content)
		// `any(kw.lower() in output for kw in test.expected_keywords)` —
		// over the STORED value. A non-list iterates the Python way (a
		// string yields characters); a non-string ELEMENT has no .lower()
		// and the AttributeError lands in this loop's per-test except, so
		// the test does not pass and the runner goes on.
		kws, iterErr := pyIterate(test.keywordsOrRaw())
		if iterErr != nil {
			continue
		}
		for _, kwAny := range kws {
			kw, isStr := kwAny.(string)
			if !isStr {
				break // AttributeError: no .lower() on this
			}
			if strings.Contains(output, pyLower(kw)) {
				passed++
				break // `any(...)` stops at the first hit
			}
		}
	}
	return passed, total
}

// ValidateSkillMutation is the gate itself.
//
// failureReasons stands for Python's `load_attributions(limit=20)` walk,
// which collects raw_reason for every attribution whose failed_skill equals
// the ORIGINAL's NAME — a name, not an id, so two skills that share a name
// share a test corpus. The port takes the list as a parameter because the
// attribution loader lives in a package this one cannot import; the caller
// does the walk, and the comparison it must make is on Name.
//
// Three fail-open paths, in order: no tests on disk and none generated;
// adapter nil, which makes the run a dry run where everything passes; and
// each test's own except. Only a real, non-dry run with a shortfall blocks.
func ValidateSkillMutation(ctx context.Context, ws string,
	original, mutated Skill, failureReasons []string,
	adapter llm.Adapter) (SkillMutationResult, error) {
	res, _, err := ValidateSkillMutationWithLog(
		ctx, ws, original, mutated, failureReasons, adapter)
	return res, err
}

// ValidateSkillMutationWithLog is ValidateSkillMutation plus the store
// warnings its load produced.
//
// Python logs those from inside `_load_skill_tests`, so a torn
// skill-tests.jsonl reaching the gate is operator-visible there. This port
// has no logger — every ported warning is returned as data — which moves
// the decision to the caller and means the caller can DROP it. The first
// cut did exactly that — it dropped the second return value — and no test
// could see it: the tripwire only checked that the loader still called the
// announced reader, one frame below where the announcement was being
// thrown away. Announcing into a return value nobody reads is the same
// silence with more code (lens 17, one level up).
//
// The name follows record.WriteOutcomeWithLog, which is the same shape for
// the same reason.
func ValidateSkillMutationWithLog(ctx context.Context, ws string,
	original, mutated Skill, failureReasons []string,
	adapter llm.Adapter) (SkillMutationResult, []string, error) {
	res := SkillMutationResult{SkillID: original.ID,
		Original: original, Mutated: mutated}

	tests, warnings := LoadSkillTests(ws, original.ID)
	if len(tests) == 0 {
		var err error
		tests, err = GenerateSkillTests(ctx, ws, original, failureReasons, adapter)
		if err != nil {
			// The IndexError out of the heuristic fallback. Python lets it
			// leave validate_skill_mutation too — there is no try here —
			// so the port returns it rather than converting it into an
			// unblocked result.
			return SkillMutationResult{}, warnings, err
		}
	}
	if len(tests) == 0 {
		// No tests available at all — allow the mutation.
		return res, warnings, nil
	}

	dryRun := adapter == nil
	passed, total := RunSkillTests(ctx, mutated, tests, adapter, dryRun)
	res.TestsRun = total
	res.TestsPassed = passed
	// `(not dry_run) and (passed < total)`. The first half CANNOT FIRE:
	// dryRun is exactly `adapter is None`, and RunSkillTests answers
	// (total, total) for that case, so `passed < total` is already false
	// whenever dryRun is true. It is a duplicated guard, and it is
	// duplicated in Python too — kept rather than simplified because the
	// simplification would be a port deciding it understands the original
	// better than the original does, and because a future RunSkillTests
	// that stopped failing open would need this half. The mutation battery
	// records it as undetectable BY CONSTRUCTION rather than as a gap.
	res.Blocked = !dryRun && passed < total
	if res.Blocked {
		// The reason is stored prose in a shared file, so the sentence is
		// the contract — the content-key divergence family.
		res.BlockReason = fmt.Sprintf("Mutation failed %d/%d tests for skill '%s'",
			total-passed, total, original.Name)
	}
	return res, warnings, nil
}
