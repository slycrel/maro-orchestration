package claimverify

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// The tables and the four patterns in this module are CONTENT keys, and a
// differential can only sample them — it exercises the prefixes,
// extensions and skip names a fixture happens to spell and cannot tell a
// complete copy from a plausible one. These tests read the literals back
// out of the Python instead, so an upstream edit fails HERE, naming the
// thing that moved.
//
// Where the port's spelling is a mechanical transformation of upstream's,
// that is what gets asserted — the transformation applied to the original
// must reproduce the port character for character. Pinning a hand-typed
// copy would pass just as happily against a table that had drifted.

func upstreamSource(t *testing.T) string {
	t.Helper()
	p := filepath.Join(pyprobe.SrcDir(t, "claim_verifier.py"),
		"claim_verifier.py")
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// frozenLiteral pulls the quoted strings out of `name = frozenset({…})`.
func frozenLiteral(t *testing.T, src, name string) []string {
	t.Helper()
	i := strings.Index(src, "\n"+name+" = frozenset({")
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = frozenset({…})", name)
	}
	rest := src[i+len("\n"+name+" = frozenset({"):]
	j := strings.Index(rest, "})")
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	out := []string{}
	for _, m := range regexp.MustCompile(`"([^"]*)"`).
		FindAllStringSubmatch(rest[:j], -1) {
		out = append(out, m[1])
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

func sortedCopy(ss []string) []string {
	out := append([]string(nil), ss...)
	sort.Strings(out)
	return out
}

func assertSameSet(t *testing.T, what string, want, got []string) {
	t.Helper()
	if strings.Join(want, "\x00") == strings.Join(got, "\x00") {
		return
	}
	t.Errorf("%s: upstream %q, port %q", what, want, got)
}

func TestTheSkipTablesMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	assertSameSet(t, "_SKIP_NAMES", frozenLiteral(t, src, "_SKIP_NAMES"),
		sortedSetKeys(skipNames))
	assertSameSet(t, "_SKIP_PATTERNS", frozenLiteral(t, src, "_SKIP_PATTERNS"),
		sortedSetKeys(skipPatterns))
	assertSameSet(t, "_SYMBOL_SKIP", frozenLiteral(t, src, "_SYMBOL_SKIP"),
		sortedSetKeys(symbolSkip))
	assertSameSet(t, "_INDEX_SKIP_DIRS",
		frozenLiteral(t, src, "_INDEX_SKIP_DIRS"),
		sortedSetKeys(indexSkipDirs))
	// The synthesis keywords are a frozenset and `any()` over it, so the
	// port's slice order carries nothing and the comparison sorts.
	assertSameSet(t, "_SYNTHESIS_KEYWORDS",
		frozenLiteral(t, src, "_SYNTHESIS_KEYWORDS"),
		sortedCopy(synthesisKeywords))
}

// TestTheScalarBoundsMatchUpstream reads each number back out of the
// Python. The differential drives _INDEX_MAX_DIRS through a test hook
// precisely because 2000 real directories cannot be built at a path
// length the kernel accepts — so the VALUE has to be pinned here.
func TestTheScalarBoundsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct{ name, want string }{
		{"_SYMBOL_MIN_LEN", "5"},
		{"_INDEX_MAX_DIRS", "2000"},
	} {
		if !strings.Contains(src, "\n"+c.name+" = "+c.want+"\n") {
			t.Errorf("upstream no longer says %s = %s", c.name, c.want)
		}
	}
	if symbolMinLen != 5 || indexMaxDirs != 2000 {
		t.Errorf("a port bound no longer matches upstream: min=%d max=%d",
			symbolMinLen, indexMaxDirs)
	}
	// The two truncations, which the differential does cover but which
	// are one character away from a different answer.
	for _, lit := range []string{"self.not_found[:3]", "file_report.verified[:5]"} {
		if !strings.Contains(src, lit) {
			t.Errorf("upstream no longer contains %q", lit)
		}
	}
}

// verboseBody reassembles a `re.VERBOSE` triple-quoted literal into the
// compact pattern it compiles to: comments dropped, whitespace removed.
// Neither is subtle here — no character class in this module contains a
// space or a `#`.
func verboseBody(t *testing.T, src, name string) string {
	t.Helper()
	head := "\n" + name + " = re.compile(\n    r\"\"\""
	i := strings.Index(src, head)
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = re.compile(r\"\"\"…", name)
	}
	rest := src[i+len(head):]
	j := strings.Index(rest, "\"\"\"")
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	var b strings.Builder
	for _, line := range strings.Split(rest[:j], "\n") {
		if k := strings.Index(line, "#"); k >= 0 {
			line = line[:k]
		}
		for _, r := range line {
			if r == ' ' || r == '\t' {
				continue
			}
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		t.Fatalf("%s reassembled to nothing", name)
	}
	return b.String()
}

// expandOptionalS turns `tests?` into the two alternatives `re` actually
// tries, in the order it tries them: greedy first.
func expandOptionalS(alts []string) []string {
	out := []string{}
	for _, a := range alts {
		a = strings.ReplaceAll(a, `\.`, ".")
		if strings.HasSuffix(a, "s?") {
			stem := strings.TrimSuffix(a, "s?")
			out = append(out, stem+"s", stem)
			continue
		}
		out = append(out, a)
	}
	return out
}

// TestTheFilePathPatternMatchesUpstream derives the port's two tables
// from the upstream literal and pins the boundary classes.
//
// The port has no regex here at all — RE2 cannot spell the lookbehind —
// so the pattern is reassembled, taken apart, and each piece checked
// against the hand-written scanner that replaced it.
func TestTheFilePathPatternMatchesUpstream(t *testing.T) {
	body := verboseBody(t, upstreamSource(t), "_FILE_PATH_RE")
	const want = "(?<![\\w`'\"(])" +
		`(?:(?:src|tests?|docs?|lat\.md|scripts?|personas?|memory|deploy)/` +
		`[\w/.-]+\.(?:py|md|json|yaml|yml|sh|txt|toml|cfg|ini)` +
		`|[\w.-]+\.py|[\w.-]+\.md)` +
		"(?![\\w`'\")])"
	if body != want {
		t.Fatalf("_FILE_PATH_RE changed upstream:\n  pinned %q\n  actual %q",
			want, body)
	}

	// The directory prefixes, in `re`'s try order.
	i := strings.Index(body, "(?:(?:") + len("(?:(?:")
	prefixes := expandOptionalS(strings.Split(body[i:strings.Index(body, ")/")], "|"))
	if strings.Join(prefixes, ",") != strings.Join(filePathDirPrefixes, ",") {
		t.Errorf("prefix order: upstream %v, port %v",
			prefixes, filePathDirPrefixes)
	}
	// The order is only load-bearing if the scanner honours it, so say so
	// with the case that proves it: `tests` matches the first four
	// characters of `test/` and then fails on the slash, and `re`
	// backtracks into the singular.
	if got := findFilePaths("see test/x.py"); len(got) != 1 ||
		got[0] != "test/x.py" {
		t.Errorf("prefix backtracking broke: %v", got)
	}

	// The extensions, likewise in order.
	k := strings.Index(body, `\.(?:`) + len(`\.(?:`)
	exts := strings.Split(body[k:strings.Index(body, `)|[\w.-]+`)], "|")
	if strings.Join(exts, ",") != strings.Join(filePathExts, ",") {
		t.Errorf("extension order: upstream %v, port %v", exts, filePathExts)
	}

	// The two boundary classes, which became predicates. They differ in
	// the paren ONLY, and that is the whole of the difference.
	left := body[len("(?<!["):strings.Index(body, "])")]
	right := body[strings.Index(body, "(?![")+len("(?![") : strings.LastIndex(body, "])")]
	assertClass(t, "left boundary", left, blocksLeft)
	assertClass(t, "right boundary", right, func(c rune) bool {
		return !rightBoundaryOK([]rune{c}, 0)
	})
}

// assertClass checks a predicate against a `[\w…]` class literal: every
// listed character blocks, and a handful of neighbours do not.
func assertClass(t *testing.T, what, class string, blocks func(rune) bool) {
	t.Helper()
	rest := strings.TrimPrefix(class, `\w`)
	if rest == class {
		t.Fatalf("%s: %q no longer opens on \\w", what, class)
	}
	for _, c := range rest {
		if !blocks(c) {
			t.Errorf("%s: %q is in the class but does not block", what, c)
		}
	}
	for _, c := range "a9_" {
		if !blocks(c) {
			t.Errorf("%s: \\w character %q does not block", what, c)
		}
	}
	// Every character the class does NOT name must pass, and the pair
	// that matters is the parens: one blocks on each side.
	for _, c := range " /.-[]{}<>!?*+=:;,\n" {
		if blocks(c) {
			t.Errorf("%s: %q is not in the class but blocks", what, c)
		}
	}
	for _, c := range "()" {
		if strings.ContainsRune(rest, c) == !blocks(c) {
			t.Errorf("%s: paren %q disagrees with the class", what, c)
		}
	}
}

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
	if j < 0 {
		j = strings.Index(rest, "\n)")
	}
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	var b strings.Builder
	for _, m := range regexp.MustCompile(`r"((?:[^"\\]|\\.)*)"`).
		FindAllStringSubmatch(rest[:j+1], -1) {
		b.WriteString(m[1])
	}
	if b.Len() == 0 {
		t.Fatalf("%s literal reassembled to nothing", name)
	}
	return b.String()
}

// subClasses applies the one substitution Go forces on every pattern
// here: `\w` and `\s` are Unicode in Python and ASCII in Go.
func subClasses(p string) string {
	p = strings.ReplaceAll(p, `\w`, pytext.WordClass)
	return strings.ReplaceAll(p, `\s`, pytext.SpaceClass)
}

func TestTheSymbolPatternsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)

	// The backtick pattern has no boundary at all, so the substitution is
	// the only difference.
	if want, got := subClasses(upstreamPattern(t, src, "_SYMBOL_BACKTICK_RE")),
		symbolBacktickRe.String(); want != got {
		t.Errorf("_SYMBOL_BACKTICK_RE diverges\n  want %q\n  got  %q", want, got)
	}

	// The def pattern's LEADING `\b` is lifted out and answered by
	// leadingWordBoundary, so the port anchors instead.
	up := upstreamPattern(t, src, "_SYMBOL_DEF_RE")
	if !strings.HasPrefix(up, `\b`) {
		t.Fatalf("_SYMBOL_DEF_RE no longer opens on \\b: %q", up)
	}
	if want, got := `\A`+subClasses(strings.TrimPrefix(up, `\b`)),
		symbolDefRe.String(); want != got {
		t.Errorf("_SYMBOL_DEF_RE diverges\n  want %q\n  got  %q", want, got)
	}

	// The context pattern is the restructured one: both leading `\b`s are
	// lifted, the TRAILING one becomes a consuming `(?:\W|$)`, and each
	// alternative gains a group so the scan knows where it really ended.
	up = upstreamPattern(t, src, "_SYMBOL_CONTEXT_RE")
	sep := strings.Index(up, `\b|\b`)
	if !strings.HasPrefix(up, `\b`) || sep < 0 {
		t.Fatalf("_SYMBOL_CONTEXT_RE no longer has the two-alternative "+
			"shape this port takes apart: %q", up)
	}
	alt1 := strings.TrimPrefix(up[:sep], `\b`)
	alt2 := up[sep+len(`\b|\b`):]
	want := pytext.PyFoldI(`(?i)\A(?:(` + subClasses(alt1) + `)(?:` +
		pytext.NotWordClass + `|$)|(` + subClasses(alt2) + `))`)
	if got := symbolContextRe.String(); want != got {
		t.Errorf("_SYMBOL_CONTEXT_RE diverges\n  want %q\n  got  %q", want, got)
	}
	// `re.IGNORECASE` is the second substitution, and PyFoldI is what
	// makes it mean the same thing. The case that proves it is not
	// upper/lower at all.
	if got := ExtractSymbolClaims("the alpha_one functİon"); len(got) != 1 ||
		got[0] != "alpha_one" {
		t.Errorf("the dotted capital I no longer folds: %v", got)
	}
}

// TestTheSearchDirsMatchUpstream pins the one ORDERED list the module
// keeps that is not a pattern.
func TestTheSearchDirsMatchUpstream(t *testing.T) {
	if !strings.Contains(upstreamSource(t),
		`search_dirs = [project_root / "src", project_root / "tests", project_root]`) {
		t.Error("the symbol index's search dirs changed upstream")
	}
}
