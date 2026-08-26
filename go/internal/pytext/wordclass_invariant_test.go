package pytext

import (
	"regexp"
	"testing"
)

// Two spellings of one fact drift. These two tests are what makes the
// spelling free.
//
// The defect that prompted them: WordClassBody was `\p{L}\p{N}_` while
// digitSupplementBody — in the same file, ten lines up — hand-listed 80
// code points that Unicode 16.0 calls decimal digits and Go's 15.0 tables
// do not. So DigitClass matched U+10D40 and WordClass did not, and a
// caller doing a `\b` check with one and a class match with the other got
// two different answers about the same character. CPython has no spelling
// for that state: its `\d` is a subset of its `\w`, always.

func TestEveryDigitClassCodePointIsAlsoAWordClassCodePoint(t *testing.T) {
	digit := regexp.MustCompile(`^` + DigitClass + `$`)
	word := regexp.MustCompile(`^` + WordClass + `$`)
	bad := 0
	for r := rune(0); r < 0x110000; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue
		}
		if digit.MatchString(string(r)) && !word.MatchString(string(r)) {
			bad++
			if bad <= 8 {
				t.Errorf("%U is a DigitClass code point and not a WordClass "+
					"one; CPython's \\d is always a subset of its \\w", r)
			}
		}
	}
	if bad > 8 {
		t.Errorf("... and %d more", bad-8)
	}
}

func TestTheWordPredicateAndTheWordClassAgree(t *testing.T) {
	word := regexp.MustCompile(`^` + WordClass + `$`)
	bad := 0
	for r := rune(0); r < 0x110000; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue
		}
		if word.MatchString(string(r)) != IsWordChar(r) {
			bad++
			if bad <= 8 {
				t.Errorf("%U: WordClass says %v, IsWordChar says %v", r,
					word.MatchString(string(r)), IsWordChar(r))
			}
		}
	}
	if bad > 8 {
		t.Errorf("... and %d more", bad-8)
	}
}

// The supplement is written twice — as a class interior for the compiled
// patterns and as a range table for the predicate — because neither
// notation can serve the other cheaply. This pins them together, and it
// also asserts the table is still CARRYING something: when Go's unicode
// tables catch up to CPython's, both go dead, and a dead table that
// nothing notices is how a stale supplement starts adding code points
// back that the runtime already knows.
func TestTheTwoSpellingsOfTheWordSupplementAgree(t *testing.T) {
	sup := regexp.MustCompile(`^[` + wordSupplementBody + `]$`)
	inTable := func(r rune) bool {
		for _, rg := range wordSupplement {
			if r >= rg[0] && r <= rg[1] {
				return true
			}
		}
		return false
	}
	bad, live := 0, 0
	for r := rune(0); r < 0x110000; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue
		}
		if sup.MatchString(string(r)) != inTable(r) {
			bad++
			if bad <= 8 {
				t.Errorf("%U: the class says %v, the table says %v", r,
					sup.MatchString(string(r)), inTable(r))
			}
		}
		if inTable(r) {
			live++
		}
	}
	if bad > 8 {
		t.Errorf("... and %d more", bad-8)
	}
	if live != 5004 {
		t.Errorf("the word supplement covers %d code points, not the 5004 "+
			"measured against CPython 3.14 — regenerate it rather than "+
			"editing this number", live)
	}
}
