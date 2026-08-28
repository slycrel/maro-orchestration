package mintground

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Fifteen patterns and nine word tables are CONTENT keys, and a
// differential can only SAMPLE them: it exercises the verbs, openers and
// flags a fixture happens to spell and cannot tell a complete copy from a
// plausible one. `make test` proved that inside an hour — the whole
// alternative was missing from the port and every one of the two hundred
// scenarios agreed, right up until the one that named it.
//
// So these tests read the literals back out of the Python. Where the
// port's spelling is a MECHANICAL transformation of upstream's — a `\b`
// factored out, a `\s` widened, a class fold-proofed — the transformation
// is applied here and the result compared character for character.
// Pinning a hand-typed copy would pass just as happily against a table
// that had drifted.

func upstreamSource(t *testing.T) string {
	t.Helper()
	p := filepath.Join(pyprobe.SrcDir(t, "mint_grounding.py"),
		"mint_grounding.py")
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// --- reading literals out of the Python ------------------------------------

// upstreamPattern reassembles a `name = re.compile(r"…")` literal,
// including the adjacent-string form Python concatenates.
func upstreamPattern(t *testing.T, src, name string) string {
	t.Helper()
	i := strings.Index(src, "\n"+name+" = re.compile(")
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = re.compile(…)", name)
	}
	rest := src[i+len("\n"+name+" = re.compile("):]
	// `)\n` first: a single-line `re.compile(r"…")` closes there, and
	// looking for `\n)` instead would run past it into the NEXT pattern's
	// multi-line close and reassemble both as one.
	j := strings.Index(rest, ")\n")
	if k := strings.Index(rest, "\n)"); j < 0 || (k >= 0 && k < j) {
		if k >= 0 {
			j = k
		}
	}
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	return joinRawStrings(t, name, rest[:j+1])
}

var rawStringRe = regexp.MustCompile(`r"((?:[^"\\]|\\.)*)"`)

func joinRawStrings(t *testing.T, name, blob string) string {
	t.Helper()
	var b strings.Builder
	for _, m := range rawStringRe.FindAllStringSubmatch(blob, -1) {
		b.WriteString(m[1])
	}
	if b.Len() == 0 {
		t.Fatalf("%s literal reassembled to nothing", name)
	}
	return b.String()
}

// frozenWords reads `_NAME = frozenset(...)` in both spellings this module
// uses: a brace set of separate words, and one long string `.split()`.
//
// The two cannot share a reader. Adjacent Python string literals
// concatenate with NO separator, so the split form must be joined the same
// way and only then split on whitespace; doing that to a brace set would
// weld "bash" and "shell" into one word.
func frozenWords(t *testing.T, src, name string) []string {
	t.Helper()
	head := "\n" + name + " = frozenset("
	i := strings.Index(src, head)
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = frozenset(…)", name)
	}
	rest := src[i+len(head):]
	body := strings.TrimLeft(rest, " \n")
	if strings.HasPrefix(body, "{") {
		j := strings.Index(body, "})")
		if j < 0 {
			t.Fatalf("unterminated %s brace set", name)
		}
		out := []string{}
		for _, m := range regexp.MustCompile(`"([^"]*)"`).
			FindAllStringSubmatch(body[:j], -1) {
			out = append(out, m[1])
		}
		sort.Strings(out)
		return out
	}
	j := strings.Index(rest, ".split())")
	if j < 0 {
		t.Fatalf("unterminated %s split set", name)
	}
	var b strings.Builder
	for _, m := range regexp.MustCompile(`"""((?s:.*?))"""|"([^"]*)"`).
		FindAllStringSubmatch(rest[:j], -1) {
		b.WriteString(m[1] + m[2])
	}
	out := strings.Fields(b.String())
	if len(out) == 0 {
		t.Fatalf("%s reassembled to no words", name)
	}
	sort.Strings(out)
	return out
}

func sortedSetKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func assertSameSet(t *testing.T, what string, want, got []string) {
	t.Helper()
	if strings.Join(want, "\x00") == strings.Join(got, "\x00") {
		return
	}
	t.Errorf("%s: upstream has %d words, port has %d\n  upstream %q\n"+
		"  port     %q", what, len(want), len(got), want, got)
}

// --- the substitutions Go forces -------------------------------------------

// subClasses applies the widenings every pattern here needs: Python's
// `\w`, `\s`, `\S` and `\d` are Unicode and Go's are ASCII.
func subClasses(p string) string {
	p = strings.ReplaceAll(p, `\S`, pytext.NotClass(""))
	p = strings.ReplaceAll(p, `\w`, pytext.WordClass)
	p = strings.ReplaceAll(p, `\s`, pytext.SpaceClass)
	return strings.ReplaceAll(p, `\d`, pytext.DigitClass)
}

// splitTop splits an alternation at the `|`s that are not inside a group
// or a character class.
func splitTop(p string) []string {
	out := []string{}
	depth, inClass, start := 0, false, 0
	for i := 0; i < len(p); i++ {
		switch {
		case p[i] == '\\':
			i++
		case inClass:
			if p[i] == ']' {
				inClass = false
			}
		case p[i] == '[':
			inClass = true
		case p[i] == '(':
			depth++
		case p[i] == ')':
			depth--
		case p[i] == '|' && depth == 0:
			out = append(out, p[start:i])
			start = i + 1
		}
	}
	return append(out, p[start:])
}

// boolFactored rewrites a BOOLEAN pattern's `\b`s the way the port does.
//
// A boolean search never reports offsets, so pytext's consuming
// WordStart/WordEnd are safe there — and consecutive alternatives that
// carry the same pair of boundaries are factored into one group, which is
// the only cosmetic liberty the port takes. Alternatives with neither
// boundary pass through untouched, and are NOT grouped, because grouping
// them would change the string without changing the pattern.
func boolFactored(t *testing.T, up string) string {
	t.Helper()
	alts := splitTop(up)
	shape := func(a string) (body string, lead, trail bool) {
		body = a
		if strings.HasPrefix(body, `\b`) {
			body, lead = body[2:], true
		}
		if strings.HasSuffix(body, `\b`) {
			body, trail = body[:len(body)-2], true
		}
		if strings.Contains(body, `\b`) {
			t.Fatalf("an interior \\b this derivation does not model: %q", a)
		}
		return
	}
	out := []string{}
	for i := 0; i < len(alts); {
		_, lead, trail := shape(alts[i])
		group := []string{}
		j := i
		for j < len(alts) {
			b, l, tr := shape(alts[j])
			if l != lead || tr != trail {
				break
			}
			group = append(group, b)
			j++
		}
		body := strings.Join(group, "|")
		if len(group) > 1 && (lead || trail) {
			body = "(?:" + body + ")"
		}
		if lead {
			body = pytext.WordStart + body
		}
		if trail {
			body += pytext.WordEnd
		}
		out = append(out, body)
		i = j
	}
	return strings.Join(out, "|")
}

// scanFactored rewrites a pattern whose OFFSETS are load-bearing.
//
// Here the leading `\b` cannot consume, so it leaves the pattern entirely
// (findBounded answers it) and the trailing one becomes pytext.WordEnd
// outside a capture group that carries the real extent — which is exactly
// what wordBounded builds.
func scanFactored(t *testing.T, up string) string {
	t.Helper()
	alts := splitTop(up)
	bodies := []string{}
	for _, a := range alts {
		if !strings.HasPrefix(a, `\b`) || !strings.HasSuffix(a, `\b`) {
			t.Fatalf("a scanned alternative without both boundaries: %q", a)
		}
		bodies = append(bodies, a[2:len(a)-2])
	}
	return strings.Join(bodies, "|")
}

// --- the tables ------------------------------------------------------------

func TestTheWordTablesMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct {
		name string
		got  map[string]bool
	}{
		{"_IMPERATIVE_OPENERS", imperativeOpeners},
		{"_SEQUENCERS", sequencers},
		{"_SUBORDINATOR", subordinator},
		{"_COMPLEMENTIZERS", complementizers},
		{"_SUBORDINATING", subordinating},
		{"_STOPWORDS", stopwords},
		{"_VERB_TOKENS", verbTokens},
		{"_EXEC_TOOLS", execTools},
		{"_READ_TOOLS", readTools},
	} {
		assertSameSet(t, c.name, frozenWords(t, src, c.name),
			sortedSetKeys(c.got))
	}
}

// TestTheAuxiliaryVetoMatchesUpstream pins the one word list that is
// spelled inline rather than as a table: the auxiliaries that cancel an
// imperative reading.
func TestTheAuxiliaryVetoMatchesUpstream(t *testing.T) {
	src := upstreamSource(t)
	const want = `("was", "were", "is", "are", "had", "has",
                                 "have")`
	if !strings.Contains(src, want) {
		t.Error("the auxiliary tuple in _opens_imperatively changed upstream")
	}
	// Derived rather than re-typed: every word above must cancel, and a
	// word that is not there must not.
	for _, w := range []string{"was", "were", "is", "are", "had", "has", "have"} {
		if opensImperatively([]string{"Verify", w}) {
			t.Errorf("%q no longer cancels the imperative reading", w)
		}
	}
	if !opensImperatively([]string{"Verify", "been"}) {
		t.Error("a word outside the tuple now cancels")
	}
}

func TestTheScalarBoundsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct {
		name string
		want int
		got  int
	}{
		{"_MAX_EVENTS", 4000, maxEvents},
		{"_EVENT_TEXT_CAP", 2000, eventTextCap},
		{"_MAX_RECEIPTS", 3, maxReceipts},
		{"_CLAIM_EXCERPT", 160, claimExcerpt},
	} {
		lit := "\n" + c.name + " = " + itoa(c.want) + "\n"
		if !strings.Contains(src, lit) {
			t.Errorf("upstream no longer says %s = %d", c.name, c.want)
		}
		if c.got != c.want {
			t.Errorf("the port's %s is %d, upstream %d", c.name, c.got, c.want)
		}
	}
	// The slices that are one character away from a different answer and
	// that no table names. The differential covers each; these say what
	// they were copied FROM.
	for _, lit := range []string{
		`sentence[:_CLAIM_EXCERPT]`,
		`str(ev.get("input", ""))[:_EVENT_TEXT_CAP]`,
		`str(ev.get("output", ""))[:_EVENT_TEXT_CAP]`,
		`scored[:_MAX_RECEIPTS]`,
		`_WORD.findall(body[:120])`,
		`_WORD.findall(body[i + 1:i + 61])`,
		`_MODAL_GOVERNS.search(before[-48:])`,
		`for b in bad[:2]`,
		`str(b.get("claim", ""))[:40]`,
		`if len(words) < 2:`,
		`for w in words[1:4]`,
		`if any(w.lower() in _COMPLEMENTIZERS for w in words[-2:])`,
	} {
		if !strings.Contains(src, lit) {
			t.Errorf("upstream no longer contains %q", lit)
		}
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	out := ""
	for n > 0 {
		out = string(rune('0'+n%10)) + out
		n /= 10
	}
	return out
}

// --- the patterns ----------------------------------------------------------

// TestTheClaimFamiliesMatchUpstream derives all five lexicon patterns,
// AND their order, from the `_CLAIM_FAMILIES` tuple.
//
// This is the test that would have caught `make test`, one level up: the
// battery cannot mutate an alternative that was never transcribed, and a
// scenario only finds it if someone thought to write it.
func TestTheClaimFamiliesMatchUpstream(t *testing.T) {
	names, pats := upstreamFamilies(t, upstreamSource(t))
	if len(names) != len(claimFamilies) {
		t.Fatalf("upstream has %d families, the port has %d: %v",
			len(names), len(claimFamilies), names)
	}
	for i, name := range names {
		if claimFamilies[i].name != name {
			t.Errorf("family %d: upstream %q, port %q", i, name,
				claimFamilies[i].name)
			continue
		}
		want := wordBounded(subClasses(scanFactored(t, pats[i]))).String()
		if got := claimFamilies[i].re.String(); want != got {
			t.Errorf("family %q diverges\n  want %q\n  got  %q",
				name, want, got)
		}
	}
}

// upstreamFamilies walks the `_CLAIM_FAMILIES = (…)` tuple, which is not
// a `NAME = re.compile(…)` and so needs its own reader.
func upstreamFamilies(t *testing.T, src string) (names, pats []string) {
	t.Helper()
	i := strings.Index(src, "\n_CLAIM_FAMILIES = (\n")
	if i < 0 {
		t.Fatal("upstream no longer defines _CLAIM_FAMILIES = (")
	}
	rest := src[i+1:]
	j := strings.Index(rest, "\n)\n")
	if j < 0 {
		t.Fatal("unterminated _CLAIM_FAMILIES tuple")
	}
	open := regexp.MustCompile(`^\s*\("(\w+)", re\.compile\(`)
	var cur []string
	var name string
	flush := func() {
		if name != "" {
			names = append(names, name)
			pats = append(pats, joinRawStrings(t, "_CLAIM_FAMILIES["+name+"]",
				strings.Join(cur, "\n")))
		}
		name, cur = "", nil
	}
	for _, line := range strings.Split(rest[:j], "\n") {
		if m := open.FindStringSubmatch(line); m != nil {
			flush()
			name = m[1]
		}
		if name != "" {
			cur = append(cur, line)
		}
	}
	flush()
	if len(names) == 0 {
		t.Fatal("_CLAIM_FAMILIES parsed to no families")
	}
	return names, pats
}

func TestTheRetrospectiveMarkersMatchUpstream(t *testing.T) {
	src := upstreamSource(t)

	// _RETRO_AUX: two alternatives, both fully bounded, both scanned.
	if want, got := wordBounded(subClasses(scanFactored(t,
		upstreamPattern(t, src, "_RETRO_AUX")))).String(),
		retroAuxRe.String(); want != got {
		t.Errorf("_RETRO_AUX diverges\n  want %q\n  got  %q", want, got)
	}

	// _RETRO_FINITE carries a THIRD `\b`, between `\w{3,}ed` and the
	// `\s+` that follows it. The port drops it, and the drop is derived
	// here rather than asserted: `\s+` requires a non-word character at
	// exactly the position the boundary was asking about, so removing it
	// cannot change a single answer. If upstream ever puts that `\b`
	// somewhere `\s` does not follow, this fails instead of quietly
	// approving the drop.
	up := upstreamPattern(t, src, "_RETRO_FINITE")
	inner := strings.TrimSuffix(strings.TrimPrefix(up, `\b`), `\b`)
	if strings.Count(inner, `\b`) != 1 || !strings.Contains(inner, `\b\s+`) {
		t.Fatalf("_RETRO_FINITE's interior boundary moved: %q", up)
	}
	want := wordBounded(subClasses(
		strings.Replace(inner, `\b\s+`, `\s+`, 1))).String()
	if got := retroFiniteRe.String(); want != got {
		t.Errorf("_RETRO_FINITE diverges\n  want %q\n  got  %q", want, got)
	}
}

func TestTheBooleanVetoesMatchUpstream(t *testing.T) {
	src := upstreamSource(t)

	// _HYPOTHETICAL: four fully bounded alternatives, boolean, so the
	// boundaries may consume and the four factor into one group.
	if want, got := pytext.PyFoldI(`(?i)`+boolFactored(t,
		subClasses(upstreamPattern(t, src, "_HYPOTHETICAL")))),
		hypotheticalRe.String(); want != got {
		t.Errorf("_HYPOTHETICAL diverges\n  want %q\n  got  %q", want, got)
	}

	// _RE_PREFIXED is the only pattern here with no re.IGNORECASE, so it
	// is the only one PyFoldI must NOT touch — a folded `re-` would
	// admit `RE-` and `Re-`, and "Re-verified" is a sentence opener, not
	// a past-tense verb.
	up := upstreamPattern(t, src, "_RE_PREFIXED")
	if !strings.HasSuffix(up, `$`) {
		t.Fatalf("_RE_PREFIXED no longer ends on $: %q", up)
	}
	if want, got := boolFactored(t, strings.TrimSuffix(up, `$`))+`\n?$`,
		rePrefixedRe.String(); want != got {
		t.Errorf("_RE_PREFIXED diverges\n  want %q\n  got  %q", want, got)
	}
	if strings.Contains(src, `_RE_PREFIXED = re.compile(r"\bre-$", re.I)`) {
		t.Error("_RE_PREFIXED gained re.IGNORECASE upstream; the port's " +
			"unfolded spelling is now wrong")
	}

	// _MODAL_GOVERNS and _NEGATOR_BEFORE both end on `$`, which Python
	// matches at the end OR before one trailing newline; Go's bare `$` is
	// the first only, so the port spells `\n?$`.
	for _, c := range []struct {
		name string
		got  *regexp.Regexp
		sub  func(string) string
	}{
		{"_MODAL_GOVERNS", modalGovernsRe, subClasses},
		{"_NEGATOR_BEFORE", negatorBeforeRe, negatorClasses(t)},
	} {
		up := upstreamPattern(t, src, c.name)
		if !strings.HasSuffix(up, `$`) {
			t.Fatalf("%s no longer ends on $: %q", c.name, up)
		}
		body := strings.TrimSuffix(up, `$`)
		want := pytext.PyFoldI(`(?i)` + boolFactored(t, c.sub(body)) + `\n?$`)
		if got := c.got.String(); want != got {
			t.Errorf("%s diverges\n  want %q\n  got  %q", c.name, want, got)
		}
	}
}

// negatorClasses is subClasses plus the one class the generic rule cannot
// handle: `[\w'\-/%.]` puts `\w` INSIDE a character class, and pytext's
// WordClass is a group, which a class cannot hold. It also drops the `\b`
// that follows the negator alternation, on the same argument the
// _RETRO_FINITE one rests on — every path out of the rest of the pattern
// needs whitespace or the end of the string there.
func negatorClasses(t *testing.T) func(string) string {
	return func(p string) string {
		const class = `[\w'\-/%.]`
		if strings.Count(p, class) != 1 {
			t.Fatalf("_NEGATOR_BEFORE's inner class moved: %q", p)
		}
		p = strings.Replace(p, class,
			`(?-i:[`+pytext.WordClassBody+`'\-/%.])`, 1)
		const dropped = `\b(?:\s+`
		if strings.Count(p, dropped) != 1 {
			t.Fatalf("_NEGATOR_BEFORE's interior boundary moved: %q", p)
		}
		return subClasses(strings.Replace(p, dropped, `(?:\s+`, 1))
	}
}

func TestTheEventMarkersMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct {
		name string
		got  *regexp.Regexp
		sub  func(string) string
	}{
		{"_NET_CMD", netCmdRe, subClasses},
		{"_TEST_CMD", testCmdRe, subClasses},
		{"_AUTH_MARK", authMarkRe, authClasses(t)},
	} {
		up := c.sub(upstreamPattern(t, src, c.name))
		want := pytext.PyFoldI(`(?i)` + boolFactored(t, up))
		if got := c.got.String(); want != got {
			t.Errorf("%s diverges\n  want %q\n  got  %q", c.name, want, got)
		}
	}
}

// authClasses is subClasses plus the credential class, which has to be
// fold-proofed BY HAND: Python's IGNORECASE folds `i` to the Turkish
// dotless and dotted forms and Go's does not, and PyFoldI cannot splice a
// group into a character class. Measured on this box:
// `re.fullmatch(r'[A-Za-z]', 'ı', re.I)` matches and Go's `(?i)[A-Za-z]`
// does not. (`K` and `ſ` fold in both.)
func authClasses(t *testing.T) func(string) string {
	return func(p string) string {
		const class = `[A-Za-z0-9_\-]`
		if strings.Count(p, class) != 2 {
			t.Fatalf("_AUTH_MARK's credential class moved: %q", p)
		}
		return subClasses(strings.ReplaceAll(p, class,
			`[A-Za-z0-9_\-`+pytext.IClassBody+`]`))
	}
}

func TestTheVerbatimPatternsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct {
		name string
		got  *regexp.Regexp
	}{
		{"_CLAUSE_BREAK", clauseBreakRe},
		{"_WORD", wordRe},
		{"_QUOTED_SPAN", quotedSpanRe},
	} {
		if want, got := upstreamPattern(t, src, c.name),
			c.got.String(); want != got {
			t.Errorf("%s diverges\n  want %q\n  got  %q", c.name, want, got)
		}
	}

	// _LEADING_TAG needs only the class widenings and `^` -> `\A`; Go's
	// `^` is the start of a LINE under (?m) and of the text without it,
	// and this port never sets (?m), so the two spellings agree today.
	// `\A` is written anyway, because the day someone adds a flag is not
	// the day to rediscover that.
	up := upstreamPattern(t, src, "_LEADING_TAG")
	if !strings.HasPrefix(up, `^`) {
		t.Fatalf("_LEADING_TAG no longer anchors: %q", up)
	}
	if want, got := pytext.PyFoldI(`(?i)\A`+subClasses(up[1:])),
		leadingTagRe.String(); want != got {
		t.Errorf("_LEADING_TAG diverges\n  want %q\n  got  %q", want, got)
	}

	// _IDENTIFIER_SHAPED is the one class that could not survive as a
	// class: `\d` widens to a GROUP, and a character class cannot hold
	// one, so the port spells the alternation instead.
	if want := `[./_\-\d]`; upstreamPattern(t, src,
		"_IDENTIFIER_SHAPED") != want {
		t.Errorf("_IDENTIFIER_SHAPED changed upstream")
	}
	if want, got := `[./_\-]|`+pytext.DigitClass,
		identifierShapedRe.String(); want != got {
		t.Errorf("_IDENTIFIER_SHAPED diverges\n  want %q\n  got  %q",
			want, got)
	}
}

// TestTheInlinePatternsMatchUpstream covers the two patterns that are
// written at their call site rather than as module constants — including
// the sentence split, which the port does not compile at all.
func TestTheInlinePatternsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)

	const split = `for sentence in re.split(r"(?<=[.;!?])\s+|\n+", text or ""):`
	if !strings.Contains(src, split) {
		t.Fatal("the sentence split changed upstream; splitSentences is a " +
			"hand-written copy of exactly one pattern and nothing else " +
			"checks it")
	}
	// Derived, not asserted: every character of the punctuation class
	// must open a split after whitespace, and a character outside it must
	// not.
	for _, r := range ".;!?" {
		if got := splitSentences("a" + string(r) + " b"); len(got) != 2 {
			t.Errorf("%q no longer ends a sentence: %q", r, got)
		}
	}
	for _, r := range ",:-" {
		if got := splitSentences("a" + string(r) + " b"); len(got) != 1 {
			t.Errorf("%q now ends a sentence: %q", r, got)
		}
	}

	const tie = `toks = re.findall(r"[a-z0-9_.\-/]{4,}", sentence.lower())`
	if !strings.Contains(src, tie) {
		t.Error("the tie-token pattern changed upstream")
	}
	if got := tieTokenRe.String(); got != `[a-z0-9_.\-/]{4,}` {
		t.Errorf("the port's tie-token pattern is %q", got)
	}
	const strip = `t.strip("./_-")`
	if !strings.Contains(src, strip) {
		t.Error("the specificity strip set changed upstream")
	}
}

// TestTheFamilyRulesMatchUpstream pins which families reach `supported`
// on family-level presence alone, which is the whole of the difference
// between a fetch claim and a probe claim.
func TestTheFamilyRulesMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	i := strings.Index(src, "\n_FAMILY_RULES = {\n")
	if i < 0 {
		t.Fatal("upstream no longer defines _FAMILY_RULES")
	}
	rest := src[i+1:]
	j := strings.Index(rest, "\n}\n")
	if j < 0 {
		t.Fatal("unterminated _FAMILY_RULES")
	}
	rows := regexp.MustCompile(`"(\w+)": \((_is_\w+), "(\w+)"\)`).
		FindAllStringSubmatch(rest[:j], -1)
	if len(rows) != len(familyRules) {
		t.Fatalf("upstream has %d rules, the port has %d", len(rows),
			len(familyRules))
	}
	for _, r := range rows {
		rule, ok := familyRules[r[1]]
		if !ok {
			t.Errorf("the port has no rule for family %q", r[1])
			continue
		}
		if rule.untied != r[3] {
			t.Errorf("family %q: upstream unties to %q, port to %q",
				r[1], r[3], rule.untied)
		}
		// The predicate is named, not compared: `_is_auth` and isAuth are
		// different functions in different languages. What is checkable
		// is that the port did not wire the wrong one, and the naming
		// convention is what makes that mechanical.
		want := "_is_" + strings.ToLower(strings.TrimPrefix(
			funcNameFor(rule.pred), "is"))
		if want != r[2] {
			t.Errorf("family %q: upstream calls %s, port wired %s",
				r[1], r[2], want)
		}
	}
}

// funcNameFor names the Go predicate a rule holds, so that "upstream
// calls _is_fetch" can be checked against "the port wired isFetch"
// rather than against nothing. The five predicates are not
// interchangeable -- an auth event is also a fetch, an exec and a probe,
// so a mis-wire is invisible on most inputs and shows up only as a
// family that grounds too easily.
func funcNameFor(pred func(Event) bool) string {
	full := runtime.FuncForPC(reflect.ValueOf(pred).Pointer()).Name()
	return full[strings.LastIndex(full, ".")+1:]
}
