// Package recall is the loop's memory seam — recall.py's v1 slice.
// Read-only by contract, stronger than Python's: even the times_applied
// receipt write-back that Python's lesson render performs is unported
// here (named below), so this seam genuinely writes nothing. Every
// failure degrades to "knows nothing" and is named in Sources rather
// than swallowed silently.
//
// Ported in this slice:
//   - ranked tiered-lesson retrieval (knowledge.QueryLessonsScored —
//     agenda-typed query first, untyped top-up, dedup by lesson_id) and
//     the "## Lessons from Prior Runs" render with certainty receipts
//     (Jeremy decree 2026-07-29: entries cite the evidence behind them
//     or read as what they are — a single observation);
//   - prior-attempt scanning over runs/<dir>/metadata.json (exact /
//     near / project match, SF-2 verdict-preferred semantics, §13b
//     external-interrupt exclusion) and the as_context_block summary
//     paragraph.
//
// Named-unported substrates (recall.py carries eight; see PORT.md):
// standing rules, decision journal, graveyard resurrection, failure
// notes, learning activity, playbook, knowledge nodes, thread identity
// (origin walk + ancestry.json), prior-decision briefs (run_card
// curation), project artifacts, dispatch_signals, portability
// re-weighting (§14a), camera frames, age stamps, the legacy flat-store
// top-up + fallback injector, magic-prefix stripping at the match
// boundary, and the times_applied write-back. The Go runtime also does
// not WRITE runs/<dir>/metadata.json yet, so prior attempts surface
// only in a workspace shared with the Python runtime — a pure-Go
// workspace honestly degrades to zero priors.
package recall

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
)

// MetadataScanCap is recall.py's _METADATA_SCAN_CAP: newest-first bound
// on run-dir METADATA READS per call. Stated honestly (adversarial
// recall r1 2026-08-22, Architect): the cap bounds only the second
// phase — the listing+stat phase before it scales with LIFETIME run
// count in both runtimes (Python iterdir+stat has the identical
// shape), so a workspace accumulating tens of thousands of runs pays
// a growing per-run listing cost. Inherited limitation, named in
// PORT.md; bounding the first phase needs an index both runtimes
// agree on.
const MetadataScanCap = 200

// NearMatchThreshold is recall.py's _NEAR_MATCH_THRESHOLD for the
// word-overlap similarity between a prior run's prompt and the goal.
const NearMatchThreshold = 0.9

// InterruptStatuses ports stop_verdicts.INTERRUPT_STATUSES: process-
// level endings that carry no goal evidence (§13b) — a run cut down
// around its goal must not read as a failed attempt at it.
var InterruptStatuses = map[string]bool{
	"interrupted": true, "stranded": true,
	"refused_busy": true, "clarification_needed": true,
}

// PriorAttempt mirrors recall.PriorAttempt. GoalAchieved is tri-state
// (SF-2): nil = unjudged — done ≠ achieved.
type PriorAttempt struct {
	Goal         string
	HandleID     string
	Status       string // done | stuck | error | unknown (never finalized)
	When         string // started_at, ISO-8601
	Match        string // "exact" | "near" | "project"
	GoalAchieved *bool
	StopVerdict  string // "external-interrupt" = not goal evidence
}

// Result is the v1 slice of recall.RecallResult.
type Result struct {
	Lessons       string
	PriorAttempts []PriorAttempt
	// Sources instruments the read (Python's RECALL_PERFORMED context);
	// degradations land here as error_* entries instead of vanishing.
	Sources map[string]any
}

// Recall is the seam. Read-only; every failure degrades to "knows
// nothing". project enables the project-family match lane on prior
// attempts (the Go loop passes "" until it threads a project concept
// pre-decompose — named in PORT.md).
//
// The recover below is the Go stand-in for Python recall()'s
// per-substrate `except Exception` blankets: this seam consumes
// hand-editable, possibly corrupted on-disk data, and its contract is
// that a broken store never blocks a run — an UNANTICIPATED bug here
// must degrade to "knows nothing" with the panic named in Sources,
// not crash the orchestrator (adversarial recall r1 2026-08-22,
// Expert QA: the doctrine held only for anticipated failures).
func Recall(workspaceDir, goal, project string) (res Result) {
	return RecallExcluding(workspaceDir, goal, project, "")
}

// RecallExcluding is Recall with the caller's OWN run excluded from the
// prior-attempt scan (Python find_prior_attempts exclude_handle_id):
// a loop that creates its run dir before recalling would otherwise
// read its own seconds-old metadata as an exact-match prior attempt
// and warn about repeating itself (closure tranche 2026-08-22 — the
// Go loop now writes run metadata, so the hazard is live).
func RecallExcluding(workspaceDir, goal, project, excludeHandleID string) (res Result) {
	t0 := time.Now()
	sources := map[string]any{"slice": "loop"}
	res = Result{Sources: sources}
	defer func() {
		if r := recover(); r != nil {
			// Value AND stack: the guard exists for UNANTICIPATED bugs,
			// and "index out of range [3]" without a trace is
			// un-actionable across ~900 lines of corrupted-data
			// handling (adversarial recall r2 2026-08-22, Expert QA).
			// Bounded under the PanicTrace budget — the string rides a
			// captain's-log event row.
			// The value is clipped FIRST so a runaway payload-carrying panic
			// value cannot crowd the stack — the actionable half — out of
			// the overall PanicTrace budget (adversarial recall r3
			// 2026-08-22: value-first ordering reproduced the bare-value
			// problem at a higher threshold).
			sources["error_recall_panic"] = budget.PanicTrace.Clip(
				budget.PanicValue.Clip(fmt.Sprintf("%v", r)) +
					"\n" + string(debug.Stack()))
			sources["elapsed_ms"] = time.Since(t0).Milliseconds()
			// Partial substrate results assembled before the panic
			// stay on res — same degradation direction as Python,
			// where a later substrate's failure keeps earlier ones.
		}
	}()

	prior, priorSkipped, scanErr := FindPriorAttempts(workspaceDir, goal, 24.0, project, excludeHandleID)
	if scanErr != nil {
		sources["error_prior_attempts"] = scanErr.Error()
	}
	res.PriorAttempts = prior
	sources["prior_attempts"] = len(prior)
	if priorSkipped > 0 {
		// A short scan must be distinguishable from a short history —
		// the same doctrine the tiered loader's skipped count carries
		// (adversarial recall r2 2026-08-22, Expert QA: every malformed
		// metadata continue vanished untraced while the package doc
		// promised degradations are named).
		sources["prior_attempts_skipped"] = priorSkipped
	}

	if panicHook != nil {
		panicHook()
	}

	res.Lessons = lessonsBlock(workspaceDir, goal, sources)

	sources["elapsed_ms"] = time.Since(t0).Milliseconds()
	return res
}

// panicHook is a test seam: the recover contract ("an unanticipated
// panic degrades to knows-nothing with partial results kept") needs an
// executing test, and no honest fixture can force a panic through the
// guarded parse paths (adversarial recall r2 2026-08-22, Skeptic — an
// instrument nothing proves can fire is untrusted). Nil in production;
// the test points it at panic() between the substrates.
// Unsynchronized by design: tests in this package must stay serial (no
// t.Parallel()) while they use this seam, and Recall's one production
// caller is the loop's single-threaded pre-decompose site.
var panicHook func()

// lessonsBlock runs the ranked selection (agenda-typed first, untyped
// top-up to 3, dedup by lesson_id — recall.py's chunk-6 rewire) and the
// receipts render. Selection windows match Python: score the top 10,
// select the first 3.
func lessonsBlock(workspaceDir, goal string, sources map[string]any) string {
	store := knowledge.NewStore(workspaceDir)
	scored, skipped, loadErrs := store.QueryLessonsScored(goal, 10, "agenda")
	lessons := make([]knowledge.TieredLesson, 0, 3)
	for _, sl := range scored {
		if len(lessons) >= 3 {
			break
		}
		lessons = append(lessons, sl.Lesson)
	}
	if len(lessons) < 3 {
		// Untyped tiered writers (evolver, verify-learn, prereq) TOP UP —
		// an existing agenda match must not mask them. Dedup by
		// lesson_id: the untyped query is a superset of the agenda one.
		have := map[string]bool{}
		for _, l := range lessons {
			have[l.LessonID] = true
		}
		untyped, uSkipped, uErrs := store.QueryLessonsScored(goal, 10, "")
		skipped += uSkipped
		loadErrs = append(loadErrs, uErrs...)
		for _, sl := range untyped[:min(len(untyped), 3)] {
			if len(lessons) >= 3 {
				break
			}
			if have[sl.Lesson.LessonID] {
				continue
			}
			lessons = append(lessons, sl.Lesson)
			have[sl.Lesson.LessonID] = true
		}
	}
	if skipped > 0 {
		sources["lessons_skipped_rows"] = skipped
	}
	if len(loadErrs) > 0 {
		sources["error_lessons"] = strings.Join(loadErrs, "; ")
	}
	if len(lessons) == 0 {
		return ""
	}

	// Render with certainty receipts. A lesson is CITED only if its line
	// is actually rendered (chunk-6 review: truncate-after-the-fact
	// dropped trailing lines while their ids stayed cited) — the budget
	// is a line breaker, never a mid-line truncator (caps decree
	// 2026-08-21).
	header := "## Lessons from Prior Runs (weigh by their receipts)"
	lines := []string{header}
	used := len([]rune(header))
	var cited []string
	for _, l := range lessons {
		icon := "✗"
		if l.Outcome == "done" {
			icon = "✓"
		}
		var rparts []string
		if l.TimesReinforced > 0 {
			rparts = append(rparts, fmt.Sprintf("reinforced %dx", l.TimesReinforced))
		}
		if l.SessionsValidated > 0 {
			rparts = append(rparts, fmt.Sprintf("%d sessions", l.SessionsValidated))
		}
		if l.TimesApplied > 0 {
			rparts = append(rparts, fmt.Sprintf("applied %dx", l.TimesApplied))
		}
		receipt := " (observed once)"
		if len(rparts) > 0 {
			receipt = " (" + strings.Join(rparts, ", ") + ")"
		}
		line := "- " + icon + " " + l.Lesson + receipt
		if used+1+len([]rune(line)) > budget.LessonInject.Limit {
			break
		}
		lines = append(lines, line)
		used += 1 + len([]rune(line))
		if l.LessonID != "" {
			cited = append(cited, l.LessonID)
		}
	}
	if len(lines) <= 1 {
		return ""
	}
	if len(cited) > 0 {
		sources["lesson_ids_cited"] = cited
	}
	return strings.Join(lines, "\n")
}

// FindPriorAttempts ports recall.find_prior_attempts: scan recent run
// dirs (mtime-ordered, capped) for goal matches. The magic-prefix strip
// Python applies at the matching boundary is unported (the Go CLI has
// no prefix grammar); a Python run recorded under a prefixed prompt may
// therefore miss the near-match here — named divergence.
//
// skipped counts run dirs whose metadata was unreadable, unparseable,
// or type-drifted (no readable prompt / started_at) — a short scan must
// be distinguishable from a short history, the same doctrine as the
// tiered loader's skipped count. Python's sibling drops these silently;
// the counter is a Go-side addition, not a divergence in what loads
// (adversarial recall r2 2026-08-22, Expert QA). Dirs that read fine
// but simply don't match, or fall outside the window, are NOT skips.
func FindPriorAttempts(workspaceDir, goal string, windowHours float64, project, excludeHandleID string) (attempts []PriorAttempt, skipped int, err error) {
	root := filepath.Join(workspaceDir, "runs")
	entries, err := os.ReadDir(root)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, 0, nil
		}
		return nil, 0, err
	}
	type dirM struct {
		name  string
		mtime time.Time
	}
	var dirs []dirM
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		info, ierr := e.Info()
		if ierr != nil {
			continue
		}
		dirs = append(dirs, dirM{name: e.Name(), mtime: info.ModTime()})
	}
	sort.SliceStable(dirs, func(i, j int) bool {
		return dirs[i].mtime.After(dirs[j].mtime)
	})
	if len(dirs) > MetadataScanCap {
		dirs = dirs[:MetadataScanCap]
	}

	cutoff := time.Now().UTC().Add(-time.Duration(windowHours * float64(time.Hour)))
	goalNorm := normalize(goal)
	for _, d := range dirs {
		meta := readRunMetadata(filepath.Join(root, d.name, "metadata.json"))
		if meta == nil {
			skipped++
			continue
		}
		handleID, _ := meta["handle_id"].(string)
		if handleID == "" {
			handleID = strings.SplitN(d.name, "-", 2)[0]
		}
		if excludeHandleID != "" && handleID == excludeHandleID {
			continue
		}
		started, _ := meta["started_at"].(string)
		when, perr := parseISO(started)
		if perr != nil {
			skipped++ // unreadable timestamp = broken row, not old row
			continue
		}
		if when.Before(cutoff) {
			continue
		}
		prompt, _ := meta["prompt"].(string)
		if prompt == "" {
			skipped++ // a run with no readable prompt cannot match anything
			continue
		}
		var match string
		metaProject, _ := meta["project"].(string)
		switch {
		case normalize(prompt) == goalNorm:
			match = "exact"
		case textSimilarity(prompt, goal) >= NearMatchThreshold:
			match = "near"
		case project != "" && metaProject == project:
			// A caller-selected persistent project is a stronger, cheaper
			// family key than guessing semantic similarity from a rephrase.
			match = "project"
		default:
			continue
		}
		// Third reader of goal_achieved (siblings: inspector.goalAchieved,
		// evolver.triState). A present-but-non-bool value is judged-NOT-
		// achieved, NOT unjudged — the same conservative direction the r1/
		// r2 hardening chose, so a corrupt prior-attempt row surfaces as a
		// failure to the director instead of a neutral no-verdict (r3
		// review sibling-census).
		var ga *bool
		if v, present := meta["goal_achieved"]; present && v != nil {
			b, isBool := v.(bool)
			if !isBool {
				b = false // malformed verdict → judged-false
			}
			ga = &b
		}
		sv, _ := meta["stop_verdict"].(string)
		status, _ := meta["status"].(string)
		if status == "" {
			status = "unknown"
		}
		if sv == "" && InterruptStatuses[status] {
			// Status-derived fallback for runs that predate break-site
			// stamping (mirrors run_curation.classify_outcome).
			sv = "external-interrupt"
		}
		attempts = append(attempts, PriorAttempt{
			Goal: prompt, HandleID: handleID, Status: status,
			When: started, Match: match, GoalAchieved: ga, StopVerdict: sv,
		})
	}
	// Lexical sort on the RAW started_at string — byte-for-byte
	// Python's `attempts.sort(key=lambda a: a.when, reverse=True)`.
	// Known-preserved wrinkle (adversarial recall r1, Architect):
	// heterogeneous timestamp shapes (space vs T separator) would
	// misorder; both runtimes share it, and fixing it is a
	// cross-runtime change, not a Go patch.
	sort.SliceStable(attempts, func(i, j int) bool {
		return attempts[i].When > attempts[j].When
	})
	return attempts, skipped, nil
}

func readRunMetadata(path string) map[string]any {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil {
		return nil
	}
	return m
}

// parseISO accepts the ISO-8601 shapes Python datetime.fromisoformat
// does for our writers; a naive timestamp is assumed UTC (Python parity).
func parseISO(s string) (time.Time, error) {
	for _, layout := range []string{
		time.RFC3339Nano, time.RFC3339,
		"2006-01-02T15:04:05.999999999", "2006-01-02T15:04:05",
		"2006-01-02 15:04:05", "2006-01-02",
	} {
		if t, err := time.ParseInLocation(layout, s, time.UTC); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("unparseable timestamp %q", s)
}

func normalize(text string) string {
	return strings.Join(strings.Fields(strings.ToLower(text)), " ")
}

var similarityStripRe = regexp.MustCompile(`[^a-z0-9 ]`)

// textSimilarity ports memory_ledger._text_similarity: word-overlap
// Jaccard over lowercased alphanumeric-and-space text.
func textSimilarity(a, b string) float64 {
	wordSet := func(s string) map[string]bool {
		out := map[string]bool{}
		for _, w := range strings.Fields(similarityStripRe.ReplaceAllString(strings.ToLower(s), "")) {
			out[w] = true
		}
		return out
	}
	wa, wb := wordSet(a), wordSet(b)
	if len(wa) == 0 || len(wb) == 0 {
		return 0.0
	}
	inter := 0
	for w := range wa {
		if wb[w] {
			inter++
		}
	}
	union := len(wa) + len(wb) - inter
	if union == 0 {
		return 0.0
	}
	return float64(inter) / float64(union)
}

// ContextBlock ports the prior-attempt portion of
// RecallResult.as_context_block: one injectable paragraph for ancestry
// context, empty when nothing is known. The thread/artifact/decision
// substrates of the Python block are unported (see package doc), so
// this block is attempts-only in v1.
func (r Result) ContextBlock() string {
	if len(r.PriorAttempts) == 0 {
		return ""
	}
	byStatus := map[string]int{}
	for _, a := range r.PriorAttempts {
		byStatus[a.Status]++
	}
	statuses := make([]string, 0, len(byStatus))
	for s := range byStatus {
		statuses = append(statuses, s)
	}
	sort.Strings(statuses)
	parts := make([]string, 0, len(statuses))
	for _, s := range statuses {
		parts = append(parts, fmt.Sprintf("%d %s", byStatus[s], s))
	}
	breakdown := strings.Join(parts, ", ")
	nTrue, nFalse, nInt := 0, 0, 0
	for _, a := range r.PriorAttempts {
		if a.GoalAchieved != nil {
			if *a.GoalAchieved {
				nTrue++
			} else {
				nFalse++
			}
		}
		if a.StopVerdict == "external-interrupt" || InterruptStatuses[a.Status] {
			nInt++
		}
	}
	if nTrue > 0 || nFalse > 0 {
		breakdown += fmt.Sprintf("; goal verdicts: %d achieved, %d NOT achieved, rest unjudged", nTrue, nFalse)
	}
	if nInt > 0 {
		breakdown += fmt.Sprintf("; %d externally interrupted (not goal evidence)", nInt)
	}
	text := "== Recall (what the system already knows) ==\n" + fmt.Sprintf(
		"Prior attempts at this goal (recent window): %d runs — %s. "+
			"Newest: %s (%s). "+
			"Do not repeat an approach that already failed; if every "+
			"prior attempt failed the same way, change the approach or "+
			"surface the blocker instead of retrying.",
		len(r.PriorAttempts), breakdown,
		r.PriorAttempts[0].When, r.PriorAttempts[0].Status)
	limit := budget.RecallContext.Limit
	if len([]rune(text)) <= limit {
		return text
	}
	if limit <= 128 {
		// Degenerate budget: no room for an announced cut — the bound
		// wins. Ported WITH Python's 2026-08-14 fixpoint guard, not
		// just the subtraction: limit-64 at or below zero would hit
		// budget.Clip's breaker-off path and return the WHOLE text
		// unbounded (adversarial recall r1 2026-08-22, Expert QA —
		// unreachable at today's 4000 constant, reintroduced-landmine
		// class).
		r := []rune(text)
		if limit < 0 {
			limit = 0
		}
		return string(r[:limit])
	}
	// Reserve room for clip's marker so the return honors the bound
	// (Python as_context_block's max_chars - 64).
	return budget.Clip(text, limit-64)
}

// DecomposeExtras assembles the context blocks the initial decompose
// prompt receives, in Python's extras order (ancestry before lessons —
// planner.py:962). Empty blocks are dropped, matching the `if x` filter.
func (r Result) DecomposeExtras() []string {
	var out []string
	if cb := r.ContextBlock(); cb != "" {
		out = append(out, cb)
	}
	if r.Lessons != "" {
		out = append(out, r.Lessons)
	}
	return out
}
