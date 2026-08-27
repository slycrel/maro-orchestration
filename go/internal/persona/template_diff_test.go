package persona

import (
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// Code points, not glyphs: a fixture whose whole subject is WHICH separator
// it carries cannot be reviewed if the answer is "look closely at that
// space".
var (
	nbspT = string(rune(0x00A0)) // NO-BREAK SPACE — \s to Python, not to Go
	vtabT = string(rune(0x000B)) // LINE TABULATION — likewise
	fsT   = string(rune(0x001C)) // FILE SEPARATOR — likewise
	emT   = string(rune(0x2003)) // EM SPACE
)

var templateCorpus = []string{
	"Use {{ standing_rules }}. Goal: {{ goal }}.",
	"{{goal}}",
	"{{  goal  }}",
	"{{ go al }}",   // a space inside: \w+ cannot span it, so no match
	"{{ café }}",    // \w is UNICODE in Python; a bare Go \w+ finds nothing
	"{{ 研究 }}",      // the same, outside Latin
	"{{ naïve }}",   // ...and mixed with ASCII, where a byte port half-matches
	"{{ _x1 }}",     // underscore and digit, which both engines take
	"{{ ünknown }}", // a non-ASCII name that is NOT in the context: it must
	// survive the substitution pass untouched
	"{{" + nbspT + "goal" + nbspT + "}}",
	"{{" + vtabT + "goal" + vtabT + "}}",
	"{{" + fsT + "goal" + fsT + "}}",
	"{{" + emT + "goal" + emT + "}}",
	"{{ goal}}{{ goal }}", // two matches, no gap
	"{{ }}",               // \w+ needs one character
	"{ { goal } }",
	"{{ goal }} and {{ unknown }}",
	"{{{ goal }}}",
	"{{ goal }} {{ standing_rules }} {{ recent_lessons }} {{ task_type }}",
	"",
	"no references at all",
}

const templateProbeSrc = `
import json, sys
import persona
tmpls, goal = json.loads(sys.argv[1])
print(json.dumps({
  "vars": [sorted(persona.extract_template_variables(t)) for t in tmpls],
  "render": [persona.render_persona_template(t, goal=goal) for t in tmpls],
}, ensure_ascii=False))
`

// TestTemplateVariablesMatchCPython drives extract + render against the
// interpreter over a corpus built for the two escapes in the pattern.
//
// The render half is run with NO memory available — the probe imports
// persona in a process whose `memory` import works but whose store is
// empty, so standing_rules and recent_lessons render "(none)". The Go side
// is given providers that return empty for the same reason, which is what
// makes the two comparable; the "(unavailable)" arm is a separate test
// because it needs the import to FAIL.
func TestTemplateVariablesMatchCPython(t *testing.T) {
	ws := t.TempDir()
	probe := pyprobe.Probe{Marker: "persona.py", Workspace: ws}
	var want struct {
		Vars   [][]string `json:"vars"`
		Render []string   `json:"render"`
	}
	probe.RunJSON(t, templateProbeSrc, &want,
		pyprobe.Arg(t, []any{templateCorpus, "my goal"}))
	if len(want.Vars) != len(templateCorpus) {
		t.Fatalf("probe returned %d rows for %d templates",
			len(want.Vars), len(templateCorpus))
	}

	// CLAIM: the empty store renders "(none)", not "(unavailable)". If it
	// ever renders "(unavailable)" the probe's memory import broke and the
	// whole render column is measuring the except arm.
	if !strings.Contains(want.Render[0], "(none)") {
		t.Fatalf("CLAIM moved: with an empty workspace CPython rendered "+
			"standing_rules as %q, not \"(none)\" — the probe is measuring "+
			"the exception arm", want.Render[0])
	}
	// CLAIM: the Unicode \w rows really do capture a non-ASCII name.
	if !reflect.DeepEqual(want.Vars[4], []string{"café"}) {
		t.Fatalf("CLAIM moved: CPython no longer captures a non-ASCII \\w "+
			"name (got %v) — the corpus cannot separate the two engines",
			want.Vars[4])
	}
	// CLAIM: the Unicode \s rows really do substitute.
	for _, i := range []int{9, 10, 11, 12} {
		if want.Render[i] != "my goal" {
			t.Fatalf("CLAIM moved: CPython's \\s no longer eats %q in "+
				"template %d (rendered %q)", templateCorpus[i], i, want.Render[i])
		}
	}

	tc := TemplateContext{
		StandingRules: func() ([]string, error) { return nil, nil },
		RecentLessons: func(string, int) ([]Lesson, error) { return nil, nil },
	}
	var nonASCIIVar, unicodeSpace int
	for i, tmpl := range templateCorpus {
		gotVars := ExtractTemplateVariables(tmpl)
		var names []string
		for n := range gotVars {
			names = append(names, n)
		}
		sort.Strings(names)
		w := want.Vars[i]
		if len(names) == 0 && len(w) == 0 {
			// reflect.DeepEqual(nil, []string{}) is false; both are "no
			// variables" and the distinction is not Python's.
		} else if !reflect.DeepEqual(names, w) {
			t.Errorf("extract_template_variables(%q)\n  go %v\n  py %v", tmpl, names, w)
		}
		for _, n := range w {
			if !isASCIIStr(n) {
				nonASCIIVar++
			}
		}
		if got := RenderTemplate(tmpl, "my goal", tc); got != want.Render[i] {
			t.Errorf("render_persona_template(%q)\n  go %q\n  py %q",
				tmpl, got, want.Render[i])
		}
		if len(w) > 0 && !isASCIIStr(tmpl) && isASCIIStr(strings.Join(w, "")) {
			unicodeSpace++
		}
	}
	if nonASCIIVar == 0 {
		t.Fatal("no MATCHING row captured a non-ASCII variable name, so the " +
			"corpus cannot reach the \\w divergence")
	}
	if unicodeSpace == 0 {
		t.Fatal("no MATCHING row has a non-ASCII template with an ASCII " +
			"variable name, so the corpus cannot reach the \\s divergence")
	}
}

func isASCIIStr(s string) bool {
	for _, r := range s {
		if r > 127 {
			return false
		}
	}
	return true
}

// The `{{ task_type }}` branch is DEAD in CPython: `from intent import
// classify_intent` raises ImportError on every call, so the value is the
// constant "general" and the classifier is never run.
//
// This pins the PREMISE, not the port. If someone adds classify_intent to
// intent.py, CPython starts classifying and this port does not — so the
// test must fail at that moment rather than when a run mysteriously costs
// a model call more.
func TestTaskTypeImportIsStillDead(t *testing.T) {
	var py struct {
		HasAttr   bool   `json:"has_attr"`
		ImportErr string `json:"import_err"`
		WithGoal  string `json:"with_goal"`
		NoGoal    string `json:"no_goal"`
	}
	pyprobe.Probe{Marker: "persona.py", Workspace: t.TempDir()}.RunJSON(t, `
import json
import intent, persona
try:
    from intent import classify_intent
    err = ""
except Exception as e:
    err = "%s: %s" % (type(e).__name__, e)
print(json.dumps({
  "has_attr": hasattr(intent, "classify_intent"),
  "import_err": err,
  "with_goal": persona.render_persona_template("[{{ task_type }}]", goal="build a thing"),
  "no_goal": persona.render_persona_template("[{{ task_type }}]", goal=""),
}, ensure_ascii=False))
`, &py)

	if py.HasAttr || py.ImportErr == "" {
		t.Fatalf("intent.classify_intent EXISTS now (has_attr=%v import_err=%q). "+
			"CPython's task_type branch is live again and this port still "+
			"hardcodes \"general\" — the divergence is real as of this "+
			"failure and needs a decision.", py.HasAttr, py.ImportErr)
	}
	if py.WithGoal != "[general]" || py.NoGoal != "[general]" {
		t.Fatalf("CPython's task_type is no longer the constant \"general\": "+
			"with a goal %q, without one %q", py.WithGoal, py.NoGoal)
	}
	tc := TemplateContext{}
	for _, goal := range []string{"build a thing", ""} {
		if got := RenderTemplate("[{{ task_type }}]", goal, tc); got != "[general]" {
			t.Errorf("port rendered task_type as %q for goal %q", got, goal)
		}
	}
}

// The three-way answer for standing_rules and recent_lessons: rendered,
// "(none)" for an empty result, "(unavailable)" for a raising provider.
// CPython's third arm is an import or a call that throws; here it is a nil
// or erroring function, and both sides are measured.
func TestStandingRulesAndLessonsRenderingMatchesCPython(t *testing.T) {
	var py struct {
		Full  string `json:"full"`
		Empty string `json:"empty"`
		Err   string `json:"err"`
	}
	pyprobe.Probe{Marker: "persona.py", Workspace: t.TempDir()}.RunJSON(t, `
import json
import memory, persona
class R:
    def __init__(self, r): self.rule = r
class L:
    def __init__(self, o, t): self.outcome = o; self.lesson = t
TMPL = "SR:\n{{ standing_rules }}\nRL:\n{{ recent_lessons }}"
memory.load_standing_rules = lambda: [R("r%d" % i) for i in range(7)]
memory.query_lessons = lambda g, n=3: [L("done", "L1"), L("stuck", "L2"), L("", "L3")]
full = persona.render_persona_template(TMPL, goal="g")
memory.load_standing_rules = lambda: []
memory.query_lessons = lambda g, n=3: []
empty = persona.render_persona_template(TMPL, goal="g")
def boom(*a, **k): raise RuntimeError("nope")
memory.load_standing_rules = boom
memory.query_lessons = boom
err = persona.render_persona_template(TMPL, goal="g")
print(json.dumps({"full": full, "empty": empty, "err": err}, ensure_ascii=False))
`, &py)

	// CLAIM: the fixture really does exercise the [:5] cap and both glyphs.
	if strings.Count(py.Full, "- r") != 5 {
		t.Fatalf("CLAIM moved: CPython rendered %d standing rules, not the "+
			"first 5 of 7 (%q)", strings.Count(py.Full, "- r"), py.Full)
	}
	if !strings.Contains(py.Full, "✓") || !strings.Contains(py.Full, "✗") {
		t.Fatalf("CLAIM moved: the lesson fixture no longer reaches both "+
			"glyphs (%q)", py.Full)
	}

	const tmpl = "SR:\n{{ standing_rules }}\nRL:\n{{ recent_lessons }}"
	full := RenderTemplate(tmpl, "g", TemplateContext{
		StandingRules: func() ([]string, error) {
			return []string{"r0", "r1", "r2", "r3", "r4", "r5", "r6"}, nil
		},
		RecentLessons: func(string, int) ([]Lesson, error) {
			return []Lesson{{"done", "L1"}, {"stuck", "L2"}, {"", "L3"}}, nil
		},
	})
	if full != py.Full {
		t.Errorf("rendered\n  go %q\n  py %q", full, py.Full)
	}
	empty := RenderTemplate(tmpl, "g", TemplateContext{
		StandingRules: func() ([]string, error) { return nil, nil },
		RecentLessons: func(string, int) ([]Lesson, error) { return nil, nil },
	})
	if empty != py.Empty {
		t.Errorf("empty\n  go %q\n  py %q", empty, py.Empty)
	}
	errText := RenderTemplate(tmpl, "g", TemplateContext{})
	if errText != py.Err {
		t.Errorf("unavailable (nil provider vs raising call)\n  go %q\n  py %q",
			errText, py.Err)
	}
	if py.Empty == py.Err {
		t.Fatal("CLAIM moved: \"(none)\" and \"(unavailable)\" are now the " +
			"same string, so the three-way answer is not being measured")
	}
}

// dedent's whole point is that it runs AFTER interpolation, so a field
// carrying a newline moves the margin for the entire header.
const buildProbeSrc = `
import json, sys
import persona
cases, goal = json.loads(sys.argv[1])
out = []
for name, role, style, scope, body in cases:
    s = persona.PersonaSpec(name=name, role=role, model_tier="mid",
                            tool_access=[], memory_scope=scope,
                            communication_style=style, system_prompt=body,
                            hooks=[], composes=[])
    out.append(persona.build_persona_system_prompt(s, goal=goal))
print(json.dumps(out, ensure_ascii=False))
`

func TestBuildSystemPromptMatchesCPython(t *testing.T) {
	// [name, role, style, scope, body]
	cases := [][5]string{
		{"n", "R", "cs", "session", "BODY"},
		// THE dedent case: an injected newline at column 0 collapses the
		// margin to "" and the header keeps its eight-space indent.
		{"n", "R\nX", "cs", "session", "BODY"},
		// ...and one at column 2, which drags the margin down to six.
		{"n\n  m", "R", "cs", "session", "BODY"},
		// A TAB-indented injected line: min/max are lexicographic over the
		// whole line, and '\t' < ' ', so the margin goes to 0.
		{"n", "R", "cs\n\tdeep", "session", "BODY"},
		// A trailing newline in the LAST interpolated field, so the header
		// gains a blank line the strip then eats.
		{"n", "R", "cs", "s\n", "BODY"},
		// A line that is only U+000B: str.isspace() is True, so dedent
		// treats it as blank and normalizes it to "". Go's unicode.IsSpace
		// agrees here but not on U+001C, which the next row covers.
		{"n", "R" + "\n" + vtabT + "\nX", "cs", "session", "BODY"},
		{"n", "R" + "\n" + fsT + "\nX", "cs", "session", "BODY"},
		// Empty fields, which still render their labels.
		{"n", "", "", "", "BODY"},
		// Bodies: absent, whitespace-only, and template-bearing.
		{"n", "R", "cs", "session", ""},
		{"n", "R", "cs", "session", "  \n "},
		{"n", "R", "cs", "session", "{{ goal }}"},
		{"n", "R", "cs", "session", nbspT + "BODY" + nbspT},
		// Non-ASCII in every slot, so the code-point arithmetic is exercised.
		{"名", "café 研究", "直接", "session", "本文"},
	}
	probeCases := make([]any, len(cases))
	for i, c := range cases {
		probeCases[i] = []string{c[0], c[1], c[2], c[3], c[4]}
	}

	for _, goal := range []string{"G", ""} {
		var want []string
		personaProbe(t).RunJSON(t, buildProbeSrc, &want,
			pyprobe.Arg(t, []any{probeCases, goal}))
		if len(want) != len(cases) {
			t.Fatalf("probe returned %d rows for %d cases", len(want), len(cases))
		}
		// CLAIM: the two dedent-defeating rows really do keep their indent.
		if !strings.Contains(want[1], "\n        You are operating") {
			t.Fatalf("CLAIM moved: a newline in `role` no longer collapses the "+
				"dedent margin (%q)", want[1])
		}
		if !strings.Contains(want[2], "\n      You are operating") {
			t.Fatalf("CLAIM moved: a two-space-indented newline in `name` no "+
				"longer moves the margin to six (%q)", want[2])
		}
		var indented int
		for i, c := range cases {
			spec := &Spec{Name: c[0], Role: c[1], CommunicationStyle: c[2],
				MemoryScope: c[3], SystemPrompt: c[4]}
			got := BuildSystemPrompt(spec, goal, TemplateContext{})
			if got != want[i] {
				t.Errorf("build_persona_system_prompt case %d goal=%q\n  go %q\n  py %q",
					i, goal, got, want[i])
			}
			if strings.Contains(want[i], "\n    ") {
				indented++
			}
		}
		if indented < 2 {
			t.Fatalf("goal=%q: only %d rows kept an indent, so the corpus is "+
				"not exercising the post-interpolation dedent", goal, indented)
		}
	}
}

// dedent itself, over shapes build_persona_system_prompt cannot reach —
// the helper is separable and the header only ever produces a few of its
// inputs.
func TestDedentMatchesCPython(t *testing.T) {
	corpus := []string{
		"        a\n\n        b\n",
		"        a\nb\n        c\n",
		"        a\n   \n        b\n",
		"        a\n\t\n        b\n",
		"        a\n" + vtabT + "\n        b\n",
		"        a\n" + fsT + "\n        b\n",
		"        a\n" + nbspT + "\n        b\n",
		"\ta\n\tb\n",
		"  \ta\n\t  b\n",
		"",
		"   ",
		"\n\n\n",
		"        a\n        \n        b",
		"    ",
		"    x",
		"        a\n     b\n",
		"  " + nbspT + "a\n  " + nbspT + "b\n", // NBSP is not " " or "\t": margin stops at 2
		"        # Persona: R\n\n        You are operating as **R** (n).\n",
	}
	var want []string
	personaProbe(t).RunJSON(t, `
import json, sys, textwrap
print(json.dumps([textwrap.dedent(s) for s in json.loads(sys.argv[1])],
                 ensure_ascii=False))
`, &want, pyprobe.Arg(t, corpus))

	// CLAIM: the U+001C line is BLANK to CPython's isspace and is
	// normalized away — the row that separates pytext.IsSpace from
	// unicode.IsSpace.
	if want[5] != "a\n\nb\n" {
		t.Fatalf("CLAIM moved: a U+001C-only line no longer counts as blank "+
			"(dedent gave %q)", want[5])
	}
	// CLAIM: an NBSP-only line is ALSO blank, and this row is the control
	// — Go's unicode.IsSpace agrees about U+00A0 and disagrees about
	// U+001C, so a port using the wrong predicate passes here and fails
	// the row above. Measured, not assumed: an earlier version of this
	// test asserted the opposite and the interpreter said no.
	if want[6] != "a\n\nb\n" {
		t.Fatalf("CLAIM moved: an NBSP-only line is no longer blank (%q)", want[6])
	}
	// The margin half of the same character: NBSP is not " " or "\t", so
	// the `c not in ' \t'` test breaks the scan and the margin stops at 2.
	if want[16] != nbspT+"a\n"+nbspT+"b\n" {
		t.Fatalf("CLAIM moved: an NBSP inside the indent no longer stops the "+
			"margin scan (%q)", want[16])
	}
	var changed int
	for i, s := range corpus {
		if got := dedent(s); got != want[i] {
			t.Errorf("dedent(%q)\n  go %q\n  py %q", s, got, want[i])
		}
		if want[i] != s {
			changed++
		}
	}
	if changed < 5 {
		t.Fatalf("only %d of %d dedent rows changed anything", changed, len(corpus))
	}
}
