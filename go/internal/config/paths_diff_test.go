package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// Workspace() against config.workspace_root(), and Home() against
// config._maro_dir().
//
// The pair is measured TOGETHER on purpose. The two Python resolvers
// differ by exactly one call — workspace_root ends in `.resolve()` and
// _maro_dir does not — so a differential covering only one of them cannot
// tell a missing resolve from a spurious one, and a fix applied to the
// wrong resolver would still look green.
//
// Nothing here existed before. That is why an earlier round could record
// "`.resolve()` deliberately NOT ported" with a rationale that was simply
// untrue: every fixture in the suite set MARO_WORKSPACE to an absolute,
// symlink-free, already-clean t.TempDir() path, and on such a path
// resolving and not resolving give the same answer. The claim was never
// wrong in a way any test could notice.
//
// Read-only by construction: neither resolver creates anything, which is
// what lets the fixtures be ordinary paths rather than a writing probe's
// guarded tree. The paths are still handed to pyprobe's per-case door so
// the live-workspace refusal runs over every one of them.
//
// RELATIVE inputs are deliberately absent here and covered one level
// down, in pypath's 30-row Realpath table with a matched cwd: pyprobe
// refuses a workspace path outside the test's own temp tree, which a
// relative value is by definition. The composition is honest — this test
// pins the WIRING (which resolver each Go function stands for), and the
// primitive's own differential pins what resolution means.
const configPathPySrc = `
import json, os, sys

root = sys.argv[1]
os.makedirs(os.path.join(root, "real", "deep"), exist_ok=True)

def link(name, target):
    p = os.path.join(root, name)
    if not os.path.islink(p) and not os.path.exists(p):
        os.symlink(target, p)

link("link_to_real", os.path.join(root, "real"))
link("deeplink", os.path.join(root, "real", "deep"))

import config

out = []
for var, val in json.loads(sys.argv[2]):
    for name in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        os.environ.pop(name, None)
    if var == "MARO_USER_DIR":
        os.environ[var] = val
        out.append(str(config._maro_dir()))
    else:
        # Guard on the EXPANDED path, which is the same string the Go side
        # hands pyprobe. Guarding the literal "~/..." would check a path
        # nobody uses -- a door that checks a different string from the one
        # the subject reads is not a door.
        _pyprobe_use(os.path.expanduser(val))
        os.environ.pop("MARO_WORKSPACE", None)
        os.environ[var] = val
        out.append(str(config.workspace_root()))
print(json.dumps(out))
`

func TestWorkspaceAndHomeResolveTheWayTheirPythonTwinsDo(t *testing.T) {
	root := t.TempDir()

	type row struct{ name, env, val string }
	rows := []row{
		{"an existing dir", "MARO_WORKSPACE", root + "/real"},
		{"a dir that does not exist yet", "MARO_WORKSPACE", root + "/missing"},
		{"a missing dir several levels deep", "MARO_WORKSPACE", root + "/missing/a/b"},
		{"a symlink to a directory", "MARO_WORKSPACE", root + "/link_to_real"},
		{"a suffix under a symlink", "MARO_WORKSPACE", root + "/link_to_real/deep"},
		// The row that separates follow-then-pop from lexical collapse:
		// deeplink -> <root>/real/deep, so this is <root>/real and NOT
		// <root>.
		{"dot-dot after a symlink pointing deeper", "MARO_WORKSPACE", root + "/deeplink/.."},
		{"dot segments through a missing dir", "MARO_WORKSPACE", root + "/gone/../other"},
		{"a trailing slash", "MARO_WORKSPACE", root + "/real/"},
		{"a doubled slash", "MARO_WORKSPACE", root + "//real"},
		// The legacy compat names must resolve identically — they are the
		// same branch in both runtimes, and a port that resolved only the
		// first would be right on every test that uses the first.
		{"the OPENCLAW_WORKSPACE compat name", "OPENCLAW_WORKSPACE", root + "/link_to_real"},
		{"the WORKSPACE_ROOT compat name", "WORKSPACE_ROOT", root + "/link_to_real"},
		// _maro_dir does NOT resolve. Same inputs, so a fix applied to the
		// wrong resolver shows up as these rows changing.
		{"the user dir is a symlink and stays one", "MARO_USER_DIR", root + "/link_to_real"},
		{"the user dir carries dot segments", "MARO_USER_DIR", root + "/real/deep/../other"},
		{"the user dir has a trailing slash", "MARO_USER_DIR", root + "/real/"},
		// Tilde rows. HOME is repointed at the fixture root for BOTH
		// runtimes below, so `~` names a directory this test owns. These
		// are the rows that separate pathlib's normalisation from
		// filepath.Join's cleaning: Join removes `..`, Path does not.
		{"a tilde path", "MARO_USER_DIR", "~/real"},
		{"a tilde path with dot segments", "MARO_USER_DIR", "~/real/deep/../other"},
		{"a bare tilde", "MARO_USER_DIR", "~"},
		{"a tilde with a doubled slash", "MARO_USER_DIR", "~//real"},
		{"a tilde workspace with dot segments", "MARO_WORKSPACE", "~/real/deep/../other"},
	}

	// Both sides expand `~` against this. Set before the probe runs so the
	// child inherits it — Probe.Run builds the child env from this
	// process's, so a t.Setenv here reaches CPython too.
	t.Setenv("HOME", root)

	pairs := make([][2]string, len(rows))
	spaces := []string{}
	for i, r := range rows {
		pairs[i] = [2]string{r.env, r.val}
		// pyprobe's door takes the paths it will point MARO_WORKSPACE at,
		// and checks each is inside the test's own temp tree. A `~` value
		// is not one until it is expanded, so it is expanded HERE for the
		// guard — the guard then verifies the same string the probe will
		// use, which is the point of handing it over at all.
		if r.env != "MARO_USER_DIR" {
			v := r.val
			if strings.HasPrefix(v, "~") {
				v = root + strings.TrimPrefix(v, "~")
			}
			spaces = append(spaces, v)
		}
	}

	var want []string
	pyprobe.Probe{Marker: "config.py", Workspaces: spaces}.
		RunJSON(t, configPathPySrc, &want, root, pyprobe.Arg(t, pairs))
	if len(want) != len(rows) {
		t.Fatalf("probe answered %d rows, want %d", len(want), len(rows))
	}

	byName := map[string]string{}
	for i, r := range rows {
		byName[r.name] = want[i]
	}
	// Anti-vacuity, from CPython's answers rather than by eye. Three
	// independent ways this test could agree while proving nothing: the
	// symlink farm failing to build (every row would answer its input),
	// the resolve half being absent from the PYTHON side too, and the two
	// resolvers turning out to behave identically — in which case the
	// pairing that makes this test worth writing does not exist.
	for _, c := range []struct{ name, want string }{
		{"a symlink to a directory", root + "/real"},
		{"dot-dot after a symlink pointing deeper", root + "/real"},
		{"the user dir is a symlink and stays one", root + "/link_to_real"},
	} {
		if got, ok := byName[c.name]; !ok || got != c.want {
			t.Fatalf("CPython answered %q for %q, want %q — the fixture tree "+
				"is not producing the distinction this test is about",
				got, c.name, c.want)
		}
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			for _, name := range []string{"MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"} {
				t.Setenv(name, "")
			}
			t.Setenv(r.env, r.val)
			var got string
			if r.env == "MARO_USER_DIR" {
				got = Home()
			} else {
				got = Workspace()
			}
			if got != want[i] {
				t.Errorf("%s=%s\n go: %q\n py: %q", r.env, r.val, got, want[i])
			}
		})
	}
}

// A guard for the seam the differential above cannot reach: the DEFAULT
// branch carries no .resolve() in the Python either, so a port that
// resolved unconditionally would be wrong in the other direction — and
// every fixture above sets an env var, so none of them would notice.
func TestTheDefaultWorkspaceBranchIsNotResolved(t *testing.T) {
	home := t.TempDir()
	real := filepath.Join(home, "real")
	if err := os.MkdirAll(real, 0o775); err != nil {
		t.Fatal(err)
	}
	// A HOME whose own path is a symlink. If Workspace() resolved the
	// default branch, the answer would name the target instead.
	linkedHome := filepath.Join(t.TempDir(), "home")
	if err := os.Symlink(home, linkedHome); err != nil {
		t.Skipf("cannot create a symlink here: %v", err)
	}
	for _, name := range []string{"MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"} {
		t.Setenv(name, "")
	}
	t.Setenv("HOME", linkedHome)

	got := Workspace()
	wantPrefix := linkedHome + "/"
	if !strings.HasPrefix(got, wantPrefix) {
		t.Errorf("the default branch resolved its symlinked HOME: got %q, "+
			"want a path under %q — Python's `Path.home() / \".maro\" / "+
			"\"workspace\"` has no .resolve() on it", got, linkedHome)
	}
}
