// Package tasks ports task_store.py: a file-per-task JSON queue under
// output/queues/tasks/, with one advisory lock per task file and no
// global lock.
//
// A task is carried as a pyval.Obj — an ORDERED key/value list — rather
// than a Go struct with a raw-carry escape hatch, because two of its
// fields (`origin` and `artifact_paths`) are open dicts whose keys are
// whatever the producer put there, and because Python's writers rewrite
// the whole dict from what they read. Assigning to a Python dict updates
// a present key in place and appends a new one at the tail; pyval.Obj.Set
// does exactly that, which is why `result_status` and `error` land at the
// tail here the way they do in Python without anyone arranging it.
//
// Ported lessons (each pinned):
//
//   - The lock file REPLACES the extension. Python locks
//     `path.with_suffix(".lock")`, so `task-abc.json` is guarded by
//     `task-abc.lock` — not `task-abc.json.lock`, which is what
//     record.Locked would have produced. A port that appended would take
//     a different lock file and not mutually exclude with Python at all:
//     both runtimes "holding the lock" on the same task, both writing.
//
//   - The task writer is NOT file_lock.atomic_write. It leaves mkstemp's
//     0600 in place (no fchmod), writes with ensure_ascii=False, and adds
//     a trailing newline. All three are measured, all three differ from
//     the sidecar writer one directory over.
//
//   - Reads are NOT announced-and-skipped. `_read_task` uses json.loads,
//     which raises, so a torn file fails `list`/`status` outright instead
//     of quietly dropping the row. That is the opposite of this port's
//     posture everywhere else, and it is carried deliberately: a queue
//     that under-reports its own contents lets a claimed task vanish.
//
// Deliberately unported, NAMED: the argparse CLI (`main`), which is a
// thin shell over these functions and belongs with the Go CLI surface.
package tasks

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Task is one queue row, key order preserved.
type Task = pyval.Obj

// ValidStatuses is task_store.VALID_STATUSES, in its declared order.
var ValidStatuses = []string{"queued", "claimed", "done", "failed", "archived"}

// ErrNotFound is Python's FileNotFoundError for a missing task file.
var ErrNotFound = errors.New("task not found")

// ConflictError is Python's RuntimeError: the task exists but is not in a
// state that permits the operation. Kept distinct from ErrNotFound because
// callers retry the two differently — a conflict may resolve on its own.
type ConflictError struct{ msg string }

func (e *ConflictError) Error() string { return e.msg }

func conflictf(format string, a ...any) error {
	return &ConflictError{msg: fmt.Sprintf(format, a...)}
}

// CycleError is Python's ValueError from _check_cycle.
type CycleError struct{ msg string }

func (e *CycleError) Error() string { return e.msg }

// TasksDir and ArchiveDir resolve at CALL time, not at package init:
// Python re-reads workspace_root() on every call so a test that moves the
// workspace mid-process is honoured, and a Go package-level var would
// freeze the first one.
func TasksDir(ws string) string   { return filepath.Join(ws, "output", "queues", "tasks") }
func ArchiveDir(ws string) string { return filepath.Join(ws, "output", "queues", "archive") }

// TaskPath is the file for one job id.
func TaskPath(ws, jobID string) string {
	return filepath.Join(TasksDir(ws), jobID+".json")
}

// UTCNow is task_store.utc_now(): second precision with a literal Z.
func UTCNow() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

// NewJobID is task_store.new_job_id(): task-<compact UTC stamp>-<8 hex>.
func NewJobID() string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return "task-" + time.Now().UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(b[:])
}

// newRunID is str(uuid.uuid4()) — the hyphenated 36-character spelling,
// not a bare hex run. The version and variant nibbles are set because a
// reader that validates the shape (and Python's own uuid.UUID() does)
// would reject 32 random bytes wearing the same punctuation.
func newRunID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant 10
	h := hex.EncodeToString(b[:])
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32]
}

// Options are make_task's keyword arguments. The zero value is Python's
// default set except for Lane and Source, which default below — Go has no
// argument defaults, so the substitution is explicit and one place.
type Options struct {
	JobID  string
	Lane   string
	Source string
	// Reason and ParentJobID are `any` for the same reason
	// ContinuationDepth below is: `make_task` writes whatever the caller
	// passed, and the escalation's continue branch passes the task's OWN
	// `reason` and `job_id` — values it read back off disk and deliberately
	// keeps raw. A `string` here spells a dict reason as a Python repr and
	// an integer id as a quoted number, and `reason` is the continuation's
	// entire goal, so the next pass reads a different goal than CPython
	// would (adversarial r11 round 2, HIGH).
	//
	// Callers whose value is an f-string in Python — the narrow branch's
	// revised goal, for one — still pass a Go string, which is what an
	// f-string is.
	Reason      any
	ParentJobID any
	BlockedBy   []string
	Origin      pyval.Obj
	// ContinuationDepth is `any` rather than `int` because Python's
	// annotation is a hint, not a coercion: `make_task` writes whatever the
	// caller passed straight into the row. The director derives it from a
	// task field it read back off disk, so a foreign writer — jq, a
	// JavaScript tool, anything that types every JSON number as a double —
	// makes it a float, and `depth + 1` keeps it one down the whole chain.
	// nil is the parameter default of 0.
	ContinuationDepth any
}

func (o Options) continuationDepth() any {
	if o.ContinuationDepth == nil {
		return 0
	}
	return o.ContinuationDepth
}

// reason and parentJobID substitute Python's "" parameter defaults. nil is
// "not passed", which is a different thing from a caller passing nil — but
// no Python caller passes None to either, so the two collapse safely.
func (o Options) reason() any {
	if o.Reason == nil {
		return ""
	}
	return o.Reason
}

func (o Options) parentJobID() any {
	if o.ParentJobID == nil {
		return ""
	}
	return o.ParentJobID
}

func (o Options) lane() string {
	if o.Lane == "" {
		return "now"
	}
	return o.Lane
}

func (o Options) source() string {
	if o.Source == "" {
		return "task_store"
	}
	return o.Source
}

// MakeTask builds a fresh queued task. The key order below IS the on-disk
// order and is not alphabetical: Python builds this dict literal and
// json.dump preserves insertion order.
func MakeTask(jobID string, o Options) Task {
	now := UTCNow()
	blocked := pyval.List{}
	for _, b := range o.BlockedBy {
		blocked = append(blocked, b)
	}
	origin := pyval.Obj{}
	origin = append(origin, o.Origin...)
	return Task{
		{Key: "job_id", Val: jobID},
		{Key: "run_id", Val: newRunID()},
		{Key: "lane", Val: o.lane()},
		{Key: "source", Val: o.source()},
		{Key: "reason", Val: pyval.FromPlain(o.reason())},
		{Key: "status", Val: "queued"},
		{Key: "attempt", Val: 0},
		{Key: "parent_job_id", Val: pyval.FromPlain(o.parentJobID())},
		{Key: "blocked_by", Val: blocked},
		{Key: "continuation_depth", Val: pyval.FromPlain(o.continuationDepth())},
		// Ancestry back to the work that spawned this task. Without it a
		// requeued plan step arrives at handle() as a brand-new goal with
		// no thread identity — the fan-out failure traced in the
		// 2026-06-10 goal-brain pressure test.
		{Key: "origin", Val: origin},
		{Key: "timestamps", Val: pyval.Obj{
			{Key: "queued_at_utc", Val: now},
			{Key: "claimed_at_utc", Val: ""},
			{Key: "finished_at_utc", Val: ""},
		}},
		{Key: "artifact_paths", Val: pyval.Obj{}},
		{Key: "claimed_by_pid", Val: nil},
	}
}

// Enqueue creates a task and writes it.
func Enqueue(ws string, o Options) (Task, error) {
	jid := o.JobID
	if jid == "" {
		jid = NewJobID()
	}
	task := MakeTask(jid, o)

	// Python checks the cycle BEFORE taking the lock and before the file
	// exists, so a rejected task leaves nothing behind.
	if blocked := blockedList(task); len(blocked) > 0 {
		if err := checkCycle(ws, jid, blocked, nil); err != nil {
			return nil, err
		}
	}

	path := TaskPath(ws, jid)
	err := locked(path, false, func() error { return writeTask(path, task) })
	if err != nil {
		return nil, err
	}
	return task, nil
}

// checkCycle walks blocked_by chains transitively from jobID.
//
// `visited` tracks the nodes on the CURRENT DFS path, not every node ever
// seen — the discard on the way back out is the whole point, and dropping
// it would report a diamond (two paths to one dependency) as a cycle.
func checkCycle(ws, jobID string, blockedBy []string, visited map[string]bool) error {
	if visited == nil {
		visited = map[string]bool{jobID: true} // seed with the new task's id
	}
	for _, depID := range blockedBy {
		if visited[depID] {
			return &CycleError{msg: fmt.Sprintf(
				"cycle detected: %s appears in dependency chain of %s", depID, jobID)}
		}
		visited[depID] = true
		dep, err := readTask(TaskPath(ws, depID))
		if err != nil {
			return err
		}
		if dep != nil {
			if inner := blockedList(dep); len(inner) > 0 {
				if err := checkCycle(ws, jobID, inner, visited); err != nil {
					return err
				}
			}
		}
		delete(visited, depID) // backtrack for other branches
	}
	return nil
}

// Claim takes a queued task for pid. A pid of 0 means this process, which
// is also what Python's `pid or os.getpid()` does with an explicit 0.
func Claim(ws, jobID string, pid int) (Task, error) {
	if pid == 0 {
		pid = os.Getpid()
	}
	path := TaskPath(ws, jobID)
	var task Task
	err := locked(path, false, func() error {
		t, err := readTask(path)
		if err != nil {
			return err
		}
		if t == nil {
			return fmt.Errorf("%w: %s", ErrNotFound, jobID)
		}

		// Stale-claim recovery: a dead claimer releases the task rather
		// than parking it forever.
		claimStatus, err := indexStr(t, "status")
		if err != nil {
			return err
		}
		if claimStatus == "claimed" {
			claimedPID := pyval.IntOf(mustGet(t, "claimed_by_pid"))
			if claimedPID != 0 && !pidAlive(claimedPID) {
				t.Set("status", "queued")
				t.Set("claimed_by_pid", nil)
			} else {
				return conflictf("task %s already claimed by pid %v", jobID, claimedPID)
			}
		}
		// Re-indexed rather than reused: the stale-claim branch above can
		// have REWRITTEN it, and Python reads `task["status"]` again here.
		s, err := indexStr(t, "status")
		if err != nil {
			return err
		}
		if s != "queued" {
			return conflictf("task %s has status '%s', expected 'queued'", jobID, s)
		}

		for _, depID := range blockedList(t) {
			dep, err := readTask(TaskPath(ws, depID))
			if err != nil {
				return err
			}
			depStatus := "missing"
			if dep != nil {
				// `dep is None or dep["status"] != "done"` — a subscript,
				// short-circuited past only when dep is None.
				if depStatus, err = indexStr(dep, "status"); err != nil {
					return err
				}
			}
			if depStatus != "done" {
				return conflictf("task %s blocked by %s (status=%s)", jobID, depID, depStatus)
			}
		}

		t.Set("status", "claimed")
		t.Set("claimed_by_pid", pid)
		// `task["attempt"] += 1` — a subscript and Python's own `+`, so a
		// float attempt STAYS a float (`2.0` becomes `3.0` in the file, not
		// `3`) and a missing one raises rather than starting over at 1.
		attempt, err := index(t, "attempt")
		if err != nil {
			return err
		}
		bumped, err := pyval.IAddOne(attempt)
		if err != nil {
			return err
		}
		t.Set("attempt", bumped)
		if err := setTimestamp(&t, "claimed_at_utc", UTCNow()); err != nil {
			return err
		}
		if err := writeTask(path, t); err != nil {
			return err
		}
		task = t
		return nil
	})
	if err != nil {
		return nil, err
	}
	return task, nil
}

// Complete marks a task done and unblocks its dependents.
//
// Queue "done" means DRAINED — the task was claimed and processed. What
// the processing concluded is a separate fact, which is why resultStatus
// exists: 2026-07-16, task …b414ccab showed queue "done" beside dispatch
// "clarification_needed" and derailed a diagnosis.
func Complete(ws, jobID string, artifactPaths pyval.Obj, resultStatus string) (Task, error) {
	path := TaskPath(ws, jobID)
	var task Task
	err := locked(path, false, func() error {
		t, err := readTask(path)
		if err != nil {
			return err
		}
		if t == nil {
			return fmt.Errorf("%w: %s", ErrNotFound, jobID)
		}
		// "queued" is accepted alongside "claimed" — a task completed
		// without ever being claimed is legal here.
		s, err := indexStr(t, "status")
		if err != nil {
			return err
		}
		if s != "claimed" && s != "queued" {
			return conflictf("task %s has status '%s', cannot complete", jobID, s)
		}
		t.Set("status", "done")
		if err := setTimestamp(&t, "finished_at_utc", UTCNow()); err != nil {
			return err
		}
		t.Set("claimed_by_pid", nil)
		if len(artifactPaths) > 0 {
			// `task["artifact_paths"].update(...)` — a subscript AND an
			// attribute. A null or a scalar there is an AttributeError that
			// leaves the task claimed and its dependents blocked, where a
			// dropped type assertion wrote it done and resolved them.
			rawPaths, err := index(t, "artifact_paths")
			if err != nil {
				return err
			}
			paths, isObj := rawPaths.(pyval.Obj)
			if !isObj {
				return &pyval.PyErr{Class: "AttributeError",
					Msg: fmt.Sprintf("'%s' object has no attribute 'update'",
						pyval.TypeName(rawPaths))}
			}
			for _, f := range artifactPaths {
				paths.Set(f.Key, f.Val) // dict.update: in place, or appended
			}
			t.Set("artifact_paths", paths)
		}
		if resultStatus != "" {
			t.Set("result_status", resultStatus)
		}
		if err := writeTask(path, t); err != nil {
			return err
		}
		task = t
		return nil
	})
	if err != nil {
		return nil, err
	}
	if err := resolveDependents(ws, jobID); err != nil {
		return nil, err
	}
	return task, nil
}

// Fail marks a task failed. Note it accepts ANY prior status, including
// done and archived — Python has no guard here and adding one would make
// a Go failure path reject what a Python one accepts.
func Fail(ws, jobID, errText string) (Task, error) {
	path := TaskPath(ws, jobID)
	var task Task
	err := locked(path, false, func() error {
		t, err := readTask(path)
		if err != nil {
			return err
		}
		if t == nil {
			return fmt.Errorf("%w: %s", ErrNotFound, jobID)
		}
		t.Set("status", "failed")
		if err := setTimestamp(&t, "finished_at_utc", UTCNow()); err != nil {
			return err
		}
		t.Set("claimed_by_pid", nil)
		if errText != "" {
			t.Set("error", errText)
		}
		if err := writeTask(path, t); err != nil {
			return err
		}
		task = t
		return nil
	})
	if err != nil {
		return nil, err
	}
	return task, nil
}

// resolveDependents drops one occurrence of the completed id from every
// other task's blocked_by.
//
// ONE occurrence, matching Python's list.remove: a duplicate entry
// survives a completion and keeps the dependent blocked. That is a real
// behaviour with a real consequence, and it is carried rather than
// silently improved — a Go runtime that removed all copies would unblock
// a task Python leaves blocked, and the two would disagree about what is
// claimable.
func resolveDependents(ws, completedJobID string) error {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return err
	}
	for _, p := range paths {
		err := locked(p, false, func() error {
			t, err := readTask(p)
			if err != nil || t == nil {
				return err
			}
			blocked, _ := mustGet(t, "blocked_by").(pyval.List)
			for i, v := range blocked {
				if s, ok := v.(string); ok && s == completedJobID {
					blocked = append(blocked[:i:i], blocked[i+1:]...)
					t.Set("blocked_by", blocked)
					return writeTask(p, t)
				}
			}
			return nil
		})
		if err != nil {
			return err
		}
	}
	return nil
}

// List returns every task, optionally filtered by status.
//
// Python sorts the glob HERE and nowhere else, so list_tasks is the one
// sweep with a defined order. Go's filepath.Glob sorts on its own
// (verified, not assumed: it reads through a sorted readdirnames), which
// makes the explicit sort below redundant — it stays because the ORDER is
// a requirement of this function and not an accident of its helper, and
// no mutation can kill it, which is recorded rather than hidden.
//
// The same fact runs the other way in the three unsorted sweeps:
// resolveDependents, StatusSummary and RecoverStaleClaims iterate in
// sorted order here and in arbitrary directory order in Python. Nothing
// depends on it — none of the three is order-sensitive — except that
// RecoverStaleClaims RETURNS its ids, so a caller diffing the two
// runtimes' lists would see the same set in a different order.
func List(ws, statusFilter string) ([]Task, error) {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	sort.Strings(paths)
	out := []Task{}
	for _, p := range paths {
		t, err := readTask(p)
		if err != nil {
			return nil, err
		}
		if t == nil {
			continue
		}
		if statusFilter != "" && t.GetString("status") != statusFilter {
			continue
		}
		out = append(out, t)
	}
	return out, nil
}

// StatusSummary counts tasks by status. A row with no status counts as
// "unknown" rather than being dropped.
func StatusSummary(ws string) (map[string]int, error) {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	counts := map[string]int{}
	for _, p := range paths {
		t, err := readTask(p)
		if err != nil {
			return nil, err
		}
		if t == nil {
			continue
		}
		s, ok := t.Get("status")
		str, isStr := s.(string)
		if !ok || !isStr {
			str = "unknown"
		}
		counts[str]++
	}
	return counts, nil
}

// Archive moves a done/failed task into the archive directory.
func Archive(ws, jobID string) (Task, error) {
	path := TaskPath(ws, jobID)
	var task Task
	err := locked(path, false, func() error {
		t, err := readTask(path)
		if err != nil {
			return err
		}
		if t == nil {
			return fmt.Errorf("%w: %s", ErrNotFound, jobID)
		}
		if s := t.GetString("status"); s != "done" && s != "failed" {
			return conflictf("task %s has status '%s', can only archive done/failed", jobID, s)
		}
		t.Set("status", "archived")
		if err := os.MkdirAll(ArchiveDir(ws), 0o777); err != nil {
			return err
		}
		if err := writeTask(filepath.Join(ArchiveDir(ws), jobID+".json"), t); err != nil {
			return err
		}
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
			return err
		}
		// The lock file is unlinked while still HELD. That is deliberate
		// in Python and correct: the flock lives on the open file
		// description, so unlinking the name neither releases this lock
		// nor lets a concurrent waiter through early — the waiter is
		// blocked on the inode, and it wakes to find the task gone.
		if err := os.Remove(lockPath(path)); err != nil && !os.IsNotExist(err) {
			return err
		}
		task = t
		return nil
	})
	if err != nil {
		return nil, err
	}
	return task, nil
}

// RecoverStaleClaims resets to queued every claimed task whose claimer is
// gone, and reports the ids it reset.
func RecoverStaleClaims(ws string) ([]string, error) {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	recovered := []string{}
	for _, p := range paths {
		err := locked(p, false, func() error {
			t, err := readTask(p)
			if err != nil || t == nil {
				return err
			}
			if t.GetString("status") != "claimed" {
				return nil
			}
			pid := pyval.IntOf(mustGet(t, "claimed_by_pid"))
			if pid == 0 || pidAlive(pid) {
				return nil
			}
			t.Set("status", "queued")
			t.Set("claimed_by_pid", nil)
			if err := writeTask(p, t); err != nil {
				return err
			}
			recovered = append(recovered, t.GetString("job_id"))
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	return recovered, nil
}

// pidAlive is Python's os.kill(pid, 0) existence check.
//
// It is NOT a /proc lookup: that is Linux-only, and the 2026-07-08 fix
// records what happened when it was — on macOS /proc/{pid} is always
// missing, so every claim looked stale immediately and claiming an
// already-claimed job never raised. EPERM means the process exists and
// belongs to someone else, which is still alive.
func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	if err == nil {
		return true
	}
	return !errors.Is(err, syscall.ESRCH)
}

// blockedList reads blocked_by as strings, skipping non-strings the way
// Python's `for dep_id in task.get("blocked_by", [])` would carry them —
// except that a non-string there could never match a job id anyway.
func blockedList(t Task) []string {
	raw, _ := mustGet(t, "blocked_by").(pyval.List)
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func mustGet(t Task, key string) any {
	v, _ := t.Get(key)
	return v
}

// index is Python's `task[key]` — the SUBSCRIPT, not `.get`.
//
// task_store indexes six fields it never defaults, and a queue file is a
// document a foreign writer can produce: `maro-enqueue`, a dispatch from
// another box, a hand-edited row, an older schema. CPython raises KeyError
// there, INSIDE the file lock and BEFORE the atomic write — so the task is
// left exactly as it was and the caller sees the failure. Reading them
// through GetString/IntOf instead answered ""/0, wrote the task anyway, and
// the two runtimes disagreed on both the file's bytes and whether the task
// was still claimable (adversarial r11 round 3, MEDIUM).
//
// The exception CLASS comes from pyval because Python's does: KeyError's
// str() is the repr of the key, not a sentence.
func index(t Task, key string) (any, error) {
	v, ok := t.Get(key)
	if !ok {
		return nil, &pyval.PyErr{Class: "KeyError", Msg: pyval.Repr(key)}
	}
	return v, nil
}

// indexStr is `task[key]` where the caller then compares it to a string.
// The comparison itself is Python's `==`, which is False rather than an
// error for a non-string, so only the missing-key case raises.
func indexStr(t Task, key string) (string, error) {
	v, err := index(t, key)
	if err != nil {
		return "", err
	}
	return pyval.Str(v), nil
}

// setTimestamp is `task["timestamps"][key] = val` — TWO subscripts, and
// the outer one raises for a task file that has no timestamps object.
// Synthesizing it looked like the more useful shape for a queue whose job
// is not losing rows; what it actually did was write a task CPython leaves
// untouched, so the two runtimes disagreed on whether the task was still
// queued (adversarial r11 round 3, MEDIUM).
func setTimestamp(t *Task, key, val string) error {
	raw, err := index(*t, "timestamps")
	if err != nil {
		return err
	}
	ts, isObj := raw.(pyval.Obj)
	if !isObj {
		// A LIST is the one non-mapping that supports item assignment, so
		// it fails on the INDEX instead and says so in its own words. Every
		// other shape gets the "does not support" sentence.
		switch raw.(type) {
		case pyval.List, []any, []string:
			return &pyval.PyErr{Class: "TypeError",
				Msg: "list indices must be integers or slices, not str"}
		}
		return &pyval.PyErr{Class: "TypeError",
			Msg: fmt.Sprintf("'%s' object does not support item assignment",
				pyval.TypeName(raw))}
	}
	ts.Set(key, val)
	t.Set("timestamps", ts)
	return nil
}

// lockPath is Python's `path.with_suffix(".lock")` — the extension is
// REPLACED, not appended, so task-abc.json is guarded by task-abc.lock and
// not by task-abc.json.lock.
//
// pathlib's suffix is NOT filepath.Ext. The rule, measured against CPython
// 3.14.3 on this box rather than read from the docs: the suffix is the last
// dot AT OR AFTER the first non-dot character. So "..json" has no suffix at
// all and gains one (`..json.lock`), ".hidden.json" has ".json" and loses
// it (`.hidden.lock`), and "x." has a suffix of "." (`x.lock`).
//
// This rule has CHANGED across Python versions — older pathlib required the
// dot to be at index > 0 and to have something after it, which makes
// "..json" carry a ".json" suffix and "x." carry none. The names it differs
// on are not names a job id produces, so the divergence is bounded; it is
// spelled out anyway because the alternative is a lock file whose name
// depends on which interpreter ran last.
func lockPath(path string) string {
	dir, name := filepath.Split(path)
	lead := 0
	for lead < len(name) && name[lead] == '.' {
		lead++
	}
	stem := name
	if i := strings.LastIndexByte(name[lead:], '.'); i >= 0 {
		stem = name[:lead+i]
	}
	return filepath.Join(dir, stem+".lock")
}
