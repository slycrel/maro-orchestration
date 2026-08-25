package skills

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/outcomepolicy"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// extractSystem is _EXTRACT_SYSTEM, byte for byte. It is a textwrap.dedent
// of a triple-quoted literal followed by .strip(), so the result has no
// leading indentation and no trailing newline — reproduced here as a plain
// literal because the dedent has already happened at authoring time and a
// runtime dedent would be a second implementation of the same thing.
const extractSystem = `You are a skill extraction agent.
Analyze successful goal completions and find patterns worth generalizing.
A skill is a step sequence that solved a class of problems and could be
reused for similar future goals.
Identify 1-3 reusable skill patterns. For each skill, extract:
- A short name (2-4 words)
- A description of what the skill does
- 2-4 trigger patterns (phrases in goals/steps that suggest this skill applies)
- A reusable step template (3-5 steps)
- A domain: one short lowercase phrase naming the subject area
  (e.g. "web-research", "git", "data-analysis")
- 3-6 tags: lowercase discovery keywords a future goal might contain
Respond ONLY with JSON, no prose, no markdown fences.
JSON shape:
{
  "skills": [
    {
      "name": "short name",
      "description": "what it does",
      "trigger_patterns": ["pattern1", "pattern2"],
      "steps_template": ["step1", "step2", "step3"],
      "domain": "subject-area",
      "tags": ["keyword1", "keyword2", "keyword3"]
    }
  ]
}`

// MintOptions carries the two values Python takes from module state, so a
// caller can drive extraction deterministically. Both are required in tests
// and both have production defaults at the call site.
type MintOptions struct {
	// NewID substitutes `str(uuid.uuid4())[:8]`.
	NewID func() string
	// Now substitutes `datetime.now(timezone.utc).isoformat()`.
	Now func() string
}

// ExtractSkills analyses successful outcomes and extracts reusable skill
// patterns, saving each one it accepts.
//
// The port's shape follows Python's exactly, including WHERE the try starts,
// because that boundary is behaviour. `outcomes_text` is built BEFORE the
// try, and it slices `o.get("summary", o.get("result_summary", ""))[:300]` —
// so an outcome whose `summary` key is present and null raises TypeError out
// of this function, while a malformed LLM reply one frame later is swallowed
// and answers an empty list. A port that wrapped the whole body would turn a
// caller-visible raise into a silent empty result.
//
// Note the DEFAULT in that chained `.get`: it applies only when `summary` is
// ABSENT. A present null does not fall back to result_summary — it is the
// value, and slicing it is the raise.
func ExtractSkills(ctx context.Context, ws string, outcomes []pyval.Obj,
	adapter llm.Adapter, opts MintOptions) ([]Skill, error) {
	if len(outcomes) == 0 {
		return nil, nil
	}

	// Verdict-preferred (SF-2): never crystallize skills from runs judged
	// goal-NOT-achieved (done != achieved). Verified-achieved runs are the
	// strongest examples and go FIRST; unjudged done runs are the weaker
	// fallback, because absence means "not judged" rather than "judged no".
	var candidates []pyval.Obj
	for _, o := range outcomes {
		ok, err := outcomepolicy.IsLearnable(outcomepolicy.Outcome(o))
		if err != nil {
			// is_learnable_outcome raises for an unhashable success_class
			// and this comprehension has no try around it.
			return nil, err
		}
		if ok {
			candidates = append(candidates, o)
		}
	}
	// `sort(key=lambda o: o.get("goal_achieved") is not True)` — a STABLE
	// sort on a bool, so judged-True first and everything else in its
	// original order. Identity, not truthiness: a numeric 1 sorts with the
	// unjudged rows.
	stableSortByFalseFirst(candidates, func(o pyval.Obj) bool {
		v, _ := o.Get("goal_achieved")
		b, isBool := v.(bool)
		return !(isBool && b) // "is not True"
	})
	successes := candidates
	if len(successes) > 20 {
		successes = successes[:20]
	}
	if len(successes) == 0 {
		return nil, nil
	}

	var parts []string
	for _, o := range successes {
		// Both carry an EMPTY-STRING default, and the default applies to
		// an absent key only — a present null still renders "None",
		// because that is what str(None) is.
		goal := getOr(o, "goal", "")
		taskType := getOr(o, "task_type", "")
		// The chained default, spelled exactly: result_summary is consulted
		// only when `summary` is ABSENT.
		summary, has := o.Get("summary")
		if !has {
			if rs, ok := o.Get("result_summary"); ok {
				summary = rs
			} else {
				summary = ""
			}
		}
		// `[:300]` on whatever that was — the RAW value, subscripted.
		// pyval.SliceHead is the whole operator rather than the string
		// case of it: a LIST summary slices to a list and renders as one,
		// a dict is a KEY lookup with a slice object and raises, and a
		// number is not subscriptable at all. The raise happens OUTSIDE
		// Python's try, so it leaves this function.
		sliced, serr := pyval.SliceHead(summary, 300)
		if serr != nil {
			return nil, serr
		}
		parts = append(parts, fmt.Sprintf("Goal: %s\nTask type: %s\nSummary: %s",
			pyval.Str(goal), pyval.Str(taskType), pyval.Str(sliced)))
	}
	outcomesText := strings.Join(parts, "\n\n")

	// The `if o.get("outcome_id")` filter is TRUTHINESS and runs BEFORE the
	// str()+slice, so a row with outcome_id 0 contributes nothing while one
	// with the string "0" contributes "0".
	sourceIDs := []string{}
	for _, o := range successes {
		if len(sourceIDs) == 10 {
			break
		}
		v, _ := o.Get("outcome_id")
		if !pyval.Truthy(v) {
			continue
		}
		sourceIDs = append(sourceIDs, pyval.Clip(pyval.Str(v), 8))
	}

	// Everything from here is inside Python's `try: ... except Exception:
	// pass`, which returns the empty list. The port answers (nil, nil) for
	// the same cases and reserves its error return for the raises ABOVE.
	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: extractSystem},
		{Role: "user", Content: "Successful goal completions to analyze:\n\n" +
			outcomesText},
		// Python passes `no_tools=True`; the Go adapter's utility mode is
		// the DEFAULT (AgentTools false, no Tools injected), so the flag
		// has no spelling here rather than being dropped.
	}, llm.Options{MaxTokens: 2048, Temperature: 0.3,
		Purpose: "skill extraction"})
	if err != nil {
		return nil, nil
	}

	data, jerr := jsonx.ObjectOrdered(llm.ContentOrEmpty(resp))
	if jerr != nil {
		return nil, nil
	}
	// `if data:` — an empty object is falsy and skips the whole block.
	if len(data) == 0 {
		return nil, nil
	}
	rawSkills, _ := data.Get("skills")
	if rawSkills == nil {
		// `data.get("skills", [])` gives None for a PRESENT null, and
		// `None[:3]` is a TypeError the bare except swallows. Absent gives
		// the [] default, which slices to nothing. Both answer empty.
		if _, present := data.Get("skills"); present {
			return nil, nil
		}
		rawSkills = pyval.List{}
	}
	list, isList := rawSkills.(pyval.List)
	if !isList {
		// A dict or a string here is `raw_skills[:3]` on a non-sliceable
		// (dict) or a slice of characters (str) — the first raises into the
		// bare except and the second yields str entries whose `.get` then
		// raises. Both end as the empty list.
		return nil, nil
	}
	if len(list) > 3 {
		list = list[:3]
	}

	now := opts.Now()
	var extracted []Skill
	for _, rsAny := range list {
		rs, isObj := rsAny.(pyval.Obj)
		if !isObj {
			// `rs.get(...)` on a non-mapping is an AttributeError, which
			// the bare except catches — abandoning the WHOLE loop, not just
			// this element, so anything already appended is discarded too.
			return nil, nil
		}
		sk := newSkill()
		sk.ID = opts.NewID()
		// `str(rs.get("name", "unnamed")).strip()` — the default applies
		// only when the key is ABSENT. A present null becomes the STRING
		// "None", which is truthy, so the skill is saved under that name.
		//
		// pyStrip, NOT strings.TrimSpace. Python's str.strip() removes 29
		// code points; Go's unicode.IsSpace knows 25 of them, missing
		// U+001C–U+001F (the FILE/GROUP/RECORD/UNIT separators). This
		// package already carries the correct spelling and NormalizeTags
		// three lines below already used it — so within this one function
		// `tags` was right and `name` was wrong, which is how it survived
		// review. Two live consequences, both measured:
		//
		//   - the separators land in the name and description, so
		//     ComputeSkillHash returns a different content_hash than
		//     CPython for the same reply, and the two runtimes write rows
		//     that disagree about their own identity;
		//   - a name that is ONLY separators strips to "" in CPython (the
		//     skill is dropped silently at the truthiness gate below), but
		//     stays truthy here, reaches SaveSkill, and is refused by
		//     ValidateSkillRow — and that refusal returns nil,nil at the
		//     bare-except, discarding skills already appended AND already
		//     written to disk. One unstripped byte turned a silent drop
		//     into a partial write with a disagreeing return value.
		sk.Name = pyStrip(pyval.Str(getOr(rs, "name", "unnamed")))
		sk.Description = pyStrip(pyval.Str(getOr(rs, "description", "")))
		trig, terr := strListOf(rs, "trigger_patterns")
		if terr != nil {
			return nil, nil
		}
		sk.TriggerPatterns = trig
		steps, serr := strListOf(rs, "steps_template")
		if serr != nil {
			return nil, nil
		}
		sk.StepsTemplate = steps
		sk.SourceLoopIDs = sourceIDs
		sk.CreatedAt = now
		sk.Origin = "crystallized"
		// strip THEN lower THEN truncate — the order is load-bearing: a
		// 40-character cut applied first would keep trailing spaces that
		// the strip removes.
		//
		// pyLower, not strings.ToLower: Go applies SIMPLE case mapping,
		// one rune in and one rune out, where Python's str.lower() maps
		// U+0130 to TWO runes ("i" + U+0307). Measured: "İSTANBUL".lower()
		// is "i̇stanbul" in CPython and "i̇stanbul" one rune shorter
		// here. That is a one-character difference INSIDE a 40-character
		// clip, so the divergence is not only the domain string but where
		// the truncation falls.
		sk.Domain = pyval.Clip(
			pyLower(pyStrip(pyval.Str(getOr(rs, "domain", "")))), 40)
		tagsRaw, _ := rs.Get("tags")
		sk.Tags = NormalizeTags(tagsRaw, 6)

		// Truthiness on both: an empty name or an empty template drops the
		// skill silently, which is why a null name surviving as "None"
		// matters — it passes this gate.
		if sk.Name != "" && len(sk.StepsTemplate) > 0 {
			if err := SaveSkill(ws, &sk); err != nil {
				return nil, nil
			}
			extracted = append(extracted, sk)
		}
	}
	return extracted, nil
}

// getOr is `d.get(key, def)`: the default applies to an ABSENT key only, and
// a present null is None rather than the default.
func getOr(o pyval.Obj, key string, def any) any {
	if v, ok := o.Get(key); ok {
		return v
	}
	return def
}

// strListOf is `[str(p).strip() for p in d.get(key, []) if str(p).strip()]`.
//
// The `if` runs str().strip() a SECOND time rather than reusing the value,
// which is invisible here but is the reason the filter and the element can
// never disagree. An error means the value was not iterable — Python's
// TypeError, which the caller's bare except swallows.
func strListOf(o pyval.Obj, key string) ([]string, error) {
	v, ok := o.Get(key)
	if !ok {
		return []string{}, nil
	}
	var items []any
	switch t := v.(type) {
	case pyval.List:
		items = []any(t)
	case string:
		// Iterating a str yields its CHARACTERS, and Python does that
		// without complaint — a "tags": "abc" reply becomes three
		// one-character entries rather than an error.
		for _, r := range t {
			items = append(items, string(r))
		}
	case pyval.Obj:
		// Iterating a dict yields its KEYS.
		for _, kv := range t {
			items = append(items, kv.Key)
		}
	default:
		return nil, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
			"'%s' object is not iterable", pyval.TypeName(v))}
	}
	out := []string{}
	for _, it := range items {
		// pyStrip — see the note at the name/description sites. This one
		// feeds trigger_patterns and steps_template, so an unstripped
		// separator both changes the content hash and survives into the
		// keyword matcher as part of a trigger.
		s := pyStrip(pyval.Str(it))
		if s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

// stableSortByFalseFirst is list.sort with a bool key: stable, False first.
//
// STABILITY is the contract — Python's sort keeps the ledger's order within
// each group, and that order decides which twenty rows survive the [:20].
// An earlier version hand-rolled an insertion sort on the stated grounds
// that sort.SliceStable could not be relied on for that, which is simply
// false: SliceStable is documented stable and that is its entire reason to
// exist beside sort.Slice. The false premise bought a quadratic sort with
// an O(keys) comparison over the WHOLE outcome ledger — measured 824ms at
// 6000 rows against CPython's 1.1ms, on a live ledger already at 1,524.
//
// The keys are computed ONCE, up front. list.sort() calls a Python key
// function exactly once per element and sorts the decorated values; calling
// key() inside the comparator instead is a second divergence from what is
// being ported, and it is the one that made the quadratic cost bite.
func stableSortByFalseFirst(xs []pyval.Obj, key func(pyval.Obj) bool) {
	keys := make([]bool, len(xs))
	for i, x := range xs {
		keys[i] = key(x)
	}
	idx := make([]int, len(xs))
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool { return !keys[idx[a]] && keys[idx[b]] })
	out := make([]pyval.Obj, len(xs))
	for i, j := range idx {
		out[i] = xs[j]
	}
	copy(xs, out)
}
