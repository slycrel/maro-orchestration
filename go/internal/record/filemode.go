package record

import (
	"os"
	"sync"
	"syscall"
)

// Python creates ledger files through a plain open(), so a NEW file lands
// at 0666 & ~umask and a new directory at 0777 & ~umask. This port had
// 0644 and 0755 hardcoded, which is the same thing on a umask-022 host and
// NARROWER on a umask-002 one: a ledger Go created first was not
// group-writable, so a Python writer sharing the group got EACCES on it
// (adversarial r4, L2). Everything on this box runs as one user, so the
// bug was invisible here and would have surfaced on someone else's.
//
// Python's atomic_write reads the umask back with os.umask(0); os.umask()
// and calls that "momentary, and file writes here are multiprocess, not
// multithreaded, so the window is acceptable". Go's runtime IS threaded,
// and for the length of that window every other goroutine in the process
// creating a file would see umask 0 — a world-writable secrets file is a
// real outcome of losing that race. So this port reads the umask ONCE and
// caches it. The divergence from Python is deliberate and narrow: a
// process that changes its own umask mid-run gets the value from startup
// rather than the current one. Nothing in either runtime does that.
var (
	umaskOnce  sync.Once
	cachedMask int
)

func processUmask() int {
	umaskOnce.Do(func() {
		cachedMask = syscall.Umask(0)
		syscall.Umask(cachedMask)
	})
	return cachedMask
}

// NewFileMode is the mode a plain open() would give a file this process
// creates — Python's `0o666 & ~_umask`.
func NewFileMode() os.FileMode { return os.FileMode(0o666 &^ processUmask()) }

// NewDirMode is the mode Path.mkdir() would give a directory this process
// creates. MkdirAll applies the umask itself, so passing 0o777 through is
// enough; the constant is named so call sites read as intent rather than
// as a suspiciously wide literal.
const NewDirMode = os.FileMode(0o777)
