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
func readTask(path string) (Task, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	v, err := pyval.LoadsOrdered(string(raw))
	if err != nil {
		return nil, fmt.Errorf("read task %s: %w", path, err)
	}
	t, ok := v.(pyval.Obj)
	if !ok {
		return nil, fmt.Errorf("read task %s: expected a JSON object, got %T", path, v)
	}
	return t, nil
}
