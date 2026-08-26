package tasks

import (
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// TestTaskStoreDriveProbe is the Go half of the cross-runtime byte
// differential. It is a PROBE, not a test: skipped unless the harness
// names a workspace, so `go test ./...` never runs it.
//
// Its output is line-for-line what ts_measure.py — its sibling in THIS
// directory — prints from CPython, and ts_diff.sh owns the two temp
// workspaces, the normalisation and the diff. One command:
//
//	bash go/internal/tasks/ts_diff.sh     -> "identical across 101 lines"
//
// Both halves used to be described in REVIEW.md with only the Go half
// existing: the scratch driver it named had been deleted, so `go test`
// reported one honest skip and the interop claim rested on nothing anyone
// could re-run (adversarial tasks-r1 LOW). A claim that cannot be
// re-checked is not a claim, so the Python half lives in the repo now.
func TestTaskStoreDriveProbe(t *testing.T) {
	ws := os.Getenv("MARO_TASKS_DRIVE_WS")
	if ws == "" {
		t.Skip("set MARO_TASKS_DRIVE_WS to run the cross-runtime probe")
	}
	if !strings.HasPrefix(ws, "/tmp/") {
		t.Fatalf("refusing to drive a workspace outside /tmp: %s", ws)
	}

	dump := func(path string) {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		os.Stdout.Write(raw)
	}

	if _, err := Enqueue(ws, Options{
		JobID: "task-fixed-0001", Lane: "agenda", Source: "cli",
		Reason:      "caf\u00e9 \u2192 na\u00efve \u007f",
		ParentJobID: "task-parent", ContinuationDepth: 2,
		Origin: pyval.Obj{
			{Key: "parent_loop_id", Val: "L1"},
			{Key: "parent_goal", Val: "do a thing"},
			{Key: "z", Val: 1},
		},
	}); err != nil {
		t.Fatal(err)
	}
	p := TaskPath(ws, "task-fixed-0001")
	fmt.Println("=== BYTES ===")
	dump(p)
	st, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	fmt.Printf("=== MODE === 0o%o\n", st.Mode().Perm())
	fmt.Printf("=== LOCKNAME === %s\n", strings.TrimPrefix(lockPath(p), TasksDir(ws)+"/"))

	if _, err := Claim(ws, "task-fixed-0001", os.Getpid()); err != nil {
		t.Fatal(err)
	}
	if _, err := Complete(ws, "task-fixed-0001",
		pyval.Obj{{Key: "a", Val: "/x/y"}}, "incomplete"); err != nil {
		t.Fatal(err)
	}
	fmt.Println("=== AFTER COMPLETE ===")
	dump(p)

	if _, err := Enqueue(ws, Options{JobID: "task-fail-0002"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Fail(ws, "task-fail-0002", "boom"); err != nil {
		t.Fatal(err)
	}
	fmt.Println("=== AFTER FAIL ===")
	dump(TaskPath(ws, "task-fail-0002"))

	counts, err := StatusSummary(ws)
	if err != nil {
		t.Fatal(err)
	}
	summary, err := pyval.DumpsCompactPy(counts)
	if err != nil {
		t.Fatal(err)
	}
	fmt.Printf("=== SUMMARY === %s\n", summary)
	fmt.Printf("=== NEWID === %s\n", NewJobID())
	fmt.Printf("=== NOW === %s\n", UTCNow())

	fmt.Println("=== EMPTYORIGIN ===")
	if _, err := Enqueue(ws, Options{JobID: "task-min-0003"}); err != nil {
		t.Fatal(err)
	}
	dump(TaskPath(ws, "task-min-0003"))
}
