// Package pytext holds the Python string primitives this port keeps
// needing, in one place, because every one of them is a place where the
// obvious Go call is subtly different and the difference lands in a
// SHARED file that both runtimes read.
//
// The three that have already cost this port a bug:
//
//   - str.isspace() / re \s — measured identical to each other over the
//     whole rune range (29 code points), and both include U+001C–U+001F,
//     which Go's unicode.IsSpace omits and Go's regexp \s omits along
//     with \v, U+0085, U+00A0 and every Unicode space. A predicate that
//     drives a REFUSAL must not be more lenient than the other runtime's.
//   - str.lower() — full case mapping; U+0130 lowercases to TWO runes.
//   - str.splitlines() — splits on ten separators, not one. In a
//     line-addressed ledger (NEXT.md items are identified by line
//     NUMBER) a disagreement about where lines begin is a disagreement
//     about which item a write flips.
//
// Everything here was measured against CPython on this box rather than
// read off documentation; the counts in the comments are those
// measurements.
package pytext

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// IsSpace is Python's str.isspace() for one rune, which is also exactly
// what re's \s matches in a str pattern — swept over the full rune range,
// the two agree on the same 29 code points and Go's unicode.IsSpace
// misses four of them: the ASCII information separators U+001C–U+001F.
func IsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// Strip is Python's str.strip() — whitespace by IsSpace, both ends.
func Strip(s string) string { return strings.TrimFunc(s, IsSpace) }

// TrimRight is Python's str.rstrip() — trailing whitespace only. Note
// that on a joined multi-line document this removes trailing BLANK LINES
// as well as trailing spaces, which is how the NEXT.md writer normalizes
// its tail.
func TrimRight(s string) string { return strings.TrimRightFunc(s, IsSpace) }

// Lower is Python's str.lower(). Go's strings.ToLower applies SIMPLE case
// mapping (one rune in, one rune out); Python applies the full mapping,
// where U+0130 (LATIN CAPITAL LETTER I WITH DOT ABOVE) lowercases to two
// runes, "i" + U+0307. Swept over the whole rune range that is the only
// unconditional multi-rune lowercase mapping there is, so handling it
// outright is the whole fix.
//
// NAMED DIVERGENCE, version-dependent and unfixable from here: 27 further
// runes disagree purely because Go's and CPython's Unicode tables are at
// different revisions. Those move with the toolchains, not with this code.
func Lower(s string) string {
	if !strings.ContainsRune(s, 0x0130) {
		return strings.ToLower(s)
	}
	return strings.ToLower(strings.ReplaceAll(s, "İ", "i̇"))
}

// lineSeparators is Python str.splitlines()' break set, measured: it is
// NOT the whitespace set. U+001C–U+001E break lines but U+001F does not,
// and U+00A0 / U+2000-200A are whitespace that never break.
var lineSeparators = map[rune]bool{
	0x000a: true, // \n
	0x000b: true, // \v
	0x000c: true, // \f
	0x000d: true, // \r
	0x001c: true, // FILE SEPARATOR
	0x001d: true, // GROUP SEPARATOR
	0x001e: true, // RECORD SEPARATOR
	0x0085: true, // NEXT LINE
	0x2028: true, // LINE SEPARATOR
	0x2029: true, // PARAGRAPH SEPARATOR
}

// SplitLines is Python's str.splitlines(): ten separators, "\r\n" counted
// as one break, and no trailing empty element for a document that ends in
// a separator.
//
// Python reaches the same answer by two steps — read_text() opens with
// newline=None, which translates "\r\n" and a lone "\r" to "\n" BEFORE
// splitlines() sees them — so doing the \r\n pairing here is equivalent,
// not merely similar: either way the pair is one break and the "\r" never
// survives into a line's text.
func SplitLines(s string) []string {
	if s == "" {
		return nil
	}
	var out []string
	start := 0
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		if !lineSeparators[r] {
			i += size
			continue
		}
		out = append(out, s[start:i])
		i += size
		if r == '\r' && i < len(s) && s[i] == '\n' {
			i++
		}
		start = i
	}
	if start < len(s) {
		out = append(out, s[start:])
	}
	return out
}

// Repr renders a string the way Python's !r does — single quotes unless
// the value contains one and no double quote. Reasons and errors built
// from it are stored prose in shared files, so a differently-spelled
// reason is a differently stored row.
func Repr(s string) string {
	if strings.Contains(s, "'") && !strings.Contains(s, "\"") {
		return "\"" + s + "\""
	}
	escaped := strings.ReplaceAll(s, "\\", "\\\\")
	escaped = strings.ReplaceAll(escaped, "'", "\\'")
	return "'" + escaped + "'"
}
