// Package testenv isolates a test binary from the operator's real machine.
//
// It exists because of one bug and the shape of it. `notify.Emit` falls back
// to `config.Load()` when handed no config, `config.Load()` reads
// `~/.maro/config.yml`, and on the box this port is written on that file
// registers a `notify.command` that messages Telegram and ssh's to another
// host. Two packages' tests — `internal/director` and `internal/scans` —
// emit default-on event types, so `go test ./...` had been paging the
// operator on every run, from the Go side AND from the CPython probes that
// inherit the environment (adversarial r11 round 2, HIGH).
//
// The Python repo already answers this: `MARO_USER_DIR` overrides the
// user-config directory precisely "so the box's real config doesn't leak in"
// (src/config.py). Python gets it applied globally by a conftest fixture. Go
// has no conftest, so each package that can reach the hook needs a TestMain
// — and "each package that can reach the hook" is an enumeration, which is
// the weak form. `tripwire_test.go` in this package turns it back into a
// class: it asks the toolchain which test packages transitively import
// notify and fails if any of them is missing its TestMain.
package testenv

import (
	"fmt"
	"os"
	"testing"
)

// Isolate points MARO_USER_DIR at a scratch directory for the whole test
// binary, runs the tests, and cleans up. Use it as the body of TestMain:
//
//	func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
//
// It deliberately does NOT touch MARO_WORKSPACE. Tests pass their own
// workspace explicitly and the live-workspace refusal in pyprobe guards the
// probes; overriding it here would paper over a caller that forgot to.
func Isolate(m *testing.M) int {
	dir, err := os.MkdirTemp("", "maro-testenv-")
	if err != nil {
		fmt.Fprintf(os.Stderr, "testenv: could not create a scratch config dir: %v\n", err)
		return 1
	}
	defer os.RemoveAll(dir)
	// Set, never restore-on-panic: a test binary is a whole process, and if
	// it dies mid-run the environment dies with it.
	if err := os.Setenv("MARO_USER_DIR", dir); err != nil {
		fmt.Fprintf(os.Stderr, "testenv: could not set MARO_USER_DIR: %v\n", err)
		return 1
	}
	return m.Run()
}
