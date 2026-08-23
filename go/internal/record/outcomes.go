package record

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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
	var rows []map[string]any
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var m map[string]any
		if jerr := json.Unmarshal([]byte(line), &m); jerr != nil {
			continue
		}
		rows = append(rows, m)
	}
	// Newest first: file order is append order (oldest first).
	for i, j := 0, len(rows)-1; i < j; i, j = i+1, j-1 {
		rows[i], rows[j] = rows[j], rows[i]
	}
	if limit > 0 && len(rows) > limit {
		rows = rows[:limit]
	}
	return rows, nil
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
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
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
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
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
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	mode := os.FileMode(0o644)
	if st, err := os.Stat(path); err == nil {
		mode = st.Mode().Perm()
	}
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, mode)
	if err != nil {
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
