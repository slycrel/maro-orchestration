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
	"fmt"
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

// Repr renders a string the way Python's repr() / !r does. Reasons and
// errors built from it are stored prose in shared files, so a
// differently-spelled reason is a differently stored row.
//
// WHY THIS IS NO LONGER THE TWO-REPLACEMENT VERSION (adversarial r5,
// MEDIUM). That version had two defects, and the second one CORRUPTS a
// file Python then has to read:
//
//   - the double-quote branch escaped nothing at all, so a value holding
//     both an apostrophe and a backslash came back with the backslash
//     bare. `it's a \ backslash` rendered as "it's a \ backslash", which
//     Python reads back as `it's a ` followed by an escape of whatever
//     came next;
//   - NEITHER branch escaped control or non-printable characters, so a
//     NUL, an ESC or a U+2028 line separator went out raw. In SKILL.md —
//     written through this function by skills/export_md.go and parsed by
//     Python's own loader — a raw newline inside a quoted value ends the
//     value early, and a raw NUL is not representable at all.
//
// Python's actual rule, measured on CPython 3.14.3 over the whole rune
// range rather than read off documentation:
//
//   - the quote is ' unless the value contains ' and no ", then ";
//   - \ and the chosen quote are backslash-escaped;
//   - \t, \n and \r get their short forms;
//   - anything failing str.isprintable() becomes \xXX (< 0x100), \uXXXX
//     (< 0x10000) or \UXXXXXXXX, hex LOWERCASE;
//   - str.isprintable() is false for categories Cc, Cf, Cs, Co, Cn, Zl,
//     Zp and Zs, with U+0020 the single exception — confirmed by sweep as
//     the only printable Zs.
//
// NAMED DIVERGENCE, measured, and it costs spelling rather than meaning:
// Go's unicode tables are 15.0.0 where CPython's unidata is 16.0.0, so
// 5,812 code points assigned in 16.0.0 read as Cn (unassigned) here. Go
// escapes those where Python prints them raw. The direction is ONE-WAY —
// swept, there is no code point Go prints raw and Python escapes — and
// since \uXXXX parses back to exactly the same character, the two
// spellings carry the same string VALUE. Only the bytes differ, so a
// consumer that hashes this output rather than parsing it sees two keys.
// It moves with the toolchains, not with this code.
func Repr(s string) string {
	quote := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.WriteByte(quote)
	for _, r := range s {
		switch {
		case r == rune(quote) || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\t':
			b.WriteString(`\t`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case IsPrintable(r):
			b.WriteRune(r)
		case r < 0x100:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r < 0x10000:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			fmt.Fprintf(&b, `\U%08x`, r)
		}
	}
	b.WriteByte(quote)
	return b.String()
}

// IsPrintable is Python's str.isprintable() for one rune.
//
// Python states the rule negatively — false for Cc, Cf, Cs, Co, Cn, Zl,
// Zp and Zs, with U+0020 the single exception — and transcribing it that
// way is a trap, because Cn is UNASSIGNED and unassigned code points
// appear in no category table at all. A literal "not in C and not in Z"
// would let all 800k+ of them through as printable.
//
// So it is stated positively: printable is exactly L, M, N, P, S plus the
// space. That is equivalent (those five categories are disjoint from C
// and Z, which together with Z and C exhaust the assigned range) and it
// gets Cn right by construction rather than by a second guard.
//
// The guard IS what this used to have, and a mutation battery is what
// removed it: deleting `C` from it survived, and so did deleting `Z`,
// which is only possible if the whole line was dead. Two survivors that
// look like missing coverage were one piece of unreachable code.
func IsPrintable(r rune) bool {
	return r == ' ' || unicode.In(r, unicode.L, unicode.M, unicode.N,
		unicode.P, unicode.S)
}
