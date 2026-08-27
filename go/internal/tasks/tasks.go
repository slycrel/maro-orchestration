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
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Task is one queue row, key order preserved.
type Task = pyval.Obj

// ValidStatuses is task_store.VALID_STATUSES, in its declared order.
var ValidStatuses = []string{"queued", "claimed", "done", "failed", "archived"}

// ErrNotFound is Python's FileNotFoundError for a missing task file.
//
// A typed sentinel rather than errors.New, so it can answer PyClass the way
// ConflictError and CycleError do. It was the third member of that family
// and the one that could not answer: pyval.ClassOf returned "" for it, and
// a differential comparing classes saw "*fmt.wrapError" against CPython's
// FileNotFoundError while the message matched exactly (adversarial r11
// round 6, LOW). `errors.Is(err, ErrNotFound)` still holds — every raise
// site wraps it with %w.
type notFoundError struct{ msg string }

func (e *notFoundError) Error() string   { return e.msg }
func (e *notFoundError) PyClass() string { return "FileNotFoundError" }

var ErrNotFound error = &notFoundError{msg: "task not found"}

// ConflictError is Python's RuntimeError: the task exists but is not in a
// state that permits the operation. Kept distinct from ErrNotFound because
// callers retry the two differently — a conflict may resolve on its own.
type ConflictError struct{ msg string }

func (e *ConflictError) Error() string { return e.msg }

// PyClass is the exception CPython raises here, so a differential can
// compare the class and not just the sentence.
func (e *ConflictError) PyClass() string { return "RuntimeError" }

func conflictf(format string, a ...any) error {
	return &ConflictError{msg: fmt.Sprintf(format, a...)}
}

// CycleError is Python's ValueError from _check_cycle.
type CycleError struct{ msg string }

func (e *CycleError) Error() string { return e.msg }

func (e *CycleError) PyClass() string { return "ValueError" }

// TasksDir and ArchiveDir resolve at CALL time, not at package init:
// Python re-reads workspace_root() on every call so a test that moves the
// workspace mid-process is honoured, and a Go package-level var would
// freeze the first one.
func TasksDir(ws string) string   { return filepath.Join(ws, "output", "queues", "tasks") }
func ArchiveDir(ws string) string { return filepath.Join(ws, "output", "queues", "archive") }

// TaskPath is the file for one job id.
func TaskPath(ws, jobID string) string {
	// pypath.Join, not filepath.Join. `task_path` is
	// `_tasks_dir() / f"{job_id}.json"`, and pathlib's `/` lets an
	// ABSOLUTE right-hand side replace the left one — so CPython's
	// task_path("/etc/passwd") is "/etc/passwd.json", outside the
	// workspace entirely, where filepath.Join answers
	// "<ws>/output/queues/tasks/etc/passwd.json".
	//
	// That is not a hypothetical: `blocked_by` is the foreign-writable
	// field round 5 built pyIter for, and claim's dependency loop feeds
	// each entry straight to task_path. A queue row naming an absolute
	// dependency is CLAIMABLE under CPython — the dep file exists and
	// reads done — and permanently blocked here, which is the two
	// runtimes disagreeing about what the queue may run (adversarial r11
	// round 6, HIGH).
	//
	// Everything downstream inherits it: the lock path, the atomic
	// write's temp dir, and archive's destination all derive from this
	// one string.
	return pypath.Join(TasksDir(ws), jobID+".json")
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
	// JobID and BlockedBy stay STRING-typed, and that is a measured
	// decision rather than an oversight. Round 8 widened `Complete`'s job
	// id to `any` and the lens says a fix is evidence about its siblings —
	// so these two were traced the same way, and they are not the same
	// case. Complete's argument arrives from a task FILE, where any JSON
	// type is reachable and a foreign writer routinely produces one. These
	// are PARAMETERS, and every producer in the Python tree hands them a
	// str: `job_id` is `Optional[str]` and no in-repo caller passes it at
	// all, while `blocked_by` comes from a comma-split CLI argument
	// (cli.py, task_store.py) or from `[job_ids[-1]]`, whose element is
	// `new_job_id()`'s string (handle_queue.enqueue_goals). Widening them
	// would add an unreachable arm — the shape this suite keeps deleting.
	//
	// The one behavioural subtlety a widening WOULD have to carry: Python
	// is `jid = job_id or new_job_id()`, truthiness rather than a None
	// check, so a hypothetical `job_id=0` mints a fresh id. For a str that
	// is exactly `== ""`, which is what Enqueue tests.
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
	// HasReason and HasParentJobID say "the caller passed this, and it was
	// None". Without them a nil field is indistinguishable from an omitted
	// one, and Python's default for both is "" — so a genuine None was
	// written as "". Only a caller that means None sets these.
	HasReason      bool
	HasParentJobID bool
	BlockedBy      []string
	Origin         pyval.Obj
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

// reason and parentJobID substitute Python's "" parameter defaults.
//
// nil is "not passed" — and that is NOT the same as a caller passing None,
// which is what the first cut of this comment claimed ("no Python caller
// passes None to either, so the two collapse safely"). `handle_escalation`
// reads `task.get("reason", "")` and `task.get("job_id", "unknown")`, and
// `.get` on a key that is PRESENT and null returns None; both go straight
// into `make_task`, which writes whatever it was handed. So a
// loop_escalation row carrying `"reason": null` enqueues a continuation
// whose reason is `null` in CPython and was `""` here — a different goal
// for the next pass, and different bytes in the queue file (adversarial
// r11 round 7, MEDIUM).
//
// HasReason/HasParentJobID are the same shape as DrainOptions.HasMaxTasks:
// the zero value cannot carry "passed, and it was None". A caller with a
// non-nil value needs neither flag; only a caller that means None does.
// This is the same lesson as notify's "state every raw field at every call
// site" — a zero value that must mean two things means neither.
func (o Options) reason() any {
	if o.Reason == nil && !o.HasReason {
		return ""
	}
	return o.Reason
}

func (o Options) parentJobID() any {
	if o.ParentJobID == nil && !o.HasParentJobID {
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
	blockedRaw, _ := task.Get("blocked_by")
	if pyval.Truthy(blockedRaw) {
		if err := checkCycle(ws, jid, blockedRaw, nil); err != nil {
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
func checkCycle(ws, jobID string, blockedBy any, visited map[string]bool) error {
	if visited == nil {
		visited = map[string]bool{"s:" + jobID: true} // seed with the new task's id
	}
	deps, err := pyIter(blockedBy)
	if err != nil {
		return err
	}
	for _, depRaw := range deps {
		// `dep_id in visited` and `visited.add(dep_id)` are SET
		// operations, so an unhashable dep_id raises before either.
		key, hashable := pyval.HashKey(depRaw)
		if !hashable {
			// The full CPython wording, matching the three siblings that
			// already carry it (handlequeue's set element, pyops' dict key,
			// statusSummary's dict key). This site had the bare
			// "unhashable type: 'list'" — the pre-3.12 sentence — so one of
			// four members of a class disagreed with the interpreter
			// (adversarial r11 round 8, MEDIUM). Measured on 3.14.3:
			//   cannot use 'list' as a set element (unhashable type: 'list')
			return &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
				"cannot use '%s' as a set element (unhashable type: '%s')",
				pyval.TypeName(depRaw), pyval.TypeName(depRaw))}
		}
		depID := pyval.Str(depRaw) // task_path is an f-string
		if visited[key] {
			return &CycleError{msg: fmt.Sprintf(
				"cycle detected: %s appears in dependency chain of %s", depID, jobID)}
		}
		visited[key] = true
		depRawRow, err := readRaw(TaskPath(ws, depID))
		if err != nil {
			return err
		}
		// `if dep and dep.get("blocked_by")` — TRUTHINESS first, so a
		// falsy junk row (0, "", []) is stepped over without the .get
		// ever running, and only a TRUTHY one raises AttributeError.
		if pyval.Truthy(depRawRow) {
			dep, merr := asMapping(depRawRow)
			if merr != nil {
				return merr
			}
			// `if dep and dep.get("blocked_by")` — TRUTHINESS, not a
			// length: a falsy blocked_by (null, 0, "", []) skips the
			// recursion entirely and never raises, where the same value
			// on the task being CLAIMED does raise. Two sites, two
			// behaviours, one field.
			inner, _ := dep.Get("blocked_by")
			if pyval.Truthy(inner) {
				if err := checkCycle(ws, jobID, inner, visited); err != nil {
					return err
				}
			}
		}
		delete(visited, key) // backtrack for other branches
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
			// RAW. Python's gate is `if claimed_pid and not
			// _pid_alive(claimed_pid)` — truthiness on the stored value,
			// and _pid_alive's own `pid <= 0` then RAISES for anything
			// that is not a number. Reading it through IntOf answered 0
			// for a string pid, which made the gate false, so the port
			// reported a conflict "by pid 0" where CPython raised
			// TypeError — and the sweep below released a task CPython
			// never touches (adversarial r11 round 4, MEDIUM).
			claimedPID := mustGet(t, "claimed_by_pid")
			// SHORT-CIRCUITED, because Python's `and` is. `if claimed_pid
			// and not _pid_alive(claimed_pid)` never calls _pid_alive for
			// a falsy pid — and three falsy values RAISE inside it: "",
			// [] and {} are all TypeError at `pid <= 0`. Evaluating both
			// sides first turned "already claimed by pid " into a
			// TypeError that aborts the claim.
			release := false
			if pyval.Truthy(claimedPID) {
				alive, aerr := pidAliveRaw(claimedPID)
				if aerr != nil {
					return aerr
				}
				release = !alive
			}
			if release {
				t.Set("status", "queued")
				t.Set("claimed_by_pid", nil)
			} else {
				return conflictf("task %s already claimed by pid %s",
					jobID, pyval.Str(claimedPID))
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

		deps, derr := blockedIter(t)
		if derr != nil {
			return derr
		}
		for _, depRaw := range deps {
			// `task_path(dep_id)` is an f-string and the message
			// interpolates the same value, so a non-string dep_id is not
			// dropped — it names a file that does not exist and the claim
			// is refused with status=missing.
			depID := pyval.Str(depRaw)
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
// jobIDRaw is `any` because complete() feeds its argument to TWO consumers
// with different rules: task_path f-strings it (so a str spelling is right),
// and _resolve_dependents compares it to every other task's blocked_by
// entries by VALUE (so a str spelling is wrong). Collapsing at the caller
// satisfied the first and inverted the second — a job_id of 4242 unblocked
// the dependents listing "4242" and left the ones listing 4242 blocked,
// exactly backwards from CPython (adversarial r11 round 8, HIGH).
func Complete(ws string, jobIDRaw any, artifactPaths pyval.Obj, resultStatus string) (Task, error) {
	jobID := pyval.Str(jobIDRaw)
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
	if err := resolveDependents(ws, jobIDRaw); err != nil {
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
		// readRaw, not readTask, because fail's FIRST act on the row is
		// `task["status"] = "failed"` — an item ASSIGNMENT. claim, complete
		// and archive all read `task["status"]` first, and the two
		// operations do not word their TypeError the same way. Measured:
		//
		//	          __setitem__                        __getitem__
		//	str    'str' object does not support…   string indices must be…
		//	int    'int' object does not support…   'int' object is not subscriptable
		//	list   list indices must be integers…   list indices must be integers…
		//
		// Only the list arm agrees; a str, int, float or bool row got the
		// subscript sentence here where CPython gives the assignment one
		// (adversarial r11 round 9, MEDIUM).
		v, err := readRaw(path)
		if err != nil {
			return err
		}
		if v == nil {
			return fmt.Errorf("%w: %s", ErrNotFound, jobID)
		}
		t, err := asAssignable(v)
		if err != nil {
			return err
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
func resolveDependents(ws string, completedJobID any) error {
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
			v, err := readRaw(p)
			if err != nil || v == nil {
				return err
			}
			t, merr := asMapping(v)
			if merr != nil {
				return merr
			}
			// `blocked = task.get("blocked_by", [])`, then
			// `if completed_job_id in blocked`, then `blocked.remove(...)`
			// — three different operations on the same raw value, and
			// each one refuses a different set of shapes. `in` is a
			// SUBSTRING test on a str and a KEY test on a dict; `.remove`
			// exists on neither, so a hit there is an AttributeError
			// rather than an edit. A type assertion to List answered
			// "not present" for all of them and silently skipped the row
			// (adversarial r11 round 5, HIGH).
			var blockedRaw any = pyval.List{}
			if v, ok := t.Get("blocked_by"); ok {
				blockedRaw = v
			}
			hit, cerr := pyContains(blockedRaw, completedJobID)
			if cerr != nil {
				return cerr
			}
			if !hit {
				return nil
			}
			blocked, isList := blockedRaw.(pyval.List)
			if !isList {
				return &pyval.PyErr{Class: "AttributeError",
					Msg: fmt.Sprintf("'%s' object has no attribute 'remove'",
						pyval.TypeName(blockedRaw))}
			}
			// list.remove drops the FIRST equal element only, and "equal"
			// is Python's == — the same relation pyContains just used, so
			// the two cannot disagree about which element was found.
			for i, v := range blocked {
				if pyval.Eq(v, completedJobID) {
					blocked = append(blocked[:i:i], blocked[i+1:]...)
					break
				}
			}
			t.Set("blocked_by", blocked)
			return writeTask(p, t)
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
// sweep with a defined order. That order must come from pypath.FSLess:
// `sorted()` over Path objects compares the surrogateescape-DECODED
// string, while both filepath.Glob's own ordering and sort.Strings compare
// RAW BYTES, and the two part on any task filename that is not valid
// UTF-8. This shipped as sort.Strings on top of Glob, with a comment
// calling the sort redundant; it was neither redundant nor right
// (adversarial r7, MEDIUM — found by censusing the class rather than
// fixing the one site the review named).
//
// The same fact runs the other way in the three unsorted sweeps:
// resolveDependents, StatusSummary and RecoverStaleClaims iterate in
// sorted order here and in arbitrary readdir order in Python. This used to
// say "nothing depends on it", and that was wrong for two of the three
// (adversarial tasks-r1 LOW):
//
//   - RecoverStaleClaims RETURNS its ids, so a caller diffing the two
//     runtimes' lists sees the same set in a different order.
//   - StatusSummary returns an ORDERED Obj precisely because order is
//     observable — the CLI json.dumps'es it — so its printed key order is
//     sorted-filename order here and readdir order there. Worse, when two
//     rows' statuses compare equal but SPELL differently (1 and true, 5 and
//     5.0), the bucket takes the label of whichever ARRIVED FIRST. Go's
//     answer is deterministic; CPython's depends on readdir.
//
// Neither can be closed, only named: CPython's order is unspecified. The
// sweep differential monkeypatches _tasks_dir to sort, which pins the
// MERGING behaviour and deliberately does not pin the order.
func List(ws, statusFilter string) ([]any, error) {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	sort.SliceStable(paths, func(i, j int) bool {
		return pypath.FSLess(paths[i], paths[j])
	})
	out := []any{}
	for _, p := range paths {
		v, err := readRaw(p)
		if err != nil {
			return nil, err
		}
		if v == nil {
			continue
		}
		// SHORT-CIRCUITED: `if status_filter and task.get(...) != filter`.
		// With no filter the `.get` is never called, so an unfiltered
		// list_tasks RETURNS a junk row rather than raising on it — and
		// the filtered one raises AttributeError on the same row. The
		// return type is []any for that reason: a row that is not a
		// mapping is still a row CPython hands back.
		if statusFilter != "" {
			t, merr := asMapping(v)
			if merr != nil {
				return nil, merr
			}
			if t.GetString("status") != statusFilter {
				continue
			}
		}
		out = append(out, v)
	}
	return out, nil
}

// StatusSummary counts tasks by status. A row with no status counts as
// "unknown" rather than being dropped.
//
// It returns an ORDERED Obj and not a map, because two distinct dict keys
// can share one JSON spelling and a map silently drops one of them.
// `counts` is keyed by the RAW status, so a row holding 5 and a row holding
// "5" are two buckets — and `json.dumps` writes them both as "5", producing
// a JSON object with a duplicate key. Measured, seven rows in, CPython:
//
//	{"true": 1, "null": 1, "5": 1, "1.0": 2, "None": 1, "5": 1}
//
// A map[string]int answered four buckets to CPython's six and a total of 4
// against its 7 — the queue under-reporting its own contents, which is the
// failure the read path was written to prevent (adversarial r11 round 6).
func StatusSummary(ws string) (pyval.Obj, error) {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	buckets := map[string]*statusBucket{}
	order := []*statusBucket{}
	for _, p := range paths {
		v, err := readRaw(p)
		if err != nil {
			return nil, err
		}
		if v == nil {
			continue
		}
		t, merr := asMapping(v)
		if merr != nil {
			return nil, merr
		}
		// `s = task.get("status", "unknown")` — the default applies only
		// when the KEY is absent, and the raw value becomes a dict key.
		// The CLI then json.dumps that dict, which renders a non-string
		// key by its JSON spelling: 5 is "5", None is "null", True is
		// "true". Folding them all to "unknown" merged buckets CPython
		// keeps apart (adversarial r11 round 4, LOW).
		raw, ok := t.Get("status")
		if !ok {
			raw = "unknown"
		}
		key, hashable := pyval.HashKey(raw)
		if !hashable {
			return nil, &pyval.PyErr{Class: "TypeError",
				Msg: fmt.Sprintf("cannot use '%s' as a dict key "+
					"(unhashable type: '%s')",
					pyval.TypeName(raw), pyval.TypeName(raw))}
		}
		// Counted by IDENTITY, labelled by the FIRST spelling seen — which
		// is Python's dict rule: a later 1.0 increments the bucket True
		// opened without renaming it.
		if b, ok := buckets[key]; ok {
			b.n++
			continue
		}
		display, _ := statusKey(raw)
		b := &statusBucket{display: display, n: 1}
		buckets[key] = b
		order = append(order, b)
	}
	counts := make(pyval.Obj, 0, len(order))
	for _, b := range order {
		// append, not Set: Set would dedupe by spelling and re-introduce
		// exactly the collapse this return type exists to avoid.
		counts = append(counts, pyval.Field{Key: b.display, Val: b.n})
	}
	return counts, nil
}

type statusBucket struct {
	display string
	n       int
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
		// `task["status"]` is the SUBSCRIPT, and archive was the one site
		// of that class rounds 3 to 5 never enumerated: claim, complete,
		// fail and recover_stale_claims each got the treatment and this
		// one kept GetString, which answers "" for a missing key AND for
		// any non-string. A row with no status raised RuntimeError here
		// where CPython raises KeyError, and every non-string status
		// printed as '' where CPython prints the value — 'None', 'True',
		// '5', "['done']" (adversarial r11 round 6, MEDIUM).
		//
		// The membership is `not in ("done", "failed")`, which is `==`
		// against two strings and never raises whatever the value is; the
		// message then subscripts the SAME key a second time, which cannot
		// fail if the first did not.
		//
		// pyval.Eq rather than comparing pyval.Str(raw), and the battery
		// scores that swap as an UNOBSERVABLE mutation on purpose: the two
		// differ only for a value whose str() is "done" while not being
		// the string "done", and no JSON-decodable value has that shape —
		// a list spells "['done']", a dict spells "{...}", a number
		// spells digits. Eq is kept because it is what `==` IS, not
		// because a fixture can tell.
		raw, ierr := index(t, "status")
		if ierr != nil {
			return ierr
		}
		if !pyval.Eq(raw, "done") && !pyval.Eq(raw, "failed") {
			return conflictf("task %s has status '%s', can only archive done/failed",
				jobID, pyval.Str(raw))
		}
		t.Set("status", "archived")
		if err := os.MkdirAll(ArchiveDir(ws), 0o777); err != nil {
			return err
		}
		// `_archive_dir() / f"{job_id}.json"` — the same pathlib join, so
		// an absolute job id archives to an absolute path here too.
		if err := writeTask(pypath.Join(ArchiveDir(ws), jobID+".json"), t); err != nil {
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
//
// The ids are RAW. Python appends `task["job_id"]` and the CLI json-dumps
// the list, so a numeric job_id prints as a number; stringifying here made
// it print as "4242" (adversarial r11 round 8, MEDIUM) — the same
// narrow-type shape as Complete's needle, one field over.
func RecoverStaleClaims(ws string) ([]any, error) {
	dir := TasksDir(ws)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	recovered := []any{}
	for _, p := range paths {
		err := locked(p, false, func() error {
			v, err := readRaw(p)
			if err != nil || v == nil {
				return err
			}
			t, ierr := asIndexable(v)
			if ierr != nil {
				return ierr
			}
			// SUBSCRIPTS, both of them: `task["status"]` and
			// `task["job_id"]` are indexed here, not defaulted, so a
			// claimed row missing either raises out of the whole sweep.
			claimStatus, err := indexStr(t, "status")
			if err != nil {
				return err
			}
			if claimStatus != "claimed" {
				return nil
			}
			// Same raw read as Claim, and the same raise: a single queue
			// row with a string pid aborts the WHOLE sweep in CPython,
			// which is why the error propagates instead of skipping the
			// row.
			pidRaw := mustGet(t, "claimed_by_pid")
			if !pyval.Truthy(pidRaw) {
				return nil
			}
			alive, aerr := pidAliveRaw(pidRaw)
			if aerr != nil {
				return aerr
			}
			if alive {
				return nil
			}
			t.Set("status", "queued")
			t.Set("claimed_by_pid", nil)
			if err := writeTask(p, t); err != nil {
				return err
			}
			recoveredID, err := index(t, "job_id")
			if err != nil {
				return err
			}
			recovered = append(recovered, recoveredID)
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

// pidAliveRaw is _pid_alive on the value a task file actually holds.
//
//	if pid is None or pid <= 0: return False
//
// Two raises live in that line and neither is caught anywhere up the
// stack. `pid <= 0` for a str, a list or a dict is a TypeError naming both
// operand types; a FLOAT compares fine and then `os.kill` refuses it; and
// an int past a C int overflows there. True is 1 and is a real pid.
func pidAliveRaw(v any) (bool, error) {
	if v == nil {
		return false, nil
	}
	switch t := v.(type) {
	case bool:
		// A bool IS an int to `pid <= 0` and to os.kill alike, so it takes
		// the same path as one rather than a hand-written arm: False is 0
		// and dead, True is pid 1 and answers PermissionError, which
		// `except OSError: return True` calls alive.
		if t {
			return pidAliveInt(1)
		}
		return pidAliveInt(0)
	case int:
		return pidAliveInt(int64(t))
	case int64:
		return pidAliveInt(t)
	case float64:
		if t <= 0 {
			return false, nil
		}
		return false, &pyval.PyErr{Class: "TypeError",
			Msg: "'float' object cannot be interpreted as an integer"}
	case json.Number:
		lit, isInt := pyval.IntLiteral(t.String())
		if !isInt {
			f, err := t.Float64()
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return false, &pyval.PyErr{Class: "TypeError", Msg: cmpTypeErr(v)}
			}
			return pidAliveRaw(f)
		}
		if i, err := t.Int64(); err == nil {
			return pidAliveInt(i)
		}
		// A Python int too wide for int64 — and still an INT, so `pid <= 0`
		// answers on its sign without os.kill ever seeing it.
		if strings.HasPrefix(lit, "-") {
			return false, nil
		}
		return false, pidOverflow()
	}
	return false, &pyval.PyErr{Class: "TypeError", Msg: cmpTypeErr(v)}
}

// pidAliveInt is `_pid_alive` for a value that is a Python int.
//
// The ORDER is the contract: `pid <= 0` returns False before os.kill is
// reached, so a hugely NEGATIVE pid is simply dead — it never overflows.
// Only a pid past the positive C-int bound raises, and that raise is what
// stops the sweep from releasing a claim it could not verify. Measured on
// this box: os.kill(2**31-1, 0) is a ProcessLookupError, os.kill(2**31, 0)
// is an OverflowError.
func pidAliveInt(pid int64) (bool, error) {
	// ONE-SIDED on purpose. A symmetric `|| pid < math.MinInt32` reads
	// like the C-int range and is wrong: `pid <= 0` has already answered
	// False by the time os.kill could overflow, so a hugely negative pid
	// is dead, not an error. pidAlive carries that sign test, so it is
	// not repeated here — duplicating it would make the wrong bound
	// unobservable, which is how the symmetric version shipped.
	if pid > math.MaxInt32 {
		return false, pidOverflow()
	}
	return pidAlive(int(pid)), nil
}

func pidOverflow() error {
	return &pyval.PyErr{Class: "OverflowError",
		Msg: "Python int too large to convert to C int"}
}

// cmpTypeErr is CPython's message for `x <= 0` where x is not a number.
func cmpTypeErr(v any) string {
	return fmt.Sprintf("'<=' not supported between instances of '%s' and 'int'",
		pyval.TypeName(v))
}

// pyIter is Python's `for x in v` over a decoded JSON value.
//
// A queue file is a document a foreign writer can produce, and `blocked_by`
// is the field that decides whether a task may run at all — yet it was the
// one field rounds 3 and 4 never opened, because every fixture in this
// package spells `"blocked_by": []`. The port type-asserted it to a List,
// which is nil for anything else: a `null`, a number or a string
// `blocked_by` became NO DEPENDENCIES, and Go claimed and drained tasks
// CPython refuses to claim (adversarial r11 round 5, HIGH).
//
// Measured, and none of these is the same as "empty": a str iterates by
// CHARACTER, a dict iterates by KEY, and None/int/float/bool are all a
// TypeError naming the type.
func pyIter(v any) ([]any, error) {
	switch t := v.(type) {
	case string:
		out := make([]any, 0, len(t))
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	case pyval.List:
		return []any(t), nil
	case []any:
		return t, nil
	case []string:
		out := make([]any, 0, len(t))
		for _, e := range t {
			out = append(out, e)
		}
		return out, nil
	case pyval.Obj:
		out := make([]any, 0, len(t))
		for _, f := range t {
			out = append(out, f.Key)
		}
		return out, nil
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out := make([]any, 0, len(keys))
		for _, k := range keys {
			out = append(out, k)
		}
		return out, nil
	}
	return nil, &pyval.PyErr{Class: "TypeError",
		Msg: fmt.Sprintf("'%s' object is not iterable", pyval.TypeName(v))}
}

// pyContains is Python's `needle in v`, which is NOT iteration: it is a
// SUBSTRING test on a str, a KEY test on a dict, and a TypeError with its
// own wording on everything non-container.
//
// The NEEDLE is `any` for the same reason the container is. Round 5 widened
// the container here and left the needle a Go string, which made the two
// runtimes disagree about which tasks a completion unblocks — see
// resolveDependents. A str needle is not a universal spelling of a value;
// it is one of the operands, and Python compares operands by value.
func pyContains(v any, needle any) (bool, error) {
	switch t := v.(type) {
	case string:
		// `x in "some string"` demands a str on the left. CPython names the
		// type it got, and this is reachable: blocked_by is read off a task
		// file, so it can be a string while job_id is a number.
		s, ok := needle.(string)
		if !ok {
			return false, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
				"'in <string>' requires string as left operand, not %s",
				pyval.TypeName(needle))}
		}
		return strings.Contains(t, s), nil
	case pyval.List:
		return listHas([]any(t), needle), nil
	case pyval.Obj:
		keys := make([]string, 0, len(t))
		for _, kv := range t {
			keys = append(keys, kv.Key)
		}
		return objHasKey(keys, needle)
	}
	// Go-native containers do not appear here and are not silently
	// handled. The only caller reads blocked_by off a decoded task file,
	// and pyval's decoder produces Obj/List/string/number/bool/nil and
	// nothing else — so a []any or a map[string]any reaching this point
	// is a PORT bug, not a Python condition, and must not be answered
	// with a TypeError CPython would never raise for a list. Round 8
	// found the []string arm here unreachable: three arms that could not
	// fire were standing in for evidence that the shapes were handled.
	switch v.(type) {
	case []any, []string, map[string]any:
		return false, fmt.Errorf(
			"pyContains: Go-native %T reached a Python membership test; "+
				"decode through pyval before comparing", v)
	}
	return false, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
		"argument of type '%s' is not a container or iterable",
		pyval.TypeName(v))}
}

// listHas is Python's `==` between the needle and each element, which is
// pyval.Eq: a str never equals an int, but 5 DOES equal 5.0 and True DOES
// equal 1. A non-matching element is not a match — it is NOT skipped, which
// matters because the same element is still interpolated into a path by the
// caller.
func listHas(l []any, needle any) bool {
	for _, e := range l {
		if pyval.Eq(e, needle) {
			return true
		}
	}
	return false
}

// objHasKey is `needle in some_dict`, which hashes the needle rather than
// comparing it to each key. A decoded JSON object's keys are all strings, so
// a number never matches one — but an UNHASHABLE needle raises rather than
// answering False, and job_id is read off a file and can be a list.
func objHasKey(keys []string, needle any) (bool, error) {
	nk, ok := pyval.HashKey(needle)
	if !ok {
		return false, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
			"cannot use '%s' as a dict key (unhashable type: '%s')",
			pyval.TypeName(needle), pyval.TypeName(needle))}
	}
	for _, k := range keys {
		if kk, ok := pyval.HashKey(k); ok && kk == nk {
			return true, nil
		}
	}
	return false, nil
}

// blockedIter is `for dep_id in task.get("blocked_by", [])` — a `.get` with
// a list default, then Python's own iteration.
func blockedIter(t Task) ([]any, error) {
	v, ok := t.Get("blocked_by")
	if !ok {
		return nil, nil // the [] default; a PRESENT null is not this
	}
	return pyIter(v)
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

// A value's IDENTITY as a Python dict key or set member lives in
// pyval.HashKey — this package used to carry its own copy, which was the
// same Python operation implemented twice. `status_summary` buckets with
// it, `_check_cycle` decides visited-set membership with it, and
// `drain_task_store` filters its job-id set with it; two homes could
// disagree and no caller would be able to see it.
//
// statusKey below is the OTHER half, and the distinction is the point:
// hashing is by numeric value (`True`, `1` and `1.0` are one key), while
// spelling is what json.dumps writes — and two distinct keys can spell
// alike, which is how a status summary emits a duplicate JSON key.

// statusKey is the same value's SPELLING once json.dumps renders the
// summary — which is what an operator reads.
func statusKey(v any) (string, bool) {
	switch t := v.(type) {
	case string:
		return t, true
	case nil:
		return "null", true
	case bool:
		if t {
			return "true", true
		}
		return "false", true
	case pyval.Obj, pyval.List, map[string]any, []any, []string:
		return "", false
	}
	// Numbers render as their Python repr, which is also their JSON key
	// spelling: 5 is "5" and 2.0 is "2.0", and 1e19 is "1e+19".
	//
	// The three non-finites are the exception, and they are NOT str(): a
	// float KEY goes through json's own float writer, which spells them
	// "NaN", "Infinity" and "-Infinity" where `str()` spells them "nan",
	// "inf" and "-inf". Measured:
	//
	//	json.dumps({float("nan"): 1, float("inf"): 2, float("-inf"): 3})
	//	  -> {"NaN": 1, "Infinity": 2, "-Infinity": 3}
	//
	// Reachable, because `json.loads` accepts the bare tokens and so does
	// this port's decoder: a task file holding `"status": NaN` reads back
	// as a status on both sides, and the summary an operator prints is
	// where the two spellings meet (adversarial r11 round 7, LOW).
	if f, ok := pyval.Float(v); ok {
		switch {
		case math.IsNaN(f):
			return "NaN", true
		case math.IsInf(f, 1):
			return "Infinity", true
		case math.IsInf(f, -1):
			return "-Infinity", true
		}
	}
	return pyval.Str(v), true
}
