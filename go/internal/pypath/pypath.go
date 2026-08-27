package pypath

import (
	"errors"
	"os"
	"os/user"
	"strings"
	"unicode/utf8"
)

// Package pypath is CPython's `pathlib.PurePosixPath` where this port leans
// on it, and nothing more. Go's `path/filepath` is close enough to look
// interchangeable and is not:
//
//	              pathlib            filepath
//	  Path("")      "."   name ""      Base("")  "."
//	  Path("/")     "/"   name ""      Base("/") "/"
//	  Path("a/.")   "a"   name "a"     Base("a/.") "."
//	  Path("a/..")  "a/.." name ".."   Base("a/..") ".."
//
// The `a/.` row is the one that bites: an attachment named `a/.` is stored
// as `a` by Python and would be stored as `artifact` by a port that reached
// for filepath.Base. These are small functions with exact twins, so they are
// written out rather than approximated.
//
// It lives in its own package because it acquired a SECOND caller. It was
// written inside `internal/dispatch` for dispatch_envelope.py's attachment
// names; then `internal/tasks` needed Join, because task_path is
// `_tasks_dir() / f"{job_id}.json"` and pathlib's `/` does something
// filepath.Join will not do. Two homes for one runtime's path semantics is
// the defect pyval.Clip's own comment names — two implementations of one
// Python operation disagreeing is a defect even before they disagree.

// Name is PurePosixPath(s).name: the last component, with empty and
// "." components dropped and ".." kept.
func Name(s string) string {
	parts := strings.Split(s, "/")
	for i := len(parts) - 1; i >= 0; i-- {
		if parts[i] == "" || parts[i] == "." {
			continue
		}
		return parts[i]
	}
	return ""
}

// Str is str(PurePosixPath(s)): components joined by single slashes,
// "." components dropped, ".." kept, and an empty path spelled ".".
//
// The double-slash case is POSIX, not a quirk: a path beginning with
// exactly two slashes is implementation-defined and pathlib preserves it,
// while three or more collapse to one.
func Str(s string) string {
	root := ""
	switch {
	case strings.HasPrefix(s, "///"):
		root = "/"
	case strings.HasPrefix(s, "//"):
		root = "//"
	case strings.HasPrefix(s, "/"):
		root = "/"
	}
	var parts []string
	for _, p := range strings.Split(s, "/") {
		if p == "" || p == "." {
			continue
		}
		parts = append(parts, p)
	}
	joined := strings.Join(parts, "/")
	if root != "" {
		return root + joined
	}
	if joined == "" {
		return "."
	}
	return joined
}

// Suffix is PurePosixPath(name).suffix on THIS interpreter — CPython
// 3.14, which is what runs on this box:
//
//	name.lstrip('.'); i = rfind('.'); return name[i:] if i != -1 else ''
//
// The version matters and is the reason it is spelled out. CPython <= 3.13
// used `if 0 < i < len(name) - 1`, which answers differently for two shapes
// this code can reach: `f.` (suffix "." here, "" there) and `..f` (suffix ""
// here, ".f" there). A port matching the older rule would disambiguate a
// collision under a different name than the Python beside it.
func Suffix(name string) string {
	trimmed := strings.TrimLeft(name, ".")
	i := strings.LastIndex(trimmed, ".")
	if i < 0 {
		return ""
	}
	return trimmed[i:]
}

// Stem is PurePosixPath(name).stem: the name with its suffix removed.
func Stem(name string) string {
	return name[:len(name)-len(Suffix(name))]
}

// ErrNoHome is `Path.expanduser`'s RuntimeError("Could not determine home
// directory."). It is deliberately NOT an *Error: Python raises RuntimeError
// here, so a caller catching EnvelopeError does not catch this, and the CLI
// that prints an actionable line for a bad attachment prints a traceback for
// a `~nosuchuser` one. Same shape on this side — an *Error would quietly
// widen the lane's own refusal vocabulary to cover something it does not.
var ErrNoHome = errors.New("Could not determine home directory.")

// ExpandUser is Path.expanduser(): a leading `~` or `~user`, and nothing
// else. A path that does not start with `~` comes back untouched. A `~user`
// naming somebody this host has never heard of RAISES rather than passing
// through — `Path("~nosuchuser/x").expanduser()` is a RuntimeError, and so
// is `Path("~~")`.
//
// Callers pass a path that has already been through Str, because Python
// writes `Path(str(raw)).expanduser()` — the normalisation happens FIRST, so
// `~//a` is `~/a` by the time the tilde is looked at.
func ExpandUser(p string) (string, error) {
	if !strings.HasPrefix(p, "~") {
		return p, nil
	}
	rest := p[1:]
	name := rest
	if i := strings.Index(rest, "/"); i >= 0 {
		name, rest = rest[:i], rest[i:]
	} else {
		rest = ""
	}
	var home string
	if name == "" {
		// Python reads HOME first and only falls back to the password
		// database when it is unset, so a test that repoints HOME is
		// honoured — which is exactly how this port's tests run.
		home = os.Getenv("HOME")
		if home == "" {
			u, err := user.Current()
			if err != nil {
				return "", ErrNoHome
			}
			home = u.HomeDir
		}
	} else {
		u, err := user.Lookup(name)
		if err != nil {
			return "", ErrNoHome
		}
		home = u.HomeDir
	}
	// pathlib drops a trailing slash from the home it substitutes, and the
	// join below must not produce a doubled one.
	home = strings.TrimRight(home, "/")
	if home == "" {
		home = "/"
	}
	if rest == "" {
		return home, nil
	}
	return Str(home + rest), nil
}

// Realpath is os.path.realpath(strict=False) — the function
// `Path.resolve()` delegates to (measured: the two agree on all 25 rows of
// the differential table, 3.14.3).
//
// This is a TRANSLATION of CPython 3.14's posixpath.realpath, not an
// approximation of it, because two rounds of approximation were wrong in
// three different ways. What the previous version did was: filepath.Abs,
// then EvalSymlinks on the parent, then walk the final component's links.
// The differential that covered it asked only about single-component
// paths under an existing directory — `<dir>/<name>` — and under that
// shape all three defects are invisible:
//
//   - A MISSING INTERMEDIATE component made it REFUSE. EvalSymlinks fails
//     outright on a path that is not there, so `<ws>/missing/a/b` came
//     back "unresolvable" where CPython answers the path itself. Every
//     caller reads false as "no such path" and skips work CPython does —
//     and an operator's MARO_WORKSPACE pointing at a tree that does not
//     exist YET is the ordinary case, not the exotic one.
//   - `..` was collapsed LEXICALLY, by filepath.Abs (which Cleans) before
//     any link was followed. CPython pops the ALREADY-RESOLVED path
//     instead, so a link pointing deeper than itself answers its target's
//     parent: with deeplink -> <r>/real/deep, `deeplink/..` is <r>/real,
//     not <r>. Two engines sharing a workspace reached through a symlink
//     would have disagreed about which directory that is.
//   - Only the FINAL component's links were walked, so a symlink in the
//     middle of a longer path was followed by EvalSymlinks (fine) but a
//     dangling one in the middle was not (not fine).
//
// The algorithm below is CPython's own, spelled the same way: a stack of
// unresolved parts, a `path` that is absolute throughout, and a `seen`
// map with THREE states — absent, present-but-unresolved (a loop), and
// present-with-a-value (a cache). The marker entries pushed onto the
// stack are how CPython records a resolved target, and they are kept
// rather than restructured because the loop case reads that map.
//
// It returns false only when the process has no working directory to
// resolve a relative path against, which is where CPython raises.
func Realpath(p string) (string, bool) {
	const sep = "/"

	// "The resolved path, which is absolute throughout this function."
	// CPython seeds it from getcwd() for a relative input; Go's os.Getwd
	// goes to the kernel, which — like CPython's comment says of its own —
	// returns a normalised, symlink-free path.
	var path string
	if strings.HasPrefix(p, sep) {
		path = sep
	} else {
		wd, err := os.Getwd()
		if err != nil {
			return "", false
		}
		path = wd
	}

	parts := strings.Split(p, sep)
	rest := make([]rpPart, 0, len(parts)+16)
	for i := len(parts) - 1; i >= 0; i-- {
		rest = append(rest, rpPart{name: parts[i]})
	}
	// CPython's `part_count`: the number of REAL parts still on the stack,
	// which stops matching len(rest) as soon as a marker pair is pushed.
	//
	// Honest note, because the mutation battery asked: spelling the loop
	// `len(rest) > 0` instead is EQUIVALENT for the answer. The marker arm
	// only writes to `seen`, never to `path`, and when partCount hits zero
	// everything left on the stack is marker pairs — so the difference is
	// a little wasted work after the result is already final, not a
	// different result. The count is kept because it is CPython's, and
	// because `seen` becoming a cache with a lifetime would make the
	// difference real.
	partCount := len(parts)

	seen := map[string]*string{}

	for partCount > 0 {
		it := rest[len(rest)-1]
		rest = rest[:len(rest)-1]
		if it.marker {
			// The entry below a marker is the symlink whose target has
			// just been fully resolved; `path` is that resolution.
			sym := rest[len(rest)-1]
			rest = rest[:len(rest)-1]
			resolved := path
			seen[sym.name] = &resolved
			continue
		}
		name := it.name
		partCount--
		if name == "" || name == "." {
			continue
		}
		if name == ".." {
			// CPython: `path = path[:path.rindex(sep)] or sep`. A pure
			// STRING pop of the resolved path — no lstat, and nothing
			// lexical about the ORIGINAL path. path always starts with a
			// separator, so the index is always found.
			if i := strings.LastIndex(path, sep); i > 0 {
				path = path[:i]
			} else {
				path = sep
			}
			continue
		}
		var newpath string
		if path == sep {
			newpath = sep + name
		} else {
			newpath = path + sep + name
		}
		fi, err := os.Lstat(newpath)
		if err != nil {
			// strict=False sets ignored_error = OSError, and the ignored
			// branch ends at `path = newpath`. THIS is the arm that lets
			// a missing component keep resolving.
			path = newpath
			continue
		}
		if fi.Mode()&os.ModeSymlink == 0 {
			path = newpath
			continue
		}
		if cached, ok := seen[newpath]; ok {
			if cached != nil {
				path = *cached
				continue
			}
			// Seen but unresolved: a symlink loop. strict=False answers
			// the path at which the repeat was detected rather than
			// raising ELOOP.
			path = newpath
			continue
		}
		target, rerr := os.Readlink(newpath)
		if rerr != nil {
			path = newpath
			continue
		}
		if strings.HasPrefix(target, sep) {
			// "Symlink target is absolute; reset resolved path."
			path = sep
		}
		seen[newpath] = nil
		rest = append(rest, rpPart{name: newpath})
		rest = append(rest, rpPart{marker: true})
		tp := strings.Split(target, sep)
		for i := len(tp) - 1; i >= 0; i-- {
			rest = append(rest, rpPart{name: tp[i]})
		}
		partCount += len(tp)
	}
	return path, true
}

// rpPart is one entry on Realpath's stack. CPython pushes a bare None as
// the marker; Go has no such value in a []string, so the flag is carried
// beside the name.
type rpPart struct {
	name   string
	marker bool
}

// Join is pathlib's `/`, and the half of it that differs from
// filepath.Join is the half that matters: an ABSOLUTE right-hand side
// REPLACES the left one instead of being appended to it.
//
//	PurePosixPath("/ws/tasks") / "plain"       -> /ws/tasks/plain
//	PurePosixPath("/ws/tasks") / "/etc/passwd" -> /etc/passwd
//	PurePosixPath("/ws/tasks") / "//x/y"       -> //x/y
//	PurePosixPath("/ws/tasks") / "../../x"     -> /ws/tasks/../../x
//
// filepath.Join answers /ws/tasks/etc/passwd to the second and CLEANS the
// fourth to /ws/x. The first difference decides whether a job id written by
// a foreign producer can name a file outside the workspace — CPython lets
// it, so a port that refuses is a port that disagrees about which rows the
// queue may run. The fourth is lexical only: both spellings open the same
// file, and the cleaning is what makes them look different.
//
// POSIX keeps exactly two leading slashes and collapses three or more,
// which is why "//x/y" survives as itself.
func Join(base, rhs string) string {
	if strings.HasPrefix(rhs, "/") || base == "" {
		return Str(rhs)
	}
	if rhs == "" {
		// PurePosixPath("/a") / "" is a ValueError in CPython, but every
		// caller here builds the right-hand side from a format string, so
		// the empty case only arises as `f"{''}.json"` = ".json" — a
		// non-empty component. Answered rather than spelled as an error
		// nothing can reach.
		return Str(base)
	}
	// Str, not concatenation: pathlib PARSES the result, so a "." component
	// disappears ("a/./b" is "a/b") while ".." survives ("a/../b" stays).
	// Delegating is also what keeps the two rules in one place — the first
	// cut of this function concatenated, and the `./x.json` fixture caught
	// it immediately.
	//
	// The base is normalized FIRST so the separator is not inserted twice
	// at a root. "/" + "/" + "x" reads as the POSIX double-slash root and
	// answers "//x"; "//" + "/" + "x" reads as three slashes, which POSIX
	// collapses, and answers "/x". Both are one character away from right
	// and neither is caught by any non-root fixture.
	b := Str(base)
	if strings.HasSuffix(b, "/") {
		return Str(b + rhs)
	}
	return Str(b + "/" + rhs)
}

// FSDecode is `os.fsdecode` on Linux: UTF-8 with the SURROGATEESCAPE error
// handler, which is how every filename reaches Python. A byte that cannot
// be decoded becomes the lone surrogate U+DC00+byte, and Python then sorts,
// compares and reprs those code points like any others.
//
// Go has no such step — `os.ReadDir` hands back the raw bytes — so the two
// runtimes hold DIFFERENT VALUES for the same filename, and every operation
// that orders or renders one is a place they can part.
//
// One surrogate PER BYTE, not per maximal subpart. That is the opposite of
// `errors="replace"`, which emits one U+FFFD per subpart, and the two rules
// live about two hundred lines apart in the artifactcheck port. Measured:
//
//	b"a\xe2\x82b".decode("utf-8", "surrogateescape") == 'a\udce2\udc82b'
//	b"a\xe2\x82b".decode("utf-8", "replace")         == 'a�b'
//
// Go's utf8.DecodeRuneInString returns (RuneError, 1) for every ill-formed
// byte — including a valid encoding of a surrogate and an overlong form,
// both of which Python also escapes byte by byte — so the one-byte step is
// exactly right here and would be wrong for the replace rule.
func FSDecode(s string) []rune {
	out := make([]rune, 0, len(s))
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		if r == utf8.RuneError && size == 1 {
			out = append(out, rune(0xDC00)+rune(s[i]))
			i++
			continue
		}
		out = append(out, r)
		i += size
	}
	return out
}

// FSLess orders two filenames the way Python's `sorted()` orders them —
// by CODE POINT over the surrogateescape decoding — rather than the way
// Go's sort.Strings does, by raw byte.
//
// They agree for all valid UTF-8, and for undecodable bytes against ASCII
// or astral characters, which is why a casual probe finds nothing. They
// part in the two-byte range, where Python compares 0xDC00+b against a code
// point below 2048 and Go compares b against that character's first byte:
//
//	names          b"\x80bad", "école", b"zulu"
//	Python sorted  'zulu', 'école', '\udc80bad'
//	sort.Strings   "zulu", "\x80bad", "école"
//
// The trailing length comparison is the PREFIX rule and it is load-bearing:
// "zz.tx" sorts before "zz.txt". Returning false there passed every
// end-to-end fixture in artifactcheck (r3 battery, G4c) because the walk
// hands the names over in map order and a tie leaves them wherever they
// were. TestFSLessOrdersNamesTheWayCPythonSortsThem drives the whole pair
// matrix against the interpreter, which is what pins this line.
func FSLess(a, b string) bool {
	ra, rb := FSDecode(a), FSDecode(b)
	for i := 0; i < len(ra) && i < len(rb); i++ {
		if ra[i] != rb[i] {
			return ra[i] < rb[i]
		}
	}
	return len(ra) < len(rb)
}

// EntryIsDir answers CPython's `Path.is_dir()` / `os.scandir` entry
// `is_dir()` for one `os.ReadDir` entry: it FOLLOWS a symlink, and it is
// False — not an error — when the stat fails.
//
// Go's `os.DirEntry.IsDir()` reads the entry's OWN type bits, which is
// `Path.is_symlink()`-shaped, not `is_dir()`-shaped. The two questions
// differ on exactly one input and they differ in BOTH directions
// depending on which Python call is being ported:
//
//	CPython call                 a symlink to a directory
//	root.iterdir() + is_dir()    kept   — Path.is_dir() follows
//	os.walk(followlinks=False)   put in dirnames, so emitted NOWHERE
//	Go DirEntry.IsDir()          False  — always
//
// So a port that transcribes `is_dir()` as `IsDir()` silently DROPS a
// symlinked project/mission/run from an `iterdir` listing (measured
// 2026-08-26, r10: `projects/linked -> real` carrying a NEXT.md was
// listed by `list_projects` and missing from `orch.ListProjects`), and
// the same spelling in an `os.walk` port INVENTS a file that CPython
// never names (measured 2026-08-26, r9, in closure's inventory). One
// helper, two call shapes — the caller still has to know which Python
// call it is reproducing.
//
// The "False on stat error" half is load-bearing: a DANGLING symlink is
// not a directory to CPython either, and a helper that propagated the
// error would have to invent a third answer where the Python has two.
func EntryIsDir(dir string, e os.DirEntry) bool {
	if e.IsDir() {
		return true
	}
	if e.Type()&os.ModeSymlink == 0 {
		return false
	}
	st, err := os.Stat(Join(dir, e.Name()))
	return err == nil && st.IsDir()
}
