// Package playbook ports src/playbook.py — the director's operational
// wisdom file, a markdown document both runtimes append to, dedup, expire
// entries from, and inject into director and decompose prompts.
//
// It is the most shape-sensitive file ported so far. Everything here is
// simultaneously (a) bytes in a file the other runtime reads, (b) a dedup
// key, and (c) text that goes into a prompt — so a one-character
// divergence is not cosmetic in any of the three roles. The specific
// hazards, all measured on this box rather than reasoned about:
//
//   - `_entry_core` ends in `.casefold()`, which is NOT `.lower()`: it
//     differs on 297 code points and has no Final_Sigma rule. See
//     pytext.CaseFold.
//   - Python's `re` reads `\s` as 29 code points and `\d` as 760; Go's
//     reads them as 5 and 10. See pytext.SpaceClass / pytext.DigitClass.
//   - `str.split()` splits on 29 code points; strings.Fields splits on 25.
//     The four that differ (U+001C..U+001F) are collapsed to a space by
//     Python and kept verbatim by Go, in text that becomes a file row.
//   - Every length in Inject is a CODE-POINT length, and the budget is
//     documented as a real contract. Go's len() is bytes.
//   - There are TWO dedup keys in this file and they are not the same one.
//     Append's pre-append check does not casefold and does not strip
//     attribution; entryCore does both.
//
// The one deliberate structural difference from Python: these verbs take
// the workspace directory as an argument instead of reading a module-level
// path, matching the rest of this port.
package playbook

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// utcNow is the time seam. Rotation uses the same one; the archive
// filenames and alarm stamps are content, so tests must be able to freeze
// them rather than assert around them.
var utcNow = func() time.Time { return time.Now().UTC() }

// seedContent is playbook.py's _SEED_CONTENT. It is the initial BYTES of
// a file both runtimes read, so it must be byte-identical including the
// {date} substitution — the only honest pin is a differential against
// Python's own seed_playbook.
const seedContent = `# Director's Playbook

Operational wisdom accumulated by the orchestration system. This document
is maintained automatically (evolver, standing rules) and can be edited
manually by the operator. It's injected into director and decompose context.

Entry form (operator decree, 2026-08-02): guidance reads "usually, do
this", not "it has to look like this" — entries are priors the run weighs,
not requirements it obeys.

---

## Decomposition

- Research goals benefit from a gather → synthesize → verify structure.
- Wide/deep goals often work well with staged-pass decomposition.
- Atomic steps usually beat broad ones — often one file or one command per step.

## Execution

- If a step fails 3 times, the problem is usually the decomposition, not the execution.
- Build tasks usually need more room than research tasks (~2x tokens is a fair prior, not a cap).
- Always verify outputs before recording as done.

## Cost

- Execution floor is MID (2026-07-21 unification); POWER at orchestrator/planner/reviewer decision points; CHEAP only for non-agentic calls (classify, triage, curation).
- Enable extended thinking for decompose (high) and advisory calls (mid).
- Narrow goals can often skip multi-plan (saves 3 LLM calls).
- Cost signals are inputs to weigh, not verdicts — when cost and correctness pull apart, correctness usually wins.

## Quality

- The verification loop is usually the highest-leverage quality investment — though a good skill or foundational data can beat it.
- Inspector friction signals should be acted on, not just logged.
- Standing rules are zero-cost — promote when validated, and contest/retire is the exit when one turns out wrong.

---

*Last updated: %s*
`

const (
	alarmMarker           = "alarm"
	defaultAlarmTTLDays   = 14
	defaultInjectMaxChars = 800
)

// seedSections are the sections _SEED_CONTENT ships. An entry outside them
// counts as learned even with no attribution.
var seedSections = map[string]bool{
	"Decomposition": true, "Execution": true, "Cost": true, "Quality": true,
}

// noInjectSections stay in the file and never reach a prompt. "Signals"
// held proposed autonomous goals for human review; as a non-seed section
// they ranked as learned, so unreviewed proposals outranked the curated
// seed in every director call.
var noInjectSections = map[string]bool{"Signals": true}

var (
	// attribRE is Python's _ATTRIB_RE. Both `\s` are the measured
	// 29-code-point class, not Go's five, because the transcription is
	// the contract — but only the TRAILING one is load-bearing, and the
	// comment here used to claim both were.
	//
	// EQUIVALENT-MUTANT NOTE (adversarial r10 LOW): narrowing the LEADING
	// class to Go's `\s` changes no output. It sits immediately before an
	// end-anchored attribution, so whatever it declines to consume becomes
	// TRAILING whitespace on the remainder — and entryCore's own
	// pytext.Strip removes that anyway. ParseEntries only asks
	// match/no-match, where `*` can always match zero. The trailing class
	// genuinely differs (a dedup key keeps or drops a U+001C) and is
	// pinned by the "- entry *(from x)*" corpus line.
	attribRE = regexp.MustCompile(pytext.SpaceClass + `*\*\(from [^)]*\)\*` +
		pytext.SpaceClass + `*$`)

	// alarmRE is Python's _ALARM_RE. Three separate class hazards: the two
	// `\s` runs, the negated `[^\s)·]` key class, and `\d` in the date —
	// which Python matches for all 760 Unicode decimal digits.
	alarmRE = regexp.MustCompile(`·` + pytext.SpaceClass + `*` + alarmMarker +
		pytext.SpaceClass + `+(` + pytext.NotClass(")·") + `+)` +
		pytext.SpaceClass + `+@(` + pytext.DigitClass + `{4}-` +
		pytext.DigitClass + `{2}-` + pytext.DigitClass + `{2})`)

	// lastUpdatedRE is greedy and line-bounded in Python (`.` excludes
	// newline, `.*` runs to the LAST `*` on the line). Go's default `.`
	// also excludes newline, so this ports directly — but only because the
	// two defaults agree, which is worth not "cleaning up".
	lastUpdatedRE = regexp.MustCompile(`\*Last updated:.*\*`)

	// strptimeMonth and strptimeDay are the TWO-CHARACTER alternations
	// CPython's _strptime builds for %m and %d, which is all alarmRE can
	// hand them. They are transcribed from the measured patterns rather
	// than reasoned about; see alarmDate for the measurement.
	//
	// Note which one carries pytext.DigitClass and which does not: the
	// day's `[1-2]\d` is Unicode-aware and every other alternation is a
	// literal. Making them uniform in either direction is a divergence.
	strptimeMonth = regexp.MustCompile(`^(?:1[0-2]|0[1-9])$`)
	strptimeDay   = regexp.MustCompile(`^(?:3[01]|[1-2]` + pytext.DigitClass +
		`|0[1-9])$`)
	// The day's `3[01]` upper bound is belt-and-braces: widening it to
	// `3[0-2]` is an EQUIVALENT mutation, because the only extra string it
	// admits is "32" and time.Parse rejects day 32 in every month of every
	// year (proved by exhaustive probe, r9 battery). It is transcribed
	// faithfully anyway — the transcription is the contract, and a reader
	// who "simplified" it here would have to re-derive that proof.

	// sectionHeaderRE finds the next section boundary. Python anchors with
	// (?m)^## ; the LINE anchoring is a security property, not a nicety —
	// a substring lookup let crafted entry text spoof section membership.
	nextHeaderRE = regexp.MustCompile(`(?m)^## `)
)

// Path is config.playbook_path(): workspace_root()/playbook.md.
func Path(ws string) string { return filepath.Join(ws, "playbook.md") }

// Seed creates the initial playbook. It never overwrites.
func Seed(ws string) error {
	path := Path(ws)
	if _, err := os.Stat(path); err == nil {
		return nil // don't overwrite
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	body := fmt.Sprintf(seedContent, utcNow().Format("2006-01-02"))
	return os.WriteFile(path, []byte(body), 0o644)
}

// Load returns the full playbook text, seeding the file if it is missing.
// Python swallows read errors and returns "" — ported, because callers sit
// on prompt-assembly paths where a raised error would kill a run over a
// file that is advisory by design.
func Load(ws string) string {
	path := Path(ws)
	if _, err := os.Stat(path); err != nil {
		_ = Seed(ws)
	}
	text, err := readText(path)
	if err != nil {
		return ""
	}
	return text
}

// decodeText is the newline half of Python's
// `Path.read_text(encoding="utf-8")`, and newlines — not encoding — are
// where the two runtimes actually diverged.
//
// Text mode with the default `newline=None` is UNIVERSAL NEWLINES: it
// rewrites "\r\n" AND a lone "\r" to "\n" before the caller sees a single
// character. All FIVE of Python's playbook reads go through it
// (playbook.py:142, 299, 472, 691, 734); os.ReadFile does not, and
// nothing downstream re-normalises.
//
// It translates those two sequences and NOTHING else. Measured, not
// assumed: "\x0b", "\x0c", "\x1e" and U+2028 all survive read_text
// unchanged, so this is a two-step replacement and emphatically not a
// whitespace fold — widening it would diverge in the other direction.
//
// A CR is not hypothetical on a document Go itself writes: compress()
// stores the model's reply after stripping only its ENDS, so a model that
// answers in CRLF puts CRs into playbook.md through Go's own path. The
// consequence was a forged section — sectionSpan's
// `(?m)^##[ \t]+Cost[ \t]*$` cannot match "## Cost\r", so insertEntry took
// the create-a-new-section branch and grew a SECOND "## Cost" where
// Python found the first and normalised the file (adversarial r10 HIGH).
func decodeText(b []byte) string {
	s := string(b)
	if !strings.ContainsRune(s, '\r') {
		return s
	}
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n")
}

// readText is os.ReadFile through decodeText: the ONE read helper for this
// package, so a future read site cannot reintroduce the raw-bytes path by
// omission.
func readText(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return decodeText(b), nil
}

// sectionSpan returns the byte offsets of the named section's body, and
// whether the file has such a header LINE at all.
//
// Line-anchored on purpose, and the ONE section-boundary implementation:
// the original substring lookup treated `## Canon` embedded anywhere —
// including inside an entry's own text — as the section header, so crafted
// entry content could spoof section membership. Entry text is
// LLM/pipeline-derived, not operator-only.
func sectionSpan(text, section string) (int, int, bool) {
	re, err := regexp.Compile(`(?m)^##[ \t]+` + regexp.QuoteMeta(section) + `[ \t]*$`)
	if err != nil {
		return 0, 0, false
	}
	m := re.FindStringIndex(text)
	if m == nil {
		return 0, 0, false
	}
	headerEnd := m[1]
	end := len(text)
	if nxt := nextHeaderRE.FindStringIndex(text[headerEnd:]); nxt != nil {
		end = headerEnd + nxt[0]
	}
	return headerEnd, end, true
}

// SectionText is the named section's text from the live playbook, "" when
// absent. It exists so a caller verifying that an append LANDED IN a
// section can scope its membership check: the canon door once checked its
// marker against the WHOLE playbook, so a same-marker entry in any other
// section false-verified a deduped append.
func SectionText(ws, section string) string {
	text := Load(ws)
	start, end, ok := sectionSpan(text, section)
	if !ok {
		return ""
	}
	return text[start:end]
}

// entryCore is the dedup key: bullet text without dash prefix,
// attribution, or case.
//
// NOT the same key Append uses before appending — see appendCore. Two
// distinct rules, one line apart in Python, and routing both through one
// helper changes which appends are skipped.
func entryCore(line string) string {
	core := attribRE.ReplaceAllString(pytext.Strip(line), "")
	// `.lstrip("- ")` strips the CHARACTER SET {'-',' '}, which is not
	// whitespace stripping and not a prefix removal.
	core = strings.TrimLeft(core, "- ")
	return pytext.CaseFold(pytext.Strip(core))
}

// AlarmKey is the check a line reports on, "" when the line is not an
// alarm.
func AlarmKey(line string) string {
	m := alarmRE.FindStringSubmatch(line)
	if m == nil {
		return ""
	}
	return m[1]
}

// alarmDate is the stamp on an alarm line, and whether the line carries
// one that CPython's strptime would also accept.
//
// The second return is not redundant with a match: alarmRE matches all
// 760 Unicode decimal digits (Python's `\d`), and strptime accepts a
// DIFFERENT set position by position. A stamp that matches but does not
// parse means "unreadable stamp → keep, don't guess" in both runtimes.
//
// The three positions do not agree, and the earlier comment here claimed
// they did — that strptime "REJECTS the non-ASCII ones it just matched"
// so both runtimes keep the line anyway. That is false for the year, and
// the whole-document differential found it (adversarial r9). CPython's
// _strptime builds the format from these, measured on this box:
//
//	%Y -> (?P<Y>\d\d\d\d)                       Unicode-aware
//	%m -> (?P<m>1[0-2]|0[1-9]|[1-9])             ASCII literals only
//	%d -> (?P<d>3[0-1]|[1-2]\d|0[1-9]|[1-9]| [1-9])
//
// So, for the two-digit groups alarmRE guarantees:
//
//   - the YEAR takes any of the 760, each contributing its
//     unicodedata.decimal value: '٢٠٠١-01-01' parses as 2001;
//   - the MONTH is ASCII-only, because both two-character alternations
//     are literal: '2001-1٢-01' is a ValueError;
//   - the DAY's SECOND digit is Unicode-capable through `[1-2]\d` and
//     not otherwise: '2001-01-1٢' is the 12th, while '2001-01-3٠' and
//     '2001-01-0٥' both fail.
//
// Year 0000 is a ValueError in CPython even when it matches ("year must
// be in 1..9999"), and time.Parse accepts it — so it is refused here
// explicitly rather than left to the layout.
func alarmDate(line string) (time.Time, bool) {
	m := alarmRE.FindStringSubmatch(line)
	if m == nil {
		return time.Time{}, false
	}
	stamp := m[2]
	// Split on the two ASCII hyphens alarmRE itself matched.
	parts := strings.Split(stamp, "-")
	if len(parts) != 3 {
		return time.Time{}, false
	}
	year, month, day := parts[0], parts[1], parts[2]
	if !strptimeMonth.MatchString(month) || !strptimeDay.MatchString(day) {
		return time.Time{}, false // unreadable stamp → keep, don't guess
	}
	// Only the year and the day's second digit can be non-ASCII by now,
	// and CPython folds both by decimal VALUE — which is exactly what
	// pytext.FoldDecimals does.
	t, err := time.Parse("2006-01-02",
		pytext.FoldDecimals(year)+"-"+month+"-"+pytext.FoldDecimals(day))
	if err != nil {
		return time.Time{}, false
	}
	if t.Year() == 0 {
		return time.Time{}, false // CPython: "year must be in 1..9999"
	}
	return t.UTC(), true
}

// replaceAlarm swaps the existing entry for key in place, keeping its
// position. An alarm that keeps re-firing must not leapfrog the rest of
// the playbook every time it fires.
func replaceAlarm(text, key, newLine string) (bool, string) {
	lines := strings.Split(text, "\n")
	replaced := false
	for i, line := range lines {
		if !replaced && AlarmKey(line) == key {
			lines[i] = newLine
			replaced = true
		}
	}
	return replaced, strings.Join(lines, "\n")
}

// Dropped is one expired alarm: the check it reported on and its line.
type Dropped struct {
	Key  string
	Line string
}

// expireText drops alarms whose check has stopped re-firing. Pure — no
// I/O.
//
// The cutoff is a wall-clock instant, not a midnight: Python compares a
// date-only stamp (midnight UTC) against `now - timedelta(days=N)`. Port
// the arithmetic, not a date-difference simplification.
func expireText(text string, maxAgeDays int) (string, []Dropped) {
	cutoff := utcNow().AddDate(0, 0, -maxAgeDays)
	var kept []string
	var dropped []Dropped
	for _, line := range strings.Split(text, "\n") {
		if key := AlarmKey(line); key != "" {
			if stamped, ok := alarmDate(line); ok && stamped.Before(cutoff) {
				dropped = append(dropped, Dropped{Key: key, Line: line})
				continue
			}
		}
		kept = append(kept, line)
	}
	return strings.Join(kept, "\n"), dropped
}

// stampUpdated refreshes the document's Last-updated line, if it has one.
func stampUpdated(text string) string {
	if !strings.Contains(text, "*Last updated:") {
		return text
	}
	return lastUpdatedRE.ReplaceAllString(text,
		"*Last updated: "+utcNow().Format("2006-01-02")+"*")
}

// Entry is one parsed playbook bullet.
type Entry struct {
	Section  string
	Line     string
	Core     string
	Learned  bool
	Position int
}

// ParseEntries parses playbook bullets into entries with section and
// provenance. An entry is LEARNED if it carries a source attribution or
// lives in a non-seed section; bare bullets inside seed sections are seed
// content.
func ParseEntries(text string) []Entry {
	var entries []Entry
	section := ""
	for pos, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "## ") {
			section = pytext.Strip(line[3:])
			continue
		}
		if !strings.HasPrefix(strings.TrimLeftFunc(line, pytext.IsSpace), "- ") {
			continue
		}
		core := entryCore(line)
		if core == "" {
			continue
		}
		learned := attribRE.MatchString(line) ||
			(section != "" && !seedSections[section])
		sec := section
		if sec == "" {
			sec = "Notes"
		}
		entries = append(entries, Entry{
			Section:  sec,
			Line:     pytext.Strip(line),
			Core:     core,
			Learned:  learned,
			Position: pos,
		})
	}
	return entries
}

// Inject is the ranked playbook selection for context injection.
//
// Priority: learned entries first (newest first — file position is append
// order), then seed entries in file order; exact duplicates by normalized
// core dropped, highest-ranked copy surviving. Fills the budget greedily
// and renders grouped by section.
//
// EVERY length here is a code-point count, because Python's is, and the
// budget covering everything emitted is documented as a real contract.
// The live playbook always holds at least one non-ASCII character (the
// alarm marker is U+00B7), so byte counting is not a theoretical
// difference.
func Inject(ws string, maxChars int) string {
	text := Load(ws)
	if text == "" {
		return ""
	}

	var entries []Entry
	for _, e := range ParseEntries(text) {
		if !noInjectSections[e.Section] {
			entries = append(entries, e)
		}
	}
	if len(entries) == 0 {
		return ""
	}

	var learned, seed []Entry
	for _, e := range entries {
		if e.Learned {
			learned = append(learned, e)
		} else {
			seed = append(seed, e)
		}
	}
	// Newest (last-appended) first.
	for i, j := 0, len(learned)-1; i < j; i, j = i+1, j-1 {
		learned[i], learned[j] = learned[j], learned[i]
	}
	ranked := append(learned, seed...)

	// Dedup in RANK order — the highest-ranked copy survives: a learned
	// entry beats a seed line with the same core, a newer learned entry
	// beats an older one. First-occurrence-wins discarded the attributed
	// copy.
	seen := map[string]bool{}
	var unique []Entry
	for _, e := range ranked {
		if seen[e.Core] {
			continue
		}
		seen[e.Core] = true
		unique = append(unique, e)
	}

	const top = "## Operational Playbook"
	var selected []Entry
	included := map[string]bool{}
	chars := utf8.RuneCountInString(top) + 1
	for _, e := range unique {
		cost := utf8.RuneCountInString(e.Line) + 1
		if !included[e.Section] {
			cost += utf8.RuneCountInString(e.Section) + 4 // "## <section>\n"
		}
		if chars+cost > maxChars {
			continue
		}
		selected = append(selected, e)
		included[e.Section] = true
		chars += cost
	}
	if len(selected) == 0 {
		return ""
	}

	// Sections in FILE order (stable document shape); bullets within a
	// section in RANK order — newest learned first — so the ranking is
	// visible to the consumer, not just to selection.
	byPosition := make([]Entry, len(selected))
	copy(byPosition, selected)
	// Stable: positions are unique line indices today, but this feeds
	// section ORDER and must not depend on that staying true.
	sort.SliceStable(byPosition, func(i, j int) bool {
		return byPosition[i].Position < byPosition[j].Position
	})

	// dict.fromkeys(...) — an ORDERED set. A Go map would reshuffle the
	// rendered playbook per process.
	var order []string
	seenSec := map[string]bool{}
	for _, e := range byPosition {
		if !seenSec[e.Section] {
			seenSec[e.Section] = true
			order = append(order, e.Section)
		}
	}

	var out []string
	for _, sec := range order {
		out = append(out, "## "+sec)
		for _, e := range selected {
			if e.Section == sec {
				out = append(out, e.Line)
			}
		}
	}
	return top + "\n" + strings.Join(out, "\n")
}

// collapseSpace is Python's `" ".join(s.split())`.
//
// NOT strings.Fields: that splits on unicode.IsSpace (25 code points) and
// Python's bare split() uses its own 29. An entry carrying U+001C..U+001F
// is collapsed to a space by Python and kept verbatim by Go otherwise, and
// this text becomes both a file row and a dedup key.
func collapseSpace(s string) string {
	return strings.Join(strings.FieldsFunc(s, pytext.IsSpace), " ")
}

// clipRunes truncates to n CODE POINTS, appending the ellipsis Python
// appends. Python slices `entry[:500]`, which is code points.
func clipRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n]) + "…"
}

// DefaultInjectMaxChars and DefaultAlarmTTLDays are Python's keyword
// defaults, exported because Go has no optional arguments and the
// alternative — a verb that silently substitutes its default for a value
// the caller actually passed — is a sign flip at a prompt-assembly seam.
//
// Inject(ws, 0) used to mean "use 800"; in Python `inject_playbook(0)`
// selects nothing and returns "" (the running cost starts at len(top)+1
// and every entry overflows). A caller spelling "inject nothing" as 0 got
// the whole playbook (adversarial r9 LOW). Now 0 means 0 in both, and a
// caller wanting the default names it.
const (
	DefaultInjectMaxChars = defaultInjectMaxChars
	DefaultAlarmTTLDays   = defaultAlarmTTLDays
)

// ExpireStaleAlarms drops alarms whose check has stopped re-firing, and
// returns how many.
//
// maxAgeDays has no default here — pass DefaultAlarmTTLDays for Python's.
// Note that 0 is a real value and an aggressive one: it expires every
// alarm stamped before this instant.
//
// An alarm is only as true as its last reading. When the condition
// clears, the scanner simply stops emitting it — there is no "resolved"
// event to listen for — so silence is what has to retire it. Archives
// before rewriting, like every other playbook mutation: the entry leaves
// the injected context, not the record.
func ExpireStaleAlarms(ws string, maxAgeDays int) int {
	path := Path(ws)
	text, err := readText(path)
	if err != nil {
		return 0
	}

	updated, dropped := expireText(text, maxAgeDays)
	if len(dropped) == 0 {
		return 0
	}
	if _, err := Archive(ws, text, "alarm-expiry"); err != nil {
		return 0 // can't restore → don't rewrite (same rule as curation)
	}
	if err := atomicWrite(path, []byte(stampUpdated(updated))); err != nil {
		return 0
	}
	return len(dropped)
}

// Archive is the append-only archive of a playbook version. It never
// deletes.
//
// Data-retention rule: learning data is never destroyed, so every rewrite
// preserves the pre-rewrite version here first. The collision loop is
// load-bearing — two rewrites in the same second must not clobber each
// other — and so is the caller's obligation to ABORT when this fails.
func Archive(ws, text, reason string) (string, error) {
	dir := filepath.Join(ws, "playbook_history")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	ts := utcNow().Format("20060102T150405Z")
	p := filepath.Join(dir, "playbook-"+ts+"-"+reason+".md")
	for n := 1; ; n++ {
		if _, err := os.Stat(p); os.IsNotExist(err) {
			break
		}
		if n > maxArchiveCollisions {
			return "", fmt.Errorf("no free archive name after %d attempts at %s",
				maxArchiveCollisions, ts)
		}
		p = filepath.Join(dir,
			fmt.Sprintf("playbook-%s-%s-%d.md", ts, reason, n))
	}
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		return "", err
	}
	return p, nil
}

// maxArchiveCollisions bounds the same-second retry loop. Python's is
// unbounded; an unbounded loop over a stat() that keeps saying "exists"
// is a hang, not a retry, so this fails loudly instead. The caller then
// aborts the rewrite, which is the safe direction.
const maxArchiveCollisions = 1000

// atomicWrite is a package-level indirection over record.AtomicWrite.
//
// Its comment used to say it was "a seam so tests can pin the ORDER of
// the archive write and the rewrite". No test has ever assigned to it,
// and the ordering it named is pinned by other means — the two tests that
// block the history directory with a regular file and then assert the
// document is untouched. A var described by a contract nothing holds
// reads to the next author as covered (adversarial r9 LOW).
//
// It is kept, and honestly labelled, for one reason: the two writes it
// fronts are the only ones in this package that can destroy an operator's
// document, so a future test that needs to fail one of them mid-sequence
// has somewhere to stand. If that test never arrives, delete this.
var atomicWrite = record.AtomicWrite

// Append adds an operational insight to the playbook.
//
// key marks the entry as a re-readable ALARM, identified by the check it
// reports on. A later reading of the same check REPLACES this entry in
// place rather than appending beside it, and the entry expires if the
// check stops re-firing. Without a key an entry is a durable insight:
// appended once, never auto-removed.
//
// rec may be nil; the captain's-log row is written outside the lock and
// its failure never fails the append, matching Python.
func Append(ws string, rec *record.Recorder, entry, section, source, key string) error {
	// Reject empty or whitespace-only entries.
	entry = pytext.Strip(entry)
	if entry == "" {
		return nil
	}
	// An entry is ONE playbook line by contract. Collapse embedded
	// newlines instead of writing raw lines into the file: a multiline
	// entry (or source/key marker) could otherwise land a `## <section>`
	// header line of its own and spoof section membership for every later
	// line-anchored read. Entry text is LLM/pipeline-derived, not
	// operator-only.
	entry = collapseSpace(entry)
	source = collapseSpace(source)
	key = collapseSpace(key)
	entry = clipRunes(entry, 500)

	path := Path(ws)
	if _, err := os.Stat(path); err != nil {
		if err := Seed(ws); err != nil {
			return err
		}
	}

	var entryLine string
	var wrote bool
	err := record.Locked(path, func() error {
		text, err := readText(path)
		if err != nil {
			return err
		}

		entryLine = entry
		if !strings.HasPrefix(entryLine, "- ") {
			entryLine = "- " + entry
		}
		if source != "" || key != "" {
			var bits []string
			if source != "" {
				bits = append(bits, "from "+source)
			}
			if key != "" {
				// The space before @ is load-bearing: _ALARM_RE requires
				// `\s+@`, so an alarm written without it never matches its
				// own key-lookup — in EITHER runtime. Alarms would then
				// accrete beside each other instead of being replaced in
				// place, which is the exact failure this mechanism exists
				// to stop. Caught by the whole-file differential; it is
				// the same one-character content-key divergence that has
				// produced a defect in every chunk of this port.
				bits = append(bits, alarmMarker+" "+key+" @"+
					utcNow().Format("2006-01-02"))
			}
			entryLine += " *(" + strings.Join(bits, " · ") + ")*"
		}

		if key != "" {
			// Same check, newer reading: replace in place. Keeping the
			// original position is deliberate — an alarm that keeps
			// re-firing must not leapfrog the rest of the playbook every
			// time it fires.
			if replaced, updated := replaceAlarm(text, key, entryLine); replaced {
				// NO captain's-log row, and that is Python's behaviour,
				// not an omission: its replace branch RETURNS from inside
				// the lock, so the trailing log_event is never reached.
				// An alarm re-read is not news — re-firing is the whole
				// point of an alarm, so logging every reading would add
				// one row per scan to a shared, rotated, rendered log.
				// `wrote` stays false (adversarial r9 HIGH).
				return atomicWrite(path, []byte(stampUpdated(updated)))
			}
		}

		// Dedup. NOTE this is NOT entryCore: no casefold, no attribution
		// strip, and a plain substring test against the whole file. Two
		// dedup rules one line apart in Python, and merging them changes
		// which appends are skipped.
		appendCore := pytext.Strip(strings.TrimLeft(entry, "- "))
		if appendCore != "" && strings.Contains(text, appendCore) {
			return nil // duplicate; no write at all
		}

		updated := insertEntry(text, section, entryLine)
		wrote = true
		return atomicWrite(path, []byte(stampUpdated(updated)))
	})
	if err != nil {
		return err
	}

	// Captain's log, outside the lock — it does not need file exclusivity,
	// and its failure must not fail the append.
	if wrote && rec != nil {
		_ = rec.Event("PLAYBOOK_UPDATED", section, clipNoEllipsis(entryLine, 200),
			map[string]any{"source": source, "section": section}, "")
	}
	return nil
}

// insertEntry places entryLine under section, creating the section if the
// file has no such header LINE.
func insertEntry(text, section, entryLine string) string {
	sectionHeader := "## " + section
	if headerEnd, sectionEnd, ok := sectionSpan(text, section); ok {
		if sectionEnd == len(text) {
			// Last section: insert before the "Last updated" stamp.
			if i := strings.Index(text[headerEnd:], "\n*Last updated:"); i >= 0 {
				sectionEnd = headerEnd + i
			}
		}
		return text[:headerEnd] +
			pytext.TrimRight(text[headerEnd:sectionEnd]) +
			"\n" + entryLine + "\n" +
			text[sectionEnd:]
	}
	// Create a new section before "Last updated", or at the end.
	if i := strings.Index(text, "\n*Last updated:"); i >= 0 {
		return pytext.TrimRight(text[:i]) + "\n\n" +
			sectionHeader + "\n\n" + entryLine + "\n" + text[i:]
	}
	return pytext.TrimRight(text) + "\n\n" + sectionHeader + "\n\n" + entryLine + "\n"
}

// clipNoEllipsis is Python's `s[:n]` — a plain code-point slice with NO
// ellipsis. clipRunes is the OTHER one, which appends "…". Both appear in
// playbook.py within a few lines of each other.
func clipNoEllipsis(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}
