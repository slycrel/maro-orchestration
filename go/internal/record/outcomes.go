package record

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// LoadOutcomes returns up to limit recent outcome rows, NEWEST FIRST —
// Python memory.load_outcomes' ordering, which both the inspector and
// the evolver consume ("Recent outcomes:" summaries iterate the head).
// Rows come back as tolerant maps, not typed structs: the store is
// shared with the Python runtime, whose rows carry fields this port has
// never written (mission_id, lesson_extraction_status, verdict_history),
// and a reader that drops unknown fields would blind every downstream
// judgment that keys on them (the inspector's verdict-pending rule reads
// lesson_extraction_status — a field only Python writes today).
//
// A line that fails to parse is skipped, one row lost — never the whole
// read (the announced-read posture of the Python jsonl_utils arc: one
// torn byte costs one row, not an empty corpus). limit <= 0 means all
// rows — the zero value degrades to "everything", never to "nothing"
// (the recall r1 LoadOptions lesson).
//
// A missing file is an empty store, not an error.
func LoadOutcomes(workspaceDir string, limit int) ([]map[string]any, error) {
	path := filepath.Join(workspaceDir, "memory", "outcomes.jsonl")
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("load outcomes: %w", err)
	}
	// read_jsonl_announced, then the dataclass filter — Python's
	// `_rows_as(path, "load_outcomes", lambda d: Outcome(**...))`, which is
	// TWO readers stacked and was ported as neither.
	//
	// The old body split on "\n", stripped, and dropped anything that
	// failed json.Unmarshal, silently. That differs from CPython twice:
	//
	//   - the LOSS was unannounced and unbucketed. A torn byte, an array
	//     row and a truncated line are three different damages and CPython
	//     says which; this said nothing at all, so a store quietly rotting
	//     looked exactly like a store that was fine.
	//   - the SCHEMA drift was not applied. Outcome has six fields with no
	//     default, and a row missing any of them raises TypeError inside
	//     _rows_as and is EXCLUDED. This port kept it. Measured on a
	//     three-row fixture without outcome_id/lessons: CPython loads 0 and
	//     the evolver skips the cycle ("only 0 outcomes (need 3)"), while
	//     the port loads 3 and runs one. Same store, same knob, opposite
	//     decisions — and the differential that found it had been reporting
	//     agreement, because both sides minted from an empty list.
	// Decoded ordered and then FLATTENED, rather than through CountLine
	// directly, because the two readers type numbers differently and every
	// consumer of these rows type-asserts.
	//
	// CountLine leaves a number as json.Number; pyval.Plain resolves it to
	// an int for an integral literal and a float64 otherwise, which is
	// CPython's own json.loads typing and what asdict(Outcome) hands the
	// inspector. The old body used json.Unmarshal, whose every number is a
	// float64 — so `tokens_in: 1200` arrived as 1200.0 where Python has the
	// int 1200, and a renderer reaching for str() got "1200.0".
	rep := SkipReport{}
	var rows []map[string]any
	for _, line := range strings.Split(string(raw), "\n") {
		o := CountLineOrdered(line, &rep)
		if o == nil {
			continue
		}
		m, ok := pyval.Plain(o).(map[string]any)
		if !ok {
			continue
		}
		rows = append(rows, m)
	}
	if w := rep.Announce("load_outcomes", path); w != "" {
		warn("%s", w)
	}
	rows = keepLoadableOutcomes(rows, path)
	// Newest first: file order is append order (oldest first).
	for i, j := 0, len(rows)-1; i < j; i, j = i+1, j-1 {
		rows[i], rows[j] = rows[j], rows[i]
	}
	if limit > 0 && len(rows) > limit {
		rows = rows[:limit]
	}
	return rows, nil
}

// keepLoadableOutcomes applies the dataclass filter and announces the drift
// in CPython's own sentence.
//
// Drift is announced SEPARATELY from framing loss on purpose, and the
// Python docstring says why: a row that is not JSON is corruption, a row
// that is JSON but the current dataclass rejects is schema drift, and
// collapsing them hides which one is happening — drift being the one that
// grows quietly as the schema moves.
func keepLoadableOutcomes(rows []map[string]any, path string) []map[string]any {
	kept := make([]map[string]any, 0, len(rows))
	drifted, firstErr := 0, ""
	for _, r := range rows {
		missing := missingOutcomeFields(r)
		if len(missing) == 0 {
			kept = append(kept, r)
			continue
		}
		drifted++
		if firstErr == "" {
			firstErr = "TypeError: " + PyMissingArgsMessage("Outcome", missing)
		}
	}
	if drifted > 0 {
		warn("load_outcomes: %d row(s) in %s are JSON but not loadable under "+
			"the current schema — excluded from the %d returned (first: %s)",
			drifted, path, len(kept), firstErr)
	}
	return kept
}

// missingOutcomeFields is the PRESENCE test the dataclass performs. A
// present null is a value — `Outcome(outcome_id=None, ...)` constructs
// fine, because a dataclass does not enforce its annotations — so only
// absence excludes a row.
func missingOutcomeFields(row map[string]any) []string {
	var missing []string
	for _, f := range outcomeRequiredFields {
		if _, ok := row[f]; !ok {
			missing = append(missing, f)
		}
	}
	return missing
}

// PyMissingArgsMessage reproduces CPython's TypeError text for a call with
// missing positional arguments, measured on this box:
//
//	missing 1 required positional argument: 'lessons'
//	missing 2 required positional arguments: 'summary' and 'lessons'
//	missing 5 required positional arguments: 'goal', 'task_type', 'status', 'summary', and 'lessons'
//
// Singular/plural, "and" with no comma at two, and the Oxford comma at
// three or more. It is operator-facing prose in a warning both runtimes
// emit about the same file, which is the class of divergence this port
// keeps finding — a byte-different sentence about identical damage reads
// as two different problems.
//
// Exported because the same sentence appears wherever a `_rows_as` loader
// is ported — evolver_store's load_suggestions is the second — and a
// second private copy of it is how the two spellings start to drift.
func PyMissingArgsMessage(cls string, names []string) string {
	quoted := make([]string, len(names))
	for i, n := range names {
		quoted[i] = "'" + n + "'"
	}
	var list string
	switch len(quoted) {
	case 1:
		list = quoted[0]
	case 2:
		list = quoted[0] + " and " + quoted[1]
	default:
		list = strings.Join(quoted[:len(quoted)-1], ", ") + ", and " + quoted[len(quoted)-1]
	}
	plural := "s"
	if len(quoted) == 1 {
		plural = ""
	}
	return fmt.Sprintf("%s.__init__() missing %d required positional argument%s: %s",
		cls, len(names), plural, list)
}

// LockedRMW runs one read-modify-write of a whole file under the same
// .lock flock protocol every appender takes (Python file_lock.locked_rmw
// parity — cross-runtime writers of the cadence counters and the
// suggestions store contend on the identical path+".lock" file). fn
// receives the current content ("" when the file is missing) and returns
// the full replacement; the write lands via temp+rename so a reader
// never sees a partial rewrite.
func LockedRMW(path string, fn func(old string) string) error {
	// The lock file lives beside the data file — its parent must exist
	// before Locked can open it (first tick on a fresh workspace).
	if err := os.MkdirAll(filepath.Dir(path), NewDirMode); err != nil {
		return err
	}
	return Locked(path, func() error {
		var old string
		if raw, err := os.ReadFile(path); err == nil {
			old = string(raw)
		} else if !os.IsNotExist(err) {
			return fmt.Errorf("rmw read %s: %w", path, err)
		}
		out := fn(old)
		if err := os.MkdirAll(filepath.Dir(path), NewDirMode); err != nil {
			return err
		}
		if err := AtomicWrite(path, []byte(out)); err != nil {
			return fmt.Errorf("rmw write %s: %w", path, err)
		}
		return nil
	})
}

// AtomicWrite replaces a file's whole contents crash-safely: write to a
// temp beside it, FSYNC, rename.
//
// The fsync is the point, and it is what Python's file_lock.atomic_write
// does ("mkstemp in path's dir, write, fsync, os.replace"). Without it, a
// rename can be durable while the data behind it is not, so a power loss in
// that window truncates the file to zero — and every caller here is a
// DESTRUCTIVE whole-store rewrite, so what is lost is the whole store, not
// one appended line. Three copies of the unsynced temp+rename had grown
// across this port before they were collapsed here.
//
// An existing file's mode is preserved, again matching Python: a store an
// operator chmod'd stays chmod'd across a rewrite.
//
// Residual, recorded: the DIRECTORY entry is not fsynced, so a crash can
// lose the rename itself even though the data is durable. Python does not
// fsync the directory either, and the failure mode is "the old file is
// still there", not a corrupt one.
func AtomicWrite(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, NewDirMode); err != nil {
		return err
	}
	// An existing file keeps its exact mode; a new one gets what a plain
	// open() would give it. Both are set with Chmod AFTER create rather
	// than through OpenFile's mode argument, because the kernel applies
	// the umask to that argument — which would silently narrow an
	// operator's deliberately-widened 0666 ledger back to 0664 on every
	// rewrite. Python uses mkstemp + fchmod for exactly this reason.
	mode := NewFileMode()
	if st, err := os.Stat(path); err == nil {
		mode = st.Mode().Perm()
	}
	// A UNIQUE temp name, not path+".tmp". Two concurrent writers of the
	// same store shared that one name: both opened it O_TRUNC, both wrote,
	// and whichever renamed second published a file the other had already
	// truncated under it. The r3 notes deferred this on the grounds that
	// every caller holds the store lock — six of them do not
	// (adversarial r4, L6), and the ones that do not are whole-store
	// rewrites, so what a lost race costs is the whole store.
	f, err := os.CreateTemp(dir, filepath.Base(path)+".tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	if err := f.Chmod(mode); err != nil {
		f.Close()
		os.Remove(tmp)
		return err
	}
	if _, err := f.Write(data); err != nil {
		f.Close()
		os.Remove(tmp)
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp) // don't strand the temp beside the intact store
		return err
	}
	return nil
}
