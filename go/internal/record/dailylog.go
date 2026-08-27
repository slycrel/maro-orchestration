package record

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// The human-readable half of the outcome ledger — Python memory_ledger's
// _append_daily_log and _update_memory_index, both called by record_outcome.
//
// WHY THIS EXISTS (adversarial r5, MEDIUM): the Go port wrote
// outcomes.jsonl and nothing else, so a Go run left NO trace in the two
// surfaces a person actually opens. The asymmetry is what makes it worth
// porting rather than shrugging at: MEMORY.md is a full REWRITE from the
// ledger, so the next Python run self-heals it and a Go-only gap there is
// temporary — but the daily log is APPEND-PER-OUTCOME, so a run that never
// appended is absent from that day forever. Nothing rebuilds it. Every Go
// run was a permanent hole in the day's record.
//
// Both files are shared with the Python runtime, so the spellings below are
// measured against CPython on this box, not inferred.

// dailyLogSummaryCut mirrors _DAILY_LOG_SUMMARY_CUT.
const dailyLogSummaryCut = 400

// dailyLogSummary is the one-glance version of a stored summary, honest
// when it trims.
//
// The cut is in CODE POINTS, not bytes — Python's summary[:400] slices a
// str — and the reported total is a code-point count too. Slicing the Go
// string by bytes would both cut at the wrong place and split a rune in
// half, which lands as U+FFFD in a file Python reads back.
func dailyLogSummary(summary string) string {
	s := pytext.Strip(summary)
	runes := []rune(s)
	if len(runes) <= dailyLogSummaryCut {
		return s
	}
	return fmt.Sprintf("%s… [%d chars total; full text in outcomes.jsonl]",
		string(runes[:dailyLogSummaryCut]), len(runes))
}

// appendDailyLog appends one human-readable entry to today's daily log.
//
// recordedAt is the row's own stamp rather than a second clock read: the
// heading must name the same instant the ledger row does.
//
// TWO CLOCKS, AND THAT IS PYTHON'S BUG FAITHFULLY PORTED. The FILE is named
// from date.today() — the machine's LOCAL date — while the HEADING inside
// it is recorded_at[:10], which is UTC. West of UTC after 17:00 local, and
// east of it before 09:00, every entry lands in a file named for a
// different day than the entry says. Matching it is the whole point: a
// runtime that "fixed" this would write a run into a file the other
// runtime's reader is not looking at, and the reader (_update_memory_index
// globbing ????-??-??.md) keys on the FILENAME.
func appendDailyLog(dir string, o Outcome, taskType, recordedAt string) error {
	name := time.Now().Format("2006-01-02") + ".md"
	path := filepath.Join(dir, name)

	// Prefer the goal verdict over process status: done-but-goal-not-achieved
	// renders as a failure, not a success (SF-2). nil is "not judged", which
	// is not False, so it keeps the tick.
	icon := "✗"
	if o.Status == "done" && (o.GoalAchieved == nil || *o.GoalAchieved) {
		icon = "✓"
	}
	statusStr := o.Status
	if o.GoalAchieved != nil {
		if *o.GoalAchieved {
			statusStr += " (goal achieved)"
		} else {
			statusStr += " (goal NOT achieved)"
		}
	}

	day := recordedAt
	if r := []rune(day); len(r) > 10 {
		day = string(r[:10])
	}
	goal := o.Goal
	if r := []rune(goal); len(r) > 80 {
		goal = string(r[:80])
	}

	var b strings.Builder
	// The leading newline is Python's: it separates this entry from the
	// previous one without depending on how the previous writer ended.
	fmt.Fprintf(&b, "\n## [%s] %s %s\n", day, icon, goal)
	fmt.Fprintf(&b, "- **Status**: %s\n", statusStr)
	fmt.Fprintf(&b, "- **Type**: %s\n", taskType)
	// DISPLAY, so it gets a display bound: the stored summary carries real
	// per-step evidence for the lesson extractor, which is the right size
	// for a prompt and the wrong size for a human skimming a day's runs.
	fmt.Fprintf(&b, "- **Summary**: %s\n", dailyLogSummary(o.Summary))
	// Two blocks Python writes here are deliberately ABSENT, and both for
	// the same reason — the Go row has nothing to put in them, so emitting
	// them would be inventing content:
	//
	//   - the cost suffix, because WriteOutcome omits cost_usd on purpose
	//     (a hardcoded 0.0 is a record that lies about spend);
	//   - the Lessons block, because WriteOutcome writes an empty lessons
	//     list — the extractor is not ported yet.
	//
	// Neither is coded as an untakeable branch. When either lands, this is
	// the second writer to update, and the pins in dailylog_test.go say so.
	fmt.Fprintf(&b, "- **Tokens**: %din+%dout in %dms\n",
		o.TokensIn, o.TokensOut, o.ElapsedMS)
	if o.Project != "" {
		fmt.Fprintf(&b, "- **Project**: %s\n", o.Project)
	}
	entry := b.String()

	// A multi-line block append can exceed PIPE_BUF (4096B), so a bare
	// O_APPEND write interleaves and tears under concurrent writers. Take
	// the file's lock — the same path+".lock" Python's locked_write takes,
	// which is what makes the exclusion cross-runtime.
	return Locked(path, func() error {
		f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
		if err != nil {
			return fmt.Errorf("open daily log %s: %w", path, err)
		}
		defer f.Close()
		if _, err := f.WriteString(entry); err != nil {
			return fmt.Errorf("append daily log %s: %w", path, err)
		}
		return nil
	})
}

// updateMemoryIndex rewrites MEMORY.md with a current index of memory files.
//
// ONE NAMED DIVERGENCE, and it is a deliberate improvement rather than a
// port gap: Python wraps this whole body in a bare `except: pass`, so an
// unwritable memory dir or a full disk leaves the index silently months
// stale and nothing anywhere says so. Go returns the error to the caller,
// which announces it as a warning. The behaviour that matters — a failure
// here never fails the outcome — is preserved at the call site, where it
// belongs, instead of by swallowing.
func updateMemoryIndex(dir string) error {
	// Python globs "????-??-??.md" and sorts REVERSE, newest first. Measured
	// on CPython 3.14.3: pathlib's glob does NOT hide dotfiles and "?" is
	// not digit-restricted, so ".026-08-23.md" and "abcd-ef-gh.md" both
	// match — filepath.Glob agrees on both counts.
	//
	// It does NOT sort the same way, and this comment used to claim it did.
	// filepath.Glob orders by raw BYTE; `sorted()` orders the Path objects
	// by surrogateescape-decoded CODE POINT, and the two part for any name
	// that is not valid UTF-8. The `[:7]` below is what makes it matter:
	// the two runtimes would not merely list the same seven days in a
	// different order, they would pick DIFFERENT SEVEN and render different
	// days into MEMORY.md. Found by widening the sort class guard past the
	// single spelling it was derived from (adversarial r6, MEDIUM).
	matches, err := filepath.Glob(filepath.Join(dir, "????-??-??.md"))
	if err != nil {
		return fmt.Errorf("glob daily logs: %w", err)
	}
	// The full path, not the name: every match shares one directory prefix,
	// so comparing whole strings and comparing tails agree, and the whole
	// string is what Python compares.
	sort.SliceStable(matches, func(i, j int) bool {
		return pypath.FSLess(matches[j], matches[i]) // reverse=True
	})
	if len(matches) > 7 {
		matches = matches[:7]
	}

	// Python's load_outcomes rehydrates each row into the Outcome
	// dataclass, so a row missing any of the six fields that have no
	// default raises TypeError and is EXCLUDED. Measured by seeding partial
	// rows — CPython announced "4 row(s) ... are JSON but not loadable
	// under the current schema".
	//
	// That filter used to live HERE, applied locally by this renderer,
	// under the reasoning that "LoadOutcomes' tolerance is right for its
	// other consumers". It was not: the same tolerance made the evolver
	// mint a cycle off three rows CPython excludes, where CPython skipped
	// with "only 0 outcomes (need 3)". The filter belongs at the reader,
	// which is where Python puts it, and it is there now — a fix for the
	// site that has the fixture is evidence about its siblings, not a fix
	// for the class.
	//
	// The exclusion happens BEFORE the last-10 slice, so a dropped row does
	// not consume a window slot. LoadOutcomes filters before its own limit,
	// so the head of the full read is the same ten rows Python renders.
	all, err := LoadOutcomes(filepath.Dir(dir), 0)
	if err != nil {
		return err
	}
	rows := all
	if len(rows) > 10 {
		rows = rows[:10]
	}
	done, stuck, achieved, notAchieved, totalTokens := 0, 0, 0, 0, 0
	for _, row := range rows {
		switch s, _ := row["status"].(string); s {
		case "done":
			done++
		case "stuck":
			stuck++
		}
		// Python tests `is True` / `is False`, so a malformed goal_achieved
		// counts as neither and falls into the unjudged remainder. This is
		// the ONE place the port does not use record.GoalAchieved's
		// hardened tri-state: that reader grades a malformed verdict
		// NOT-achieved, which is right for a trust decision and wrong for a
		// tally whose three buckets must still sum to len(rows).
		if b, ok := row["goal_achieved"].(bool); ok {
			if b {
				achieved++
			} else {
				notAchieved++
			}
		}
		totalTokens += intOf(row["tokens_in"]) + intOf(row["tokens_out"])
	}

	lines := []string{
		"# Memory Index",
		"",
		fmt.Sprintf("*Auto-updated: %s*", time.Now().UTC().Format("2006-01-02 15:04 UTC")),
		"",
		"## Stats (last 10 runs)",
		fmt.Sprintf("- Done: %d | Stuck: %d", done, stuck),
		fmt.Sprintf("- Goal verdicts: %d achieved | %d NOT achieved | %d unjudged",
			achieved, notAchieved, len(rows)-achieved-notAchieved),
		fmt.Sprintf("- Total tokens: %s", thousands(totalTokens)),
		"",
		"## Daily Logs",
	}
	for _, m := range matches {
		base := filepath.Base(m)
		lines = append(lines, fmt.Sprintf("- [%s](%s)",
			strings.TrimSuffix(base, ".md"), base))
	}

	lines = append(lines, "", "## Lessons Count")
	// The FLAT legacy store (memory/lessons.jsonl), not the tiered one —
	// Python's _lessons_path points here and this line is that file's count.
	// A missing file reads as 0 rather than as an error, matching the
	// exists() branch.
	if raw, rerr := os.ReadFile(filepath.Join(dir, "lessons.jsonl")); rerr == nil {
		n := 0
		for _, l := range pytext.SplitLines(string(raw)) {
			if pytext.Strip(l) != "" {
				n++
			}
		}
		lines = append(lines, fmt.Sprintf("- %d lessons stored in lessons.jsonl", n))
	} else if os.IsNotExist(rerr) {
		lines = append(lines, "- 0 lessons stored")
	} else {
		return fmt.Errorf("count lessons: %w", rerr)
	}

	content := strings.Join(lines, "\n") + "\n"
	// Python's atomic_write defaults to errors="strict" and this caller does
	// not override it. A daily-log filename that is not valid UTF-8 reaches
	// the rendered index as a LONE SURROGATE, the encoder raises
	// UnicodeEncodeError, and the bare `except: pass` wrapped around this
	// whole body swallows it — so CPython writes NOTHING and MEMORY.md is
	// left exactly as it was. Measured on CPython 3.14.3: eight daily logs,
	// one of them named "2026-08-0\x80.md", index file never created.
	//
	// A Go string holds those bytes happily, so without this gate the port
	// would rewrite an index CPython would never have produced — the two
	// runtimes' MEMORY.md diverging over one shared store, which is the
	// failure this whole file exists to prevent. Found alongside the sort
	// fix above: the surrogate name only reaches the top seven because
	// CPython's code-point order keeps it, which is what made it visible.
	//
	// It returns rather than passing, per the named divergence in the
	// doc comment: the write does not happen either way, and the caller
	// announces it as a warning instead of nobody hearing.
	if !utf8.ValidString(content) {
		return fmt.Errorf("refusing to rewrite %s: a daily-log filename is "+
			"not valid UTF-8, and CPython's strict encoder abandons the whole "+
			"index rewrite rather than writing it",
			filepath.Join(dir, "MEMORY.md"))
	}
	return AtomicWrite(filepath.Join(dir, "MEMORY.md"), []byte(content))
}

// outcomeRequiredFields are the Outcome dataclass fields with no default
// (memory_ledger.Outcome). A row missing any of them cannot be rehydrated,
// so Python's reader drops it. Order is the dataclass's.
var outcomeRequiredFields = []string{
	"outcome_id", "goal", "task_type", "status", "summary", "lessons",
}

// OutcomeRequiredFields returns the field names load_outcomes requires, as
// a fresh copy.
//
// Exported for TEST FIXTURES in other packages. A seeder that hand-lists
// them re-spells a fact that lives here, and the failure mode is quiet:
// the fixture stops being a row CPython would load, the reader excludes
// it, and the test measures an empty corpus while reporting whatever an
// empty corpus produces. Three packages seeded outcome rows missing
// outcome_id and lessons before this filter existed, and every one of them
// was asserting over a store the Python runtime would have read as empty.
func OutcomeRequiredFields() []string {
	return append([]string(nil), outcomeRequiredFields...)
}

// loadableAsOutcome reports whether Python's load_outcomes would keep this
// row. Presence is the whole test — the rehydration passes values straight
// through without type checking, so a `goal_achieved` of "yes" survives it
// and is counted as unjudged downstream, exactly as it is here.
//
// LoadOutcomes now applies this at the read, so no renderer has to. It
// stays as the single spelling of the predicate that missingOutcomeFields
// answers, and as the thing a test can call directly.
func loadableAsOutcome(row map[string]any) bool {
	return len(missingOutcomeFields(row)) == 0
}

// thousands is Python's format(n, ",") — groups of three, comma-separated,
// with the sign outside the grouping ("-1,234,567").
func thousands(n int) string {
	sign := ""
	if n < 0 {
		sign = "-"
		n = -n
	}
	digits := fmt.Sprintf("%d", n)
	var out []byte
	for i, c := range []byte(digits) {
		if i > 0 && (len(digits)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, c)
	}
	return sign + string(out)
}

// intOf reads a JSON-decoded number as an int. Whole-file JSON decoding
// yields float64 for every number, so the ledger's token counts arrive as
// floats even though they were written as ints.
func intOf(v any) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	case int64:
		return int(n)
	}
	return 0
}
