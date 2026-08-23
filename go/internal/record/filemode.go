package record

import (
	"os"
	"strconv"
	"strings"
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
// real outcome of losing that race.
//
// Caching the read-back under a sync.Once NARROWS that window to one
// occurrence and does not close it (adversarial r5, LOW: measured ~2.5%
// leakage under contention). The kernel publishes the value directly, so
// the first attempt is a READ with no window at all:
//
//	/proc/self/status, "Umask:\t0002" — Linux >= 4.7.
//
// The swap-and-restore stays as the fallback for a kernel or a container
// without /proc mounted, still under the Once, still the narrowed version.
// A umask is at most four octal digits, so a malformed line is rejected
// rather than parsed into something plausible.
//
// One deliberate divergence from Python either way: a process that changes
// its own umask mid-run gets the value from startup. Nothing in either
// runtime does that.
var (
	umaskOnce  sync.Once
	cachedMask int
)

func processUmask() int {
	umaskOnce.Do(func() {
		if m, ok := umaskFromProc(); ok {
			cachedMask = m
			return
		}
		cachedMask = syscall.Umask(0)
		syscall.Umask(cachedMask)
	})
	return cachedMask
}

// umaskFromProc reads the umask without changing it. Returns false on any
// doubt at all — a wrong mode here is silent until someone else's runtime
// gets EACCES, so the racy-but-correct fallback beats a confident guess.
func umaskFromProc() (int, bool) {
	raw, err := os.ReadFile("/proc/self/status")
	if err != nil {
		return 0, false
	}
	return parseUmaskStatus(string(raw))
}

// parseUmaskStatus is the parser, split from the read so its refusals can
// be driven without a writable /proc. It is NOT duplicated in the test —
// three copies of a "simple" string helper is exactly what made M2's
// repr() bug survive in this port, and this one decides file modes.
func parseUmaskStatus(raw string) (int, bool) {
	for _, line := range strings.Split(raw, "\n") {
		rest, found := strings.CutPrefix(line, "Umask:")
		if !found {
			continue
		}
		rest = strings.TrimSpace(rest)
		if rest == "" || len(rest) > 4 {
			return 0, false
		}
		m, err := strconv.ParseInt(rest, 8, 32)
		if err != nil || m < 0 || m > 0o777 {
			return 0, false
		}
		return int(m), true
	}
	return 0, false
}

// NewFileMode is the mode a plain open() would give a file this process
// creates — Python's `0o666 & ~_umask`.
func NewFileMode() os.FileMode { return os.FileMode(0o666 &^ processUmask()) }

// NewDirMode is the mode Path.mkdir() would give a directory this process
// creates. MkdirAll applies the umask itself, so passing 0o777 through is
// enough; the constant is named so call sites read as intent rather than
// as a suspiciously wide literal.
const NewDirMode = os.FileMode(0o777)
