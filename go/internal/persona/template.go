package persona

import (
	"regexp"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// templateVarRE is Python's `re.compile(r"\{\{\s*(\w+)\s*\}\}")`.
//
// Both escapes in it are Unicode in Python's `re` and ASCII in Go's
// regexp, and both differences are REACHABLE in a persona body:
//
//	{{ café }}        Python captures "café"; a bare Go \w+ captures nothing
//	{{<U+00A0>goal }} Python's \s eats the NO-BREAK SPACE; Go's does not
//	{{<U+000B>goal }} same for LINE TABULATION, which Go's \s excludes
//	{{<U+001C>goal }} same for FILE SEPARATOR
//
// The direction is not symmetric. A missed \s spelling leaves `{{ goal }}`
// UNSUBSTITUTED in a system prompt, so the model is handed the template
// source; a missed \w spelling means extract_template_variables under-
// reports what a persona needs, which is what the lazy-context fetch keys
// on. Measured against CPython in template_diff_test.go.
var templateVarRE = regexp.MustCompile(
	`\{\{` + pytext.SpaceClass + `*(` + pytext.WordClass + `+)` + pytext.SpaceClass + `*\}\}`)

// ExtractTemplateVariables returns the `{{ variable }}` names in a template.
//
// Python returns a set, so this returns a map used as one — order is not
// part of the contract and the two callers only ever ask "is X in it".
func ExtractTemplateVariables(template string) map[string]bool {
	out := map[string]bool{}
	for _, m := range templateVarRE.FindAllStringSubmatch(template, -1) {
		out[m[1]] = true
	}
	return out
}

// TemplateContext is the lazy-fetch seam render_persona_template reaches
// through. Python spells it as three `from X import Y` statements inside
// the function body, each under its own `except Exception`, which makes the
// IMPORT FAILING part of the contract — that is the branch that produces
// "(unavailable)".
//
// A nil function here IS that failing import, so a caller with no memory
// wired renders the same "(unavailable)" CPython renders on a box where
// the import blows up. An empty result is a different answer, "(none)".
type TemplateContext struct {
	// StandingRules is `memory.load_standing_rules()`, returning each
	// rule's `.rule` text in store order. Only the first five are used.
	StandingRules func() ([]string, error)
	// RecentLessons is `memory.query_lessons(goal, n=3)`. Outcome is the
	// lesson's `.outcome` and decides the ✓/✗ glyph — anything that is not
	// exactly "done" gets ✗, the empty string included.
	RecentLessons func(goal string, n int) ([]Lesson, error)
}

// Lesson is the two fields render_persona_template reads off a TieredLesson.
type Lesson struct {
	Outcome string
	Lesson  string
}

// RenderTemplate substitutes `{{ variable }}` references in a persona body.
//
// TASK_TYPE IS A DEAD BRANCH IN CPYTHON, and this port reproduces its
// OUTCOME rather than its code. Python's line is
//
//	from intent import classify_intent
//	context["task_type"] = classify_intent(goal).lower() if goal else "general"
//
// and `intent.classify_intent` DOES NOT EXIST — measured on this tree, the
// import raises `ImportError: cannot import name 'classify_intent' from
// 'intent'`, so the `except Exception` arm fires on every call and the
// value is always the constant "general", goal or no goal. Writing a call
// to intent.Classify here would be a port of the source's INTENTION and a
// divergence from its behaviour, in the direction that costs money (a
// classify round trip per render). TestTaskTypeImportIsStillDead pins the
// premise so this comment cannot go quietly stale.
//
// The fast path is `if not variables: return template` — a template with no
// references never touches memory at all, which is the whole point of the
// lazy fetch and is why a broken StandingRules provider is invisible to
// most personas.
func RenderTemplate(template, goal string, tc TemplateContext) string {
	variables := ExtractTemplateVariables(template)
	if len(variables) == 0 {
		return template
	}

	context := map[string]string{}

	if variables["goal"] {
		// `goal or ""` — a falsy goal is the empty string either way, but
		// the idiom is what is written.
		context["goal"] = goal
	}

	if variables["task_type"] {
		context["task_type"] = "general" // see the doc comment above
	}

	if variables["standing_rules"] {
		context["standing_rules"] = "(unavailable)"
		if tc.StandingRules != nil {
			if rules, err := tc.StandingRules(); err == nil {
				if len(rules) == 0 {
					context["standing_rules"] = "(none)"
				} else {
					lines := make([]string, 0, 5)
					for _, r := range rules[:minInt(5, len(rules))] {
						lines = append(lines, "- "+r)
					}
					context["standing_rules"] = strings.Join(lines, "\n")
				}
			}
		}
	}

	if variables["recent_lessons"] {
		context["recent_lessons"] = "(unavailable)"
		if tc.RecentLessons != nil {
			if lessons, err := tc.RecentLessons(goal, 3); err == nil {
				if len(lessons) == 0 {
					context["recent_lessons"] = "(none)"
				} else {
					lines := make([]string, 0, len(lessons))
					for _, l := range lessons {
						mark := "✗"
						if l.Outcome == "done" {
							mark = "✓"
						}
						lines = append(lines, "- "+mark+" "+l.Lesson)
					}
					context["recent_lessons"] = strings.Join(lines, "\n")
				}
			}
		}
	}

	// `context.get(var, match.group(0))` — an UNKNOWN reference is left
	// exactly as written, whitespace and all, rather than blanked.
	return templateVarRE.ReplaceAllStringFunc(template, func(m string) string {
		name := templateVarRE.FindStringSubmatch(m)[1]
		if v, ok := context[name]; ok {
			return v
		}
		return m
	})
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// BuildSystemPrompt builds the full system prompt for a spawned persona.
//
// The header is a `textwrap.dedent(f"""...""")`, and the order of those two
// operations is the whole subtlety: the f-string INTERPOLATES FIRST, so a
// spec field carrying a newline changes the common margin dedent computes
// over the WHOLE header. Measured:
//
//	role = "R\nX"      the injected line is at column 0, so the margin
//	                   collapses to "" and the other four lines keep their
//	                   eight leading spaces
//	name = "n\n  m"    the injected line has two spaces, so the margin
//	                   becomes six and every other line loses six of eight
//
// A port that pre-dedents a constant template and interpolates afterwards
// gets the ordinary case right and both of these wrong, and the field this
// reaches is one an EVOLVER writes.
func BuildSystemPrompt(spec *Spec, goal string, tc TemplateContext) string {
	header := pytext.Strip(dedent(
		"        # Persona: " + spec.Role + "\n" +
			"\n" +
			"        You are operating as **" + spec.Role + "** (" + spec.Name + ").\n" +
			"        Communication style: " + spec.CommunicationStyle + "\n" +
			"        Memory scope: " + spec.MemoryScope + "\n"))

	if goal != "" {
		header += "\n\nCurrent goal: " + goal
	}

	body := pytext.Strip(spec.SystemPrompt)
	if body != "" {
		body = RenderTemplate(body, goal, tc)
		return header + "\n\n" + body
	}
	return header
}

// dedent is CPython 3.13+/3.14 textwrap.dedent, transcribed:
//
//	lines = text.split('\n')
//	non_blank_lines = [l for l in lines if l and not l.isspace()]
//	l1 = min(non_blank_lines, default=''); l2 = max(..., default='')
//	margin = 0
//	for margin, c in enumerate(l1):
//	    if c != l2[margin] or c not in ' \t':
//	        break
//	return '\n'.join([l[margin:] if not l.isspace() else '' for l in lines])
//
// Three things a rewrite gets wrong. `str.isspace()` is UNICODE, so a line
// holding only U+000B or U+001C is BLANK to dedent and is normalized to
// "" — Go's unicode.IsSpace disagrees about U+001C..U+001F, which is why
// pytext.IsSpace is used. `min`/`max` are lexicographic over the whole
// line, not over its indent, so a tab-indented line and a space-indented
// one produce margin 0 by construction. And every index is a CODE POINT.
//
// The loop variable escaping the loop (Python's `for margin, c in ...`
// leaves margin at the break index) is reproduced with an explicit
// variable; it cannot fall through to len(l1) here because a line that is
// not blank and not isspace always holds a character outside " \t".
func dedent(text string) string {
	lines := strings.Split(text, "\n")

	var l1, l2 []rune
	first := true
	for _, l := range lines {
		if l == "" || isSpaceStr(l) {
			continue
		}
		rs := []rune(l)
		if first {
			l1, l2, first = rs, rs, false
			continue
		}
		if runesLess(rs, l1) {
			l1 = rs
		}
		if runesLess(l2, rs) {
			l2 = rs
		}
	}

	margin := 0
	for i, c := range l1 {
		margin = i
		if c != l2[i] || (c != ' ' && c != '\t') {
			break
		}
	}

	out := make([]string, len(lines))
	for i, l := range lines {
		if isSpaceStr(l) {
			out[i] = ""
			continue
		}
		rs := []rune(l)
		if margin >= len(rs) {
			out[i] = ""
			continue
		}
		out[i] = string(rs[margin:])
	}
	return strings.Join(out, "\n")
}

// isSpaceStr is Python's `str.isspace()`: non-empty and every character a
// whitespace character. The empty string is False there, which is why
// dedent's filter tests `l and not l.isspace()` rather than just the
// second half.
func isSpaceStr(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !pytext.IsSpace(r) {
			return false
		}
	}
	return true
}

func runesLess(a, b []rune) bool {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return len(a) < len(b)
}

// skepticAddition is _SKEPTIC_ADDITION, verbatim.
const skepticAddition = "\n\nSKEPTIC MODE: Before proposing any plan or answer, " +
	"briefly list 2-3 ways it could fail, go wrong, or miss the mark. Be specific " +
	"to this task — not generic warnings. Then proceed with your best answer " +
	"accounting for those risks."

// ApplySkepticModifier returns a copy of spec with the skeptic framing
// APPENDED to the system prompt.
//
// Python's docstring says "prepended" and its code says
// `spec.system_prompt + _SKEPTIC_ADDITION`. The code wins; the docstring is
// wrong upstream and is not repeated here.
//
// `dataclasses.replace` builds a new instance whose list fields are the
// SAME objects, and the struct copy below shares its slices for the same
// reason. That is the Python behaviour, not an oversight.
func ApplySkepticModifier(spec *Spec) *Spec {
	out := *spec
	out.SystemPrompt = spec.SystemPrompt + skepticAddition
	return &out
}
