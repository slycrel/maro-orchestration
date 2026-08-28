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
// A fourth thing, found 2026-08-27 and the reason the sandbox is no longer
// optional: "this probe is read-only" was a CALLER'S JUDGEMENT and nothing
// checked it. `create_skill_variant` returns a mutated object and looks like
// a pure transform; it also writes the captain's log, inside a bare except.
// Its probe named no Workspace, so MARO_WORKSPACE was unset, so the write
// resolved captains_log's default — ~/.maro/workspace — and 648 synthetic
// rows went into the operator's live log over three days. 648 of the 649
// rows the log received in that window were this test.
//
// So every probe now gets MARO_WORKSPACE and HOME pointed at temp dirs, and
// an undeclared probe that WROTE to either is a FAILURE naming the caller.
// The mitigation alone would only have moved the damage somewhere quiet;
// the assertion is what makes the misdeclaration visible.
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

// operatorHome is the real home directory, captured at package
// initialisation — before any test's t.Setenv can move it. Two things need
// it: the live-workspace refusal, which must keep meaning the OPERATOR'S
// ~/.maro rather than the sandbox's, and the decision below about whether a
// test has deliberately repointed HOME.
var operatorHome = func() string {
	if h, err := os.UserHomeDir(); err == nil && h != "" {
		return h
	}
	return "/root"
}()

// childHome decides what HOME the probe runs with.
//
// A test that has already repointed HOME with t.Setenv did so on purpose:
// its SUBJECT is `~` expansion, and both sides are meant to expand to the
// same fixture root — internal/pypath asserts exactly that. Overriding it
// here would compare two different homes and call the difference a port bug.
// Every other probe gets a sandbox it cannot damage.
func childHome(t *testing.T) (home string, sandboxed bool) {
	if h := os.Getenv("HOME"); h != "" && h != operatorHome {
		return h, false
	}
	return t.TempDir(), true
}

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
_live = _os.path.realpath(_os.environ.get("MARO_PYPROBE_LIVE_HOME") or _os.path.expanduser("~") + "/.maro")
_real = _os.path.realpath(_ws)
if _real == _live or _os.path.commonpath([_real, _live]) == _live:
    raise SystemExit(
        "pyprobe: refusing to run — MARO_WORKSPACE %r resolves to %r, inside the live workspace %r"
        % (_ws, _real, _live))
`

// blockerPreamble is the ONE way a probe makes a module unimportable.
//
// It is prepended to every probe because there were three hand-copies of
// it, and the third lost a line. L59's tripwire already said what the line
// was for — "prove the module is actually absent (pop + finder) rather
// than stubbed" — and the head probe still shipped without it: a meta-path
// finder is never consulted for a module already in sys.modules, and the
// probe had imported captains_log two lines earlier to patch it. The
// fixture ran the LIVE module and agreed with the port for the wrong
// reason.
//
// So the eviction is not advice here, and neither is the proof: after
// installing the finder this IMPORTS each name and refuses to continue if
// one still resolves. A fixture that cannot fail is worse than no fixture.
const blockerPreamble = `
import importlib as _pyprobe_il, sys as _pyprobe_sys


class _PyprobeBlocker:
    """A meta-path finder that makes named modules genuinely unimportable.

    A stub whose every attribute raises is a DIFFERENT state: the import
    statement SUCCEEDS against it and the failure lands somewhere else,
    with the name bound. The message is CPython's own, exactly, because
    an operator searches for it.
    """

    def __init__(self, names):
        self.names = set(names)

    def find_spec(self, name, path=None, target=None):
        if name in self.names:
            raise ModuleNotFoundError("No module named %r" % name, name=name)
        return None


def _pyprobe_block(names):
    """Block ` + "`names`" + `, and prove they are blocked. Returns the finder."""
    names = list(names or [])
    for _n in names:
        _pyprobe_sys.modules.pop(_n, None)
    _b = _PyprobeBlocker(names)
    _pyprobe_sys.meta_path.insert(0, _b)
    for _n in names:
        try:
            _pyprobe_il.import_module(_n)
        except ModuleNotFoundError:
            continue
        except BaseException as _e:
            raise SystemExit(
                "pyprobe: blocking %r raised %s rather than "
                "ModuleNotFoundError: %s" % (_n, type(_e).__name__, _e))
        raise SystemExit(
            "pyprobe: %r is STILL IMPORTABLE after blocking it — the "
            "fixture that depends on it being gone cannot fail" % _n)
    return _b


def _pyprobe_unblock(_b):
    _pyprobe_sys.meta_path.remove(_b)
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
    _live = _os.path.realpath(_os.environ.get("MARO_PYPROBE_LIVE_HOME") or _os.path.expanduser("~") + "/.maro")
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
	// live-workspace refusal. It says "this is the tree I will assert
	// against" — NOT "this probe writes".
	//
	// A probe that leaves it empty still gets a sandbox: MARO_WORKSPACE and
	// HOME both point at temp dirs the test owns, and Run FAILS if the
	// probe wrote to either. "Read-only" was the caller's own judgement and
	// nothing checked it, which is how `create_skill_variant` — a function
	// that returns a mutated object and looks like a pure transform — put
	// 648 synthetic SKILL_VARIANT_CREATED rows into the operator's live
	// captain's log between 2026-08-25 and 2026-08-27, 648 of the 649 rows
	// the log received in those three days. Its probe declared nothing, so
	// MARO_WORKSPACE was unset, so captains_log resolved its default:
	// ~/.maro/workspace. The write is wrapped in a bare except, so it could
	// never fail loudly enough to be noticed.
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
	src = blockerPreamble + src
	userDir := p.UserDir
	if userDir == "" {
		userDir = t.TempDir()
	}
	// The sandbox every probe gets, declared or not. A probe that names no
	// workspace is still pointed at one it cannot damage, and HOME goes
	// with it because MARO_WORKSPACE only covers the modules that read it —
	// anything expanding `~` itself would walk straight back out.
	sandbox := ""
	if p.Workspace == "" && len(p.Workspaces) == 0 {
		sandbox = t.TempDir()
	}
	home, homeSandboxed := childHome(t)
	if (p.Marker == "") != p.Stdlib {
		t.Fatalf("pyprobe: a probe needs either a Marker or Stdlib, not "+
			"both and not neither (Marker=%q Stdlib=%v)", p.Marker, p.Stdlib)
	}
	cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
	// The refusal must keep meaning the OPERATOR'S workspace, not the
	// sandbox's — expanding `~` in the child would now resolve to the temp
	// HOME and the guard would wave through the very path it exists to
	// refuse.
	liveHome := filepath.Join(operatorHome, ".maro")
	env := append(cmd.Environ(), "MARO_USER_DIR="+userDir, "HOME="+home,
		"MARO_PYPROBE_LIVE_HOME="+liveHome)
	if !p.Stdlib {
		env = append(env, "PYTHONPATH="+SrcDir(t, p.Marker))
	}
	if p.Workspace != "" {
		env = append(env, "MARO_WORKSPACE="+p.Workspace)
	}
	if sandbox != "" {
		env = append(env, "MARO_WORKSPACE="+sandbox)
	}
	for _, kv := range p.Env {
		name, _, ok := strings.Cut(kv, "=")
		if !ok {
			t.Fatalf("pyprobe: Env entry %q is not K=V", kv)
		}
		switch name {
		case "MARO_WORKSPACE", "MARO_USER_DIR", "PYTHONPATH", "HOME",
			"MARO_PYPROBE_LIVE_HOME":
			t.Fatalf("pyprobe: Env must not set %s — it is what a guard reads. "+
				"Use the Workspace or UserDir field.", name)
		}
		env = append(env, kv)
	}
	cmd.Env = env
	out, err := cmd.Output()
	if err == nil {
		checkHome := home
		if !homeSandboxed {
			// The test owns this HOME and put fixtures in it.
			checkHome = ""
		}
		assertUndeclaredProbeWroteNothing(t, sandbox, checkHome)
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

// assertUndeclaredProbeWroteNothing turns "this probe is read-only" from a
// caller's claim into a checked one. sandbox is empty for a probe that DID
// declare a workspace — those assert their own tree — so only the
// undeclared ones are held to it. HOME is checked for every probe: nothing
// here should be reaching the operator's home directory at all.
func assertUndeclaredProbeWroteNothing(t *testing.T, sandbox, home string) {
	t.Helper()
	check := func(dir, what string) {
		if dir == "" {
			return
		}
		ents, err := os.ReadDir(dir)
		if err != nil || len(ents) == 0 {
			return
		}
		names := make([]string, 0, len(ents))
		for _, e := range ents {
			names = append(names, e.Name())
		}
		t.Fatalf("pyprobe: this probe declared no Workspace but WROTE to "+
			"its %s (%v). It is not read-only. Give it Workspace: "+
			"t.TempDir() and assert what it produces — a probe whose writes "+
			"nobody names is a probe that writes wherever the environment "+
			"sends it.", what, names)
	}
	check(sandbox, "sandbox workspace")
	check(home, "sandboxed HOME")
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
