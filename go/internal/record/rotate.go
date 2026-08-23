package record

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Captain's-log rotation, ported from captains_log._maybe_rotate
// (adversarial r5, L2 — absent in this runtime entirely until now).
//
// WHY THIS IS NOT A DATA-RETENTION VIOLATION. PORT.md used to say this
// runtime has "no delete/rotate/compact verbs at all" and offered that as
// the Go shape of the append-only invariant. That conflated two different
// things, and the conflation is what kept the port from noticing the gap:
// the invariant is NEVER AUTO-DELETE, and rotation deletes nothing. Every
// entry moves to a timestamped archive BESIDE the active file and stays
// readable; the active file keeps the most recent tail. Python's own
// docstring says it outright — "never deletes data".
//
// WHAT IT IS FOR is read cost, not disk. load_log JSON-parses the whole
// active file on every call and it sits on the dispatch recall hot path,
// so an unbounded active file makes every recall slower forever. A Go
// runtime that appends to the same file and never rotates hands that cost
// to the Python runtime sharing it — the store is the interop contract,
// and half of this contract is the file's SIZE.
//
// Size-gated and riding on the append, with no scheduler, matching Python
// (the no-scheduler invariant). Never returns an error: the entry that
// triggered it is already durable, so a failed rotation must not fail the
// event. Problems go to stderr, which is where Python's logger.warning
// lands in this system and the only warning surface this port has.
//
// Python also emits a logger.info line on a SUCCESSFUL rotation. That one
// is deliberately not duplicated: the LOG_ROTATED row below carries the
// same three numbers durably, and a CLI that prints to stderr every time a
// log rolls over trains its reader to ignore stderr.
//
// ONE DELIBERATE IMPROVEMENT over Python: both rewrites go through
// AtomicWrite (temp + fsync + rename) where Python uses write_text. A
// reader without the lock — and load_log takes none — can observe Python's
// active file mid-truncation and read a short log; it cannot observe this
// one. The archive gets the same treatment for the same reason.
const (
	defaultRotateMB   = 5.0
	defaultRotateKeep = 1000
	// maxArchiveCollisions bounds the same-second suffix search. Python's
	// loop is unbounded; a bound turns a pathological directory into a
	// loud warning instead of a spin.
	maxArchiveCollisions = 1000
)

// rotationInProgress is Python's _rotation_in_progress module global. The
// LOG_ROTATED audit append lands in the FRESH active file and re-enters
// this function; without the guard a rotate_mb smaller than the retained
// tail cascades. It is also what keeps Locked from deadlocking against
// itself here, since Locked is not reentrant and the audit event appends
// under the same lock path.
var rotationInProgress atomic.Bool

// maybeRotateCaptainsLog is called after every captain's-log append.
func (r *Recorder) maybeRotateCaptainsLog(path string) {
	if !rotationInProgress.CompareAndSwap(false, true) {
		return
	}
	defer rotationInProgress.Store(false)

	st, err := os.Stat(path)
	if err != nil {
		return // no active file yet; nothing to rotate
	}

	// Python reads config here too, on every log_event. Its load_config is
	// mtime-cached and this one is not, so this runtime re-reads two small
	// YAML files per event where Python re-reads none. Recorded rather than
	// worked around: the read is bounded and the alternative (a cache in
	// config.Load) changes behaviour for every other caller, including the
	// tests that write a config file and immediately read it back.
	cfg, _ := config.Load()
	rotateMB := config.Get(cfg, "captains_log.rotate_mb", defaultRotateMB)
	keep := config.Get(cfg, "captains_log.rotate_keep", defaultRotateKeep)
	if rotateMB <= 0 {
		return // explicitly disabled
	}
	maxBytes := int64(rotateMB * 1024 * 1024)
	if st.Size() < maxBytes {
		return
	}
	if keep < 0 {
		keep = 0
	}

	var archived, retained int
	var archiveName string
	err = Locked(path, func() error {
		// Re-check under the lock: another process may have rotated between
		// the stat above and the lock.
		st, err := os.Stat(path)
		if err != nil || st.Size() < maxBytes {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		// pytext.SplitLines and pytext.Strip, not strings.Split("\n") and
		// strings.TrimSpace. Python reads this file with
		// `read_text().splitlines()`, which breaks on ten separators, and
		// filters with `l.strip()`, which drops U+001C–U+001F where Go's
		// TrimSpace does not. It is not hypothetical here: this port does not
		// set ensure_ascii, so a Go-written row can carry a RAW U+2028, and
		// Python would then see two lines where Go sees one — a rotation that
		// disagrees with the other runtime about where rows begin is a
		// rotation that cuts one in half.
		var lines []string
		for _, l := range pytext.SplitLines(string(raw)) {
			if pytext.Strip(l) != "" {
				lines = append(lines, l)
			}
		}
		head, tail := lines, []string(nil)
		if keep > 0 {
			if keep >= len(lines) {
				return nil // fewer entries than the tail retention
			}
			head, tail = lines[:len(lines)-keep], lines[len(lines)-keep:]
		}
		if len(head) == 0 {
			return nil
		}

		archive, err := freeArchivePath(path)
		if err != nil {
			return err
		}
		if err := atomicWrite(archive, []byte(strings.Join(head, "\n")+"\n")); err != nil {
			return fmt.Errorf("write archive: %w", err)
		}
		// The active file is only truncated AFTER the archive is durable.
		// The reverse order loses every archived entry to a crash in the
		// window, which is the auto-delete this rotation must never be.
		body := ""
		if len(tail) > 0 {
			body = strings.Join(tail, "\n") + "\n"
		}
		if err := atomicWrite(path, []byte(body)); err != nil {
			return fmt.Errorf("rewrite active file: %w", err)
		}
		archived, retained, archiveName = len(head), len(tail), filepath.Base(archive)
		return nil
	})
	if err != nil {
		warn("captains_log: rotation failed: %v", err)
		return
	}
	if archiveName == "" {
		return
	}

	// The audit row, byte-identical to Python's. It re-enters EventNoted,
	// which re-enters this function — the guard above is what stops that,
	// and it is still held here because the deferred Store has not run.
	if err := r.EventNoted("LOG_ROTATED", archiveName,
		fmt.Sprintf("Rotated %d entries to %s; %d retained in the active file",
			archived, archiveName, retained),
		map[string]any{
			"archived": archived,
			"retained": retained,
			"archive":  archiveName,
		}, "", "", nil); err != nil {
		warn("captains_log: rotated to %s but the audit row was not written: %v",
			archiveName, err)
	}
}

// utcNow is the clock freeArchivePath stamps with, behind one indirection
// so the same-second collision path can be tested without racing the wall
// clock: occupying a second's worth of archive names takes long enough that
// the second can tick over mid-setup, and a test that only sometimes
// exercises the path it names is worse than no test.
var utcNow = func() time.Time { return time.Now().UTC() }

// atomicWrite is AtomicWrite behind one indirection, so a test can fail the
// ARCHIVE write specifically and assert the active file survived. Without a
// per-path seam, "archive first, truncate second" is an equivalent mutation
// — swapping the two calls changes nothing on any successful rotation, and
// the whole point of the order is what happens when the first one fails.
var atomicWrite = AtomicWrite

// warn is this package's stand-in for Python's logger.warning. It exists as
// a named function so the rotation tests can capture what was said rather
// than assert only that nothing crashed.
var warn = func(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
}

// freeArchivePath is Python's `captains_log.<stamp>.jsonl` with its
// same-second collision suffix. The stamp is fixed-width and UTC, which is
// what makes a lexicographic sort of the archives chronological — the
// property _archive_paths relies on and the readers inherit.
func freeArchivePath(active string) (string, error) {
	dir := filepath.Dir(active)
	stamp := utcNow().Format("20060102-150405")
	archive := filepath.Join(dir, "captains_log."+stamp+".jsonl")
	for n := 1; ; n++ {
		if _, err := os.Stat(archive); os.IsNotExist(err) {
			return archive, nil
		}
		if n > maxArchiveCollisions {
			return "", fmt.Errorf("no free archive name after %d attempts at %s",
				maxArchiveCollisions, stamp)
		}
		archive = filepath.Join(dir,
			fmt.Sprintf("captains_log.%s-%d.jsonl", stamp, n))
	}
}

// ArchivePaths is captains_log._archive_paths: the rotated archives, oldest
// first. The timestamped pattern never matches the active
// captains_log.jsonl, and the fixed-width stamp makes lexicographic order
// chronological. Both runtimes' archaeology readers span these; the hot-path
// reader stays active-file-only.
func ArchivePaths(memoryDir string) []string {
	matches, err := filepath.Glob(filepath.Join(memoryDir, "captains_log.*.jsonl"))
	if err != nil {
		return nil
	}
	// filepath.Glob already returns sorted names, and Python's sorted() over
	// Path objects sorts on the same bytes.
	//
	// Directories matching the pattern are dropped, which Python's glob does
	// not do. Every consumer of this list reads the path as a file, so a
	// directory here is an unreadable entry in a corpus rather than an extra
	// one — and this port's own rotation test managed to create three.
	out := matches[:0]
	for _, m := range matches {
		if st, err := os.Stat(m); err == nil && st.Mode().IsRegular() {
			out = append(out, m)
		}
	}
	return out
}

// AllLogPaths is captains_log._all_log_paths: archives oldest first, then
// the active file — the full corpus in chronological order.
func AllLogPaths(memoryDir string) []string {
	return append(ArchivePaths(memoryDir),
		filepath.Join(memoryDir, "captains_log.jsonl"))
}
