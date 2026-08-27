// slice2.go — sheriff.py's enumeration, archival and heartbeat-state half.
//
// Slice 1 (sheriff.go) is the per-project check and the formatting. This
// file is `check_all_projects` (:334), `archive_dormant_projects` (:357),
// `write_heartbeat_state` (:534) and `read_heartbeat_state` (:556).
//
// `check_system_health` (:439) is deliberately NOT here, and the boundary
// is not arbitrary: three of its five checks are a stat and a socket and
// port directly, but check 2 is `__import__("requests")` — a question
// about the PYTHON environment, which only a Python subprocess can answer
// honestly — and check 4 needs `llm.detect_backends()`, which this tree
// does not have. Porting detect_backends drags in the credentials-env
// reader, `_get_key`, the backend-order config walk and two binary
// probes; that is an `internal/llm` tranche, not a sheriff one. Emitting
// four of five checks is not an option: the rollup is `startswith("warn")`
// over the map, so a MISSING check turns a degraded box healthy — the
// fail-open direction. See scratchpad/sheriff_slice2_design.md.
package sheriff

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// CheckAllProjects is sheriff.check_all_projects.
//
// Four things worth stating, all of them observable:
//
//  1. **The blanket `except` wraps the WHOLE body, comprehension
//     included.** An exception raised while checking slug #4 discards
//     slugs 1-3 and returns a ONE-element list whose project is "*". The
//     port must not accumulate-and-return-partial. In practice CheckProject
//     answers with a Report rather than failing — every error path inside
//     it already returns status=unknown — so the reachable case is the
//     enumeration itself, and that is what this reproduces.
//  2. **A missing projects dir returns an EMPTY list, not the "*" report.**
//     Absent and broken are different answers and a caller can tell.
//  3. **`sorted(slugs)` sorts str**, which on Linux is the
//     surrogateescape decoding compared by code point, not raw bytes —
//     `pypath.FSLess`, not `sort.Strings`. A directory whose name is not
//     valid UTF-8 orders differently under the two rules and this is the
//     function's whole return value. Same class as ProjectActivityAgeDays'
//     truncated artifact list, and it is the class guard in
//     internal/pypath that keeps it closed.
//  4. **`d.is_dir()` FOLLOWS symlinks and swallows OSError** (pathlib
//     catches OSError and answers False), so a symlink to a directory IS a
//     project and a dangling one is not. `os.Stat`, not `os.Lstat`, and a
//     stat error is a skip rather than a failure.
//
// NAMED residual, the same one `dispatch/envelope.go`'s `oserr` carries:
// the enumeration failure interpolates `str(exc)`, and Python spells an
// OSError `[Errno 13] Permission denied: '<path>'` where Go spells it
// `open <path>: permission denied`. The verdict, the project name and the
// prefix all match; the errno text does not.
func CheckAllProjects(ws string, windowMinutes int, now time.Time, dormantDays float64) []Report {
	dir := orch.ProjectsRoot(ws)
	if _, err := os.Stat(dir); err != nil {
		// `if not projects_dir.exists(): return []`. Note this is inside
		// the try, so a stat that RAISES would take the except arm in
		// Python — but Path.exists() swallows OSError and answers False,
		// so the two agree and the empty list is right either way.
		return []Report{}
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return []Report{{
			Project:   "*",
			Status:    "unknown",
			Diagnosis: "Could not enumerate projects: " + err.Error(),
			Evidence:  []string{},
			// SheriffReport's checked_at is a default_factory, so the "*"
			// report carries a timestamp too — it is not the zero value.
			CheckedAt: pyval.NowISO(now.UTC()),
		}}
	}
	var slugs []string
	for _, e := range entries {
		name := e.Name()
		// Python tests is_dir() FIRST and the prefix second. The order is
		// not observable here (is_dir cannot raise out of pathlib), but
		// the transcription keeps it.
		st, serr := os.Stat(filepath.Join(dir, name))
		if serr != nil || !st.IsDir() {
			continue
		}
		// `d.name.startswith((".", "_"))` — one call, a TUPLE of prefixes.
		// `projects/_archive/` is the archive sweep's own target and must
		// never be enumerated as a project.
		if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") {
			continue
		}
		slugs = append(slugs, name)
	}
	sort.Slice(slugs, func(i, j int) bool { return pypath.FSLess(slugs[i], slugs[j]) })
	out := make([]Report, 0, len(slugs))
	for _, slug := range slugs {
		out = append(out, CheckProject(ws, slug, windowMinutes, now, dormantDays))
	}
	return out
}

// ArchiveDormantProjects is sheriff.archive_dormant_projects.
//
// It is the manual hygiene op behind `maro sheriff archive`, dry-run by
// default. Its Python docstring says it is never called from an automated
// path "so an off switch stays off", and porting it does not change that:
// NOTHING in this tree may call it from a scheduled path.
//
// **There is no try/except in the Python.** Every other function in this
// module swallows; this one lets an OSError out, so the Go returns an
// error rather than a report. A port that wrapped it in the module's house
// style would turn a failed move into a silent success.
//
// The details that decide the answer:
//
//   - `age is None or age <= days` — LESS THAN OR EQUAL. A project exactly
//     at the threshold is NOT archived.
//   - `round(age, 1)` is Python's round, which is half-to-EVEN applied to
//     the binary double, not the decimal literal: round(0.35, 1) is 0.3,
//     not 0.4. `pyval.Round` is that; `math.Round` is not.
//   - `sorted(projects_dir.iterdir())` sorts PATH objects, which compare
//     by the whole path string — same parent, so it reduces to name order
//     under the same surrogateescape rule CheckAllProjects uses.
//   - The entry's key order is project, age_days, moved, and then target
//     ONLY when the move happened. The absence of `target` is observable,
//     which is why this returns pyval.Obj rather than a struct with an
//     omitempty tag.
//   - `archive_root.mkdir(exist_ok=True)` takes mode 0o777, so the mode on
//     disk is `0o777 &^ umask` — 0o775 on this box. A hard-coded 0o755
//     would create a group-read-only directory where Python creates a
//     group-writable one.
//   - `mkdir` with exist_ok but WITHOUT parents: a missing parent still
//     raises. os.Mkdir, not os.MkdirAll.
func ArchiveDormantProjects(ws string, days float64, apply bool, now time.Time) ([]pyval.Obj, error) {
	out := []pyval.Obj{}
	dir := orch.ProjectsRoot(ws)
	if _, err := os.Stat(dir); err != nil {
		return out, nil
	}
	archiveRoot := filepath.Join(dir, "_archive")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Slice(names, func(i, j int) bool { return pypath.FSLess(names[i], names[j]) })

	for _, name := range names {
		src := filepath.Join(dir, name)
		st, serr := os.Stat(src)
		if serr != nil || !st.IsDir() ||
			strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") {
			continue
		}
		age, ok := ProjectActivityAgeDays(ws, name, now)
		if !ok || age <= days {
			continue
		}
		entry := pyval.Obj{}
		entry.Set("project", name)
		entry.Set("age_days", pyval.Round(age, 1))
		entry.Set("moved", false)
		if apply {
			// exist_ok=True, no parents. os.Mkdir returns EEXIST, which is
			// exactly what exist_ok swallows — and ONLY that one.
			if merr := os.Mkdir(archiveRoot, 0o777); merr != nil && !os.IsExist(merr) {
				return nil, merr
			}
			target := filepath.Join(archiveRoot, name)
			if _, terr := os.Stat(target); terr == nil {
				// `int(time.time())` truncates the float toward zero.
				// Two projects colliding inside the same second get the
				// same suffix and the second one overwrites; that is the
				// Python's behaviour and the port keeps it rather than
				// improving on it.
				target = filepath.Join(archiveRoot,
					fmt.Sprintf("%s-%d", name, int64(pySeconds(now))))
			}
			if merr := pyMove(src, target); merr != nil {
				return nil, merr
			}
			entry.Set("moved", true)
			entry.Set("target", target)
		}
		out = append(out, entry)
	}
	return out, nil
}

// pyMove is `shutil.move(src, dst)` for the one shape this module calls it
// with: src is a directory that exists, dst is a path under _archive.
//
// A bare os.Rename gets two things wrong, and the first one is REACHABLE:
//
//  1. **The is-dir-destination special case.** shutil checks
//     `os.path.isdir(dst)` and, if so, moves the source INSIDE it —
//     `dst/basename(src)` — raising shutil.Error if THAT already exists.
//     The caller's `target.exists()` guard means this is normally not
//     reached, but the check and the move are not atomic: a concurrent
//     mkdir of the target between them lands the project one level deeper
//     with no error at all. That is the Python's behaviour and this
//     reproduces it rather than improving on it.
//  2. **The rename fallback.** CPython catches `OSError` — not EXDEV, ANY
//     OSError — and for a directory source falls through to
//     copytree(symlinks=True) + rmtree. NAMED GAP: this port returns the
//     error instead. `projects/` and `projects/_archive/` are on the same
//     device in every install, which is the only common way that branch is
//     reached, and an untested copytree is worse than a named gap. A
//     caller seeing a rename error where CPython would have copied is
//     getting a refusal, not a wrong answer — the fail-closed direction.
//
// The `_samefile(src, dst) and not islink(src)` sub-branch is reproduced
// too: it exists for case-insensitive filesystems, and on this one it can
// only fire if dst IS src, where the rename is a no-op either way.
func pyMove(src, dst string) error {
	realDst := dst
	if st, err := os.Stat(dst); err == nil && st.IsDir() {
		if same, serr := sameFile(src, dst); serr == nil && same {
			if lst, lerr := os.Lstat(src); lerr == nil && lst.Mode()&os.ModeSymlink == 0 {
				return os.Rename(src, dst)
			}
		}
		// _basename strips a trailing separator first, so a src spelled
		// "a/b/" still contributes "b" rather than "".
		realDst = filepath.Join(dst, pyBasename(src))
		if _, err := os.Stat(realDst); err == nil {
			// shutil.Error's exact text, which is what a caller printing
			// str(exc) would show.
			return fmt.Errorf("Destination path '%s' already exists", realDst)
		}
	}
	return os.Rename(src, realDst)
}

// pyBasename is shutil._basename: os.path.basename AFTER stripping every
// trailing separator, so a directory path spelled with a slash still has a
// last component.
func pyBasename(p string) string {
	trimmed := strings.TrimRight(p, string(os.PathSeparator))
	if trimmed == "" {
		// All separators: rstrip leaves "" and basename("") is "".
		return ""
	}
	return filepath.Base(trimmed)
}

// sameFile is os.path.samefile: same device and inode.
func sameFile(a, b string) (bool, error) {
	sa, err := os.Stat(a)
	if err != nil {
		return false, err
	}
	sb, err := os.Stat(b)
	if err != nil {
		return false, err
	}
	return os.SameFile(sa, sb), nil
}

// HeartbeatStateName is the file both runtimes read and write.
const HeartbeatStateName = "heartbeat-state.json"

// WriteHeartbeatState is sheriff.write_heartbeat_state.
//
// It returns the path on success and "" where Python returns None — the
// blanket `except Exception: return None` covers the whole body, including
// the serialisation, so a caller cannot distinguish "could not import" from
// "could not write". Both runtimes give back one bit.
//
// `json.dumps(payload, indent=2)` is three decisions in one call and
// `pyval.DumpsIndent2` is all three: the item separator LOSES its trailing
// space under indent, `ensure_ascii` escapes every non-ASCII rune, and `<`
// and `&` stay raw where encoding/json escapes them. The key order is
// checked_at, system_status, checks, stuck_projects, and `checks` keeps
// its own insertion order — which is why Health.Checks is a pyval.Obj and
// not a map.
//
// `write_text` is NOT atomic: a failure partway leaves a truncated file
// AND returns None, so a reader can find a half-written JSON document that
// no writer will admit to. Reproduced deliberately — os.WriteFile, not the
// port's atomic writer — because a heartbeat reader that starts tolerating
// truncation on one runtime and not the other is a divergence in the
// consumer, not the producer.
func WriteHeartbeatState(ws string, h Health, projectReports []Report) string {
	payload := pyval.Obj{}
	payload.Set("checked_at", h.CheckedAt)
	payload.Set("system_status", h.Status)
	payload.Set("checks", h.Checks)
	// `if project_reports:` is a TRUTHINESS test, so nil and an EMPTY
	// slice take the same branch — and both render as `[]`, never `null`.
	//
	// EQUIVALENT MUTANT, recorded rather than pinned (L8): `var stuck
	// pyval.List` renders identically, because pyval's renderer switches on
	// the TYPE and a nil List still matches `case List` with len 0. The
	// literal stays because the property it protects is one writer away —
	// encoding/json spells a nil slice `null` — and the next person to
	// swap the emitter should not have to rediscover that.
	stuck := pyval.List{}
	for _, r := range projectReports {
		if r.Status == "stuck" || r.Status == "warning" {
			stuck = append(stuck, r.Project)
		}
	}
	payload.Set("stuck_projects", stuck)

	text, err := pyval.DumpsIndent2(payload)
	if err != nil {
		return ""
	}
	dir := orch.MemoryDir(ws)
	path := filepath.Join(dir, HeartbeatStateName)
	// `memory_dir()` on the Python side CREATES the directory; write_text
	// does not. Mirrored through the shared helper rather than re-spelled,
	// so this inherits its mode rule instead of picking one.
	if _, derr := orch.EnsureMemoryDir(ws); derr != nil {
		return ""
	}
	if werr := os.WriteFile(path, []byte(text), 0o644); werr != nil {
		return ""
	}
	return path
}

// ReadHeartbeatState is sheriff.read_heartbeat_state.
//
// It answers absent for FOUR different failures — no file, an unreadable
// file, invalid JSON, and a valid document whose top level is not an
// object — and the last one is a lie the Python tells rather than a
// behaviour the port invents. `json.loads("[]")` returns a LIST, the
// annotation says Optional[Dict], and the function returns the list
// anyway. Go cannot return a list from an object-shaped signature, so this
// port answers absent there.
//
// NAMED DIVERGENCE, and it is the one place in this file the two runtimes
// give different answers on the same bytes: a `heartbeat-state.json`
// holding `[]` reads back as an empty list in Python and as absent here.
// Nothing in either tree writes that file as an array, and the Python
// caller that would receive the list does `state.get(...)` on it, which
// raises — so the Python "answer" is a crash one line later and the port's
// is a miss. Choosing the miss is deliberate; inventing a list-shaped
// return so both can crash identically is not worth a second return type.
func ReadHeartbeatState(ws string) (pyval.Obj, bool) {
	path := filepath.Join(orch.MemoryDir(ws), HeartbeatStateName)
	if _, err := os.Stat(path); err != nil {
		return nil, false
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, false
	}
	v, err := pyval.LoadsOrdered(string(raw))
	if err != nil {
		return nil, false
	}
	o, ok := v.(pyval.Obj)
	if !ok {
		return nil, false
	}
	return o, true
}
