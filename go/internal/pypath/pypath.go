package pypath

import (
	"errors"
	"os"
	"os/user"
	"path/filepath"
	"strings"
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

// Realpath is os.path.Realpath(strict=False): every symlink resolved, and
// a DANGLING one resolved to the path it points at rather than refused.
// filepath.EvalSymlinks cannot be used alone for that — it fails on a
// dangling link, which is one of the two cases this is called for.
func Realpath(p string) (string, bool) {
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", false
	}
	dir, base := filepath.Split(abs)
	rd, err := filepath.EvalSymlinks(filepath.Clean(dir))
	if err != nil {
		return "", false
	}
	cur := filepath.Join(rd, base)
	// A SEEN SET, not a hop counter, because that is what CPython does and
	// the two answer differently in both directions. `os.path.realpath` is
	// pure Python walking lstat/readlink itself, so the kernel's ELOOP
	// never enters into it:
	//
	//   - It has NO depth limit. A chain of 60 distinct links resolves to
	//     its end (measured on 3.14.3). This loop stopped at 40 — the old
	//     comment cited MAXSYMLINKS and asserted "so does Python", which
	//     is simply not true of this function — and refused.
	//   - On a CYCLE it does not refuse either. `_joinrealpath` keeps a
	//     seen dict and, with strict=False, returns the path at which the
	//     repeat was detected: a→b→a answers a, and a chain ENTERING a
	//     cycle answers the cycle's first node (start→g→h→i→g answers g).
	//     This returned "unresolvable" for all of them.
	//
	// The refusal is not a harmless conservatism: callers read false as
	// "no such path" and skip work CPython performs.
	seen := map[string]bool{}
	for {
		if seen[cur] {
			// The repeat itself is the answer, exactly as strict=False
			// spells it.
			return cur, true
		}
		seen[cur] = true
		fi, err := os.Lstat(cur)
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			return cur, true
		}
		t, err := os.Readlink(cur)
		if err != nil {
			return "", false
		}
		if !filepath.IsAbs(t) {
			t = filepath.Join(filepath.Dir(cur), t)
		}
		cur = filepath.Clean(t)
	}
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
