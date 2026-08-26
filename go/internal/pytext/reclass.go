package pytext

import (
	"strings"
	"unicode"
)

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

// SpaceClassBody is the interior of a character class matching what
// Python's `re` matches with `\s` — the body WITHOUT its brackets, which
// is what a pattern needing \s inside a larger class (Python `[_\s]`)
// has to splice in. SpaceClass and NotClass build on it, so they can
// never drift apart.
//
// Measured: 29 code points in 10 ranges, and identical to `str.isspace()`
// — so this and IsSpace describe one set in two notations. The four that
// separate it from Go's `unicode.White_Space` are U+001C..U+001F.
const SpaceClassBody = `\x{9}-\x{D}\x{1C}-\x{20}\x{85}\x{A0}\x{1680}` +
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
const SpaceClass = `[` + SpaceClassBody + `]`

// DigitClass matches one code point that Python's `re` matches with `\d`.
const DigitClass = `[\p{Nd}` + digitSupplementBody + `]`

// WordClassBody is the interior of a character class matching what
// Python's `re` matches with `\w` on a str pattern. Go's `regexp` reads
// `\w` as ASCII `[0-9A-Za-z_]`; Python's is `str.isalnum() or "_"`.
//
// Measured over the full rune range against CPython on this box:
//
//	CPython \w:                    142940 code points
//	matched by Go but NOT CPython:      0
//	matched by CPython but not Go:   5004
//
// Zero false positives — the class is exactly right — and the 5004 are
// the SAME Go-15.0-vs-CPython-16.0 table skew digitSupplementBody
// documents, in its letter half. They are not enumerated here: unlike
// the 80 digits, they are five thousand code points across dozens of
// newly-added scripts, and the honest fix is a Go toolchain with newer
// tables, not a hand-copied list that rots. Named residual, not a
// silent one.
//
// The body is exported too: a pattern that needs \w INSIDE a larger
// class (Python `[\w-]`) cannot use WordClass, which is already
// bracketed.
//
// UPDATE (artifactcheck r1, 2026-08-26). The paragraph above used to end
// "the honest fix is a Go toolchain with newer tables, not a hand-copied
// list that rots", and the sentence before it claimed the 5004 were "all
// letters in recently-added scripts". Both were wrong, and the second one
// was checkable from inside this file: 80 of the 5004 are Unicode 16.0
// DECIMAL DIGITS, and digitSupplementBody five lines up already
// hand-copies exactly those — so DigitClass matched U+10D40 while
// WordClass did not, which is a state CPython has no spelling for.
//
// The argument against a hand-copied list had also already been overruled
// twice, with measurements, by internal/metrics and internal/skills, each
// of which carried its own byte-identical copy of the full 27-range
// supplement. Three copies of one table is not restraint. The table lives
// here now, once, and the two predicates that were duplicating it call
// IsWordChar instead.
const WordClassBody = `\p{L}\p{N}_` + wordSupplementBody

// wordSupplementBody is the Unicode 16 minus Unicode 15 delta for `\w`,
// as a character-class interior. It is the same 27 ranges as
// wordSupplement below, in the other notation, and
// TestTheTwoSpellingsOfTheWordSupplementAgree pins them to each other —
// two spellings of one fact is exactly how the first version of this drifted.
//
// REGENERATE (do not hand-edit) when Go's unicode tables move. The skew
// sweep re-derives the whole set from CPython, so a stale table fails
// loudly rather than quietly narrowing.
const wordSupplementBody = `\x{01C89}-\x{01C8A}\x{0A7CB}-\x{0A7CD}\x{0A7DA}-\x{0A7DC}` +
	`\x{105C0}-\x{105F3}\x{10D40}-\x{10D65}\x{10D6F}-\x{10D85}` +
	`\x{10EC2}-\x{10EC4}\x{11380}-\x{11389}\x{1138B}` +
	`\x{1138E}\x{11390}-\x{113B5}\x{113B7}` +
	`\x{113D1}\x{113D3}\x{116D0}-\x{116E3}` +
	`\x{11BC0}-\x{11BE0}\x{11BF0}-\x{11BF9}\x{13460}-\x{143FA}` +
	`\x{16100}-\x{1611D}\x{16130}-\x{16139}\x{16D40}-\x{16D6C}` +
	`\x{16D70}-\x{16D79}\x{18CFF}\x{1CCF0}-\x{1CCF9}` +
	`\x{1E5D0}-\x{1E5ED}\x{1E5F0}-\x{1E5FA}\x{2EBF0}-\x{2EE5D}`

// wordSupplement is wordSupplementBody as ranges, for the PREDICATE half.
// A compiled class cannot answer "is this rune a word character" without
// a regexp match per rune, and boundaryAt asks that question in a loop.
var wordSupplement = [...][2]rune{
	{0x01C89, 0x01C8A}, // Cyrillic Tje
	{0x0A7CB, 0x0A7CD}, // Latin extensions
	{0x0A7DA, 0x0A7DC}, // Latin extensions
	{0x105C0, 0x105F3}, // Todhri
	{0x10D40, 0x10D65}, // Garay
	{0x10D6F, 0x10D85}, // Garay
	{0x10EC2, 0x10EC4}, // Arabic Extended-C
	{0x11380, 0x11389}, // Tulu-Tigalari
	{0x1138B, 0x1138B}, // Tulu-Tigalari
	{0x1138E, 0x1138E}, // Tulu-Tigalari
	{0x11390, 0x113B5}, // Tulu-Tigalari
	{0x113B7, 0x113B7}, // Tulu-Tigalari
	{0x113D1, 0x113D1}, // Tulu-Tigalari
	{0x113D3, 0x113D3}, // Tulu-Tigalari
	{0x116D0, 0x116E3}, // Myanmar Extended-C
	{0x11BC0, 0x11BE0}, // Sunuwar
	{0x11BF0, 0x11BF9}, // Sunuwar
	{0x13460, 0x143FA}, // Egyptian Hieroglyphs Extended-A (3,995)
	{0x16100, 0x1611D}, // Gurung Khema
	{0x16130, 0x16139}, // Gurung Khema
	{0x16D40, 0x16D6C}, // Kirat Rai
	{0x16D70, 0x16D79}, // Kirat Rai
	{0x18CFF, 0x18CFF}, // Khitan Small Script
	{0x1CCF0, 0x1CCF9}, // Outlined digits
	{0x1E5D0, 0x1E5ED}, // Ol Onal
	{0x1E5F0, 0x1E5FA}, // Ol Onal
	{0x2EBF0, 0x2EE5D}, // CJK Extension I (622)
}

// WordSupplementRanges is the supplement as inclusive rune ranges, copied
// so a caller cannot edit the table through it.
//
// It exists for the SKEW TESTS in the packages that used to keep their own
// copies: each of them sweeps a representative from every range against
// CPython, and a fixture set drawn from anywhere else would not notice a
// range going stale. Not for matching — use IsWordChar or WordClass.
func WordSupplementRanges() [][2]rune {
	out := make([][2]rune, len(wordSupplement))
	copy(out, wordSupplement[:])
	return out
}

// IsWordChar is Python's `\w` as a predicate: `str.isalnum() or "_"`.
//
// It is the same set WordClass matches, and that is not a coincidence to
// be maintained by hand — TestTheWordPredicateAndTheWordClassAgree sweeps
// the whole rune range and fails if they ever part company. A boundary
// check and a class match disagreeing about one code point is a defect
// with no CPython counterpart, and it is the defect this function was
// added to make impossible.
func IsWordChar(r rune) bool {
	if r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) {
		return true
	}
	lo, hi := 0, len(wordSupplement)-1
	for lo <= hi {
		mid := (lo + hi) / 2
		switch {
		case r < wordSupplement[mid][0]:
			hi = mid - 1
		case r > wordSupplement[mid][1]:
			lo = mid + 1
		default:
			return true
		}
	}
	return false
}

// WordClass matches one code point that Python's `re` matches with `\w`.
const WordClass = `[` + WordClassBody + `]`

// NotWordClass matches one code point that Python's `re` matches with
// `\W`.
const NotWordClass = `[^` + WordClassBody + `]`

// WordStart and WordEnd stand in for Python's Unicode-aware `\b` at the
// two ends of a literal word. Go's `\b` is ASCII-only, so it fires
// between a non-ASCII letter and an ASCII word where Python's does not:
// measured, `\bplan\b` matches "研究plan" in Go and NOT in CPython, which
// flips the classifier's LANE (adversarial mission-r6 MEDIUM).
//
// RE2 has no lookaround, so these CONSUME the boundary character, and
// that is a much sharper constraint than the first version of this
// comment said. It listed match OFFSETS and two adjacent matches sharing
// a boundary. It missed the case that actually broke, and the very first
// call site was an instance of it (adversarial mission-r7 HIGH):
//
//	\b(?:save|write)\b[^.;\n]{0,40}\bto\s+
//
// Two of those \b are INTERIOR. Python's are zero-width, so the space in
// "write to out.json" is available to the {0,40} window. A consuming
// WordEnd eats it, the window then starts at "to", and the WordStart
// before "to" has nothing left to consume -- so the whole pattern fails
// on the most ordinary input it exists to match. Measured:
// _requires_file_output("write to out.json") is True in CPython and was
// False here, which routes the message to a different LANE.
//
// The rule, stated properly:
//
//	WordStart/WordEnd are safe ONLY at the two ENDS of a pattern, in a
//	boolean predicate. An interior boundary -- one with more pattern
//	after it -- must be folded into whatever follows, using
//	NotWordClassPlus. They are also wrong wherever the caller needs
//	match offsets or the matched TEXT (FindString, FindAllStringIndex,
//	ReplaceAllString), because the consumed character is part of the
//	match.
//
// Folding an interior boundary into a following {0,n} window looks like
// this, and there is no shortcut for it -- the arithmetic on n is part
// of the translation:
//
//	Python  \bKEYWORD\b[^.;\n]{0,40}\bto
//	RE2     WordStart KEYWORD (?:NW|NW [^.;\n]{0,38} NW) to
//	        where NW = NotWordClassPlus(".;\n")
//
// The window must be non-empty (KEYWORD immediately followed by "to" has
// no boundary between them), its first character carries the boundary
// after KEYWORD, and its last carries the boundary before "to" -- the
// same character when the window is one wide.
const WordStart = `(?:^|` + NotWordClass + `)`
const WordEnd = `(?:$|` + NotWordClass + `)`

// NotWordClassPlus builds a NEGATED character class excluding Python's
// `\w` plus whatever extra characters the caller names -- the shape a
// pattern needs when it must express "this position is a word boundary"
// INSIDE a window it is also consuming.
//
// See WordStart/WordEnd's doc for why that case cannot use them.
func NotWordClassPlus(extra string) string {
	return `[^` + WordClassBody + escapeInClass(extra) + `]`
}

// NotClass builds a NEGATED character class that excludes Python's `\s`
// plus whatever extra characters the caller names, which is the shape
// Python patterns like `[^\s)·]` take.
//
// The argument is spliced verbatim into a character class, so a caller
// passing `]` or `-` or `^` in the wrong position writes a different
// pattern than it means. Callers here pass fixed literals; this is not a
// user-input path and must not become one.
func NotClass(extra string) string {
	return `[^` + SpaceClassBody + escapeInClass(extra) + `]`
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
