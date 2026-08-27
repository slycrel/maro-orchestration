package persona

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
)

// This file exists because a mutation sweep found the hole it closes.
//
// internal/persona has three os.MkdirAll sites — EnsureWorkspaceDir,
// SaveManifest, RecordDispatch — and every one of them passed
// record.NewDirMode to a suite that never looked at a mode. Measured: with
// all three rewritten to 0o700 the package still reported `ok`. Directory
// modes are part of this port's contract, and "never asserted" is
// indistinguishable from "guarded" until something mutates the value.
//
// The three creation paths are Python's:
//
//	config.personas_dir()                  -> <ws>/personas
//	save_manifest()                        -> parent of output_path (+ parents)
//	record_persona_dispatch()              -> the memory dir
//
// all of which bottom out in `Path.mkdir(parents=True, exist_ok=True)` with
// the DEFAULT mode 0o777, which the kernel then masks by the process umask.
// Go's os.MkdirAll(path, perm) masks the same way, so the two agree only if
// the port passes 0o777 — which is exactly what record.NewDirMode is.
const dirModeProbeSrc = `
import json, os, stat, sys
from pathlib import Path

ws = Path(os.environ["MARO_WORKSPACE"])

# Observe the umask without leaving it changed: there is no getter, so the
# only way to read it is to set it and put it back.
u = os.umask(0)
os.umask(u)

import config, persona
from orch_items import memory_dir

modes = {}

# 1. What PersonaRegistry() with no argument resolves -- and mkdirs.
modes["personas_dir"] = stat.S_IMODE(os.stat(config.personas_dir()).st_mode)

# 2. save_manifest's parent mkdir, through the REAL function, with parents
#    that do not exist yet so the intermediate mode is observable too.
target = ws / "deep" / "nested" / "manifest.json"
persona.save_manifest(output_path=target,
                      registry=persona.PersonaRegistry(personas_dir=ws / "personas"))
modes["manifest_parent"] = stat.S_IMODE(os.stat(target.parent).st_mode)
modes["manifest_intermediate"] = stat.S_IMODE(os.stat(target.parent.parent).st_mode)

# 3. record_persona_dispatch's parent mkdir, through the REAL function.
persona.record_persona_dispatch("g", "p", 1.0)
modes["dispatch_parent"] = stat.S_IMODE(os.stat(memory_dir()).st_mode)

# exist_ok=True must NOT re-chmod a directory that already exists. If it did,
# a second run would widen a directory the operator had deliberately closed.
pre = ws / "pre"
pre.mkdir(mode=0o700)
pre.mkdir(parents=True, exist_ok=True)

print(json.dumps({
    "umask": u,
    "modes": modes,
    "exist_ok_mode": stat.S_IMODE(os.stat(pre).st_mode),
}))
`

type dirModeAnswer struct {
	Umask       int            `json:"umask"`
	Modes       map[string]int `json:"modes"`
	ExistOKMode int            `json:"exist_ok_mode"`
}

// TestNewDirectoryModesMatchCPython pins the mode of every directory this
// package creates, against the mode CPython gives the same directory.
//
// The expected value is DERIVED from the probe's own umask rather than
// assuming the common 022. Both engines run as the same process tree and so
// inherit the same umask; hard-coding 0o755 here would pass on this box and
// silently stop measuring on any box configured differently.
func TestNewDirectoryModesMatchCPython(t *testing.T) {
	var py dirModeAnswer
	personaProbe(t).RunJSON(t, dirModeProbeSrc, &py)

	// CLAIM, checked BEFORE the port is compared: CPython creates all four
	// directories at 0o777 masked by the umask it reported.
	want := os.FileMode(0o777 &^ py.Umask)
	for _, k := range []string{"personas_dir", "manifest_parent",
		"manifest_intermediate", "dispatch_parent"} {
		got, ok := py.Modes[k]
		if !ok {
			t.Fatalf("CLAIM moved: the probe no longer reports %q (got %v)", k, py.Modes)
		}
		if os.FileMode(got) != want {
			t.Fatalf("CLAIM moved: CPython created %s at %#o, not 0o777&^umask(%#o) = %#o",
				k, got, py.Umask, want)
		}
	}
	// VACUITY FLOOR. Every mutation this test is meant to catch narrows the
	// mode, and a umask that already narrows it to the same value would make
	// the comparison agree for the wrong reason. On a box with umask 0o077,
	// `want` would BE 0o700 and the 0o700 mutant would survive green.
	if want == 0o700 || want == 0 {
		t.Fatalf("VACUOUS: umask %#o leaves want=%#o, which cannot distinguish "+
			"a narrowed mode from the correct one", py.Umask, want)
	}
	// CLAIM: mkdir(exist_ok=True) does not re-chmod an existing directory.
	if py.ExistOKMode != 0o700 {
		t.Fatalf("CLAIM moved: exist_ok mkdir re-chmod'ed an existing 0o700 "+
			"directory to %#o", py.ExistOKMode)
	}

	// Now the port. Each case drives the REAL function that owns its
	// MkdirAll -- naming the directory and stat'ing it without going through
	// the function would leave the mode argument unmeasured, which is the
	// defect this file was written to fix.
	t.Run("EnsureWorkspaceDir", func(t *testing.T) {
		ws := t.TempDir()
		dir, err := EnsureWorkspaceDir(ws)
		if err != nil {
			t.Fatal(err)
		}
		assertMode(t, dir, want)
	})

	t.Run("SaveManifest", func(t *testing.T) {
		ws := t.TempDir()
		target := filepath.Join(ws, "deep", "nested", "manifest.json")
		if _, err := SaveManifest(ws, target, "json", NewFromDir(t.TempDir())); err != nil {
			t.Fatal(err)
		}
		assertMode(t, filepath.Dir(target), want)
		// The INTERMEDIATE too: Python's mkdir(parents=True) and Go's
		// MkdirAll both create it, and a port that created parents with a
		// different mode than the leaf would pass a leaf-only check.
		assertMode(t, filepath.Dir(filepath.Dir(target)), want)
	})

	t.Run("RecordDispatch", func(t *testing.T) {
		ws := t.TempDir()
		if err := RecordDispatch(ws, "g", "p", 1.0, false, ""); err != nil {
			t.Fatal(err)
		}
		assertMode(t, orch.MemoryDir(ws), want)
	})

	t.Run("ExistingDirectoryIsNotReChmodded", func(t *testing.T) {
		ws := t.TempDir()
		dir := filepath.Join(ws, "personas")
		if err := os.Mkdir(dir, 0o700); err != nil {
			t.Fatal(err)
		}
		if _, err := EnsureWorkspaceDir(ws); err != nil {
			t.Fatal(err)
		}
		assertMode(t, dir, 0o700)
	})
}

func assertMode(t *testing.T, path string, want os.FileMode) {
	t.Helper()
	st, err := os.Stat(path)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	if !st.IsDir() {
		t.Fatalf("%s is not a directory", path)
	}
	if got := st.Mode().Perm(); got != want {
		t.Fatalf("%s has mode %#o, CPython gives %#o", path, got, want)
	}
}

// TestDispatchLogPathRejectsARegularFile is the second thing the sweep
// surfaced, in a different shape: every fixture in this package that has no
// memory directory has NOTHING at that path, so an `err == nil` check and an
// `err == nil && IsDir()` check agree everywhere and the difference is
// invisible. This hands the creation path a REGULAR FILE where the directory
// belongs and pins that the port surfaces it rather than silently doing
// nothing -- Python's mkdir raises FileExistsError there (exist_ok only
// forgives an existing DIRECTORY), which its blanket `except Exception: pass`
// then swallows.
func TestRecordDispatchOnAFileWhereTheDirBelongs(t *testing.T) {
	var py struct {
		Raised string `json:"raised"`
		Wrote  bool   `json:"wrote"`
	}
	personaProbe(t).RunJSON(t, `
import json, os
from pathlib import Path
import persona
from orch_items import memory_dir

ws = Path(os.environ["MARO_WORKSPACE"])
mem = memory_dir()          # resolves, and may create it
if mem.exists():
    import shutil; shutil.rmtree(mem)
mem.parent.mkdir(parents=True, exist_ok=True)
mem.write_text("i am a file, not a directory")

raised = ""
try:
    Path(mem).mkdir(parents=True, exist_ok=True)
except Exception as e:
    raised = type(e).__name__

# The real function swallows it -- this measures that nothing was written and
# nothing escaped.
persona.record_persona_dispatch("g", "p", 1.0)
print(json.dumps({
    "raised": raised,
    "wrote": mem.read_text() != "i am a file, not a directory",
}))
`, &py)

	// CLAIM first: exist_ok=True does NOT forgive a regular file.
	if py.Raised != "FileExistsError" {
		t.Fatalf("CLAIM moved: mkdir(exist_ok=True) over a regular file raised "+
			"%q, not FileExistsError", py.Raised)
	}
	if py.Wrote {
		t.Fatalf("CLAIM moved: record_persona_dispatch wrote through a regular " +
			"file standing where the memory directory belongs")
	}

	// The port returns the error instead of swallowing it (the documented
	// divergence for this function), so the assertion is that it FAILS --
	// not that it quietly does nothing.
	ws := t.TempDir()
	mem := orch.MemoryDir(ws)
	if err := os.MkdirAll(filepath.Dir(mem), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(mem, []byte("i am a file, not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := RecordDispatch(ws, "g", "p", 1.0, false, ""); err == nil {
		t.Fatal("RecordDispatch answered nil with a regular file where the " +
			"memory directory belongs; CPython raises FileExistsError there")
	}
	body, err := os.ReadFile(mem)
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "i am a file, not a directory" {
		t.Fatalf("the port wrote through the regular file: %q", body)
	}
}
