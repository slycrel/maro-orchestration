// Package claimverify ports `src/claim_verifier.py` — the zero-LLM
// file-existence and symbol-existence check.
//
// It exists because of one measured failure mode: a synthesis step reads
// accumulated findings from many prior steps, has to cite specific files,
// and confabulates paths that are not there. The answer is deliberately
// not another model call — it is `stat`, a bounded tree walk, and a
// regex, so the check cannot itself hallucinate.
//
// The port's whole difficulty is in the extraction. Three of this
// module's patterns are things Go's RE2 will not express: one has a
// LOOKBEHIND, one is `re.VERBOSE` with a nine-way extension alternation
// behind a greedy dotted run, and one is `re.IGNORECASE` over literals
// containing `i` — which in CPython also matches U+0130 and U+0131.
//
// The verification half has its own trap. Every fallback here was added
// to stop a real file being called a hallucination, and each one widens
// what counts as "exists": a bare name verifies if any file of that name
// exists anywhere under the project, and a relative claim verifies if it
// is a suffix of an indexed relative path. Both are bounded on purpose —
// the suffix match must be UNIQUE, and an ambiguous one lands in
// `unresolvable`, which is neither verified nor a hallucination. A port
// that quietly turned "ambiguous" into "verified" would delete the signal
// this module exists to produce.
package claimverify

import (
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

// ClaimReport is the result of verifying file-path claims in a text block.
//
// SuffixMatched records the claims verified by the unique-suffix fallback
// rather than a direct hit, claim → the indexed relpath it matched. It is
// kept VISIBLE so a suffix-verify never silently absorbs the
// hallucination signal.
type ClaimReport struct {
	RawClaims    []string
	Verified     []string
	NotFound     []string
	Unresolvable []string
	// SuffixMatched is a Python dict, so insertion order is part of it;
	// the keys are appended in claim order.
	SuffixMatched []SuffixMatch
}

// SuffixMatch is one entry of ClaimReport.SuffixMatched.
type SuffixMatch struct {
	Claim   string
	RelPath string
}

func (r ClaimReport) HasHallucinations() bool { return len(r.NotFound) > 0 }

// HallucinationRate is `not_found / (verified + not_found)`, and the
// denominator deliberately excludes `unresolvable`: a claim nobody could
// check is not evidence either way, so counting it would dilute the rate
// toward zero exactly when the tree is ambiguous.
func (r ClaimReport) HallucinationRate() float64 {
	checkable := len(r.Verified) + len(r.NotFound)
	if checkable == 0 {
		return 0.0
	}
	return float64(len(r.NotFound)) / float64(checkable)
}

func (r ClaimReport) Summary() string {
	parts := []string{}
	if len(r.Verified) > 0 {
		parts = append(parts, strconv.Itoa(len(r.Verified))+" verified")
	}
	if len(r.NotFound) > 0 {
		parts = append(parts, strconv.Itoa(len(r.NotFound))+" NOT FOUND: "+
			strings.Join(head(r.NotFound, 3), ", "))
	}
	if len(r.Unresolvable) > 0 {
		parts = append(parts,
			strconv.Itoa(len(r.Unresolvable))+" unresolvable")
	}
	if len(parts) == 0 {
		return "no file claims found"
	}
	return strings.Join(parts, "; ")
}

// SymbolReport is the result of verifying Python symbol claims.
type SymbolReport struct {
	RawClaims []string
	Verified  []string
	NotFound  []string
}

func (r SymbolReport) HasHallucinations() bool { return len(r.NotFound) > 0 }

func (r SymbolReport) Summary() string {
	parts := []string{}
	if len(r.Verified) > 0 {
		parts = append(parts,
			strconv.Itoa(len(r.Verified))+" symbol(s) verified")
	}
	if len(r.NotFound) > 0 {
		parts = append(parts, strconv.Itoa(len(r.NotFound))+
			" symbol(s) NOT FOUND: "+strings.Join(head(r.NotFound, 3), ", "))
	}
	if len(parts) == 0 {
		return "no symbol claims found"
	}
	return strings.Join(parts, "; ")
}

// head is a Python list slice: it clamps instead of raising.
func head(ss []string, n int) []string {
	if len(ss) < n {
		return ss
	}
	return ss[:n]
}

// ---------------------------------------------------------------------------
// Extraction: the file-path pattern
// ---------------------------------------------------------------------------

// filePathDirPrefixes are `_FILE_PATH_RE`'s known-directory alternation,
// EXPANDED and in the order `re` would try them.
//
// The Python spells four of these with `s?`, and a greedy `?` tries the
// `s` present first — so `tests` is attempted before `test`, and the
// alternation backtracks into the shorter one when the `/` after it does
// not appear. Writing the expansion out is what makes the hand-scan below
// able to reproduce that without a backtracker; the guard test derives
// this list from the upstream literal so the expansion cannot drift.
var filePathDirPrefixes = []string{
	"src", "tests", "test", "docs", "doc", "lat.md",
	"scripts", "script", "personas", "persona", "memory", "deploy",
}

// filePathExts is the extension alternation of branch one, in Python's
// order. Branch two and three are `.py` and `.md` alone.
var filePathExts = []string{
	"py", "md", "json", "yaml", "yml", "sh", "txt", "toml", "cfg", "ini",
}

// skipNames are modules that are stdlib or common packages.
var skipNames = map[string]bool{
	"os.py": true, "sys.py": true, "json.py": true, "re.py": true,
	"io.py": true, "time.py": true, "uuid.py": true, "pathlib.py": true,
	"datetime.py": true, "logging.py": true, "typing.py": true,
	"abc.py": true, "dataclasses.py": true, "functools.py": true,
	"itertools.py": true, "collections.py": true, "threading.py": true,
	"subprocess.py": true, "shutil.py": true, "hashlib.py": true,
	"README.md": true, "CHANGELOG.md": true, "LICENSE.md": true,
}

// skipPatterns are strings that look like file paths but are not.
var skipPatterns = map[string]bool{"setup.py": true, "manage.py": true}

// ExtractFileClaims extracts file-path claims from text, deduplicated in
// first-seen order.
func ExtractFileClaims(text string) []string {
	raw := findFilePaths(text)
	seen := map[string]bool{}
	result := []string{}
	for _, p := range raw {
		// `str.strip(" .,;:\"'`")` is a CHARACTER SET, not a prefix: it
		// eats any run of those from both ends, which is how a claim
		// picked up out of prose loses its sentence punctuation.
		// EQUIVALENT MUTANT (kept, marked `equivalent`): eight
		// characters are named and only ONE of them can ever be inside a
		// match — the two classes admit `.`, and neither admits a space,
		// comma, semicolon, colon, quote or backtick. A match cannot END
		// on a dot either (it ends on an extension), so only a LEADING
		// dot is ever stripped, and Trim, TrimLeft and a set without the
		// semicolon all agree on every claim the pattern can produce.
		p = strings.Trim(p, " .,;:\"'`")
		name := pypath.Name(p)
		if skipNames[name] || skipPatterns[p] {
			continue
		}
		if !seen[p] {
			seen[p] = true
			result = append(result, p)
		}
	}
	return result
}

// findFilePaths is `_FILE_PATH_RE.findall`, hand-written.
//
//	(?<![\w`'"(])
//	(?:
//	    (?:src|tests?|docs?|lat\.md|scripts?|personas?|memory|deploy)/
//	    [\w/.-]+\.(?:py|md|json|yaml|yml|sh|txt|toml|cfg|ini)
//	|   [\w.-]+\.py
//	|   [\w.-]+\.md
//	)
//	(?![\w`'")])
//
// The lookbehind is why this cannot be a Go pattern: RE2 has none, and
// pytext's consuming boundaries are no help because findall returns
// matched TEXT and a consumed character would be inside it.
//
// The scan is the same shape as pathrewrite's and receipts': try each
// alternative in order at each position, advance one character on
// failure, and jump past a match. What is new here is that each
// alternative ends in a GREEDY run followed by a literal dot and an
// extension, which is a genuine backtracking construct — so the tail
// scan below walks the candidate dot positions from the RIGHT, which is
// the order a backtracking `+` releases characters in.
func findFilePaths(text string) []string {
	r := []rune(text)
	out := []string{}
	for i := 0; i < len(r); {
		if !leftBoundaryOK(r, i) {
			i++
			continue
		}
		if n := matchPrefixedPath(r, i); n > 0 {
			out = append(out, string(r[i:i+n]))
			i += n
			continue
		}
		if n := matchBareName(r, i, "py"); n > 0 {
			out = append(out, string(r[i:i+n]))
			i += n
			continue
		}
		if n := matchBareName(r, i, "md"); n > 0 {
			out = append(out, string(r[i:i+n]))
			i += n
			continue
		}
		i++
	}
	return out
}

// leftBoundaryOK is `(?<![\w`'"(])`.
func leftBoundaryOK(r []rune, i int) bool {
	if i == 0 {
		return true
	}
	return !blocksLeft(r[i-1])
}

func blocksLeft(c rune) bool {
	return pytext.IsWordChar(c) || c == '`' || c == '\'' || c == '"' ||
		c == '('
}

// rightBoundaryOK is `(?![\w`'")])`. It differs from the left one in the
// paren ONLY: an open paren blocks on the left and a close paren on the
// right, which is the pattern refusing a path that is being quoted or
// called rather than named.
func rightBoundaryOK(r []rune, i int) bool {
	if i >= len(r) {
		return true
	}
	c := r[i]
	return !(pytext.IsWordChar(c) || c == '`' || c == '\'' || c == '"' ||
		c == ')')
}

// matchPrefixedPath is alternative one: the length of the match at i, or 0.
func matchPrefixedPath(r []rune, i int) int {
	for _, p := range filePathDirPrefixes {
		pr := []rune(p)
		j := i + len(pr)
		if j >= len(r) || string(r[i:j]) != p || r[j] != '/' {
			continue
		}
		// The alternation BACKTRACKS: if the tail fails for `tests`, `re`
		// retries with `test` and then with the next alternative
		// entirely. So a failed tail continues this loop rather than
		// returning.
		if n := matchDottedTail(r, j+1, filePathExts); n > 0 {
			return j + 1 + n - i
		}
	}
	return 0
}

// matchBareName is alternative two and three: `[\w.-]+\.py` / `\.md`.
func matchBareName(r []rune, i int, ext string) int {
	n := matchDottedTailClass(r, i, []string{ext}, isBareRune)
	if n == 0 {
		return 0
	}
	return n
}

// matchDottedTail is `[\w/.-]+\.(?:ext|…)` with the trailing lookahead.
func matchDottedTail(r []rune, j int, exts []string) int {
	return matchDottedTailClass(r, j, exts, isPathRune)
}

// matchDottedTailClass is the shared tail: a greedy run of `class`, a
// literal dot, one of `exts`, and the right lookahead.
//
// The run is maximal, and every extension character is itself in the
// class, so the whole match is inside the run — which is what makes the
// backtracking finite. `re` releases characters from the right, so the
// candidate dot positions are walked from the right too, and the FIRST
// one that carries a valid extension and clears the lookahead wins. That
// is why `src/a.py.md` matches whole rather than stopping at `src/a.py`.
func matchDottedTailClass(r []rune, j int, exts []string,
	class func(rune) bool) int {
	end := j
	for end < len(r) && class(r[end]) {
		end++
	}
	// `+` needs at least one character before the dot, so the dot can sit
	// no earlier than j+1 and no later than end-1.
	for d := end - 1; d >= j+1; d-- {
		if r[d] != '.' {
			continue
		}
		for _, ext := range exts {
			stop := d + 1 + len([]rune(ext))
			// EQUIVALENT MUTANT (kept, marked `equivalent`): bounding on
			// `end` rather than on len(r) cannot change an answer,
			// because every extension character is a LETTER and every
			// letter is in the class — so a match that ran past the
			// maximal run would need r[end] to be a letter, which is
			// exactly what stopped the run. The bound is written on the
			// run because the run is what the Python's `+` matched.
			if stop > end || string(r[d+1:stop]) != ext {
				continue
			}
			if rightBoundaryOK(r, stop) {
				return stop - j
			}
		}
	}
	return 0
}

// isPathRune is `[\w/.-]`; isBareRune is `[\w.-]`.
func isPathRune(c rune) bool { return isBareRune(c) || c == '/' }

func isBareRune(c rune) bool {
	return pytext.IsWordChar(c) || c == '.' || c == '-'
}

// ---------------------------------------------------------------------------
// Extraction: the symbol patterns
// ---------------------------------------------------------------------------

// The three symbol patterns have no lookaround, so they stay REGEXES. Go
// resolves alternation and bounded repeats with the same leftmost-first
// priority a backtracking engine would, so the captured groups agree.
//
// Two substitutions are still forced. `\w` and `\s` are Unicode in
// Python, so they come from pytext. And `_SYMBOL_CONTEXT_RE` is
// IGNORECASE over literals containing `i` — `function`, `attribute` —
// which CPython also matches against U+0130 and U+0131; PyFoldI is what
// makes Go's `(?i)` mean the same thing.
// A word run for `\w+` and a space run for `\s`.
const (
	wordRun  = pytext.WordClass + "+"
	spaceRun = pytext.SpaceClass
)

var (
	// No boundary at all, so this one stays an ordinary FindAll.
	symbolBacktickRe = regexp.MustCompile("`([A-Za-z_]" + wordRun +
		")" + spaceRun + `*(?:\([^)]{0,40}\))?` + "`")

	// `\b(?:def|class)\s+([A-Za-z_]\w+)`, with the LEADING boundary
	// lifted out of the pattern and answered by leadingWordBoundary. See
	// scanBoundaried for why.
	symbolDefRe = regexp.MustCompile(`\A(?:def|class)` + spaceRun +
		"+([A-Za-z_]" + wordRun + ")")

	// `_SYMBOL_CONTEXT_RE`, restructured.
	//
	// The leading `\b` of each alternative is lifted out; the TRAILING
	// `\b` of alternative one cannot be, so it is spelled as a consuming
	// `(?:\W|$)` and the alternative's real extent is captured in group 1
	// — see scanBoundaried. Group 2 is the symbol from alternative one,
	// group 3 the whole of alternative two, group 4 its symbol.
	symbolContextRe = regexp.MustCompile(pytext.PyFoldI(`(?i)\A(?:` +
		"(([A-Za-z_]" + wordRun + ")" + spaceRun +
		`*(?:\(\)|\([^)]{0,40}\))?` + spaceRun +
		"+(?:function|method|class|property|attribute))" +
		"(?:" + pytext.NotWordClass + "|$)" +
		"|((?:function|method|class|property)" + spaceRun +
		"+`?([A-Za-z_]" + wordRun + ")`?)" +
		")"))
)

// scanBoundaried is `finditer` for a pattern whose `\b`s Go cannot spell.
//
// RE2 has no lookaround, and pytext's WordStart/WordEnd CONSUME the
// boundary character. Consumption is harmless when the caller wants a
// boolean; it is NOT harmless here, for two reasons that pull in opposite
// directions. A consumed LEADING boundary shifts the match start, which
// costs nothing on its own — but a consumed TRAILING one shortens the
// remaining text, so a second match that began at the very next character
// is silently lost. `alpha_one function method beta_two` is the live
// shape: alternative one ends on the space, and alternative two needed
// that same space for its own boundary.
//
// So the two boundaries get different treatments. The leading one is
// lifted out of the pattern entirely and answered here, by scanning
// candidate start positions and trying an `\A`-anchored match at each —
// which is what a leftmost search does anyway. The trailing one stays in
// the pattern, consuming, and the alternative's REAL extent is captured
// in a group so the scan can resume from the right place.
//
// `extent` names the group whose end is the true match end, or 0 for the
// whole match. It is consulted per alternative, because only alternative
// one has a trailing boundary to undo.
func scanBoundaried(re *regexp.Regexp, text string,
	extent func(loc []int) int) [][]string {
	out := [][]string{}
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
		groups := make([]string, 0, len(loc)/2)
		for g := 0; g*2 < len(loc); g++ {
			if loc[g*2] < 0 {
				groups = append(groups, "")
				continue
			}
			groups = append(groups, text[i+loc[g*2]:i+loc[g*2+1]])
		}
		out = append(out, groups)
		end := extent(loc)
		if end <= 0 {
			// A zero-width match would loop; none of these patterns can
			// produce one (every alternative requires at least two
			// characters), and this is the guard that says so.
			end = runeLen(text, i)
		}
		i += end
	}
	return out
}

// leadingWordBoundary is `\b` at a position whose NEXT character is a
// word character — which is true of every alternative here, because each
// begins with `[A-Za-z_]` or a literal word. Reduced to its only
// remaining question: is the character BEFORE it a word character?
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

// symbolSkip are keywords, builtins and names that are not real claims.
var symbolSkip = map[string]bool{
	"True": true, "False": true, "None": true, "self": true, "cls": true,
	"args": true, "kwargs": true, "int": true, "str": true, "bool": true,
	"list": true, "dict": true, "set": true, "tuple": true, "float": true,
	"Optional": true, "List": true, "Dict": true, "Set": true,
	"Tuple": true, "Any": true, "Union": true, "Path": true,
	"Exception": true, "ValueError": true, "TypeError": true,
	"KeyError": true, "RuntimeError": true, "StopIteration": true,
	"yield": true, "return": true, "raise": true, "import": true,
	"from": true, "as": true, "if": true, "else": true, "elif": true,
	"for": true, "while": true, "with": true, "try": true,
	"except": true, "finally": true, "pass": true, "break": true,
	"continue": true, "lambda": true, "and": true, "or": true,
	"not": true, "in": true, "is": true, "class": true, "def": true,
	"print": true, "len": true, "range": true, "open": true,
	"type": true, "super": true,
}

// symbolMinLen avoids noise on names like "id" and "db".
const symbolMinLen = 5

// ExtractSymbolClaims returns the Python symbol names a text claims exist.
//
// The result is SORTED, because Python accumulates into a set and then
// sorts it — which is also why the three patterns' relative order does
// not matter here, only which names they find.
func ExtractSymbolClaims(text string) []string {
	candidates := map[string]bool{}
	for _, m := range symbolBacktickRe.FindAllStringSubmatch(text, -1) {
		candidates[m[1]] = true
	}
	for _, m := range scanBoundaried(symbolDefRe, text,
		func(loc []int) int { return loc[1] }) {
		candidates[m[1]] = true
	}
	for _, m := range scanBoundaried(symbolContextRe, text, func(loc []int) int {
		// Alternative one consumed its trailing boundary; group 1 is its
		// real extent. Alternative two has no trailing boundary, so the
		// whole match is the extent.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): resuming from
		// the END of alternative one instead — boundary included — finds
		// the same symbols. The consumed character is by definition a
		// NON-word character, and every alternative begins with a word
		// character, so no match can start where the boundary sat. The
		// group is kept because it is what `m.end()` means in Python,
		// and because a pattern whose trailing context were WIDER than
		// one character would need it.
		if loc[2] >= 0 {
			return loc[3]
		}
		return loc[1]
	}) {
		// `m.group(1) or m.group(2)` — an unmatched group is None in
		// Python and "" here, and the two behave the same under `or`.
		sym := m[2]
		if sym == "" {
			sym = m[4]
		}
		if sym != "" {
			candidates[sym] = true
		}
	}
	names := make([]string, 0, len(candidates))
	for s := range candidates {
		names = append(names, s)
	}
	// `sorted(...)` over str compares CODE POINTS, which is what Go's
	// string compare does for valid UTF-8; a symbol name is `[A-Za-z_]\w+`
	// and cannot be anything else.
	sort.Strings(names)
	out := []string{}
	for _, s := range names {
		if symbolSkip[s] || len([]rune(s)) < symbolMinLen ||
			strings.HasPrefix(s, "__") {
			continue
		}
		out = append(out, s)
	}
	return out
}

// ---------------------------------------------------------------------------
// Project root and the tree index
// ---------------------------------------------------------------------------

// Deps is the seam `_infer_project_root` reaches through.
//
// Python imports `llm.get_default_subprocess_cwd` INSIDE the try, so both
// the import failing and the call raising land in the same handler. A nil
// DefaultSubprocessCwd stands for the import that did not resolve.
type Deps struct {
	DefaultSubprocessCwd func() (string, error)
}

// InferProjectRoot resolves the base directory claims are checked against.
//
// The run-scoped subprocess cwd comes FIRST and that ordering is the
// whole point: without it, inference walks up from Maro's own launch cwd
// — the repo, which has a `src/` — and a file a worker cited relative to
// its project dir is declared not-found while sitting on disk.
func InferProjectRoot(d Deps) string {
	if d.DefaultSubprocessCwd != nil {
		if runCwd, err := d.DefaultSubprocessCwd(); err == nil &&
			runCwd != "" {
			if st, serr := os.Stat(runCwd); serr == nil && st.IsDir() {
				return runCwd
			}
		}
	}
	cwd, err := os.Getwd()
	if err != nil {
		// `Path.cwd()` raises if the directory is gone, and nothing here
		// catches it — the caller's `except Exception` is around the
		// per-claim work, not around this.
		return ""
	}
	// `[cwd, *cwd.parents]` — the directory itself, then every ancestor
	// up to and including the root.
	for _, p := range append([]string{cwd}, parentsOf(cwd)...) {
		if exists(pypath.Join(p, "pyproject.toml")) ||
			exists(pypath.Join(p, "src")) {
			return p
		}
	}
	return cwd
}

// parentsOf is `PurePath.parents`, which stops at the anchor and does not
// include the path itself.
func parentsOf(p string) []string {
	out := []string{}
	for {
		parent := path.Dir(p)
		if parent == p {
			return out
		}
		out = append(out, parent)
		p = parent
	}
}

// exists is `Path.exists()`, which FOLLOWS symlinks — a dangling link
// does not exist.
func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// indexSkipDirs are never descended into when indexing basenames.
var indexSkipDirs = map[string]bool{
	".git": true, "__pycache__": true, "node_modules": true,
	".venv": true, ".tox": true,
}

// indexMaxDirs bounds the walk. It is a var, not a const, only so the
// differential can drive the cap with a fixture that is a handful of
// directories deep rather than two thousand — a 2000-link chain is the
// only shape whose walk ORDER is forced, and its path is longer than the
// kernel will accept. The VALUE is pinned against upstream in
// upstream_test.go; nothing outside a test writes this.
var indexMaxDirs = 2000

// treeIndex is one bounded walk of the project tree → (file basenames,
// relative posix paths).
//
// It backs both fallbacks in VerifyFileClaims, and it is built LAZILY on
// the first miss, so a text whose every claim resolves directly never
// walks the tree at all.
//
// The walk order matters, which is not obvious given both results are
// SETS: the cap is on DIRECTORIES VISITED, so which 2000 directories are
// seen decides what is in the sets. `os.walk` yields in `scandir` order,
// so this reads names raw rather than through `os.ReadDir`, which sorts.
func treeIndex(root string) (map[string]bool, map[string]bool) {
	names := map[string]bool{}
	relpaths := map[string]bool{}
	visited := 0
	// `os.walk` is top-down and iterative over a queue it can be pruned
	// through; the recursion here is equivalent because `dirnames[:]` is
	// only ever narrowed, never reordered or extended.
	//
	// EQUIVALENT MUTANT (kept, marked `equivalent`): dropping the
	// upward propagation of the refusal below changes nothing. `walk`
	// returns false ONLY when `visited >= indexMaxDirs`, and that test
	// is at its own entry — so once one call refuses, every later call
	// refuses immediately on its own. The propagation saves recursion,
	// not answers.
	var walk func(dir string) bool
	walk = func(dir string) bool {
		if visited >= indexMaxDirs {
			return false
		}
		visited++
		files, subdirs := scandirSplit(dir)
		for _, f := range files {
			names[f] = true
		}
		// `Path(dirpath).relative_to(project_root)` raises ValueError when
		// dirpath is not under root, and the Python `continue`s — which
		// skips the RELPATHS for that directory while KEEPING the
		// basenames it just added. The order of those two statements is
		// the behaviour.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): the ValueError
		// arm cannot fire under this walk, because every directory
		// reached it by descending FROM the root. It is spelled out
		// because the arm exists upstream and a reader is entitled to
		// see it answered.
		if rel, ok := relativeTo(dir, root); ok {
			prefix := ""
			if rel != "." {
				prefix = rel + "/"
			}
			for _, f := range files {
				relpaths[prefix+f] = true
			}
		}
		for _, d := range subdirs {
			if indexSkipDirs[d] {
				continue
			}
			sub := pypath.Join(dir, d)
			// `os.walk`'s top-down branch descends only when
			// `followlinks or not islink(new_path)`, and followlinks
			// defaults to False. So a symlink to a directory contributes
			// NOTHING: `is_dir()` followed the link and kept it out of
			// the filenames, and this keeps it out of the walk. It also
			// costs no slot against the cap, because the directory it
			// points at is never yielded.
			if isSymlink(sub) {
				continue
			}
			if !walk(sub) {
				return false
			}
		}
		return true
	}
	walk(root)
	return names, relpaths
}

// scandirSplit lists one directory the way `os.walk` does: names in
// SCANDIR order, split on `entry.is_dir()`, which FOLLOWS a symlink.
//
// A symlink to a directory therefore lands in dirnames — and, because
// `followlinks` defaults to False, contributes nothing at all: it is not
// recorded as a file and is not descended into. Classifying on Go's
// `DirEntry.IsDir()`, which reports the entry's OWN type, would record it
// as a file and invent a basename CPython never yields.
func scandirSplit(dir string) (files, subdirs []string) {
	fh, err := os.Open(dir)
	if err != nil {
		// `os.walk`'s default onerror swallows a scandir failure and
		// yields nothing for that directory.
		return nil, nil
	}
	defer fh.Close()
	entries, err := fh.Readdirnames(-1)
	if err != nil {
		return nil, nil
	}
	for _, name := range entries {
		st, serr := os.Stat(pypath.Join(dir, name))
		if serr == nil && st.IsDir() {
			subdirs = append(subdirs, name)
			continue
		}
		// `is_dir()` answers False when the stat fails, so a dangling
		// symlink is a FILE here — and gets a basename row.
		files = append(files, name)
	}
	return files, subdirs
}

// isSymlink is `os.path.islink`, which answers False when the lstat
// fails rather than raising.
func isSymlink(p string) bool {
	fi, err := os.Lstat(p)
	return err == nil && fi.Mode()&os.ModeSymlink != 0
}

// relativeTo is `Path(p).relative_to(root)` as posix, with the ValueError
// reported rather than raised.
func relativeTo(p, root string) (string, bool) {
	rel, err := filepath.Rel(root, p)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "../") {
		// `relative_to` is PURELY LEXICAL and refuses to walk upward; Go's
		// Rel is happy to answer with `..`, which would silently invent a
		// relpath outside the tree.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): no caller can
		// reach the upward case today, for the same reason the
		// ValueError arm above cannot. The two tests are the whole
		// reason the two engines agree if one ever does.
		return "", false
	}
	return filepath.ToSlash(rel), true
}

// buildSymbolIndex scans Python source files and returns the top-level
// symbols they declare.
var defOrClassRe = regexp.MustCompile("^" + pytext.SpaceClass +
	"*(?:async" + pytext.SpaceClass + "+)?def" + pytext.SpaceClass +
	"+([A-Za-z_]" + pytext.WordClass + "+)|^" + pytext.SpaceClass +
	"*class" + pytext.SpaceClass + "+([A-Za-z_]" + pytext.WordClass + "+)")

func buildSymbolIndex(root string) map[string]bool {
	symbols := map[string]bool{}
	// `project_root` is searched LAST and covers `src` and `tests` again.
	// The dedup is on the SEARCH DIR, not on the files, so those two are
	// scanned twice — harmless into a set, and faithfully so.
	//
	// The first two entries look redundant — the root pass rglobs the
	// whole tree — and they are not. `rglob` does not descend into a
	// SYMLINKED directory, so a `src` or `tests` that is a link to
	// somewhere outside the project is reached ONLY by being named here,
	// where `is_dir()` follows the link and the glob starts inside it.
	// The battery found this by killing a row that had been reasoned
	// into an equivalence.
	//
	// EQUIVALENT MUTANT (kept, marked `equivalent`): the seen-set is a
	// different matter. It dedups on the SEARCH DIR, and the result is a
	// SET, so a second scan of the same directory adds the same symbols
	// again. It is faithful to the Python, which also keeps it, but no
	// answer depends on it.
	searchDirs := []string{pypath.Join(root, "src"),
		pypath.Join(root, "tests"), root}
	seen := map[string]bool{}
	for _, dir := range searchDirs {
		st, err := os.Stat(dir)
		if err != nil || !st.IsDir() || seen[dir] {
			continue
		}
		seen[dir] = true
		for _, py := range rglobPy(dir) {
			data, rerr := os.ReadFile(py)
			if rerr != nil {
				// `except OSError: continue` — a directory named `*.py`
				// is yielded by rglob and lands here.
				//
				// EQUIVALENT MUTANT (kept, marked `equivalent`): falling
				// through does the same thing, because os.ReadFile
				// returns NO data alongside its error and decoding
				// nothing yields no lines. The branch is spelled because
				// the Python spells it, and because a reader needs to
				// see the directory case answered.
				continue
			}
			// `errors="ignore"` drops undecodable bytes rather than
			// raising, so a file with one bad byte still contributes its
			// other lines.
			for _, line := range pytext.SplitLines(decodeIgnore(data)) {
				m := defOrClassRe.FindStringSubmatch(line)
				if m == nil {
					continue
				}
				sym := m[1]
				if sym == "" {
					sym = m[2]
				}
				if sym != "" {
					symbols[sym] = true
				}
			}
		}
	}
	return symbols
}

// rglobPy is `Path(dir).rglob("*.py")`.
//
// Since 3.13 rglob does NOT recurse into symlinked directories, but it
// still YIELDS one whose name matches — which is why the caller's read
// has to tolerate a directory. Order is irrelevant to the caller (the
// result is an unbounded set), so this does not have to reproduce
// scandir order the way treeIndex does.
func rglobPy(dir string) []string {
	out := []string{}
	var walk func(d string)
	walk = func(d string) {
		fh, err := os.Open(d)
		if err != nil {
			return
		}
		entries, rerr := fh.Readdirnames(-1)
		fh.Close()
		if rerr != nil {
			return
		}
		for _, name := range entries {
			full := pypath.Join(d, name)
			if pytext.FnMatch(name, "*.py") {
				out = append(out, full)
			}
			// The recursion test is the entry's OWN type: a symlinked
			// directory is not descended into.
			st, serr := os.Lstat(full)
			if serr == nil && st.IsDir() {
				walk(full)
			}
		}
	}
	walk(dir)
	return out
}

// decodeIgnore is `bytes.decode("utf-8", errors="ignore")`: every byte
// that is not part of a well-formed sequence is DROPPED, not replaced.
//
// CPython's error handler skips the span its decoder reports and Go's
// DecodeRune reports one byte at a time, but the OUTPUT is the same
// either way, because both spellings drop exactly the bytes that are not
// part of a well-formed sequence. Surrogate and overlong encodings are
// ill-formed to both.
func decodeIgnore(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	for i := 0; i < len(b); {
		r, size := utf8.DecodeRune(b[i:])
		if r == utf8.RuneError && size <= 1 {
			i++
			continue
		}
		sb.Write(b[i : i+size])
		i += size
	}
	return sb.String()
}

// ---------------------------------------------------------------------------
// Verification
// ---------------------------------------------------------------------------

// VerifySymbolClaims extracts symbol claims and verifies them against a
// direct scan of the project's .py files — no grep subprocess, no LLM.
//
// A nil projectRoot means None, and infers.
func VerifySymbolClaims(text string, projectRoot *string, d Deps) SymbolReport {
	root := rootOr(projectRoot, d)
	claims := ExtractSymbolClaims(text)
	if len(claims) == 0 {
		// The early return is not an optimisation: it means an empty
		// claim list never builds the index, so a text with no symbol
		// claims costs nothing even under a huge tree.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): the report it
		// returns is the same one the loop below would build from no
		// claims, so only the COST differs — and cost is the reason.
		return SymbolReport{RawClaims: []string{}, Verified: []string{},
			NotFound: []string{}}
	}
	index := buildSymbolIndex(root)
	verified := []string{}
	notFound := []string{}
	for _, s := range claims {
		if index[s] {
			verified = append(verified, s)
		} else {
			notFound = append(notFound, s)
		}
	}
	return SymbolReport{RawClaims: claims, Verified: verified,
		NotFound: notFound}
}

func rootOr(projectRoot *string, d Deps) string {
	if projectRoot != nil {
		return *projectRoot
	}
	return InferProjectRoot(d)
}

// VerifyFileClaims extracts file-path claims and verifies existence on
// disk.
func VerifyFileClaims(text string, projectRoot *string, d Deps) ClaimReport {
	root := rootOr(projectRoot, d)
	claims := ExtractFileClaims(text)
	verified := []string{}
	notFound := []string{}
	unresolvable := []string{}
	suffixMatched := []SuffixMatch{}
	var names, relpaths map[string]bool
	indexed := false

	for _, claim := range claims {
		// `project_root / claim` — pathlib's `/`, which an ABSOLUTE right
		// operand REPLACES the root with entirely. No claim the pattern
		// produces can be absolute, so this and the `is_absolute()` test
		// below are both defensive; they are kept because the reason they
		// are unreachable is a property of the PATTERN, and patterns move.
		candidate := pypath.Join(root, claim)
		if exists(candidate) {
			verified = append(verified, claim)
			continue
		}
		found := false
		if claimHasNoDirectory(claim) {
			// A bare name verifies if any file of that name exists
			// anywhere under the project. Workers cite bare names for
			// artifacts they saved in subdirs, and "exists somewhere
			// under the project" is the honest existence answer for a
			// name with no directory in it.
			if !indexed {
				names, relpaths = treeIndex(root)
				indexed = true
			}
			// EQUIVALENT MUTANT (kept, marked `equivalent`): this arm
			// runs only when the claim has NO directory component, and
			// `pypath.Name` is the identity on such a claim. The call is
			// here because the Python says `claim_path.name`.
			found = names[pypath.Name(claim)]
		} else if !strings.HasPrefix(claim, "/") {
			// EQUIVALENT MUTANT (kept, marked `equivalent`): see
			// claimHasNoDirectory — no claim is absolute, so this test
			// admits every claim that reaches it.
			// A relative claim WITH a directory component: the goal may
			// have pointed the worker into a subdirectory, so
			// `tests/test_ledger.py` is real even though the root only
			// sees `pkg/tests/test_ledger.py`. The WHOLE claimed path
			// must be a suffix — `wrong_dir/module.py` still cannot match
			// `src/module.py`.
			norm := normpath(claim)
			// EQUIVALENT MUTANT (kept, marked `equivalent`): a claim
			// normalising to `..` or `/` is searched for among relative
			// paths a walk built from the root, none of which can begin
			// with either — so dropping the guard finds nothing and
			// lands the claim in not_found exactly where the guard
			// leaves it. Upstream refuses explicitly, and so does this.
			if !strings.HasPrefix(norm, "..") &&
				!strings.HasPrefix(norm, "/") {
				if !indexed {
					names, relpaths = treeIndex(root)
					indexed = true
				}
				suffix := "/" + norm
				matches := []string{}
				for rp := range relpaths {
					if rp == norm || strings.HasSuffix(rp, suffix) {
						matches = append(matches, rp)
					}
				}
				if len(matches) > 1 {
					// Several is genuinely AMBIGUOUS and lands in
					// unresolvable — neither verified nor a
					// hallucination. Collapsing it to either would be the
					// port deciding something the module refuses to.
					unresolvable = append(unresolvable, claim)
					continue
				}
				if len(matches) == 1 {
					found = true
					suffixMatched = append(suffixMatched,
						SuffixMatch{Claim: claim, RelPath: matches[0]})
				}
			}
		}
		if found {
			verified = append(verified, claim)
		} else {
			notFound = append(notFound, claim)
		}
	}
	return ClaimReport{RawClaims: claims, Verified: verified,
		NotFound: notFound, Unresolvable: unresolvable,
		SuffixMatched: suffixMatched}
}

// claimHasNoDirectory is `PurePosixPath(claim).parent == Path(".")`,
// reduced to the only question the caller asks.
//
// It is spelled out rather than delegated to `path.Dir` because Go's Dir
// CLEANS its answer and pathlib's `.parent` does not: `src/../a.py` has
// parent `src/..`, which is not `.`, while Dir folds it to `.` and sends
// the claim down the bare-name branch instead of the suffix one. Found by
// the differential, which is the only thing that could have.
//
// pathlib drops empty and `.` components when it PARSES, so those do not
// count; `..` is a real component and does.
func claimHasNoDirectory(claim string) bool {
	if strings.HasPrefix(claim, "/") {
		// An absolute claim's parent is the root, never `.`.
		//
		// EQUIVALENT MUTANT (kept, marked `equivalent`): no claim the
		// pattern produces is absolute. The lookbehind does not block a
		// leading `/`, but no alternative can MATCH one — the bare
		// classes exclude it and the prefixed branch opens on a known
		// directory name.
		return false
	}
	n := 0
	for _, p := range strings.Split(claim, "/") {
		// EQUIVALENT MUTANT (kept, marked `equivalent`) on the `.` half:
		// no producible claim has a `.` component that moves the count
		// across the `<= 1` threshold — a bare name has no slash at all,
		// and anything with a slash already has two real components.
		// `./x.py` is not extractable, because the bare alternative's
		// class has no `/`.
		if p == "" || p == "." {
			continue
		}
		n++
	}
	return n <= 1
}

// normpath is `os.path.normpath` on posix, with the separator swap the
// Python does after it folded in.
func normpath(p string) string {
	if p == "" {
		return "."
	}
	return filepath.ToSlash(filepath.Clean(p))
}

// AnnotateResult returns resultText with a claim-verification note
// appended — or unchanged, when the step is clean and the caller asked
// for zero noise.
func AnnotateResult(resultText string, projectRoot *string,
	onlyIfHallucinations, checkSymbols bool, d Deps) string {
	fileReport := VerifyFileClaims(resultText, projectRoot, d)
	symbolReport := SymbolReport{RawClaims: []string{},
		Verified: []string{}, NotFound: []string{}}
	if checkSymbols {
		symbolReport = VerifySymbolClaims(resultText, projectRoot, d)
	}
	hasAny := fileReport.HasHallucinations() || symbolReport.HasHallucinations()
	if onlyIfHallucinations && !hasAny {
		return resultText
	}
	parts := []string{}
	if len(fileReport.NotFound) > 0 {
		parts = append(parts, "FILE_CLAIMS_NOT_FOUND: "+
			strings.Join(fileReport.NotFound, ", "))
	}
	if len(fileReport.Verified) > 0 && !onlyIfHallucinations {
		parts = append(parts, "FILE_CLAIMS_VERIFIED: "+
			strings.Join(head(fileReport.Verified, 5), ", "))
	}
	if len(symbolReport.NotFound) > 0 {
		parts = append(parts, "SYMBOL_CLAIMS_NOT_FOUND: "+
			strings.Join(symbolReport.NotFound, ", "))
	}
	if len(parts) == 0 {
		return resultText
	}
	return resultText + "\n\n[claim-verifier] " + strings.Join(parts, " | ")
}

// ---------------------------------------------------------------------------
// Synthesis step detection
// ---------------------------------------------------------------------------

// synthesisKeywords is a frozenset in Python, and `any()` over it, so the
// order here carries nothing.
var synthesisKeywords = []string{
	"synthesize", "synthesis", "summarize", "compile findings",
	"write report", "analyze all", "review all", "aggregate",
	"combine findings", "final report", "final summary", "conclusion",
}

// IsSynthesisStep is the heuristic for a synthesis/summarisation step —
// the highest hallucination risk, because such a step reads accumulated
// findings from many prior steps and must cite specific files.
func IsSynthesisStep(stepText string) bool {
	lower := pytext.Lower(stepText)
	for _, kw := range synthesisKeywords {
		if strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}
