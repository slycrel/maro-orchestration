package orch

import (
	"fmt"
	"os"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// NextItem is one checklist line. Index is the item's IDENTITY — nothing
// else in the ledger distinguishes two items with the same text — and it
// is the 0-based line number produced by pytext.SplitLines, so it means
// the same thing to both runtimes only as long as they split lines the
// same way.
type NextItem struct {
	Index  int
	State  string
	Text   string
	Line   string
	Indent int // in CHARACTERS, the way Python's len() counts them
}

// pySpaceClass is Python re's \s written out. Go's regexp \s is
// [\t\n\f\r ] — five characters against Python's 29 — so writing the
// pattern with \s would silently narrow every one of the four places
// orch_items.ITEM_RE uses it.
const pySpaceClass = `[\x{0009}-\x{000d}\x{001c}-\x{001f}\x{0020}\x{0085}\x{00a0}` +
	`\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]`

// itemRe is Python orch_items.ITEM_RE with \s expanded. Go's regexp uses
// leftmost-first (Perl) submatch semantics, so the lazy `.+?` between two
// greedy whitespace runs resolves to the same span a backtracking engine
// would pick: the text with its surrounding whitespace trimmed.
var itemRe = regexp.MustCompile(
	`^(` + pySpaceClass + `*)-` + pySpaceClass + `*\[([ xX~!])\]` +
		pySpaceClass + `*(.+?)` + pySpaceClass + `*$`)

// checkboxRe is Python's `\[(.)\]` — deliberately loose. mark_item
// rewrites the FIRST bracket pair on the line, which is the checkbox only
// because the pattern that FOUND the item requires the checkbox to come
// before any text. Kept verbatim rather than tightened: a tighter
// substitution here would rewrite a different character than Python's on
// some line neither of us has thought of, and the file is shared.
var checkboxRe = regexp.MustCompile(`\[(.)\]`)

// validState reports whether a state character is one of the four.
func validState(s string) bool {
	return s == StateTodo || s == StateDoing || s == StateDone || s == StateBlocked
}

// readLedger reads NEXT.md as Python's read_text(encoding="utf-8") does:
// strictly. Invalid UTF-8 raises there, so it must refuse here too —
// admitting a file the other runtime cannot read would let this runtime
// renumber, rewrite and mark items in a ledger Python has stopped being
// able to touch at all.
func readLedger(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if !utf8.Valid(raw) {
		return "", fmt.Errorf("%s is not valid UTF-8; the Python runtime "+
			"cannot read it either — repair the file rather than letting the "+
			"two runtimes disagree about its contents", path)
	}
	return string(raw), nil
}

// parseLines splits a ledger body into lines and finds its items. Split
// out from ParseNext so the in-lock mutators can re-parse content they
// already hold without a second read.
func parseLines(body string) ([]string, []NextItem) {
	lines := pytext.SplitLines(body)
	var items []NextItem
	for i, line := range lines {
		m := itemRe.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		state := m[2]
		if state == "X" {
			state = StateDone
		}
		items = append(items, NextItem{
			Index:  i,
			State:  state,
			Text:   pytext.Strip(m[3]),
			Line:   line,
			Indent: utf8.RuneCountInString(m[1]),
		})
	}
	return lines, items
}

// ParseNext reads a project's checklist. A missing NEXT.md is an error,
// not an empty list: Python raises ValueError, and every caller here
// treats "no ledger" as a broken project rather than a finished one.
func ParseNext(ws, slug string) ([]string, []NextItem, error) {
	body, err := readLedger(NextPath(ws, slug))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil, fmt.Errorf("project %s has no NEXT.md", slug)
		}
		return nil, nil, err
	}
	lines, items := parseLines(body)
	return lines, items, nil
}

// renderLedger is Python write_next_lines' body: join with "\n", rstrip,
// then exactly one trailing newline.
//
// The rstrip is load-bearing and easy to read as cosmetic. It strips
// Python whitespace from the END of the whole joined document, which
// removes trailing BLANK LINES, not just trailing spaces — so a ledger
// that ends in blank lines loses them on the first write, and an item
// whose text is empty ("- [ ] " with one trailing space) stops being an
// item if it happens to be the last line.
func renderLedger(lines []string) []byte {
	return []byte(pytext.TrimRight(strings.Join(lines, "\n")) + "\n")
}

// GetItem returns one item by line index.
func GetItem(ws, slug string, itemIndex int) (NextItem, error) {
	_, items, err := ParseNext(ws, slug)
	if err != nil {
		return NextItem{}, err
	}
	for _, it := range items {
		if it.Index == itemIndex {
			return it, nil
		}
	}
	return NextItem{}, fmt.Errorf("item_index %d not found in NEXT.md for %s",
		itemIndex, slug)
}

// MarkItem flips one item's state.
//
// Parse and rewrite happen under NEXT.md's lock because they are one
// read-modify-write: two concurrent markers (the heartbeat's backlog
// drain flipping an item to DOING while a finishing run flips it DONE)
// was a lost-update race in Python before the lock was added, and the two
// runtimes contend on the SAME lock file.
func MarkItem(ws, slug string, itemIndex int, newState string) error {
	if !validState(newState) {
		return fmt.Errorf("invalid new state: %s", pytext.Repr(newState))
	}
	path := NextPath(ws, slug)
	return record.Locked(path, func() error {
		body, err := readLedger(path)
		if err != nil {
			if os.IsNotExist(err) {
				return fmt.Errorf("project %s has no NEXT.md", slug)
			}
			return err
		}
		lines, items := parseLines(body)
		found := -1
		for _, it := range items {
			if it.Index == itemIndex {
				found = it.Index
				break
			}
		}
		if found < 0 {
			return fmt.Errorf("item_index %d not found in NEXT.md for %s",
				itemIndex, slug)
		}
		// ReplaceAllString would expand $ in the replacement; the states
		// contain none, but a literal replacement says so structurally
		// rather than relying on the alphabet staying the way it is.
		once := true
		lines[found] = checkboxRe.ReplaceAllStringFunc(lines[found], func(m string) string {
			if !once {
				return m
			}
			once = false
			return "[" + newState + "]"
		})
		if err := record.AtomicWrite(path, renderLedger(lines)); err != nil {
			return err
		}
		// The PID stamp and the state flip are ONE transition, so the
		// stamp is written under the same lock. Best-effort by design: a
		// failed stamp costs later crash forensics, and failing the mark
		// over it would cost the run.
		stampDoingPID(ws, slug, itemIndex, newState)
		return nil
	})
}

// AppendNextItems adds TODO items to the end of the checklist and returns
// the line index of each.
//
// NAMED IMPROVEMENT over Python, which computes the returned indices as
// range(len(lines_before), …) — arithmetic that assumes one item per
// line. An item whose text contains a line separator produces two
// physical lines, and every index after it is off by one, so a caller
// that marks what it just appended marks the WRONG item (or a non-item
// line, which raises). The FILE this writes is byte-identical either way;
// only the returned indices differ, and only where Python's are wrong. So
// the indices are read back out of the rendered result instead of
// predicted.
func AppendNextItems(ws, slug string, items []string) ([]int, error) {
	if len(items) == 0 {
		return nil, nil
	}
	path := NextPath(ws, slug)
	var indices []int
	err := record.Locked(path, func() error {
		body, err := readLedger(path)
		if err != nil {
			if !os.IsNotExist(err) {
				return err
			}
			// Defensive, matching Python: EnsureProject normally does
			// this, but a partially-initialized project must not crash a
			// drain loop.
			if err := os.MkdirAll(ProjectDir(ws, slug), record.NewDirMode); err != nil {
				return err
			}
			body = "# NEXT — " + slug + "\n\n"
		}
		lines := pytext.SplitLines(body)
		start := len(lines)
		for _, it := range items {
			lines = append(lines, "- [ ] "+it)
		}
		rendered := renderLedger(lines)
		if err := record.AtomicWrite(path, rendered); err != nil {
			return err
		}
		_, parsed := parseLines(string(rendered))
		for _, it := range parsed {
			if it.Index >= start {
				indices = append(indices, it.Index)
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return indices, nil
}

// SelectNextItem is the first TODO item in file order, nil when there is
// none. File order, not priority order: the checklist IS the plan.
func SelectNextItem(ws, slug string) (*NextItem, error) {
	_, items, err := ParseNext(ws, slug)
	if err != nil {
		return nil, err
	}
	for i := range items {
		if items[i].State == StateTodo {
			it := items[i]
			return &it, nil
		}
	}
	return nil, nil
}

// MarkFirstTodoDone flips the first TODO item to DONE and returns it.
func MarkFirstTodoDone(ws, slug string) (*NextItem, error) {
	it, err := SelectNextItem(ws, slug)
	if err != nil || it == nil {
		return nil, err
	}
	if err := MarkItem(ws, slug, it.Index, StateDone); err != nil {
		return nil, err
	}
	return it, nil
}

// Counts is the per-state tally Python item_counts() returns.
type Counts struct{ Todo, Doing, Blocked, Done int }

func ItemCounts(ws, slug string) (Counts, error) {
	_, items, err := ParseNext(ws, slug)
	if err != nil {
		return Counts{}, err
	}
	var c Counts
	for _, it := range items {
		switch it.State {
		case StateTodo:
			c.Todo++
		case StateDoing:
			c.Doing++
		case StateBlocked:
			c.Blocked++
		case StateDone:
			c.Done++
		}
	}
	return c, nil
}

// Status is Python's ProjectStatus dataclass.
type Status struct {
	Slug     string
	Priority int
	Todo     int
	Doing    int
	Blocked  int
	Done     int
	NextItem *NextItem
}

func ProjectStatus(ws, slug string) (Status, error) {
	counts, err := ItemCounts(ws, slug)
	if err != nil {
		return Status{}, err
	}
	next, err := SelectNextItem(ws, slug)
	if err != nil {
		return Status{}, err
	}
	return Status{
		Slug:     slug,
		Priority: ProjectPriority(ws, slug),
		Todo:     counts.Todo,
		Doing:    counts.Doing,
		Blocked:  counts.Blocked,
		Done:     counts.Done,
		NextItem: next,
	}, nil
}
