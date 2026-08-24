package testenv_test

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/notify"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/testenv"
)

func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }

// TestTheHookIsReachableAndIsolateIsWhatStopsIt proves both halves of the
// claim, because either alone is worthless.
//
// The first subtest fires a real hook command through the same zero-Options
// path `director.FireCheckin` and `scans.notifyVerdict` used, showing the
// ambient-config read genuinely executes a shell command chosen by a file
// outside the test. The second shows that under Isolate the same call finds
// no command and runs nothing.
//
// A test that only asserted "nothing fired" would pass just as happily if
// emit had been broken, or if the event type had stopped being default-on —
// **a guard that cannot fire is not evidence that the danger is gone.**
func TestTheHookIsReachableAndIsolateIsWhatStopsIt(t *testing.T) {
	ws := t.TempDir()
	payload := pyval.Obj{{Key: "handle_id", Val: "h1"}}

	t.Run("a registered command really does run", func(t *testing.T) {
		userDir := t.TempDir()
		marker := filepath.Join(t.TempDir(), "fired")
		// Single-quoted so the YAML reader takes it literally; `touch` is the
		// most inert observable side effect available.
		if err := os.WriteFile(filepath.Join(userDir, "config.yml"),
			[]byte("notify:\n  command: \"touch "+marker+"\"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
		t.Setenv("MARO_USER_DIR", userDir)
		notify.EmitOrdered(context.Background(), ws, "recursion_checkin",
			payload, notify.Options{})
		if _, err := os.Stat(marker); err != nil {
			t.Fatalf("the hook did not run (%v) — this subtest is supposed to "+
				"demonstrate that a config file outside the test can execute a "+
				"command, and if it cannot, the subtest below proves nothing", err)
		}
	})

	t.Run("Isolate leaves no command to run", func(t *testing.T) {
		home, err := os.UserHomeDir()
		if err == nil {
			if got := config.Home(); got == filepath.Join(home, ".maro") {
				t.Fatalf("config.Home() is %q — Isolate did not repoint "+
					"MARO_USER_DIR, so every test in this binary is reading the "+
					"operator's real config", got)
			}
		}
		cfg, _ := config.Load()
		if cmd := strings.TrimSpace(config.Get(cfg, "notify.command", "")); cmd != "" {
			t.Fatalf("notify.command resolves to %q under Isolate", cmd)
		}
	})
}

// TestEveryPackageThatCanReachTheNotifyHookIsolatesItsConfig asks the
// toolchain which test binaries can reach `notify`, and fails if one of them
// does not call testenv.Isolate.
//
// The bug this guards was found in `internal/director` and then found again,
// unprompted, in `internal/scans` — same defect, different package, and a
// fix applied only to the two known sites would be an enumeration that goes
// stale the next time someone emits an event from somewhere new. `go list`
// knows the import graph; the enumeration does not have to be maintained by
// anybody.
//
// Reaching notify is the criterion rather than "calls Emit", because the
// call can be three functions deep in non-test code — which is exactly how
// `internal/scans` reached it (a test calls `notifyVerdict`, which is
// unexported and calls `Emit` itself).
func TestEveryPackageThatCanReachTheNotifyHookIsolatesItsConfig(t *testing.T) {
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	root = filepath.Dir(root) // the module root, one above internal/

	type pkg struct {
		ImportPath   string
		Dir          string
		Deps         []string
		TestGoFiles  []string
		XTestGoFiles []string
		TestImports  []string
		XTestImports []string
	}

	cmd := exec.Command("go", "list", "-json", "./...")
	cmd.Dir = root
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("go list failed: %v\n%s", err, ee.Stderr)
		}
		t.Skipf("go list unavailable: %v", err)
	}

	const notifyPkg = "github.com/slycrel/maro-orchestration/go/internal/notify"
	dec := json.NewDecoder(strings.NewReader(string(out)))
	var checked, isolated int
	for {
		var p pkg
		if err := dec.Decode(&p); err != nil {
			break
		}
		if len(p.TestGoFiles) == 0 && len(p.XTestGoFiles) == 0 {
			continue
		}
		// The package under test is notify itself: its own tests inject an
		// Exec recorder by construction (that is what they are for), and its
		// package doc says no test in it shells out.
		if p.ImportPath == notifyPkg {
			continue
		}
		if !reaches(p.Deps, notifyPkg) &&
			!reaches(p.TestImports, notifyPkg) &&
			!reaches(p.XTestImports, notifyPkg) {
			continue
		}
		checked++
		if hasIsolatingTestMain(t, p.Dir) {
			isolated++
			continue
		}
		t.Errorf("%s has tests and can reach notify, but no TestMain calling "+
			"testenv.Isolate — its tests read the operator's ~/.maro/config.yml "+
			"and a registered notify.command would actually run", p.ImportPath)
	}

	// The tripwire's own guard. A `go list` that returned nothing, or a
	// criterion that stopped matching, would report zero failures forever.
	if checked < 2 {
		t.Fatalf("only %d package(s) matched the criterion; at least "+
			"internal/director and internal/scans reach notify, so this "+
			"tripwire is no longer looking at what it thinks it is", checked)
	}
	t.Logf("%d/%d notify-reaching test packages isolated", isolated, checked)
}

func reaches(deps []string, target string) bool {
	for _, d := range deps {
		if d == target {
			return true
		}
	}
	return false
}

// hasIsolatingTestMain looks for the call rather than for a TestMain: a
// TestMain that forgets the call is the failure mode this test exists to
// catch, and it is indistinguishable from no TestMain at all.
func hasIsolatingTestMain(t *testing.T, dir string) bool {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false
	}
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		body, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		if strings.Contains(string(body), "testenv.Isolate(m)") {
			return true
		}
	}
	return false
}
