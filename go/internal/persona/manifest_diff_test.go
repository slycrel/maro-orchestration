package persona

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// persona_to_dict is asdict(spec) plus one appended key, so its ORDER is the
// dataclass field order — and it is the order a caller that json.dumps()es
// the result writes to disk.
func TestPersonaToDictMatchesCPython(t *testing.T) {
	// A body long enough to be sliced, whose 200th code point lands inside a
	// run of whitespace: persona_to_dict does NOT strip, and its manifest
	// sibling DOES, so a preview keeping that whitespace is the only thing
	// separating the two.
	body := "\n  " + strings.Repeat("研", 190) + strings.Repeat(" ", 7) +
		strings.Repeat("z", 50)
	// ONE set of values, shared by both engines. Two hand-typed copies is how
	// a NO-BREAK SPACE got into the Go literal and a plain space into the
	// probe's — this test caught it and nothing else would have.
	name, role, tier := "n ame", "R\nole", "power"
	tools := []any{"t1", 2, true}
	scope, style := "global", "style"
	hooks, composes := []any{"h1"}, []any{"c1", nil}
	src := "/src/n.md"
	args := []any{name, role, tier, tools, scope, style, body, hooks, composes, src}

	var py struct {
		Keys []string `json:"keys"`
		JSON string   `json:"json"`
	}
	personaProbe(t).RunJSON(t, `
import json, sys
import persona
a = json.loads(sys.argv[1])
s = persona.PersonaSpec(name=a[0], role=a[1], model_tier=a[2], tool_access=a[3],
                        memory_scope=a[4], communication_style=a[5],
                        system_prompt=a[6], hooks=a[7], composes=a[8],
                        source_file=a[9])
d = persona.persona_to_dict(s)
print(json.dumps({"keys": list(d.keys()),
                  "json": json.dumps(d)},
                 ensure_ascii=False))
`, &py, pyprobe.Arg(t, args))

	wantKeys := []string{"name", "role", "model_tier", "tool_access",
		"memory_scope", "communication_style", "system_prompt", "hooks",
		"composes", "source_file", "system_prompt_preview"}
	if !reflect.DeepEqual(py.Keys, wantKeys) {
		t.Fatalf("CLAIM moved: the PersonaSpec field order is now %v", py.Keys)
	}
	// CLAIM: this fixture actually reaches the unstripped-preview branch.
	if !strings.Contains(py.JSON, `"system_prompt_preview": "   \u7814`) {
		t.Fatalf("CLAIM moved: the preview no longer keeps the body's LEADING "+
			"whitespace (%.90s)", py.JSON)
	}

	spec := &Spec{
		Name: name, Role: role, ModelTier: tier,
		ToolAccess: tools, MemoryScope: scope,
		CommunicationStyle: style, SystemPrompt: body,
		Hooks: hooks, Composes: composes, SourceFile: src,
	}
	d := ToDict(spec)
	var gotKeys []string
	for _, f := range d {
		gotKeys = append(gotKeys, f.Key)
	}
	if !reflect.DeepEqual(gotKeys, py.Keys) {
		t.Errorf("key order\n  go %v\n  py %v", gotKeys, py.Keys)
	}
	got, err := pyval.DumpsCompactPy(d)
	if err != nil {
		t.Fatal(err)
	}
	if got != py.JSON {
		t.Errorf("persona_to_dict\n  go %s\n  py %s", got, py.JSON)
	}

	// The preview is a 200-CODE-POINT slice of a body whose first 190
	// characters are three bytes each. A byte slice would cut mid-rune.
	prev := d.GetString("system_prompt_preview")
	if n := len([]rune(prev)); n != 200 {
		t.Errorf("preview is %d code points, want 200", n)
	}
	if strings.ContainsRune(prev, '�') {
		t.Error("the preview contains U+FFFD — it was sliced by byte")
	}
	// The slice both BEGINS and ENDS in whitespace (the leading "\n  "
	// became "   " under the replace). generate_manifest strips exactly
	// this and persona_to_dict does not, which is the whole difference
	// between the two previews.
	if !strings.HasPrefix(prev, "   ") || !strings.HasSuffix(prev, "       ") {
		t.Errorf("persona_to_dict's preview was stripped; only the MANIFEST "+
			"description strips (%q ... %q)", prev[:6], prev[len(prev)-10:])
	}
}

// generate_manifest and save_manifest, over one registry both engines build
// from the same files.
const manifestProbeSrc = `
import json, sys
import persona
from pathlib import Path
d, out = json.loads(sys.argv[1])
reg = persona.PersonaRegistry(personas_dir=Path(d))
m = persona.generate_manifest(registry=reg)
p = persona.save_manifest(output_path=Path(out), registry=reg, fmt="json")
print(json.dumps({
    "keys": [list(e.keys()) for e in m],
    "names": [e["name"] for e in m],
    "roles": [e["role"] for e in m],
    "descriptions": [e["description"] for e in m],
    "triggers": [e["trigger_keywords"] for e in m],
    "path": str(p),
    "content": Path(out).read_text(encoding="utf-8"),
}, ensure_ascii=False))
`

type pyManifest struct {
	Keys         [][]string `json:"keys"`
	Names        []string   `json:"names"`
	Roles        []string   `json:"roles"`
	Descriptions []string   `json:"descriptions"`
	Triggers     [][]string `json:"triggers"`
	Path         string     `json:"path"`
	Content      string     `json:"content"`
}

func TestGenerateAndSaveManifestMatchCPython(t *testing.T) {
	dir := t.TempDir()
	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// A persona whose name IS a routing-keywords key, so the `.get(name, [])`
	// lookup returns a real list.
	write("builder.md", "---\nname: builder\nrole: Builder\nmodel_tier: power\n"+
		"tool_access: [bash, edit]\nmemory_scope: project\ncomposes: [critic]\n---\n"+
		"Builder body.")
	// The name in a manifest entry is the one registry.list() RETURNED, and
	// list() returns each file's FRONTMATTER name. load() then re-resolves
	// that name from scratch and matches on name-OR-STEM over the files in
	// order — so when an early file's STEM equals a later file's frontmatter
	// NAME, the entry is named after the later file and carries the earlier
	// file's data. z-alias.md contributes the name "a-stem"; a-stem.md
	// supplies everything under it, and z-alias.md never appears at all.
	write("a-stem.md", "---\nname: whatever\nrole: FROM-STEM-FILE\n---\nStem body.")
	write("z-alias.md", "---\nname: a-stem\nrole: FROM-ALIAS-FILE\n---\nAlias body.")
	// A persona with NO routing keywords at all: `.get(name, [])`.
	write("unknown-one.md", "---\nname: unknown-one\nrole: Nobody\n---\nNobody body.")
	// A description whose 200th code point falls inside a run of spaces.
	// LEADING whitespace cannot exercise the strip — the parser already
	// stripped the body — so only a slice that ENDS in whitespace separates
	// slice-then-strip from strip-then-slice. The embedded newline before
	// the 200 mark exercises the replace.
	write("critic.md", "---\nname: critic\nrole: Critic\n---\n"+
		strings.Repeat("é", 100)+"\n"+strings.Repeat("é", 89)+
		strings.Repeat(" ", 20)+strings.Repeat("Q", 90))
	// A persona whose list fields carry NON-STRINGS. `list(spec.composes)`
	// copies them uncoerced into a file the Python runtime reads back.
	write("ops.md", "---\nname: ops\nrole: Ops\ncomposes: [1, 2.5, true]\n"+
		"tool_access: [7]\n---\nOps body.")
	// A file that list() advertises and load() refuses, so the `continue`
	// arm inside the loop fires.
	if err := os.WriteFile(filepath.Join(dir, "tainted.md"),
		[]byte("---\nname: tainted\n---\n\xff"), 0o644); err != nil {
		t.Fatal(err)
	}
	// README is skipped by the registry, not by the manifest.
	write("README.md", "not a persona")

	out := filepath.Join(t.TempDir(), "py", "manifest.json")
	var want pyManifest
	personaProbe(t).RunJSON(t, manifestProbeSrc, &want, pyprobe.Arg(t, []any{dir, out}))

	// --- CLAIMS ---
	roleOf := map[string]string{}
	for i, n := range want.Names {
		roleOf[n] = want.Roles[i]
	}
	if roleOf["a-stem"] != "FROM-STEM-FILE" {
		t.Fatalf("CLAIM moved: the entry named \"a-stem\" carries role %q, not "+
			"FROM-STEM-FILE — the name-from-list()/data-from-load() split is "+
			"not being measured (names=%v)", roleOf["a-stem"], want.Names)
	}
	for _, r := range want.Roles {
		if r == "FROM-ALIAS-FILE" {
			t.Fatalf("CLAIM moved: z-alias.md now supplies manifest DATA; it is "+
				"supposed to contribute only the name (names=%v roles=%v)",
				want.Names, want.Roles)
		}
	}
	if !containsString(want.Names, "whatever") {
		t.Fatalf("CLAIM moved: a-stem.md's own frontmatter name is missing from "+
			"the manifest (%v)", want.Names)
	}
	var builderTriggers []string
	for i, n := range want.Names {
		if n == "builder" {
			builderTriggers = want.Triggers[i]
		}
	}
	if len(builderTriggers) == 0 {
		t.Fatalf("CLAIM moved: \"builder\" no longer picks up trigger keywords, "+
			"so the name-keyed _PERSONA_ROUTING_KEYWORDS lookup is untested (%v)",
			want.Triggers)
	}
	for i, n := range want.Names {
		if n == "unknown-one" && len(want.Triggers[i]) != 0 {
			t.Fatalf("CLAIM moved: an unlisted persona now has triggers %v",
				want.Triggers[i])
		}
		if n == "critic" {
			desc := want.Descriptions[i]
			if strings.HasPrefix(desc, " ") || strings.HasSuffix(desc, " ") {
				t.Fatalf("CLAIM moved: the manifest description is no longer "+
					"stripped (%q)", desc)
			}
			if len([]rune(desc)) >= 200 {
				t.Fatalf("CLAIM moved: the description fixture no longer loses "+
					"characters to the strip-after-slice (%d code points)",
					len([]rune(desc)))
			}
		}
	}
	if containsString(want.Names, "tainted") {
		t.Fatalf("CLAIM moved: the unloadable file reached the manifest (%v)",
			want.Names)
	}
	if containsString(want.Names, "README") {
		t.Fatalf("CLAIM moved: README reached the manifest (%v)", want.Names)
	}
	if len(want.Names) < 4 {
		t.Fatalf("vacuity: only %d entries in the manifest (%v)",
			len(want.Names), want.Names)
	}

	// --- the comparison ---
	reg := NewFromDir(dir)
	got := GenerateManifest(reg)
	if len(got) != len(want.Keys) {
		t.Fatalf("manifest length: go %d py %d\n  go %v\n  py %v",
			len(got), len(want.Keys), manifestNames(got), want.Names)
	}
	for i, e := range got {
		var keys []string
		for _, f := range e {
			keys = append(keys, f.Key)
		}
		if !reflect.DeepEqual(keys, want.Keys[i]) {
			t.Errorf("entry %d key order\n  go %v\n  py %v", i, keys, want.Keys[i])
		}
		if e.GetString("name") != want.Names[i] {
			t.Errorf("entry %d name: go %q py %q", i, e.GetString("name"), want.Names[i])
		}
		if e.GetString("role") != want.Roles[i] {
			t.Errorf("entry %d role: go %q py %q", i, e.GetString("role"), want.Roles[i])
		}
		if e.GetString("description") != want.Descriptions[i] {
			t.Errorf("entry %d description\n  go %q\n  py %q",
				i, e.GetString("description"), want.Descriptions[i])
		}
	}

	// save_manifest writes bytes, and the bytes are the contract.
	goOut := filepath.Join(t.TempDir(), "go", "manifest.json")
	p, err := SaveManifest("", goOut, "json", reg)
	if err != nil {
		t.Fatal(err)
	}
	if p != goOut {
		t.Errorf("SaveManifest returned %q, want %q", p, goOut)
	}
	raw, err := os.ReadFile(goOut)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != want.Content {
		t.Errorf("manifest.json bytes differ\n  go %q\n  py %q",
			truncForMsg(string(raw)), truncForMsg(want.Content))
	}
	// CLAIM: the file really carries the shapes this test set up.
	if !strings.HasSuffix(want.Content, "}\n") {
		t.Fatalf("CLAIM moved: save_manifest no longer appends a newline (%q)",
			want.Content[len(want.Content)-6:])
	}
	if !strings.Contains(want.Content, "é") {
		t.Fatal("CLAIM moved: ensure_ascii=False is no longer set on the " +
			"manifest dump, so the raw-unicode path is untested")
	}
	if !strings.Contains(want.Content, "2.5") || !strings.Contains(want.Content, "true") {
		t.Fatalf("CLAIM moved: the uncoerced non-string list elements no longer " +
			"reach the file, so a []string port would pass this test")
	}
}

// SaveManifest's default path is <output_root>/agents/manifest.<fmt>, and the
// parent directory is created before anything is written.
func TestSaveManifestDefaultPath(t *testing.T) {
	root := t.TempDir()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.md"),
		[]byte("---\nname: a\n---\nA"), 0o644); err != nil {
		t.Fatal(err)
	}
	p, err := SaveManifest(root, "", "json", NewFromDir(dir))
	if err != nil {
		t.Fatal(err)
	}
	if p != filepath.Join(root, "agents", "manifest.json") {
		t.Fatalf("default path is %q", p)
	}
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("the default path was not written: %v", err)
	}
}

// The yaml format is deliberately NOT ported. This pins WHY, by measuring
// what PyYAML actually produces, so the day someone decides to port it the
// three obstacles are already written down and checkable.
func TestYAMLManifestFormatIsNotReproducible(t *testing.T) {
	var py struct {
		Text string `json:"text"`
	}
	personaProbe(t).RunJSON(t, `
import json
import yaml
m = [{"name": "z-first", "role": "R", "model_tier": "power",
      "tool_access": [], "memory_scope": "session",
      "trigger_keywords": ["a"], "composes": [],
      "description": "lorem ipsum dolor " * 8}]
print(json.dumps({"text": yaml.dump({"agents": m},
                                    default_flow_style=False,
                                    allow_unicode=True)}, ensure_ascii=False))
`, &py)

	// 1. PyYAML SORTS keys, discarding the insertion order the JSON manifest
	//    preserves. "composes" is emitted before "name".
	iComposes := strings.Index(py.Text, "composes:")
	iName := strings.Index(py.Text, "name:")
	if iComposes < 0 || iName < 0 || iComposes > iName {
		t.Fatalf("CLAIM moved: PyYAML no longer sorts manifest keys:\n%s", py.Text)
	}
	// 2. It FOLDS a long scalar across lines with a continuation indent, and
	//    quotes it with SINGLE quotes.
	//
	//    Both details were measured rather than assumed, and the first
	//    assumption was wrong twice over: a 120-character run of "x" does
	//    NOT fold (there is nowhere to break — PyYAML breaks at whitespace),
	//    and even a folding scalar exceeds 80 columns on the line that
	//    carries the closing quote. So the assertion is "the value continues
	//    onto a following line", which is the property that actually holds.
	//    The first version of this check asked whether the text contained a
	//    run of x's, which was true whether it folded or not.
	//
	//    Mutating the FIXTURE isolates each of the three checks below:
	//    "lorem " (short, still quoted for its trailing space) fires only the
	//    fold check; ("lorem ipsum dolor " * 8).strip() (long, folds, but
	//    plain) fires only the quote check; dropping the key fires only the
	//    field-present check.
	lines := strings.Split(py.Text, "\n")
	descIdx := -1
	for i, l := range lines {
		if strings.HasPrefix(strings.TrimLeft(l, " -"), "description:") {
			descIdx = i
			break
		}
	}
	if descIdx < 0 || descIdx+1 >= len(lines) {
		t.Fatalf("CLAIM moved: no description line in the dump:\n%s", py.Text)
	}
	if !strings.Contains(lines[descIdx], ": 'lorem") {
		t.Fatalf("CLAIM moved: PyYAML no longer uses SINGLE quotes for this "+
			"scalar (%q)", lines[descIdx])
	}
	if !strings.HasPrefix(strings.TrimSpace(lines[descIdx+1]), "dolor") {
		t.Fatalf("CLAIM moved: PyYAML no longer folds the description onto a "+
			"continuation line (next line %q):\n%s", lines[descIdx+1], py.Text)
	}
	// 3. Neither is configurable through gopkg.in/yaml.v3's Marshal, so the
	//    port refuses rather than writing a file that differs on every line.
	if _, err := SaveManifest(t.TempDir(), "", "yaml", NewFromDir(t.TempDir())); err != ErrYAMLManifestNotPorted {
		t.Fatalf("SaveManifest(fmt=yaml) returned %v, want ErrYAMLManifestNotPorted", err)
	}
}

func manifestNames(rows []pyval.Obj) []string {
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = r.GetString("name")
	}
	return out
}

func truncForMsg(s string) string {
	r := []rune(s)
	if len(r) <= 400 {
		return s
	}
	return string(r[:400]) + "..."
}
