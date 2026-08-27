package tasks

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// list_tasks is the ONE sweep in task_store that sorts, and what it sorts
// is a glob of Path objects:
//
//	for p in sorted(_tasks_dir().glob("*.json")):
//
// `sorted()` compares the surrogateescape-DECODED string, so a task file
// whose name is not valid UTF-8 sorts by the code points 0xDC00+b. The
// port took `filepath.Glob`'s order and then re-sorted it with
// `sort.Strings`; BOTH of those are raw-byte orders, and the comment above
// the sort called it redundant. It was neither redundant nor right.
//
// The existing sweep differential cannot see this: it monkeypatches
// `_tasks_dir` to return a wrapper whose `glob` is already sorted,
// deliberately, so that the MERGING behaviour is pinned and the readdir
// order of the two UNSORTED sweeps is not. That patch also neutralises the
// one sort that is real. Hence a separate probe, with no patch.
const pyListOrderSrc = `
import json, os, sys
ws = sys.argv[1]
assert ws.startswith("/tmp/"), "refusing to touch a non-tmp store: " + ws
assert "/.maro/" not in ws, "refusing to touch the live store: " + ws
os.environ["MARO_WORKSPACE"] = ws
import task_store
assert str(task_store._tasks_dir()).startswith(ws), (
    "task_store resolved outside the fixture: " + str(task_store._tasks_dir()))
rows = task_store.list_tasks()
print(json.dumps({
    "job_ids": [t.get("job_id") for t in rows],
    "names": [list(os.fsencode(p.name))
              for p in sorted(task_store._tasks_dir().glob("*.json"))],
}))
`

// TestListTasksOrdersNamesTheWayPythonOrdersThem seeds four task files
// whose names span the divergence and asks both runtimes for the order.
//
// The four names, and why these four: byte order and code-point order
// AGREE for all valid UTF-8 and for a bad byte against ASCII, which is how
// this class survived so many rounds. They part only when a bad byte meets
// a MULTI-byte valid sequence.
//
//	"z"        z      = U+007A = 122       first byte 0x7a = 122
//	"\xc3\xa9" e-acute= U+00E9 = 233       first byte 0xc3 = 195
//	"\x80"     bad    = U+DC80 = 56448     first byte 0x80 = 128
//	"\xff"     bad    = U+DCFF = 56575     first byte 0xff = 255
//
// CPython: z, e-acute, \x80, \xff.   Raw bytes: z, \x80, e-acute, \xff.
// The middle pair swaps, so a fixture that only used \x80 and z would pass
// against the byte sort and prove nothing (P12's shape one level up).
func TestListTasksOrdersNamesTheWayPythonOrdersThem(t *testing.T) {
	ws := t.TempDir()
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	// name marker -> the job_id its content carries, so the ORDER is
	// readable in both runtimes' answers without either of them having to
	// hand back a non-UTF-8 string.
	seed := []struct {
		name  string
		jobID string
	}{
		{"z", "Z"},
		{"\xc3\xa9", "E"},
		{"\x80", "H"},
		{"\xff", "F"},
	}
	for _, s := range seed {
		p := filepath.Join(dir, s.name+".json")
		body := `{"job_id": "` + s.jobID + `", "status": "queued"}`
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	var py struct {
		JobIDs []string `json:"job_ids"`
		Names  [][]int  `json:"names"`
	}
	pyprobe.Probe{Marker: "task_store.py", Workspace: ws}.
		RunJSON(t, pyListOrderSrc, &py, ws)

	// The oracle is CPython's answer, but an oracle that agrees with the
	// subject by construction proves nothing, so state the expected order
	// independently first and check CPython against it. If CPython ever
	// disagrees with this, the FIXTURE is wrong and the test must say so
	// rather than silently re-baselining on whatever the interpreter did.
	want := []string{"Z", "E", "H", "F"}
	if len(py.JobIDs) != len(want) {
		t.Fatalf("CPython returned %d rows, want %d: %v", len(py.JobIDs), len(want), py.JobIDs)
	}
	for i := range want {
		if py.JobIDs[i] != want[i] {
			t.Fatalf("CPython ordered %v; the surrogateescape code points say "+
				"%v. Either the fixture's premise is wrong or this "+
				"interpreter does not decode filenames the documented way.",
				py.JobIDs, want)
		}
	}
	// And the raw-byte order must be DIFFERENT, or the fixture cannot fail.
	if bytes := []string{"Z", "H", "E", "F"}; sameOrder(want, bytes) {
		t.Fatal("the expected order and the raw-byte order coincide; this " +
			"fixture cannot detect the bug it was written for")
	}

	rows, err := List(ws, "")
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	var got []string
	for _, r := range rows {
		m, ok := r.(pyval.Obj)
		if !ok {
			t.Fatalf("row %T is not a mapping", r)
		}
		v, _ := m.Get("job_id")
		s, _ := v.(string)
		got = append(got, s)
	}
	if !sameOrder(got, py.JobIDs) {
		t.Errorf("List ordered %v, CPython ordered %v.\n"+
			"sorted() compares surrogateescape-decoded code points; "+
			"filepath.Glob and sort.Strings compare raw bytes. The order "+
			"must come from pypath.FSLess.", got, py.JobIDs)
	}
}

func sameOrder(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
