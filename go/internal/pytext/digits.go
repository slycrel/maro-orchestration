package pytext

import (
	"strings"
	"unicode"
)

// The decimal-digit VALUE fold: what code point means what number.
//
// This is a third, separate hazard from SpaceClass/DigitClass, and the
// distinction is worth stating because the three look interchangeable and
// are not. DigitClass decides MEMBERSHIP inside a regex — is this rune a
// digit at all. This file decides VALUE — which number is it. Two callers
// need the value and neither can get it from Go's standard library:
//
//   - record's float() coercion, which must reproduce CPython's
//     PyUnicode_TransformDecimalAndSpaceToASCII before float() ever sees
//     the text;
//   - playbook's alarm dates, because CPython's strptime accepts all 760
//     decimal digits for %Y/%m/%d and maps each by its
//     unicodedata.decimal value, where Go's time.Parse takes ASCII only.
//
// Measured: `datetime.strptime("200"+chr(c)+"-01-01", "%Y-%m-%d")` parses
// for every one of the 760 code points Python's `re` matches with `\d`,
// and the resulting year is 2000 + unicodedata.decimal(chr(c)) in every
// case. Mixed scripts are accepted freely — "٢0٠1-01-01" parses to 2001.
//
// It lives here, once, rather than in either caller. Three copies of
// pyRepr in this port each carried the same two defects, and fixing one
// left the others feeding the same shared files.

// digitSupplement covers the code points CPython folds and Go's table
// does not: Go ships unicode 15.0.0 and CPython here has 16.0.0, so 80 Nd
// code points in 7 ranges are digits THERE and not HERE.
//
// These are the same seven ranges as reclass.go's digitSupplementBody,
// deliberately spelled twice: that one is regex-class syntax deciding
// membership and this one is range literals recovering a value. A single
// table would have to serve both without either caller being able to see
// which behaviour it was getting. Both are re-derived from CPython by
// their own sweeps, so they fail together if the skew ever moves.
var digitSupplement = [...][2]rune{
	{0x10D40, 0x10D49}, // Garay
	{0x116D0, 0x116E3}, // Myanmar Pao / Eastern Pwo Karen (two 10-blocks)
	{0x11BF0, 0x11BF9}, // Sunuwar
	{0x16130, 0x16139}, // Gurung Khema
	{0x16D70, 0x16D79}, // Kirat Rai
	{0x1CCF0, 0x1CCF9}, // Outlined
	{0x1E5F1, 0x1E5FA}, // Ol Onal
}

// DecimalDigit returns r's decimal value as an ASCII byte, and whether r
// is a decimal digit at all.
//
// The value is recovered by walking back to the start of the rune's run
// and taking the offset mod 10. Measured over Go's OWN digit table: 64
// maximal runs, 63 of length 10 and one of length 50 (U+1D7CE–U+1D7FF),
// and in all 680 cases the digit value equals (offset from run start) mod
// 10 — zero mismatches. The walk-back is exact, not approximate, and the
// modulo is load-bearing for the length-50 run.
func DecimalDigit(r rune) (byte, bool) {
	if unicode.IsDigit(r) {
		start := r
		for start > 0 && unicode.IsDigit(start-1) {
			start--
		}
		return byte('0' + (r-start)%10), true
	}
	for _, rg := range digitSupplement {
		if r >= rg[0] && r <= rg[1] {
			return byte('0' + (r-rg[0])%10), true
		}
	}
	return 0, false
}

// FoldDecimals rewrites every non-ASCII decimal digit in s as its ASCII
// equivalent, leaving everything else — including ASCII digits, which
// are already themselves — untouched.
//
// A string with no non-ASCII digits is returned unchanged, so the common
// path allocates nothing.
func FoldDecimals(s string) string {
	if !strings.ContainsFunc(s, func(r rune) bool {
		if r <= unicode.MaxASCII {
			return false
		}
		_, ok := DecimalDigit(r)
		return ok
	}) {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if r > unicode.MaxASCII {
			if d, ok := DecimalDigit(r); ok {
				b.WriteByte(d)
				continue
			}
		}
		b.WriteRune(r)
	}
	return b.String()
}
