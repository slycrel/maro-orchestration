// Package pathrewrite ports `src/path_rewrite.py` — the transform that
// fixes machine-embedded absolute paths when workspace data changes host.
//
// A workspace is full of paths that name the machine that produced it.
// Copy it to another install and every one of them is a lie: lessons cite
// files that do not exist, run metadata points at nothing. This is the
// module that rewrites them at IMPORT time, over the extracted copy only,
// with every transformed file named in a durable record.
//
// The whole module is one long argument for UNDER-rewriting. A stale
// absolute path is visible and inert; a confidently-wrong one resolves to
// a real local directory that is not the one meant. Every screen here —
// the content-addressed directories, the binary sniff, the root
// validation, the two match boundaries — exists because the other
// direction is silent corruption. The port's job is to keep every one of
// those screens exactly as wide as it is, and the differential's job is
// to prove it.
//
// The one place the port cannot copy the original's spelling is the
// matcher. Python compiles ONE pattern whose left boundary is an
// alternation of LOOKBEHINDS, and Go's regexp has no lookbehind or
// lookahead at all. The scan is therefore written out by hand, and
// `Substitute`'s comment is where that argument lives.
package pathrewrite

import (
	"bytes"
	"errors"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Roles are the roles a source install records about itself, longest-lived
// name first. Adding one here is enough to make it travel; both lanes
// iterate this.
var Roles = []string{"workspace_root", "maro_user_dir", "repo_root"}

const (
	sniffBytes          = 8192
	DefaultMaxFileBytes = 64 * 1024 * 1024
	minRootComponents   = 2
	minDepthBelowShared = 2
	// TmpSuffix is the in-place rewrite's temp-file suffix. It is a
	// constant so the skip screens can recognise a leftover from a killed
	// process: it is never deleted (retention decree), so it must never be
	// rewritten by a later pass either.
	TmpSuffix = ".maro-rewrite.tmp"
)

// skipDirParts are whole directories whose contents are content-addressed
// or otherwise byte-sensitive, matched as a PATH COMPONENT at any depth:
// archived project repos live under runs/ and projects/.
var skipDirParts = map[string]bool{".git": true, ".hg": true, ".svn": true}

// skipSuffixes is the belt to the NUL sniff's braces — fast, and it
// catches the case where a file's first 8 KiB happen to be NUL-free.
var skipSuffixes = map[string]bool{
	// databases (paths live inside pages — never regex these)
	".db": true, ".db-wal": true, ".db-shm": true, ".sqlite": true,
	".sqlite3": true,
	// compressed / archived
	".gz": true, ".tgz": true, ".bz2": true, ".xz": true, ".zst": true,
	".zip": true, ".tar": true, ".7z": true, ".rar": true,
	// images / media / documents
	".png": true, ".jpg": true, ".jpeg": true, ".gif": true, ".bmp": true,
	".ico": true, ".webp": true, ".tiff": true,
	".pdf": true, ".mp3": true, ".mp4": true, ".wav": true, ".mov": true,
	".avi": true, ".webm": true, ".ogg": true,
	// compiled / packed
	".pyc": true, ".pyo": true, ".so": true, ".dylib": true, ".dll": true,
	".exe": true, ".o": true, ".a": true, ".class": true,
	".jar": true, ".whl": true, ".bin": true, ".dat": true,
	".parquet": true, ".npy": true, ".npz": true, ".pkl": true,
	// fonts
	".woff": true, ".woff2": true, ".ttf": true, ".otf": true, ".eot": true,
}

// systemRoots is the exact-match denylist. It is exact-match on purpose,
// and minDepthBelowShared is what covers everything below it: `/usr/lib`
// clears this set while being every bit as generic as `/usr`.
var systemRoots = map[string]bool{
	"/": true, "/usr": true, "/etc": true, "/var": true, "/tmp": true,
	"/opt": true, "/bin": true, "/sbin": true, "/lib": true,
	"/lib64": true, "/boot": true, "/dev": true, "/proc": true,
	"/sys": true, "/run": true, "/mnt": true, "/media": true,
	"/home": true, "/root": true, "/Users": true, "/Applications": true,
	"/System": true, "/Library": true,
	"/private": true, "/private/tmp": true, "/private/var": true,
	"/Volumes": true, "/srv": true,
}

// BadRoot is a recorded root that must never be used as a rewrite source.
//
// It is a ValueError subclass in Python, and `build_map` catches it by
// name — so the CLASS is part of the contract, not just the message.
type BadRoot struct{ Msg string }

func (e *BadRoot) Error() string   { return e.Msg }
func (e *BadRoot) PyClass() string { return "BadRoot" }

// ValidateRoot normalises a recorded root, or returns *BadRoot with the
// reason.
//
// `strict` is the BOUNDARY, not a knob. A SOURCE root arrives inside an
// archive written by another machine and possibly another person, so it
// gets the full screen including the depth-below-a-shared-directory rule.
// A DESTINATION root is this install's own computed path — refusing it
// would only disable the rewrite on a legitimately-configured machine to
// defend against an input we generated ourselves.
//
// value is `any` because Python's first check is `isinstance(value, str)`
// and the caller hands it whatever an archive recorded.
func ValidateRoot(value any, strict bool) (string, error) {
	s, ok := value.(string)
	if !ok {
		return "", &BadRoot{"not a string (" + pyval.TypeName(value) + ")"}
	}
	root := pytext.Strip(s)
	if root == "" {
		return "", &BadRoot{"empty"}
	}
	if strings.ContainsRune(root, 0) {
		return "", &BadRoot{"contains NUL"}
	}
	if !strings.HasPrefix(root, "/") {
		return "", &BadRoot{"not absolute: " + pytext.Repr(root)}
	}
	// Collapse `//` and any trailing slash; purely lexical, because the
	// path names a directory on a machine we do not have.
	root = collapseSlashes(root)
	root = strings.TrimRight(root, "/")
	if root == "" {
		return "", &BadRoot{"resolves to filesystem root"}
	}
	if systemRoots[root] {
		return "", &BadRoot{"system directory: " + root}
	}
	var parts []string
	for _, p := range strings.Split(root, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	for _, p := range parts {
		if p == ".." {
			return "", &BadRoot{"contains a relative segment: " + root}
		}
	}
	if len(parts) < minRootComponents {
		return "", &BadRoot{"too shallow: " + root}
	}
	if strict {
		// EQUIVALENT MUTANTS (kept, marked `equivalent`): both bounds of
		// this loop can be widened without changing an answer. At i=0 the
		// prefix is "/", which IS on the denylist — but the depth test is
		// len(parts)-0, and the minimum-components rule above already
		// guarantees that is at least 2. At i=len(parts) the prefix
		// rebuilds the root exactly, and the root was tested against the
		// same denylist twenty lines up. The loop is spelled as Python
		// spells it.
		//
		// Every PREFIX, not just the first component: the denylist is
		// exact-match, so `/usr/lib` clears it while being as generic as
		// `/usr`. A crafted archive declaring workspace_root `/usr/lib`
		// rewrote every traceback path in the imported data.
		for i := 1; i < len(parts); i++ {
			prefix := "/" + strings.Join(parts[:i], "/")
			if systemRoots[prefix] && (len(parts)-i) < minDepthBelowShared {
				return "", &BadRoot{
					"too shallow under the shared directory " + prefix +
						": " + root}
			}
		}
	}
	return root, nil
}

// collapseSlashes is `re.sub(r"/+", "/", root)`.
//
// It is NOT pypath.Str: POSIX keeps a leading double slash and pathlib
// preserves it, and this substitution does not. The two rules disagree on
// exactly one input, `//srv/ws`, and the one that ships here is the
// regex's.
func collapseSlashes(s string) string {
	var b strings.Builder
	prevSlash := false
	for i := 0; i < len(s); i++ {
		if s[i] == '/' {
			if prevSlash {
				continue
			}
			prevSlash = true
		} else {
			prevSlash = false
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

// Pair is one source→destination root rewrite.
type Pair struct{ Src, Dst string }

// Rejection is one role that could not be mapped, and why. Nothing is
// dropped silently.
type Rejection struct{ Role, Value, Reason string }

// RewriteMap is an ordered, validated set of root rewrites.
type RewriteMap struct {
	// Pairs is ordered longest source first.
	Pairs    []Pair
	Rejected []Rejection
}

// Bool is `__bool__`: a map with no pairs is falsy, and `rewrite_tree`
// returns early on one.
func (m RewriteMap) Bool() bool { return len(m.Pairs) > 0 }

// Describe is `describe()`.
func (m RewriteMap) Describe() []pyval.Obj {
	out := []pyval.Obj{}
	for _, p := range m.Pairs {
		out = append(out, pyval.Obj{{Key: "from", Val: p.Src},
			{Key: "to", Val: p.Dst}})
	}
	return out
}

// Substitute is `substitute()`: (rewritten bytes, replacements).
//
// Python compiles ONE pattern over all sources — one pass, not one pass
// per pair, because sequential passes let a destination that happens to
// contain a later source string be rewritten a second time. That property
// is preserved here by construction: the scan below emits into a separate
// buffer and never re-reads what it has emitted.
//
// What cannot be preserved is the SPELLING. The pattern's left boundary is
//
//	(?:(?<![A-Za-z0-9_./-])|(?<=\n)|(?<=\t)|(?<=\r)|(?<=\f)|(?<=\v)|(?<=\b))
//
// — an alternation of lookbehinds, and Go's regexp has neither lookbehind
// nor lookahead. So the walk is written out: at every byte position, if
// the left boundary holds, try each source IN ORDER (longest first) and
// take the first whose right boundary also holds; otherwise advance one
// BYTE. That last detail is the one a rewrite gets wrong: a failed
// lookbehind makes CPython retry at the next position, not skip the
// candidate's length.
//
// Trying the sources in order and falling through on a failed RIGHT
// boundary is the other half. `re` backtracks into the next alternative
// when the trailing lookahead fails, so a longer source that is blocked by
// its right edge does not prevent a shorter one from matching there.
func (m RewriteMap) Substitute(data []byte) ([]byte, int) {
	// EQUIVALENT MUTANT (kept, marked `equivalent`): removing this early
	// exit changes nothing — with no pairs every position falls through
	// to the one-byte copy below and the function still returns (data,
	// 0). It is here because Python's matcher() returns None and
	// substitute() short-circuits on it, and because copying a file's
	// worth of bytes to prove there was nothing to do is silly.
	if len(m.Pairs) == 0 {
		return data, 0
	}
	out := make([]byte, 0, len(data))
	count := 0
	for i := 0; i < len(data); {
		hit := -1
		if leftBoundaryOK(data, i) {
			for k, p := range m.Pairs {
				if bytes.HasPrefix(data[i:], []byte(p.Src)) &&
					rightBoundaryOK(data, i+len(p.Src)) {
					hit = k
					break
				}
			}
		}
		if hit < 0 {
			out = append(out, data[i])
			i++
			continue
		}
		out = append(out, m.Pairs[hit].Dst...)
		i += len(m.Pairs[hit].Src)
		count++
	}
	return out, count
}

// SubstituteText is `substitute_text()`, for callers holding a str. The
// live-workspace merge lane rewrites ledger LINES before its exact-line
// dedup, so it never touches a file at all.
func (m RewriteMap) SubstituteText(text string) (string, int) {
	out, n := m.Substitute([]byte(text))
	return string(out), n
}

// leftBoundaryOK is the left half of the match boundary.
//
// A match must not be a SUFFIX of a longer path. Without this, every root
// fires mid-string — `/mnt/backup/home/clawd/.maro/workspace` (a backup
// mirror, a different directory) and `notes/Users/jeremy/x` (a relative
// path) were both rewritten into confidently-wrong absolute paths. The
// left side gets no `.`/`/` exemption: nothing legitimate precedes a root
// with one, and under-rewriting is this module's safe direction.
//
// …with one exception that only real data could show. A path inside a
// JSON string is very often preceded by an ESCAPE, and at the byte level
// the character before the root is then the `n` of `\n` — which the plain
// lookbehind reads as an identifier character and blocks. The first live
// box→Mac import left 5,543 occurrences of the primary root unrewritten
// for exactly this reason. So a two-byte escape sequence is a boundary
// too, and the `\` is a LITERAL backslash: the pattern is a bytes pattern
// written `rb"(?<=\\n)"`, which is backslash-then-n, not a newline.
func leftBoundaryOK(data []byte, i int) bool {
	if i == 0 {
		return true
	}
	c := data[i-1]
	if !isLeftBlockingByte(c) {
		return true
	}
	if i >= 2 && data[i-2] == '\\' {
		switch c {
		case 'n', 't', 'r', 'f', 'v', 'b':
			return true
		}
	}
	return false
}

// isLeftBlockingByte is `[A-Za-z0-9_./-]`.
func isLeftBlockingByte(c byte) bool {
	switch {
	case c >= 'A' && c <= 'Z', c >= 'a' && c <= 'z', c >= '0' && c <= '9':
		return true
	}
	return c == '_' || c == '.' || c == '/' || c == '-'
}

// rightBoundaryOK is `(?![A-Za-z0-9_-])`: a match must not be followed by
// a character that would make it a PREFIX of a different name, so
// `/home/clawd/.maro` does not fire inside `/home/clawd/.maro-acceptance-
// probe`. `.` is deliberately ALLOWED so prose like "recorded at <root>."
// rewrites — a trailing-dot sibling resolves nowhere either way, while
// unrewritten prose is exactly the reader-facing lie this module fixes.
func rightBoundaryOK(data []byte, j int) bool {
	if j >= len(data) {
		return true
	}
	c := data[j]
	switch {
	case c >= 'A' && c <= 'Z', c >= 'a' && c <= 'z', c >= '0' && c <= '9':
		return false
	}
	return c != '_' && c != '-'
}

// BuildMap pairs each role's recorded source root with this install's own.
//
// A role is dropped — with a recorded reason, never silently — when the
// source did not record it, when either side fails validation, or when the
// two sides are identical (importing on the machine that exported).
func BuildMap(sourceRoots, destRoots pyval.Obj, roles []string) RewriteMap {
	pairs := []Pair{}
	rejected := []Rejection{}
	for _, role := range roles {
		rawSrc, _ := sourceRoots.Get(role)
		rawDst, _ := destRoots.Get(role)
		// `raw in (None, "")` — an identity/equality test against exactly
		// two values. A recorded 0 or False is NOT skipped here; it
		// reaches validate_root and is rejected as "not a string".
		if isNoneOrEmpty(rawSrc) {
			continue // not recorded — nothing to map
		}
		if isNoneOrEmpty(rawDst) {
			rejected = append(rejected, Rejection{role, pyval.Str(rawSrc),
				"no local counterpart"})
			continue
		}
		src, err := ValidateRoot(rawSrc, true)
		if err != nil {
			rejected = append(rejected, Rejection{role,
				pyval.Clip(pyval.Str(rawSrc), 200), "source " + err.Error()})
			continue
		}
		dst, err := ValidateRoot(rawDst, false)
		if err != nil {
			rejected = append(rejected, Rejection{role,
				pyval.Clip(pyval.Str(rawDst), 200),
				"destination " + err.Error()})
			continue
		}
		if src == dst {
			continue // same machine — a no-op, not a fault
		}
		pairs = append(pairs, Pair{src, dst})
	}

	// Longest source first so a nested root (…/.maro/workspace) is
	// consumed before its parent (…/.maro) can claim the prefix. The
	// length is in CODE POINTS, because Python's len() counts those, and
	// the tie-break is a code-point comparison — which is what a bytewise
	// comparison of UTF-8 already is.
	// EQUIVALENT MUTANT (kept, marked `equivalent`): the comparator is a
	// TOTAL order — length, then the source string — so nothing is ever
	// tied and an unstable sort would agree. Stable, because Python's
	// list.sort is.
	sort.SliceStable(pairs, func(a, b int) bool {
		la, lb := utf8.RuneCountInString(pairs[a].Src),
			utf8.RuneCountInString(pairs[b].Src)
		if la != lb {
			return la > lb
		}
		return pairs[a].Src < pairs[b].Src
	})
	// Two roles can record the same source (a workspace pinned at the
	// repo). Keep the first — the longest, already ordered.
	seen := map[string]bool{}
	deduped := []Pair{}
	for _, p := range pairs {
		if seen[p.Src] {
			continue
		}
		seen[p.Src] = true
		deduped = append(deduped, p)
	}
	return RewriteMap{Pairs: deduped, Rejected: rejected}
}

func isNoneOrEmpty(v any) bool {
	if v == nil {
		return true
	}
	s, ok := v.(string)
	return ok && s == ""
}

// SkipReason says why this file must not be rewritten, or "" if it may be.
// Path-shaped screens only; the content sniff lives in RewriteFile,
// because it needs the bytes.
func SkipReason(relPath, absPath string) string {
	for _, p := range pathParts(relPath) {
		if skipDirParts[p] {
			return "vcs-internal"
		}
	}
	// EQUIVALENT MUTANT (kept, marked `equivalent`): this screen and the
	// suffix screen below can be swapped, because no name can trip both —
	// a leftover ends in ".maro-rewrite.tmp", whose pathlib suffix is
	// ".tmp", and ".tmp" is not in skipSuffixes. That is a property of a
	// CONSTANT, though, so the order stays Python's.
	if strings.HasSuffix(relPath, TmpSuffix) {
		// A leftover from a process killed mid-swap. It is never deleted
		// (retention: it may hold the only copy of a partial write), so a
		// later pass must not treat it as ordinary text and rewrite it.
		return "rewrite-leftover"
	}
	if skipSuffixes[pytext.Lower(pypath.Suffix(pypath.Name(relPath)))] {
		return "binary-suffix"
	}
	st, err := os.Lstat(absPath)
	if err != nil {
		return "missing"
	}
	if st.Mode()&os.ModeSymlink != 0 {
		return "symlink" // never follow: the target may be outside
	}
	// os.path.isfile FOLLOWS links and answers False on any error, which
	// is why it is spelled as a second stat rather than as a mode test on
	// the lstat above.
	if fi, err := os.Stat(absPath); err != nil || !fi.Mode().IsRegular() {
		return "not-a-file"
	}
	if st.Size() == 0 {
		// The LSTAT size, which for a regular file is the file's own.
		return "empty"
	}
	return ""
}

// pathParts is `PurePosixPath(s).parts` for the membership test above:
// components split on `/`, with empty and "." dropped and ".." kept. The
// root component pathlib prepends for an absolute path is not modelled
// because "/" is not in skipDirParts and nothing else reads this.
//
// EQUIVALENT MUTANTS (three, kept and marked `equivalent`): every one of
// those decisions can be flipped without changing an answer, because the
// sole consumer asks one question — is this component .git, .hg or .svn —
// and "", "." and ".." are none of them. The filtering mirrors pathlib
// rather than serving a caller, which is exactly why it is worth saying
// so here: the next consumer may care, and it will not be obvious that
// this function was never tested on the distinction.
func pathParts(s string) []string {
	out := []string{}
	for _, p := range strings.Split(s, "/") {
		if p == "" || p == "." {
			continue
		}
		out = append(out, p)
	}
	return out
}

// RewriteFile applies the map to one file in place, atomically, and
// returns (status, replacements).
//
// status is one of rewritten, unchanged, binary, oversize, unreadable.
// Mode and mtime are preserved: the content is the SOURCE machine's
// content, and reconciliation against the source reads both.
func RewriteFile(absPath string, mapping RewriteMap, maxBytes int64) (string, int) {
	st, err := os.Stat(absPath)
	if err != nil {
		return "unreadable", 0
	}
	if st.Size() > maxBytes {
		return "oversize", 0
	}
	fh, err := os.Open(absPath)
	if err != nil {
		return "unreadable", 0
	}
	head := make([]byte, sniffBytes)
	n, rerr := readFull(fh, head)
	head = head[:n]
	if rerr != nil {
		fh.Close()
		return "unreadable", 0
	}
	if bytes.IndexByte(head, 0) >= 0 {
		fh.Close()
		return "binary", 0 // cheap exit before reading the rest
	}
	rest, rerr := readAll(fh)
	fh.Close()
	if rerr != nil {
		return "unreadable", 0
	}
	data := append(head, rest...)
	// The head sniff is an early exit, NOT the test. Screening on the
	// first 8 KiB alone let a NUL-free-header binary (a checkpoint, a
	// custom serialized format) through and spliced a path into its
	// binary tail. The whole file is already in memory by here, so
	// checking all of it costs nothing and makes the docstring's claim
	// — "catches every unknown-extension binary" — actually true.
	if bytes.IndexByte(data, 0) >= 0 {
		return "binary", 0
	}

	newData, count := mapping.Substitute(data)
	if count == 0 {
		return "unchanged", 0
	}

	// A SECOND stat, after the read: Python re-stats here, and the times
	// it captures are the ones restored below.
	st2, err := os.Stat(absPath)
	if err != nil {
		return "unreadable", 0
	}
	tmp := pypath.Str(absPath) + TmpSuffix
	// os.replace is the commit point and NOTHING that can fail may share
	// its try block. mtime preservation used to sit inside it, so a utime
	// failure AFTER the swap reported ("unreadable", 0) for a file whose
	// bytes had already changed — the report every audit downstream is
	// built on, saying nothing happened when something did.
	if err := writeSwap(tmp, absPath, newData); err != nil {
		// `os.unlink` in a bare `except OSError: pass`, and unlink is the
		// operative word: it removes a FILE. os.Remove would also remove
		// an empty DIRECTORY, and a directory sitting on the temp path is
		// exactly how this branch is reached — CPython leaves it alone
		// and a port that deletes it destroys something it was only
		// trying to clean up after itself.
		_ = syscall.Unlink(tmp)
		return "unreadable", 0
	}
	// Best-effort metadata; the content is already committed. Python
	// restores (atime, mtime) and Go's Chtimes takes the same pair.
	//
	// EQUIVALENT MUTANT (kept, marked `equivalent`): st would do as well
	// as st2 — nothing between the two stats writes to the file. The
	// second stat is here because Python takes one here.
	_ = chtimes(absPath, st2)
	return "rewritten", count
}

// errNoStat is unreachable on every platform this ships to; it exists so
// the type assertion in writeSwap has an error to return rather than a
// panic.
var errNoStat = errors.New("no stat_t")

// writeSwap is the body of the commit try block: write the temp file,
// copy the mode across, then replace. Split out so the recovery path
// above cannot accidentally grow a second failable statement.
func writeSwap(tmp, absPath string, data []byte) error {
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := out.Write(data); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	// shutil.copymode copies the PERMISSION BITS of the original onto the
	// temp file — the new file must not inherit the umask's idea of what
	// an imported file's mode should be. It re-stats at THIS moment
	// rather than reusing an earlier one, and it copies all twelve bits
	// (setuid/setgid/sticky included), which is why this goes through
	// syscall.Chmod rather than os.FileMode's nine-bit Perm().
	fi, err := os.Stat(absPath)
	if err != nil {
		return err
	}
	sys, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return errNoStat
	}
	if err := syscall.Chmod(tmp, sys.Mode&07777); err != nil {
		return err
	}
	return os.Rename(tmp, absPath)
}

// Report is what the pass did — printed in summary, recorded in full.
type Report struct {
	Mapping        []pyval.Obj
	Rejected       []pyval.Obj
	FilesScanned   int
	FilesRewritten int
	Replacements   int
	// Skipped is a counter per reason. Python's dict is insertion-ordered
	// but is only ever read back SORTED (both as_record and summary sort
	// it), so a Go map is faithful for every consumer.
	Skipped map[string]int
	Files   []pyval.Obj // every file touched
	Failed  []string    // every file that ERRORED
}

// skip records one screened file.
func (r *Report) skip(reason, rel string) {
	if r.Skipped == nil {
		r.Skipped = map[string]int{}
	}
	r.Skipped[reason]++
	// The screens (binary, vcs-internal, oversize…) are deliberate and
	// self-explaining, so a count is enough. `unreadable` is not a screen
	// — it is an unexpected I/O failure, and an operator asking "why does
	// this lesson still cite the old machine" needs the filename, which a
	// bare tally cannot give them.
	// EQUIVALENT MUTANT (kept, marked `equivalent`): neither half of this
	// condition can fail at a live call site — the screen site passes ""
	// but never the reason "unreadable", and the status site passes
	// "unreadable" but always with a name. Python carries the same dead
	// guard, and it is carried here for the same reason: the next caller.
	if reason == "unreadable" && rel != "" {
		r.Failed = append(r.Failed, rel)
	}
}

// AsRecord is `as_record()`.
func (r *Report) AsRecord() pyval.Obj {
	skipped := pyval.Obj{}
	for _, k := range sortedKeys(r.Skipped) {
		skipped = append(skipped, pyval.Field{Key: k, Val: r.Skipped[k]})
	}
	return pyval.Obj{
		{Key: "mapping", Val: objList(r.Mapping)},
		{Key: "rejected_roots", Val: objList(r.Rejected)},
		{Key: "files_scanned", Val: r.FilesScanned},
		{Key: "files_rewritten", Val: r.FilesRewritten},
		{Key: "replacements", Val: r.Replacements},
		{Key: "skipped", Val: skipped},
		{Key: "failed", Val: strList(r.Failed)},
		{Key: "files", Val: objList(r.Files)},
	}
}

// Summary is `summary()`.
func (r *Report) Summary() string {
	if len(r.Mapping) == 0 {
		return "path rewrite: nothing to map (no differing roots)"
	}
	head := "path rewrite: " + itoa(r.FilesRewritten) + " file(s), " +
		itoa(r.Replacements) + " occurrence(s)"
	parts := []string{}
	for _, k := range sortedKeys(r.Skipped) {
		parts = append(parts, k+"="+itoa(r.Skipped[k]))
	}
	skips := strings.Join(parts, ", ")
	if skips != "" {
		head += " — not rewritten: " + skips
	}
	if len(r.Failed) > 0 {
		head += "\n  FAILED to rewrite (I/O error, paths left stale): " +
			strings.Join(clipList(r.Failed, 10), ", ")
		if len(r.Failed) > 10 {
			head += " … +" + itoa(len(r.Failed)-10) + " more"
		}
	}
	return head
}

// RewriteTree applies `mapping` to the named files under `root`.
//
// It takes an explicit file list rather than walking: on a MERGE import
// only the files THIS import wrote may be touched, and a walk cannot tell
// those from the ones already there.
func RewriteTree(root string, relNames []string, mapping RewriteMap,
	maxBytes int64) *Report {
	rejected := []pyval.Obj{}
	for _, rj := range mapping.Rejected {
		rejected = append(rejected, pyval.Obj{
			{Key: "role", Val: rj.Role},
			{Key: "value", Val: rj.Value},
			{Key: "reason", Val: rj.Reason},
		})
	}
	report := &Report{Mapping: mapping.Describe(), Rejected: rejected,
		Skipped: map[string]int{}, Files: []pyval.Obj{}, Failed: []string{}}
	if !mapping.Bool() {
		return report
	}

	rootResolved, ok := pypath.Realpath(root)
	if !ok {
		// resolve() on the root is outside the per-file try block in
		// Python, so its failure propagates rather than becoming a skip.
		// Go has no exception to propagate; the only reachable cause is a
		// missing working directory, and the honest answer is a report
		// that touched nothing.
		return report
	}
	for _, rel := range relNames {
		absPath := pypath.Join(root, rel)
		// Containment: the caller's list is derived from archive member
		// names, which are untrusted even after the extractor's screens.
		resolved, rok := pypath.Realpath(absPath)
		if !rok || !isUnder(resolved, rootResolved) {
			report.skip("outside-root", "")
			continue
		}
		reason := SkipReason(rel, absPath)
		if reason != "" {
			report.skip(reason, "")
			continue
		}
		report.FilesScanned++
		status, count := RewriteFile(absPath, mapping, maxBytes)
		if status == "rewritten" {
			report.FilesRewritten++
			report.Replacements += count
			report.Files = append(report.Files, pyval.Obj{
				{Key: "path", Val: rel},
				{Key: "replacements", Val: count},
			})
		} else if status != "unchanged" {
			report.skip(status, rel)
		}
	}
	return report
}

// isUnder is `child.relative_to(parent)` reduced to the question the
// caller asks: does it raise?
//
// It is COMPONENT-WISE, not a string prefix — `/a/bc` is not under `/a/b`
// even though the string says otherwise. A path is under itself, because
// relative_to on equal paths returns "." rather than raising.
func isUnder(child, parent string) bool {
	c, p := absParts(child), absParts(parent)
	if len(c) < len(p) {
		return false
	}
	for i := range p {
		if c[i] != p[i] {
			return false
		}
	}
	return true
}

// absParts splits an absolute path into pathlib's `.parts` minus the "/"
// root element, which both sides always share here.
//
// EQUIVALENT MUTANT (kept, marked `equivalent`): both arguments are
// Realpath output — absolute, no doubled or trailing slashes — so keeping
// the empty components would add exactly one leading "" to each side and
// the compare would be unchanged.
func absParts(s string) []string {
	out := []string{}
	for _, p := range strings.Split(s, "/") {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func sortedKeys(m map[string]int) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func objList(v []pyval.Obj) []any {
	out := make([]any, 0, len(v))
	for _, o := range v {
		out = append(out, o)
	}
	return out
}

func strList(v []string) []any {
	out := make([]any, 0, len(v))
	for _, s := range v {
		out = append(out, s)
	}
	return out
}

func clipList(v []string, n int) []string {
	if len(v) > n {
		return v[:n]
	}
	return v
}

func itoa(n int) string { return strconv.Itoa(n) }

// readFull reads up to len(buf) bytes, the way one Python `fh.read(n)`
// does: short reads are retried, and EOF is not an error.
func readFull(f *os.File, buf []byte) (int, error) {
	n := 0
	for n < len(buf) {
		m, err := f.Read(buf[n:])
		n += m
		if err == io.EOF {
			return n, nil
		}
		if err != nil {
			return n, err
		}
		if m == 0 {
			return n, nil
		}
	}
	return n, nil
}

func readAll(f *os.File) ([]byte, error) { return io.ReadAll(f) }

// chtimes restores (atime, mtime) from a stat. Go's FileInfo exposes only
// mtime portably, so atime comes from the syscall stat underneath — and
// when it cannot, mtime is used for both, which is what a rewritten file's
// atime would be anyway.
func chtimes(path string, fi os.FileInfo) error {
	at := fi.ModTime()
	if sys, ok := fi.Sys().(*syscall.Stat_t); ok {
		at = time.Unix(sys.Atim.Sec, sys.Atim.Nsec)
	}
	return os.Chtimes(path, at, fi.ModTime())
}
