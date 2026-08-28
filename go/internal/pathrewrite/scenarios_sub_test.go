package pathrewrite

import "strings"

// prSubstituteScenarios exercises the matcher, which is the one part of
// the module the port could not copy: Python compiles a single pattern
// with an alternation of LOOKBEHINDS, and Go's regexp has none. Every
// row here is a property of that pattern, not of the Go scan.
func prSubstituteScenarios() []prSpec {
	one := [][]string{{prMU, prNewMU}}
	return []prSpec{
		sub("no-pairs-returns-the-input-untouched", [][]string{},
			"anything at all "+prMU),
		sub("an-empty-buffer", one, ""),
		sub("the-whole-buffer-is-the-root", one, prMU),
		sub("a-match-at-the-very-start", one, prMU+"/x and more"),
		sub("a-match-at-the-very-end", one, "recorded at "+prMU),
		sub("a-slash-after-the-match-is-a-boundary", one, prMU+"/lessons"),
		// `.` is deliberately allowed on the right so prose rewrites.
		sub("a-dot-after-the-match-is-a-boundary", one, "at "+prMU+"."),
		sub("a-letter-after-the-match-blocks", one, prMU+"acceptance"),
		sub("a-hyphen-after-the-match-blocks", one, prMU+"-acceptance-probe"),
		sub("an-underscore-after-the-match-blocks", one, prMU+"_old"),
		sub("a-digit-after-the-match-blocks", one, prMU+"2"),
		sub("a-space-before-the-match-is-a-boundary", one, "at "+prMU),
		sub("a-quote-before-the-match-is-a-boundary", one, `"`+prMU+`"`),
		sub("a-letter-before-the-match-blocks", one, "x"+prMU),
		sub("a-slash-before-the-match-blocks", one, "/mnt/backup"+prMU),
		sub("a-dot-before-the-match-blocks", one, "."+prMU),
		sub("a-hyphen-before-the-match-blocks", one, "-"+prMU),
		sub("an-underscore-before-the-match-blocks", one, "_"+prMU),
		sub("a-newline-byte-before-the-match-is-a-boundary", one,
			"line\n"+prMU),
		// The five two-character escape sequences, spelled as the LITERAL
		// backslash-plus-letter they are in a JSON string. This is the
		// exemption the first live box→Mac import needed: 5,543
		// occurrences sat behind a `\n`.
		sub("a-literal-backslash-n-before-the-match", one, `x\n`+prMU),
		sub("a-literal-backslash-t-before-the-match", one, `x\t`+prMU),
		sub("a-literal-backslash-r-before-the-match", one, `x\r`+prMU),
		sub("a-literal-backslash-f-before-the-match", one, `x\f`+prMU),
		sub("a-literal-backslash-v-before-the-match", one, `x\v`+prMU),
		sub("a-literal-backslash-b-before-the-match", one, `x\b`+prMU),
		// Not one of the six: the character before the root is a plain
		// letter and the escape exemption does not apply.
		sub("a-literal-backslash-x-before-the-match-blocks", one,
			`y\x`+prMU),
		// The escape is only an exemption when the backslash is really
		// there — `an` at the end of a word must still block.
		sub("a-bare-n-before-the-match-blocks", one, "an"+prMU),
		// A backslash alone IS outside the blocking class, so it is a
		// boundary in its own right and needs no exemption.
		sub("a-lone-backslash-before-the-match", one, `x\`+prMU),
		sub("two-occurrences-are-both-counted", one,
			prMU+"/a and "+prMU+"/b"),
		sub("adjacent-occurrences", one, prMU+prMU),
		// One pass, not one per pair: the destination CONTAINS the source
		// and must not be rewritten again.
		sub("the-destination-is-never-re-entered",
			[][]string{{"/srv/old", "/srv/new/srv/old"}}, "/srv/old/x"),
		// Longest source first, so the nested root wins over its parent.
		sub("the-nested-root-wins-over-its-parent", prPairs(),
			prWS+"/memory"),
		sub("the-parent-root-still-fires-on-its-own", prPairs(),
			prMU+"/config.yml"),
		// The backtracking row. `/srv/ab/c` is blocked by the `x`, and
		// `re` retries the SHORTER alternative at the same position,
		// where the right boundary is a `/` and the match stands.
		sub("a-blocked-longer-source-falls-through-to-a-shorter",
			[][]string{{"/srv/ab/c", "/LONG"}, {"/srv/ab", "/SHORT"}},
			"/srv/ab/cx"),
		sub("the-longer-source-wins-when-it-is-not-blocked",
			[][]string{{"/srv/ab/c", "/LONG"}, {"/srv/ab", "/SHORT"}},
			"/srv/ab/c/d"),
		// A failed match advances ONE byte, not the candidate's length —
		// the overlapping-prefix case a length-skip gets wrong.
		sub("a-failed-match-advances-a-single-byte",
			[][]string{{"/aa", "/Z"}}, "//aa"),
		// re.escape: a source is a LITERAL, so its dot matches only a dot.
		sub("a-dot-in-the-source-is-not-a-wildcard",
			[][]string{{"/srv/a.b", "/Z"}}, "/srv/aXb and /srv/a.b"),
		sub("a-plus-in-the-source-is-literal",
			[][]string{{"/srv/a+b", "/Z"}}, "/srv/a+b"),
		sub("a-bracket-in-the-source-is-literal",
			[][]string{{"/srv/a[b]", "/Z"}}, "/srv/a[b]"),
		// A UTF-8 continuation byte is not in the blocking class, so a
		// non-ASCII character before the root is a boundary. The scan is
		// over BYTES on both sides, and this is the row that says so.
		sub("a-multibyte-character-before-the-match-is-a-boundary", one,
			"é"+prMU),
		sub("a-non-ascii-source-root",
			[][]string{{"/srv/wsé", "/x"}}, "at /srv/wsé/notes"),
		// A high byte after the match is likewise outside the right-hand
		// class, so it does not block.
		sub("a-multibyte-character-after-the-match", one, prMU+"é"),
		sub("bytes-that-are-not-valid-utf8-around-a-match", one,
			"\xff\xfe"+prMU+"\xff"),
		sub("a-nul-byte-around-a-match", one, "\x00"+prMU+"\x00"),
		sub("many-occurrences", one,
			strings.Repeat(prMU+" ", 50)),
		sub("a-source-that-is-a-single-slash",
			[][]string{{"/", "/Z"}}, "/a/b"),
		{Name: "substitute-text-round-trips-unicode",
			Kind: "substitute_text", Pairs: [][]string{{prMU, prNewMU}},
			Text: "héllo " + prMU + "/x — ok"},
		{Name: "substitute-text-with-no-match", Kind: "substitute_text",
			Pairs: [][]string{{prMU, prNewMU}}, Text: "nothing here"},
		{Name: "substitute-text-with-no-pairs", Kind: "substitute_text",
			Pairs: [][]string{}, Text: prMU},
	}
}
