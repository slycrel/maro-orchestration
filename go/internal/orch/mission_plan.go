package orch

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Mission decomposition — mission.py's planning half.
//
// Slice 1 ported the store. This is the layer above it: turn a goal into
// milestones and features, resolve the dependency edges, and decide
// whether the result is chain-shaped. `run_mission` itself is not here —
// it needs the agent loop — but everything it calls before executing
// anything is.
//
// THE HAZARD THAT SHAPES THIS FILE: every field the model supplies
// reaches a string through Python's `str(x).strip()`, which is not a
// cast. `"title": null` becomes the four characters `None`; `"title":
// {"a": 1}` becomes the eight characters `{'a': 1}`; and `"features":
// "abc"` becomes THREE features named `a`, `b` and `c`, because `"abc"[:3]`
// slices the string and iterating a string yields characters. All three
// were measured against CPython before this file was written, and all
// three land in a shared store as text a human reads and a later run
// keys on.

// The two system prompts are byte-exact transcriptions. They are sent to
// a paid API and recorded in the run log, so a reflowed line is a real
// difference even though it reads the same.
const decomposeSystem = `You are a mission planning agent.
Decompose a multi-day goal into 2-4 milestones with validation criteria,
and 2-4 features per milestone.
Each feature is a discrete unit of work completable in one agent session.
Respond ONLY with JSON. No prose. No markdown fences.
JSON shape:
{
  "milestones": [
    {
      "title": "Milestone title",
      "features": ["Feature one description", "Feature two description"],
      "validation_criteria": ["Check one", "Check two"],
      "depends_on": [0]
    }
  ]
}
"depends_on" is optional: a list of zero-based indexes of EARLIER milestones
in your array whose completion this one must wait for. Omit it for a simple
pipeline (each milestone follows the previous one). Use [] for a milestone
that can start immediately. Declare independence ([] or a partial list) only
when the work genuinely does not build on the skipped milestones' output —
independent milestones may execute concurrently, IN THE SAME project
working directory. Do not declare milestones independent if they would
edit the same files or contend over the same resources; when in doubt,
keep the dependency.`

const validateSystem = `You are a milestone validation agent.
Given completed feature work and validation criteria, decide if this milestone succeeded.
Respond ONLY with JSON: {"passed": true or false, "reason": "one sentence"}`

// ErrMalformedPlan is what a Python TypeError/KeyError out of the parse
// loop becomes here.
//
// This is not defensive tidying. `rm.get("features", [])[:3]` on a
// `"features": null` raises TypeError, and the surrounding `try` catches
// only ImportError — so the exception leaves decompose_mission and kills
// the caller. A Go port that quietly fell through to the heuristic would
// produce a working mission where Python produces none, which is the
// larger divergence of the two.
var ErrMalformedPlan = errors.New("mission: malformed decomposition")

// ErrNoAdapter is what Python's AttributeError becomes here.
//
// decompose_mission has NO nil-adapter guard: it calls
// `adapter.complete(...)` unconditionally, and the surrounding `except
// ImportError` does not catch an AttributeError, so a None adapter kills
// the caller. This port used to guard with `if a != nil` and fall
// through to the heuristic, which hands back a WORKING mission where
// Python hands back none — the same class of divergence ErrMalformedPlan
// exists to prevent (adversarial mission-r1 LOW). Callers that want the
// heuristic must ask for it; they cannot get it by passing nothing.
var ErrNoAdapter = errors.New(
	"mission: AttributeError: 'NoneType' object has no attribute 'complete'")

// DecomposeMission is decompose_mission: one LLM call, a heuristic
// fallback, and the depends_on resolution in between.
func DecomposeMission(
	ctx context.Context,
	goal string,
	a llm.Adapter,
	maxMilestones, maxFeaturesPerMilestone int,
	now func() string,
	newID func() string,
) (*Mission, error) {
	missionID := newID()
	createdAt := now()

	// Unconditional, exactly as Python calls it — see ErrNoAdapter.
	milestones, err := decomposeViaLLM(ctx, goal, a,
		maxMilestones, maxFeaturesPerMilestone, newID)
	if err != nil {
		return nil, err
	}

	if len(milestones) == 0 {
		milestones = heuristicMilestones(goal, newID)
	}

	return &Mission{
		ID:         missionID,
		Goal:       goal,
		Project:    "", // filled in by run_mission
		Milestones: milestones,
		Status:     "pending",
		CreatedAt:  createdAt,
	}, nil
}

// decomposeViaLLM is the body of the `try` block. An adapter error is
// swallowed (Python's `except ImportError` never fires for a live
// adapter, and a failed call simply leaves `data` empty), but a MALFORMED
// PLAN is not — see ErrMalformedPlan.
func decomposeViaLLM(
	ctx context.Context,
	goal string,
	a llm.Adapter,
	maxMilestones, maxFeaturesPerMilestone int,
	newID func() string,
) ([]Milestone, error) {
	if a == nil {
		return nil, ErrNoAdapter
	}
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: decomposeSystem},
		{Role: "user", Content: fmt.Sprintf(
			"Goal: %s\n\nDecompose into %d or fewer milestones, "+
				"each with %d or fewer features.",
			goal, maxMilestones, maxFeaturesPerMilestone)},
	}, llm.Options{
		MaxTokens:   2048,
		Temperature: 0.2,
		Purpose:     "mission decompose",
		// Python passes no_tools=True. Here that is the absence of both
		// Tools and AgentTools, which is this struct's zero value — the
		// utility lane. Spelled out because "no field" is easy to read
		// as "nobody thought about it".
	})
	if err != nil {
		return nil, nil // no data; fall through to the heuristic
	}

	data, jerr := jsonx.ObjectOrdered(contentOrEmpty(resp))
	if jerr != nil {
		return nil, nil // no usable JSON; fall through to the heuristic
	}

	// safe_list(element_type=dict, max_items=N): FILTER first, then take
	// the first N. The features list one scope down does the OPPOSITE —
	// slice first, then filter — and the two orders are four lines apart
	// in the Python.
	rawMilestones := safeListOfObjects(data, "milestones", maxMilestones)

	// kept tracks the raw index a milestone came from, because depends_on
	// refs are indexes into the MODEL's array and feature-less milestones
	// are dropped from ours — the two numberings diverge the moment one
	// is dropped.
	type kept struct {
		rawIdx  int
		depsRaw any
		hasDeps bool
		pos     int // index into out
	}
	var keptList []kept
	keptByRaw := map[int]int{} // raw index -> index into out
	var out []Milestone

	for rawIdx, rm := range rawMilestones {
		featuresRaw, present := rm.Get("features")
		if !present {
			featuresRaw = pyval.List{}
		}
		items, err := sliceForFeatures(featuresRaw, maxFeaturesPerMilestone)
		if err != nil {
			return nil, err
		}
		var features []Feature
		for _, f := range items {
			title := pytext.Strip(pyval.Str(f))
			if title == "" {
				continue
			}
			features = append(features, Feature{
				ID:     newID(),
				Title:  title,
				Status: "pending",
			})
		}
		if len(features) == 0 {
			continue
		}

		criteria, err := criteriaOf(rm)
		if err != nil {
			return nil, err
		}

		titleRaw, present := rm.Get("title")
		if !present {
			titleRaw = "Milestone"
		}
		depsRaw, hasDeps := rm.Get("depends_on")
		keptList = append(keptList, kept{rawIdx, depsRaw, hasDeps, len(out)})
		keptByRaw[rawIdx] = len(out)
		out = append(out, Milestone{
			ID:                 newID(),
			Title:              pytext.Strip(pyval.Str(titleRaw)),
			Features:           features,
			ValidationCriteria: criteria,
			Status:             "pending",
		})
	}

	// Resolve depends_on: raw zero-based indexes -> milestone ids.
	// Absent or malformed -> chain to the previous KEPT milestone, which
	// reproduces the pre-DAG sequential semantics exactly. Only earlier,
	// kept indexes resolve — self/forward refs and refs to dropped
	// (feature-less) milestones are discarded, so cycles are impossible
	// by construction.
	for pos, k := range keptList {
		list, isList := k.depsRaw.(pyval.List)
		if !k.hasDeps || !isList {
			if pos > 0 {
				out[k.pos].DependsOn = []string{out[keptList[pos-1].pos].ID}
			}
			continue
		}
		var resolved []string
		for _, j := range list {
			idx, ok := pyval.IsInt(j)
			if !ok {
				continue
			}
			if idx < 0 || idx >= k.rawIdx {
				continue
			}
			depPos, ok := keptByRaw[idx]
			if !ok {
				continue
			}
			if !containsString(resolved, out[depPos].ID) {
				resolved = append(resolved, out[depPos].ID)
			}
		}
		if len(list) > 0 && len(resolved) == 0 && pos > 0 {
			// The model explicitly signaled a dependency and every ref
			// was invalid (forward/self/dropped) — chain to the
			// predecessor rather than silently ungating a milestone the
			// model meant to order. Only a literal [] means
			// "independent root".
			resolved = []string{out[keptList[pos-1].pos].ID}
		}
		out[k.pos].DependsOn = resolved
	}

	return out, nil
}

// sliceForFeatures is `rm.get("features", [])[:n]` — and the subject is
// whatever the model sent, so the slice is Python's, not Go's.
//
// A list slices to a list. A STRING slices to a string, and the loop that
// follows iterates it CHARACTER by character, so `"features": "abc"` with
// n=3 produces three features named `a`, `b` and `c` (measured). A dict
// raises KeyError and null raises TypeError; both leave the function.
// pySliceLen is the length of `seq[:n]` for a Python sequence of length
// size. Python clamps rather than raising: a non-negative n is capped at
// size, and a NEGATIVE n counts back from the end and floors at zero.
//
//	[a,b,c][:5]  -> 3      [a,b,c][:-1] -> 2
//	[a,b,c][:0]  -> 0      [a,b,c][:-9] -> 0
//
// Go's t[:n] panics on both out-of-range ends, so every slice built from
// a caller-supplied bound goes through here.
func pySliceLen(size, n int) int {
	if n < 0 {
		if size+n < 0 {
			return 0
		}
		return size + n
	}
	if n > size {
		return size
	}
	return n
}

func sliceForFeatures(v any, n int) ([]any, error) {
	switch t := v.(type) {
	case pyval.List:
		// pySliceLen, not `if len(t) > n`: a NEGATIVE n made this evaluate
		// t[:-1] and PANIC, taking the whole process down where Python's
		// [:-1] simply drops the last element (adversarial mission-r1
		// MEDIUM). Both bounds are plain ints on an exported function.
		t = t[:pySliceLen(len(t), n)]
		return []any(t), nil
	case string:
		// The string arm slices too, and it had the mirror of the list
		// arm's bug: `len(out) == n` never fires for a negative n, so Go
		// returned every character where Python's "abc"[:-1] returns "ab".
		// Counting RUNES, because Python slices a str by code point.
		limit := pySliceLen(utf8.RuneCountInString(t), n)
		var out []any
		for _, r := range t {
			if len(out) == limit {
				break
			}
			out = append(out, string(r))
		}
		return out, nil
	case nil:
		return nil, fmt.Errorf("%w: 'NoneType' object is not subscriptable",
			ErrMalformedPlan)
	case pyval.Obj:
		return nil, fmt.Errorf("%w: KeyError: slice(None, %d, None)",
			ErrMalformedPlan, n)
	}
	return nil, fmt.Errorf("%w: object is not subscriptable", ErrMalformedPlan)
}

// criteriaOf is the validation_criteria comprehension. It ITERATES
// rather than slicing, so the failure spelling differs from features':
// null is "not iterable", and a string yields its characters.
func criteriaOf(rm pyval.Obj) ([]string, error) {
	raw, present := rm.Get("validation_criteria")
	if !present {
		return nil, nil
	}
	var items []any
	switch t := raw.(type) {
	case pyval.List:
		items = []any(t)
	case string:
		for _, r := range t {
			items = append(items, string(r))
		}
	case pyval.Obj:
		for _, f := range t {
			items = append(items, f.Key) // iterating a dict yields keys
		}
	case nil:
		return nil, fmt.Errorf("%w: 'NoneType' object is not iterable",
			ErrMalformedPlan)
	default:
		return nil, fmt.Errorf("%w: object is not iterable", ErrMalformedPlan)
	}
	var out []string
	for _, c := range items {
		if s := pytext.Strip(pyval.Str(c)); s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// contentOrEmpty is llm_parse.content_or_empty: the response's content,
// stripped, or "" when there is none.
func contentOrEmpty(r *llm.Response) string {
	if r == nil {
		return ""
	}
	return pytext.Strip(r.Content)
}

// safeListOfObjects is `safe_list(data.get(key, []), element_type=dict,
// max_items=n)`.
//
// The ORDER is the hazard. safe_list FILTERS first and slices second, so
// a list whose first element is a string still yields n objects. The
// features list four lines away in the Python does the opposite — slices
// first, filters second — so a blank first feature yields n-1. Both
// orders are reproduced verbatim; making them uniform would be a
// divergence in one direction or the other.
func safeListOfObjects(data pyval.Obj, key string, n int) []pyval.Obj {
	raw, present := data.Get(key)
	if !present {
		return nil
	}
	list, ok := raw.(pyval.List)
	if !ok {
		// EQUIVALENT-MUTANT NOTE: returning an empty non-nil slice here
		// is indistinguishable — the only caller ranges over the result
		// and never asks whether it is nil. The mutant survives and
		// should; no fixture can or should pin the difference.
		return nil // `if not isinstance(value, list): return []`
	}
	// safe_list is `[v for v in value if isinstance(...)][:max_items]`
	// (llm_parse.py:238-240): it filters the WHOLE list, then slices. The
	// early-break version here was equivalent only for n > 0.
	//
	// For n == 0, `len(out) == n` could never hold — the counter starts at
	// 1 — so Go returned the model's ENTIRE plan where Python returns
	// nothing and falls through to the heuristic: two different missions
	// in the same mission.json (adversarial mission-r1 MEDIUM). For n < 0
	// the break never fired either, where Python's [:-1] drops the LAST
	// kept object. Filtering in full and slicing once reproduces both.
	var out []pyval.Obj
	for _, v := range list {
		if o, ok := v.(pyval.Obj); ok {
			out = append(out, o) // filter-then-slice; see above
		}
	}
	//
	// EQUIVALENT-MUTANT NOTE: re-adding the early `if len(out) == n {
	// break }` now changes nothing, because the slice below applies
	// either way — for n > 0 the break stops at exactly n, and for n <= 0
	// it never fires. That mutant survives the suite and is meant to.
	// The break was only ever wrong when it was the ONLY bound.
	return out[:pySliceLen(len(out), n)]
}

// heuristicMilestones is the fallback: split the goal's WORDS in half and
// name two phases after the halves.
//
// `len(words) // 2 or 1` is floor division with a zero guard, so a
// one-word goal puts the whole word in BOTH halves and a three-word goal
// splits 1/2, not 2/1. The clips are code points, not bytes.
func heuristicMilestones(goal string, newID func() string) []Milestone {
	words := pytext.Split(goal)
	half := len(words) / 2
	if half == 0 {
		half = 1
	}
	if half > len(words) {
		half = len(words)
	}
	firstHalf := strings.Join(words[:half], " ")
	secondHalf := strings.Join(words[half:], " ")
	if secondHalf == "" {
		secondHalf = firstHalf
	}

	one := Milestone{
		ID:    newID(),
		Title: "Phase 1: " + pyval.Clip(firstHalf, 60),
		Features: []Feature{
			{ID: newID(), Title: "Research " + pyval.Clip(firstHalf, 40), Status: "pending"},
			{ID: newID(), Title: "Implement " + pyval.Clip(firstHalf, 40), Status: "pending"},
		},
		ValidationCriteria: []string{
			"Phase 1 work for '" + pyval.Clip(firstHalf, 40) + "' is complete"},
		Status: "pending",
	}
	two := Milestone{
		ID:    newID(),
		Title: "Phase 2: " + pyval.Clip(secondHalf, 60),
		Features: []Feature{
			{ID: newID(), Title: "Finalize " + pyval.Clip(secondHalf, 40), Status: "pending"},
			{ID: newID(), Title: "Verify " + pyval.Clip(secondHalf, 40), Status: "pending"},
		},
		ValidationCriteria: []string{
			"Phase 2 work for '" + pyval.Clip(secondHalf, 40) + "' is complete"},
		Status:    "pending",
		DependsOn: []string{one.ID},
	}
	return []Milestone{one, two}
}

// IsChainShaped is _is_chain_shaped: true when depends_on encodes exactly
// the old sequential walk — every milestone depends on precisely its
// predecessor, the first on nothing.
//
// This is the flag that keeps the DAG scheduler inert until a
// decomposition actually declares independence. A chain-shaped mission
// takes the literal sequential path, because the scheduler would buy it
// zero concurrency and change failure semantics for nothing in return.
func IsChainShaped(m *Mission) bool {
	// Python's sentinel is `prev_id = None`, which is distinct from a
	// milestone id of "". Go's "" was not, and LoadMission accepts
	// `"id": ""` (requiredString only checks presence and type), so a
	// hand-edited or foreign-written mission.json reaches it. The two
	// runtimes then chose DIFFERENT EXECUTION LANES for the same file —
	// sequential vs DAG, with different failure and context semantics
	// (adversarial mission-r1 MEDIUM). A separate flag restores the
	// three-valued distinction Python gets for free.
	first := true
	prevID := ""
	for _, ms := range m.Milestones {
		if first {
			first = false
			if len(ms.DependsOn) != 0 {
				return false
			}
		} else if len(ms.DependsOn) != 1 || ms.DependsOn[0] != prevID {
			return false
		}
		prevID = ms.ID
	}
	return true
}

func containsString(ss []string, s string) bool {
	for _, x := range ss {
		if x == s {
			return true
		}
	}
	return false
}
