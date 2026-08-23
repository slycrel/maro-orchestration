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
// Its output is line-for-line what scratchpad/ts_measure.py prints from
// Python, so the harness can diff them after normalising the two volatile
// fields (run_id and the clock).
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
	summary, err := pyval.DumpsCompactPy(sortedObj(counts))
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

// sortedObj renders a count map the way Python's json.dumps(sort_keys=True)
// would — the probe's summary line is the only place a Go map's undefined
// order would otherwise leak into a byte comparison.
func sortedObj(m map[string]int) pyval.Obj {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	out := pyval.Obj{}
	for _, k := range keys {
		out = append(out, pyval.Field{Key: k, Val: m[k]})
	}
	return out
}
