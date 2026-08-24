package dispatch

import (
	"errors"
	"os"
	"os/user"
	"path/filepath"
	"strings"
)

// This file is CPython's `pathlib.PurePosixPath` where dispatch_envelope.py
// leans on it, and nothing more. Go's `path/filepath` is close enough to
// look interchangeable and is not:
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

// pathName is PurePosixPath(s).name: the last component, with empty and
// "." components dropped and ".." kept.
func pathName(s string) string {
	parts := strings.Split(s, "/")
	for i := len(parts) - 1; i >= 0; i-- {
		if parts[i] == "" || parts[i] == "." {
			continue
		}
		return parts[i]
	}
	return ""
}

// pathStr is str(PurePosixPath(s)): components joined by single slashes,
// "." components dropped, ".." kept, and an empty path spelled ".".
//
// The double-slash case is POSIX, not a quirk: a path beginning with
// exactly two slashes is implementation-defined and pathlib preserves it,
// while three or more collapse to one.
func pathStr(s string) string {
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

// pathSuffix is PurePosixPath(name).suffix on THIS interpreter — CPython
// 3.14, which is what runs on this box:
//
//	name.lstrip('.'); i = rfind('.'); return name[i:] if i != -1 else ''
//
// The version matters and is the reason it is spelled out. CPython <= 3.13
// used `if 0 < i < len(name) - 1`, which answers differently for two shapes
// this code can reach: `f.` (suffix "." here, "" there) and `..f` (suffix ""
// here, ".f" there). A port matching the older rule would disambiguate a
// collision under a different name than the Python beside it.
func pathSuffix(name string) string {
	trimmed := strings.TrimLeft(name, ".")
	i := strings.LastIndex(trimmed, ".")
	if i < 0 {
		return ""
	}
	return trimmed[i:]
}

// pathStem is PurePosixPath(name).stem: the name with its suffix removed.
func pathStem(name string) string {
	return name[:len(name)-len(pathSuffix(name))]
}

// ErrNoHome is `Path.expanduser`'s RuntimeError("Could not determine home
// directory."). It is deliberately NOT an *Error: Python raises RuntimeError
// here, so a caller catching EnvelopeError does not catch this, and the CLI
// that prints an actionable line for a bad attachment prints a traceback for
// a `~nosuchuser` one. Same shape on this side — an *Error would quietly
// widen the lane's own refusal vocabulary to cover something it does not.
var ErrNoHome = errors.New("Could not determine home directory.")

// expandUser is Path.expanduser(): a leading `~` or `~user`, and nothing
// else. A path that does not start with `~` comes back untouched. A `~user`
// naming somebody this host has never heard of RAISES rather than passing
// through — `Path("~nosuchuser/x").expanduser()` is a RuntimeError, and so
// is `Path("~~")`.
//
// Callers pass a path that has already been through pathStr, because Python
// writes `Path(str(raw)).expanduser()` — the normalisation happens FIRST, so
// `~//a` is `~/a` by the time the tilde is looked at.
func expandUser(p string) (string, error) {
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
	return pathStr(home + rest), nil
}

// realpath is os.path.realpath(strict=False): every symlink resolved, and
// a DANGLING one resolved to the path it points at rather than refused.
// filepath.EvalSymlinks cannot be used alone for that — it fails on a
// dangling link, which is one of the two cases this is called for.
func realpath(p string) (string, bool) {
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
	// 40 is Linux's own MAXSYMLINKS. Past it the kernel answers ELOOP and
	// so does Python; answering "unresolvable" here is the same refusal
	// wearing this function's vocabulary.
	for i := 0; i < 40; i++ {
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
	return "", false
}
