package record

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/stopverdicts"
)

// StampOutcomeStopVerdict is memory_ledger.stamp_outcome_stop_verdict: a
// post-hoc stop-verdict stamp on the NEWEST outcomes row for a loop id.
//
// Post-hoc is the whole point. The reachable-but-not-worth-it verdict is
// decided AFTER the run closed — a director escalation "close" is a later
// value/cost judgment about a run that already ended "stuck" — so it has
// to land on a row that was written and closed by someone else, the same
// way closure verdicts do.
//
// Merge-only: it touches stop_verdict and (when evidence is given)
// stop_evidence, and NEVER the goal-verdict fields. Best-effort by
// contract — false on any miss, including an off-vocabulary verdict,
// which fails to UNSTAMPED so a reader's status fallback applies rather
// than a phantom value standing in the field.
//
// Returns whether a row was actually updated. That boolean is the honest
// answer to "is this judgment recorded?", and a caller that wants to
// report the close as durable has to look at it.
func StampOutcomeStopVerdict(workspaceDir string, loopID any,
	stopVerdict, stopEvidence string) (bool, error) {
	// `any`, because Python's `if not loop_id` is a truthiness gate over
	// whatever the caller held and the match below is Python's `==`. The
	// escalation close hands this a task's raw `parent_job_id`, so an
	// integer id must stay an integer and match NOTHING — which is what
	// CPython does, and the opposite of what a spelled "4242" does.
	if !pyval.Truthy(loopID) || stopVerdict == "" {
		return false, nil
	}
	// The vocabulary gate lives HERE and not at the metadata stamp,
	// matching Python. See runs.StampRunStopVerdict's note on why the
	// asymmetry is preserved rather than tidied.
	if !stopverdicts.IsValidStopValue(stopVerdict) {
		return false, nil
	}
	path := filepath.Join(workspaceDir, "memory", "outcomes.jsonl")

	// A missing store is a miss, not an error, and must not CREATE the
	// store file. That property is real, and an earlier cut of this port
	// bought it with a `os.Stat(path)` short-circuit ABOVE the lock, under
	// a comment claiming Python "reads first ... before any writer is
	// involved". Python does not: `with locked_write(path):` comes first,
	// and only inside it does the read raise FileNotFoundError
	// (memory_ledger.py:921). The no-phantom-store property was never
	// carried by the stat — it is carried by the conditional AtomicWrite
	// below, which does not run on a miss, in either runtime.
	//
	// The stat cost three things and bought nothing:
	//   - a TOCTOU window Python does not have. The stat is unsynchronized,
	//     so a row appended between it and the lock is a row this returns
	//     "no such store" for and Python stamps.
	//   - the .lock sidecar and the memory/ directory, which
	//     locked_write creates unconditionally (file_lock.py:144). A cold
	//     workspace ends up shaped differently on the two runtimes after
	//     the identical call.
	//   - the honesty of the comment above it.
	// Found by widening the escalation differential to compare lock files
	// by name instead of skipping them.
	hit := false
	// Locked plus a CONDITIONAL AtomicWrite — deliberately NOT LockedRMW.
	//
	// LockedRMW is the natural fit here and it is the wrong one: it writes
	// whatever the callback returns, unconditionally, so a lookup that
	// found nothing still replaces the file. Same bytes, but a new inode,
	// a new mtime, and a window to race anyone appending. Python is shaped
	// this way for exactly that reason — `locked_write`, read, then
	// `if hit["v"]: atomic_write` — and the tell is in the guard itself:
	// a rewrite that is conditional on a flag is not a read-modify-write.
	// (Caught here by an mtime assertion; the content comparison alone
	// passed, because the rewrite really is byte-identical.)
	err := Locked(path, func() error {
		raw, rerr := os.ReadFile(path)
		if rerr != nil {
			// No store, or it vanished under the lock. A miss, not an
			// error — Python's inner `except FileNotFoundError: return
			// False` is this exact branch.
			return nil
		}
		out, matched := stampNewestRow(string(raw), loopID, stopVerdict, stopEvidence)
		if !matched {
			return nil
		}
		hit = true
		return AtomicWrite(path, []byte(out))
	})
	if err != nil {
		return false, err
	}
	return hit, nil
}

// stampNewestRow rewrites the newest row belonging to loopID and returns
// the whole store text, plus whether anything matched. On a miss the text
// it returns is meaningless and the caller must not write it.
func stampNewestRow(old string, loopID any, stopVerdict, stopEvidence string) (string, bool) {
	// SplitLines, not strings.Split(old, "\n"): Python's str.splitlines()
	// breaks on eight separators Go's does not — \v, \f, \x1c–\x1e,
	// U+2028, U+2029, U+0085. A row is ensure_ascii-escaped so its own
	// content never carries one raw, but a FOREIGN writer's row can, and
	// then the two runtimes disagree about how many rows the store has.
	// Matching Python here means matching it including where Python is
	// lossy: the rejoin below normalizes every one of those to \n.
	lines := pytext.SplitLines(old)
	for i := len(lines) - 1; i >= 0; i-- {
		// Newest first. The ledger is append-ordered, so the LAST matching
		// row is the current one — an earlier row for the same loop id is
		// a superseded attempt and keeps its own record.
		line := pytext.Strip(lines[i])
		if line == "" {
			continue
		}
		// LoadsCleanOrdered, not LoadsClean: this line is about to be
		// re-serialized, and a map would re-emit every key alphabetized.
		// A line it REFUSES is skipped and carried verbatim — the
		// corruption keeps announcing itself instead of being laundered
		// by a rewrite.
		row, perr := LoadsCleanOrdered(line)
		if perr != nil {
			continue
		}
		// Python compares `row.get("loop_id") == loop_id` against a str,
		// so a row whose loop_id decoded as a NUMBER does not match
		// (5 == "5" is False in Python). The type assertion is what keeps
		// that true here; pyval.Str would spell the number and stamp a
		// row Python skips.
		v, present := row.Get("loop_id")
		if !present {
			continue
		}
		// Python's `==`, not Go's: `5 == "5"` is False and `5 == 5.0` is
		// True, and both sides here were decoded from separate files.
		if !pyval.Eq(v, loopID) {
			continue
		}
		row.Set("stop_verdict", stopVerdict)
		if stopEvidence != "" {
			// Absent evidence leaves any EXISTING stop_evidence standing
			// rather than clearing it — this stamper is merge-only,
			// unlike the metadata tuple owner, which replaces whole. The
			// two really are different contracts: metadata describes THIS
			// ending, the ledger row accumulates what is known about the
			// run.
			row.Set("stop_evidence", budget.Clip(stopEvidence, 800))
		}
		out, derr := pyval.DumpsCompactPy(row)
		if derr != nil {
			// Leave the row untouched rather than writing a partial one,
			// and report a miss so nothing is written at all.
			return "", false
		}
		lines[i] = out
		// Python's `("\n" if lines else "")` cannot fire here — this is
		// inside a loop over `lines`, so there is at least one. Spelling
		// the guard out anyway read as though the empty case were handled,
		// which is worse than not handling it (adversarial r11 round 2,
		// LOW). The empty case returns not-found above, where it belongs.
		return strings.Join(lines, "\n") + "\n", true
	}
	return "", false
}
