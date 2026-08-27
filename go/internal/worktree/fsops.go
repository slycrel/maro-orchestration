package worktree

import (
	"os"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// parentOf is `PurePath.parent`: the path with its last component
// dropped, PARSED the way pathlib parses it.
//
// The three cases a naive `filepath.Dir` gets wrong are the three at the
// edges — pathlib answers "/" for "/a", "." for "a", and "/" for "/" —
// and filepath.Dir happens to agree on all three, which is why this
// exists as a name rather than as a call: what it must NOT do is
// filepath.Dir's Clean of the whole path, because the caller's argument
// was already built by pypath.Join and re-cleaning it would be a second,
// different normalisation. Str is pathlib's own parse.
func parentOf(p string) string {
	s := pypath.Str(p)
	if s == "/" || s == "." {
		return s
	}
	i := strings.LastIndexByte(s, '/')
	switch {
	case i < 0:
		return "."
	case i == 0:
		return "/"
	}
	return pypath.Str(s[:i])
}

// makeDirs is `Path.mkdir(parents=True, exist_ok=True)`.
//
// The mode is 0o777 before umask, which is what pathlib passes and what
// record.NewDirMode already spells for the rest of the port. An existing
// path that is a FILE is an error in both runtimes (FileExistsError /
// ENOTDIR), and both call sites treat that as the provisioning failure it
// is rather than continuing.
func makeDirs(dir string) error {
	return os.MkdirAll(dir, record.NewDirMode)
}

// removeTree is `shutil.rmtree(path, ignore_errors=True)`: recursive,
// and EVERY error is discarded including "it was never there".
//
// os.RemoveAll is not quite the same function — it stops at the first
// error it cannot ignore, where rmtree with ignore_errors keeps walking —
// but the difference is only observable for a partially-unremovable tree,
// where both leave the tree behind and neither reports anything. Named
// rather than silently equated.
func removeTree(path string) {
	_ = os.RemoveAll(path)
}

// removeFile is `Path.unlink(missing_ok=True)`.
//
// missing_ok swallows FileNotFoundError and NOTHING else. A permission
// failure or an EISDIR still raises — out of cleanup_clone, past its try
// (which has already closed above this line) and out of the sweep. So
// this REPORTS, and the caller propagates.
//
// The directory arm is not defensive padding, it is a MEASURED
// divergence that the obvious spelling gets wrong. `Path.unlink` calls
// os.unlink and nothing else, so a path that is a directory raises
// `IsADirectoryError: [Errno 21] Is a directory: '<p>'` whether the
// directory is empty or not. Go's `os.Remove` tries unlink AND THEN
// rmdir, so for an EMPTY directory it silently SUCCEEDS — measured on
// this box, both sides. That is the wrong direction twice over: the port
// deletes a directory CPython refuses to touch, and cleanup_clone then
// carries on where CPython's sweep would have stopped. The Lstat is a
// tiny TOCTOU window against a caller that raced us to swap the path for
// a directory; every non-racing input matches CPython.
func removeFile(path string) error {
	if st, err := os.Lstat(path); err == nil && st.IsDir() {
		return &PyError{class: "IsADirectoryError",
			msg: "[Errno 21] Is a directory: " + pytext.Repr(path)}
	}
	err := os.Remove(path)
	switch {
	case err == nil, os.IsNotExist(err):
		return nil // missing_ok=True
	case os.IsPermission(err):
		return &PyError{class: "PermissionError",
			msg: "[Errno 13] Permission denied: " + pytext.Repr(path)}
	}
	// Any other errno keeps Go's own text. The class is the part callers
	// branch on and OSError is the honest one; the message is NOT claimed
	// to match CPython here and no fixture asserts that it does.
	return &PyError{class: "OSError", msg: err.Error()}
}
