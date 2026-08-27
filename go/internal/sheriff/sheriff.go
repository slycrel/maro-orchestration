// Package sheriff ports the deterministic half of Python sheriff.py: the
// two report shapes and their renderers, the per-project health check, the
// no-progress detector, the state fingerprint, and the lifecycle markers.
//
// It is the PRODUCER of what internal/heartbeat consumes — `checks` and
// `stuck_projects` both come from here — so the two packages share a seam
// that is now ported at both ends rather than one.
//
// WHAT IS NOT HERE, NAMED:
//
//   - `check_system_health`'s five probes. Four of them measure the
//     ENVIRONMENT (a socket to 127.0.0.1:18789, `shutil.disk_usage("/")`,
//     an `__import__("requests")`, `llm.detect_backends()`), which is not
//     a function of its arguments and cannot be compared across runtimes
//     without reimplementing the environment too. The FIFTH thing that
//     function does — rolling the checks up into a status — is pure, and
//     is here as RollupStatus, because that is the value heartbeat branches
//     on.
//   - `check_all_projects` — a directory sweep plus a call to CheckProject
//     per slug. Lands with the projects tranche, which owns the sweep.
//   - `archive_dormant_projects` — it MOVES directories; it belongs with
//     the sweep and wants its own differential.
//   - `write_heartbeat_state` / `read_heartbeat_state` — one JSON file with
//     a nested project list. Portable, but its shape is decided by
//     check_all_projects, so it lands with that.
//   - `main` — the CLI.
package sheriff

import (
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// StuckRepetitionThreshold and DecisionWindow are sheriff.py's constants.
// Both are read by more than one function and the threshold does double
// duty — it is the repeat count in CheckProject AND the window length in
// DetectNoProgress, which is why it is one constant and not two.
const (
	StuckRepetitionThreshold = 3
	DecisionWindow           = 20
	DormantDaysDefault       = 14.0
)

const (
	failedMarker = ".maro-failed"
	pausedMarker = ".maro-paused"
)

// ---------------------------------------------------------------------------
// The two report shapes
// ---------------------------------------------------------------------------

// Report is sheriff.SheriffReport.
//
// RecommendedAction is a string and not *string even though Python's is
// `Optional[str]`, because the two spellings are distinguishable in exactly
// one place and it is not the one a Go pointer would help with: the TEXT
// renderer tests `if self.recommended_action:` — TRUTHINESS — so None and
// "" both print no line (measured). The JSON renderer does tell them apart,
// writing `null` versus `""`, and HasAction carries that.
type Report struct {
	Project   string
	Status    string // healthy|warning|stuck|dormant|failed|paused|unknown
	Diagnosis string
	Evidence  []string
	Action    string
	HasAction bool // false spells JSON `null`; true with Action=="" spells `""`
	CheckedAt string
}

// WithAction returns a copy carrying an action, including an EMPTY one —
// which is a real state: `recommended_action=""` renders as `""` in JSON
// and prints no action line in text.
func (r Report) WithAction(a string) Report {
	r.Action, r.HasAction = a, true
	return r
}

// Format is SheriffReport.format(mode).
//
// The mode test is `if mode == "json"`, so EVERY other value — including a
// typo, including "" — falls through to the text renderer. Measured: a mode
// of "zzz" produces the text form, not an error and not an empty string. A
// Go enum with a default-case error would be a third behaviour.
func (r Report) Format(mode string) (string, error) {
	if mode == "json" {
		o := pyval.Obj{}
		o.Set("project", r.Project)
		o.Set("status", r.Status)
		o.Set("diagnosis", r.Diagnosis)
		o.Set("evidence", strList(r.Evidence))
		if r.HasAction {
			o.Set("recommended_action", r.Action)
		} else {
			o.Set("recommended_action", nil)
		}
		o.Set("checked_at", r.CheckedAt)
		// indent=2 with ensure_ascii ON, which is json.dumps' default —
		// so a non-ASCII diagnosis is written é here and raw in the
		// TEXT form one branch up. Both spellings, one object.
		return pyval.DumpsIndent2(o)
	}
	lines := []string{
		"project=" + r.Project,
		"status=" + r.Status,
		"diagnosis=" + r.Diagnosis,
	}
	for _, e := range r.Evidence {
		lines = append(lines, "  evidence: "+e)
	}
	// TRUTHINESS, so an empty action prints nothing. A `!= ""` happens to
	// be the same test for a string; it is written this way because the
	// Python is truthiness and the next field to grow here might not be.
	if r.Action != "" {
		lines = append(lines, "action: "+r.Action)
	}
	return strings.Join(lines, "\n"), nil
}

// Health is sheriff.SystemHealth.
//
// Checks is an ordered map for the same reason heartbeat's is: its order is
// the order the text renderer prints, and check_system_health builds it in
// a fixed sequence. A Go map would reorder an operator-facing readout on
// every run.
type Health struct {
	Status    string // healthy|degraded|critical
	Checks    pyval.Obj
	CheckedAt string
}

// Format is SystemHealth.format(mode). Same mode rule as Report.Format:
// anything but "json" is text.
func (h Health) Format(mode string) (string, error) {
	if mode == "json" {
		o := pyval.Obj{}
		o.Set("status", h.Status)
		o.Set("checks", h.Checks)
		o.Set("checked_at", h.CheckedAt)
		return pyval.DumpsIndent2(o)
	}
	lines := []string{"health=" + h.Status, "checked_at=" + h.CheckedAt}
	for _, f := range h.Checks {
		lines = append(lines, "  "+f.Key+": "+pyval.Str(f.Val))
	}
	return strings.Join(lines, "\n"), nil
}

// RollupStatus is the last eight lines of check_system_health: the rule that
// turns a checks map into the word heartbeat branches on.
//
// It is lifted out on purpose. The four probes above it in Python measure
// the environment — a socket, the disk, an import, the backend lane — and
// none of them is a function of its arguments, so a differential over
// `check_system_health` would be comparing two machines rather than two
// implementations. This IS a function of its arguments, it is the only part
// heartbeat's behaviour depends on, and it has three subtleties worth
// pinning:
//
//   - The tests are `startswith`, so "failure" is a fail and "warning" is a
//     warn — and "FAIL" is NEITHER, because they are case-sensitive.
//   - A check whose value starts with neither word counts as neither. An
//     all-unknown checks map is HEALTHY.
//   - An EMPTY checks map is healthy too, which is how a probe that
//     crashed before writing anything reports perfect health. That is the
//     behaviour; it is also worth knowing.
func RollupStatus(checks pyval.Obj) string {
	warn := false
	for _, f := range checks {
		s := pyval.Str(f.Val)
		if strings.HasPrefix(s, "fail") {
			// Python collects BOTH lists before testing, so a fail
			// anywhere wins over a warn anywhere — order-independent, and
			// an early return here is the same answer.
			return "critical"
		}
		if strings.HasPrefix(s, "warn") {
			warn = true
		}
	}
	if warn {
		return "degraded"
	}
	return "healthy"
}

// ---------------------------------------------------------------------------
// No-progress detection
// ---------------------------------------------------------------------------

// DetectNoProgress is sheriff.detect_no_progress.
//
// `len(set(recent)) == 1 and recent[0] != ""` over the LAST THREE
// fingerprints. Three things follow, all measured:
//
//   - Fewer than three fingerprints is always False, whatever they are.
//   - The window is exactly the last three: [A,A,A,B,C,D] is False.
//   - Three EMPTY strings are False. An empty fingerprint is what
//     FingerprintProjectState returns when it could not read anything, so
//     "we failed to look three times" must not read as "nothing changed".
//     Only the FIRST element is tested for emptiness, which is enough
//     because all three are equal by then.
func DetectNoProgress(fingerprints []string) bool {
	if len(fingerprints) < StuckRepetitionThreshold {
		return false
	}
	recent := fingerprints[len(fingerprints)-StuckRepetitionThreshold:]
	for _, f := range recent[1:] {
		if f != recent[0] {
			return false
		}
	}
	return recent[0] != ""
}

// FingerprintProjectState is sheriff.fingerprint_project_state: md5 of
// NEXT.md plus the TAIL of DECISIONS.md, joined with a newline.
//
// Two decisions that a port gets wrong by writing the obvious thing:
//
//   - `text[-2000:]` is CODE POINTS, not bytes. A DECISIONS.md of 2500
//     accented characters is 5000 bytes, so a byte slice starts in a
//     different place and hashes differently. clipTail below is the
//     rune-aware half; pyval.Clip is the HEAD slice and is not it.
//   - The parts are only APPENDED WHEN THE FILE EXISTS, and then joined.
//     So "neither file" hashes the empty string, while "two empty files"
//     hashes a single newline — two states a port that always joined two
//     strings would collapse into one. Measured:
//     d41d8cd9... versus 68b329da...
//
// Every failure answers "" — Python's bare `except Exception` — and "" is
// the value DetectNoProgress refuses to call stuck. That pairing is the
// design: an unreadable project is not a stuck one.
func FingerprintProjectState(ws, slug string) string {
	dir := orch.ProjectDir(ws, slug)
	var parts []string
	for i, name := range []string{"NEXT.md", "DECISIONS.md"} {
		path := filepath.Join(dir, name)
		// `if p.exists(): parts.append(p.read_text(...))` is TWO failure
		// lanes with two different answers, and collapsing them into one
		// `os.ReadFile` err != nil is the port's easy mistake:
		//
		//   stat fails            -> the part is SKIPPED and the hash goes on
		//   stat ok, read raises  -> the bare `except` returns ""
		//
		// A DECISIONS.md that is a directory takes the second lane
		// (EISDIR on read, ENOENT never), so it must fingerprint as
		// unreadable rather than as absent. The two answers differ:
		// "" is what DetectNoProgress refuses to call stuck, while a
		// skipped part still produces a real, comparable hash.
		if _, serr := os.Stat(path); serr != nil {
			continue
		}
		b, rerr := os.ReadFile(path)
		if rerr != nil {
			return ""
		}
		text, derr := pyval.DecodeUTF8Strict(b)
		if derr != nil {
			return "" // read_text(encoding="utf-8") raises; the catch eats it
		}
		if i == 1 {
			text = clipTail(text, 2000)
		}
		parts = append(parts, text)
	}
	sum := md5.Sum([]byte(strings.Join(parts, "\n")))
	return hex.EncodeToString(sum[:])
}

// clipTail is Python's `s[-n:]` in CODE POINTS: the last n runes, or the
// whole string when it is shorter. pyval.Clip is the head half of this and
// deliberately not reused — a tail is not a head, and spelling it as
// `Clip(reverse(s), n)` would be worse than four lines.
func clipTail(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[len(runes)-n:])
}

// ---------------------------------------------------------------------------
// Lifecycle markers
// ---------------------------------------------------------------------------

// ProjectLifecycleState is sheriff.project_lifecycle_state: "failed",
// "paused" or "active", from two marker files an OPERATOR creates by hand.
// Nothing in the tree writes them, which is the point — they are the manual
// door out of sheriff/backlog-drain/heartbeat rotation.
//
// The test is `.exists()`, so a marker that is a DIRECTORY counts (measured).
// Failed is checked first and wins when both are present.
func ProjectLifecycleState(ws, slug string) string {
	dir := orch.ProjectDir(ws, slug)
	if _, err := os.Stat(filepath.Join(dir, failedMarker)); err == nil {
		return "failed"
	}
	if _, err := os.Stat(filepath.Join(dir, pausedMarker)); err == nil {
		return "paused"
	}
	return "active"
}

// ProjectActivityAgeDays is sheriff.project_activity_age_days: days since
// the newest mtime among the project dir, NEXT.md, DECISIONS.md, and
// artifacts/ (the directory plus its first fifty entries by name).
//
// Returns ok=false for Python's None — an absent project, or one where
// nothing could be statted at all.
//
// `sorted(artifacts.iterdir())[:50]` sorts the FULL PATHS and then takes
// fifty, so which fifty depends on the sort and not on readdir order. Go's
// filepath.Glob returns sorted names, which is the same order for entries
// sharing one parent.
func ProjectActivityAgeDays(ws, slug string, now time.Time) (float64, bool) {
	dir := orch.ProjectDir(ws, slug)
	if _, err := os.Stat(dir); err != nil {
		return 0, false
	}
	candidates := []string{dir,
		filepath.Join(dir, "NEXT.md"), filepath.Join(dir, "DECISIONS.md")}
	artifacts := filepath.Join(dir, "artifacts")
	if _, err := os.Stat(artifacts); err == nil {
		candidates = append(candidates, artifacts)
		entries, err := os.ReadDir(artifacts)
		if err == nil {
			// os.ReadDir sorts by filename; Python sorts by full Path,
			// which under one parent is the same order.
			var names []string
			for _, e := range entries {
				names = append(names, filepath.Join(artifacts, e.Name()))
			}
			// `sorted(artifacts.iterdir())[:50]` orders Path objects -- by
			// the surrogateescape decoding, not by raw byte -- and then
			// TRUNCATES. That makes this the W24 shape rather than the W23
			// one: two orderings do not merely name the same files
			// differently, they name DIFFERENT FILES, and `newest` over
			// that set is what decides the dormancy verdict.
			sort.Slice(names, func(i, j int) bool { return pypath.FSLess(names[i], names[j]) })
			if len(names) > 50 {
				names = names[:50]
			}
			candidates = append(candidates, names...)
		}
	}
	var newest float64
	for _, p := range candidates {
		st, err := os.Stat(p)
		if err != nil {
			continue // Python's `except OSError: continue`
		}
		if m := pySeconds(st.ModTime()); m > newest {
			newest = m
		}
	}
	// `if newest <= 0: return None` — an epoch-zero mtime is indistinguishable
	// from "nothing could be statted" to this function, so a project whose
	// files all sit at 1970 reports UNKNOWN age and is never dormant. That is
	// the behaviour, not a rounding of it: a `< 0` here would call such a
	// project 20,000 days idle and archive it.
	if newest <= 0 {
		return 0, false
	}
	return (pySeconds(now) - newest) / 86400.0, true
}

// pySeconds is CPython's `st_mtime` / `time.time()`: seconds as a float,
// built from the whole-second and nanosecond parts separately, which is
// what CPython's `sec + 1e-9*nsec` does.
//
// Written this way rather than `float64(t.UnixNano()) / 1e9` because the
// two are different expressions and round differently. MEASURED, not
// assumed — the first draft of this comment claimed the difference could
// flip an exactly-fourteen-day dormancy comparison, and that is FALSE:
//
//	1754794000.000000000  identical (whole seconds are exact either way)
//	1756003600.123456789  identical
//	1700000000.987654321  ...209 (split) vs ...448 (UnixNano)
//
// So the disagreement is confined to sub-second mtimes and is ~1e-12 days,
// far below anything `%.0f` or the day threshold can see. This is a
// faithfulness choice, not a live bug, and the battery's M46b is a
// labelled near-equivalent for exactly that reason. It stays because the
// expression is the original's and the next caller may compare at finer
// resolution than a day.
func pySeconds(t time.Time) float64 {
	return float64(t.Unix()) + float64(t.Nanosecond())/1e9
}

// ---------------------------------------------------------------------------
// The per-project check
// ---------------------------------------------------------------------------

// CheckProject is sheriff.check_project.
//
// Six things this function does that a reasonable port does differently,
// every one of them measured against CPython before a line was written:
//
//  1. **The evidence strings embed Python REPRs.** The doing-items line is
//     `f"...: {[i.text for i in doing_items[:3]]}"`, which is a LIST repr
//     inside an f-string: `['doing one']`, brackets and quotes included, a
//     name with an apostrophe flipping the whole list to double quotes, and
//     non-ASCII staying literal (`['café → naïve']`). The repeated-log line
//     is `{text[:60]!r}` — a sixty-CODE-POINT slice, then repr.
//  2. **The truncation is at three, after the filter.** Five doing items
//     report "5 item(s)" and list three.
//  3. **A missing NEXT.md does not take the missing-directory branch.** The
//     directory exists, `parse_next` raises, and the blanket `except`
//     catches it — so the diagnosis is `Sheriff check failed: project X has
//     no NEXT.md`, not `Project directory does not exist`. Both are
//     status=unknown and they are NOT interchangeable strings.
//  4. **The problem list is checked by PRECEDENCE, not by insertion.** A
//     project with both `items_stuck_doing` and `no_artifacts` is STUCK,
//     not warning — the first branch tests two membership conditions and
//     wins. Recording an extra problem never downgrades the status.
//  5. **Blocked items are evidence and never a problem.** They add a line
//     and cannot change the status.
//  6. **The dormancy check runs BEFORE any of it** and returns early, so a
//     dormant project never reports stuck however bad its NEXT.md looks.
//
// `now` is the clock, seamed so the artifact-age line is reproducible.
func CheckProject(ws, slug string, windowMinutes int, now time.Time, dormantDays float64) Report {
	at := pyval.NowISO(now.UTC())
	dir := orch.ProjectDir(ws, slug)
	if _, err := os.Stat(dir); err != nil {
		return Report{Project: slug, Status: "unknown",
			Diagnosis: "Project directory does not exist",
			Evidence:  []string{}, CheckedAt: at}
	}
	switch ProjectLifecycleState(ws, slug) {
	case "failed":
		return Report{Project: slug, Status: "failed",
			Diagnosis: "Marked failed (" + failedMarker + ")",
			Evidence:  []string{}, CheckedAt: at}
	case "paused":
		return Report{Project: slug, Status: "paused",
			Diagnosis: "Marked paused (" + pausedMarker + ")",
			Evidence:  []string{}, CheckedAt: at}
	}
	if dormantDays > 0 {
		if age, ok := ProjectActivityAgeDays(ws, slug, now); ok && age > dormantDays {
			return Report{Project: slug, Status: "dormant",
				Diagnosis: fmt.Sprintf(
					"No file activity in %sd (>%sd) — excluded from diagnosis/escalation",
					pyval.PercentF(age, 0), pyval.FormatG(dormantDays)),
				Evidence: []string{}, CheckedAt: at}.WithAction(
				"Archive with `maro sheriff archive --apply`, " +
					"or touch a project file to reactivate")
		}
	}

	var evidence []string
	problems := map[string]bool{}

	_, items, err := orch.ParseNext(ws, slug)
	if err != nil {
		// The blanket `except Exception` around the whole body. Its message
		// is `f"Sheriff check failed: {exc}"`, so the EXCEPTION'S OWN TEXT
		// is part of the readout and this port has to carry it verbatim.
		return Report{Project: slug, Status: "unknown",
			Diagnosis: "Sheriff check failed: " + err.Error(),
			Evidence:  []string{}, CheckedAt: at}
	}
	var doing, blocked, todo []string
	for _, it := range items {
		switch it.State {
		case orch.StateDoing:
			doing = append(doing, it.Text)
		case orch.StateBlocked:
			blocked = append(blocked, it.Text)
		case orch.StateTodo:
			todo = append(todo, it.Text)
		}
	}
	if len(doing) > 0 {
		evidence = append(evidence, fmt.Sprintf(
			"%d item(s) stuck in 'doing' state: %s",
			len(doing), pyval.ReprStrings(firstN(doing, 3))))
		problems["items_stuck_doing"] = true
	}
	if len(blocked) > 0 {
		evidence = append(evidence, fmt.Sprintf("%d blocked item(s): %s",
			len(blocked), pyval.ReprStrings(firstN(blocked, 3))))
	}
	if len(todo) == 0 && len(doing) == 0 {
		evidence = append(evidence, "No TODO items remaining — project may be complete")
	}

	decisionsPath := filepath.Join(dir, "DECISIONS.md")
	if _, serr := os.Stat(decisionsPath); serr == nil {
		// Inside the try, so unlike the fingerprint's silent "" a failed
		// read here surfaces as status=unknown carrying the exception's
		// own text. Only the stat lane is a skip.
		b, rerr := os.ReadFile(decisionsPath)
		if rerr != nil {
			return Report{Project: slug, Status: "unknown",
				Diagnosis: "Sheriff check failed: " + rerr.Error(),
				Evidence:  []string{}, CheckedAt: at}
		}
		content, derr := pyval.DecodeUTF8Strict(b)
		if derr != nil {
			return Report{Project: slug, Status: "unknown",
				Diagnosis: "Sheriff check failed: " + derr.Error(),
				Evidence:  []string{}, CheckedAt: at}
		}
		// Blank lines are dropped BEFORE the window, so the window is the
		// last twenty non-blank lines and not the non-blank subset of the
		// last twenty. splitlines(), not split("\n"): a trailing newline
		// does not produce a final empty element, and \r and \x0b are line
		// breaks to Python.
		var nonBlank []string
		for _, l := range pytext.SplitLines(content) {
			if strings.TrimSpace(l) != "" {
				nonBlank = append(nonBlank, l)
			}
		}
		recent := nonBlank
		if len(recent) > DecisionWindow {
			recent = recent[len(recent)-DecisionWindow:]
		}
		// Counter over the STRIPPED lines, and Counter.items() walks in
		// FIRST-INSERTION order — so `repeated[0]` is the earliest-seen
		// repeated text, not the most frequent one. An ordered count is
		// the only shape that gets that right.
		order := []string{}
		counts := map[string]int{}
		for _, l := range recent {
			s := strings.TrimSpace(l)
			if s == "" {
				continue
			}
			if _, seen := counts[s]; !seen {
				order = append(order, s)
			}
			counts[s]++
		}
		var repeated []string
		for _, s := range order {
			if counts[s] >= StuckRepetitionThreshold {
				repeated = append(repeated, s)
			}
		}
		if len(repeated) > 0 {
			evidence = append(evidence, fmt.Sprintf(
				"Repeated log entries (%d patterns): %s x%d",
				len(repeated), pyval.Repr(pyval.Clip(repeated[0], 60)),
				counts[repeated[0]]))
			problems["repeated_decisions"] = true
		}
	}

	artifactsDir := filepath.Join(dir, "artifacts")
	if _, serr := os.Stat(artifactsDir); serr == nil {
		files, gerr := readdirOrder(artifactsDir)
		if gerr == nil && len(files) > 0 {
			// `sorted(glob("*"), key=mtime, reverse=True)` — Python's sort
			// is STABLE and reverse=True does NOT reverse ties (measured:
			// keys [1,1,2,1] over a,b,c,d give c,a,b,d), so among equal
			// mtimes the FIRST by INPUT order wins. SliceStable with a
			// strict `>` is that; sort.Slice is not.
			//
			// And the input order is the FILESYSTEM's, not the name order —
			// which is why this reads the directory raw instead of using
			// filepath.Glob. pathlib's glob walks os.scandir and yields
			// what readdir yields; Go's Glob sorts. On ext4 here the two
			// disagree for as little as two files (readdir gives
			// bbb.txt before aaa.txt, by name hash), and the C30 fixture
			// caught exactly that. Matching readdir is also what makes the
			// differential STABLE rather than lucky: both runtimes then ask
			// the same filesystem the same question, so a box whose /tmp is
			// tmpfs (insertion order) still sees the two agree.
			type ent struct {
				path string
				mt   time.Time
			}
			var es []ent
			for _, p := range files {
				st, err := os.Stat(p)
				if err != nil {
					// Python's key= raises here and the whole call dies in
					// the blanket except. A file deleted between the glob
					// and the stat is the reachable case.
					return Report{Project: slug, Status: "unknown",
						Diagnosis: "Sheriff check failed: " + err.Error(),
						Evidence:  []string{}, CheckedAt: at}
				}
				es = append(es, ent{p, st.ModTime()})
			}
			sort.SliceStable(es, func(i, j int) bool {
				return es[i].mt.After(es[j].mt)
			})
			newest := es[0]
			ageMin := (pySeconds(now) - pySeconds(newest.mt)) / 60
			evidence = append(evidence, fmt.Sprintf("Newest artifact: %s (%smin ago)",
				filepath.Base(newest.path), pyval.PercentF(ageMin, 0)))
			if ageMin > float64(windowMinutes) && len(doing) > 0 {
				problems["artifact_stale"] = true
				evidence = append(evidence, fmt.Sprintf(
					"Artifact is >%dmin old with items in progress — potential stall",
					windowMinutes))
			}
		} else if gerr == nil && len(doing) > 0 {
			evidence = append(evidence, "No artifacts produced despite items in progress")
			problems["no_artifacts"] = true
		}
	}

	if len(evidence) == 0 {
		evidence = []string{}
	}
	if len(problems) == 0 {
		diagnosis := fmt.Sprintf("Project healthy: %d todo, %d doing",
			len(todo), len(doing))
		if len(todo) == 0 && len(doing) == 0 {
			diagnosis = "Project appears complete (no remaining TODO items)"
		}
		// Both arms are status=healthy — the `status = "healthy"` inside
		// each branch is the same assignment twice in Python, and writing
		// it once here is the only place this port collapses anything.
		return Report{Project: slug, Status: "healthy", Diagnosis: diagnosis,
			Evidence: evidence, CheckedAt: at}
	}
	// PRECEDENCE, not insertion order: a project carrying both
	// items_stuck_doing and no_artifacts is STUCK. Recording an extra
	// problem can never downgrade the status.
	switch {
	case problems["repeated_decisions"] || problems["items_stuck_doing"]:
		return Report{Project: slug, Status: "stuck",
			Diagnosis: "Loop detected: repeated decisions or items stuck in doing state",
			Evidence:  evidence, CheckedAt: at}.WithAction(
			"Force-complete or skip stuck items: orch done " + slug)
	case problems["artifact_stale"] || problems["no_artifacts"]:
		return Report{Project: slug, Status: "warning",
			Diagnosis: "Potential stall: items in progress but no recent artifact activity",
			Evidence:  evidence, CheckedAt: at}.WithAction(
			"Check execution bridge or re-run tick")
	}
	// UNREACHABLE from this function: every problem it records is one of
	// the four above, so the else arm cannot be taken. It is kept because
	// the Python has it and a future problem name would land here rather
	// than silently reporting healthy — which is what deleting it would do.
	return Report{Project: slug, Status: "warning",
		Diagnosis: "Anomalies detected; manual review recommended",
		Evidence:  evidence, CheckedAt: at}.WithAction(
		"Review DECISIONS.md and NEXT.md")
}

// ResolveDormantDays is `_dormant_days`: the threshold CheckProject's
// dormancy branch compares against, or 0 to disable it.
//
// `float(get("sheriff.dormant_days", DORMANT_DAYS_DEFAULT) or 0)` inside a
// blanket try is THREE lanes with three different answers, and the middle
// one is the trap a port collapses:
//
//	unset / usable value  -> that value            (14.0 when unset)
//	FALSY value           -> 0.0, the check OFF
//	unparseable value     -> 14.0, the check ON at the default
//
// So `sheriff.dormant_days: 0`, `: ""` and `: null` all turn dormancy off,
// while `: "abc"` turns it on at fourteen days. A port that answers "the
// default" for both of the last two silently re-enables a check the
// operator switched off. Measured, every lane: 7 -> 7, "  7  " -> 7,
// "1e3" -> 1000, 0/""/None/False/[] -> 0, True -> 1, -3 -> -3,
// "abc"/[1] -> 14.
//
// cfg is a THUNK, as heartbeat's cadence resolvers are, and it stands for
// the whole `from config import get; get(key, default)` that Python does
// inside the try: a nil thunk or an error is the except arm, and a thunk
// that finds nothing returns DormantDaysDefault itself, because that is the
// argument Python passes to get.
func ResolveDormantDays(cfg func() (any, error)) float64 {
	if cfg == nil {
		return DormantDaysDefault
	}
	v, err := cfg()
	if err != nil {
		return DormantDaysDefault
	}
	if !pyval.Truthy(v) {
		// The `or 0`, which runs BEFORE float() and cannot raise.
		return 0
	}
	f, ok := pyval.Float(v)
	if !ok {
		return DormantDaysDefault
	}
	return f
}

// readdirOrder is pathlib's `glob("*")`: every entry, dotfiles included,
// in the order readdir returns them. Explicitly NOT sorted — see the note
// at its call site.
func readdirOrder(dir string) ([]string, error) {
	f, err := os.Open(dir)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	names, err := f.Readdirnames(-1)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(names))
	for _, n := range names {
		out = append(out, filepath.Join(dir, n))
	}
	return out, nil
}

func firstN(ss []string, n int) []string {
	if len(ss) > n {
		return ss[:n]
	}
	return ss
}

func strList(ss []string) pyval.List {
	out := pyval.List{}
	for _, s := range ss {
		out = append(out, s)
	}
	return out
}
