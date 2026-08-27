package persona

import (
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Registry scans the workspace and repo `personas/` directories, workspace
// winning on a name collision.
//
// Python's constructor RESOLVES those two directories itself, through five
// fallbacks: config.personas_dir(), orch_root()/personas, the repo layout
// relative to __file__, the packaged maro_assets copy, and finally
// cwd/personas. This port takes both as ARGUMENTS, for the reason
// internal/orch's ProjectsRoot gives at length: after the 2026-08-16
// live-ledger incident there is one resolution order in this runtime and it
// is passed in, not discovered. EnsureWorkspaceDir is the one piece of that
// chain reproduced here, because it has a SIDE EFFECT (see its doc).
//
// A note on what the "workspace wins" rule actually keys on: it is the
// filename STEM, not the parsed `name`. A workspace file `zeta.md` whose
// frontmatter says `name: alpha` does NOT suppress a repo `alpha.md` — both
// are listed, both parse to the name "alpha", and Load returns whichever
// comes first in the file order. Ported as-is.
type Registry struct {
	repoDir string
	wsDir   string
	cache   map[string]*Spec
}

// NewFromDir is `PersonaRegistry(personas_dir=...)`: one explicit directory,
// used as the REPO tier with no workspace tier at all. Python's comment
// calls this the test constructor and that is what it is.
func NewFromDir(dir string) *Registry {
	return &Registry{repoDir: dir, cache: map[string]*Spec{}}
}

// New is the two-tier registry. Either directory may be "" for absent,
// which is Python's None: an absent workspace tier is the common case on a
// fresh install, and an absent repo tier is what Python's chain lands on
// when nothing exists.
func New(wsDir, repoDir string) *Registry {
	return &Registry{wsDir: wsDir, repoDir: repoDir, cache: map[string]*Spec{}}
}

// EnsureWorkspaceDir is Python config.personas_dir(): <workspace>/personas,
// CREATED if missing.
//
// The mkdir is inside the name in Python, and it is observable twice over.
// Constructing a PersonaRegistry with no explicit directory creates the
// workspace personas directory as a side effect — so `ws.exists()` on the
// next line is nearly always true, and the branch that leaves _ws_dir unset
// is reached only when the mkdir RAISES. Callers reproduce that by passing
// "" for wsDir when this returns an error, which is what the surrounding
// `except Exception: pass` does.
func EnsureWorkspaceDir(ws string) (string, error) {
	dir := filepath.Join(ws, "personas")
	if err := os.MkdirAll(dir, record.NewDirMode); err != nil {
		return "", err
	}
	return dir, nil
}

// RepoDir is `orch_root() / "personas"` for a caller that already knows the
// repo root. It does NOT check existence — Python's `.exists()` guard lives
// in the constructor and is reproduced by New treating an unreadable
// directory as empty.
func RepoDir(repoRoot string) string { return filepath.Join(repoRoot, "personas") }

// personaFiles collects persona files from workspace then repo, workspace
// stems suppressing repo ones.
//
// `sorted(dir.glob("*.md"))` twice, and both halves matter:
//
//   - The pattern is a CONSTANT, so a suffix test is exact — see
//     pytext.FnMatch's doc for why a NAME-derived pattern would not be. It
//     matches dotfiles (`.hidden.md` is a persona to CPython, measured) and
//     it matches DIRECTORIES (`adir.md/` is globbed, reaches the parser and
//     raises IsADirectoryError there, measured).
//   - `sorted()` over Paths is by code point over the surrogateescape
//     decoding, which is pypath.FSLess and is NOT sort.Strings for an
//     undecodable byte in the two-byte range.
func (r *Registry) personaFiles() []string {
	seen := map[string]bool{}
	var files []string

	for _, p := range sortedMDFiles(r.wsDir) {
		if pypath.Name(p) != "README.md" {
			files = append(files, p)
			seen[pypath.Stem(pypath.Name(p))] = true
		}
	}
	for _, p := range sortedMDFiles(r.repoDir) {
		if pypath.Name(p) != "README.md" && !seen[pypath.Stem(pypath.Name(p))] {
			files = append(files, p)
		}
	}
	return files
}

// sortedMDFiles is one tier's `sorted(dir.glob("*.md"))`. An empty dir name
// is Python's None (the tier is absent) and an unreadable directory is
// Python's `.exists()` returning False or a glob over a non-directory
// yielding nothing — all three answer with no files rather than an error,
// because Python's constructor already decided those are not exceptional.
func sortedMDFiles(dir string) []string {
	if dir == "" {
		return nil
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var names []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".md") {
			names = append(names, e.Name())
		}
	}
	sort.SliceStable(names, func(i, j int) bool { return pypath.FSLess(names[i], names[j]) })
	out := make([]string, len(names))
	for i, n := range names {
		out[i] = filepath.Join(dir, n)
	}
	return out
}

// List returns the available persona names, sorted.
//
// The `except` arm is not a formality: a file the parser REFUSES still
// contributes its filename STEM to this list, so List can advertise a name
// that Load answers None for. Measured on a byte-tainted file and on a
// directory named `*.md`. Ported, because the manifest generator loops over
// exactly this list and its own `if spec is None: continue` is what covers
// the gap on that path.
func (r *Registry) List() []string {
	names := []string{}
	for _, p := range r.personaFiles() {
		if spec, err := ParseFile(p); err == nil {
			names = append(names, spec.Name)
		} else {
			names = append(names, pypath.Stem(pypath.Name(p)))
		}
	}
	// `sorted(names)` — CPython compares str by code point, which for
	// valid UTF-8 is Go's byte order.
	sort.SliceStable(names, func(i, j int) bool { return names[i] < names[j] })
	return names
}

// Load returns a persona by name, or nil when nothing matches.
//
// Two properties are Python's and are easy to lose:
//
//   - The match is `spec.name == name OR p.stem == name`, so a file can be
//     loaded under either spelling, and the CACHE is keyed by the NAME THE
//     CALLER ASKED FOR. Loading "zeta" and "alpha" from one file leaves two
//     cache entries pointing at one spec.
//   - A MISS is not cached. Python re-walks the directory on every failed
//     lookup, which is how a persona written after the registry was
//     constructed becomes visible without a new registry.
//
// The returned pointer is the cached object, exactly as Python returns the
// cached instance: a caller that mutates it mutates the registry. Compose
// relies on that (its single-name fast path returns the spec unchanged).
func (r *Registry) Load(name string) *Spec {
	if spec, ok := r.cache[name]; ok {
		return spec
	}
	for _, p := range r.personaFiles() {
		spec, err := ParseFile(p)
		if err != nil {
			continue
		}
		if spec.Name == name || pypath.Stem(pypath.Name(p)) == name {
			r.cache[name] = spec
			return spec
		}
	}
	return nil
}

// LoadAll returns every persona that parses, in file order — which is
// workspace-then-repo, each half sorted, NOT sorted overall and NOT sorted
// by name. `load_all` and `list` therefore disagree about order on purpose.
func (r *Registry) LoadAll() []*Spec {
	specs := []*Spec{}
	for _, p := range r.personaFiles() {
		if spec, err := ParseFile(p); err == nil {
			specs = append(specs, spec)
		}
	}
	return specs
}
