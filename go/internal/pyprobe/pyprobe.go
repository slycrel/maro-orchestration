// Package pyprobe is the one CPython probe harness the differential tests
// share.
//
// It exists because there were EIGHT of them. Every package that compares
// itself against the Python grew its own `runPy`/`runPython`/`runPyIn`, and
// they had diverged on the two things that decide whether a differential is
// worth anything:
//
//   - **Whether a failing probe is a skip.** Six treated a non-zero exit as
//     a missing interpreter and skipped. That turned ten of twelve playbook
//     differentials — the seed-bytes pin included — green while running
//     nothing, and it hid exactly the failures that matter: a renamed helper,
//     a changed signature, a safety assert firing (adversarial r9 LOW). A
//     missing interpreter is a skip. A probe that RAN and failed is not.
//
//   - **Whether a writing probe checks where it is about to write.** Four
//     carried a live-workspace refusal, in three different spellings; two
//     carried none at all while setting MARO_WORKSPACE and writing a whole
//     tree. The refusal is not theoretical: on 2026-08-16 a probe overwrote
//     the live ledgers under ~/.maro, and the standing rule from it is that
//     any probe that writes asserts its RESOLVED path first.
//
// A third thing none of the eight did: isolate the OPERATOR'S CONFIG. A
// probe inherits this process's environment, so `config.get` reads the real
// ~/.maro/config.yml — and on this box that file registers a
// `notify.command` which shells out to Telegram and ssh's to another host.
// Every differential that emitted a default-on event has been paging the
// operator, from both runtimes, on every test run (adversarial r11 round 2,
// HIGH). MARO_USER_DIR is the repo's own answer to this ("tests point this
// at tmp so the box's real config doesn't leak in", src/config.py:124) and
// every probe gets it, read-only ones included: an operator's config is not
// an input any comparison here wants.
//
// One helper, one answer to each. Callers that write pass Workspace, and the
// ones that write through a specific Python resolver pass Guard so the
// resolver's own answer is asserted rather than assumed.
package pyprobe

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// SrcDir is the repo's Python tree, located relative to a package's own
// directory. marker is a file that must exist in it — a package naming the
// module it ports gets an honest skip when that module is gone, instead of a
// probe that fails on an import three lines in.
func SrcDir(t *testing.T, marker string) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, marker)); err != nil {
		// MARO_PYPROBE_REQUIRED turns the skip into a failure, for any
		// caller whose whole point is that the differentials RAN.
		//
		// The skip is right on a machine without the Python tree. It is
		// catastrophic in a mutation battery: the battery copies the Go
		// module to a scratch directory (P4 — a Go module builds through
		// its import graph, so a battery owns the whole tree it runs in),
		// `../../../src` no longer resolves, every differential skips, and
		// `go test` prints `ok`. The 2026-08-26 SystemMetrics battery
		// reported 34 of 42 mutants surviving — including one the
		// differential had CAUGHT live an hour earlier — because none of
		// the differentials ran at all. A baseline-green gate does not
		// catch this: the baseline really is green, and empty.
		if os.Getenv("MARO_PYPROBE_REQUIRED") != "" {
			t.Fatalf("MARO_PYPROBE_REQUIRED is set but the python source "+
				"tree is unavailable (%s): %v — this run would have skipped "+
				"every differential and reported ok", marker, err)
		}
		t.Skipf("python source tree unavailable (%s): %v", marker, err)
	}
	return p
}

// liveGuard refuses to run when MARO_WORKSPACE is unset or resolves inside
// the live workspace. It is deliberately spelled with realpath on BOTH sides:
// a symlinked temp dir pointing at ~/.maro passes a string comparison and is
// the shape that makes this class of accident survive review.
const liveGuard = `
import os as _os, sys as _sys
_ws = _os.environ.get("MARO_WORKSPACE", "")
if not _ws:
    raise SystemExit("pyprobe: refusing to run a writing probe with MARO_WORKSPACE unset")
_live = _os.path.realpath(_os.path.expanduser("~/.maro"))
_real = _os.path.realpath(_ws)
if _real == _live or _os.path.commonpath([_real, _live]) == _live:
    raise SystemExit(
        "pyprobe: refusing to run — MARO_WORKSPACE %r resolves to %r, inside the live workspace %r"
        % (_ws, _real, _live))
`

// perCaseGuard is liveGuard for a probe that writes to MORE THAN ONE
// workspace in a single invocation — a table-driven differential with a
// fresh tree per case, which is the shape every metrics probe has.
//
// Those probes could not use Workspace, which is one path, so they each set
// `os.environ["MARO_WORKSPACE"]` in their own loop and the refusal never ran.
// One of them substituted a hand-rolled `assert "/.maro/" not in ws` on the
// UNRESOLVED path — the exact string comparison liveGuard's comment says a
// symlink defeats — and another carried no guard at all (adversarial metrics
// r1, MEDIUM). The answer is not more discipline in each snippet: it is a
// door that does the switching, so a probe cannot set the variable and skip
// the check without visibly not calling this.
const perCaseGuard = `
import os as _os
def _pyprobe_use(_ws):
    """Point MARO_WORKSPACE at _ws, refusing anything inside ~/.maro."""
    if not _ws:
        raise SystemExit("pyprobe: refusing an empty workspace")
    _live = _os.path.realpath(_os.path.expanduser("~/.maro"))
    _real = _os.path.realpath(_ws)
    if _real == _live or _os.path.commonpath([_real, _live]) == _live:
        raise SystemExit(
            "pyprobe: refusing to run — workspace %r resolves to %r, inside "
            "the live workspace %r" % (_ws, _real, _live))
    _os.environ["MARO_WORKSPACE"] = _ws
    return _ws
`

// Probe is one configured CPython probe.
type Probe struct {
	// Marker is the file SrcDir checks for; required unless Stdlib is set.
	Marker string
	// Stdlib says this probe imports nothing from the repo — it asks the
	// interpreter about the LANGUAGE (casefold, repr, float()), not about
	// a ported module. Such a probe gets no PYTHONPATH and no marker, and
	// must not borrow an unrelated module's name to satisfy one: a marker
	// is a claim about what would make the skip honest, and a false claim
	// there is how a probe skips for a reason that has nothing to do with
	// it. Set this and leave Marker empty.
	Stdlib bool
	// Workspace, when set, is exported as MARO_WORKSPACE and turns on the
	// live-workspace refusal. Leave it empty for a read-only probe.
	Workspace string
	// Workspaces is Workspace for a probe that writes to several trees in
	// one invocation. Every path is checked here the way Workspace is, and
	// the probe body switches between them by calling `_pyprobe_use(ws)`
	// rather than assigning os.environ["MARO_WORKSPACE"] itself. Setting
	// both this and Workspace is a mistake, and is refused.
	Workspaces []string
	// Guard is extra Python run before the snippet, for the caller's own
	// "the resolved path is inside the workspace" assertion. It runs after
	// the live-workspace refusal, so it can import freely.
	Guard string
	// Env is extra "K=V" pairs appended to the child's environment, for a
	// probe whose SUBJECT is something only settable before the interpreter
	// starts. The one that exists is PYTHONHASHSEED: a function that
	// iterates a set of strings does not answer the same way twice, and the
	// honest way to test a port against it is to sweep the seed and require
	// the port's deterministic answer to be one CPython can produce — see
	// internal/skills' retire-variants chain case.
	//
	// The four variables Run sets itself — MARO_WORKSPACE, MARO_USER_DIR,
	// PYTHONPATH, PYTHONDONTWRITEBYTECODE — are REFUSED here rather than
	// overridden. Every one of them is load-bearing for a guard (the
	// live-workspace refusal reads MARO_WORKSPACE; the marker check decides
	// PYTHONPATH), and an escape hatch that can switch off the guard from a
	// field named Env is one nobody would think to look at. Set Workspace
	// or UserDir for those.
	Env []string
	// UserDir overrides MARO_USER_DIR. Leave it empty and Run points it at
	// a fresh temp dir — see the package doc. Set it only when the probe's
	// subject IS the user-tier config, and then point it somewhere the test
	// owns; there is no way to ask for the operator's real one, because
	// nothing in a differential should want it.
	UserDir string
}

// Run executes src with the probe's environment and returns its stdout.
//
// A missing python3 skips. A probe that ran and failed is FATAL, with its
// stderr — see the package doc for why that distinction is the whole point.
func (p Probe) Run(t *testing.T, src string, args ...string) string {
	t.Helper()
	// The Go-side half of the same check: a caller that hands this a path
	// outside the test's own temp tree has made a mistake the Python guard
	// would also catch, and catching it here names the caller rather than
	// the subprocess.
	ownedByTest := func(ws string) {
		t.Helper()
		if !strings.HasPrefix(ws, os.TempDir()) &&
			!strings.HasPrefix(ws, "/tmp/") {
			t.Fatalf("pyprobe: refusing to run a writing probe against %q — "+
				"use t.TempDir()", ws)
		}
	}
	if p.Workspace != "" && len(p.Workspaces) > 0 {
		t.Fatal("pyprobe: set Workspace or Workspaces, not both — two " +
			"answers to which tree the probe writes is the bug both guard against")
	}
	switch {
	case p.Workspace != "":
		ownedByTest(p.Workspace)
		src = liveGuard + p.Guard + src
	case len(p.Workspaces) > 0:
		for _, ws := range p.Workspaces {
			ownedByTest(ws)
		}
		src = perCaseGuard + p.Guard + src
	case p.Guard != "":
		src = p.Guard + src
	}
	userDir := p.UserDir
	if userDir == "" {
		userDir = t.TempDir()
	}
	if (p.Marker == "") != p.Stdlib {
		t.Fatalf("pyprobe: a probe needs either a Marker or Stdlib, not "+
			"both and not neither (Marker=%q Stdlib=%v)", p.Marker, p.Stdlib)
	}
	cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
	env := append(cmd.Environ(), "MARO_USER_DIR="+userDir)
	if !p.Stdlib {
		env = append(env, "PYTHONPATH="+SrcDir(t, p.Marker))
	}
	if p.Workspace != "" {
		env = append(env, "MARO_WORKSPACE="+p.Workspace)
	}
	for _, kv := range p.Env {
		name, _, ok := strings.Cut(kv, "=")
		if !ok {
			t.Fatalf("pyprobe: Env entry %q is not K=V", kv)
		}
		switch name {
		case "MARO_WORKSPACE", "MARO_USER_DIR", "PYTHONPATH":
			t.Fatalf("pyprobe: Env must not set %s — it is what a guard reads. "+
				"Use the Workspace or UserDir field.", name)
		}
		env = append(env, kv)
	}
	cmd.Env = env
	out, err := cmd.Output()
	if err == nil {
		return string(out)
	}
	if ee, ok := err.(*exec.ExitError); ok {
		t.Fatalf("the CPython probe FAILED (exit %d). This is not a missing "+
			"interpreter — the differential cannot report green.\nstderr:\n%s",
			ee.ExitCode(), ee.Stderr)
	}
	if _, lookErr := exec.LookPath("python3"); lookErr != nil {
		t.Skipf("python3 unavailable: %v", lookErr)
	}
	t.Fatalf("python3 is present but the probe could not run: %v", err)
	return ""
}

// RunJSON runs the probe and decodes its stdout into out.
func (p Probe) RunJSON(t *testing.T, src string, out any, args ...string) {
	t.Helper()
	raw := p.Run(t, src, args...)
	if err := json.Unmarshal([]byte(raw), out); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, raw)
	}
}

// Arg marshals a Go value as one JSON argv entry, so a probe's fixtures are
// written as Go values rather than as hand-escaped JSON inside a string
// literal — which is where three of the eight harnesses' fixtures came from
// and where a mis-escaped quote reads as a port bug.
func Arg(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
