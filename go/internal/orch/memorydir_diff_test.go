package orch

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The mkdir that lives inside a NAME.
//
// Five of config.py's path helpers create the directory they resolve —
// memory, output, projects, skills, personas — while secrets_dir and
// playbook_path do not. So `memory_dir()` is not a join in the original:
// it CREATES, at a MODE, and it can FAIL. syshealth r3 rated the first
// instance HIGH; this pins the general property for the memory family
// against CPython rather than against a transcription of it.
//
// What the probe measures, per call:
//   - whether memory/ exists AFTERWARDS,
//   - its MODE (Path.mkdir passes 0o777 and lets the umask narrow it, so
//     asserting a literal 0o775 here would pin the umask of whoever ran
//     the test — the probe reports what CPython actually produced),
//   - whether the call RAISED, and with what.
const memDirPySrc = `
import json, os, stat, sys

out = []
for i, mode in enumerate(json.loads(sys.argv[2])):
    ws = _pyprobe_use(os.path.join(sys.argv[1], "ws%d" % i))
    os.makedirs(ws, exist_ok=True)
    if mode == "shadowed":
        open(os.path.join(ws, "memory"), "w").close()
    elif mode == "readonly":
        os.chmod(ws, 0o555)
    elif mode == "already":
        os.makedirs(os.path.join(ws, "memory"), exist_ok=True)
    row = {"mode": mode}
    try:
        from config import memory_dir
        p = memory_dir()
        row["raised"] = ""
        row["exists"] = os.path.isdir(str(p))
        row["perm"] = oct(stat.S_IMODE(os.stat(str(p)).st_mode))
    except Exception as e:
        row["raised"] = type(e).__name__
        row["exists"] = os.path.isdir(os.path.join(ws, "memory"))
        row["perm"] = ""
    if mode == "readonly":
        os.chmod(ws, 0o755)
    out.append(row)
print(json.dumps(out))
`

type memDirRow struct {
	Mode   string `json:"mode"`
	Raised string `json:"raised"`
	Exists bool   `json:"exists"`
	Perm   string `json:"perm"`
}

func TestEnsureMemoryDirCreatesWhatPythonCreates(t *testing.T) {
	modes := []string{"fresh", "already", "shadowed", "readonly"}
	root := t.TempDir()
	spaces := make([]string, len(modes))
	for i := range modes {
		spaces[i] = filepath.Join(root, "ws"+strconv.Itoa(i))
	}

	var want []memDirRow
	pyprobe.Probe{Marker: "config.py", Workspaces: spaces}.
		RunJSON(t, memDirPySrc, &want, root, pyprobe.Arg(t, modes))
	if len(want) != len(modes) {
		t.Fatalf("probe answered %d rows, want %d", len(want), len(modes))
	}

	// Anti-vacuity: this table is only worth anything if CPython actually
	// took both lanes. If the chmod silently did nothing (a root test
	// runner) or the shadowing file failed to land, every row would report
	// a happy creation and the port's failure lane would go untested.
	created, raised := 0, 0
	for _, w := range want {
		if w.Raised == "" && w.Exists {
			created++
		}
		if w.Raised != "" {
			raised++
		}
	}
	if created < 2 || raised < 1 {
		t.Fatalf("CPython neither created nor failed the way this test needs "+
			"(%d created, %d raised) — the fixture modes are not taking "+
			"effect: %+v", created, raised, want)
	}

	for i, mode := range modes {
		t.Run(mode, func(t *testing.T) {
			ws := filepath.Join(t.TempDir(), "ws")
			if err := os.MkdirAll(ws, 0o775); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "shadowed":
				if err := os.WriteFile(filepath.Join(ws, "memory"), nil, 0o644); err != nil {
					t.Fatal(err)
				}
			case "readonly":
				if err := os.Chmod(ws, 0o555); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = os.Chmod(ws, 0o755) })
			case "already":
				if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o775); err != nil {
					t.Fatal(err)
				}
			}

			dir, err := EnsureMemoryDir(ws)
			w := want[i]
			if (err != nil) != (w.Raised != "") {
				t.Fatalf("EnsureMemoryDir err=%v; CPython raised %q", err, w.Raised)
			}
			fi, statErr := os.Stat(filepath.Join(ws, "memory"))
			gotExists := statErr == nil && fi.IsDir()
			if gotExists != w.Exists {
				t.Errorf("memory/ isdir=%v after the call; CPython %v",
					gotExists, w.Exists)
			}
			if err != nil {
				return
			}
			if dir != filepath.Join(ws, "memory") {
				t.Errorf("resolved %q, want %q", dir, filepath.Join(ws, "memory"))
			}
			// Compared against CPython's own answer, not a literal: both
			// runtimes pass 0o777 and let the SAME umask narrow it, so a
			// hard-coded 0o775 would pin the umask of whoever ran the test
			// rather than the behaviour.
			if got := "0o" + strconv.FormatUint(uint64(fi.Mode().Perm()), 8); got != w.Perm {
				t.Errorf("memory/ mode %s; CPython produced %s", got, w.Perm)
			}
		})
	}
}

// TestTheMemoryPathHelpersAllCarryTheSideEffect is the FILE-derived half:
// a fix applied to EnsureMemoryDir alone would leave every caller still
// joining, and nothing above would notice. These are the package's own
// exported paths that stand for a Python `memory_dir() / name` line.
func TestTheMemoryPathHelpersAllCarryTheSideEffect(t *testing.T) {
	for _, tc := range []struct {
		name string
		fn   func(string) (string, error)
		base string
	}{
		{"MissionLogPath", MissionLogPath, "mission-log.jsonl"},
		{"DrainLockPath", DrainLockPath, drainLockFile},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := t.TempDir()
			got, err := tc.fn(ws)
			if err != nil {
				t.Fatal(err)
			}
			if want := filepath.Join(ws, "memory", tc.base); got != want {
				t.Fatalf("resolved %q, want %q", got, want)
			}
			if fi, serr := os.Stat(filepath.Join(ws, "memory")); serr != nil || !fi.IsDir() {
				t.Errorf("%s did not create memory/ (stat: %v) — in Python "+
					"resolving this path calls memory_dir(), which mkdirs",
					tc.name, serr)
			}
		})

		t.Run(tc.name+" reports a failure it cannot create", func(t *testing.T) {
			ws := t.TempDir()
			// A FILE where memory/ belongs. Chosen over a chmod because it
			// needs no permissions to set up and so still discriminates
			// under a root test runner, where chmod 0555 does not.
			if err := os.WriteFile(filepath.Join(ws, "memory"), nil, 0o644); err != nil {
				t.Fatal(err)
			}
			if _, err := tc.fn(ws); err == nil {
				t.Errorf("%s returned no error on a workspace where memory/ "+
					"cannot be created", tc.name)
			}
		})
	}
}

// TestTheDrainLockPredicateForksOnAnUncreatableMemoryDir pins the RESIDUAL
// named on IsDrainRunning, so the divergence is a recorded fact rather
// than an omission. CPython raises out of is_drain_running; a Go bool has
// no third state and answers false, which lets a second drain start.
//
// If this ever starts failing because IsDrainRunning grew an error
// channel, that is the fix landing — delete the test, not the assertion.
func TestTheDrainLockPredicateForksOnAnUncreatableMemoryDir(t *testing.T) {
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, "memory"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if IsDrainRunning(ws) {
		t.Fatal("expected the documented false")
	}
	// And the writer on the same workspace DOES report it, which is what
	// makes the predicate's silence a fork rather than a house style.
	if _, err := DrainLockPath(ws); err == nil {
		t.Error("DrainLockPath swallowed the same failure IsDrainRunning did")
	}
}
