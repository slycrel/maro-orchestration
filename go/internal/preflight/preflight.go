// Package preflight ports `src/pre_flight.py` — the cheap plan review
// that runs between decomposition and execution.
//
// It plays skeptic on the proposed step list: one small LLM call with a
// critic prompt, producing recommendations rather than gates. The loop
// proceeds regardless of what comes back, which is why almost everything
// here is about DEGRADING well.
//
// Two halves are deliberately not ported and are named here rather than
// left to be discovered:
//
//   - `_build_reviewers`, which constructs adapters in cost order. It is
//     adapter construction and key resolution, the same boundary every
//     other tranche in this port has left to the caller; ReviewPlan takes
//     the candidate list instead.
//   - `_preflight_stats_main`, the argparse entry point for
//     maro-preflight-stats. CalibrationStats — the part that computes
//     anything — is here.
//
// The behaviour worth stating before the code is what happens when the
// reviewer says nothing usable. A dead key that BUILDS but fails every
// call is not a hypothetical: it returned scope="unknown" for months and
// wrote 488 calibration entries with zero flags. Every failure path in
// this file therefore lands on the heuristic estimate, and "unknown"
// survives in exactly two places — an empty step list, and a heuristic
// that itself raised.
package preflight

import (
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Flag is pre_flight.PlanFlag.
//
// Message and the flag values behind it are `any` because Python's
// dataclass annotates them `str` and never enforces it: `a.get("issue",
// "")` hands back whatever the reviewer's JSON held, and an integer
// issue reaches format_for_log as an integer and is rendered by the
// f-string. Typing it `string` here would silently coerce a value the
// original passes through.
type Flag struct {
	// Kind is "assumption" | "milestone" | "unknown" | "class".
	Kind string
	// Step is a 1-based step index; 0 means the whole plan.
	Step    int
	Message any
	// Severity is "info" | "warn".
	Severity string
}

// Review is pre_flight.PlanReview.
type Review struct {
	// Scope is "narrow" | "medium" | "wide" | "unknown".
	Scope                string
	ScopeNote            any
	Flags                []Flag
	MilestoneStepIndices []int
	// Raw is the reviewer's own output, kept for debugging.
	Raw string
}

// HasConcerns is the `has_concerns` property.
func (r Review) HasConcerns() bool {
	if r.Scope == "wide" {
		return true
	}
	for _, f := range r.Flags {
		if f.Severity == "warn" {
			return true
		}
	}
	return false
}

// Summary is `summary()`.
//
// The milestone list is rendered as Python renders a list of ints, because
// that is what the f-string does with it: `milestone_candidates=[1, 3]`,
// with the space after the comma.
func (r Review) Summary() string {
	parts := []string{"scope=" + r.Scope}
	if len(r.MilestoneStepIndices) > 0 {
		nums := make([]string, len(r.MilestoneStepIndices))
		for i, n := range r.MilestoneStepIndices {
			nums[i] = fmt.Sprint(n)
		}
		parts = append(parts, "milestone_candidates=["+
			strings.Join(nums, ", ")+"]")
	}
	warn := 0
	for _, f := range r.Flags {
		if f.Severity == "warn" {
			warn++
		}
	}
	if warn != 0 {
		parts = append(parts, fmt.Sprintf("warnings=%d", warn))
	}
	return strings.Join(parts, " ")
}

// FormatForLog is `format_for_log()`.
//
// `if self.scope_note:` is a TRUTHINESS test, not a nil check: an empty
// string, a zero and an empty list all skip the line.
func (r Review) FormatForLog() string {
	lines := []string{"Pre-flight review: " + r.Summary()}
	if pyval.Truthy(r.ScopeNote) {
		lines = append(lines, "  Scope: "+pyval.Str(r.ScopeNote))
	}
	for _, f := range r.Flags {
		stepStr := "plan"
		if f.Step != 0 {
			stepStr = fmt.Sprintf("step %d", f.Step)
		}
		lines = append(lines, fmt.Sprintf("  [%s] %s: %s",
			f.Kind, stepStr, pyval.Str(f.Message)))
	}
	return strings.Join(lines, "\n")
}

// wideKeywords and narrowKeywords are matched as SUBSTRINGS of the joined
// step text, which is what `kw in text_lower` does. "all" therefore fires
// on "install", "finally" and "call" — a property of the original, not a
// bug in the port, and the fixtures pin it.
var wideKeywords = []string{"deploy", "refactor", "migrate", "rewrite",
	"redesign", "overhaul", "all"}

var narrowKeywords = []string{"fetch", "check", "read", "list", "get",
	"show", "status"}

// HeuristicScope is `_heuristic_scope`: the scope estimate when no
// reviewer answers.
//
// The branch ORDER is the specification. A three-step plan containing a
// wide keyword falls past the first arm into the second and comes out
// "wide"; an eight-step plan of nothing but narrow keywords comes out
// "wide" too, because `n >= 8` is checked before the narrow arm.
func HeuristicScope(steps []string) string {
	n := len(steps)
	textLower := pytext.Lower(strings.Join(steps, " "))
	hasWide := containsAny(textLower, wideKeywords)
	hasNarrow := containsAny(textLower, narrowKeywords)

	if n <= 3 && !hasWide {
		return "narrow"
	}
	if n >= 8 || hasWide {
		return "wide"
	}
	if hasNarrow && n <= 5 {
		return "narrow"
	}
	return "medium"
}

func containsAny(s string, kws []string) bool {
	for _, kw := range kws {
		if strings.Contains(s, kw) {
			return true
		}
	}
	return false
}

// Logger is the subset of `log` this file uses. It is injected because the
// two lines it writes are OBSERVABLE decisions — one says a reviewer's
// answer was off-vocabulary and names it, the other says the answer was
// unparseable and names the exception — and a differential that could not
// see them would be comparing the return value of a function whose whole
// job is to degrade quietly.
type Logger interface {
	Info(format string, args ...any)
	Debug(format string, args ...any)
	Log(level string, format string, args ...any)
}

// ParseReview is `_parse_review`: a reviewer response turned into a
// Review, or nil if it is unusable.
//
// Nil means "this reviewer failed", and the caller moves to the next
// candidate — so a garbled answer is indistinguishable from no answer, on
// purpose.
//
// The "unparseable" log line interpolates the exception with %s, so what
// it writes is `str(exc)` — the SENTENCE, not the class. Every sentence
// this file can produce is spelled out exactly, with one named exception:
// see jsonDecodeErr.
func ParseReview(raw string, log Logger) (*Review, error) {
	if raw == "" {
		return nil, nil
	}
	// Strip markdown fences if present.
	//
	// The trailing-fence check runs on the text with the FIRST LINE
	// already removed, and splitlines has dropped a trailing newline —
	// so "```json\n{}\n```\n" ends with the fence here and "```json\n{}"
	// does not. A response whose closing fence is followed by prose keeps
	// the fence and fails to parse, which is the same "next reviewer"
	// answer.
	if strings.HasPrefix(raw, "```") {
		lines := pytext.SplitLines(raw)
		if len(lines) > 0 {
			lines = lines[1:]
		}
		raw = strings.Join(lines, "\n")
		if strings.HasSuffix(raw, "```") {
			raw = pytext.Strip(raw[:len(raw)-3])
		}
	}

	rev, err := parseBody(raw, log)
	if err != nil {
		log.Info("pre_flight: reviewer response unparseable (%s)",
			err.Error())
	}
	return rev, err
}

// parseBody is the body of the try. Every return path is an exception
// the caller's single `except Exception` would have caught, which is why
// there is one error type and one handler rather than a class per arm.
func parseBody(raw string, log Logger) (*Review, error) {
	decoded, err := pyval.LoadsOrdered(raw)
	if err != nil {
		return nil, jsonDecodeErr()
	}
	data, ok := decoded.(pyval.Obj)
	if !ok {
		// `data.get(...)` on a list, a number or a string is an
		// AttributeError, and it is the FIRST get that raises.
		return nil, attrErr(decoded, "get")
	}
	scope, _ := objGet(data, "scope", "")
	scopeStr, isStr := scope.(string)
	// EQUIVALENT MUTANT (kept, marked `equivalent`): dropping the type
	// test changes nothing, because scopeStr is the zero string whenever
	// isStr is false and the empty string is already off-vocabulary. It
	// is written because Python's `scope not in (...)` is a membership
	// test over values, not a string comparison, and spelling it as a
	// string comparison alone would be a coincidence that holds.
	if !isStr || (scopeStr != "narrow" && scopeStr != "medium" &&
		scopeStr != "wide") {
		log.Info("pre_flight: reviewer scope %s off-vocabulary — "+
			"treating as failed", pyval.Repr(scope))
		return nil, nil
	}
	scopeNote, _ := objGet(data, "scope_note", "")

	// Both start as EMPTY LISTS, not as nil: Python's dataclass defaults
	// are `field(default_factory=list)` and an absent flag list is `[]`
	// there, which is a different value from None everywhere it is
	// rendered or serialised.
	flags := []Flag{}
	milestone := []int{}

	items, err := iterate(data, "assumptions")
	if err != nil {
		return nil, err
	}
	for _, a := range items {
		step, err := getStep(a, "step")
		if err != nil {
			return nil, err
		}
		msg, err := getFrom(a, "issue", "")
		if err != nil {
			return nil, err
		}
		flags = append(flags, Flag{Kind: "assumption", Step: step,
			Message: msg, Severity: "warn"})
	}

	items, err = iterate(data, "milestone_candidates")
	if err != nil {
		return nil, err
	}
	for _, m := range items {
		idx, err := getStep(m, "step")
		if err != nil {
			return nil, err
		}
		// EQUIVALENT MUTANT (kept, marked `equivalent`): moving this
		// below the message read changes nothing, because a read that
		// raises discards the whole Review two lines later.
		milestone = append(milestone, idx)
		msg, err := getFrom(m, "reason", "")
		if err != nil {
			return nil, err
		}
		flags = append(flags, Flag{Kind: "milestone", Step: idx,
			Message: msg, Severity: "warn"})
	}

	items, err = iterate(data, "unknown_unknowns")
	if err != nil {
		return nil, err
	}
	for _, u := range items {
		// No `.get` here: the item itself is the message, whatever it is.
		flags = append(flags, Flag{Kind: "unknown", Step: 0, Message: u,
			Severity: "info"})
	}

	items, err = iterate(data, "class_gaps")
	if err != nil {
		return nil, err
	}
	for _, c := range items {
		step, err := getStep(c, "step")
		if err != nil {
			return nil, err
		}
		msg, err := getFrom(c, "issue", "")
		if err != nil {
			return nil, err
		}
		flags = append(flags, Flag{Kind: "class", Step: step,
			Message: msg, Severity: "warn"})
	}

	return &Review{Scope: scopeStr, ScopeNote: scopeNote, Flags: flags,
		MilestoneStepIndices: milestone, Raw: raw}, nil
}

// jsonDecodeErr stands for CPython's json.JSONDecodeError.
//
// DIVERGENCE, named: CPython's sentence is "Expecting value: line 1
// column 4 (char 3)" — a scanner position this port's decoder does not
// carry, and reproducing it means reimplementing CPython's error
// positions, which is a larger job than the one file it would serve. The
// CLASS is right, and the class is what the code branches on; the
// differential canonicalises this one sentence on both sides and compares
// every other message exactly. If a second site ever needs the position,
// that is the moment to build it in pyval rather than here.
// EQUIVALENT MUTANT (kept, marked `equivalent`): the CLASS here is
// unobservable. The only caller logs `str(exc)`, and in CPython
// JSONDecodeError is a ValueError subclass, so no `except` in the ported
// source can tell them apart either. It is spelled correctly anyway,
// because the next caller might.
func jsonDecodeErr() error {
	return &pyval.PyErr{Class: "JSONDecodeError", Msg: "<JSONDecodeError>"}
}

// attrErr is `'<type>' object has no attribute '<name>'`.
func attrErr(v any, name string) error {
	return &pyval.PyErr{Class: "AttributeError",
		Msg: fmt.Sprintf("'%s' object has no attribute '%s'",
			pyval.TypeName(v), name)}
}

// notIterable is `'<type>' object is not iterable`, raised by the `for`
// statement itself rather than by anything in its body.
func notIterable(v any) error {
	return &pyval.PyErr{Class: "TypeError",
		Msg: fmt.Sprintf("'%s' object is not iterable", pyval.TypeName(v))}
}

// objGet is `d.get(key, default)` over a decoded object.
func objGet(o pyval.Obj, key string, def any) (any, bool) {
	if v, ok := o.Get(key); ok {
		return v, true
	}
	return def, false
}

// iterate is `for x in d.get(key, [])`, with Python's answer for a value
// that is not a list.
//
// The three non-list cases are three different exceptions and they are all
// reachable from a reviewer's JSON:
//
//   - an OBJECT iterates its KEYS, so every item is a string and the
//     first `.get` on one is an AttributeError;
//   - a STRING iterates its characters, same ending;
//   - a number, a bool or null is not iterable at all, which is a
//     TypeError raised by the `for` itself.
//
// The first two are reported here as the exception their FIRST item
// produces, because nothing between the loop header and that `.get` can
// raise — except in `unknown_unknowns`, which has no `.get` and therefore
// accepts all three happily. That asymmetry is the original's.
func iterate(o pyval.Obj, key string) ([]any, error) {
	v, _ := objGet(o, key, pyval.List{})
	switch t := v.(type) {
	case pyval.List:
		return []any(t), nil
	case pyval.Obj:
		out := make([]any, 0, len(t))
		for _, f := range t {
			out = append(out, f.Key)
		}
		return out, nil
	case string:
		out := make([]any, 0, len(t))
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	}
	return nil, notIterable(v)
}

// getFrom is `item.get(key, default)` where item came out of a list that
// may hold anything.
func getFrom(item any, key string, def any) (any, error) {
	o, ok := item.(pyval.Obj)
	if !ok {
		return nil, attrErr(item, "get")
	}
	v, _ := objGet(o, key, def)
	return v, nil
}

// getStep is `int(item.get(key, 0))` — two exceptions in one expression.
//
// The AttributeError comes from an item that is not a mapping; the
// int() raises ValueError for a string that is not a number and
// TypeError for None or a list, and pyval.Int is where CPython's exact
// domain lives.
func getStep(item any, key string) (int, error) {
	v, err := getFrom(item, key, 0)
	if err != nil {
		return 0, err
	}
	n, err := pyval.Int(v)
	if err != nil {
		return 0, err
	}
	return n, nil
}
