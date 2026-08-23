package pytext

import (
	"strings"
	"unicode"
)

// CaseFold is Python's str.casefold(). It exists because playbook.py uses
// it as a DEDUP KEY (`_entry_core` ends in `.casefold()`), so a runtime
// that folds differently keeps a different set of bullets in a file both
// runtimes write.
//
// casefold is not lower. It differs on 297 code points, 103 of which
// expand to more than one rune (ß→ss, ﬄ→ffl), and — the trap — it has NO
// context rules. `'ΟΔΟΣ'.lower()` is `'οδος'` with a final sigma;
// `'ΟΔΟΣ'.casefold()` is `'οδοσ'` with a plain one. ς and Σ both fold to
// σ, which is the whole point of casefold: it is a matching key, not a
// rendering.
//
// Folding rune by rune rather than calling Lower first is therefore the
// obvious-looking safety measure, and it is worth being precise about
// what it actually buys, because the answer is "not correctness".
// Measured: routing the whole string through Lower first and then folding
// gives an IDENTICAL result at every code point in every sigma-triggering
// context (5,560,320 strings, 0 differences) — Lower's Final_Sigma emits
// ς, and the table maps ς to σ, so the damage is undone on the way out.
// The per-rune path is used because the alternative would leave
// correctness resting on one table entry silently repairing a context
// rule that should never have run here. That is a fragility, not a bug;
// do not "simplify" this into Lower on the strength of the table.
func CaseFold(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if v, ok := caseFoldTable[r]; ok {
			b.WriteString(v)
			continue
		}
		b.WriteString(lowerRune(r))
	}
	return b.String()
}

// lowerRune is Lower's per-rune half, without the two whole-string passes
// (Final_Sigma needs a preceding cased rune, so it can never fire on one
// rune; the U+0130 expansion can, and is kept).
//
// It is a SEPARATE implementation from Lower's body, which is a liability
// — so a pin re-derives it against Lower at every code point rather than
// trusting that the two stay in step.
func lowerRune(r rune) string {
	if r == 0x0130 {
		return "i\u0307" // LATIN CAPITAL LETTER I WITH DOT ABOVE
	}
	l := unicode.ToLower(r)
	if sup, ok := lowerSupplement[l]; ok {
		l = sup
	}
	return string(l)
}

// caseFoldTable is every code point where CPython's str.casefold()
// differs from this package's per-rune Lower. It was DERIVED, not
// transcribed: Go's mapping was dumped for all 0x110000 code points and
// diffed against CPython's casefold on this box (unidata 16.0.0), so the
// unicode 15-vs-16 skew Lower already corrects is not re-introduced here.
//
// 297 entries, 103 of which expand to more than one rune
// (ß→ss, ﬄ→ffl …). Note U+03C2: casefold sends final sigma to
// σ where Lower leaves it alone, which is why CaseFold must NOT route
// through Lower's Final_Sigma rule — casefold has no context rule at all.
var caseFoldTable = map[rune]string{
	0x00B5: "\u03bc",             // MICRO SIGN
	0x00DF: "\u0073\u0073",       // LATIN SMALL LETTER SHARP S
	0x0149: "\u02bc\u006e",       // LATIN SMALL LETTER N PRECEDED BY APOSTROPHE
	0x017F: "\u0073",             // LATIN SMALL LETTER LONG S
	0x01F0: "\u006a\u030c",       // LATIN SMALL LETTER J WITH CARON
	0x0345: "\u03b9",             // COMBINING GREEK YPOGEGRAMMENI
	0x0390: "\u03b9\u0308\u0301", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND TONOS
	0x03B0: "\u03c5\u0308\u0301", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND TONOS
	0x03C2: "\u03c3",             // GREEK SMALL LETTER FINAL SIGMA
	0x03D0: "\u03b2",             // GREEK BETA SYMBOL
	0x03D1: "\u03b8",             // GREEK THETA SYMBOL
	0x03D5: "\u03c6",             // GREEK PHI SYMBOL
	0x03D6: "\u03c0",             // GREEK PI SYMBOL
	0x03F0: "\u03ba",             // GREEK KAPPA SYMBOL
	0x03F1: "\u03c1",             // GREEK RHO SYMBOL
	0x03F5: "\u03b5",             // GREEK LUNATE EPSILON SYMBOL
	0x0587: "\u0565\u0582",       // ARMENIAN SMALL LIGATURE ECH YIWN
	0x13A0: "\u13a0",             // CHEROKEE LETTER A
	0x13A1: "\u13a1",             // CHEROKEE LETTER E
	0x13A2: "\u13a2",             // CHEROKEE LETTER I
	0x13A3: "\u13a3",             // CHEROKEE LETTER O
	0x13A4: "\u13a4",             // CHEROKEE LETTER U
	0x13A5: "\u13a5",             // CHEROKEE LETTER V
	0x13A6: "\u13a6",             // CHEROKEE LETTER GA
	0x13A7: "\u13a7",             // CHEROKEE LETTER KA
	0x13A8: "\u13a8",             // CHEROKEE LETTER GE
	0x13A9: "\u13a9",             // CHEROKEE LETTER GI
	0x13AA: "\u13aa",             // CHEROKEE LETTER GO
	0x13AB: "\u13ab",             // CHEROKEE LETTER GU
	0x13AC: "\u13ac",             // CHEROKEE LETTER GV
	0x13AD: "\u13ad",             // CHEROKEE LETTER HA
	0x13AE: "\u13ae",             // CHEROKEE LETTER HE
	0x13AF: "\u13af",             // CHEROKEE LETTER HI
	0x13B0: "\u13b0",             // CHEROKEE LETTER HO
	0x13B1: "\u13b1",             // CHEROKEE LETTER HU
	0x13B2: "\u13b2",             // CHEROKEE LETTER HV
	0x13B3: "\u13b3",             // CHEROKEE LETTER LA
	0x13B4: "\u13b4",             // CHEROKEE LETTER LE
	0x13B5: "\u13b5",             // CHEROKEE LETTER LI
	0x13B6: "\u13b6",             // CHEROKEE LETTER LO
	0x13B7: "\u13b7",             // CHEROKEE LETTER LU
	0x13B8: "\u13b8",             // CHEROKEE LETTER LV
	0x13B9: "\u13b9",             // CHEROKEE LETTER MA
	0x13BA: "\u13ba",             // CHEROKEE LETTER ME
	0x13BB: "\u13bb",             // CHEROKEE LETTER MI
	0x13BC: "\u13bc",             // CHEROKEE LETTER MO
	0x13BD: "\u13bd",             // CHEROKEE LETTER MU
	0x13BE: "\u13be",             // CHEROKEE LETTER NA
	0x13BF: "\u13bf",             // CHEROKEE LETTER HNA
	0x13C0: "\u13c0",             // CHEROKEE LETTER NAH
	0x13C1: "\u13c1",             // CHEROKEE LETTER NE
	0x13C2: "\u13c2",             // CHEROKEE LETTER NI
	0x13C3: "\u13c3",             // CHEROKEE LETTER NO
	0x13C4: "\u13c4",             // CHEROKEE LETTER NU
	0x13C5: "\u13c5",             // CHEROKEE LETTER NV
	0x13C6: "\u13c6",             // CHEROKEE LETTER QUA
	0x13C7: "\u13c7",             // CHEROKEE LETTER QUE
	0x13C8: "\u13c8",             // CHEROKEE LETTER QUI
	0x13C9: "\u13c9",             // CHEROKEE LETTER QUO
	0x13CA: "\u13ca",             // CHEROKEE LETTER QUU
	0x13CB: "\u13cb",             // CHEROKEE LETTER QUV
	0x13CC: "\u13cc",             // CHEROKEE LETTER SA
	0x13CD: "\u13cd",             // CHEROKEE LETTER S
	0x13CE: "\u13ce",             // CHEROKEE LETTER SE
	0x13CF: "\u13cf",             // CHEROKEE LETTER SI
	0x13D0: "\u13d0",             // CHEROKEE LETTER SO
	0x13D1: "\u13d1",             // CHEROKEE LETTER SU
	0x13D2: "\u13d2",             // CHEROKEE LETTER SV
	0x13D3: "\u13d3",             // CHEROKEE LETTER DA
	0x13D4: "\u13d4",             // CHEROKEE LETTER TA
	0x13D5: "\u13d5",             // CHEROKEE LETTER DE
	0x13D6: "\u13d6",             // CHEROKEE LETTER TE
	0x13D7: "\u13d7",             // CHEROKEE LETTER DI
	0x13D8: "\u13d8",             // CHEROKEE LETTER TI
	0x13D9: "\u13d9",             // CHEROKEE LETTER DO
	0x13DA: "\u13da",             // CHEROKEE LETTER DU
	0x13DB: "\u13db",             // CHEROKEE LETTER DV
	0x13DC: "\u13dc",             // CHEROKEE LETTER DLA
	0x13DD: "\u13dd",             // CHEROKEE LETTER TLA
	0x13DE: "\u13de",             // CHEROKEE LETTER TLE
	0x13DF: "\u13df",             // CHEROKEE LETTER TLI
	0x13E0: "\u13e0",             // CHEROKEE LETTER TLO
	0x13E1: "\u13e1",             // CHEROKEE LETTER TLU
	0x13E2: "\u13e2",             // CHEROKEE LETTER TLV
	0x13E3: "\u13e3",             // CHEROKEE LETTER TSA
	0x13E4: "\u13e4",             // CHEROKEE LETTER TSE
	0x13E5: "\u13e5",             // CHEROKEE LETTER TSI
	0x13E6: "\u13e6",             // CHEROKEE LETTER TSO
	0x13E7: "\u13e7",             // CHEROKEE LETTER TSU
	0x13E8: "\u13e8",             // CHEROKEE LETTER TSV
	0x13E9: "\u13e9",             // CHEROKEE LETTER WA
	0x13EA: "\u13ea",             // CHEROKEE LETTER WE
	0x13EB: "\u13eb",             // CHEROKEE LETTER WI
	0x13EC: "\u13ec",             // CHEROKEE LETTER WO
	0x13ED: "\u13ed",             // CHEROKEE LETTER WU
	0x13EE: "\u13ee",             // CHEROKEE LETTER WV
	0x13EF: "\u13ef",             // CHEROKEE LETTER YA
	0x13F0: "\u13f0",             // CHEROKEE LETTER YE
	0x13F1: "\u13f1",             // CHEROKEE LETTER YI
	0x13F2: "\u13f2",             // CHEROKEE LETTER YO
	0x13F3: "\u13f3",             // CHEROKEE LETTER YU
	0x13F4: "\u13f4",             // CHEROKEE LETTER YV
	0x13F5: "\u13f5",             // CHEROKEE LETTER MV
	0x13F8: "\u13f0",             // CHEROKEE SMALL LETTER YE
	0x13F9: "\u13f1",             // CHEROKEE SMALL LETTER YI
	0x13FA: "\u13f2",             // CHEROKEE SMALL LETTER YO
	0x13FB: "\u13f3",             // CHEROKEE SMALL LETTER YU
	0x13FC: "\u13f4",             // CHEROKEE SMALL LETTER YV
	0x13FD: "\u13f5",             // CHEROKEE SMALL LETTER MV
	0x1C80: "\u0432",             // CYRILLIC SMALL LETTER ROUNDED VE
	0x1C81: "\u0434",             // CYRILLIC SMALL LETTER LONG-LEGGED DE
	0x1C82: "\u043e",             // CYRILLIC SMALL LETTER NARROW O
	0x1C83: "\u0441",             // CYRILLIC SMALL LETTER WIDE ES
	0x1C84: "\u0442",             // CYRILLIC SMALL LETTER TALL TE
	0x1C85: "\u0442",             // CYRILLIC SMALL LETTER THREE-LEGGED TE
	0x1C86: "\u044a",             // CYRILLIC SMALL LETTER TALL HARD SIGN
	0x1C87: "\u0463",             // CYRILLIC SMALL LETTER TALL YAT
	0x1C88: "\ua64b",             // CYRILLIC SMALL LETTER UNBLENDED UK
	0x1E96: "\u0068\u0331",       // LATIN SMALL LETTER H WITH LINE BELOW
	0x1E97: "\u0074\u0308",       // LATIN SMALL LETTER T WITH DIAERESIS
	0x1E98: "\u0077\u030a",       // LATIN SMALL LETTER W WITH RING ABOVE
	0x1E99: "\u0079\u030a",       // LATIN SMALL LETTER Y WITH RING ABOVE
	0x1E9A: "\u0061\u02be",       // LATIN SMALL LETTER A WITH RIGHT HALF RING
	0x1E9B: "\u1e61",             // LATIN SMALL LETTER LONG S WITH DOT ABOVE
	0x1E9E: "\u0073\u0073",       // LATIN CAPITAL LETTER SHARP S
	0x1F50: "\u03c5\u0313",       // GREEK SMALL LETTER UPSILON WITH PSILI
	0x1F52: "\u03c5\u0313\u0300", // GREEK SMALL LETTER UPSILON WITH PSILI AND VARIA
	0x1F54: "\u03c5\u0313\u0301", // GREEK SMALL LETTER UPSILON WITH PSILI AND OXIA
	0x1F56: "\u03c5\u0313\u0342", // GREEK SMALL LETTER UPSILON WITH PSILI AND PERISPOMENI
	0x1F80: "\u1f00\u03b9",       // GREEK SMALL LETTER ALPHA WITH PSILI AND YPOGEGRAMMENI
	0x1F81: "\u1f01\u03b9",       // GREEK SMALL LETTER ALPHA WITH DASIA AND YPOGEGRAMMENI
	0x1F82: "\u1f02\u03b9",       // GREEK SMALL LETTER ALPHA WITH PSILI AND VARIA AND YPOGEGRAMMENI
	0x1F83: "\u1f03\u03b9",       // GREEK SMALL LETTER ALPHA WITH DASIA AND VARIA AND YPOGEGRAMMENI
	0x1F84: "\u1f04\u03b9",       // GREEK SMALL LETTER ALPHA WITH PSILI AND OXIA AND YPOGEGRAMMENI
	0x1F85: "\u1f05\u03b9",       // GREEK SMALL LETTER ALPHA WITH DASIA AND OXIA AND YPOGEGRAMMENI
	0x1F86: "\u1f06\u03b9",       // GREEK SMALL LETTER ALPHA WITH PSILI AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F87: "\u1f07\u03b9",       // GREEK SMALL LETTER ALPHA WITH DASIA AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F88: "\u1f00\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND PROSGEGRAMMENI
	0x1F89: "\u1f01\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND PROSGEGRAMMENI
	0x1F8A: "\u1f02\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND VARIA AND PROSGEGRAMMENI
	0x1F8B: "\u1f03\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND VARIA AND PROSGEGRAMMENI
	0x1F8C: "\u1f04\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND OXIA AND PROSGEGRAMMENI
	0x1F8D: "\u1f05\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND OXIA AND PROSGEGRAMMENI
	0x1F8E: "\u1f06\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH PSILI AND PERISPOMENI AND PROSGEGRAMMENI
	0x1F8F: "\u1f07\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH DASIA AND PERISPOMENI AND PROSGEGRAMMENI
	0x1F90: "\u1f20\u03b9",       // GREEK SMALL LETTER ETA WITH PSILI AND YPOGEGRAMMENI
	0x1F91: "\u1f21\u03b9",       // GREEK SMALL LETTER ETA WITH DASIA AND YPOGEGRAMMENI
	0x1F92: "\u1f22\u03b9",       // GREEK SMALL LETTER ETA WITH PSILI AND VARIA AND YPOGEGRAMMENI
	0x1F93: "\u1f23\u03b9",       // GREEK SMALL LETTER ETA WITH DASIA AND VARIA AND YPOGEGRAMMENI
	0x1F94: "\u1f24\u03b9",       // GREEK SMALL LETTER ETA WITH PSILI AND OXIA AND YPOGEGRAMMENI
	0x1F95: "\u1f25\u03b9",       // GREEK SMALL LETTER ETA WITH DASIA AND OXIA AND YPOGEGRAMMENI
	0x1F96: "\u1f26\u03b9",       // GREEK SMALL LETTER ETA WITH PSILI AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F97: "\u1f27\u03b9",       // GREEK SMALL LETTER ETA WITH DASIA AND PERISPOMENI AND YPOGEGRAMMENI
	0x1F98: "\u1f20\u03b9",       // GREEK CAPITAL LETTER ETA WITH PSILI AND PROSGEGRAMMENI
	0x1F99: "\u1f21\u03b9",       // GREEK CAPITAL LETTER ETA WITH DASIA AND PROSGEGRAMMENI
	0x1F9A: "\u1f22\u03b9",       // GREEK CAPITAL LETTER ETA WITH PSILI AND VARIA AND PROSGEGRAMMENI
	0x1F9B: "\u1f23\u03b9",       // GREEK CAPITAL LETTER ETA WITH DASIA AND VARIA AND PROSGEGRAMMENI
	0x1F9C: "\u1f24\u03b9",       // GREEK CAPITAL LETTER ETA WITH PSILI AND OXIA AND PROSGEGRAMMENI
	0x1F9D: "\u1f25\u03b9",       // GREEK CAPITAL LETTER ETA WITH DASIA AND OXIA AND PROSGEGRAMMENI
	0x1F9E: "\u1f26\u03b9",       // GREEK CAPITAL LETTER ETA WITH PSILI AND PERISPOMENI AND PROSGEGRAMMENI
	0x1F9F: "\u1f27\u03b9",       // GREEK CAPITAL LETTER ETA WITH DASIA AND PERISPOMENI AND PROSGEGRAMMENI
	0x1FA0: "\u1f60\u03b9",       // GREEK SMALL LETTER OMEGA WITH PSILI AND YPOGEGRAMMENI
	0x1FA1: "\u1f61\u03b9",       // GREEK SMALL LETTER OMEGA WITH DASIA AND YPOGEGRAMMENI
	0x1FA2: "\u1f62\u03b9",       // GREEK SMALL LETTER OMEGA WITH PSILI AND VARIA AND YPOGEGRAMMENI
	0x1FA3: "\u1f63\u03b9",       // GREEK SMALL LETTER OMEGA WITH DASIA AND VARIA AND YPOGEGRAMMENI
	0x1FA4: "\u1f64\u03b9",       // GREEK SMALL LETTER OMEGA WITH PSILI AND OXIA AND YPOGEGRAMMENI
	0x1FA5: "\u1f65\u03b9",       // GREEK SMALL LETTER OMEGA WITH DASIA AND OXIA AND YPOGEGRAMMENI
	0x1FA6: "\u1f66\u03b9",       // GREEK SMALL LETTER OMEGA WITH PSILI AND PERISPOMENI AND YPOGEGRAMMENI
	0x1FA7: "\u1f67\u03b9",       // GREEK SMALL LETTER OMEGA WITH DASIA AND PERISPOMENI AND YPOGEGRAMMENI
	0x1FA8: "\u1f60\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND PROSGEGRAMMENI
	0x1FA9: "\u1f61\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND PROSGEGRAMMENI
	0x1FAA: "\u1f62\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND VARIA AND PROSGEGRAMMENI
	0x1FAB: "\u1f63\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND VARIA AND PROSGEGRAMMENI
	0x1FAC: "\u1f64\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND OXIA AND PROSGEGRAMMENI
	0x1FAD: "\u1f65\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND OXIA AND PROSGEGRAMMENI
	0x1FAE: "\u1f66\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH PSILI AND PERISPOMENI AND PROSGEGRAMMENI
	0x1FAF: "\u1f67\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH DASIA AND PERISPOMENI AND PROSGEGRAMMENI
	0x1FB2: "\u1f70\u03b9",       // GREEK SMALL LETTER ALPHA WITH VARIA AND YPOGEGRAMMENI
	0x1FB3: "\u03b1\u03b9",       // GREEK SMALL LETTER ALPHA WITH YPOGEGRAMMENI
	0x1FB4: "\u03ac\u03b9",       // GREEK SMALL LETTER ALPHA WITH OXIA AND YPOGEGRAMMENI
	0x1FB6: "\u03b1\u0342",       // GREEK SMALL LETTER ALPHA WITH PERISPOMENI
	0x1FB7: "\u03b1\u0342\u03b9", // GREEK SMALL LETTER ALPHA WITH PERISPOMENI AND YPOGEGRAMMENI
	0x1FBC: "\u03b1\u03b9",       // GREEK CAPITAL LETTER ALPHA WITH PROSGEGRAMMENI
	0x1FBE: "\u03b9",             // GREEK PROSGEGRAMMENI
	0x1FC2: "\u1f74\u03b9",       // GREEK SMALL LETTER ETA WITH VARIA AND YPOGEGRAMMENI
	0x1FC3: "\u03b7\u03b9",       // GREEK SMALL LETTER ETA WITH YPOGEGRAMMENI
	0x1FC4: "\u03ae\u03b9",       // GREEK SMALL LETTER ETA WITH OXIA AND YPOGEGRAMMENI
	0x1FC6: "\u03b7\u0342",       // GREEK SMALL LETTER ETA WITH PERISPOMENI
	0x1FC7: "\u03b7\u0342\u03b9", // GREEK SMALL LETTER ETA WITH PERISPOMENI AND YPOGEGRAMMENI
	0x1FCC: "\u03b7\u03b9",       // GREEK CAPITAL LETTER ETA WITH PROSGEGRAMMENI
	0x1FD2: "\u03b9\u0308\u0300", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND VARIA
	0x1FD3: "\u03b9\u0308\u0301", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND OXIA
	0x1FD6: "\u03b9\u0342",       // GREEK SMALL LETTER IOTA WITH PERISPOMENI
	0x1FD7: "\u03b9\u0308\u0342", // GREEK SMALL LETTER IOTA WITH DIALYTIKA AND PERISPOMENI
	0x1FE2: "\u03c5\u0308\u0300", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND VARIA
	0x1FE3: "\u03c5\u0308\u0301", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND OXIA
	0x1FE4: "\u03c1\u0313",       // GREEK SMALL LETTER RHO WITH PSILI
	0x1FE6: "\u03c5\u0342",       // GREEK SMALL LETTER UPSILON WITH PERISPOMENI
	0x1FE7: "\u03c5\u0308\u0342", // GREEK SMALL LETTER UPSILON WITH DIALYTIKA AND PERISPOMENI
	0x1FF2: "\u1f7c\u03b9",       // GREEK SMALL LETTER OMEGA WITH VARIA AND YPOGEGRAMMENI
	0x1FF3: "\u03c9\u03b9",       // GREEK SMALL LETTER OMEGA WITH YPOGEGRAMMENI
	0x1FF4: "\u03ce\u03b9",       // GREEK SMALL LETTER OMEGA WITH OXIA AND YPOGEGRAMMENI
	0x1FF6: "\u03c9\u0342",       // GREEK SMALL LETTER OMEGA WITH PERISPOMENI
	0x1FF7: "\u03c9\u0342\u03b9", // GREEK SMALL LETTER OMEGA WITH PERISPOMENI AND YPOGEGRAMMENI
	0x1FFC: "\u03c9\u03b9",       // GREEK CAPITAL LETTER OMEGA WITH PROSGEGRAMMENI
	0xAB70: "\u13a0",             // CHEROKEE SMALL LETTER A
	0xAB71: "\u13a1",             // CHEROKEE SMALL LETTER E
	0xAB72: "\u13a2",             // CHEROKEE SMALL LETTER I
	0xAB73: "\u13a3",             // CHEROKEE SMALL LETTER O
	0xAB74: "\u13a4",             // CHEROKEE SMALL LETTER U
	0xAB75: "\u13a5",             // CHEROKEE SMALL LETTER V
	0xAB76: "\u13a6",             // CHEROKEE SMALL LETTER GA
	0xAB77: "\u13a7",             // CHEROKEE SMALL LETTER KA
	0xAB78: "\u13a8",             // CHEROKEE SMALL LETTER GE
	0xAB79: "\u13a9",             // CHEROKEE SMALL LETTER GI
	0xAB7A: "\u13aa",             // CHEROKEE SMALL LETTER GO
	0xAB7B: "\u13ab",             // CHEROKEE SMALL LETTER GU
	0xAB7C: "\u13ac",             // CHEROKEE SMALL LETTER GV
	0xAB7D: "\u13ad",             // CHEROKEE SMALL LETTER HA
	0xAB7E: "\u13ae",             // CHEROKEE SMALL LETTER HE
	0xAB7F: "\u13af",             // CHEROKEE SMALL LETTER HI
	0xAB80: "\u13b0",             // CHEROKEE SMALL LETTER HO
	0xAB81: "\u13b1",             // CHEROKEE SMALL LETTER HU
	0xAB82: "\u13b2",             // CHEROKEE SMALL LETTER HV
	0xAB83: "\u13b3",             // CHEROKEE SMALL LETTER LA
	0xAB84: "\u13b4",             // CHEROKEE SMALL LETTER LE
	0xAB85: "\u13b5",             // CHEROKEE SMALL LETTER LI
	0xAB86: "\u13b6",             // CHEROKEE SMALL LETTER LO
	0xAB87: "\u13b7",             // CHEROKEE SMALL LETTER LU
	0xAB88: "\u13b8",             // CHEROKEE SMALL LETTER LV
	0xAB89: "\u13b9",             // CHEROKEE SMALL LETTER MA
	0xAB8A: "\u13ba",             // CHEROKEE SMALL LETTER ME
	0xAB8B: "\u13bb",             // CHEROKEE SMALL LETTER MI
	0xAB8C: "\u13bc",             // CHEROKEE SMALL LETTER MO
	0xAB8D: "\u13bd",             // CHEROKEE SMALL LETTER MU
	0xAB8E: "\u13be",             // CHEROKEE SMALL LETTER NA
	0xAB8F: "\u13bf",             // CHEROKEE SMALL LETTER HNA
	0xAB90: "\u13c0",             // CHEROKEE SMALL LETTER NAH
	0xAB91: "\u13c1",             // CHEROKEE SMALL LETTER NE
	0xAB92: "\u13c2",             // CHEROKEE SMALL LETTER NI
	0xAB93: "\u13c3",             // CHEROKEE SMALL LETTER NO
	0xAB94: "\u13c4",             // CHEROKEE SMALL LETTER NU
	0xAB95: "\u13c5",             // CHEROKEE SMALL LETTER NV
	0xAB96: "\u13c6",             // CHEROKEE SMALL LETTER QUA
	0xAB97: "\u13c7",             // CHEROKEE SMALL LETTER QUE
	0xAB98: "\u13c8",             // CHEROKEE SMALL LETTER QUI
	0xAB99: "\u13c9",             // CHEROKEE SMALL LETTER QUO
	0xAB9A: "\u13ca",             // CHEROKEE SMALL LETTER QUU
	0xAB9B: "\u13cb",             // CHEROKEE SMALL LETTER QUV
	0xAB9C: "\u13cc",             // CHEROKEE SMALL LETTER SA
	0xAB9D: "\u13cd",             // CHEROKEE SMALL LETTER S
	0xAB9E: "\u13ce",             // CHEROKEE SMALL LETTER SE
	0xAB9F: "\u13cf",             // CHEROKEE SMALL LETTER SI
	0xABA0: "\u13d0",             // CHEROKEE SMALL LETTER SO
	0xABA1: "\u13d1",             // CHEROKEE SMALL LETTER SU
	0xABA2: "\u13d2",             // CHEROKEE SMALL LETTER SV
	0xABA3: "\u13d3",             // CHEROKEE SMALL LETTER DA
	0xABA4: "\u13d4",             // CHEROKEE SMALL LETTER TA
	0xABA5: "\u13d5",             // CHEROKEE SMALL LETTER DE
	0xABA6: "\u13d6",             // CHEROKEE SMALL LETTER TE
	0xABA7: "\u13d7",             // CHEROKEE SMALL LETTER DI
	0xABA8: "\u13d8",             // CHEROKEE SMALL LETTER TI
	0xABA9: "\u13d9",             // CHEROKEE SMALL LETTER DO
	0xABAA: "\u13da",             // CHEROKEE SMALL LETTER DU
	0xABAB: "\u13db",             // CHEROKEE SMALL LETTER DV
	0xABAC: "\u13dc",             // CHEROKEE SMALL LETTER DLA
	0xABAD: "\u13dd",             // CHEROKEE SMALL LETTER TLA
	0xABAE: "\u13de",             // CHEROKEE SMALL LETTER TLE
	0xABAF: "\u13df",             // CHEROKEE SMALL LETTER TLI
	0xABB0: "\u13e0",             // CHEROKEE SMALL LETTER TLO
	0xABB1: "\u13e1",             // CHEROKEE SMALL LETTER TLU
	0xABB2: "\u13e2",             // CHEROKEE SMALL LETTER TLV
	0xABB3: "\u13e3",             // CHEROKEE SMALL LETTER TSA
	0xABB4: "\u13e4",             // CHEROKEE SMALL LETTER TSE
	0xABB5: "\u13e5",             // CHEROKEE SMALL LETTER TSI
	0xABB6: "\u13e6",             // CHEROKEE SMALL LETTER TSO
	0xABB7: "\u13e7",             // CHEROKEE SMALL LETTER TSU
	0xABB8: "\u13e8",             // CHEROKEE SMALL LETTER TSV
	0xABB9: "\u13e9",             // CHEROKEE SMALL LETTER WA
	0xABBA: "\u13ea",             // CHEROKEE SMALL LETTER WE
	0xABBB: "\u13eb",             // CHEROKEE SMALL LETTER WI
	0xABBC: "\u13ec",             // CHEROKEE SMALL LETTER WO
	0xABBD: "\u13ed",             // CHEROKEE SMALL LETTER WU
	0xABBE: "\u13ee",             // CHEROKEE SMALL LETTER WV
	0xABBF: "\u13ef",             // CHEROKEE SMALL LETTER YA
	0xFB00: "\u0066\u0066",       // LATIN SMALL LIGATURE FF
	0xFB01: "\u0066\u0069",       // LATIN SMALL LIGATURE FI
	0xFB02: "\u0066\u006c",       // LATIN SMALL LIGATURE FL
	0xFB03: "\u0066\u0066\u0069", // LATIN SMALL LIGATURE FFI
	0xFB04: "\u0066\u0066\u006c", // LATIN SMALL LIGATURE FFL
	0xFB05: "\u0073\u0074",       // LATIN SMALL LIGATURE LONG S T
	0xFB06: "\u0073\u0074",       // LATIN SMALL LIGATURE ST
	0xFB13: "\u0574\u0576",       // ARMENIAN SMALL LIGATURE MEN NOW
	0xFB14: "\u0574\u0565",       // ARMENIAN SMALL LIGATURE MEN ECH
	0xFB15: "\u0574\u056b",       // ARMENIAN SMALL LIGATURE MEN INI
	0xFB16: "\u057e\u0576",       // ARMENIAN SMALL LIGATURE VEW NOW
	0xFB17: "\u0574\u056d",       // ARMENIAN SMALL LIGATURE MEN XEH
}
