package pytext

import "strings"

// Character classes for porting Python REGEXES.
//
// This is a separate hazard from Strip and IsSpace, and the difference has
// bitten the port before in its non-regex form. Go's `regexp` reads `\s` as
// exactly `[\t\n\f\r ]` (five code points) and `\d` as `[0-9]` (ten).
// Python's `re` on a str pattern reads `\s` as its full 29-code-point
// whitespace set and `\d` as every Unicode decimal digit — 760 of them on
// this box. A pattern transcribed character-for-character from Python
// therefore matches a DIFFERENT language in Go, silently, and the places
// this port needs it (playbook.py's alarm and attribution patterns) decide
// whether an entry is replaced in place or appended beside its twin.
//
// Both classes are MEASURED against CPython by the sweeps in
// reclass_test.go, not transcribed.

// spaceClassBody is the interior of a character class matching what
// Python's `re` matches with `\s`. Exported only through SpaceClass and
// NotClass, so the two can never drift apart.
//
// Measured: 29 code points in 10 ranges, and identical to `str.isspace()`
// — so this and IsSpace describe one set in two notations. The four that
// separate it from Go's `unicode.White_Space` are U+001C..U+001F.
const spaceClassBody = `\x{9}-\x{D}\x{1C}-\x{20}\x{85}\x{A0}\x{1680}` +
	`\x{2000}-\x{200A}\x{2028}-\x{2029}\x{202F}\x{205F}\x{3000}`

// digitSupplementBody covers the decimal digits CPython knows and Go's
// `\p{Nd}` does not: Go ships unicode 15.0.0 and CPython here has 16.0.0,
// so 80 code points in 7 ranges are digits THERE and not HERE.
//
// This is the same skew, and the same seven ranges, that
// record.digitSupplement carries for float() coercion. Deliberately not
// shared: that one folds digits to their VALUES and this one only decides
// membership, and a single table would have to serve both without either
// caller being able to see which behaviour it was getting. The sweeps on
// both sides re-derive from CPython, so they fail together if the skew
// ever moves.
const digitSupplementBody = `\x{10D40}-\x{10D49}\x{116D0}-\x{116E3}` +
	`\x{11BF0}-\x{11BF9}\x{16130}-\x{16139}\x{16D70}-\x{16D79}` +
	`\x{1CCF0}-\x{1CCF9}\x{1E5F1}-\x{1E5FA}`

// SpaceClass matches one code point that Python's `re` matches with `\s`.
// Use it in place of `\s`, never alongside it.
const SpaceClass = `[` + spaceClassBody + `]`

// DigitClass matches one code point that Python's `re` matches with `\d`.
const DigitClass = `[\p{Nd}` + digitSupplementBody + `]`

// NotClass builds a NEGATED character class that excludes Python's `\s`
// plus whatever extra characters the caller names, which is the shape
// Python patterns like `[^\s)·]` take.
//
// The argument is spliced verbatim into a character class, so a caller
// passing `]` or `-` or `^` in the wrong position writes a different
// pattern than it means. Callers here pass fixed literals; this is not a
// user-input path and must not become one.
func NotClass(extra string) string {
	return `[^` + spaceClassBody + escapeInClass(extra) + `]`
}

// escapeInClass escapes the three characters that change a character
// class's meaning depending on position.
func escapeInClass(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch r {
		case ']', '\\', '^', '-':
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}
