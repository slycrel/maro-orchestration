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

// Probe is one configured CPython probe.
type Probe struct {
	// Marker is the file SrcDir checks for; required.
	Marker string
	// Workspace, when set, is exported as MARO_WORKSPACE and turns on the
	// live-workspace refusal. Leave it empty for a read-only probe.
	Workspace string
	// Guard is extra Python run before the snippet, for the caller's own
	// "the resolved path is inside the workspace" assertion. It runs after
	// the live-workspace refusal, so it can import freely.
	Guard string
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
	if p.Workspace != "" {
		// The Go-side half of the same check: a caller that hands this a
		// path outside the test's own temp tree has made a mistake the
		// Python guard would also catch, and catching it here names the
		// caller rather than the subprocess.
		if !strings.HasPrefix(p.Workspace, os.TempDir()) &&
			!strings.HasPrefix(p.Workspace, "/tmp/") {
			t.Fatalf("pyprobe: refusing to run a writing probe against %q — "+
				"use t.TempDir()", p.Workspace)
		}
		src = liveGuard + p.Guard + src
	} else if p.Guard != "" {
		src = p.Guard + src
	}
	userDir := p.UserDir
	if userDir == "" {
		userDir = t.TempDir()
	}
	cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
	env := append(cmd.Environ(), "PYTHONPATH="+SrcDir(t, p.Marker),
		"MARO_USER_DIR="+userDir)
	if p.Workspace != "" {
		env = append(env, "MARO_WORKSPACE="+p.Workspace)
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
