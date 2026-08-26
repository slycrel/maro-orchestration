package metrics

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The memory/ side effect, pinned in THIS package.
//
// Pinned here rather than trusted to internal/orch because the mutation
// battery proved the orch pin does not cover it: MD-6 reverted
// StepCostsPath to `filepath.Join(orch.MemoryDir(ws), "step-costs.jsonl")`
// and SURVIVED, with internal/orch's four-shape differential green
// throughout. A guard is package-local; the property is port-wide.
//
// What CPython does is measured once, against config.memory_dir() itself,
// in internal/orch/memorydir_diff_test.go. This asserts only that this
// package's helper still routes through the helper that carries it.
func TestStepCostsPathCarriesTheMkdir(t *testing.T) {
	t.Run("it creates memory/", func(t *testing.T) {
		ws := t.TempDir()
		got, err := StepCostsPath(ws)
		if err != nil {
			t.Fatal(err)
		}
		if want := filepath.Join(ws, "memory", "step-costs.jsonl"); got != want {
			t.Fatalf("resolved %q, want %q", got, want)
		}
		if fi, serr := os.Stat(filepath.Join(ws, "memory")); serr != nil || !fi.IsDir() {
			t.Errorf("StepCostsPath did not create memory/ (stat: %v) — the "+
				"Python line this ports is `o.memory_dir() / \"step-costs.jsonl\"`, "+
				"and memory_dir mkdirs", serr)
		}
	})

	t.Run("it reports a memory/ it cannot create", func(t *testing.T) {
		ws := t.TempDir()
		// A FILE where memory/ belongs: needs no permissions to set up, so
		// it still discriminates under a root test runner where a chmod
		// 0555 does not.
		if err := os.WriteFile(filepath.Join(ws, "memory"), nil, 0o644); err != nil {
			t.Fatal(err)
		}
		if _, err := StepCostsPath(ws); err == nil {
			t.Error("StepCostsPath returned no error on a workspace where " +
				"memory/ cannot be created")
		}
	})

	// The three READERS above this layer are deliberately NOT on that list.
	// `spend_today`, `spend_for_loops` and `load_step_costs` each wrap
	// their whole body — path helper included — in `except Exception`, so
	// returning the empty answer is faithful rather than a residual. That
	// was checked against the Python, not assumed; an earlier comment in
	// stepcosts.go claimed the opposite.
	t.Run("the readers swallow it, faithfully", func(t *testing.T) {
		ws := t.TempDir()
		if err := os.WriteFile(filepath.Join(ws, "memory"), nil, 0o644); err != nil {
			t.Fatal(err)
		}
		if got := SpendToday(ws, time.Unix(0, 0).UTC()); got != 0 {
			t.Errorf("SpendToday = %v, want 0", got)
		}
		if got := SpendForLoops(ws, []string{"loop-1"}); got != 0 {
			t.Errorf("SpendForLoops = %v, want 0", got)
		}
		if got := LoadStepCosts(ws, 10); got != nil {
			t.Errorf("LoadStepCosts = %v, want nil", got)
		}
	})
}

// RecordStepCost's SECOND MkdirAll is not pinned, and this comment is why.
//
// Python calls `path.parent.mkdir(parents=True, exist_ok=True)` again at
// metrics.py:425 even though `_step_costs_path()` already created it, and
// the port is faithful to the redundancy. Battery mutant MD-7 changed that
// second call's mode from record.NewDirMode to a literal 0o755 and
// SURVIVED — correctly. By the time it runs, StepCostsPath has returned
// without error, so memory/ EXISTS, and MkdirAll does not chmod a
// directory that already exists. The mode at that site is unobservable.
//
// It is not strictly an equivalent mutant: it becomes reachable if memory/
// disappears between the two calls, which is the exact race the redundant
// Python call exists to survive. But that is a window this suite cannot
// open deterministically, and a test that cannot fail is worse than no
// test. Recorded instead of faked.
//
// The comment at the site used to read as though the mode change fixed
// something observable. It does not, and it now says so.
