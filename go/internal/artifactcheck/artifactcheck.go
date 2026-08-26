// Package artifactcheck ports the string-and-filesystem half of
// src/artifact_check.py — the zero-LLM ground-truth check that catches a
// step reporting status="done" for work no file on disk can corroborate.
//
// SCOPE. This is slice 1: everything through `check_execution_claim`
// (Python lines 1-483). The scavenging detector below it — `_in_fence`,
// `fence_allow_roots`, `goal_declared_roots`, `detect_out_of_fence_access`
// and the four regexes they share — is roughly 250 more lines and is its
// own tranche. It is not started, and nothing here pretends otherwise.
//
// WHAT IS NOT HERE, NAMED
//
//   - `_python_is_inert` (Python :245) parses the candidate module with
//     CPython's `ast` and asks whether its top-level body is purely
//     definitions. Go has no way to answer that question about Python
//     source, and a hand-rolled Python parser would be a much larger and
//     much less trustworthy thing than the check it serves. It is not
//     ported. It is INJECTED: every entry point that can reach layer 2
//     takes an InertFunc, and passing nil is a supported, documented
//     choice that makes layer 2 permanently fail open. See InertFunc.
//
//   - Layer 2's control flow IS here — `_python_candidates`,
//     `_inert_output_verdict`, and the layer ordering inside
//     `check_fabrication` — because that half is ordinary logic and is
//     where the port can get things wrong. The differential drives it with
//     inertness values MEASURED from CPython, so the seam carries data and
//     never carries the assertion.
//
// TWO REGEXES DO NOT SURVIVE TRANSCRIPTION, and both are the reason this
// package has more prose than code. Their translations are derived in
// outputClaimRE and stdoutClaimBranches below, not asserted.
package artifactcheck

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// MaxFiles is Python's `_MAX_FILES` (:93): the safety cap on the workspace
// walk.
//
// A note the Python does not make and this port has to: at the cap, the
// answer is not a function of the input. Python fills `snap` in
// `os.scandir` order, which on ext4 is a hash order that depends on how
// the directory was built, so CPython does not return the same 20000 files
// twice for two trees with the same contents. This port walks in sorted
// order (`os.ReadDir`). Neither is more correct and no differential can
// pin the difference, because the reference side has no stable answer to
// compare against. Named, not silently absorbed.
//
// One consequence, recorded because the mutation battery found it and it
// would otherwise read as a coverage hole: nothing pins the ORDER of the
// counter and the stat. Python increments AFTER the try, so an unstattable
// file does not spend one of the 20000, and this port does the same — but
// moving the increment above the guard survives the whole suite, because
// telling the two apart needs a tree of 20000 files with an unreadable one
// in it. That is inside the region the paragraph above already declares
// uncomparable, so it is left unpinned deliberately rather than pinned
// with a 20000-file fixture that would prove one arm of a behaviour whose
// other arm nothing can check.
const MaxFiles = 20000

// MtimeEps is Python's `_MTIME_EPS` (:96), the mtime jitter tolerance in
// seconds. Kept as the literal 1e-4 rather than a time.Duration: the
// comparison it feeds is a float subtraction on raw st_mtime seconds, and
// routing it through Duration would round it to nanoseconds on the way in
// and change which side of the boundary a value lands on.
const MtimeEps = 1e-4

// skipDirs is Python's `_SKIP_DIRS` (:91).
var skipDirs = map[string]bool{
	".git": true, "__pycache__": true, ".mypy_cache": true,
	".pytest_cache": true, "node_modules": true,
}

// executionTools is Python's `_EXECUTION_TOOLS` (:405) — the one-element
// frozenset kept as a set because that is what it is, not because a second
// member is expected.
var executionTools = map[string]bool{"Bash": true}

// Verdict is Python's `ArtifactVerdict` dataclass (:99).
//
// Judged carries the field's real meaning and its real default: True. It
// is False only on the fail-open path, where fabricated=False means "the
// check errored", not "clean bill" — a distinction every reader of this
// struct got wrong before the field existed (Python :108-111).
type Verdict struct {
	Fabricated   bool
	Claims       []string
	Missing      []string
	ChangedCount int
	Reason       string
	// Kind is "" | "missing-artifact" | "inert-output" |
	// "execution-contradiction".
	Kind   string
	Judged bool
}

// newVerdict builds a Verdict with the dataclass's defaults, which is the
// only way to get Judged=true without every construction site repeating
// it. The Python gets this from `field(default=True)`; a Go zero value
// gets it backwards, and a zero-value Verdict is therefore a fail-open
// verdict that nobody asked for. Construct through here.
func newVerdict() Verdict {
	return Verdict{Claims: []string{}, Missing: []string{}, Judged: true}
}

// ToDict renders the dataclass the way `dataclasses.asdict` does — every
// field, in declaration order, no omissions. The differential compares
// this against CPython's own dict, so a field that stops being emitted
// here is a diff rather than a silently narrower comparison.
func (v Verdict) ToDict() pyval.Obj {
	var o pyval.Obj
	o.Set("fabricated", v.Fabricated)
	o.Set("claims", nonNil(v.Claims))
	o.Set("missing", nonNil(v.Missing))
	o.Set("changed_count", v.ChangedCount)
	o.Set("reason", v.Reason)
	o.Set("kind", v.Kind)
	o.Set("judged", v.Judged)
	return o
}

// nonNil is the empty-list-not-null rule: `field(default_factory=list)`
// produces `[]`, and a nil slice would emit `null`. Absence and empty are
// different values on the wire and this port does not get to confuse them.
func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// --- small shared helpers -------------------------------------------------

// reprList is Python's f-string rendering of a `List[str]`, which is the
// list's repr: `['a.txt', 'b.txt']`, each element quoted Python's way and
// separated by ", ". Both reason strings this package builds interpolate a
// list, and Go's %v would print `[a.txt b.txt]` — a spelling CPython
// cannot produce, in an operator-facing string both runtimes write.
func reprList(ss []string) string { return pyval.ReprStrings(ss) }

// itoa keeps the reason strings free of a fmt import, matching the rest of
// the tree.
func itoa(n int) string { return strconv.Itoa(n) }

// decodeReplace is `bytes.decode("utf-8", errors="replace")`: every
// ill-formed byte becomes U+FFFD. Go's []byte->string conversion keeps the
// bad bytes as-is, and they would then reach the InertFunc — which is a
// Python parser being handed something CPython would never have shown it.
func decodeReplace(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	for i := 0; i < len(b); {
		r, size := utf8.DecodeRune(b[i:])
		if r == utf8.RuneError && size == 1 {
			sb.WriteRune(utf8.RuneError)
		} else {
			sb.Write(b[i : i+size])
		}
		i += size
	}
	return sb.String()
}

// isLetterOrNumber is the `\p{L}\p{N}` half of pytext.WordClassBody as a
// predicate, so boundaryAt and the compiled classes cannot drift.
func isLetterOrNumber(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsNumber(r)
}

// --- the write-claim regex ------------------------------------------------

// outputClaimRE is Python's `_OUTPUT_CLAIM_RE` (:55).
//
// The Python:
//
//	\b(?:sav\w*|writ\w*|wrote|creat\w*|output\w*|stor\w*|export\w*|
//	     generat\w*|dump\w*)\b
//	[^.\n]*?\b(?:to|into|at|as)\s+[`'"(]?(?P<path>[^\s`'")]+)
//
// Three separate things have to change, and only the third is interesting.
//
// 1. `\w` and `\s` are Unicode-aware on a Python str pattern and ASCII in
// Go. pytext.WordClass and pytext.SpaceClass are the measured stand-ins.
//
// 2. `\b` is likewise Unicode-aware, and RE2 has no zero-width assertion
// for it, so pytext's WordStart/WordEnd CONSUME the boundary character.
// That is safe only at the ends of a pattern.
//
// 3. Both of this pattern's remaining `\b`s are INTERIOR — there is more
// pattern after them — so neither may use WordEnd/WordStart. They fold
// into the window between them, and the folding is the translation:
//
//	Python   VERB \b [^.\n]*? \b (?:to|into|at|as)
//	RE2      VERB (?: NW | NW [^.\n]*? NW ) (?:to|into|at|as)
//	         where NW = pytext.NotWordClassPlus(".\n")
//
// The window's FIRST character carries the boundary after VERB and its
// LAST carries the boundary before the preposition, which is the same
// character when the window is one wide — hence the two-branch shape
// rather than a `{1,}` with two NWs that would demand a minimum of two.
//
// The zero-width window is dropped entirely, and that is a claim worth
// stating because dropping a case is how a translation quietly narrows.
// With an empty window the two `\b`s are the SAME position, between VERB's
// last character and the preposition's first. Every VERB alternative ends
// in `\w*` or a literal letter, so VERB's last character is always a word
// character; `t`, `i`, and `a` are word characters. No boundary can exist
// there, so Python cannot match with an empty window either. E19 is the
// fixture that would fail if this reasoning were wrong.
//
// A fourth thing does NOT change: `[^.\n]` is a literal class in both, and
// the non-greedy `*?` means the same thing in both, because Go's regexp
// resolves alternation and repetition leftmost-FIRST (Perl semantics), not
// leftmost-longest. `regexp.MustCompilePOSIX` would not, and is not used.
var outputClaimRE = regexp.MustCompile(
	`(?i)` + pytext.WordStart +
		`(?:sav` + pytext.WordClass + `*` +
		`|writ` + pytext.WordClass + `*` +
		`|wrote` +
		`|creat` + pytext.WordClass + `*` +
		`|output` + pytext.WordClass + `*` +
		`|stor` + pytext.WordClass + `*` +
		`|export` + pytext.WordClass + `*` +
		`|generat` + pytext.WordClass + `*` +
		`|dump` + pytext.WordClass + `*` + `)` +
		`(?:` + notWordDotNL + `|` + notWordDotNL + `[^.\n]*?` + notWordDotNL + `)` +
		`(?:to|into|at|as)` + pytext.SpaceClass + `+` +
		"[`'\"(]?" +
		`(?P<path>[^` + pytext.SpaceClassBody + "`'\"" + `)]+)`)

var notWordDotNL = pytext.NotWordClassPlus(".\n")

// extRE is Python's `_EXT_RE` (:64), `\.[A-Za-z0-9]{1,6}$`.
//
// The `\n?` is not decoration. Python's `$` without re.MULTILINE matches
// at the end of the string OR immediately before a single trailing
// newline; Go's `$` without `(?m)` matches only at the end. Writing `$`
// alone here would be a narrower pattern wearing the same characters.
//
// It is also unobservable from anywhere this package can reach, and that
// is a measured claim rather than a hopeful one: the mutation battery
// removed the `\n?` and the whole suite stayed green. The only caller
// passes a token from cleanToken, whose leading `strip()` removes
// newlines, and the token comes from a match group that excludes
// whitespace to begin with — so no input produces a token this could tell
// apart.
//
// E34 is NOT the fixture that pins it, which is what this comment claimed
// until the battery said otherwise. E34's text is "wrote to a.txt\n"; the
// newline never reaches the token. Nothing pins it, nothing can, and it
// stays because the Python line is `$` and a port that "simplifies" a
// pattern it cannot distinguish is guessing rather than porting.
//
// The character class stays ASCII because Python wrote it out as ASCII.
// `[A-Za-z0-9]` is not `\w` and does not acquire Unicode members.
var extRE = regexp.MustCompile(`\.[A-Za-z0-9]{1,6}\n?$`)

// cleanToken is Python's `_clean_token` (:114):
//
//	tok.strip().strip("`'\"()").rstrip(".,;:")
//
// Three passes, and their ORDER is observable, which is why this is not
// one TrimFunc over the union of the three sets. `strip()` runs FIRST, so
// " `a.txt` " loses its spaces and then its backticks; a token that is
// “ `a.txt ` “ — wrapper, content, space, wrapper — keeps the inner
// space, because by the time the wrapper strip runs the outer whitespace
// is gone and the trailing wrapper is not adjacent to anything it can
// reach. `rstrip` runs LAST, so a trailing "." survives the wrapper pass
// and dies in the third. T6 and T7 are the fixtures for the two orders.
func cleanToken(tok string) string {
	s := pytext.Strip(tok)
	s = strings.Trim(s, "`'\"()")
	return strings.TrimRight(s, ".,;:")
}

// ExtractWriteClaims is Python's `extract_write_claims` (:118).
//
// `text or ""` is Python truthiness on a str, which for the only type this
// signature accepts means "empty stays empty" — there is no observable
// difference here and the Go says nothing extra.
//
// The guard is Python's, including a clause that cannot fire. The Python
// reads:
//
//	if not tok or tok in ("/", "./", "../") or tok.endswith("/"):
//
// and every one of those three literals ENDS IN "/", so the middle test is
// subsumed by the one after it. Established by mutation, not by reading:
// deleting the "../" arm here left all 191 fixtures green, which is what
// sent someone to look at why.
//
// It is kept because this is a port and the Python line has three tests.
// The dead clause is a PYTHON-side observation and is filed as one; it is
// not this port's to delete, and deleting it here would make the two files
// disagree about something neither can observe.
func ExtractWriteClaims(text string) []string {
	out := []string{}
	seen := map[string]bool{}
	idx := outputClaimRE.SubexpIndex("path")
	for _, m := range outputClaimRE.FindAllStringSubmatch(text, -1) {
		tok := cleanToken(m[idx])
		if tok == "" || tok == "/" || tok == "./" || tok == "../" ||
			strings.HasSuffix(tok, "/") {
			continue
		}
		if !extRE.MatchString(tok) {
			continue
		}
		if !seen[tok] {
			seen[tok] = true
			out = append(out, tok)
		}
	}
	return out
}

// --- the workspace diff ---------------------------------------------------

// SnapshotDir is Python's `snapshot_dir` (:156): relpath -> mtime for every
// file under root, {} if root is missing, never raises.
//
// `if not root` is Python truthiness, and both of the falsy values the
// annotation admits — None and "" — collapse to the same Go check. W5 and
// W6 are the two fixtures that keep that collapse honest.
//
// The walk mirrors `os.walk(base)` with `followlinks` at its default:
// top-down, pre-order, this directory's files before its subdirectories,
// and `dirnames[:]` pruned in place so a skipped directory is not
// descended into.
//
// A symlink is classified the way `os.scandir` classifies it, which is by
// FOLLOWING it: a symlink to a directory lands in dirnames (and so is
// neither descended into nor recorded, because followlinks is False), and
// a symlink to a file lands in filenames and is stat'd through. Go's
// `os.ReadDir` reports a symlink's own type, so classifying on
// `entry.IsDir()` alone would put a symlinked directory into filenames and
// record it as a file — a divergence with no Python line to point at.
func SnapshotDir(root string) map[string]float64 {
	snap := map[string]float64{}
	if root == "" {
		return snap
	}
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		return snap
	}
	count := 0
	var walk func(dir string) bool // false = stop, the cap was hit
	walk = func(dir string) bool {
		entries, err := os.ReadDir(dir)
		if err != nil {
			// os.walk's default onerror swallows a scandir failure and
			// yields nothing for that directory, then carries on with
			// the ones it already has. It does not abandon the walk.
			return true
		}
		var subdirs []string
		for _, e := range entries {
			name := e.Name()
			isDir := e.IsDir()
			isLink := e.Type()&os.ModeSymlink != 0
			if isLink {
				// scandir's is_dir() follows; a dangling link is neither.
				if st, err := os.Stat(filepath.Join(dir, name)); err == nil {
					isDir = st.IsDir()
				} else {
					isDir = false
				}
			}
			if isDir {
				// A symlinked directory goes into `dirnames` and is then
				// NOT recursed into, because os.walk's followlinks
				// defaults to False. So it contributes nothing at all:
				// not its own row (it is a directory, and only files get
				// rows) and not its contents. Descending here instead —
				// which is what the first version of this walk did —
				// silently adds every file under the link target, and W17
				// is the fixture that caught it.
				if !isLink && !skipDirs[name] {
					subdirs = append(subdirs, name)
				}
				continue
			}
			fp := filepath.Join(dir, name)
			st, err := os.Stat(fp)
			if err != nil {
				// Python's `except OSError: continue` — and the count is
				// incremented AFTER the try, so an unstattable file does
				// not spend one of the 20000.
				continue
			}
			rel, rerr := filepath.Rel(root, fp)
			if rerr != nil {
				continue
			}
			snap[rel] = float64(st.ModTime().UnixNano()) / 1e9
			count++
			if count >= MaxFiles {
				return false
			}
		}
		sort.Strings(subdirs)
		for _, d := range subdirs {
			if !walk(filepath.Join(dir, d)) {
				return false
			}
		}
		return true
	}
	walk(root)
	return snap
}

// ChangedSince is Python's `changed_since` (:186).
//
// `prev is None` is absence, not zero: a file whose recorded mtime is 0.0
// is present in `before` and is compared, while a file missing from it is
// new and is changed unconditionally. A Go map read returning the zero
// value cannot tell those apart, so the two-value form is load-bearing.
func ChangedSince(before map[string]float64, root string) map[string]bool {
	after := SnapshotDir(root)
	changed := map[string]bool{}
	for rel, mtime := range after {
		prev, ok := before[rel]
		if !ok || (mtime-prev) > MtimeEps {
			changed[rel] = true
		}
	}
	return changed
}

// FilesModifiedSince is Python's `files_modified_since` (:137).
//
// The default limit is 20 and is keyword-only in Python; Go has no such
// thing, so it is an ordinary parameter and every call site says the
// number. `datetime.fromisoformat` failing on ValueError or TypeError is
// the empty-list path, and a NAIVE stamp is interpreted in LOCAL time,
// which is what `.timestamp()` does to a tz-less datetime — W15 is the
// fixture, and it is the reason this does not reach for time.UTC.
func FilesModifiedSince(root, sinceISO string, limit int) []string {
	ts, ok := parseISO(sinceISO)
	if !ok {
		return []string{}
	}
	snap := SnapshotDir(root)
	var changed []string
	for rel, mtime := range snap {
		if mtime >= ts {
			changed = append(changed, rel)
		}
	}
	sort.Strings(changed)
	if limit < 0 {
		// Python's `changed[:limit]` with a negative limit drops from the
		// END, which is not "no limit". Preserved rather than clamped.
		if n := len(changed) + limit; n > 0 {
			changed = changed[:n]
		} else {
			changed = nil
		}
	} else if len(changed) > limit {
		changed = changed[:limit]
	}
	if changed == nil {
		return []string{}
	}
	return changed
}

// parseISO is the `datetime.fromisoformat(s).timestamp()` pair, returning
// false where Python raises ValueError or TypeError.
//
// A naive stamp gets LOCAL time, matching `.timestamp()`'s behaviour on a
// tz-less datetime; an aware one keeps its offset.
func parseISO(s string) (float64, bool) {
	if s == "" {
		return 0, false
	}
	layouts := []string{
		"2006-01-02T15:04:05.999999999Z07:00",
		"2006-01-02T15:04:05Z07:00",
		"2006-01-02 15:04:05.999999999Z07:00",
		"2006-01-02 15:04:05Z07:00",
	}
	for _, l := range layouts {
		if t, err := time.Parse(l, s); err == nil {
			return float64(t.UnixNano()) / 1e9, true
		}
	}
	naive := []string{
		"2006-01-02T15:04:05.999999999",
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05.999999999",
		"2006-01-02 15:04:05",
		"2006-01-02T15:04",
		"2006-01-02 15:04",
		"2006-01-02",
	}
	for _, l := range naive {
		if t, err := time.ParseInLocation(l, s, time.Local); err == nil {
			return float64(t.UnixNano()) / 1e9, true
		}
	}
	return 0, false
}

// existsAnywhere is Python's `_exists_anywhere` (:197).
//
// It deliberately does NOT consult the process's cwd; the reasoning is in
// the Python docstring and is a design decision, not an omission.
//
// `Path.exists()` swallows ValueError as well as OSError, which is how a
// claim containing a NUL byte answers False instead of exploding out
// through check_fabrication's blanket except and costing the caller its
// judged=True. A9 is the fixture, and it is measured — the alternative
// reading (that the ValueError escapes and fails the whole check open) is
// equally plausible from the source and is wrong.
func existsAnywhere(claim, projectDir string) bool {
	if strings.ContainsRune(claim, 0) {
		return false
	}
	if filepath.IsAbs(claim) {
		return pathExists(claim)
	}
	if projectDir != "" {
		if pathExists(filepath.Join(projectDir, claim)) {
			return true
		}
		if pathExists(filepath.Join(projectDir, pyBasename(claim))) {
			return true
		}
	}
	return false
}

// pathExists is `Path.exists()`: it FOLLOWS symlinks, so a dangling link
// is not evidence of anything, and any error is False.
func pathExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// pyBasename is `PurePath.name`, which is not filepath.Base.
//
// They disagree in general — `Path("/").name` is "" while
// `filepath.Base("/")` is "/", and `Path(".").name` is "" while
// `filepath.Base(".")` is "." — but NOT on anything this package can
// reach, which the mutation battery established by swapping one for the
// other and watching all 191 fixtures stay green.
//
// The reachable inputs are narrow on both call sites. existsAnywhere sees
// only tokens ExtractWriteClaims produced, which are non-empty, do not end
// in "/", and take the absolute branch when they start with one; the
// inert-output reason sees only paths built by filepath.Join. On every one
// of those the two functions agree, and where they do not
// (`filepath.Base("")` is "." and this returns "") filepath.Join cleans the
// difference away before anything observes it.
//
// It stays because the Python line is `PurePath.name`, and because the
// narrowness is a property of today's callers rather than of the function.
// Recorded as equivalent-under-reachable-input, not as pinned.
func pyBasename(p string) string {
	p = strings.TrimRight(p, "/")
	if p == "" || p == "." || p == ".." {
		return ""
	}
	if i := strings.LastIndexByte(p, '/'); i >= 0 {
		return p[i+1:]
	}
	return p
}

// --- the stdout claim -----------------------------------------------------

// stdoutBranch is one alternative of Python's `_STDOUT_CLAIM_RE` (:79),
// kept separate because one of them carries a negative LOOKAHEAD and RE2
// has no expression for lookaround at all.
//
// Decomposing an alternation into per-branch matching is only sound
// because the caller needs a BOOLEAN. `_claims_concrete_stdout` asks
// whether `.search()` found anything; Python's engine, on hitting the
// lookahead and failing, backtracks and keeps looking — at other
// alternatives and at later positions — so "search succeeded" means
// exactly "some branch matched somewhere with its own conditions met".
// That is what the OR over branches computes. It would NOT be sound for a
// caller that wanted the match offsets or the matched text, and there is
// no such caller here; if one appears, this decomposition has to be
// revisited rather than reused.
type stdoutBranch struct {
	name string
	re   *regexp.Regexp
	// after, when non-nil, is the negative lookahead: it is handed the
	// text following the branch's match and returns true if the match
	// SURVIVES. This is where `(?!\s+(?:file|to|into|path|dir))` lives.
	after func(tail string) bool
	// wordEnd is the branch-trailing `\b`. It is checked against the
	// character after the match rather than compiled in, because
	// pytext.WordEnd consumes a character and the lookahead needs the
	// unconsumed tail to look at.
	wordEnd bool
}

// stdoutClaimBranches is `_STDOUT_CLAIM_RE` taken apart.
//
// Worth noticing about the Python, because it looks like an oversight and
// is preserved either way: the leading `\b` applies to the whole group but
// there is NO trailing `\b` on the group. Only two alternatives carry one
// internally — `output(?:s|ted|ting)?\b` and the `the\b` inside
// `running\s+(?:it|the\b)`. So `prints?` matches inside "printsomething",
// and `stdout` matches inside "stdoutish". The port reproduces that. S20
// is the fixture that would notice if a tidier translation added the
// boundary the Python does not have.
var stdoutClaimBranches = buildStdoutBranches()

func buildStdoutBranches() []stdoutBranch {
	sp := pytext.SpaceClass
	ws := pytext.WordStart
	lit := func(name, body string) stdoutBranch {
		return stdoutBranch{name: name, re: regexp.MustCompile(`(?i)` + ws + body)}
	}
	// The excluded words after "output". Python's `\s+` is Unicode-aware,
	// so the separator class is pytext's, and the words themselves carry
	// no boundary of their own — `(?!\s+(?:file|to|...))` fails the match
	// for "output to" and equally for "output tomorrow", because the
	// lookahead only has to match a PREFIX. S7 and S10 are the pair that
	// pins that: "output to" is excluded and "output quickly" is not, and
	// the honest question here was whether "output tolerances" is excluded
	// too. It is.
	excl := regexp.MustCompile(`(?i)^` + sp + `+(?:file|to|into|path|dir)`)
	return []stdoutBranch{
		lit("prints", `prints?`),
		lit("printed", `printed`),
		lit("printing", `printing`),
		lit("stdout", `stdout`),
		{
			name:    "output",
			re:      regexp.MustCompile(`(?i)` + ws + `output(?:s|ted|ting)?`),
			wordEnd: true,
			after:   func(tail string) bool { return !excl.MatchString(tail) },
		},
		lit("verified-output", `verified`+sp+`+(?:the`+sp+`+)?output`),
		lit("confirmed-output", `confirmed`+sp+`+(?:the`+sp+`+)?output`),
		lit("the-output-is", `the`+sp+`+output`+sp+`+(?:is|was)`),
		lit("produces-output", `produces?`+sp+`+(?:the`+sp+`+)?output`),
		lit("when-run", `(?:when|after)`+sp+`+(?:you`+sp+`+)?run`),
		{
			name:    "running-the",
			re:      regexp.MustCompile(`(?i)` + ws + `running` + sp + `+the`),
			wordEnd: true,
		},
		lit("running-it", `running`+sp+`+it`),
	}
}

// concreteOutputRE is Python's `_CONCRETE_OUTPUT_RE` (:88):
// `\d|'[^']+'|"[^"]+"|` + backtick span.
//
// `\d` is every Unicode decimal digit in Python and `[0-9]` in Go, so it
// is pytext.DigitClass. The three literal spans are ASCII quote characters
// and stay as written. Each requires a NON-EMPTY interior, which is why
// the empty quoted literal in S23 is not concrete content.
var concreteOutputRE = regexp.MustCompile(
	pytext.DigitClass + `|'[^']+'|"[^"]+"|` + "`[^`]+`")

// claimsConcreteStdout is Python's `_claims_concrete_stdout` (:222): a
// runtime-stdout verb AND concrete output-looking content. `returns` is
// excluded on purpose — it is true of an inert module.
func claimsConcreteStdout(text string) bool {
	if text == "" {
		return false
	}
	return searchStdoutClaim(text) && concreteOutputRE.MatchString(text)
}

// searchStdoutClaim is `_STDOUT_CLAIM_RE.search(text)` reduced to its
// boolean, over the decomposed branches. See stdoutBranch for why that
// reduction is sound.
func searchStdoutClaim(text string) bool {
	for _, b := range stdoutClaimBranches {
		for _, loc := range b.re.FindAllStringIndex(text, -1) {
			end := loc[1]
			if b.wordEnd && !boundaryAt(text, end) {
				continue
			}
			if b.after != nil && !b.after(text[end:]) {
				continue
			}
			return true
		}
	}
	return false
}

// boundaryAt reports whether Python's `\b` holds at byte offset i, given
// that the character BEFORE i is a word character — which is the only
// position any caller here asks about, since every branch that needs it
// ends in a letter. The boundary holds at end-of-string or before a
// non-word character.
func boundaryAt(s string, i int) bool {
	if i >= len(s) {
		return true
	}
	return !isWordChar([]rune(s[i:])[0])
}

// isWordChar is pytext's WordClassBody as a predicate. It shares the class
// body's known limit: 5004 code points are `\w` to CPython 3.14's Unicode
// 16.0 tables and not to Go's 15.0, all of them letters in recently-added
// scripts. That gap is named in pytext and is not re-litigated here; what
// matters is that this predicate and the compiled classes agree with each
// other, so a boundary check and a class match never disagree about the
// same character.
func isWordChar(r rune) bool {
	return r == '_' || isLetterOrNumber(r)
}

// --- layer 2 --------------------------------------------------------------

// InertFunc is the seam standing where `_python_is_inert` (:245) would be.
//
// It answers the question that function answers: given Python source, does
// running it as a script provably produce no stdout? The two return values
// are Python's tri-state — `True`, `False`, and `None` for "cannot tell",
// which is the syntax-error case and which makes the caller fail open.
// `known=false` is None; `inert` is meaningless when known is false.
//
// Passing nil is legitimate and is what production does until something
// can answer: a nil InertFunc means layer 2 never fires, so
// CheckFabrication becomes layer 1 alone. That is a behaviour difference
// from CPython, it is the largest one in this package, and it is the
// reason this parameter is on the signature instead of hidden in a
// package-level variable. A seam you can see is a seam a reader can price.
type InertFunc func(source string) (inert bool, known bool)

// pythonCandidates is Python's `_python_candidates` (:266): existing .py
// files this step plausibly produced.
//
// The dedup key is the RESOLVED path (`p.resolve()`), so two claims that
// reach the same file through different spellings are one candidate; when
// resolve fails the unresolved string is the key, which is a different key
// and can therefore admit a duplicate. Preserved as written.
//
// `changed` is a set, and Python iterates it in hash order, so the ORDER
// of the fresh-file candidates is not deterministic on the Python side.
// This port sorts them. The order is observable — it reaches the `names`
// list in the inert-output reason string — so the differential drives that
// case with at most one fresh candidate, and the multi-candidate ordering
// is a named residual rather than a comparison nobody can win.
func pythonCandidates(claims []string, projectDir string, changed map[string]bool) []string {
	var out []string
	seen := map[string]bool{}
	add := func(p string) {
		rp, err := filepath.EvalSymlinks(p)
		if err != nil {
			rp = p
		} else {
			rp, _ = filepath.Abs(rp)
		}
		if seen[rp] {
			return
		}
		st, err := os.Stat(p)
		if err != nil || st.IsDir() {
			return
		}
		seen[rp] = true
		out = append(out, p)
	}
	for _, c := range claims {
		if !strings.HasSuffix(c, ".py") {
			continue
		}
		if filepath.IsAbs(c) {
			add(c)
		} else if projectDir != "" {
			add(filepath.Join(projectDir, c))
		}
	}
	if projectDir != "" {
		var fresh []string
		for rel := range changed {
			if strings.HasSuffix(rel, ".py") {
				fresh = append(fresh, rel)
			}
		}
		sort.Strings(fresh)
		for _, rel := range fresh {
			add(filepath.Join(projectDir, rel))
		}
	}
	return out
}

// inertOutputVerdict is Python's `_inert_output_verdict` (:300).
//
// The loop's shape is the whole of it, and it is not a simple "any inert
// candidate": a single candidate that CAN produce output returns None for
// the whole check, even if another candidate was already found inert. So
// the answer depends on candidate order only in that it exits early — the
// verdict itself does not, because any False anywhere is fatal to the
// claim. `inert_any` then guards the case where the candidate list somehow
// produced no True at all.
//
// Every ambiguity is None: unreadable file, unparseable source, a
// non-inert candidate. Fail open is the design, not an accident.
func inertOutputVerdict(resultText, projectDir string, claims []string,
	changed map[string]bool, isInert InertFunc) (Verdict, bool) {

	if !claimsConcreteStdout(resultText) {
		return Verdict{}, false
	}
	cands := pythonCandidates(claims, projectDir, changed)
	if len(cands) == 0 {
		return Verdict{}, false
	}
	if isInert == nil {
		// The seam is empty. Python would consult the AST here; this port
		// cannot, and answers "no opinion" — the same answer Python gives
		// for a file it cannot parse.
		return Verdict{}, false
	}
	inertAny := false
	for _, path := range cands {
		src, err := os.ReadFile(path)
		if err != nil {
			return Verdict{}, false
		}
		// `read_text(encoding="utf-8", errors="replace")` never raises on
		// bad bytes; it substitutes U+FFFD. Go's []byte -> string does not
		// substitute, so the replacement is explicit.
		inert, known := isInert(decodeReplace(src))
		if !known {
			return Verdict{}, false
		}
		if !inert {
			return Verdict{}, false
		}
		inertAny = true
	}
	if !inertAny {
		return Verdict{}, false
	}
	names := make([]string, 0, len(cands))
	for _, p := range cands {
		names = append(names, pyBasename(p))
	}
	v := newVerdict()
	v.Fabricated = true
	v.Claims = claims
	v.ChangedCount = len(changed)
	v.Reason = "step claimed concrete program output but the produced file(s) " +
		reprList(names) + " are inert (no __main__/top-level code) and print " +
		"nothing when run"
	v.Kind = "inert-output"
	return v, true
}

// --- the two entry points -------------------------------------------------

// CheckFabrication is Python's `check_fabrication` (:344).
//
// The layer ORDER is observable and is the subject of the D fixtures: when
// both layers would fire, layer 1 wins, because it returns before layer 2
// is consulted. A reading that ran both and preferred the "stronger" one
// would produce a different kind on the same input.
//
// The blanket `except Exception` is the fail-open contract: any internal
// error yields fabricated=False with judged=FALSE, which is how a reader
// tells "the check found nothing" from "the check broke". Go has no
// exceptions to catch, so the honest port of a blanket try is not a
// recover() wrapper pretending to be one — it is to make every operation
// inside it total, which the helpers above are, and to say so here. The
// one thing that could still panic is a caller's InertFunc, and that is
// the caller's own code running inside this frame; it is recovered,
// because Python would have caught it.
func CheckFabrication(resultText, projectDir string, before map[string]float64,
	isInert InertFunc) (v Verdict) {

	defer func() {
		if r := recover(); r != nil {
			v = Verdict{Reason: "check error (fail-open)", Judged: false,
				Claims: []string{}, Missing: []string{}}
		}
	}()

	claims := ExtractWriteClaims(resultText)
	changed := ChangedSince(before, projectDir)

	if len(claims) > 0 {
		var missing []string
		for _, c := range claims {
			if !existsAnywhere(c, projectDir) {
				missing = append(missing, c)
			}
		}
		if len(changed) == 0 && len(missing) == len(claims) {
			out := newVerdict()
			out.Fabricated = true
			out.Claims = claims
			out.Missing = missing
			out.ChangedCount = len(changed)
			out.Reason = "step claimed to write " + reprList(claims) +
				" but produced no files in the workspace and none exist on disk"
			out.Kind = "missing-artifact"
			return out
		}
	}

	if inert, ok := inertOutputVerdict(resultText, projectDir, claims, changed, isInert); ok {
		return inert
	}

	out := newVerdict()
	out.Claims = claims
	out.ChangedCount = len(changed)
	out.Reason = "no fabrication signal"
	return out
}

// ToolEvent is one entry of the inner agent's real tool transcript.
//
// It is a map and not a struct, because the Python is a dict and every
// field this code reads is read with `.get`, over data that arrives from
// outside. A struct loses two distinctions the checks actually turn on:
//
//   - `is_error` is TRUTHINESS over whatever JSON put there, not a bool.
//     "false" the string is true; 0 is false; [] is false; a non-empty
//     list is true. A `bool` field would silently answer a different
//     question for five of the seven F fixtures.
//   - `te.get("name", "?")` distinguishes an ABSENT name from a name that
//     is the empty string; the first falls back to "?" and the second does
//     not. A `Name string` field collapses them.
type ToolEvent map[string]any

// toolFailed is Python's `_tool_failed` (:428). It reads `is_error` and
// deliberately does NOT scan the output text: a run that legitimately
// prints "0 failed" would false-positive a marker scan.
func toolFailed(te ToolEvent) bool {
	return pyval.Truthy(te["is_error"])
}

// successClaimRE is Python's `_SUCCESS_CLAIM_RE` (:408). `\s` and `\b`
// carry the same Unicode treatment as everywhere else in this file; the
// two emoji are literal.
var successClaimRE = regexp.MustCompile(`(?i)` +
	pytext.WordStart + `(?:all` + pytext.SpaceClass + `+)?(?:tests?` + pytext.SpaceClass + `+)?pass(?:ed|es|ing)?` + pytext.WordEnd +
	`|succe(?:ss|ssful(?:ly)?|eded)` + pytext.WordEnd +
	`|` + pytext.WordStart + `works?` + pytext.WordEnd +
	`|no` + pytext.SpaceClass + `+errors?` + pytext.WordEnd +
	`|no` + pytext.SpaceClass + `+failures?` + pytext.WordEnd +
	`|` + pytext.WordStart + `0` + pytext.SpaceClass + `+fail` + pytext.WordClass + `*` +
	`|exit(?:` + pytext.SpaceClass + `+code)?` + pytext.SpaceClass + `+0` + pytext.WordEnd +
	`|completed` + pytext.SpaceClass + `+successfully` + pytext.WordEnd +
	`|built?` + pytext.SpaceClass + `+(?:cleanly|successfully)` + pytext.WordEnd +
	`|✓|✅`)

// failureAckRE is Python's `_FAILURE_ACK_RE` (:420) — the guard that keeps
// the execution check positive-evidence-only. A result that ADMITS a
// problem is not claiming clean success, and must not be flagged.
var failureAckRE = regexp.MustCompile(`(?i)` + pytext.WordStart +
	`(?:fail(?:ed|s|ing|ure)?|error(?:ed|s)?|traceback|exception` +
	`|did` + pytext.SpaceClass + `+not|didn'?t|could` + pytext.SpaceClass + `*n'?t|unable|broke(?:n)?|crash` + pytext.WordClass + `*` +
	`|non[- ]zero|exit` + pytext.SpaceClass + `+(?:code` + pytext.SpaceClass + `+)?[1-9])` + pytext.WordEnd)

// claimsCleanSuccess is Python's `_claims_clean_success` (:436).
func claimsCleanSuccess(text string) bool {
	return successClaimRE.MatchString(text) && !failureAckRE.MatchString(text)
}

// CheckExecutionClaim is Python's `check_execution_claim` (:440).
//
// One contradiction only: the step claims the run SUCCEEDED, every command
// it actually ran FAILED, and the result never acknowledges a failure. The
// two shapes deliberately NOT flagged — "claims execution but ran nothing"
// and "some succeeded, a later one failed" — are absence and intent
// modelling respectively, and the Python docstring argues both.
//
// `if not tool_events` is truthiness over a list: nil and empty are the
// same branch, and both mean "no tool transcript" rather than "no
// execution in transcript". X1 and X3 are the two fixtures that keep those
// two reasons apart.
//
// The parameter is []any rather than []ToolEvent, and that is not
// looseness. Python's `isinstance(te, dict)` filter lives INSIDE this
// function, after the emptiness check — so a transcript holding only
// non-dict junk is non-empty (reason "no execution in transcript") and an
// empty one is not (reason "no tool transcript"). A []ToolEvent signature
// would force the caller to do the isinstance filter, and a caller that
// did it would turn the first case into the second. Where the filter lives
// is observable; X12 is the fixture that says so.
func CheckExecutionClaim(resultText string, toolEvents []any) (v Verdict) {
	defer func() {
		if r := recover(); r != nil {
			v = Verdict{Reason: "exec check error (fail-open)", Judged: false,
				Claims: []string{}, Missing: []string{}}
		}
	}()

	if len(toolEvents) == 0 {
		out := newVerdict()
		out.Reason = "no tool transcript"
		return out
	}
	var execEvents []ToolEvent
	for _, raw := range toolEvents {
		te, ok := raw.(ToolEvent)
		if !ok {
			// `isinstance(te, dict)` — anything else is skipped here, not
			// earlier, which is what keeps the two empty-ish reasons apart.
			continue
		}
		// `te.get("name") in _EXECUTION_TOOLS`: a non-string name is not
		// in a frozenset of strings, and answers False rather than raising.
		name, _ := te["name"].(string)
		if executionTools[name] {
			execEvents = append(execEvents, te)
		}
	}
	if len(execEvents) == 0 {
		out := newVerdict()
		out.Reason = "no execution in transcript"
		return out
	}
	var failed, succeeded []ToolEvent
	for _, te := range execEvents {
		if toolFailed(te) {
			failed = append(failed, te)
		} else {
			succeeded = append(succeeded, te)
		}
	}
	if len(failed) > 0 && len(succeeded) == 0 && claimsCleanSuccess(resultText) {
		cmds := make([]string, 0, len(failed))
		for _, te := range failed {
			// `(te.get("input") or {}).get("command", te.get("name","?"))`
			// — the `or {}` is truthiness, so an input that is null, or {},
			// or any other falsy value takes the default branch exactly as
			// a missing one does. The default is the event's NAME, and
			// "?" only when the name key is ABSENT.
			var val any
			var found bool
			if in, ok := te["input"].(map[string]any); ok && pyval.Truthy(in) {
				val, found = in["command"]
			}
			if !found {
				if n, ok := te["name"]; ok {
					val = n
				} else {
					val = "?"
				}
			}
			cmds = append(cmds, pyval.Clip(pyval.Str(val), 80))
		}
		out := newVerdict()
		out.Fabricated = true
		out.ChangedCount = len(execEvents)
		out.Reason = "step claimed success but all " + itoa(len(failed)) +
			" command(s) it ran failed (non-zero exit): " + reprList(cmds)
		out.Kind = "execution-contradiction"
		return out
	}
	out := newVerdict()
	out.Reason = "execution claim consistent with transcript"
	return out
}
