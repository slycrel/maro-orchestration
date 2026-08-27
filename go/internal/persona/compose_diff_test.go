package persona

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The compose probe builds the SAME registry from files on disk (so the
// specs it composes came through the real parser on both sides) and runs
// compose_persona over a list of (names, extra_prompt) cases.
const composeProbeSrc = `
import json, sys
import persona
from pathlib import Path
d, cases = json.loads(sys.argv[1])
reg = persona.PersonaRegistry(personas_dir=Path(d))
out = []
for names, extra in cases:
    try:
        s = persona.compose_persona(*names, registry=reg, extra_prompt=extra)
        out.append({"err": "", "name": s.name, "role": s.role,
                    "model_tier": s.model_tier, "tool_access": s.tool_access,
                    "memory_scope": s.memory_scope,
                    "communication_style": s.communication_style,
                    "system_prompt": s.system_prompt, "hooks": s.hooks,
                    "composes": s.composes, "source_file": s.source_file})
    except Exception as e:
        out.append({"err": "%s: %s" % (type(e).__name__, e)})
print(json.dumps(out, ensure_ascii=False))
`

type pyCompose struct {
	Err                string `json:"err"`
	Name               string `json:"name"`
	Role               string `json:"role"`
	ModelTier          string `json:"model_tier"`
	ToolAccess         []any  `json:"tool_access"`
	MemoryScope        string `json:"memory_scope"`
	CommunicationStyle string `json:"communication_style"`
	SystemPrompt       string `json:"system_prompt"`
	Hooks              []any  `json:"hooks"`
	Composes           []any  `json:"composes"`
	SourceFile         string `json:"source_file"`
}

func TestComposeMatchesCPython(t *testing.T) {
	dir := t.TempDir()
	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// a: low tier, broad scope, two tools, one hook.
	write("a.md", "---\nname: a\nrole: RA\nmodel_tier: cheap\nmemory_scope: global\n"+
		"tool_access: [t1, t2]\nhooks: [h1]\ncommunication_style: ca\n---\nA")
	// b: high tier, narrow scope, an overlapping tool, and a body that is
	// WHITESPACE ONLY — it must contribute neither text nor a separator.
	write("b.md", "---\nname: b\nrole: RB\nmodel_tier: power\nmemory_scope: session\n"+
		"tool_access: [t2, t3]\nhooks: [h1, h2]\ncommunication_style: cb\n---\n \n ")
	// c: UNKNOWN tier and scope, so the two rank defaults (1 and 0) are
	// exercised, and a communication_style that DUPLICATES a's.
	write("c.md", "---\nname: c\nrole: RC\nmodel_tier: bespoke\nmemory_scope: odd\n"+
		"communication_style: ca\n---\nC")
	// d: EMPTY tier/scope/style via quoted empties, which the max()
	// generators filter out entirely.
	write("d.md", "---\nname: d\nrole: RD\nmodel_tier: ''\nmemory_scope: ''\n"+
		"communication_style: ''\n---\nD")
	// e/f: both at "cheap", the pair that separates `default=` from a real
	// candidate that ties with it.
	write("e.md", "---\nname: e\nrole: RE\nmodel_tier: cheap\nmemory_scope: session\n---\nE")
	write("f.md", "---\nname: f\nrole: RF\nmodel_tier: cheap\nmemory_scope: session\n---\nF")
	// g: integer list elements, so the dedup runs over non-strings.
	write("g.md", "---\nname: g\nrole: RG\ncomposes: [1, 2]\ntool_access: [1, 'x']\n---\nG")
	write("h.md", "---\nname: h\nrole: RH\ntool_access: [1, 'y']\n---\nH")
	// i/j: the two shapes where Python's `in` and Go's `==` disagree.
	// 1 == 1.0 is TRUE in Python, so the dedup collapses them to one
	// element; Go's == over `any` says int(1) != float64(1.0) and keeps
	// both. And `[q] == [q]` is True in Python while Go's == PANICS on a
	// slice operand rather than answering. g/h alone reach neither: their
	// shared element is an int on both sides.
	write("i.md", "---\nname: i\nrole: RI\ntool_access: [1, [q]]\n---\nI")
	write("j.md", "---\nname: j\nrole: RJ\ntool_access: [1.0, [q]]\n---\nJ")
	// m/n: a KNOWN tier and scope at the same RANK as c's unknown ones.
	// "bespoke" and "mid" both rank 1, and "odd" and "session" both rank 0,
	// so these are the pairs where first-maximal and last-maximal disagree
	// about the STRING while agreeing about the rank. Without them the
	// tie rule is unmeasured: e/f tie at "cheap" and produce the same
	// string either way.
	write("m.md", "---\nname: m\nrole: RM\nmodel_tier: mid\nmemory_scope: session\n---\nM")

	type composeCase struct {
		names []string
		extra string
	}
	cases := []composeCase{
		{[]string{"a"}, ""},           // the single-name FAST PATH
		{[]string{"a"}, "EX"},         // ...defeated by an extra prompt
		{[]string{"a"}, " "},          // ...NOT defeated-by-strip: " " is truthy
		{[]string{"a", "b"}, ""},      // tier/scope/tool union, whitespace body
		{[]string{"b", "a"}, ""},      // order: union order and role flip
		{[]string{"a", "b", "c"}, ""}, // unknown tier ranks 1; style dedup
		{[]string{"c", "d"}, ""},      // empty tier/scope filtered out
		{[]string{"d", "d"}, ""},      // generator EMPTY -> default= applies
		{[]string{"e", "f"}, ""},      // tie at "cheap" -> NOT the default
		{[]string{"c", "b"}, ""},
		{[]string{"g", "h"}, ""}, // integer element dedup
		{[]string{"i", "j"}, ""}, // 1 == 1.0, and a NESTED list element
		{[]string{"c", "m"}, ""}, // rank tie 1v1: FIRST maximal -> "bespoke"
		{[]string{"m", "c"}, ""}, // ...and the same rule the other way
		{[]string{"a", "c"}, ""}, // unknown tier ranks 1, so it beats cheap
		{[]string{"e", "c"}, ""}, // unknown scope ranks 0, so session holds
		{[]string{"c", "e"}, ""}, // ...and first-maximal picks "odd"
		{[]string{"a", "b"}, "EXTRA"},
		{[]string{}, ""},          // ValueError: no names
		{[]string{"nope"}, ""},    // ValueError: not found
		{[]string{"a", "??"}, ""}, // ...and its repr with a non-identifier name
	}

	probeCases := make([]any, len(cases))
	for i, c := range cases {
		probeCases[i] = []any{c.names, c.extra}
	}
	var want []pyCompose
	personaProbe(t).RunJSON(t, composeProbeSrc, &want, pyprobe.Arg(t, []any{dir, probeCases}))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(cases))
	}

	// CLAIMS about CPython, checked before comparison.
	if want[0].Composes == nil || len(want[0].Composes) != 0 || want[0].SourceFile == "" {
		t.Fatalf("CLAIM moved: the single-name fast path no longer returns the "+
			"registry's own spec (composes=%v source_file=%q)",
			want[0].Composes, want[0].SourceFile)
	}
	if len(want[2].Composes) != 1 {
		t.Fatalf("CLAIM moved: extra_prompt=\" \" no longer defeats the fast "+
			"path (composes=%v)", want[2].Composes)
	}
	byNames := map[string]pyCompose{}
	for i, c := range cases {
		byNames[strings.Join(c.names, "+")+"|"+c.extra] = want[i]
	}
	if n := len(byNames["i+j|"].ToolAccess); n != 2 {
		t.Fatalf("CLAIM moved: composing [1, [q]] with [1.0, [q]] yielded %d "+
			"tools, not 2 -- Python's `in` no longer treats 1 == 1.0 and two "+
			"equal lists as duplicates (%v)", n, byNames["i+j|"].ToolAccess)
	}
	if byNames["c+m|"].ModelTier != "bespoke" || byNames["m+c|"].ModelTier != "mid" {
		t.Fatalf("CLAIM moved: max() no longer returns the FIRST maximal tier "+
			"on a rank tie (c+m -> %q, m+c -> %q)",
			byNames["c+m|"].ModelTier, byNames["m+c|"].ModelTier)
	}
	if byNames["a+c|"].ModelTier != "bespoke" {
		t.Fatalf("CLAIM moved: an UNKNOWN tier no longer ranks 1 (a+c -> %q, "+
			"want \"bespoke\" beating \"cheap\")", byNames["a+c|"].ModelTier)
	}
	if byNames["e+c|"].MemoryScope != "session" || byNames["c+e|"].MemoryScope != "odd" {
		t.Fatalf("CLAIM moved: an UNKNOWN scope no longer ranks 0 (e+c -> %q, "+
			"c+e -> %q; both should be the FIRST of two rank-0 scopes)",
			byNames["e+c|"].MemoryScope, byNames["c+e|"].MemoryScope)
	}
	if want[7].ModelTier != "mid" {
		t.Fatalf("CLAIM moved: composing two tier-less personas answered %q, "+
			"not the max() default \"mid\"", want[7].ModelTier)
	}
	if want[8].ModelTier != "cheap" {
		t.Fatalf("CLAIM moved: two personas tied at \"cheap\" composed to %q — "+
			"the default-vs-candidate distinction is not being measured",
			want[8].ModelTier)
	}
	if want[3].SystemPrompt != "A" {
		t.Fatalf("CLAIM moved: a whitespace-only body now contributes to the "+
			"composed prompt (%q)", want[3].SystemPrompt)
	}
	if want[len(cases)-3].Err == "" || want[len(cases)-2].Err == "" {
		t.Fatal("CLAIM moved: the two ValueError cases no longer raise")
	}

	var errs, oks int
	for i, c := range cases {
		w := want[i]
		got, err := Compose(NewFromDir(dir), c.extra, c.names...)
		if w.Err != "" {
			errs++
			if err == nil {
				t.Errorf("case %d %v: CPython raised %q, port succeeded", i, c.names, w.Err)
				continue
			}
			// Python's message text reaches a SpawnResult summary an
			// operator reads, so the wording is compared, not just the
			// fact of an error.
			wantMsg := w.Err[len("ValueError: "):]
			if err.Error() != wantMsg {
				t.Errorf("case %d %v error TEXT\n  go %q\n  py %q",
					i, c.names, err.Error(), wantMsg)
			}
			continue
		}
		oks++
		if err != nil {
			t.Errorf("case %d %v: CPython succeeded, port failed: %v", i, c.names, err)
			continue
		}
		label := func(f string) string { return "case " + itoaTest(i) + " " + f }
		eq := func(f, g, p string) {
			t.Helper()
			if g != p {
				t.Errorf("%s %v\n  go %q\n  py %q", label(f), c.names, g, p)
			}
		}
		eq("name", got.Name, w.Name)
		eq("role", got.Role, w.Role)
		eq("model_tier", got.ModelTier, w.ModelTier)
		eq("memory_scope", got.MemoryScope, w.MemoryScope)
		eq("communication_style", got.CommunicationStyle, w.CommunicationStyle)
		eq("system_prompt", got.SystemPrompt, w.SystemPrompt)
		eq("source_file", got.SourceFile, w.SourceFile)
		compareList(t, label("tool_access"), got.ToolAccess, w.ToolAccess)
		compareList(t, label("hooks"), got.Hooks, w.Hooks)
		compareList(t, label("composes"), got.Composes, w.Composes)
	}
	if errs < 3 || oks < 8 {
		t.Fatalf("vacuity: %d raising cases and %d succeeding ones reached the "+
			"comparison", errs, oks)
	}
}

func itoaTest(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

// The single-name fast path returns the registry's OWN object in Python, so
// the caller and the registry share it. A port that copied would diverge
// the moment anything mutates a composed spec — which apply_skeptic_modifier
// deliberately does NOT do (it replaces), and which is exactly why the
// aliasing has never bitten.
func TestComposeSingleNameReturnsTheCachedObject(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.md"),
		[]byte("---\nname: a\n---\nA"), 0o644); err != nil {
		t.Fatal(err)
	}
	reg := NewFromDir(dir)
	loaded := reg.Load("a")
	composed, err := Compose(reg, "", "a")
	if err != nil {
		t.Fatal(err)
	}
	if composed != loaded {
		t.Fatal("the single-name fast path returned a COPY; Python returns " +
			"specs[0], the same object the registry cached")
	}
}

// ApplySkepticModifier APPENDS. The docstring upstream says "prepended" and
// the code does not, so the text is pinned by position, not by containment.
func TestSkepticModifierAppendsExactly(t *testing.T) {
	var py struct {
		Addition string `json:"addition"`
		Result   string `json:"result"`
	}
	personaProbe(t).RunJSON(t, `
import json
import persona
s = persona.PersonaSpec(name="n", role="R", model_tier="mid", tool_access=[],
                        memory_scope="session", communication_style="cs",
                        system_prompt="BODY", hooks=[], composes=[])
out = persona.apply_skeptic_modifier(s)
print(json.dumps({"addition": persona._SKEPTIC_ADDITION,
                  "result": out.system_prompt}, ensure_ascii=False))
`, &py)

	if py.Addition != skepticAddition {
		t.Errorf("_SKEPTIC_ADDITION text\n  go %q\n  py %q", skepticAddition, py.Addition)
	}
	got := ApplySkepticModifier(&Spec{SystemPrompt: "BODY"})
	if got.SystemPrompt != py.Result {
		t.Errorf("apply_skeptic_modifier\n  go %q\n  py %q", got.SystemPrompt, py.Result)
	}
	if py.Result[:4] != "BODY" {
		t.Fatalf("CLAIM moved: CPython now PREPENDS the skeptic framing (%q)",
			py.Result[:40])
	}
}

// The list fields survive a compose without aliasing the sources: Python
// builds fresh lists, so appending to a composed spec's tool_access cannot
// reach back into a registry-cached one.
func TestComposeDoesNotAliasSourceLists(t *testing.T) {
	dir := t.TempDir()
	for name, body := range map[string]string{
		"a.md": "---\nname: a\ntool_access: [t1]\n---\nA",
		"b.md": "---\nname: b\ntool_access: [t2]\n---\nB",
	} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	reg := NewFromDir(dir)
	a := reg.Load("a")
	c, err := Compose(reg, "", "a", "b")
	if err != nil {
		t.Fatal(err)
	}
	c.ToolAccess[0] = "MUTATED"
	if !reflect.DeepEqual(a.ToolAccess, []any{"t1"}) {
		t.Fatalf("mutating the composed tool_access reached the source spec: %v",
			a.ToolAccess)
	}
}
