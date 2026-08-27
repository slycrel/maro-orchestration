package orch

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// ListProjects is every directory under <workspace>/projects that has a
// NEXT.md, sorted. The NEXT.md test is what makes a directory a project;
// a bare directory is not one.
//
// Python's projects_root() CREATES the directory as a side effect of
// resolving it, on both of its branches, and this ports that.
//
// The comment that used to stand here said the opposite, on a ground that
// does not hold: "this port keeps path helpers side-effect-free — the
// resolved store being an argument rather than an ambient act is the
// 2026-08-16 live-ledger lesson". That lesson is about WHICH STORE gets
// resolved — not reading ambient env vars to pick one — and it is
// orthogonal to whether resolving creates a directory. EnsureMemoryDir
// takes the workspace as an argument AND mkdirs. Measured:
//
//	fresh workspace   py list_projects() -> []   and projects/ EXISTS after
//	                  go ListProjects()  -> nil  and projects/ does not
//
// A file named `projects` in the way makes CPython raise FileExistsError
// out of list_projects, which is what the error return here reproduces.
func ListProjects(ws string) ([]string, error) {
	root, err := EnsureProjectsRoot(ws)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		// Python's `if not root.exists(): return []` on the line after
		// projects_root() cannot fire — the helper just created it. The
		// port keeps the dead branch for the same reason it keeps the
		// other dead branches in this tree: a port that silently drops a
		// status the original can still emit is the worse bug. It is dead
		// HERE too, and now for the same reason.
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var slugs []string
	for _, e := range entries {
		// pypath.EntryIsDir, not e.IsDir(): orch_items.py:426 asks
		// `p.is_dir()` on an iterdir() entry, and Path.is_dir() FOLLOWS a
		// symlink. A project reached through `projects/linked -> real`
		// is listed by CPython and was silently missing here (r10).
		if !pypath.EntryIsDir(ProjectsRoot(ws), e) {
			continue
		}
		if _, err := os.Stat(NextPath(ws, e.Name())); err == nil {
			slugs = append(slugs, e.Name())
		}
	}
	// `sorted(slugs)` where each slug is `p.name` -- a DIRECTORY NAME, so
	// Python holds it surrogateescape-decoded and sorts by code point.
	//
	// This comment used to read "Python's sorted() on str compares code
	// points; Go's byte-wise compare over UTF-8 gives the same order",
	// which is true for every name that IS UTF-8 and false for the ones
	// that are not: against "e-acute" CPython compares U+DC80 (56448) and
	// Go compares 0x80 (128), and the answers are opposite. A project
	// directory whose name is not valid UTF-8 reorders the whole list, and
	// list_projects feeds the heartbeat's per-project sweep.
	sort.Slice(slugs, func(i, j int) bool { return pypath.FSLess(slugs[i], slugs[j]) })
	return slugs, nil
}

// ProjectPriority reads the PRIORITY file. Missing, empty or unparseable
// all read as 0 — Python catches ValueError and returns 0, so a
// hand-edited "high" does not stop the drain, it just deprioritizes.
//
// NAMED DIVERGENCE: Python's int() also accepts non-ASCII decimal digits
// (any Unicode Nd character). This accepts ASCII digits, a leading sign,
// and PEP-515 underscores between digits. A PRIORITY file written in, say,
// Devanagari digits reads as its value in Python and as 0 here. It costs
// ordering, never data, and no writer in either runtime emits one.
func ProjectPriority(ws, slug string) int {
	raw, err := os.ReadFile(PriorityPath(ws, slug))
	if err != nil {
		return 0
	}
	n, ok := pyInt(pytext.Strip(string(raw)))
	if !ok {
		return 0
	}
	return n
}

// pyInt is Python's int(str) for the ASCII case, underscores included.
func pyInt(s string) (int, bool) {
	if s == "" {
		return 0, false
	}
	neg := false
	if s[0] == '+' || s[0] == '-' {
		neg = s[0] == '-'
		s = s[1:]
	}
	if s == "" {
		return 0, false
	}
	var digits strings.Builder
	prevDigit := false
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '_' {
			// Python allows underscores only BETWEEN digits: not leading,
			// not trailing, not doubled.
			if !prevDigit || i == len(s)-1 {
				return 0, false
			}
			prevDigit = false
			continue
		}
		if c < '0' || c > '9' {
			return 0, false
		}
		digits.WriteByte(c)
		prevDigit = true
	}
	if !prevDigit {
		return 0, false
	}
	var n int
	for _, c := range digits.String() {
		d := int(c - '0')
		// Python's ints are unbounded; Go's are not. An overflowing
		// priority is not a number this runtime can act on, so it reads
		// as unparseable (0) rather than as a wrapped-around one.
		if n > (1<<62)/10 {
			return 0, false
		}
		n = n*10 + d
	}
	if neg {
		n = -n
	}
	return n, true
}

// LifecycleState is Python sheriff.project_lifecycle_state: "failed",
// "paused" or "active", decided by manual marker files. No code path in
// either runtime writes them; an operator touches one to pull a project
// out of rotation.
func LifecycleState(ws, slug string) string {
	dir := ProjectDir(ws, slug)
	if _, err := os.Stat(dir + "/" + failedMarker); err == nil {
		return "failed"
	}
	if _, err := os.Stat(dir + "/" + pausedMarker); err == nil {
		return "paused"
	}
	return "active"
}

// SelectGlobalNext picks the next item to work on across all projects:
// highest priority first, and among equal priorities the project whose
// NEXT.md was touched LONGEST ago. The oldest-first tiebreak is
// anti-starvation — without it a busy project keeps winning and its
// equal-priority neighbours never run.
//
// Failed and paused projects are skipped. Porting selection without that
// skip would have made this runtime drain a project an operator had
// explicitly pulled out of rotation, which is the whole point of the
// marker.
func SelectGlobalNext(ws string) (string, *NextItem, error) {
	slugs, err := ListProjects(ws)
	if err != nil {
		return "", nil, err
	}
	type candidate struct {
		priority int
		mtime    float64
		slug     string
	}
	var cands []candidate
	for _, slug := range slugs {
		st, err := os.Stat(NextPath(ws, slug))
		if err != nil {
			continue // Python catches FileNotFoundError and skips
		}
		if s := LifecycleState(ws, slug); s == "failed" || s == "paused" {
			continue
		}
		cands = append(cands, candidate{
			priority: ProjectPriority(ws, slug),
			// Python's st_mtime is seconds as a double, and this compares
			// the same value rather than nanoseconds so that two files
			// Python considers simultaneous stay simultaneous here and
			// fall through to the same tiebreak.
			mtime: float64(st.ModTime().Unix()) +
				float64(st.ModTime().Nanosecond())*1e-9,
			slug: slug,
		})
	}
	// Python sorts by (priority, -mtime) with reverse=True. Its sort is
	// stable and the slug is NOT in the key, so a full tie falls back to
	// the ascending-slug order list_projects produced. SliceStable over
	// an already-sorted slice reproduces that exactly.
	sort.SliceStable(cands, func(i, j int) bool {
		if cands[i].priority != cands[j].priority {
			return cands[i].priority > cands[j].priority
		}
		return cands[i].mtime < cands[j].mtime
	})
	for _, c := range cands {
		it, err := SelectNextItem(ws, c.slug)
		if err != nil {
			return "", nil, err
		}
		if it != nil {
			return c.slug, it, nil
		}
	}
	return "", nil, nil
}

// ListBlockedProjects is every project carrying at least one blocked
// item, worst first: highest priority, then most blocked, then slug
// descending (Python sorts the whole 3-tuple with reverse=True).
func ListBlockedProjects(ws string) ([]Status, error) {
	slugs, err := ListProjects(ws)
	if err != nil {
		return nil, err
	}
	var out []Status
	for _, slug := range slugs {
		st, err := ProjectStatus(ws, slug)
		if err != nil {
			continue // Python skips projects whose NEXT.md went missing
		}
		if st.Blocked > 0 {
			out = append(out, st)
		}
	}
	// orch_items.py:779 is
	// `sorted(out, key=lambda s: (s.priority, s.blocked, s.slug), reverse=True)`.
	// The slug is IN the key, so the tiebreak is a real Python string
	// comparison over surrogateescape-decoded code points -- and a bare `>`
	// on a Go string is raw bytes. ListProjects hands this function a
	// correctly FSLess-ordered slug list and this sort threw that order
	// away again, one call apart: the "fixed the site that had the fixture"
	// shape at the smallest possible distance. Measured with projects
	// a\x80z and a-e-acute-z at equal priority and equal blocked count, so
	// the slug decides: CPython returns \x80 first, Go returned e-acute
	// first (adversarial r8, MEDIUM).
	//
	// Note for the guard: this site is invisible to every arm of the fssort
	// guards. The listing happens in ListProjects and the sort here, so the
	// "lists AND sorts" predicate never fires, and no arm reads comparator
	// BODIES. A bare `<`/`>` on a name inside a comparator is a fifth
	// spelling of this class. It is not guarded by an AST arm because a
	// census found 32 such comparators tree-wide and all but these are
	// numeric -- 30 allowlist rows about float comparisons would be a
	// guard nobody reads. Recorded here instead of pretended away.
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Priority != out[j].Priority {
			return out[i].Priority > out[j].Priority
		}
		if out[i].Blocked != out[j].Blocked {
			return out[i].Blocked > out[j].Blocked
		}
		return pypath.FSLess(out[j].Slug, out[i].Slug) // reverse=True
	})
	return out, nil
}

// AppendSectionLines appends a timestamped block to one of the project's
// narrative files, creating it with its heading on first write.
//
// dedupeToken, when set, is checked INSIDE the lock: a caller-side
// pre-check alone is a TOCTOU, since two finalizers can both observe
// absence and then serialize only their appends (Python's adversarial
// review, 2026-08-10). Returns false when the token was already present
// and nothing was written.
//
// The whole seed-and-append is one critical section for a second reason:
// a multi-line block larger than PIPE_BUF is not an atomic append, so two
// concurrent loops on the same project could interleave their lines.
func AppendSectionLines(path, heading string, lines []string, dedupeToken string) (bool, error) {
	stamp := NowUTCISO()
	payload := []string{"", "## " + stamp}
	for _, ln := range lines {
		payload = append(payload, "- "+ln)
	}
	wrote := false
	err := record.Locked(path, func() error {
		if dedupeToken != "" {
			existing, err := os.ReadFile(path)
			if err == nil && strings.Contains(string(existing), dedupeToken) {
				return nil
			}
		}
		if err := os.MkdirAll(dirOf(path), record.NewDirMode); err != nil {
			return err
		}
		if _, err := os.Stat(path); os.IsNotExist(err) {
			if err := os.WriteFile(path, []byte(heading+"\n\n"), 0o666); err != nil {
				return err
			}
		}
		f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
		if err != nil {
			return err
		}
		defer f.Close()
		if _, err := f.WriteString(strings.Join(payload, "\n") + "\n"); err != nil {
			return err
		}
		wrote = true
		return nil
	})
	return wrote, err
}

func dirOf(path string) string {
	if i := strings.LastIndexByte(path, '/'); i >= 0 {
		return path[:i]
	}
	return "."
}

func AppendDecision(ws, slug string, lines []string) error {
	_, err := AppendSectionLines(DecisionsPath(ws, slug), "# DECISIONS", lines, "")
	return err
}

func AppendRisk(ws, slug string, lines []string, dedupeToken string) (bool, error) {
	return AppendSectionLines(RisksPath(ws, slug), "# RISKS", lines, dedupeToken)
}

func AppendProvenance(ws, slug string, lines []string) error {
	_, err := AppendSectionLines(ProvenancePath(ws, slug), "# PROVENANCE", lines, "")
	return err
}

// nextTemplate is the NEXT.md a new project starts with, byte for byte as
// Python writes it — including the em dash, which loop/project.go's
// mission fallback and every Python parser of this file already expect.
func nextTemplate(slug, mission string) string {
	return "# NEXT — " + slug + "\n\n" +
		"Mission:\n\n" +
		"> " + mission + "\n\n" +
		"## Checklist\n\n" +
		"- [ ] Define success criteria\n" +
		"- [ ] Create first-pass plan\n" +
		"- [ ] Execute next leaf task\n"
}

// EnsureProject creates a project directory and its starting ledger, and
// is idempotent: an existing NEXT.md or DECISIONS.md is left alone, so
// calling it again on a live project cannot reset its plan.
//
// RISKS.md and PROVENANCE.md are deliberately NOT pre-created. A
// "(fill in)" stub minted here outlives any run that has nothing to
// record, and because the stub counts as a file modified during the run,
// curation once served it as a run deliverable (Python 8b8671bd,
// 2026-08-06). They are lazy-created with their heading on first real
// write.
//
// PRIORITY is rewritten unconditionally — that is Python's behavior, and
// it is what lets a caller re-prioritize an existing project.
func EnsureProject(ws, slug, mission string, priority int) (string, error) {
	pdir := ProjectDir(ws, slug)
	if err := os.MkdirAll(pdir, record.NewDirMode); err != nil {
		return "", err
	}
	if _, err := os.Stat(NextPath(ws, slug)); os.IsNotExist(err) {
		if err := record.AtomicWrite(NextPath(ws, slug),
			[]byte(nextTemplate(slug, mission))); err != nil {
			return "", err
		}
	}
	if _, err := os.Stat(DecisionsPath(ws, slug)); os.IsNotExist(err) {
		if err := record.AtomicWrite(DecisionsPath(ws, slug),
			[]byte("# DECISIONS\n\n")); err != nil {
			return "", err
		}
		if err := AppendDecision(ws, slug, []string{
			"Project created.", "Mission: " + mission}); err != nil {
			return "", err
		}
	}
	if err := record.AtomicWrite(PriorityPath(ws, slug),
		[]byte(fmt.Sprintf("%d\n", priority))); err != nil {
		return "", err
	}
	return pdir, nil
}
