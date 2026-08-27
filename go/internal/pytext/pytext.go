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

// IsFloatSpace is the whitespace set Python's float() strips, which is
// NOT str.strip()'s set. Swept over the full rune range on this box:
//
//	str.strip() strips 29    float() strips 25
//	str.strip strips but float() does NOT: 0x1C 0x1D 0x1E 0x1F
//	float() strips but str.strip does NOT: (none)
//
// So the four ASCII information separators — the exact code points this
// port has been chasing since round 3 — are the whole difference, and
// here they run the OTHER way: stripping them is what diverges.
// float("0.9") is a ValueError, so safe_float returns its default;
// a port that pre-strips with Strip parses 0.9 instead (adversarial
// mission-r6 HIGH).
func IsFloatSpace(r rune) bool { return IsSpace(r) && !(r >= 0x1c && r <= 0x1f) }

// FloatStrip trims what Python's float() trims. Use it before ParseFloat
// on any string that came from a model reply or the shared store; use
// Strip everywhere str.strip() is what the Python actually calls.
func FloatStrip(s string) string { return strings.TrimFunc(s, IsFloatSpace) }

// TrimRight is Python's str.rstrip() — trailing whitespace only. Note
// that on a joined multi-line document this removes trailing BLANK LINES
// as well as trailing spaces, which is how the NEXT.md writer normalizes
// its tail.
func TrimRight(s string) string { return strings.TrimRightFunc(s, IsSpace) }

// TrimLeft is Python's str.lstrip() with no argument. Measured: it strips
// the same 29 code points as str.isspace(), so it shares IsSpace — and,
// like Strip, it differs from strings.TrimLeft(s, " \t") and from
// strings.TrimLeftFunc(s, unicode.IsSpace) on U+001C..U+001F.
//
// The call site that needs it decides whether a line is a markdown bullet
// (`line.lstrip().startswith("- ")`), so the four-code-point difference
// decides whether a bullet is counted, deduped, or passed through as
// prose.
func TrimLeft(s string) string { return strings.TrimLeftFunc(s, IsSpace) }

// Lower is Python's str.lower(). Go's strings.ToLower applies SIMPLE case
// mapping (one rune in, one rune out); Python applies the full mapping,
// where U+0130 (LATIN CAPITAL LETTER I WITH DOT ABOVE) lowercases to two
// runes, "i" + U+0307. Swept over the whole rune range that is the only
// unconditional multi-rune lowercase mapping there is, so handling it
// outright is the whole fix.
//
// The table skew was CLOSED rather than named (adversarial r5, L4). Go
// ships unicode 15.0.0 where CPython here has 16.0.0, and 27 runes
// lowercase in CPython and not in Go purely because of that. This used to
// be documented as "version-dependent and unfixable from here", which was
// wrong on the second half: 27 entries close it, and lowerSupplement below
// is that table. Every one is a single-rune mapping and Go's own table maps
// none of them, so the supplement is a pure addition — measured, not
// assumed.
//
// The pins in lower_skew_test.go re-derive the whole map from CPython, so a
// gap in EITHER direction fails, and a separate one fails when Go's table
// catches up so the literals get deleted instead of quietly rotting.
var lowerSupplement = map[rune]rune{
	0x01C89: 0x01C8A, // CYRILLIC CAPITAL LETTER TJE
	0x0A7CB: 0x00264, // LATIN CAPITAL LETTER RAMS HORN
	0x0A7CC: 0x0A7CD, // LATIN CAPITAL LETTER S WITH DIAGONAL STROKE
	0x0A7DA: 0x0A7DB, // LATIN CAPITAL LETTER LAMBDA
	0x0A7DC: 0x0019B, // LATIN CAPITAL LETTER LAMBDA WITH STROKE
	0x10D50: 0x10D70, // GARAY CAPITAL LETTER A
	0x10D51: 0x10D71, // GARAY CAPITAL LETTER CA
	0x10D52: 0x10D72, // GARAY CAPITAL LETTER MA
	0x10D53: 0x10D73, // GARAY CAPITAL LETTER KA
	0x10D54: 0x10D74, // GARAY CAPITAL LETTER BA
	0x10D55: 0x10D75, // GARAY CAPITAL LETTER JA
	0x10D56: 0x10D76, // GARAY CAPITAL LETTER SA
	0x10D57: 0x10D77, // GARAY CAPITAL LETTER WA
	0x10D58: 0x10D78, // GARAY CAPITAL LETTER LA
	0x10D59: 0x10D79, // GARAY CAPITAL LETTER GA
	0x10D5A: 0x10D7A, // GARAY CAPITAL LETTER DA
	0x10D5B: 0x10D7B, // GARAY CAPITAL LETTER XA
	0x10D5C: 0x10D7C, // GARAY CAPITAL LETTER YA
	0x10D5D: 0x10D7D, // GARAY CAPITAL LETTER TA
	0x10D5E: 0x10D7E, // GARAY CAPITAL LETTER RA
	0x10D5F: 0x10D7F, // GARAY CAPITAL LETTER NYA
	0x10D60: 0x10D80, // GARAY CAPITAL LETTER FA
	0x10D61: 0x10D81, // GARAY CAPITAL LETTER NA
	0x10D62: 0x10D82, // GARAY CAPITAL LETTER PA
	0x10D63: 0x10D83, // GARAY CAPITAL LETTER HA
	0x10D64: 0x10D84, // GARAY CAPITAL LETTER OLD KA
	0x10D65: 0x10D85, // GARAY CAPITAL LETTER OLD NA
}

// wordBreakIgnorable is the part of Unicode's Case_Ignorable that is not a
// whole general category: the Word_Break MidLetter, MidNumLet and
// Single_Quote punctuation. Measured against CPython rather than
// transcribed from the spec — these are exactly the 17 code points whose
// presence between a cased letter and a sigma still yields "ς".
var wordBreakIgnorable = map[rune]bool{
	0x0027: true, // APOSTROPHE
	0x002E: true, // FULL STOP
	0x003A: true, // COLON
	0x00B7: true, // MIDDLE DOT
	0x0387: true, // GREEK ANO TELEIA
	0x055F: true, // ARMENIAN ABBREVIATION MARK
	0x05F4: true, // HEBREW PUNCTUATION GERSHAYIM
	0x2018: true, // LEFT SINGLE QUOTATION MARK
	0x2019: true, // RIGHT SINGLE QUOTATION MARK
	0x2024: true, // ONE DOT LEADER
	0x2027: true, // HYPHENATION POINT
	0xFE13: true, // PRESENTATION FORM FOR VERTICAL COLON
	0xFE52: true, // SMALL FULL STOP
	0xFE55: true, // SMALL COLON
	0xFF07: true, // FULLWIDTH APOSTROPHE
	0xFF0E: true, // FULLWIDTH FULL STOP
	0xFF1A: true, // FULLWIDTH COLON
}

// Final_Sigma reads two derived properties, so it inherits the same
// unicode 15.0.0-vs-16.0.0 table skew as Lower, Slugify and the decimal
// fold — and here the consequence is again a FILENAME, since Slugify
// lowercases before it slugifies. Measured, the skew is 96 code points and
// it runs in BOTH directions, which none of the other three did:
//
//	casedSupplement           52   CPython says Cased, Go's tables do not
//	caseIgnorableSupplement   43   CPython says Case_Ignorable, Go does not
//	caseIgnorableExclusion     1   Go says Case_Ignorable, CPython does not
//
// The exclusion is a RECLASSIFICATION, not an addition: U+1171E was Mn in
// Unicode 15 and became Mc in 16, so Go's own table is not merely behind
// here, it disagrees. A supplement-only fix cannot express that, which is
// why this skew gets three tables where lowerSupplement needed one.
var casedSupplement = [...][2]rune{
	{0x01C89, 0x01C8A}, // CYRILLIC CAPITAL/SMALL LETTER TJE
	{0x0A7CB, 0x0A7CD}, // LATIN CAPITAL LETTER RAMS HORN and successors
	{0x0A7DA, 0x0A7DC}, // LATIN CAPITAL LETTER LAMBDA and successors
	{0x10D50, 0x10D65}, // GARAY CAPITAL LETTER A..OLD NA
	{0x10D70, 0x10D85}, // GARAY SMALL LETTER A..OLD NA
}

var caseIgnorableSupplement = [...][2]rune{
	{0x00897, 0x00897}, // ARABIC PEPET
	{0x10D4E, 0x10D4E}, // GARAY VOWEL LENGTH MARK
	{0x10D69, 0x10D6D}, // GARAY VOWEL SIGN E..SUKUN
	{0x10D6F, 0x10D6F}, // GARAY REDUPLICATION MARK
	{0x10EFC, 0x10EFC}, // ARABIC COMBINING ALEF OVERLAY
	{0x113BB, 0x113C0}, // TULU-TIGALARI VOWEL SIGN U..AI
	{0x113CE, 0x113CE}, // TULU-TIGALARI SIGN VIRAMA
	{0x113D0, 0x113D0}, // TULU-TIGALARI CONJOINER
	{0x113D2, 0x113D2}, // TULU-TIGALARI GEMINATION MARK
	{0x113E1, 0x113E2}, // TULU-TIGALARI VEDIC TONE marks
	{0x11F5A, 0x11F5A}, // KAWI SIGN NUKTA
	{0x1611E, 0x16129}, // GURUNG KHEMA VOWEL SIGN AA and successors
	{0x1612D, 0x1612F}, // GURUNG KHEMA SIGN marks
	{0x16D40, 0x16D42}, // KIRAT RAI SIGN marks
	{0x16D6B, 0x16D6C}, // KIRAT RAI SIGN marks
	{0x1E5EE, 0x1E5EF}, // OL ONAL SIGN marks
}

// caseIgnorableExclusion is Mn in Go's tables and Mc in CPython's.
const caseIgnorableExclusion = 0x1171E // AHOM CONSONANT SIGN MEDIAL RA

func inRanges(r rune, ranges [][2]rune) bool {
	for _, rg := range ranges {
		if r >= rg[0] && r <= rg[1] {
			return true
		}
	}
	return false
}

// cased and caseIgnorable are Unicode's Cased and Case_Ignorable derived
// properties, the two inputs to the Final_Sigma rule. Both were measured
// against CPython over the whole rune range rather than read off the spec:
// Cased came out as exactly Lu ∪ Ll ∪ Lt ∪ Other_Lowercase ∪
// Other_Uppercase (4,311 code points) and Case_Ignorable as Mn ∪ Me ∪ Cf ∪
// Lm ∪ Sk plus the 17 above (2,749).
//
// A rune can be BOTH — the modifier letters in Lm ∩ Other_Lowercase are the
// large case. finalSigma resolves that the way UAX #29 does, by testing
// case-ignorability first and continuing the scan, so cased() is never
// asked about them.
func cased(r rune) bool {
	return unicode.In(r, unicode.Lu, unicode.Ll, unicode.Lt,
		unicode.Other_Lowercase, unicode.Other_Uppercase) ||
		inRanges(r, casedSupplement[:])
}

func caseIgnorable(r rune) bool {
	if r == caseIgnorableExclusion {
		return false
	}
	return wordBreakIgnorable[r] ||
		unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk) ||
		inRanges(r, caseIgnorableSupplement[:])
}

// finalSigma is Unicode SpecialCasing's Final_Sigma condition for the rune
// at index i: there is a cased letter BEFORE it and none AFTER it, skipping
// case-ignorable characters in both directions.
func finalSigma(runes []rune, i int) bool {
	before := false
	for j := i - 1; j >= 0; j-- {
		if caseIgnorable(runes[j]) {
			continue
		}
		before = cased(runes[j])
		break
	}
	if !before {
		return false
	}
	for j := i + 1; j < len(runes); j++ {
		if caseIgnorable(runes[j]) {
			continue
		}
		return !cased(runes[j])
	}
	return true
}

// Lower is Python's str.lower(). Three things separate it from
// strings.ToLower, and all three cost this port a real divergence:
//
//   - U+0130 expands to TWO runes (handled by substitution below);
//   - 27 runes lowercase in CPython's unicode 16.0.0 and not in Go's 15.0.0
//     (lowerSupplement);
//   - U+03A3 is CONTEXTUAL — "ΟΔΟΣ".lower() is "οδος", with a FINAL sigma,
//     where strings.ToLower gives "οδοσ". Measured over the whole rune
//     range, it is the ONLY context-sensitive lowercase mapping there is,
//     so one special case covers it (adversarial r6, MEDIUM). It reached
//     a FILENAME through Slugify: a skill named "ΟΔΟΣ" was exported to
//     οδοσ.md here and οδος.md there — one skill, two files.
func Lower(s string) string {
	if strings.ContainsRune(s, 0x0130) {
		s = strings.ReplaceAll(s, "İ", "i̇")
	}
	// The sigma decision is made on the ORIGINAL text, because after
	// ToLower there is nothing left to distinguish a σ that was a Σ.
	if strings.ContainsRune(s, 'Σ') {
		runes := []rune(s)
		var b strings.Builder
		b.Grow(len(s))
		for i, r := range runes {
			if r == 'Σ' && finalSigma(runes, i) {
				b.WriteRune('ς')
				continue
			}
			b.WriteRune(r)
		}
		s = b.String()
	}
	s = strings.ToLower(s)
	if !strings.ContainsFunc(s, func(r rune) bool {
		_, ok := lowerSupplement[r]
		return ok
	}) {
		return s
	}
	return strings.Map(func(r rune) rune {
		if l, ok := lowerSupplement[r]; ok {
			return l
		}
		return r
	}, s)
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
//
// This one is DELIBERATELY left open where its two siblings were closed by
// enumerated tables (lowerSupplement here, pyWordSupplement in
// skills/export_md.go). The others were worth 27 and 27 range literals and
// their consequences were a wrong trust grade and a split filename. This
// one is 5,812 code points, it would need re-deriving CPython's whole
// printability set, and its consequence is a differently-spelled string
// that parses back to the same value. Documented magnitude, direction and
// consequence beat a table that large. The sweep in repr_test.go asserts
// the direction stays one-way.
func Repr(s string) string {
	quote := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.WriteByte(quote)
	// Byte-wise, not `for _, r := range s`. A Go string can carry bytes
	// that are not valid UTF-8 — every filename on Linux can — and range
	// hands those back as utf8.RuneError. U+FFFD is category So, so
	// IsPrintable says yes and the replacement character is written out
	// literally. CPython never sees such a string: os.fsdecode escapes each
	// undecodable byte to the lone surrogate U+DC00+b, which is category
	// Cs, unprintable, and reprs as `\udcXX`. MEASURED:
	//
	//	repr(['\udc80z.py'])  ->  ['\udc80z.py']
	//	this function, before ->  ['�z.py']
	//
	// One surrogate PER BYTE, which is the surrogateescape rule and NOT the
	// errors="replace" rule (one U+FFFD per maximal subpart) — the two are
	// easy to conflate and this port implements both, in different places.
	// pypath.FSDecode is the same decoding where a caller needs the runes.
	//
	// A VALID encoding of U+FFFD is untouched: DecodeRuneInString returns
	// size 3 for it, and only size 1 with RuneError means "bad byte".
	// (r3 LOW — cosmetic per call, but reprList's own doc is the standard:
	// two runtimes writing into one operator stream have to agree
	// character for character or a grep keyed on one misses the other.)
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		if r == utf8.RuneError && size == 1 {
			r = rune(0xDC00) + rune(s[i])
		}
		i += size
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

// Split is Python's bare `str.split()`: split on RUNS of whitespace with
// leading and trailing runs discarded, so "" and "   " both yield no
// elements at all.
//
// strings.Fields is the same shape over a NARROWER set — unicode.IsSpace
// misses U+001C..U+001F, which arrive through pasted terminal output more
// often than their obscurity suggests. A goal containing one of them
// splits into a different number of words in the two runtimes, and the
// mission heuristic names its phases after `len(words) // 2`.
func Split(s string) []string {
	return strings.FieldsFunc(s, IsSpace)
}

// TranslateNewlines is what `open(..., newline=None)` does before any caller
// sees a byte: "\r\n" and a lone "\r" both become "\n".
//
// This is NOT the same rule as SplitLines' separator set, and conflating
// them is how the port lost it. SplitLines does the \r\n pairing itself, so
// for anything that immediately splits, the translation is invisible — the
// note on SplitLines says so and is correct. But a caller that keeps the
// WHOLE TEXT (hashes it, ships it in a tar member, compares it for
// equality) sees the difference in every byte: CPython holds "a\nb" where an
// untranslated port holds "a\r\nb".
//
// Only \r is touched. The other seven separators splitlines() knows —
// \v \f \x1c \x1d \x1e \x85 U+2028 U+2029 — are ordinary characters to the
// io layer and survive read_text() intact. A "translation" that normalized
// those would corrupt content Python preserves.
func TranslateNewlines(s string) string {
	if !strings.ContainsRune(s, '\r') {
		return s // the common case, and it must not allocate
	}
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		if s[i] == '\r' {
			if i+1 < len(s) && s[i+1] == '\n' {
				i++
			}
			b.WriteByte('\n')
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

// upperSupplement carries every rune whose Python `str.upper()` differs
// from Go's `strings.ToUpper`. It is MEASURED, not transcribed: the
// generator sweeps all 0x110000 code points, compares the two answers and
// keeps the disagreements, so the table cannot be short by a case nobody
// thought of. 129 entries, in two kinds.
//
//	102 are SpecialCasing expansions -- one rune becoming two or three.
//	  Go's ToUpper is a 1:1 rune map and structurally cannot express
//	  them, so U+00DF uppercases to itself there and to "SS" in CPython.
//	  This is the half that changes a string's LENGTH.
//	27 are the same Unicode version skew lowerSupplement carries: CPython
//	  ships 16.0.0 against Go's 15.0.0, and these runes gained a mapping
//	  in between.
//
// Nothing here is contextual. str.upper() was checked against per-rune
// upper() over 200k random strings drawn from this table plus the usual
// suspects (sigma, the Turkish pair, the micro sign) with zero
// disagreements -- unlike LOWER, whose final-sigma rule is real and is
// why Lower cannot be written this way.
var upperSupplement = map[rune]string{
	0x00DF:  "SS",                 // LATIN SMALL LETTER SHARP S
	0x0149:  "\u02bcN",            // LATIN SMALL LETTER N PRECEDED BY APOSTROPHE
	0x019B:  "\ua7dc",             // LATIN SMALL LETTER LAMBDA WITH STROKE
	0x01F0:  "J\u030c",            // LATIN SMALL LETTER J WITH CARON
	0x0264:  "\ua7cb",             // LATIN SMALL LETTER RAMS HORN
	0x0390:  "\u0399\u0308\u0301", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND TONOS
	0x03B0:  "\u03a5\u0308\u0301", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND TONOS
	0x0587:  "\u0535\u0552",       // ARMENIAN SMALL LIGATURE ECH YIWN
	0x1C8A:  "\u1c89",             // CYRILLIC SMALL LETTER TJE
	0x1E96:  "H\u0331",            // LATIN SMALL LETTER H WITH LINE BELOW
	0x1E97:  "T\u0308",            // LATIN SMALL LETTER T WITH DIAERESIS
	0x1E98:  "W\u030a",            // LATIN SMALL LETTER W WITH RING ABOVE
	0x1E99:  "Y\u030a",            // LATIN SMALL LETTER Y WITH RING ABOVE
	0x1E9A:  "A\u02be",            // LATIN SMALL LETTER A WITH RIGHT HALF RING
	0x1F50:  "\u03a5\u0313",       // GREEK SMALL LETTER UPSILON WITH PSILI
	0x1F52:  "\u03a5\u0313\u0300", // GREEK SMALL LETTER UPSILON WITH PSILI AND VARIA
	0x1F54:  "\u03a5\u0313\u0301", // GREEK SMALL LETTER UPSILON WITH PSILI AND OXIA
	0x1F56:  "\u03a5\u0313\u0342", // GREEK SMALL LETTER UPSILON WITH PSILI AND PERISPOMENI
	0x1F80:  "\u1f08\u0399",       // GREEK SMALL LETTER ALPHA WITH PSILI AND YPOGEGRAMMENI
	0x1F81:  "\u1f09\u0399",       // GREEK SMALL LETTER ALPHA WITH DASIA AND YPOGEGRAMMENI
	0x1F82:  "\u1f0a\u0399",       // GREEK SMALL LETTER ALPHA WITH PSILI AND VARIA AND YPOGEGRAMMENI
	0x1F83:  "\u1f0b\u0399",       // GREEK SMALL LETTER ALPHA WITH DASIA AND VARIA AND YPOGEGRAMMENI
	0x1F84:  "\u1f0c\u0399",       // GREEK SMALL LETTER ALPHA WITH PSILI AND OXIA AND YPOGEGRAMMENI
	0x1F85:  "\u1f0d\u0399",       // GREEK SMALL LETTER ALPHA WITH DASIA AND OXIA AND YPOGEGRAMMENI
	0x1F86:  "\u1f0e\u0399",       // GREEK SMALL LETTER ALPHA WITH PSILI AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F87:  "\u1f0f\u0399",       // GREEK SMALL LETTER ALPHA WITH DASIA AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F88:  "\u1f08\u0399",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND PROSGEGRAMMENI
	0x1F89:  "\u1f09\u0399",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND PROSGEGRAMMENI
	0x1F8A:  "\u1f0a\u0399",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND VARIA AND PROSGEGRAMMENI
	0x1F8B:  "\u1f0b\u0399",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND VARIA AND PROSGEGRAMMENI
	0x1F8C:  "\u1f0c\u0399",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND OXIA AND PROSGEGRAMMENI
	0x1F8D:  "\u1f0d\u0399",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND OXIA AND PROSGEGRAMMENI
	0x1F8E:  "\u1f0e\u0399",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND PERISPOMENI AND PROSGEGRAMMENI
	0x1F8F:  "\u1f0f\u0399",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND PERISPOMENI AND PROSGEGRAMMENI
	0x1F90:  "\u1f28\u0399",       // GREEK SMALL LETTER ETA WITH PSILI AND YPOGEGRAMMENI
	0x1F91:  "\u1f29\u0399",       // GREEK SMALL LETTER ETA WITH DASIA AND YPOGEGRAMMENI
	0x1F92:  "\u1f2a\u0399",       // GREEK SMALL LETTER ETA WITH PSILI AND VARIA AND YPOGEGRAMMENI
	0x1F93:  "\u1f2b\u0399",       // GREEK SMALL LETTER ETA WITH DASIA AND VARIA AND YPOGEGRAMMENI
	0x1F94:  "\u1f2c\u0399",       // GREEK SMALL LETTER ETA WITH PSILI AND OXIA AND YPOGEGRAMMENI
	0x1F95:  "\u1f2d\u0399",       // GREEK SMALL LETTER ETA WITH DASIA AND OXIA AND YPOGEGRAMMENI
	0x1F96:  "\u1f2e\u0399",       // GREEK SMALL LETTER ETA WITH PSILI AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F97:  "\u1f2f\u0399",       // GREEK SMALL LETTER ETA WITH DASIA AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F98:  "\u1f28\u0399",       // GREEK CAPITAL LETTER ETA WITH PSILI AND PROSGEGRAMMENI
	0x1F99:  "\u1f29\u0399",       // GREEK CAPITAL LETTER ETA WITH DASIA AND PROSGEGRAMMENI
	0x1F9A:  "\u1f2a\u0399",       // GREEK CAPITAL LETTER ETA WITH PSILI AND VARIA AND PROSGEGRAMMENI
	0x1F9B:  "\u1f2b\u0399",       // GREEK CAPITAL LETTER ETA WITH DASIA AND VARIA AND PROSGEGRAMMENI
	0x1F9C:  "\u1f2c\u0399",       // GREEK CAPITAL LETTER ETA WITH PSILI AND OXIA AND PROSGEGRAMMENI
	0x1F9D:  "\u1f2d\u0399",       // GREEK CAPITAL LETTER ETA WITH DASIA AND OXIA AND PROSGEGRAMMENI
	0x1F9E:  "\u1f2e\u0399",       // GREEK CAPITAL LETTER ETA WITH PSILI AND PERISPOMENI AND PROSGEGRAMMENI
	0x1F9F:  "\u1f2f\u0399",       // GREEK CAPITAL LETTER ETA WITH DASIA AND PERISPOMENI AND PROSGEGRAMMENI
	0x1FA0:  "\u1f68\u0399",       // GREEK SMALL LETTER OMEGA WITH PSILI AND YPOGEGRAMMENI
	0x1FA1:  "\u1f69\u0399",       // GREEK SMALL LETTER OMEGA WITH DASIA AND YPOGEGRAMMENI
	0x1FA2:  "\u1f6a\u0399",       // GREEK SMALL LETTER OMEGA WITH PSILI AND VARIA AND YPOGEGRAMMENI
	0x1FA3:  "\u1f6b\u0399",       // GREEK SMALL LETTER OMEGA WITH DASIA AND VARIA AND YPOGEGRAMMENI
	0x1FA4:  "\u1f6c\u0399",       // GREEK SMALL LETTER OMEGA WITH PSILI AND OXIA AND YPOGEGRAMMENI
	0x1FA5:  "\u1f6d\u0399",       // GREEK SMALL LETTER OMEGA WITH DASIA AND OXIA AND YPOGEGRAMMENI
	0x1FA6:  "\u1f6e\u0399",       // GREEK SMALL LETTER OMEGA WITH PSILI AND PERISPOMENI AND YPOGEGRAMMENI
	0x1FA7:  "\u1f6f\u0399",       // GREEK SMALL LETTER OMEGA WITH DASIA AND PERISPOMENI AND YPOGEGRAMMENI
	0x1FA8:  "\u1f68\u0399",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND PROSGEGRAMMENI
	0x1FA9:  "\u1f69\u0399",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND PROSGEGRAMMENI
	0x1FAA:  "\u1f6a\u0399",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND VARIA AND PROSGEGRAMMENI
	0x1FAB:  "\u1f6b\u0399",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND VARIA AND PROSGEGRAMMENI
	0x1FAC:  "\u1f6c\u0399",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND OXIA AND PROSGEGRAMMENI
	0x1FAD:  "\u1f6d\u0399",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND OXIA AND PROSGEGRAMMENI
	0x1FAE:  "\u1f6e\u0399",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND PERISPOMENI AND PROSGEGRAMMENI
	0x1FAF:  "\u1f6f\u0399",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND PERISPOMENI AND PROSGEGRAMMENI
	0x1FB2:  "\u1fba\u0399",       // GREEK SMALL LETTER ALPHA WITH VARIA AND YPOGEGRAMMENI
	0x1FB3:  "\u0391\u0399",       // GREEK SMALL LETTER ALPHA WITH YPOGEGRAMMENI
	0x1FB4:  "\u0386\u0399",       // GREEK SMALL LETTER ALPHA WITH OXIA AND YPOGEGRAMMENI
	0x1FB6:  "\u0391\u0342",       // GREEK SMALL LETTER ALPHA WITH PERISPOMENI
	0x1FB7:  "\u0391\u0342\u0399", // GREEK SMALL LETTER ALPHA WITH PERISPOMENI AND YPOGEGRAMMENI
	0x1FBC:  "\u0391\u0399",       // GREEK CAPITAL LETTER ALPHA WITH PROSGEGRAMMENI
	0x1FC2:  "\u1fca\u0399",       // GREEK SMALL LETTER ETA WITH VARIA AND YPOGEGRAMMENI
	0x1FC3:  "\u0397\u0399",       // GREEK SMALL LETTER ETA WITH YPOGEGRAMMENI
	0x1FC4:  "\u0389\u0399",       // GREEK SMALL LETTER ETA WITH OXIA AND YPOGEGRAMMENI
	0x1FC6:  "\u0397\u0342",       // GREEK SMALL LETTER ETA WITH PERISPOMENI
	0x1FC7:  "\u0397\u0342\u0399", // GREEK SMALL LETTER ETA WITH PERISPOMENI AND YPOGEGRAMMENI
	0x1FCC:  "\u0397\u0399",       // GREEK CAPITAL LETTER ETA WITH PROSGEGRAMMENI
	0x1FD2:  "\u0399\u0308\u0300", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND VARIA
	0x1FD3:  "\u0399\u0308\u0301", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND OXIA
	0x1FD6:  "\u0399\u0342",       // GREEK SMALL LETTER IOTA WITH PERISPOMENI
	0x1FD7:  "\u0399\u0308\u0342", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND PERISPOMENI
	0x1FE2:  "\u03a5\u0308\u0300", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND VARIA
	0x1FE3:  "\u03a5\u0308\u0301", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND OXIA
	0x1FE4:  "\u03a1\u0313",       // GREEK SMALL LETTER RHO WITH PSILI
	0x1FE6:  "\u03a5\u0342",       // GREEK SMALL LETTER UPSILON WITH PERISPOMENI
	0x1FE7:  "\u03a5\u0308\u0342", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND PERISPOMENI
	0x1FF2:  "\u1ffa\u0399",       // GREEK SMALL LETTER OMEGA WITH VARIA AND YPOGEGRAMMENI
	0x1FF3:  "\u03a9\u0399",       // GREEK SMALL LETTER OMEGA WITH YPOGEGRAMMENI
	0x1FF4:  "\u038f\u0399",       // GREEK SMALL LETTER OMEGA WITH OXIA AND YPOGEGRAMMENI
	0x1FF6:  "\u03a9\u0342",       // GREEK SMALL LETTER OMEGA WITH PERISPOMENI
	0x1FF7:  "\u03a9\u0342\u0399", // GREEK SMALL LETTER OMEGA WITH PERISPOMENI AND YPOGEGRAMMENI
	0x1FFC:  "\u03a9\u0399",       // GREEK CAPITAL LETTER OMEGA WITH PROSGEGRAMMENI
	0xA7CD:  "\ua7cc",             // LATIN SMALL LETTER S WITH DIAGONAL STROKE
	0xA7DB:  "\ua7da",             // LATIN SMALL LETTER LAMBDA
	0xFB00:  "FF",                 // LATIN SMALL LIGATURE FF
	0xFB01:  "FI",                 // LATIN SMALL LIGATURE FI
	0xFB02:  "FL",                 // LATIN SMALL LIGATURE FL
	0xFB03:  "FFI",                // LATIN SMALL LIGATURE FFI
	0xFB04:  "FFL",                // LATIN SMALL LIGATURE FFL
	0xFB05:  "ST",                 // LATIN SMALL LIGATURE LONG S T
	0xFB06:  "ST",                 // LATIN SMALL LIGATURE ST
	0xFB13:  "\u0544\u0546",       // ARMENIAN SMALL LIGATURE MEN NOW
	0xFB14:  "\u0544\u0535",       // ARMENIAN SMALL LIGATURE MEN ECH
	0xFB15:  "\u0544\u053b",       // ARMENIAN SMALL LIGATURE MEN INI
	0xFB16:  "\u054e\u0546",       // ARMENIAN SMALL LIGATURE VEW NOW
	0xFB17:  "\u0544\u053d",       // ARMENIAN SMALL LIGATURE MEN XEH
	0x10D70: "\U00010d50",         // GARAY SMALL LETTER A
	0x10D71: "\U00010d51",         // GARAY SMALL LETTER CA
	0x10D72: "\U00010d52",         // GARAY SMALL LETTER MA
	0x10D73: "\U00010d53",         // GARAY SMALL LETTER KA
	0x10D74: "\U00010d54",         // GARAY SMALL LETTER BA
	0x10D75: "\U00010d55",         // GARAY SMALL LETTER JA
	0x10D76: "\U00010d56",         // GARAY SMALL LETTER SA
	0x10D77: "\U00010d57",         // GARAY SMALL LETTER WA
	0x10D78: "\U00010d58",         // GARAY SMALL LETTER LA
	0x10D79: "\U00010d59",         // GARAY SMALL LETTER GA
	0x10D7A: "\U00010d5a",         // GARAY SMALL LETTER DA
	0x10D7B: "\U00010d5b",         // GARAY SMALL LETTER XA
	0x10D7C: "\U00010d5c",         // GARAY SMALL LETTER YA
	0x10D7D: "\U00010d5d",         // GARAY SMALL LETTER TA
	0x10D7E: "\U00010d5e",         // GARAY SMALL LETTER RA
	0x10D7F: "\U00010d5f",         // GARAY SMALL LETTER NYA
	0x10D80: "\U00010d60",         // GARAY SMALL LETTER FA
	0x10D81: "\U00010d61",         // GARAY SMALL LETTER NA
	0x10D82: "\U00010d62",         // GARAY SMALL LETTER PA
	0x10D83: "\U00010d63",         // GARAY SMALL LETTER HA
	0x10D84: "\U00010d64",         // GARAY SMALL LETTER OLD KA
	0x10D85: "\U00010d65",         // GARAY SMALL LETTER OLD NA
}

// Upper is Python's `str.upper()`. Go's strings.ToUpper agrees on all but
// the 129 runes in upperSupplement, and the expansions among those change
// the LENGTH of the result -- so a port that uses ToUpper for an upper()
// does not merely spell a character differently, it produces a string of
// a different size, and every offset computed from it moves.
func Upper(s string) string {
	if !strings.ContainsFunc(s, inUpperSupplement) {
		return strings.ToUpper(s)
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if u, ok := upperSupplement[r]; ok {
			b.WriteString(u)
			continue
		}
		b.WriteRune(unicode.ToUpper(r))
	}
	return b.String()
}

func inUpperSupplement(r rune) bool {
	_, ok := upperSupplement[r]
	return ok
}
