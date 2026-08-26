package introspect

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The memory/ side effect, pinned in THIS package.
//
// It is pinned here rather than trusted to internal/orch because the
// mutation battery proved the orch pin does not cover it: MD-5 reverted
// EventsPath to `filepath.Join(orch.MemoryDir(ws), "events.jsonl")` and
// SURVIVED, with internal/orch's own four-shape differential green the
// whole time. A guard is package-local; the property is port-wide. Every
// package that resolves a Python `memory_dir() / name` line needs its own.
//
// What CPython does is measured once, in
// internal/orch/memorydir_diff_test.go, against config.memory_dir()
// itself. This file does not re-measure it — it asserts that this
// package's helpers still route through the helper that carries it.
func TestTheIntrospectPathHelpersCarryTheMkdir(t *testing.T) {
	for _, tc := range []struct {
		name string
		fn   func(string) (string, error)
		base string
	}{
		{"EventsPath", EventsPath, "events.jsonl"},
		{"DiagnosesPath", DiagnosesPath, "diagnoses.jsonl"},
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
				t.Errorf("%s did not create memory/ (stat: %v) — the Python "+
					"line this ports is `o.memory_dir() / %q`, and memory_dir "+
					"mkdirs", tc.name, serr, tc.base)
			}
		})

		t.Run(tc.name+" reports a memory/ it cannot create", func(t *testing.T) {
			ws := t.TempDir()
			// A FILE where memory/ belongs, chosen over a chmod because it
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

// The three readers do NOT agree about what that failure means, and the
// port follows each one rather than picking a house rule. This pins the
// disagreement so it stays a recorded decision:
//
//	_load_loop_events      no try   RAISES     -> port returns nil    (RESIDUAL)
//	_load_latest_loop_id   no try   RAISES     -> port returns "",false (RESIDUAL)
//	load_diagnoses         has try  partial    -> port returns nil    (faithful)
//
// Two of the three are divergences the port accepts because the Go
// signatures have nowhere to put an error; widening them is in BACKLOG.
// If this test starts failing because a reader grew an error channel,
// that is the fix landing — delete the case, do not weaken the assertion.
func TestTheReadersDisagreeAboutAnUncreatableMemoryDir(t *testing.T) {
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, "memory"), nil, 0o644); err != nil {
		t.Fatal(err)
	}

	if got := LoadLoopEvents(ws, ""); got != nil {
		t.Errorf("LoadLoopEvents returned %v, want the documented nil", got)
	}
	if id, ok := LatestLoopID(ws); ok || id != "" {
		t.Errorf("LatestLoopID returned (%q, %v), want the documented empty", id, ok)
	}
	if got := LoadDiagnoses(ws, 0); got != nil {
		t.Errorf("LoadDiagnoses returned %v, want nil", got)
	}
	// And the WRITER on the same workspace does report it — which is what
	// makes the readers' silence a fork rather than a house style.
	d := &LoopDiagnosis{LoopID: "loop-1", FailureClass: "none", Severity: "low"}
	if err := SaveDiagnosis(ws, d, time.Unix(0, 0).UTC()); err == nil {
		t.Error("SaveDiagnosis swallowed the failure the readers swallow; " +
			"then nothing in this package would notice an unusable workspace")
	}
}
