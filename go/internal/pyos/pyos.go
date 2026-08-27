// Package pyos spells an OSError the way CPython spells it.
//
// `str(OSError)` is `[Errno N] strerror: 'path'`, and Go's is `open
// /path: is a directory` — a different shape, a different order, and a
// lowercased message. Anywhere a port logs, records or compares the text
// of a failed file operation, the two disagree.
//
// This existed five times before it existed once: dispatch/envelope.go,
// sheriff/slice2.go, syshealth/syshealth.go, worktree/gitrun.go and
// worktree/fsops.go each hand-wrote a `[Errno 13] Permission denied:` or
// `[Errno 21] Is a directory:` literal for the one or two errnos that
// site happened to hit. That is the shape the 2026-08-27 decree names —
// a lens that fires twice on a surface should be CLOSED, not found
// again — so the sixth site builds the helper instead.
//
// The strerror table is not retyped. Go's linux errno strings come from
// the same glibc catalogue CPython's do, lowercased: ENOENT is "no such
// file or directory" here and "No such file or directory" there. So the
// message is Go's own errno text with its first rune upper-cased, and
// pyos_test.go pins that rule against CPython itself for every errno
// these ports can reach — a hand-copied table would be a second place
// for the strings to be wrong.
package pyos

import (
	"errors"
	"fmt"
	"syscall"
	"unicode"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// ErrorText is `str(exc)` for an OSError raised by a path operation.
//
// An error carrying no errno keeps Go's own text: inventing an errno for
// it would be a fabricated message, and every caller of this helper is
// logging, not branching.
func ErrorText(path string, err error) string {
	var errno syscall.Errno
	if !errors.As(err, &errno) {
		return err.Error()
	}
	return fmt.Sprintf("[Errno %d] %s: %s", int(errno),
		capitalize(errno.Error()), pytext.Repr(path))
}

// ErrorClass is the CPython exception class an errno maps to.
//
// The mapping is CPython's own (PyErr_SetFromErrno's errno-to-subclass
// table); everything outside it is a bare OSError there too, so the
// default is not a fallback for the unknown — it is the answer.
func ErrorClass(err error) string {
	var errno syscall.Errno
	if !errors.As(err, &errno) {
		return "OSError"
	}
	switch errno {
	case syscall.ENOENT:
		return "FileNotFoundError"
	case syscall.EACCES, syscall.EPERM:
		return "PermissionError"
	case syscall.EEXIST:
		return "FileExistsError"
	case syscall.ENOTDIR:
		return "NotADirectoryError"
	case syscall.EISDIR:
		return "IsADirectoryError"
	case syscall.EAGAIN, syscall.EALREADY, syscall.EINPROGRESS:
		return "BlockingIOError"
	case syscall.ECHILD:
		return "ChildProcessError"
	case syscall.EPIPE, syscall.ESHUTDOWN:
		// ESPIPE ("illegal seek") is NOT in this arm, however much it
		// looks like it belongs: CPython maps only EPIPE and ESHUTDOWN,
		// and a seek on a pipe raises a plain OSError. Measured.
		return "BrokenPipeError"
	case syscall.ECONNABORTED:
		return "ConnectionAbortedError"
	case syscall.ECONNREFUSED:
		return "ConnectionRefusedError"
	case syscall.ECONNRESET:
		return "ConnectionResetError"
	case syscall.EINTR:
		return "InterruptedError"
	case syscall.ENOTEMPTY:
		// CPython has no NotEmptyError: ENOTEMPTY is a plain OSError,
		// which is why `rmdir` on a full directory is caught by
		// `except OSError` and not by anything narrower.
		return "OSError"
	}
	return "OSError"
}

// capitalize upper-cases the first rune and leaves the rest alone. It is
// NOT strings.Title and not strings.ToUpper on the first byte: "input/
// output error" must become "Input/output error", with the slash and the
// second word untouched.
func capitalize(s string) string {
	if s == "" {
		return s
	}
	r, size := utf8.DecodeRuneInString(s)
	if r == utf8.RuneError && size <= 1 {
		return s
	}
	return string(unicode.ToUpper(r)) + s[size:]
}
