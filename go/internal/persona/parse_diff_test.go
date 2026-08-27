package persona

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// personaProbe is the read-only CPython probe this package's differentials
// share. `import persona` pulls in nothing from config or the workspace —
// the module's own imports are json/logging/re/textwrap/dataclasses/
// pathlib/typing — so no Workspace is set and none is needed.
// personaProbe is the CPython side of every differential in this package.
//
// Workspace is ALWAYS set, even for probes that look read-only, because
// `PersonaRegistry()` with no argument resolves config.personas_dir() and
// that resolver MKDIRS what it returns. An unset MARO_WORKSPACE points it at
// the live ~/.maro tree. Passing a per-test temp dir also arms pyprobe's own
// live-workspace refusal, so a probe added later that writes cannot reach the
// real workspace by forgetting to opt in.
func personaProbe(t *testing.T) pyprobe.Probe {
	t.Helper()
	return pyprobe.Probe{Marker: "persona.py", Workspace: t.TempDir()}
}

// pySpec is the shape the probe emits per file. It is the dataclass field
// for field, so a field this port silently stopped populating shows up as a
// mismatch rather than as an absent comparison.
type pySpec struct {
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
	Error              string `json:"error"`
}

const parseProbeSrc = `
import json, sys
import persona
from pathlib import Path
out = []
for p in json.loads(sys.argv[1]):
    try:
        s = persona._parse_persona_file(Path(p))
        out.append({"name": s.name, "role": s.role, "model_tier": s.model_tier,
                    "tool_access": s.tool_access, "memory_scope": s.memory_scope,
                    "communication_style": s.communication_style,
                    "system_prompt": s.system_prompt, "hooks": s.hooks,
                    "composes": s.composes, "source_file": s.source_file,
                    "error": ""})
    except Exception as e:
        out.append({"name": "", "role": "", "model_tier": "", "tool_access": [],
                    "memory_scope": "", "communication_style": "",
                    "system_prompt": "", "hooks": [], "composes": [],
                    "source_file": "", "error": type(e).__name__})
print(json.dumps(out, ensure_ascii=False))
`

// parseCase is one persona file. content is written VERBATIM (no newline
// translation on the way to disk) so a CRLF case really carries CRLF.
type parseCase struct {
	name    string
	content string
	// claim is what CPython is asserted to answer for this row, checked
	// against the probe BEFORE the port is compared. A fixture whose
	// branch quietly stopped firing then fails loudly instead of passing
	// green while measuring nothing. "" means no specific claim.
	claim func(t *testing.T, name string, got pySpec)
}

var nbsp = string(rune(0x00A0))  // NO-BREAK SPACE
var fsSep = string(rune(0x001C)) // FILE SEPARATOR

func wantName(want string) func(*testing.T, string, pySpec) {
	return func(t *testing.T, n string, got pySpec) {
		t.Helper()
		if got.Name != want {
			t.Fatalf("%s: the CLAIM about CPython has moved — expected name %q, "+
				"CPython answered %q. The fixture is no longer exercising the "+
				"branch it was written for.", n, want, got.Name)
		}
	}
}

var parseCorpus = []parseCase{
	{"no_frontmatter", "# Hello\nbody text\n", wantName("no_frontmatter")},
	{"empty", "", wantName("empty")},
	{"just_dashes", "---", wantName("just_dashes")},
	{"dashes_nl", "---\n", nil},
	// `\n---` found immediately: fm_text is "" and the body is everything
	// after it.
	{"close_immediately", "---\n---\nbody", nil},
	{"normal", "---\nname: alpha\nrole: Alpha Role\nmodel_tier: power\n" +
		"tool_access: [a, b]\nhooks: [h]\ncomposes: [c]\n---\n\n# Body\n",
		wantName("alpha")},
	// The list-from-string coercion, with an empty element in the middle
	// and whitespace either side: str.split never collapses, the filter is
	// on the STRIPPED value.
	{"list_from_string", "---\ntool_access: 'a, b , ,c'\nhooks: 'x'\ncomposes: ''\n---\nbody", nil},
	// A non-list, non-string value for a list field becomes [].
	{"list_from_int", "---\ntool_access: 5\nhooks: {a: 1}\n---\nbody", nil},
	// ...and a list of INTEGERS survives element-uncoerced, which is the
	// whole reason the port's list fields are []any.
	{"list_of_ints", "---\ncomposes: [1, 2]\n---\nbody", nil},
	// yaml.safe_load returning a non-dict: both a sequence and a scalar
	// fall through to the defaults, keeping the already-sliced body.
	{"nondict_seq", "---\n- a\n- b\n---\nbody", wantName("nondict_seq")},
	{"nondict_scalar", "---\njust a scalar\n---\nbody", wantName("nondict_scalar")},
	{"malformed_yaml", "---\na: [unclosed\n---\nbody", wantName("malformed_yaml")},
	{"empty_frontmatter", "---\n---\n", nil},
	// TRUTHINESS on the name: "" , 0 and false all lose to the stem.
	{"name_empty", "---\nname: ''\nrole: R\n---\nbody", wantName("name_empty")},
	{"name_zero", "---\nname: 0\n---\nbody", wantName("name_zero")},
	{"name_false", "---\nname: false\n---\nbody", wantName("name_false")},
	// No closing delimiter: the WHOLE file is the system prompt, frontmatter
	// text included.
	{"no_close", "---\nname: x\nbody with no close", wantName("no_close")},
	// UNIVERSAL NEWLINES. Written with real CRLF, so a byte-literal port
	// finds no "\n---" and hands the whole file over as the prompt.
	{"crlf", "---\r\nname: crlf\r\n---\r\nbody\r\nmore\r\n", wantName("crlf")},
	// A lone CR, which universal newlines also folds to \n.
	{"lone_cr", "---\rname: cr\r---\rbody", wantName("cr")},
	{"unicode", "---\nrole: café 研究\n---\nbody ééé", nil},
	// A NUMBER where a string field is expected: every scalar field goes
	// through `str()`, and the two engines must agree on how an int and a
	// float spell themselves. (These two resolve the same way under YAML
	// 1.1 and 1.2 — the spellings that do NOT are pinned separately, in
	// TestYAML11FrontmatterDivergesFromYAML12.)
	{"scalar_numbers", "---\nmodel_tier: 3\nmemory_scope: 4.5\n---\nbody", nil},
	// Four dashes: HasPrefix passes, the search starts at index 3 and lands
	// on the SECOND row of dashes, so the frontmatter text is "-\nname: x".
	{"dash4", "----\nname: x\n----\nbody", wantName("dash4")},
	// str.strip() is Unicode: a body wrapped in NO-BREAK SPACE and FILE
	// SEPARATOR is stripped by CPython and not by strings.TrimSpace.
	{"unicode_strip", "---\nname: us\n---\n" + nbsp + fsSep + "body" + fsSep + nbsp, wantName("us")},
	{"tab_after_close", "---\nname: t\n---\tbody", wantName("t")},
	// A nested mapping under an unread key: `meta.update` takes it and
	// nothing reads it.
	{"nested_extra", "---\nname: nd\nextra: {k: v}\n---\nbody", wantName("nd")},
	// A BOM defeats startswith("---") — encoding="utf-8" does NOT strip one
	// — so the whole file is the prompt. Spelled as an escape because a
	// literal U+FEFF is an illegal byte order mark in a Go source file.
	{"bom", "\ufeff---\nname: bom\n---\nbody", wantName("bom")},
}

func TestParseFileMatchesCPython(t *testing.T) {
	dir := t.TempDir()
	paths := make([]string, len(parseCorpus))
	for i, c := range parseCorpus {
		p := filepath.Join(dir, c.name+".md")
		if err := os.WriteFile(p, []byte(c.content), 0o644); err != nil {
			t.Fatal(err)
		}
		paths[i] = p
	}
	// A byte-tainted file: `read_text(encoding="utf-8")` RAISES, and every
	// caller of the parser treats that differently (see the registry
	// differential). Written as raw bytes, so it cannot be a parseCase.
	bad := filepath.Join(dir, "bad.md")
	if err := os.WriteFile(bad, []byte("---\nname: bad\n---\n\xff\xfe body"), 0o644); err != nil {
		t.Fatal(err)
	}
	// A DIRECTORY that `*.md` globs and the parser then chokes on.
	adir := filepath.Join(dir, "adir.md")
	if err := os.Mkdir(adir, 0o755); err != nil {
		t.Fatal(err)
	}
	all := append(append([]string{}, paths...), bad, adir)

	var want []pySpec
	personaProbe(t).RunJSON(t, parseProbeSrc, &want, pyprobe.Arg(t, all))
	if len(want) != len(all) {
		t.Fatalf("probe returned %d rows for %d files", len(want), len(all))
	}

	// Vacuity floors, checked before anything is compared: a corpus that
	// stopped reaching the frontmatter path, or stopped producing a
	// non-default field, cannot fail for the reason it exists.
	var withFrontmatter, withNonDefaultRole, withRaise int
	for _, w := range want {
		if w.Error != "" {
			withRaise++
			continue
		}
		if w.Role != "General Assistant" {
			withNonDefaultRole++
		}
		if w.ModelTier != "mid" || len(w.ToolAccess) > 0 || w.Name != "" {
			withFrontmatter++
		}
	}
	if withRaise < 2 {
		t.Fatalf("the corpus reaches CPython's RAISE path %d times; the "+
			"byte-tainted file and the directory should both raise", withRaise)
	}
	if withNonDefaultRole == 0 {
		t.Fatal("no row parsed a role out of frontmatter — the corpus cannot " +
			"tell a working parse from a defaults-only one")
	}

	for i, c := range parseCorpus {
		w := want[i]
		if c.claim != nil {
			c.claim(t, c.name, w)
		}
		got, err := ParseFile(paths[i])
		if w.Error != "" {
			if err == nil {
				t.Errorf("%s: CPython raised %s and the port returned a spec",
					c.name, w.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: CPython parsed it and the port refused: %v", c.name, err)
			continue
		}
		compareSpec(t, c.name, got, w, paths[i])
	}

	// The two out-of-corpus rows: both must RAISE on both sides.
	for j, p := range []string{bad, adir} {
		w := want[len(parseCorpus)+j]
		if w.Error == "" {
			t.Fatalf("%s: CLAIM moved — CPython was expected to raise and did not", p)
		}
		if _, err := ParseFile(p); err == nil {
			t.Errorf("%s: CPython raised %s and the port returned a spec", p, w.Error)
		}
	}
}

func compareSpec(t *testing.T, name string, got *Spec, w pySpec, path string) {
	t.Helper()
	check := func(field, g, p string) {
		t.Helper()
		if g != p {
			t.Errorf("%s.%s\n  go %q\n  py %q", name, field, g, p)
		}
	}
	check("name", got.Name, w.Name)
	check("role", got.Role, w.Role)
	check("model_tier", got.ModelTier, w.ModelTier)
	check("memory_scope", got.MemoryScope, w.MemoryScope)
	check("communication_style", got.CommunicationStyle, w.CommunicationStyle)
	check("system_prompt", got.SystemPrompt, w.SystemPrompt)
	check("source_file", got.SourceFile, w.SourceFile)
	compareList(t, name+".tool_access", got.ToolAccess, w.ToolAccess)
	compareList(t, name+".hooks", got.Hooks, w.Hooks)
	compareList(t, name+".composes", got.Composes, w.Composes)
}

// compareList compares through the JSON round trip both sides took, so an
// integer element compares as an integer rather than as float-vs-int noise
// from two different decoders.
func compareList(t *testing.T, what string, got []any, py []any) {
	t.Helper()
	g := normalizeForCompare(got)
	p := normalizeForCompare(py)
	if !reflect.DeepEqual(g, p) {
		t.Errorf("%s\n  go %#v\n  py %#v", what, g, p)
	}
}

func normalizeForCompare(v []any) []any {
	out := make([]any, len(v))
	for i, e := range v {
		switch t := e.(type) {
		case int:
			out[i] = float64(t)
		case int64:
			out[i] = float64(t)
		default:
			out[i] = e
		}
	}
	return out
}
