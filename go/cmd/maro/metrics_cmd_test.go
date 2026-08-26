package main

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// captureStdout runs fn with os.Stdout redirected and returns what it wrote.
func captureStdout(t *testing.T, fn func() error) (string, error) {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	saved := os.Stdout
	os.Stdout = w
	runErr := fn()
	os.Stdout = saved
	w.Close()
	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	r.Close()
	return string(out), runErr
}

// The whole `maro metrics` path, from a real outcomes.jsonl on disk.
//
// The differential covers compute and render over synthetic rows; this
// covers the two joints the differential cannot see — that GetMetrics reads
// the workspace the CLI resolves, and that LoadOutcomes' NEWEST-FIRST order
// is the order the report renders. Both were live seams in the Python
// original and neither is exercised by calling ComputeMetrics directly.
func TestMetricsCommandRendersFromTheStore(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", t.TempDir())
	if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	// Written OLDEST first, the way the ledger appends. load_outcomes
	// reverses, so "second" is the row the top-5 lists see first and the
	// one that wins a cost tie.
	lines := `{"outcome_id":"a","goal":"first goal","task_type":"build","status":"done","summary":"","lessons":[],"tokens_in":1000,"tokens_out":0,"elapsed_ms":10,"model":"opus"}
{"outcome_id":"b","goal":"second goal","task_type":"build","status":"stuck","summary":"","lessons":[],"tokens_in":1000,"tokens_out":0,"elapsed_ms":20,"model":"opus"}
`
	if err := os.WriteFile(filepath.Join(ws, "memory", "outcomes.jsonl"),
		[]byte(lines), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := captureStdout(t, func() error { return runMetrics(nil) })
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"=== Maro System Metrics ===",
		"Total goals: 2",
		"Overall success rate: 50.0%",
		"--- By Task Type ---",
		"build: 2 runs, 50% success, avg 15ms",
		"--- By Model ---",
		"opus: 2 runs,",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("report is missing %q:\n%s", want, out)
		}
	}
	// Newest-first: the reversed order puts "second goal" ahead of "first
	// goal" in the cost tie, which is the only place the load order is
	// observable in the output.
	iSecond := strings.Index(out, "1. $0.003000 — second goal")
	iFirst := strings.Index(out, "2. $0.003000 — first goal")
	if iSecond < 0 || iFirst < 0 || iSecond > iFirst {
		t.Errorf("load order is not newest-first in the report:\n%s", out)
	}
}

// A store the port has never written to is an empty store, not an error —
// and the report still renders its header. `maro metrics` on a fresh box is
// the first thing an operator runs.
func TestMetricsCommandOnAnEmptyWorkspace(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", t.TempDir())
	t.Setenv("MARO_USER_DIR", t.TempDir())
	out, err := captureStdout(t, func() error { return runMetrics(nil) })
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "Total goals: 0") ||
		!strings.Contains(out, "Overall success rate: 0.0%") {
		t.Fatalf("empty-store report:\n%s", out)
	}
	// The optional sections must be ABSENT, not empty-headed.
	for _, absent := range []string{"--- By Task Type ---", "--- By Model ---"} {
		if strings.Contains(out, absent) {
			t.Errorf("empty store rendered %q:\n%s", absent, out)
		}
	}
}

// -format json is REFUSED by name. A flag that is silently ignored is worse
// than one that errors: a script asking for JSON would get a text report and
// parse it as garbage.
func TestMetricsCommandRefusesTheUnportedJSONLane(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", t.TempDir())
	t.Setenv("MARO_USER_DIR", t.TempDir())
	out, err := captureStdout(t, func() error {
		return runMetrics([]string{"-format", "json"})
	})
	if err == nil {
		t.Fatalf("-format json was accepted, output:\n%s", out)
	}
	if !strings.Contains(err.Error(), "not ported yet") {
		t.Fatalf("refusal does not say why: %v", err)
	}
	if out != "" {
		t.Fatalf("the refused lane still printed:\n%s", out)
	}
}
