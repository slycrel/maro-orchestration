// Package mintground is the Go port of `src/mint_grounding.py` — the
// mint-time grounding lane: evidence receipts stamped onto a lesson from
// the minting run's own tool-event log.
//
// The Python module's docstring carries the design (docs/MINT_GROUNDING_
// DESIGN.md, decided 2026-08-06: annotation with event-log receipts,
// fail-open, no new judge) and the list of known v1 edges that are edges
// ON PURPOSE. None of that is re-argued here; this file is about the
// three places the port had to answer a question the Python did not have
// to ask.
//
// # `\b` in a pattern whose match OFFSETS are load-bearing
//
// Seven of this module's patterns are searched with `finditer` and their
// `m.start()`/`m.end()` handed to a second predicate. RE2 has no
// lookaround and pytext's WordStart/WordEnd CONSUME the boundary
// character, so a transcribed `\b` would report offsets one character
// wide of Python's and, worse, would eat the boundary a NEXT match needs.
// findBounded is the answer, and it is the same answer claimverify
// reached for its symbol patterns: scan candidate starts, anchor the
// match, and read the real extent out of a capture group.
//
// The remaining `\b`s are in patterns used only as booleans
// (`.search(...) -> bool`), where consuming costs nothing, so those keep
// pytext.WordStart/WordEnd directly.
//
// # Python indexes code points, Go indexes bytes
//
// Every slice that Python writes in code points is spelled with
// pytext.Head or lastRunes here: the 160-character claim excerpt, the
// 2000-character event-text cap, the 40-character marker head, the
// 120-character instruction window, the 48-character modal window. The
// match offsets are the exception — they are only ever used to SLICE the
// same string they came from, and byte offsets slice identically.
//
// # An exception is part of the contract
//
// `collect_run_tool_events` wraps only `json.loads` in its try. A call
// record whose top level is a list, or whose `tool_events` is a number,
// raises out of the function — and `ground_lessons_for_run`'s own
// try/except is what turns that into a fail-open empty stamp list. The
// Go signature returns the error rather than swallowing it, because a
// direct caller in Python sees the raise.
package mintground

import (
	"bytes"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// maxEvents bounds memory on pathological runs; a real 12-step benchmark
// run logged ~200 events, so this is a backstop, not a working limit.
//
// It is a `var` rather than a `const` for one reason: the differential
// has to be able to prove the cap FIRES, and the only honest fixture for
// a 4000-event cap is 4001 events, which is a quarter-megabyte of JSON
// travelling through a scenario file for one boolean. The probe drives
// this the same way, so both engines are capped at the same place and
// the shipped value stays pinned by the upstream-scalar guard. This is
// the same seam claimverify's indexMaxDirs opened, for the same reason.
var maxEvents = 4000

const (
	// eventTextCap is the event input/output text kept for the keyword
	// join. Receipts point at the full record on disk, so the trim only
	// affects tie quality, never truth.
	eventTextCap = 2000
	maxReceipts  = 3
	claimExcerpt = 160
)

// Event is one entry of `collect_run_tool_events`'s list.
type Event struct {
	Ref     string
	Name    string
	Input   string
	Output  string
	IsError bool
}

// Claim is one row of `extract_claims`. Sentence is the Python dict's
// `_sentence`: the UNTRIMMED sentence, which is what the tie scorer reads
// while Claim is the 160-character excerpt the stamp shows.
type Claim struct {
	Claim    string
	Family   string
	Sentence string
}

// Stamp is one receipt stamp.
//
// HasNote carries the ABSENCE of the Python dict's "note" key, which is
// not the same as an empty one: the tied-supported stamp has no note at
// all, and the absent-key discipline is what keeps a stamped row
// byte-identical to the pre-grounding shape everywhere it is rendered.
type Stamp struct {
	Claim    string
	Family   string
	Status   string
	Receipts []string
	Note     string
	HasNote  bool
}

// pySpace and pyWord are Python's `\s` and `\w` for a str pattern. Every
// pattern below uses them; a literal `\s` or `\w` in this file would be a
// different character set and the escape census fails the build over it.
const (
	pySpace = pytext.SpaceClass
	pyWord  = pytext.WordClass
)

// --- claim lexicon ---------------------------------------------------------
//
// Narrow on purpose: past-tense method verbs from the five logged
// specimens. Present-tense/imperative forms are excluded by construction
// — advice is not a claim.
//
// A sentence can mint one claim per family — "authenticated fetch" is an
// auth claim AND a fetch claim, and the two can ground differently (B3:
// fetch supported, auth unsupported). The ORDER is the Python tuple's,
// because it is the order the stamps come out in.
var claimFamilies = []struct {
	name string
	re   *regexp.Regexp
}{
	{"auth", wordBounded(`authenticat` + pyWord + `+|logged[- ]in|with credentials`)},
	{"fetch", wordBounded(`fetched|downloaded|retrieved|scraped`)},
	{"test", wordBounded(`tested|tests? (?:passed|ran)|pytest`)},
	{"execute", wordBounded(`executed|ran`)},
	{"probe", wordBounded(`verified|confirmed|probed|validated|measured|checked`)},
}

// wordBounded compiles a pattern whose every alternative both begins and
// ends on a word character, which is what lets the `\b`s be factored out
// of the alternation and answered once.
//
// The leading `\b` is not in the pattern at all — findBounded answers it
// by refusing to try a start position whose preceding character is a word
// character. The trailing one is spelled as pytext.WordEnd, which
// CONSUMES, so the alternation is wrapped in group 1 and findBounded
// reports that group's extent as the match. `\A` is what makes the
// scan's anchored retry mean "leftmost match starting here".
func wordBounded(alts string) *regexp.Regexp {
	return regexp.MustCompile(pytext.PyFoldI(
		`(?i)\A(` + alts + `)` + pytext.WordEnd))
}

// --- claim-shape gate: retrospective mood ----------------------------------
//
// The lexicon above finds the VOCABULARY of a method claim. It cannot
// tell an assertion from advice, because English past participles double
// as adjectives ("verified output"), as tag text ("[recovery-verified]"),
// and as filenames ("wordfreq-verified.txt"). So: a MOOD test, not a
// grammar parse — two token-local vetoes plus one sentence-level
// requirement, all of them narrowing. The measurements and the review
// history behind each are in the Python; they are not restated here.

var (
	// Veto 1: the token is welded into a hyphenated compound — a tag, a
	// slug, or a filename, never a finite verb. "re-verified" is the one
	// real exception, so the "re-" prefix survives.
	//
	// Python's `$` matches at the end OR just before a single trailing
	// newline, which is why the `\n?` is here and not in Go's bare `$`.
	// EQUIVALENT MUTANT (kept, marked `equivalent`): removing it changes
	// nothing, because the only caller reaches this line inside
	// `strings.HasSuffix(before, "-")` — a string ending in `-` cannot
	// also end in a newline. The transcription stays faithful rather
	// than locally-minimal, so the next reader compares it to the Python
	// and not to the caller.
	//
	// NOT case-insensitive upstream, so no PyFoldI: only a lowercase
	// "re-" survives the veto.
	rePrefixedRe = regexp.MustCompile(pytext.WordStart + `re-\n?$`)

	// Veto 2: a modal or infinitive governs the token — "must be
	// checked", "cannot be fetched", "can each be independently
	// checked". Policy about future work, not a report of past work. The
	// `have` arm covers the modal-perfect counterfactual ("could have
	// fetched"), which review round 1 found slipping through into
	// _RETRO_AUX's own `have ... been` arm.
	modalGovernsRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` +
		pytext.WordStart +
		`(?:can|cannot|can't|could|should|shall|must|may|might|will|won't` +
		`|would|to)` + pySpace + `+` +
		`(?:not` + pySpace + `+|never` + pySpace + `+|each` + pySpace +
		`+|then` + pySpace + `+|also` + pySpace + `+)?` +
		`(?:(?:have|has|had|having)` + pySpace + `+)?(?:not` + pySpace + `+)?` +
		`(?:be|been|being|get|become)?` + pySpace + `*` +
		`(?:` + pyWord + `+ly` + pySpace + `+)?\n?$`))

	// Veto 3: polarity. A retrospective marker says the sentence reports;
	// it does not say WHAT it reports, and "the fetch was not
	// authenticated" grounded `supported` off a real event — a record
	// that actively lies. The window is clause-local on purpose.
	//
	// The `\b` that upstream puts after the negator alternation is DROPPED
	// rather than transcribed, and it is dropped by argument, not by
	// eyeball: every path out of the rest of the pattern requires the
	// next character to be whitespace (`\s+` inside the repetition) or
	// requires the string to end (`\s*$` with the repetition taken zero
	// times). Both satisfy `\b` after a word character, and a word
	// character there fails the rest of the pattern anyway. There is no
	// input on which the boundary decides.
	negatorBeforeRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` +
		pytext.WordStart +
		`(?:not|never|no|nothing|none|neither|nor|without|n't)` +
		`(?:` + pySpace + `+(?-i:[` + pytext.WordClassBody + `'\-/%.])+){0,4}` +
		pySpace + `*\n?$`))

	clauseBreakRe = regexp.MustCompile(`[,;:()\[\]{}]|—|--| - `)

	// Sentence-level requirement: an explicit past-tense finite marker.
	// Either an auxiliary ("was fetched", "had been confirmed") or a
	// past-tense verb taking an object ("supplied the data") — the two
	// shapes every logged specimen uses.
	//
	// Both are scanned with findBounded because `_reports_in_main_clause`
	// hands `m.start()` to the embedded-clause test.
	retroAuxRe = wordBounded(
		`(?:was|were|wasn't|weren't|did|didn't)` +
			`|(?:had|hadn't|has|have|having)` + pySpace + `+` +
			`(?:not` + pySpace + `+|already` + pySpace + `+|just` + pySpace +
			`+|only` + pySpace + `+)?(?:been|` + pyWord + `+ed)`)

	// The `\b` upstream puts between `\w{3,}ed` and `\s+` is dropped on
	// the same argument as the negator's: `\s+` requires a non-word
	// character there, which is exactly what the boundary was asserting.
	retroFiniteRe = wordBounded(pyWord + `{3,}ed` + pySpace + `+` +
		`(?:the|a|an|this|that|these|those|its|their|his|her|our` +
		`|my|your|it|them|all|each|every|both|only|` + pytext.DigitClass + `)`)

	// Counterfactual/hypothetical framing reads past-tense but asserts
	// nothing: "treats a partial response as if it were a full, verified
	// fetch". Boolean, so the boundaries may consume.
	hypotheticalRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` +
		pytext.WordStart + `(?:as if|as though|would have|had it)` +
		pytext.WordEnd))

	leadingTagRe = regexp.MustCompile(pytext.PyFoldI(`(?i)\A` + pySpace +
		`*(?:[-*•]` + pySpace + `*|\[[^\]]{0,40}\]` + pySpace + `*|` +
		pytext.DigitClass + `+[.)]` + pySpace + `*|step` + pySpace + `+` +
		pytext.DigitClass + `+` + pySpace + `*[:.]` + pySpace + `*)+`))

	wordRe = regexp.MustCompile(`[A-Za-z][A-Za-z'\-]*`)

	// Commas inside a quotation or a parenthetical are not clause
	// boundaries.
	// The `\"` escapes are upstream's, kept rather than reduced to a bare
	// quote: this literal is compared to the Python character for
	// character by the upstream guard, and `re` and RE2 read `\"` the
	// same way.
	quotedSpanRe = regexp.MustCompile(
		`'[^']*'|\"[^\"]*\"|\([^)]*\)|\[[^\]]*\]|` + "`[^`]*`")
)

// wordSet is Python's `frozenset("...".split())`: whitespace-separated,
// which is why the tables below can be re-wrapped without changing them.
func wordSet(s string) map[string]bool {
	out := map[string]bool{}
	for _, w := range strings.Fields(s) {
		out[w] = true
	}
	return out
}

// The opener tables. Generic instruction vocabulary, not corpus-fitted —
// the durable answer to an open verb class is the vocabulary-INDEPENDENT
// embedded-clause rule in markerIsEmbedded, and this list is the second
// net for orders whose embedded clause reads like a main one.
//
// Sorted here and unsorted upstream: a frozenset has no order and neither
// does a Go map, so the guard compares SETS and the sort is for the
// reader.
var imperativeOpeners = wordSet(`
		add append applies apply avoid break build capture categorize check
		choose click collect commit compare compile compute confirm connect
		continue convert copy count create creates define delete deploy
		describe describes detect determine document download draft embed
		ensure ensures enter enumerate execute extract extracts fetch filter
		flag focus follow gather generate generates give halt handle handles
		identify implement include install invoke issue iterate keep label
		list load locate log loop make manage mark measure move navigate note
		open parse performs pick place plan poll prefer prepare produce
		produces prove provides pull push query quote rank read reconcile
		record recover recovers reject remove render repeat replace report
		require resolve restrict resume retry return reuse review rewrite run
		sample save scan score search select send set ship show skip sort
		specify split start state stop store structure submit summarize
		surface switch tag take test trace track treat try turn type update
		upload use validate verify wait walk watch weigh works write`)

var sequencers = wordSet(`
		additionally afterward afterwards also always finally first fourth
		further furthermore ideally instead lastly never next now optionally
		please second then third`)

var subordinator = wordSet(`
		after at before by during for given if in on once since to unless
		until when whenever while with without`)

var complementizers = wordSet(`
		each every how it that what whether which who whom why`)

var subordinating = wordSet(`
		after although as because before if once since though unless until
		when whenever wherever while`)

var stopwords = wordSet(`
		after all also and are because been before each for from goal had has
		have into its lesson not over run runs session step task that the then
		this was were what when with`)

var verbTokens = wordSet(`
		authenticated checked confirmed credentials downloaded executed
		fetched logged measured passed probed pytest retrieved scraped tested
		validated verified`)

var execTools = wordSet(`
		bash run_command shell`)

var readTools = wordSet(`
		glob grep ls read`)

// --- the clause helpers ----------------------------------------------------

// clauseComma is `_clause_comma`: the first comma that is not inside a
// quotation or a parenthetical.
//
// Python masks each span with as many SPACES as the span has code points,
// so the index it finds is still an index into the original. Masking
// byte-for-byte here keeps the same invariant in Go's units, and the
// result is a byte offset that slices `body` at the same character.
func clauseComma(body string) int {
	masked := []byte(body)
	for _, loc := range quotedSpanRe.FindAllStringIndex(body, -1) {
		for i := loc[0]; i < loc[1]; i++ {
			masked[i] = ' '
		}
	}
	return bytes.IndexByte(masked, ',')
}

// clauseTail is `_clause_tail`: the part of `before` inside the token's
// own clause.
func clauseTail(before string) string {
	locs := clauseBreakRe.FindAllStringIndex(before, -1)
	if len(locs) == 0 {
		return before
	}
	return before[locs[len(locs)-1][1]:]
}

// opensImperatively is `_opens_imperatively`.
//
// The domain's imperative verbs double as nouns — "Build 42 was
// verified", "Commit abc123 was executed" are reports whose subject
// happens to be a listed verb. An auxiliary in the next few tokens means
// the opener is a SUBJECT, so the imperative reading is dropped.
func opensImperatively(words []string) bool {
	if len(words) == 0 || !imperativeOpeners[pytext.Lower(words[0])] {
		return false
	}
	// Python's `words[1:4]` clamps; Go's slice would panic.
	stop := 4
	if stop > len(words) {
		stop = len(words)
	}
	for _, w := range words[1:stop] {
		switch pytext.Lower(w) {
		case "was", "were", "is", "are", "had", "has", "have":
			return false
		}
	}
	return true
}

// isInstruction is `_is_instruction`: true when the sentence's main
// clause orders or describes, rather than reports.
func isInstruction(sentence string) bool {
	body := leadingTagRe.ReplaceAllString(sentence, "")
	words := wordRe.FindAllString(pytext.Head(body, 120), -1)
	for len(words) > 0 && sequencers[pytext.Lower(words[0])] {
		words = words[1:]
	}
	if len(words) == 0 {
		return false
	}
	if opensImperatively(words) {
		return true
	}
	// Subordinate clause first: check the word right after its comma.
	if subordinator[pytext.Lower(words[0])] {
		if i := clauseComma(body); i >= 0 {
			after := wordRe.FindAllString(pytext.Head(body[i+1:], 60), -1)
			for len(after) > 0 && sequencers[pytext.Lower(after[0])] {
				after = after[1:]
			}
			if opensImperatively(after) {
				return true
			}
		}
	}
	return false
}

// markerIsEmbedded is `_marker_is_embedded`.
//
// A retro marker inside a subordinate clause reports nothing about the
// sentence's own mood: "confirm it WAS RETRIEVED in full", "log which
// values WERE TESTED". The complementizer that introduces the clause is a
// closed word class, so this net needs no verb vocabulary at all.
// Sentence-initial "It was fetched from the CDN" is a real subject, so
// position matters.
func markerIsEmbedded(sentence string, start int) bool {
	before := sentence[:start]
	words := wordRe.FindAllString(before, -1)
	if len(words) < 2 { // sentence-initial subject, not a complementizer
		return false
	}
	for _, w := range words[len(words)-2:] {
		if complementizers[pytext.Lower(w)] {
			return true
		}
	}
	// Subordinating conjunctions bind a whole clause, so they count
	// anywhere between the last clause break and the marker.
	for _, w := range wordRe.FindAllString(clauseTail(before), -1) {
		if subordinating[pytext.Lower(w)] {
			return true
		}
	}
	return false
}

// reportsInMainClause is `_reports_in_main_clause`: true when at least one
// retro marker sits outside a subordinate clause.
func reportsInMainClause(sentence string) bool {
	for _, re := range []*regexp.Regexp{retroAuxRe, retroFiniteRe} {
		for _, m := range findBounded(re, sentence) {
			if !markerIsEmbedded(sentence, m.start) {
				return true
			}
		}
	}
	return false
}

// isRetrospective is `_is_retrospective`: true when the sentence reports
// what happened, rather than advising what to do.
func isRetrospective(sentence string) bool {
	if hypotheticalRe.MatchString(sentence) || isInstruction(sentence) {
		return false
	}
	return reportsInMainClause(sentence)
}

// tokenAsserts is `_token_asserts`: true when this lexicon hit reads as a
// verb, not a tag or a policy.
func tokenAsserts(sentence string, start, end int) bool {
	before, after := sentence[:start], sentence[end:]
	if strings.HasSuffix(before, "-") && !rePrefixedRe.MatchString(before) {
		return false
	}
	if strings.HasPrefix(after, "-") {
		return false
	}
	if modalGovernsRe.MatchString(lastRunes(before, 48)) {
		return false
	}
	return !negatorBeforeRe.MatchString(clauseTail(before))
}

// lastRunes is Python's `s[-n:]`, which counts code points. pytext.Head
// is the other half of the same slice and does not spell this one.
func lastRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[len(r)-n:])
}

// --- the boundaried scan ---------------------------------------------------

// span is one `finditer` hit's real extent, in BYTES.
//
// Python reports code points. Both offsets are only ever used to slice
// the same string they came from, and byte offsets slice it at the same
// characters, so the units never surface.
type span struct{ start, end int }

// findBounded is `finditer` for a pattern wordBounded compiled.
//
// RE2 has no lookaround and pytext's WordStart/WordEnd consume, which
// pulls the two boundaries in opposite directions. A consumed LEADING
// boundary shifts the match start, which this module cannot afford
// because the start is handed to markerIsEmbedded. A consumed TRAILING
// boundary shortens the remaining text, so a second match beginning at
// the very next character is silently lost — "was fetched, was checked"
// is not the shape that breaks, but "the run was" followed immediately by
// another marker is.
//
// So: the leading boundary is answered here, by refusing any start whose
// preceding character is a word character and trying an `\A`-anchored
// match at every other one — which is what a leftmost search does anyway.
// The trailing boundary stays inside the pattern, consuming, and group 1
// carries the alternation's real extent so the scan resumes where Python
// would.
func findBounded(re *regexp.Regexp, text string) []span {
	out := []span{}
	for i := 0; i < len(text); {
		if !leadingWordBoundary(text, i) {
			i += runeLen(text, i)
			continue
		}
		loc := re.FindStringSubmatchIndex(text[i:])
		if loc == nil {
			i += runeLen(text, i)
			continue
		}
		out = append(out, span{i + loc[2], i + loc[3]})
		if loc[3] <= 0 {
			// A zero-width match would loop. No alternative any caller
			// compiles can produce one (every one requires at least two
			// characters), and this is the guard that says so.
			i += runeLen(text, i)
			continue
		}
		i += loc[3]
	}
	return out
}

// leadingWordBoundary is `\b` at a position whose NEXT character is a word
// character — true of every alternative wordBounded takes, because each
// begins with a literal word or `\w`. Reduced to its only remaining
// question: is the character BEFORE it a word character?
func leadingWordBoundary(text string, i int) bool {
	if i == 0 {
		return true
	}
	r, _ := utf8.DecodeLastRuneInString(text[:i])
	return !pytext.IsWordChar(r)
}

func runeLen(text string, i int) int {
	_, n := utf8.DecodeRuneInString(text[i:])
	if n == 0 {
		return 1
	}
	return n
}

// splitSentences is `re.split(r"(?<=[.;!?])\s+|\n+", text)`.
//
// The lookbehind is what RE2 cannot spell, so the split is written out:
// at each position, try the first alternative (a whitespace run whose
// preceding character is sentence punctuation), then the second (a
// newline run), then advance one character. That IS what `re.split` does
// — it takes the leftmost match and, at a tie, the earlier alternative —
// and writing it out makes the tie order visible instead of implied.
//
// One consequence worth naming, because the rest of the module leans on
// it: every newline in the text is consumed by one alternative or the
// other, so no sentence this returns can contain one. That is why the
// `$`-anchored vetoes above can treat their input as newline-free.
func splitSentences(text string) []string {
	out := []string{}
	segStart, i := 0, 0
	for i < len(text) {
		r, size := utf8.DecodeRuneInString(text[i:])
		end := -1
		if i > 0 && pytext.IsSpace(r) {
			prev, _ := utf8.DecodeLastRuneInString(text[:i])
			if prev == '.' || prev == ';' || prev == '!' || prev == '?' {
				j := i
				for j < len(text) {
					rr, sz := utf8.DecodeRuneInString(text[j:])
					if !pytext.IsSpace(rr) {
						break
					}
					j += sz
				}
				end = j
			}
		}
		if end < 0 && r == '\n' {
			j := i
			for j < len(text) && text[j] == '\n' {
				j++
			}
			end = j
		}
		if end < 0 {
			i += size
			continue
		}
		out = append(out, text[segStart:i])
		segStart, i = end, end
	}
	return append(out, text[segStart:])
}

// --- the event predicates --------------------------------------------------

var (
	netCmdRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` +
		pytext.WordStart + `(?:curl|wget)` + pytext.WordEnd + `|` +
		pytext.WordStart + `https?://`))

	// Credential-shaped markers only. Bare "token" is NOT enough: the
	// live smoke against LT-5's own specimen run found `token=a` in a
	// public syndication-CDN URL — a dummy parameter, no credential — and
	// the loose regex marked the "authenticated fetch" claim supported,
	// recreating the exact B3 false-support this module exists to refuse.
	//
	// `[A-Za-z0-9_\-]` carries pytext.IClassBody because Python's
	// IGNORECASE folds `i` to `ı` and `İ` and Go's does not: swept on this
	// box, `re.fullmatch(r'[A-Za-z]', 'ı', re.I)` is a match and Go's
	// `(?i)[A-Za-z]` is not. (`K` and `ſ` fold in BOTH, so those need
	// nothing.) An 8-character run of Turkish dotless i is a silly
	// credential and a real divergence, and the class is the one place
	// PyFoldI cannot fix it for us — a class cannot hold a group.
	authMarkRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` +
		`authorization` + pySpace + `*:` +
		`|bearer` + pySpace + `+` + pytext.NotClass("") +
		`|x-api-key` +
		`|api[_-]?key['\"]?` + pySpace + `*[=:]` + pySpace + `*['\"]?` +
		`[A-Za-z0-9_\-` + pytext.IClassBody + `]{8,}` +
		`|token['\"]?` + pySpace + `*[=:]` + pySpace + `*['\"]?` +
		`[A-Za-z0-9_\-` + pytext.IClassBody + `]{8,}` +
		`|--user` + pytext.WordEnd +
		`|-u` + pySpace + `+` + pytext.NotClass("") + `+:` +
		pytext.NotClass("") + `+` +
		`|--cookie` + pytext.WordEnd +
		`|-b` + pySpace + `+` + pytext.NotClass("") +
		// Assigned-value forms only: the bare words matched an anonymous
		// `curl .../login` URL path — the same false-support class as the
		// pinned `token=a`.
		`|passw` + pyWord + `*['\"]?` + pySpace + `*[=:]` + pySpace + `*` +
		pytext.NotClass("") +
		`|credentials?['\"]?` + pySpace + `*[=:]` + pySpace + `*` +
		pytext.NotClass("")))

	testCmdRe = regexp.MustCompile(pytext.PyFoldI(`(?i)` +
		pytext.WordStart + `pytest` + pytext.WordEnd +
		`|test[-_]safe` +
		`|` + pytext.WordStart + `(?:npm test|go test|unittest|make test)` +
		pytext.WordEnd))

	tieTokenRe = regexp.MustCompile(`[a-z0-9_.\-/]{4,}`)

	// Identifier-shaped: hosts, paths, filenames, ids — the token shapes
	// that make a claim about a PARTICULAR thing rather than generic
	// prose ("content", "data"). These are what a false receipt would
	// misattribute.
	identifierShapedRe = regexp.MustCompile(
		`[./_\-]|` + pytext.DigitClass)
)

func isExec(ev Event) bool { return execTools[pytext.Lower(ev.Name)] }

func isFetch(ev Event) bool {
	name := pytext.Lower(ev.Name)
	if strings.Contains(name, "fetch") || strings.Contains(name, "search") {
		return true
	}
	return isExec(ev) && netCmdRe.MatchString(ev.Input)
}

// isAuth: auth is fetch-with-auth-material, not its own tool. An
// "authenticated fetch" claim needs a network event that carried
// credentials (B3's unauthenticated r.jina.ai render is exactly what this
// refuses).
func isAuth(ev Event) bool {
	return isFetch(ev) && authMarkRe.MatchString(ev.Input)
}

func isTest(ev Event) bool {
	return isExec(ev) && testCmdRe.MatchString(ev.Input)
}

// isProbe: any active look at the world — command, network, or file
// inspection.
func isProbe(ev Event) bool {
	return isExec(ev) || isFetch(ev) || readTools[pytext.Lower(ev.Name)]
}

// familyRules is `_FAMILY_RULES`: family -> (event predicate, status when
// candidates exist but none tie).
//
// The specific families (a fetch event IS what "fetched" asserts, at run
// scope) stay supported on family-level presence; the vague probe verbs
// ("verified", "confirmed") only reach supported through a keyword tie —
// B1w's "blocks confirmed this session" must not be supported by an
// unrelated Bash event.
var familyRules = map[string]struct {
	pred   func(Event) bool
	untied string
}{
	"auth":    {isAuth, "supported"},
	"fetch":   {isFetch, "supported"},
	"test":    {isTest, "supported"},
	"execute": {isExec, "supported"},
	"probe":   {isProbe, "unprobed"},
}

// tieTokens is `_tie_tokens`. Lexicon verbs never count as tie tokens —
// every candidate would match.
func tieTokens(sentence string) []string {
	out := []string{}
	for _, t := range tieTokenRe.FindAllString(pytext.Lower(sentence), -1) {
		if !stopwords[t] && !verbTokens[t] {
			out = append(out, t)
		}
	}
	return out
}

// specificTokens is `_specific_tokens`. Boundary punctuation is sentence
// syntax, not identifier shape — "fetch." must not read as specific while
// vendor.example does.
func specificTokens(toks []string) []string {
	out := []string{}
	for _, t := range toks {
		if identifierShapedRe.MatchString(strings.Trim(t, "./_-")) {
			out = append(out, t)
		}
	}
	return out
}

// --- the run's ground truth ------------------------------------------------

// CollectRunToolEvents loads every tool event from a run dir's call
// records, with receipt refs.
//
// The second return is the None/[] distinction Python makes with a real
// `Optional`: FALSE means the run has no readable `build/calls/`, so
// there is no ground truth and nothing may be stamped (absent, not
// unsupported-everything). TRUE with an empty slice means call records
// exist and logged zero tool events — that IS ground truth, an LLM-only
// run affirmatively did not probe anything.
//
// The error is the third thing Python's signature does not say. Only
// `json.loads` sits inside the upstream try; `record.get(...)` and the
// `enumerate` around it do not, so a call record whose top level is a
// list, or whose `tool_events` is a number, RAISES out of this function.
// GroundLessonsForRun's own try is what makes that fail-open; a direct
// caller sees it.
func CollectRunToolEvents(runDir string) ([]Event, bool, error) {
	callsDir := pypath.Join(pypath.Join(runDir, "build"), "calls")
	st, err := os.Stat(callsDir)
	if err != nil || !st.IsDir() {
		return nil, false, nil
	}
	// `Path.glob` swallows the OSError from a directory it cannot read
	// and yields nothing, rather than raising. is_dir() has already
	// succeeded here, so this is the unreadable-directory case only.
	ents, _ := os.ReadDir(callsDir)
	names := []string{}
	for _, e := range ents {
		if pytext.FnMatch(e.Name(), "call-*.json") {
			names = append(names, e.Name())
		}
	}
	// `sorted(calls_dir.glob(...))` sorts Path objects, which for
	// siblings of one directory is their filenames in code-point order —
	// scandir order is arbitrary and the sort is what makes the receipt
	// refs reproducible.
	sort.Slice(names, func(i, j int) bool {
		return pypath.FSLess(names[i], names[j])
	})

	events := []Event{}
	for _, name := range names {
		raw, err := os.ReadFile(pypath.Join(callsDir, name))
		if err != nil {
			continue
		}
		rec, err := pyval.LoadsOrdered(pytext.DecodeReplace(raw))
		if err != nil {
			continue
		}
		obj, ok := rec.(pyval.Obj)
		if !ok {
			return nil, false, &pyval.PyErr{Class: "AttributeError",
				Msg: "'" + pyval.TypeName(rec) +
					"' object has no attribute 'get'"}
		}
		raws, _ := obj.Get("tool_events")
		if !pyval.Truthy(raws) {
			raws = pyval.List{}
		}
		items, err := enumerable(raws)
		if err != nil {
			return nil, false, err
		}
		for i, it := range items {
			ev, ok := it.(pyval.Obj)
			if !ok {
				continue
			}
			events = append(events, Event{
				Ref:     "build/calls/" + name + "#tool_events[" + strconv.Itoa(i) + "]",
				Name:    pyval.Str(getOr(ev, "name", "")),
				Input:   pytext.Head(pyval.Str(getOr(ev, "input", "")), eventTextCap),
				Output:  pytext.Head(pyval.Str(getOr(ev, "output", "")), eventTextCap),
				IsError: isErrorFlag(ev),
			})
			if len(events) >= maxEvents {
				// Upstream logs at debug here and returns. The log line is
				// the only thing dropped: this port carries no logger and
				// the cap is otherwise observable in the returned list.
				return events, true, nil
			}
		}
	}
	return events, true, nil
}

// getOr is `d.get(key, def)`: a MISSING key takes the default, a present
// one does not — including a present None, which str() renders "None".
func getOr(o pyval.Obj, key string, def any) any {
	if v, ok := o.Get(key); ok {
		return v
	}
	return def
}

// isErrorFlag is `ev.get("is_error") is True or
// str(ev.get("is_error", "")).lower() == "true"`.
//
// The identity test is not a truthiness test: JSON `1` decodes to an int
// and `1 is True` is False in Python, so a record with `"is_error": 1` is
// NOT an error by the first arm — and is not by the second either, since
// str(1) is "1". Only the literal `true` and the strings that spell it
// count.
func isErrorFlag(ev pyval.Obj) bool {
	if v, ok := ev.Get("is_error"); ok {
		if b, isBool := v.(bool); isBool && b {
			return true
		}
	}
	return pytext.Lower(pyval.Str(getOr(ev, "is_error", ""))) == "true"
}

// enumerable is `enumerate(x)` for the values `record.get("tool_events")
// or []` can hand it.
//
// Only a list can carry real events, but the other iterables are not
// errors and the difference is observable: a dict enumerates its KEYS and
// a string its characters, neither of which is a dict, so both produce
// zero events and no exception. A number or a bool raises, and the
// message names the type.
func enumerable(v any) ([]any, error) {
	switch t := v.(type) {
	case pyval.List:
		return []any(t), nil
	case []any:
		return t, nil
	case string:
		out := []any{}
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	case pyval.Obj:
		out := []any{}
		for _, f := range t {
			out = append(out, f.Key)
		}
		return out, nil
	}
	return nil, &pyval.PyErr{Class: "TypeError",
		Msg: "'" + pyval.TypeName(v) + "' object is not iterable"}
}

// --- extraction and the join -----------------------------------------------

// ExtractClaims returns the past-tense method claims in text, one per
// (sentence, family).
//
// Two-stage by design: the sentence must be retrospective at all, and the
// lexicon hit inside it must read as a verb rather than an adjective, a
// tag, or a modal policy.
func ExtractClaims(text string) []Claim {
	claims := []Claim{}
	for _, raw := range splitSentences(text) {
		sentence := pytext.Strip(raw)
		if sentence == "" || !isRetrospective(sentence) {
			continue
		}
		for _, fam := range claimFamilies {
			for _, m := range findBounded(fam.re, sentence) {
				if tokenAsserts(sentence, m.start, m.end) {
					claims = append(claims, Claim{
						Claim:    pytext.Head(sentence, claimExcerpt),
						Family:   fam.name,
						Sentence: sentence,
					})
					// Python's `any(...)` short-circuits, and one claim
					// per (sentence, family) is the contract.
					break
				}
			}
		}
	}
	return claims
}

// GroundText joins each claim in text against events and returns receipt
// stamps. The result is empty when the text makes no parseable claims —
// the absent-key discipline upstream keeps stampless rows byte-identical
// to before.
func GroundText(text string, events []Event) []Stamp {
	stamps := []Stamp{}
	for _, c := range ExtractClaims(text) {
		rule := familyRules[c.Family]
		// An error'd event is evidence of an attempt, not of the method
		// the claim asserts.
		cands := []Event{}
		for _, e := range events {
			if !e.IsError && rule.pred(e) {
				cands = append(cands, e)
			}
		}
		if len(cands) == 0 {
			stamps = append(stamps, Stamp{
				Claim: c.Claim, Family: c.Family,
				Status: "unsupported", Receipts: []string{},
				Note: "no " + c.Family +
					"-family tool events in the minting run",
				HasNote: true,
			})
			continue
		}
		toks := tieTokens(c.Sentence)
		type hit struct {
			score int
			ev    Event
		}
		scored := []hit{}
		for _, e := range cands {
			evText := pytext.Lower(e.Name + " " + e.Input + " " + e.Output)
			score := 0
			for _, t := range toks {
				if strings.Contains(evText, t) {
					score++
				}
			}
			if score != 0 {
				scored = append(scored, hit{score, e})
			}
		}
		// `scored.sort(key=lambda se: -se[0])` — Python's sort is STABLE,
		// so events with equal scores keep call-record order, and that
		// order is what the top-three receipts are cut from.
		sort.SliceStable(scored, func(i, j int) bool {
			return scored[i].score > scored[j].score
		})
		switch {
		case len(scored) > 0:
			stop := maxReceipts
			if stop > len(scored) {
				stop = len(scored)
			}
			receipts := []string{}
			for _, s := range scored[:stop] {
				receipts = append(receipts, s.ev.Ref)
			}
			stamps = append(stamps, Stamp{
				Claim: c.Claim, Family: c.Family,
				Status: "supported", Receipts: receipts,
			})
		case rule.untied == "supported" && len(specificTokens(toks)) == 0:
			// Family-level support only for GENERIC claims: "content was
			// fetched" asserts nothing a family event can't cover. A claim
			// that names an identifier-shaped specific (a host, a path, an
			// id) which ties to NO candidate must not be stamped supported
			// off an unrelated event — that attaches affirmatively wrong
			// evidence, the exact false-support class this module refuses
			// ("fetched from api-a" must not carry api-b's receipt).
			stamps = append(stamps, Stamp{
				Claim: c.Claim, Family: c.Family,
				Status: "supported", Receipts: []string{cands[0].Ref},
				Note:    "family-level match; claim names no specifics",
				HasNote: true,
			})
		default:
			stamps = append(stamps, Stamp{
				Claim: c.Claim, Family: c.Family,
				Status: "unprobed", Receipts: []string{},
				Note: strconv.Itoa(len(cands)) + " " + c.Family +
					"-family events in the run; none tie to this claim's" +
					" specifics",
				HasNote: true,
			})
		}
	}
	return stamps
}

// Deps is the one seam this module has: `from runs import
// resolve_run_dir`, imported INSIDE the try so a broken import is a
// fail-open empty result rather than a crash at mint time.
//
// A nil ResolveRunDir is that unresolved import. The bool is the
// function's own None return — an unresolvable run reference.
type Deps struct {
	ResolveRunDir func(runRef string) (string, bool, error)
}

// GroundLessonsForRun returns grounding stamps for each lesson, joined
// against runRef's events.
//
// runRef is a loop_id or handle_id (resolve_run_dir accepts both). Any
// failure — unresolvable run, unreadable records, a call record that
// raises — returns empty stamp lists: fail-open, the mint proceeds
// unstamped.
func GroundLessonsForRun(lessonTexts []string, runRef string, d Deps) [][]Stamp {
	empty := make([][]Stamp, len(lessonTexts))
	for i := range empty {
		empty[i] = []Stamp{}
	}
	if runRef == "" || len(lessonTexts) == 0 {
		return empty
	}
	if d.ResolveRunDir == nil {
		return empty
	}
	rd, ok, err := d.ResolveRunDir(runRef)
	if err != nil || !ok {
		return empty
	}
	events, present, err := CollectRunToolEvents(rd)
	if err != nil || !present {
		return empty
	}
	out := make([][]Stamp, 0, len(lessonTexts))
	for _, t := range lessonTexts {
		out = append(out, GroundText(t, events))
	}
	return out
}

// HasUnsupported is `has_unsupported`.
func HasUnsupported(grounding []Stamp) bool {
	for _, g := range grounding {
		if g.Status == "unsupported" {
			return true
		}
	}
	return false
}

// GroundingSummary is the one-glance readout tag: ` [claims: 2✓/1✗/1?]`
// (supported/unsupported/unprobed).
//
// Empty when there are no stamps — pre-grounding rows render
// byte-identically. This is also the census surface for the design doc's
// >30%-unprobed falsifier, the v2-LLM-extraction trigger.
func GroundingSummary(grounding []Stamp) string {
	if len(grounding) == 0 {
		return ""
	}
	n := map[string]int{"supported": 0, "unsupported": 0, "unprobed": 0}
	for _, g := range grounding {
		if _, ok := n[g.Status]; ok {
			n[g.Status]++
		}
	}
	return " [claims: " + strconv.Itoa(n["supported"]) + "✓/" +
		strconv.Itoa(n["unsupported"]) + "✗/" +
		strconv.Itoa(n["unprobed"]) + "?]"
}

// GroundingMarker is the compact inline marker for injection surfaces.
//
// Only unsupported claims earn prompt space — a supported stamp is the
// quiet case, and unprobed is honest uncertainty, not a warning.
func GroundingMarker(grounding []Stamp) string {
	bad := []Stamp{}
	for _, g := range grounding {
		if g.Status == "unsupported" {
			bad = append(bad, g)
		}
	}
	if len(bad) == 0 {
		return ""
	}
	stop := 2
	if stop > len(bad) {
		stop = len(bad)
	}
	heads := []string{}
	for _, b := range bad[:stop] {
		heads = append(heads, pytext.Head(b.Claim, 40))
	}
	plural := "s"
	if len(bad) == 1 {
		plural = ""
	}
	return " [mint-grounding: " + strconv.Itoa(len(bad)) + " claim" + plural +
		" unsupported by the minting run's event log: \"" +
		strings.Join(heads, "; ") + "\"]"
}
