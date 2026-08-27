package persona

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The registry probe drives the real PersonaRegistry over two directories
// the test owns, and asks it the three questions the port answers:
// list(), load(name) for a set of names, and load_all().
const registryProbeSrc = `
import json, sys
import persona
from pathlib import Path
ws, repo, names = json.loads(sys.argv[1])
reg = persona.PersonaRegistry()
reg._ws_dir = Path(ws) if ws else None
reg._repo_dir = Path(repo) if repo else None
reg._cache = {}
files = [str(p) for p in reg._persona_files()]
loaded = {}
for n in names:
    s = reg.load(n)
    loaded[n] = None if s is None else [s.name, s.role, s.source_file]
print(json.dumps({
    "files": files,
    "list": reg.list(),
    "loaded": loaded,
    "all": [[s.name, s.source_file] for s in reg.load_all()],
}, ensure_ascii=False))
`

type registryAnswer struct {
	Files  []string            `json:"files"`
	List   []string            `json:"list"`
	Loaded map[string][]string `json:"loaded"`
	All    [][]string          `json:"all"`
}

// TestRegistryMatchesCPython drives both engines over one pair of
// directories carrying every shape the resolution rules turn on.
//
// CLAIMS, asserted against the probe before the port is compared:
//
//   - README.md is skipped in BOTH tiers.
//   - A workspace file suppresses a repo file with the same STEM, and does
//     NOT suppress one whose stem differs but whose frontmatter `name`
//     collides.
//   - A byte-tainted file contributes its STEM to list() and nothing to
//     load()/load_all() — so list() advertises a name load() refuses.
//   - A DIRECTORY matching *.md behaves exactly like the tainted file.
//   - A dotfile IS a persona.
//   - load() matches on the frontmatter name OR the stem.
func TestRegistryMatchesCPython(t *testing.T) {
	// The workspace directory deliberately sorts AFTER the repo one, and
	// both live under one parent. Two separate t.TempDir() calls give
	// .../001 and .../002, which put the workspace first by accident — and
	// then a port that sorted the combined file list instead of keeping the
	// tier order would produce the same answer and never be noticed.
	base := t.TempDir()
	ws := filepath.Join(base, "z-ws")
	repo := filepath.Join(base, "a-repo")
	for _, d := range []string{ws, repo} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write := func(dir, name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// Workspace tier.
	write(ws, "README.md", "skipped in both tiers")
	write(ws, "shadowed.md", "---\nname: shadowed\nrole: WS WINS\n---\nws body")
	write(ws, ".hidden.md", "---\nname: hidden\n---\nH")
	write(ws, "zeta.md", "---\nname: alpha\nrole: Zeta File\n---\nZ body")
	// Repo tier.
	write(repo, "README.md", "skipped")
	write(repo, "shadowed.md", "---\nname: shadowed\nrole: REPO LOSES\n---\nrepo body")
	write(repo, "alpha.md", "---\nrole: Repo Alpha\n---\nA body")
	write(repo, "gamma.md", "no frontmatter gamma")
	write(repo, "notes.txt", "not a persona")
	if err := os.WriteFile(filepath.Join(repo, "bad.md"),
		[]byte("---\nname: bad\n---\n\xff\xfe"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(repo, "adir.md"), 0o755); err != nil {
		t.Fatal(err)
	}

	names := []string{"alpha", "zeta", "shadowed", "gamma", "hidden",
		"bad", "adir", "missing", "README"}

	var want registryAnswer
	personaProbe(t).RunJSON(t, registryProbeSrc, &want,
		pyprobe.Arg(t, []any{ws, repo, names}))

	// --- the CLAIMS, checked first ---
	if containsString(want.List, "README") {
		t.Fatal("CLAIM moved: CPython now lists README")
	}
	if !containsString(want.List, "bad") {
		t.Fatal("CLAIM moved: a byte-tainted file no longer contributes its " +
			"stem to list(), so the list-advertises-what-load-refuses case " +
			"is not being measured")
	}
	if want.Loaded["bad"] != nil {
		t.Fatalf("CLAIM moved: load('bad') returned %v, not None", want.Loaded["bad"])
	}
	if !containsString(want.List, "adir") || want.Loaded["adir"] != nil {
		t.Fatal("CLAIM moved: the *.md DIRECTORY case is no longer listed-but-unloadable")
	}
	if !containsString(want.List, "hidden") {
		t.Fatal("CLAIM moved: a dotfile is no longer globbed as a persona")
	}
	if r := want.Loaded["shadowed"]; r == nil || r[1] != "WS WINS" {
		t.Fatalf("CLAIM moved: the workspace tier no longer wins the stem "+
			"collision (got %v)", r)
	}
	if r := want.Loaded["zeta"]; r == nil || r[0] != "alpha" {
		t.Fatalf("CLAIM moved: load() no longer matches on the STEM (got %v)", r)
	}
	// The stem-vs-name asymmetry: zeta.md declares name "alpha" and does
	// NOT suppress repo/alpha.md, so BOTH are in the file list.
	var alphaFiles int
	for _, f := range want.Files {
		if base := filepath.Base(f); base == "zeta.md" || base == "alpha.md" {
			alphaFiles++
		}
	}
	if alphaFiles != 2 {
		t.Fatalf("CLAIM moved: the suppression key is no longer the STEM — "+
			"expected both zeta.md and alpha.md in %v", want.Files)
	}

	// --- the comparison ---
	reg := New(ws, repo)
	if got := reg.personaFiles(); !reflect.DeepEqual(got, want.Files) {
		t.Errorf("_persona_files ORDER\n  go %v\n  py %v", got, want.Files)
	}
	if got := reg.List(); !reflect.DeepEqual(got, want.List) {
		t.Errorf("list()\n  go %v\n  py %v", got, want.List)
	}
	for _, n := range names {
		got := reg.Load(n)
		w := want.Loaded[n]
		if w == nil {
			if got != nil {
				t.Errorf("load(%q): CPython said None, port returned %+v", n, got)
			}
			continue
		}
		if got == nil {
			t.Errorf("load(%q): CPython returned %v, port said nil", n, w)
			continue
		}
		if got.Name != w[0] || got.Role != w[1] || got.SourceFile != w[2] {
			t.Errorf("load(%q)\n  go %q %q %q\n  py %q %q %q",
				n, got.Name, got.Role, got.SourceFile, w[0], w[1], w[2])
		}
	}
	all := reg.LoadAll()
	if len(all) != len(want.All) {
		t.Fatalf("load_all length: go %d py %d", len(all), len(want.All))
	}
	for i, s := range all {
		if s.Name != want.All[i][0] || s.SourceFile != want.All[i][1] {
			t.Errorf("load_all[%d]\n  go %q %q\n  py %q %q",
				i, s.Name, s.SourceFile, want.All[i][0], want.All[i][1])
		}
	}

	// Vacuity: the corpus has to reach both tiers and both failure modes,
	// or the ordering comparison above is measuring one directory.
	if len(want.Files) < 7 {
		t.Fatalf("only %d files reached the comparison: %v", len(want.Files), want.Files)
	}
	if len(want.All) == len(want.List) {
		t.Fatal("load_all and list are the same length, so no file is " +
			"listed-but-unloadable and the except arms are untested")
	}
}

// The workspace tier is created by RESOLVING it, which is a side effect
// Python hides inside config.personas_dir(). A port whose path helper is
// pure leaves a fresh workspace without the directory the evolver writes
// into.
func TestEnsureWorkspaceDirCreates(t *testing.T) {
	ws := t.TempDir()
	dir, err := EnsureWorkspaceDir(ws)
	if err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(dir)
	if err != nil {
		t.Fatalf("resolving the workspace personas dir did not create it: %v", err)
	}
	if !st.IsDir() {
		t.Fatalf("%s is not a directory", dir)
	}
	if dir != filepath.Join(ws, "personas") {
		t.Fatalf("resolved %q, want <ws>/personas", dir)
	}
}

// A MISS is not cached, which is how a persona written after the registry
// was constructed becomes visible. Caching it would be the obvious Go
// optimization and would change behaviour.
func TestLoadDoesNotCacheAMiss(t *testing.T) {
	dir := t.TempDir()
	reg := NewFromDir(dir)
	if reg.Load("later") != nil {
		t.Fatal("expected a miss on an empty directory")
	}
	if err := os.WriteFile(filepath.Join(dir, "later.md"),
		[]byte("---\nname: later\n---\nb"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := reg.Load("later"); got == nil {
		t.Fatal("a persona written after the miss is invisible — the miss was cached")
	}
}
