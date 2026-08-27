package record

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
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
// tail cascades.
//
// The failure it prevents is UNBOUNDED RECURSION, not a deadlock. An
// earlier version of this comment said the guard also kept Locked from
// deadlocking against itself — it does not, and the distinction matters
// because a maintainer who goes looking for the lock nesting will not
// find any and may conclude the guard is removable. The Locked critical
// section closes before the audit append is made; each re-entry takes and
// releases the lock cleanly and then recurses, one archive per row, until
// the process dies (adversarial r8 MEDIUM ×2 — the guard was also pinned
// only at a retention where neither failure could occur).
var rotationInProgress atomic.Bool

// warnOnce emits each distinct config warning a single time per process.
//
// This exists because of the asymmetry noted below: Python's load_config is
// mtime-cached, so a malformed config warns once and again only when the
// file changes. This runtime re-reads per event, and the read happens
// BEFORE the size gate — so surfacing the warnings raw would put a line on
// stderr for every captain's-log append forever, which is how a real
// warning gets trained out of a reader. Dropping them instead was the
// previous behaviour (`cfg, _ := config.Load()`) and is worse: a config
// this runtime could not parse then changed rotation thresholds silently.
var warnedConfig sync.Map

func warnOnce(msg string) {
	if _, seen := warnedConfig.LoadOrStore(msg, true); !seen {
		warn("captains_log: %s", msg)
	}
}

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
	cfg, warnings := config.Load()
	for _, w := range warnings {
		warnOnce(w)
	}
	// Python coerces both keys with float()/int() inside ONE try/except:
	//
	//	try:
	//	    rotate_mb = float(_cfg_get("captains_log.rotate_mb", 5))
	//	    keep = int(_cfg_get("captains_log.rotate_keep", 1000))
	//	except Exception:
	//	    rotate_mb, keep = 5.0, 1000
	//
	// Two things fall out of that, and typed config.Get reproduced neither.
	// A QUOTED value — `rotate_mb: "10"`, which is what an operator gets
	// from a templated or env-substituted config — is a float() there and a
	// type mismatch here, so Python rotated at 10 MB and this runtime at 5.
	// And the reset is JOINT: a bad rotate_keep sends rotate_mb back to its
	// default too. Both matter on a shared store, where the two runtimes
	// rotating at different thresholds means one of them is archiving rows
	// the other still considers active.
	rotateMB, keep := defaultRotateMB, defaultRotateKeep
	// config.Lookup, not config.Get: Get folds "absent" and "present but
	// null" together, and Python does not. There, an absent key reaches
	// float()/int() as the default and an explicit null reaches them as
	// None, which raises and resets BOTH keys jointly. Feeding the raw
	// value (default substituted only when genuinely absent) to the same
	// coercers reproduces that without a special case (adversarial r7 LOW
	// — the residual note here used to call this unreachable without a raw
	// seam, and it was measurably reachable: `{rotate_mb: null,
	// rotate_keep: 50}` rotated at (5.0, 1000) in Python and (5.0, 50)
	// here, on a shared log).
	rawMB, present := config.Lookup(cfg, "captains_log.rotate_mb")
	if !present {
		rawMB = any(defaultRotateMB)
	}
	rawKeep, present := config.Lookup(cfg, "captains_log.rotate_keep")
	if !present {
		rawKeep = any(defaultRotateKeep)
	}
	mb, okMB := coerceFloat(rawMB)
	kp, okKeep := coerceInt(rawKeep)
	if okMB && okKeep {
		rotateMB, keep = mb, kp
	}
	if rotateMB <= 0 {
		return // explicitly disabled
	}
	// Python computes `int(rotate_mb * 1024 * 1024)` INSIDE the try that
	// wraps the whole rotation, and Python ints are arbitrary precision.
	// So the two out-of-range cases behave differently there and both
	// must be reproduced (adversarial r8 MEDIUM):
	//
	//   - .inf / .nan  → int() RAISES, the except warns, rotation is
	//     abandoned. `.inf` is exactly what an operator writes to mean
	//     "never rotate".
	//   - a finite but enormous value → int() succeeds, max_bytes is a
	//     huge int, and the size check simply never fires.
	//
	// Go's int64(±Inf) / int64(NaN) / int64(overflow) is an unrepresentable
	// conversion: on amd64 all three yield MinInt64, so `size < maxBytes`
	// is FALSE and the log rotates every time — the exact inversion of
	// what `.inf` was asked for, on a file Python considers untouched.
	// (The result is even architecture-dependent; arm64 saturates the
	// other way.)
	product := rotateMB * 1024 * 1024
	if math.IsNaN(product) || math.IsInf(product, 0) {
		warn("captains_log: rotation failed: cannot convert float %s to integer",
			nonFiniteName(product))
		return
	}
	if product >= math.MaxInt64 {
		return // Python's huge-but-finite max_bytes: the gate never fires
	}
	maxBytes := int64(product)
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
		// TrimSpace does not.
		//
		// The SplitLines half is reachable, and by exactly one rune. This
		// comment used to name U+2028, which is wrong: pyjson escapes
		// U+2028 and U+2029 unconditionally, so neither can reach the file
		// raw. Measured over every separator splitlines() breaks on, the
		// only one pyjson emits RAW is U+0085 (NEL) — every other one is
		// escaped, and U+001F, which pyjson also escapes, is not a
		// splitlines() break to begin with. So: a Go-written row can carry
		// a raw U+0085, Python then sees two lines where strings.Split
		// would see one, and a rotation that disagrees with the other
		// runtime about where rows BEGIN is a rotation that cuts one in
		// half. That is what the differential pins.
		//
		// The Strip half is parity with no demonstrated reachable case —
		// both encoders escape U+001C–U+001F, so a line that is only those
		// runes cannot come from either writer. Kept because matching
		// Python exactly is free and the file is shared with writers this
		// port does not own; labelled honestly rather than justified with
		// the SplitLines argument, which does not carry it.
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

	// The audit row. Its PROSE is byte-identical to Python's, which is
	// what matters — subject and summary are the strings a reader and the
	// log's own consumers see. The row as a whole is not: this port's JSON
	// separators and its nested-context key order are both named, accepted
	// divergences (PORT.md, and pyjson's sorted nested maps). An earlier
	// version of this comment claimed the whole row was byte-identical,
	// which contradicted PORT.md's own honest note (adversarial r8 LOW).
	//
	// It re-enters EventNoted, which re-enters this function — the guard
	// above is what stops that, and it is still held here because the
	// deferred Store has not run.
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
	// filepath.Glob's order is a RAW BYTE sort — it byte-sorts its matches
	// through a sort.Strings inside the standard library. Python's
	// `sorted()` over Path objects compares the surrogateescape-DECODED
	// string, so the two part on any archive name that is not valid UTF-8.
	// Measured, with captains_log.{z,\xc3\xa9,\x80,\xff}.jsonl in one
	// directory: CPython returns z, e-acute, \x80, \xff; Glob returns
	// z, \x80, e-acute, \xff. This list is "oldest first" and feeds the
	// archaeology readers, so the two engines would replay one corpus in
	// two orders. The comment that used to sit here asserted the opposite
	// as settled fact (adversarial r7, LOW).
	sort.SliceStable(matches, func(i, j int) bool {
		return pypath.FSLess(matches[i], matches[j])
	})
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

// nonFiniteName spells a non-finite float the way CPython's exception
// message does, so the warning a Go operator reads matches the one the
// Python runtime would have logged for the same config.
func nonFiniteName(f float64) string {
	if math.IsNaN(f) {
		return "NaN"
	}
	return "infinity"
}
