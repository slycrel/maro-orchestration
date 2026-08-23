package orch

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// seed writes a NEXT.md verbatim and returns the workspace root.
func seed(t *testing.T, slug, body string) string {
	t.Helper()
	ws := t.TempDir()
	if err := os.MkdirAll(ProjectDir(ws, slug), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(NextPath(ws, slug), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

// The item pattern, case for case, against CPython's answers measured on
// this box. Every row here is a place the obvious Go port gets a
// different answer: writing the pattern with Go's \s loses the NBSP and
// vertical-tab indents and the NBSP trim, and counting the indent with
// len() reports 2 bytes for a 1-character NBSP.
func TestItemPatternMatchesPythonCaseForCase(t *testing.T) {
	type want struct {
		match  bool
		indent int
		state  string
		text   string
	}
	cases := []struct {
		line string
		want want
	}{
		// A checkbox with NOTHING after it is not an item: the text group
		// requires one character. One trailing space and it IS one, with
		// empty text. Both runtimes must agree or a drain loop and a
		// status count disagree about how much work is left.
		{"- [ ]", want{match: false}},
		{"- [ ] ", want{true, 0, " ", ""}},
		{"- [ ]   ", want{true, 0, " ", ""}},
		{"  - [x] hi  ", want{true, 2, "x", "hi"}},
		{"-[~]a", want{true, 0, "~", "a"}},
		{"- [ ] a\tb ", want{true, 0, " ", "a\tb"}},
		{" - [ ] x", want{true, 1, " ", "x"}},
		{"- [X] Y", want{true, 0, "X", "Y"}},
		// A non-breaking space is indent to Python and invisible to Go's
		// \s — and the indent is ONE character, not its two UTF-8 bytes.
		{"\u00a0- [ ] nbsp-indent", want{true, 1, " ", "nbsp-indent"}},
		{"- [ ] tail\u00a0", want{true, 0, " ", "tail"}},
		// U+001C is whitespace to the pattern but is ALSO a line break to
		// splitlines, so this shape reaches the pattern only directly —
		// which is the point: the whitespace set and the line-break set
		// are different sets, and conflating them gets one of them wrong.
		{"- [ ] a\x1cb", want{true, 0, " ", "a\x1cb"}},
		{"\x0b- [ ] vtab-indent", want{true, 1, " ", "vtab-indent"}},
		{"* [ ] not-a-dash", want{match: false}},
		{"- [?] bad-state", want{match: false}},
	}
	for _, c := range cases {
		m := itemRe.FindStringSubmatch(c.line)
		if (m != nil) != c.want.match {
			t.Errorf("%q: match=%v, want %v", c.line, m != nil, c.want.match)
			continue
		}
		if m == nil {
			continue
		}
		indent := len([]rune(m[1]))
		if indent != c.want.indent || m[2] != c.want.state ||
			pytext.Strip(m[3]) != c.want.text {
			t.Errorf("%q: (%d, %q, %q), want (%d, %q, %q)", c.line,
				indent, m[2], pytext.Strip(m[3]),
				c.want.indent, c.want.state, c.want.text)
		}
	}
}

// The table above exercises the PATTERN; this exercises the parse built
// on it, where two of the pattern's answers are transformed before they
// reach a caller. U+001F is the useful character here: it is whitespace
// to Python and NOT a line break, so unlike the form feed it can sit
// inside a line and be trimmed from one.
func TestParsedItemsCountIndentInCharactersAndStripTheirText(t *testing.T) {
	ws := seed(t, "p", "# NEXT\n\n"+
		"\u00a0\u00a0- [ ] two nbsp indent\n"+
		"\x1f- [ ] sep indent\n"+
		"- [ ]   \n")
	_, items, err := ParseNext(ws, "p")
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 3 {
		t.Fatalf("want 3 items, got %d: %+v", len(items), items)
	}
	// Two non-breaking spaces are FOUR bytes and TWO characters, and
	// Python's len() reports the characters.
	if items[0].Indent != 2 {
		t.Errorf("nbsp indent counted in bytes: %d", items[0].Indent)
	}
	// U+001F is in Python's \s but in none of Go's whitespace shorthands.
	if items[1].Indent != 1 || items[1].Text != "sep indent" {
		t.Errorf("separator indent: %+v", items[1])
	}
	// A checkbox followed only by whitespace is an item whose text is
	// empty — the pattern hands back one whitespace character (the text
	// group needs at least one) and the strip removes it.
	if items[2].Text != "" {
		t.Errorf("an all-whitespace item must strip to empty: %q", items[2].Text)
	}
}

// An item's identity is its LINE NUMBER, so the ledger must be split the
// way Python splits it. A form feed in the file shifts every index after
// it — the difference between marking the item a caller meant and
// marking the next one.
func TestLineNumberingFollowsPythonsSplitlines(t *testing.T) {
	// The blank-looking break between the two items is a form feed, which
	// Python counts as a line break and strings.Split(s, "\n") does not.
	ws := seed(t, "p", "# NEXT\n\n- [ ] first\x0c- [ ] second\n")
	_, items, err := ParseNext(ws, "p")
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 {
		t.Fatalf("want 2 items, got %d: %+v", len(items), items)
	}
	if items[0].Index != 2 || items[1].Index != 3 {
		t.Fatalf("indices %d,%d — a naive \\n split would report 2,2",
			items[0].Index, items[1].Index)
	}
	if items[1].Text != "second" {
		t.Fatalf("text %q", items[1].Text)
	}
}

// Marking is a read-modify-write of a file the other runtime also writes,
// so it runs under NEXT.md's lock. The visible contract: exactly the
// named line changes, its state character is replaced, and a stray
// bracket pair later in the text is left alone.
func TestMarkItemRewritesOnlyTheCheckbox(t *testing.T) {
	ws := seed(t, "p", "# NEXT\n\n- [ ] see [a] here\n- [ ] other\n")
	if err := MarkItem(ws, "p", 2, StateDone); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(NextPath(ws, "p"))
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "# NEXT\n\n- [x] see [a] here\n- [ ] other\n" {
		t.Fatalf("%q", raw)
	}
	if err := MarkItem(ws, "p", 2, "?"); err == nil {
		t.Fatal("an invalid state must be refused")
	}
	if err := MarkItem(ws, "p", 99, StateDone); err == nil {
		t.Fatal("an unknown index must be refused")
	}
}

// write_next_lines rstrips the whole joined document before writing, so a
// ledger's trailing blank lines do not survive a mark. Reproduced because
// the two runtimes rewrite each other's files: if Go preserved the tail
// and Python did not, every alternating write would churn the file.
func TestWriteNormalizesTheTrailingBlankLines(t *testing.T) {
	ws := seed(t, "p", "# NEXT\n\n- [ ] a\n\n\n   \n")
	if err := MarkItem(ws, "p", 2, StateDoing); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(NextPath(ws, "p"))
	if string(raw) != "# NEXT\n\n- [~] a\n" {
		t.Fatalf("%q", raw)
	}
}

// Python computes the returned indices as range(len(lines_before), …),
// which assumes one item per line. An item whose text carries a line
// separator produces two physical lines and every later index is wrong —
// so a caller that marks what it just appended marks a non-item and
// raises. The FILE is byte-identical either way; only the indices differ.
func TestAppendedIndicesSurviveAnItemContainingALineBreak(t *testing.T) {
	ws := seed(t, "p", "# NEXT\n\n")
	got, err := AppendNextItems(ws, "p", []string{"first\nstray continuation", "second"})
	if err != nil {
		t.Fatal(err)
	}
	// Physical lines: 0 "# NEXT", 1 "", 2 "- [ ] first", 3 "stray …",
	// 4 "- [ ] second". Python would return [2, 3] and 3 is not an item.
	if !reflect.DeepEqual(got, []int{2, 4}) {
		t.Fatalf("indices %v — index-by-arithmetic would say [2 3]", got)
	}
	for _, idx := range got {
		if _, err := GetItem(ws, "p", idx); err != nil {
			t.Fatalf("index %d does not name an item: %v", idx, err)
		}
	}
	// And the ordinary case still lands where Python says it does.
	got, err = AppendNextItems(ws, "p", []string{"third"})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []int{5}) {
		t.Fatalf("plain append: %v", got)
	}
}

// A missing ledger creates a minimal one rather than crashing a drain
// loop mid-sweep (Python's defensive branch for partially-initialized
// projects).
func TestAppendCreatesAMinimalLedgerWhenNoneExists(t *testing.T) {
	ws := t.TempDir()
	if err := os.MkdirAll(ProjectDir(ws, "p"), 0o755); err != nil {
		t.Fatal(err)
	}
	idx, err := AppendNextItems(ws, "p", []string{"only"})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(NextPath(ws, "p"))
	if string(raw) != "# NEXT — p\n\n- [ ] only\n" {
		t.Fatalf("%q", raw)
	}
	if !reflect.DeepEqual(idx, []int{2}) {
		t.Fatalf("%v", idx)
	}
}

// Python's read_text(encoding="utf-8") is strict, so a byte-damaged
// ledger raises there. Admitting it here would let this runtime
// renumber, rewrite and mark items in a file the other runtime has
// stopped being able to open at all.
func TestLedgerReadRefusesInvalidUTF8(t *testing.T) {
	ws := seed(t, "p", "")
	if err := os.WriteFile(NextPath(ws, "p"),
		[]byte("# NEXT\n\n- [ ] caf\xe9\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, _, err := ParseNext(ws, "p"); err == nil {
		t.Fatal("a non-UTF-8 ledger must be refused, as Python refuses it")
	}
	if err := MarkItem(ws, "p", 2, StateDone); err == nil {
		t.Fatal("and marking it must be refused too")
	}
	if err := MarkItem(ws, "missing", 0, StateDone); err == nil {
		t.Fatal("a project with no NEXT.md must be refused")
	}
}

func TestCountsAndSelectionReadFileOrder(t *testing.T) {
	ws := seed(t, "p", "# NEXT\n\n- [x] done\n- [!] blocked\n- [ ] first todo\n"+
		"- [~] doing\n- [ ] second todo\n")
	c, err := ItemCounts(ws, "p")
	if err != nil {
		t.Fatal(err)
	}
	if (c != Counts{Todo: 2, Doing: 1, Blocked: 1, Done: 1}) {
		t.Fatalf("%+v", c)
	}
	it, err := SelectNextItem(ws, "p")
	if err != nil || it == nil {
		t.Fatalf("%v %v", it, err)
	}
	if it.Text != "first todo" {
		t.Fatalf("the checklist IS the plan — file order decides: %q", it.Text)
	}
	// GetItem addresses by line number, which is the only identity an item
	// has: returning "some item" for an index that names a different one
	// would mark the wrong work done.
	got, err := GetItem(ws, "p", 5)
	if err != nil {
		t.Fatal(err)
	}
	if got.Text != "doing" || got.State != StateDoing {
		t.Fatalf("index 5 names the DOING item: %+v", got)
	}
	if _, err := GetItem(ws, "p", 1); err == nil {
		t.Fatal("a line that is not an item must be refused, not rounded to one")
	}
	st, err := ProjectStatus(ws, "p")
	if err != nil {
		t.Fatal(err)
	}
	if st.Blocked != 1 || st.NextItem == nil || st.NextItem.Text != "first todo" {
		t.Fatalf("%+v", st)
	}
	// An uppercase X is normalized to the lowercase state on read, so a
	// hand-edited "[X]" counts as done in both runtimes.
	ws2 := seed(t, "p", "- [X] shouty\n")
	c2, _ := ItemCounts(ws2, "p")
	if c2.Done != 1 {
		t.Fatalf("[X] must count as done: %+v", c2)
	}
}

func TestMarkFirstTodoDone(t *testing.T) {
	ws := seed(t, "p", "- [x] a\n- [ ] b\n- [ ] c\n")
	it, err := MarkFirstTodoDone(ws, "p")
	if err != nil || it == nil {
		t.Fatalf("%v %v", it, err)
	}
	if it.Text != "b" {
		t.Fatalf("%q", it.Text)
	}
	raw, _ := os.ReadFile(filepath.Join(ProjectDir(ws, "p"), "NEXT.md"))
	if string(raw) != "- [x] a\n- [x] b\n- [ ] c\n" {
		t.Fatalf("%q", raw)
	}
}
