package tasks

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// locked runs fn holding an advisory lock on the task file's `.lock`
// sibling, and blocks indefinitely to get it.
//
// It does NOT use record.Locked, for two reasons that both matter:
//
//   - record.Locked APPENDS ".lock". Python replaces the extension. Two
//     runtimes taking different lock files do not exclude each other, and
//     nothing about that failure is visible — both writes succeed, one
//     wins the rename, and the loser's update is simply gone.
//
//   - record.Locked has a bounded fail-closed deadline. task_store's
//     `fcntl.flock` has none, and the difference is load-bearing:
//     _resolve_dependents and recover_stale_claims lock every task file in
//     turn, so under a burst a Go caller with a deadline would start
//     returning timeouts where Python simply waits its turn.
//
// The file is opened in APPEND mode, as Python does, and the comment there
// says why: the fd binds to the inode at open time, so another process
// unlinking and recreating the lock between a touch and an open cannot
// hand two holders the same lock. Archive() unlinks a lock it is holding,
// which is exactly that race made routine.
func locked(path string, shared bool, fn func() error) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		return err
	}
	lp := lockPath(path)
	fp, err := os.OpenFile(lp, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
	if err != nil {
		return fmt.Errorf("open lock %s: %w", lp, err)
	}
	defer fp.Close()

	how := syscall.LOCK_EX
	if shared {
		how = syscall.LOCK_SH
	}
	// EINTR is not an error here. Go's scheduler preempts goroutines with
	// SIGURG from 1.14 on, which interrupts a blocking flock; treating that
	// as a failure would turn a normal preemption into a lost write under
	// exactly the load that makes preemption likely.
	for {
		err := syscall.Flock(int(fp.Fd()), how)
		if err == nil {
			break
		}
		if errors.Is(err, syscall.EINTR) {
			continue
		}
		return fmt.Errorf("lock %s: %w", lp, err)
	}
	defer syscall.Flock(int(fp.Fd()), syscall.LOCK_UN)
	return fn()
}

// writeTask is task_store._atomic_write: a temp file in the same
// directory, then a rename.
//
// Three measured details, all of which differ from file_lock.atomic_write
// one directory over, and none of which are style choices:
//
//   - ensure_ascii=False. A reason field carrying "café" is written as
//     raw UTF-8 here and as "café" in mission.json. Both files are
//     valid JSON and both round-trip, so nothing catches a swap except a
//     byte comparison — which is why there is one in the test file.
//
//   - A trailing newline after the closing brace. json.dump does not add
//     one; the caller does, on the next line.
//
//   - Mode 0600, NOT umask-derived. There is no fchmod here, so mkstemp's
//     0600 stands, and the rename carries it onto the destination even if
//     the destination was more permissive before. A queue file is not
//     group-readable in either runtime.
//
// The one deliberate addition is the fsync before rename. Python omits it,
// but omitting it changes no byte either runtime can observe — it only
// widens the window in which a crash leaves a renamed file with no
// contents. Strengthening durability is safe in a way that changing bytes
// would not be.
func writeTask(path string, task Task) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return err
	}
	text, err := pyval.DumpsIndent2Raw(task)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, "*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	cleanup := func() {
		f.Close()
		os.Remove(tmp)
	}
	if _, err := f.WriteString(text + "\n"); err != nil {
		cleanup()
		return err
	}
	if err := f.Sync(); err != nil {
		cleanup()
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// readTask returns nil for a missing file and an ERROR for an unreadable
// or malformed one.
//
// Python's _read_task calls json.loads, which raises — so a torn task file
// fails list_tasks and status_summary outright rather than being skipped.
// Everywhere else in this port a bad row is announced and stepped over;
// here it is not, deliberately. A queue that silently under-reports its
// own contents is how a claimed task disappears without anyone noticing,
// and the two runtimes must agree on which rows exist.
// readRaw is `_read_task` exactly: bare json.loads, handing back WHATEVER
// it decoded. `null` becomes nil and is indistinguishable from a missing
// file — every caller tests `if task is None` — but so do six other
// shapes, and none of them is nil. Round 4 fixed the `null` LITERAL and
// left its class: a file holding `[]`, `5` or `"x"` still aborted every
// sweep here, where CPython's unfiltered list_tasks RETURNS it and the
// filtered one raises AttributeError (adversarial r11 round 5, MEDIUM).
//
// So the shape check moved to the callers, because Python's is at the
// callers: `.get` and `task[...]` do not raise the same exception, and
// `if dep and dep.get(...)` does not raise at all for a falsy row.
func readRaw(path string) (any, error) {
	// `_read_task` is `if not path.exists(): return None`, and
	// `Path.exists()` is `try: self.stat() except (OSError, ValueError):
	// return False` — it swallows EVERY stat failure, not just ENOENT.
	// ENAMETOOLONG, ENOTDIR, ELOOP, EACCES on a parent and an embedded NUL
	// all mean "no such task" to CPython.
	//
	// `os.IsNotExist` is true for ENOENT alone, so each of those was a hard
	// error out of the verb here. That is not cosmetic: the only caller
	// reaching this with an ATTACKER-shaped path is the `blocked_by`
	// dependency read, which is a foreign-writable field, and the two
	// runtimes disagreed about which rows the queue accepts —
	// `enqueue(blocked_by=["d"*300])` writes a row in CPython and writes
	// nothing here (adversarial tasks-r1 HIGH, measured both sides).
	//
	// Stat first, then read. A file that stats and then fails to open is a
	// genuine error in both runtimes: CPython's read_text raises there too.
	if _, serr := os.Stat(path); serr != nil {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	// `read_text(encoding="utf-8")` is STRICT: a task file carrying a byte
	// that is not valid UTF-8 raises UnicodeDecodeError, and every caller
	// here — claim, complete, the sweep — fails without touching it.
	//
	// Go's decoder does not refuse. It substitutes U+FFFD for each bad byte
	// and returns a perfectly ordinary object, and the next write_task
	// re-encodes those replacement characters back to disk. That is not a
	// divergence in what the two runtimes REPORT; it is this runtime
	// DESTROYING the file's original bytes, after which the Python runtime
	// can read the task again and sees content nobody wrote. Measured:
	// {"note":"\xff\xfe"} decodes clean in Go and re-encodes as two U+FFFD.
	//
	// The same rule already guards internal/record, internal/orch and
	// internal/knowledge — but each of those refuses with a plain Go error.
	// This one raises the exception Python raises, class and sentence, both
	// because the class is what an `except UnicodeDecodeError` sees and
	// because this reader's callers already compare exception classes
	// against CPython row by row.
	text, derr := pyval.DecodeUTF8Strict(raw)
	if derr != nil {
		return nil, derr
	}
	v, err := pyval.LoadsOrdered(text)
	if err != nil {
		return nil, fmt.Errorf("read task %s: %w", path, err)
	}
	return v, nil
}

// readTask is readRaw for a caller whose very next act is a SUBSCRIPT.
func readTask(path string) (Task, error) {
	v, err := readRaw(path)
	if err != nil || v == nil {
		return nil, err
	}
	return asIndexable(v)
}

// asIndexable is `task["status"]` on a non-mapping — three different
// TypeError sentences depending on the type, measured on this box.
func asIndexable(v any) (Task, error) {
	if t, ok := v.(pyval.Obj); ok {
		return t, nil
	}
	var msg string
	if _, isList := listish(v); isList {
		return nil, &pyval.PyErr{Class: "TypeError",
			Msg: "list indices must be integers or slices, not str"}
	}
	switch v.(type) {
	case string:
		msg = "string indices must be integers, not 'str'"
	default:
		msg = fmt.Sprintf("'%s' object is not subscriptable", pyval.TypeName(v))
	}
	return nil, &pyval.PyErr{Class: "TypeError", Msg: msg}
}

// asAssignable is `task["status"] = ...` on a non-mapping — item
// ASSIGNMENT, whose TypeError differs from the subscript READ above for
// every type but list.
//
// A list says the same sentence both ways because the complaint is about the
// INDEX, not about the container; every other type complains about the
// container, and __setitem__ and __getitem__ complain differently. That
// single agreeing arm is why a table built only from list fixtures cannot
// tell asIndexable from this function.
func asAssignable(v any) (Task, error) {
	if t, ok := v.(pyval.Obj); ok {
		return t, nil
	}
	if _, isList := listish(v); isList {
		return nil, &pyval.PyErr{Class: "TypeError",
			Msg: "list indices must be integers or slices, not str"}
	}
	return nil, &pyval.PyErr{Class: "TypeError",
		Msg: fmt.Sprintf("'%s' object does not support item assignment",
			pyval.TypeName(v))}
}

// listish reports whether a decoded value is one of the shapes that stands
// in for a Python list here. Spelled once so asIndexable and asAssignable
// cannot drift apart about which types those are.
func listish(v any) (any, bool) {
	switch v.(type) {
	case pyval.List, []any, []string:
		return v, true
	}
	return nil, false
}

// asMapping is `task.get(...)` on a non-mapping — one AttributeError
// sentence, and a DIFFERENT class from the subscript above, so an
// `except TypeError` upstream catches one and not the other.
func asMapping(v any) (Task, error) {
	if t, ok := v.(pyval.Obj); ok {
		return t, nil
	}
	return nil, &pyval.PyErr{Class: "AttributeError",
		Msg: fmt.Sprintf("'%s' object has no attribute 'get'", pyval.TypeName(v))}
}
