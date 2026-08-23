package pytext

import (
	"reflect"
	"testing"
)

// The whitespace predicate is the same 29 code points Python's
// str.isspace() and re's \s both cover — measured against CPython on this
// box over the full rune range. The four that Go's unicode.IsSpace omits
// are the ones that matter: they drive refusals and line trimming in a
// SHARED ledger, so being more lenient than Python here admits rows the
// other runtime strands.
func TestIsSpaceCoversPythonsSetExactly(t *testing.T) {
	want := []rune{
		0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20,
		0x85, 0xa0, 0x1680,
		0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
		0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
	}
	set := map[rune]bool{}
	for _, r := range want {
		set[r] = true
	}
	var got []rune
	for r := rune(0); r <= 0x10FFFF; r++ {
		if IsSpace(r) {
			got = append(got, r)
		}
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("whitespace set drifted from Python's:\ngot  %#x\nwant %#x", got, want)
	}
	// The four Go misses, called out by name so a future edit that reaches
	// for unicode.IsSpace alone fails here with the reason attached.
	for _, r := range []rune{0x1c, 0x1d, 0x1e, 0x1f} {
		if !IsSpace(r) {
			t.Errorf("U+%04X is whitespace to Python and must be here", r)
		}
	}
	if !set[0x1e] || set[0x200b] {
		t.Error("sanity: U+001E in, U+200B out")
	}
}

// str.splitlines() breaks on ten separators. The set is NOT the
// whitespace set: U+001C-1E break lines but U+001F does not, and U+00A0
// is whitespace that never breaks. In a ledger whose items are addressed
// by LINE NUMBER, using strings.Split(s, "\n") instead renumbers every
// item after the first exotic separator.
func TestSplitLinesMatchesPythonsSeparatorSet(t *testing.T) {
	breaks := []rune{0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029}
	for _, r := range breaks {
		got := SplitLines("a" + string(r) + "b")
		if !reflect.DeepEqual(got, []string{"a", "b"}) {
			t.Errorf("U+%04X must break a line: %q", r, got)
		}
	}
	// Whitespace that does NOT break, including the one separator that is
	// whitespace but not a break.
	for _, r := range []rune{0x09, 0x20, 0x1f, 0xa0, 0x2000, 0x3000} {
		got := SplitLines("a" + string(r) + "b")
		if len(got) != 1 {
			t.Errorf("U+%04X must not break a line: %q", r, got)
		}
	}
	// "\r\n" is ONE break, not two.
	if got := SplitLines("a\r\nb"); !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Errorf("CRLF is one break: %q", got)
	}
	// No trailing empty element for a document ending in a separator —
	// this is where splitlines differs from split, and it decides whether
	// an appended item lands at index N or N+1.
	if got := SplitLines("a\nb\n"); !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Errorf("trailing separator adds no element: %q", got)
	}
	if got := SplitLines(""); got != nil {
		t.Errorf("empty document has no lines: %q", got)
	}
	if got := SplitLines("\n"); !reflect.DeepEqual(got, []string{""}) {
		t.Errorf("a lone separator is one empty line: %q", got)
	}
}

func TestTrimRightRemovesTrailingBlankLines(t *testing.T) {
	// The behaviour NEXT.md's writer depends on: rstrip over a joined
	// document eats blank lines, not just spaces.
	if got := TrimRight("a\n\n  \nb\n  "); got != "a\n\n  \nb" {
		t.Fatalf("%q", got)
	}
}

func TestLowerHandlesTheDottedCapitalI(t *testing.T) {
	if got := Lower("İstanbul"); got != "i̇stanbul" {
		t.Fatalf("%q (% x)", got, []rune(got))
	}
	if got := Lower("PLAIN"); got != "plain" {
		t.Fatalf("the common path must be untouched: %q", got)
	}
}

func TestStripUsesPythonsWhitespaceSet(t *testing.T) {
	if got := Strip("\x1c hi \x1f"); got != "hi" {
		t.Fatalf("%q", got)
	}
	if got := Strip("\u00a0hi\u00a0"); got != "hi" {
		t.Fatalf("nbsp is whitespace to Python: %q", got)
	}
}
