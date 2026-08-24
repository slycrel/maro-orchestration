package runs

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The durable run-reference index, ported from runs.py.
//
// A run dir is named for its handle id, so a handle id resolves by
// building the path. Every OTHER reference to a run — and loop_id is the
// one that matters, because the outcome ledger and the verdict seam join
// on it — has to be looked up. The index is that lookup: one small JSON
// file per reference, named for the sha256 of the reference, mapping it
// to a run-dir NAME.
//
// v2 (Python, 2026-07-29) is the version this ports. v1's refs did not
// include the plural loops-ledger keys (loop_ids + loops[].loop_id).
// The loops ledger had stopped stamping the singular metadata.loop_id,
// so every v1-indexed run was unreachable by loop_id — which silently
// no-op'd every loop_id-keyed consumer at the verdict seam. The version
// bump forces one full re-migration on first miss; the v1 directory is
// left in place as an orphaned cache.
//
// The whole thing is an OPTIMIZATION over a directory scan, and the code
// is written so that it can never become an availability dependency: a
// read-only, corrupt, or lock-starved index degrades to the historical
// scan rather than reporting the run missing. That posture is the reason
// for the otherwise-odd shape of the error handling below, and it is
// preserved rather than tidied.
const (
	indexDirName = ".run-ref-index-v2"
	indexMarker  = ".migrated"
)

// indexDir is a SIBLING of runs/, not a child: Python computes it as
// `runs.parent / _RUN_INDEX_DIR`. Putting it inside runs/ would make
// every legacy scan have to skip it, which is why the scan's
// dot-prefix filter exists as a second line of defence rather than the
// only one.
func indexDir(runsRoot string) string {
	return filepath.Join(filepath.Dir(runsRoot), indexDirName)
}

func indexEntryPath(ref, runsRoot string) string {
	sum := sha256.Sum256([]byte(ref))
	return filepath.Join(indexDir(runsRoot), hex.EncodeToString(sum[:])+".json")
}

// RunsRoot is the workspace runs/ directory.
func RunsRoot(workspaceDir string) string { return filepath.Join(workspaceDir, "runs") }

// metadataRefs is every durable reference under which a run should be
// findable. Empty strings are dropped; the result is a SET, because the
// same id legitimately appears in more than one field.
//
// The order the refs come back in does not matter to any consumer — each
// is written to its own file — so this returns a map rather than being
// forced into a sorted slice for a determinism nothing needs.
func metadataRefs(meta map[string]any) map[string]bool {
	refs := map[string]bool{}
	add := func(v any) {
		// Python writes `str(x or "")` at every one of these five sites,
		// and BOTH halves matter.
		//
		// The `or ""` is a truthiness gate, not a nil check: it turns
		// None, "", 0, False and every empty container into "", which the
		// nonempty filter below then drops. Reaching straight for
		// pyval.Str skips it — and pyval.Str(nil) is "None", because
		// Python's str(None) is the four-character string "None". That
		// port wrote an index entry for the literal ref "None" on every
		// run whose metadata lacked a loop_id, i.e. most of them, all
		// colliding on one file. Caught by the CPython differential.
		//
		// The str() is why this is not a plain type assertion: a loop id
		// that arrived from JSON as a NUMBER is spelled, not dropped.
		if s := pyval.StrOrEmpty(v); s != "" {
			refs[s] = true
		}
	}
	add(meta["handle_id"])
	add(meta["loop_id"])
	// A run dir hosts SEVERAL loops — the initial one plus closure
	// restarts and continuations — recorded as metadata.loop_ids and as
	// the metadata.loops lineage ledger. Each of those ids has to be a
	// key, which is precisely what v1 got wrong.
	if lst, ok := meta["loop_ids"].([]any); ok {
		for _, lid := range lst {
			add(lid)
		}
	}
	if lst, ok := meta["loops"].([]any); ok {
		for _, e := range lst {
			if m, ok := e.(map[string]any); ok {
				add(m["loop_id"])
			}
		}
	}
	if origin, ok := meta["origin"].(map[string]any); ok {
		add(origin["resumed_from"])
	}
	return refs
}

// pathDotNameIsSelf is Python's `Path(name).name == name`, exactly — the
// predicate runs.py uses as its traversal guard on an index entry's
// stored directory name.
//
// Spelled out rather than delegated to filepath.Base, which disagrees on
// most of the interesting inputs: Base("") is ".", Base("/") is "/",
// Base("a/") is "a". pathlib normalizes trailing slashes and single dots
// away and returns the last component, so a slashed or dotted form never
// equals itself — while a plain name, and the two degenerate cases below,
// do.
func pathDotNameIsSelf(name string) bool {
	if name == "" {
		// Path("").name is "", which equals itself. TRUE in Python.
		return true
	}
	if strings.ContainsRune(name, '/') {
		// Any separator means pathlib returns a different (or empty) last
		// component: "a/b"→"b", "a/"→"a", "/"→"", "//"→"".
		return false
	}
	if name == "." {
		// A single dot is a no-op component, dropped entirely: "" ≠ ".".
		return false
	}
	// Everything else equals itself — including "..", which pathlib keeps
	// as a literal part, and "...", which is just a filename.
	return true
}

// isBareName is the guard this port actually applies: Python's predicate
// AND a refusal of the degenerate names Python's predicate lets through.
//
// A DELIBERATE, named divergence, in the safe direction. Measured against
// CPython: Path("").name == "" and Path("..").name == "..", so runs.py's
// `Path(name).name != name` check ACCEPTS an index entry whose run_dir is
// "" or "..". Both then pass the is_dir() test right after it, because
// root/"" IS the runs root and root/".." is the workspace directory — so
// a corrupt entry resolves to a directory that is not a run at all, and a
// caller like StampRunStopVerdict writes metadata.json into the top of
// the workspace.
//
// Matching that faithfully would import a bug rather than a behaviour.
// The intent is stated at Python's own raise site — "invalid run-index
// entry" — and these are invalid by that intent; the acceptance is an
// artifact of what pathlib does with a name nobody expected to store. The
// r1 lens says a hardening IS a divergence and must be named, not that it
// is always wrong. This one is named, and the test below pins both halves
// separately so the Python predicate stays honestly reproduced.
//
// If it ever fires: Go treats the entry as corrupt, unlinks it, falls
// back to the legacy scan for that one reference, and republishes what it
// finds — while Python returns a wrong directory. Neither runtime writes
// such an entry in the first place; getting one requires something else
// with write access to the index directory.
func isBareName(name string) bool {
	if name == "" || name == "." || name == ".." {
		return false
	}
	return pathDotNameIsSelf(name)
}

// writeIndexEntry publishes one ref → run-dir mapping.
//
// The read-back-and-keep branch preserves the historical scan's
// semantics for a DUPLICATE ref: when two live run dirs both claim the
// same reference, the alphabetically-first one wins, because that is
// what a sorted directory scan returned. Dropping that would make the
// index answer a question differently from the fallback it replaces,
// which is the one thing an optimization must not do.
func writeIndexEntry(ref, runDir string) error {
	root := filepath.Dir(runDir)
	name := filepath.Base(runDir)
	path := indexEntryPath(ref, root)
	payload, err := pyjsonEntry(ref, name)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return err
	}
	return record.Locked(path, func() error {
		if raw, rerr := os.ReadFile(path); rerr == nil {
			var existing map[string]any
			if v, perr := pyval.LoadsOrdered(string(raw)); perr == nil {
				existing, _ = pyval.Plain(v).(map[string]any)
			}
			if existing != nil {
				existingName, isStr := existing["run_dir"].(string)
				if pyval.StrOrEmpty(existing["ref"]) == ref && isStr &&
					isBareName(existingName) &&
					isDir(filepath.Join(root, existingName)) &&
					existingName <= name {
					return nil
				}
			}
		}
		return record.AtomicWrite(path, []byte(payload))
	})
}

// InvalidateRunIndex forces one legacy metadata migration on the next
// indexed lookup.
func InvalidateRunIndex(runsRoot string) error {
	marker := filepath.Join(indexDir(runsRoot), indexMarker)
	if err := os.MkdirAll(filepath.Dir(marker), record.NewDirMode); err != nil {
		return err
	}
	return record.Locked(marker, func() error {
		if err := os.Remove(marker); err != nil && !os.IsNotExist(err) {
			return err
		}
		return nil
	})
}

// IndexRunDir publishes one run's durable reference mappings.
// Best-effort by contract: a failure here must not fail the metadata
// write that triggered it.
//
// The recovery is the interesting part. If a PUBLISHED metadata mutation
// could not be indexed, the marker is invalidated so the next miss
// rebuilds history from a full scan — because the alternative is a run
// that is silently unreachable forever, and an expensive correct answer
// beats a cheap wrong one at this seam.
func IndexRunDir(runDir string, meta map[string]any) {
	err := func() error {
		if meta == nil {
			raw, rerr := os.ReadFile(filepath.Join(runDir, "metadata.json"))
			if rerr != nil {
				return rerr
			}
			v, perr := pyval.LoadsOrdered(string(raw))
			if perr != nil {
				return perr
			}
			m, ok := pyval.Plain(v).(map[string]any)
			if !ok {
				return errNotAnObject
			}
			meta = m
		}
		for ref := range metadataRefs(meta) {
			if werr := writeIndexEntry(ref, runDir); werr != nil {
				return werr
			}
		}
		return nil
	}()
	if err != nil {
		_ = InvalidateRunIndex(filepath.Dir(runDir))
	}
}

// RemoveRunIndex drops known mappings for a run being pruned. A mapping
// that has since been claimed by a different run dir is left alone —
// stale hits also self-heal on read, so this only has to be right about
// the entries it actually owns.
func RemoveRunIndex(runDir string, meta map[string]any) {
	root := filepath.Dir(runDir)
	name := filepath.Base(runDir)
	if meta == nil {
		if raw, rerr := os.ReadFile(filepath.Join(runDir, "metadata.json")); rerr == nil {
			if v, perr := pyval.LoadsOrdered(string(raw)); perr == nil {
				meta, _ = pyval.Plain(v).(map[string]any)
			}
		}
	}
	for ref := range metadataRefs(meta) {
		path := indexEntryPath(ref, root)
		_ = record.Locked(path, func() error {
			raw, rerr := os.ReadFile(path)
			if rerr != nil {
				return nil
			}
			v, perr := pyval.LoadsOrdered(string(raw))
			if perr != nil {
				return nil
			}
			cur, ok := pyval.Plain(v).(map[string]any)
			if ok && pyval.StrOrEmpty(cur["run_dir"]) == name {
				_ = os.Remove(path)
			}
			return nil
		})
	}
}

// legacyRunDir is the historical O(all runs) scan: read every run's
// metadata and return the first directory claiming the reference, in
// sorted directory order. This is what the index is an optimization
// over, and what every failure path here falls back to.
func legacyRunDir(ref, runsRoot string) string {
	found := ""
	scanLegacyRunDirs(runsRoot, func(dir string, meta map[string]any) bool {
		if metadataRefs(meta)[ref] {
			found = dir
			return false
		}
		return true
	})
	return found
}

// scanLegacyRunDirs walks the runs root in sorted order, yielding each
// directory that holds a parseable metadata.json. Dot-prefixed entries
// are skipped — the index directory is a sibling of runs/ rather than a
// child, so this is defence in depth rather than the load-bearing
// exclusion, but a workspace with a stray .cache in runs/ should not
// cost a parse error either.
//
// A directory whose metadata is missing or unparseable is SKIPPED, not
// an error: the scan's job is to find a run, and one unreadable
// neighbour must not hide the rest.
func scanLegacyRunDirs(runsRoot string, yield func(dir string, meta map[string]any) bool) {
	entries, err := os.ReadDir(runsRoot)
	if err != nil {
		return
	}
	// os.ReadDir is already sorted by name, which is what Python's
	// sorted(root.iterdir()) amounts to for a single parent: Path
	// ordering compares the full path string, the prefix is shared, and
	// UTF-8 byte order agrees with code-point order.
	for _, e := range entries {
		if !e.IsDir() || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		dir := filepath.Join(runsRoot, e.Name())
		raw, rerr := os.ReadFile(filepath.Join(dir, "metadata.json"))
		if rerr != nil {
			continue
		}
		v, perr := pyval.LoadsOrdered(string(raw))
		if perr != nil {
			continue
		}
		meta, ok := pyval.Plain(v).(map[string]any)
		if !ok {
			continue
		}
		if !yield(dir, meta) {
			return
		}
	}
}

// migrationComplete reads the marker's completeness flag.
//
// The default when the key is ABSENT is true (an old marker predates the
// flag and did complete); the default when the marker cannot be read or
// parsed at all is FALSE. Those are different questions and Python
// answers them differently — `bool(state.get("complete", True))` inside
// a try whose except returns False — so they are kept apart here.
func migrationComplete(marker string) bool {
	raw, err := os.ReadFile(marker)
	if err != nil {
		return false
	}
	v, perr := pyval.LoadsOrdered(string(raw))
	if perr != nil {
		return false
	}
	state, ok := pyval.Plain(v).(map[string]any)
	if !ok {
		return false
	}
	if c, present := state["complete"]; present {
		return pyval.Truthy(c)
	}
	return true
}

// ensureRunIndex performs the one-time migration of an unindexed
// workspace, returning whether it indexed EVERYTHING. An incomplete
// migration is recorded rather than retried on every call: a miss after
// an incomplete migration falls through to the legacy scan, so
// historical reachability is preserved without paying for the whole
// rewrite pass again each lookup.
func ensureRunIndex(runsRoot string) bool {
	marker := filepath.Join(indexDir(runsRoot), indexMarker)
	if isFile(marker) {
		return migrationComplete(marker)
	}
	if err := os.MkdirAll(filepath.Dir(marker), record.NewDirMode); err != nil {
		return false
	}
	complete := false
	lerr := record.Locked(marker, func() error {
		// Re-check under the lock: another process may have migrated
		// while this one waited.
		if isFile(marker) {
			complete = migrationComplete(marker)
			return errAlreadyMigrated
		}
		failed := 0
		scanLegacyRunDirs(runsRoot, func(dir string, meta map[string]any) bool {
			for ref := range metadataRefs(meta) {
				if werr := writeIndexEntry(ref, dir); werr != nil {
					failed++
				}
			}
			return true
		})
		complete = failed == 0
		payload, err := pyjsonMarker(complete, failed)
		if err != nil {
			return err
		}
		return record.AtomicWrite(marker, []byte(payload))
	})
	if lerr != nil && lerr != errAlreadyMigrated {
		return false
	}
	return complete
}

// indexedRunDir resolves one reference through the index, self-healing a
// stale or corrupt leaf.
//
// The repair is deliberately LOCAL: a bad entry unlinks itself, falls
// back to the legacy scan for that one reference, and re-publishes what
// it found. It does NOT invalidate the global migration marker, because
// doing so would force every unrelated miss through a second O(all runs)
// rebuild on account of one damaged file.
func indexedRunDir(ref, runsRoot string) string {
	path := indexEntryPath(ref, runsRoot)
	if !isFile(path) {
		// A missing entry is a plain miss, NOT a corrupt one: it must not
		// unlink anything or trigger the local repair below. Python
		// returns early here for the same reason.
		return ""
	}
	if hit := readIndexEntry(path, ref, runsRoot); hit != "" {
		return hit
	}
	_ = os.Remove(path)
	legacy := legacyRunDir(ref, runsRoot)
	if legacy != "" {
		_ = writeIndexEntry(ref, legacy)
	}
	return legacy
}

// ResolveRunDir locates a run dir by handle id (its directory-name
// prefix) or by a loop id / handle id recorded in metadata.json.
// Returns "" when there is no such run.
//
// Handle ids resolve directly from the deterministic directory name —
// which is the whole reason Nickname had to be ported, since a dir named
// without the nickname is not the name this reconstructs. Other
// references go through the index; the first lookup on an older
// workspace performs one lock-guarded migration, and misses after that
// are bounded and never make an older resumable run unreachable.
func ResolveRunDir(workspaceDir, ref string) string {
	if ref == "" {
		return ""
	}
	if direct := Dir(workspaceDir, ref); isDir(direct) {
		return direct
	}
	root := RunsRoot(workspaceDir)
	if !isDir(root) {
		return ""
	}
	if indexed := indexedRunDir(ref, root); indexed != "" {
		return indexed
	}
	complete := ensureRunIndex(root)
	if indexed := indexedRunDir(ref, root); indexed != "" {
		return indexed
	}
	if complete {
		// A COMPLETE migration indexed every run, so a miss here is a
		// real miss. Returning the scan's answer instead would be a
		// second full pass to re-confirm nothing.
		return ""
	}
	// A best-effort migration recorded failed entries. Preserve
	// historical reachability without repeating the whole rewrite pass
	// on every call.
	return legacyRunDir(ref, root)
}

// readIndexEntry validates one index leaf and returns the run dir it
// names, or "" if the leaf is unusable for ANY reason — unreadable,
// unparseable, not an object, a ref that does not match the one being
// looked up, a run_dir that is not a plain string, a run_dir that is not
// a bare directory name, or a directory that no longer exists.
//
// Collapsing all of those into one "" is deliberate and matches Python,
// where every one of them raises into the same handler: the caller's
// response to each is identical (unlink, rescan, republish), so
// distinguishing them would only invite a caller to treat one specially.
func readIndexEntry(path, ref, runsRoot string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	v, perr := pyval.LoadsOrdered(string(raw))
	if perr != nil {
		return ""
	}
	entry, isObj := pyval.Plain(v).(map[string]any)
	if !isObj {
		return ""
	}
	name, isStr := entry["run_dir"].(string)
	if pyval.StrOrEmpty(entry["ref"]) != ref || !isStr || !isBareName(name) {
		return ""
	}
	candidate := filepath.Join(runsRoot, name)
	if !isDir(candidate) {
		return ""
	}
	return candidate
}

// pyjsonEntry and pyjsonMarker spell the two index files the way Python
// spells them. Neither is compared as text by anything — both are read
// back through a JSON parser — so this is value parity with the bytes
// thrown in for free, not a byte contract worth a differential.
//
// The key ORDER is worth the two lines it costs: the entry uses
// sort_keys=True in Python (ref, run_dir — already alphabetical) and the
// marker uses insertion order (version, complete, failed_entries). A
// diff of a workspace between the two runtimes should show no noise.
func pyjsonEntry(ref, runDirName string) (string, error) {
	return pyjson.Ordered(map[string]any{
		"ref": ref, "run_dir": runDirName,
	}, []string{"ref", "run_dir"})
}

func pyjsonMarker(complete bool, failed int) (string, error) {
	return pyjson.Ordered(map[string]any{
		"version": 1, "complete": complete, "failed_entries": failed,
	}, []string{"version", "complete", "failed_entries"})
}

func isDir(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.IsDir()
}

func isFile(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.Mode().IsRegular()
}

type portErr string

func (e portErr) Error() string { return string(e) }

const (
	errNotAnObject     = portErr("metadata.json is not a JSON object")
	errAlreadyMigrated = portErr("another process completed the migration")
)
