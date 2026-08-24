// Per-project admission gate — the minimal port of Python
// acquire_project_slot (interrupt.py): an OS-level advisory flock,
// claimed before any project write and held for the run's lifetime.
// Disambiguation (project.go) only protects against SEQUENTIAL slug
// collisions; two runs racing Stat→MkdirAll on the same slug both see
// "free" and would share a live project dir — with tool-bearing workers
// that is two Bash-capable agents overwriting each other's files
// (adversarial exec r2 2026-08-22, Skeptic HIGH: the flock was never
// ported nor named).
//
// Python parity kept: refuse-immediately on busy (the heartbeat is the
// retry mechanism there; here the caller records a stuck outcome), lock
// file at <workspace>/memory/loop-<slug>.lock beside Python's own
// loop.lock naming, holder metadata written into the lock file for the
// refusal message, and environment errors (unwritable lock dir) degrade
// to UNGATED with a warning — an fs problem must not refuse work the
// old code would have run.
//
// Named divergences: no wait_s polling (--wait / admission_wait_s), no
// in-process sibling slot sharing (no in-process fan-out here yet), no
// unlink/reacquire inode guard (nothing in Go unlinks lock files), and
// the lock is released when Run returns rather than at process exit —
// equivalent for this CLI's one-run-per-process shape.
package loop

import (
	"fmt"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// acquireProjectSlot claims the per-project run slot. Returns:
//   - release, "", nil    — slot held; caller defers release().
//   - nil, "", busyErr    — another run holds it; refuse the run.
//   - nil, warn, nil      — gate unavailable (fs error); proceed UNGATED.
func acquireProjectSlot(memoryDir, slug, loopID, goal string) (release func(), warn string, err error) {
	lockPath := filepath.Join(memoryDir, "loop-"+slug+".lock")
	if mkErr := os.MkdirAll(memoryDir, 0o755); mkErr != nil {
		return nil, fmt.Sprintf("admission gate unavailable for %s (%v) — proceeding UNGATED",
			slug, mkErr), nil
	}
	f, openErr := os.OpenFile(lockPath, os.O_RDWR|os.O_CREATE, 0o644)
	if openErr != nil {
		return nil, fmt.Sprintf("admission gate unavailable for %s (%v) — proceeding UNGATED",
			slug, openErr), nil
	}
	// stdlib syscall, not x/sys: no new dependency for one flock call
	// (house rule), and unix-only is parity — Python's gate is fcntl.
	if flockErr := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); flockErr != nil {
		holder := "unknown holder"
		if raw, readErr := os.ReadFile(lockPath); readErr == nil && len(raw) > 0 {
			holder = string(raw)
		}
		f.Close()
		return nil, "", fmt.Errorf("project %q is busy — another run holds its slot: %s",
			slug, holder)
	}
	// Holder metadata is best-effort diagnostics for the NEXT contender's
	// refusal message; a failed write must not fail the acquired slot.
	// Known parity quirk (Python interrupt.py does the same seek/
	// truncate/write after flock, and neither runtime clears the file at
	// release): a contender reading in the write window can see empty
	// ("unknown holder") or a PREVIOUS holder's stale metadata. Inherited
	// diagnostics-only imprecision, flagged not fixed (adversarial exec
	// r3 2026-08-22, Expert QA LOW — fixing would diverge from parity).
	// interrupt.py writes this with json.dumps, and the goal text lands
	// in it verbatim — a goal containing `>` or a non-ASCII character was
	// written in a spelling no CPython writer produces, in a file the
	// Python side reads to name the holder (adversarial mission-r8).
	meta, _ := pyval.DumpsCompactPy(pyval.Obj{
		{Key: "loop_id", Val: loopID},
		{Key: "pid", Val: os.Getpid()},
		{Key: "goal", Val: goal},
		{Key: "started", Val: time.Now().UTC().Format(time.RFC3339)},
	})
	_ = f.Truncate(0)
	_, _ = f.WriteAt([]byte(meta), 0)
	return func() {
		_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
		_ = f.Close()
	}, "", nil
}
