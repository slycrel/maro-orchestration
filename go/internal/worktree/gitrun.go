package worktree

import (
	"bytes"
	"context"
	"errors"
	"io/fs"
	"os/exec"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Completed is `subprocess.CompletedProcess` for one git invocation, with
// the two fields worktree.py reads.
//
// Stdout and Stderr are the DECODED, newline-TRANSLATED text `text=True`
// hands back — not the raw bytes. See runGit.
type Completed struct {
	ReturnCode int
	Stdout     string
	Stderr     string
}

// PyError is a Go error standing for one CPython exception, carrying both
// the class (which decides whether a caller's `except` clause catches it)
// and str(exc) (which several callers interpolate straight into a
// MergeResult.Detail an operator reads).
//
// The class is not decoration. worktree.py catches
// `(OSError, subprocess.SubprocessError)` in nine places, and the three
// things that can go wrong here do NOT all fall inside it:
//
//	git binary missing      FileNotFoundError  OSError        caught
//	git not executable      PermissionError    OSError        caught
//	the 120s timeout fires  TimeoutExpired     SubprocessError caught
//	output is not UTF-8     UnicodeDecodeError ValueError     ESCAPES
//
// The last row is the one a straight port loses. Go has no exception, so
// a runGit that returned `error` for all four would let the caller's
// "treat an error as a failed MergeResult" arm swallow a condition
// CPython propagates out of provision() to its caller.
type PyError struct {
	class string
	msg   string
}

func (e *PyError) Error() string { return e.msg }

// PyClass names the CPython exception class this error stands for, the
// way the rest of the port spells it (pyval.ClassOf's contract).
func (e *PyError) PyClass() string { return e.class }

// IsOSOrSubprocessError answers Python's
// `except (OSError, subprocess.SubprocessError)` for one error.
//
// A non-PyError answers FALSE. That is deliberate: the only other errors
// this package produces are its own (a ValueError-shaped path failure, a
// lock failure that already carries its class), and a blanket true here
// would be the swallowing arm the whole type exists to prevent.
func IsOSOrSubprocessError(err error) bool {
	var pe *PyError
	if !errors.As(err, &pe) {
		return false
	}
	switch pe.class {
	case "FileNotFoundError", "PermissionError", "OSError", "FileLockTimeout":
		return true // OSError subclasses (file_lock.FileLockTimeout is one)
	case "TimeoutExpired", "SubprocessError", "CalledProcessError":
		return true
	}
	return false
}

// gitTimeoutS is `_GIT_TIMEOUT_S`. It is an int, and it stays an int
// because TimeoutExpired renders it with %s into a message a caller
// interpolates: `timed out after 120 seconds`, never `120.0`.
const gitTimeoutS = 120

// gitRunner is the shape of `_git` / `_git_hard`, which worktree.py
// passes to _commit_dirty as the `git=` parameter. Keeping it a value
// rather than a bool flag is what keeps `merge_back_clone`'s "all
// clone-side git runs hardened" claim checkable at the call site.
type gitRunner func(args []string, cwd string, timeoutS int) (Completed, error)

// runGit is `_git`: `subprocess.run(["git", "-C", str(cwd), *args],
// capture_output=True, text=True, timeout=timeout)`.
//
// Four things here are the difference between a port and a rewrite.
//
// **cwd is an ARGUMENT to git, not the child's working directory.**
// Python never passes `cwd=` to subprocess.run; it passes `-C <cwd>` to
// git. So a missing directory is not an OSError raised by the fork — it
// is git exiting 128 with `fatal: cannot change to '<dir>': No such file
// or directory` on stderr (measured). Setting cmd.Dir instead would
// convert a returncode the caller inspects into an error it does not
// expect.
//
// **text=True is universal newlines.** The pipes are decoded and then
// `\r\n` and lone `\r` both become `\n` before any caller sees them
// (measured: `printf "a\r\nb\rc\n"` reads back as "a\nb\nc\n"). git
// writes progress with bare `\r` — `git clone` does it on every run — and
// a clone failure's stderr goes straight into a MergeResult.Detail an
// operator reads. Without the translation the two runtimes write two
// different failure lines for one failure.
//
// **text=True is a STRICT decode.** Undecodable output raises
// UnicodeDecodeError, which is a ValueError and is NOT in any of this
// module's except clauses. stdout is decoded before stderr, so when both
// are bad the message names the stdout byte (measured).
//
// **The timeout kills the child.** Python's TimeoutExpired arrives after
// SIGKILL and a wait; exec.CommandContext does the same. The message text
// is reproduced because `_commit_dirty` writes it into a Detail.
func runGit(args []string, cwd string, timeoutS int) (Completed, error) {
	argv := append([]string{"git", "-C", cwd}, args...)
	return subprocessRun(argv, timeoutS)
}

// subprocessRun is the `subprocess.run` BOUNDARY, and it is a variable
// for the same reason Python's is patchable: it is the one seam a
// differential can hold still.
//
// The seam is here rather than at `_git` because Python's `_commit_dirty`
// takes `git=_git` as a DEFAULT ARGUMENT, which binds the original
// function object at def time — so replacing `worktree._git` does not
// reach the calls `_commit_leftovers` makes. Both runtimes' harnesses
// intercept the same lower boundary, which is also the only one where
// the full argv (including the literal "git" and "-C") is visible.
var subprocessRun = execRun

func execRun(argv []string, timeoutS int) (Completed, error) {
	ctx, cancel := context.WithTimeout(context.Background(),
		time.Duration(timeoutS)*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	var so, se bytes.Buffer
	cmd.Stdout, cmd.Stderr = &so, &se
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return Completed{}, &PyError{
			class: "TimeoutExpired",
			msg: "Command '" + pyval.ReprStrings(argv) + "' timed out after " +
				strconv.Itoa(timeoutS) + " seconds",
		}
	}
	if err != nil {
		var ee *exec.ExitError
		if !errors.As(err, &ee) {
			return Completed{}, spawnError(argv[0], err)
		}
	}
	// stdout first: CPython's _communicate translates stdout and then
	// stderr, so when both carry undecodable bytes the exception names
	// the stdout one.
	out, derr := decodeText(so.Bytes())
	if derr != nil {
		return Completed{}, derr
	}
	errText, derr := decodeText(se.Bytes())
	if derr != nil {
		return Completed{}, derr
	}
	return Completed{ReturnCode: cmd.ProcessState.ExitCode(), Stdout: out, Stderr: errText}, nil
}

// runGitHard is `_git_hard`: `_git` with hooks and fsmonitor disabled for
// HOST-side git run against a worker-controlled scratch clone.
//
// The argv ORDER is part of the port. Python builds
// `["git", "-C", cwd, "-c", "core.hooksPath=/dev/null", "-c",
// "core.fsmonitor=", *args]` — `-C` first, because the `-c` pair is
// prepended to `args`, not to the whole command line. git accepts either
// order; a fixture that pins argv does not.
func runGitHard(args []string, cwd string, timeoutS int) (Completed, error) {
	hard := append([]string{"-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor="}, args...)
	return runGit(hard, cwd, timeoutS)
}

// spawnError maps a failure to START the child onto the OSError subclass
// CPython raises, including str(exc), which reaches an operator through
// `detail=f"autocommit error: {exc}"`.
//
// CPython names the FILENAME it was given — 'git' for a PATH lookup,
// the path itself for an absolute argv[0] (both measured) — which is
// exactly `name` here, never the resolved path.
//
// The two failures arrive as two DIFFERENT Go types and only one of them
// is an *exec.Error: a PATH lookup that finds nothing is
// `&exec.Error{Err: exec.ErrNotFound}`, but a named file that exists and
// cannot be executed fails inside the fork as
// `&fs.PathError{Op: "fork/exec", Err: EACCES}`. The first draft here
// tested `errors.As(err, &ee)` before anything else and answered OSError
// with Go's own "fork/exec ...: permission denied" text for the second —
// caught by TestTextModeSubprocessBoundaryMatchesCPython's noexec row,
// which is the only fixture in the package that reaches a real fork
// failure. Matching on the SENTINEL rather than the wrapper type covers
// both shapes.
func spawnError(name string, err error) error {
	switch {
	case errors.Is(err, exec.ErrNotFound), errors.Is(err, fs.ErrNotExist):
		return &PyError{
			class: "FileNotFoundError",
			msg:   "[Errno 2] No such file or directory: " + pytext.Repr(name),
		}
	case errors.Is(err, fs.ErrPermission):
		return &PyError{
			class: "PermissionError",
			msg:   "[Errno 13] Permission denied: " + pytext.Repr(name),
		}
	}
	return &PyError{class: "OSError", msg: err.Error()}
}

// decodeText is the `text=True` decode: strict UTF-8, then universal
// newlines. On failure it returns the UnicodeDecodeError CPython raises,
// message included.
//
// The message is reproduced rather than approximated because the three
// reasons CPython distinguishes are the three a reader would use to tell
// a truncated pipe from a binary blob:
//
//	b"\xff"          byte 0xff in position 0: invalid start byte
//	b"\xc3("         byte 0xc3 in position 0: invalid continuation byte
//	b"abc\xe2\x82"   bytes in position 3-4: unexpected end of data
//
// All three measured against CPython 3.14 on this box; the surrogate and
// out-of-range leads (0xed 0xa0 0x80, 0xf5 ...) fold onto the first two.
func decodeText(b []byte) (string, error) {
	if !utf8.Valid(b) {
		return "", utf8DecodeError(b)
	}
	return pytext.TranslateNewlines(string(b)), nil
}

// utf8DecodeError builds the UnicodeDecodeError CPython raises for the
// FIRST bad sequence in b.
//
// The three-way split is CPython's, not Go's, and Go's own decoder cannot
// make it: `utf8.DecodeRune` answers RuneError for all three.
//
//   - invalid start byte    — the byte cannot lead any sequence
//   - invalid continuation  — a byte inside the sequence is out of range
//   - unexpected end of data — the input ran out with everything valid
//
// The distinction turns on the SECOND byte's range, which is narrower
// than `0b10xxxxxx` for four leads (E0, ED, F0, F4). Using the generic
// mask instead reports `b"\xed\xa0"` as a truncation where CPython
// reports an invalid continuation byte, because A0 is already outside
// ED's range and CPython never gets far enough to run out of input.
func utf8DecodeError(b []byte) *PyError {
	for i := 0; i < len(b); {
		if b[i] < 0x80 {
			i++
			continue
		}
		want, lo, hi := seqShape(b[i])
		if want == 0 {
			return decodeErr(b, i, i+1, "invalid start byte")
		}
		n := 1
		for ; n < want; n++ {
			if i+n >= len(b) {
				// Everything present was well-formed and the input ran
				// out. The reported range is the whole PARTIAL sequence,
				// which is why this one can be longer than a byte.
				return decodeErr(b, i, len(b), "unexpected end of data")
			}
			c := b[i+n]
			ok := c >= 0x80 && c <= 0xBF
			if n == 1 {
				ok = c >= lo && c <= hi
			}
			if !ok {
				return decodeErr(b, i, i+1, "invalid continuation byte")
			}
		}
		i += want
	}
	// utf8.Valid said no and this loop found nothing: unreachable, but a
	// silent "" would be a decoded string CPython never produced.
	return decodeErr(b, 0, len(b), "unexpected end of data")
}

// decodeErr renders UnicodeDecodeError.__str__, whose message has TWO
// forms and picks between them by the WIDTH of the bad range:
//
//	end == start+1   byte 0xc2 in position 0: unexpected end of data
//	end >  start+1   bytes in position 0-1: unexpected end of data
//
// Both reasons can produce either form, so the split does not follow the
// reason. A truncated two-byte sequence is one byte wide and reads
// "byte"; a truncated four-byte sequence with two continuations present
// is three wide and reads "bytes". The first draft here spelled the
// end-of-data arm as always-plural and 55 rows of the sweep caught it.
func decodeErr(b []byte, start, end int, why string) *PyError {
	const prefix = "'utf-8' codec can't decode "
	if end == start+1 {
		return &PyError{class: "UnicodeDecodeError",
			msg: prefix + "byte 0x" + hexByte(b[start]) + " in position " +
				strconv.Itoa(start) + ": " + why}
	}
	return &PyError{class: "UnicodeDecodeError",
		msg: prefix + "bytes in position " + strconv.Itoa(start) + "-" +
			strconv.Itoa(end-1) + ": " + why}
}

// seqShape is the sequence length a UTF-8 lead byte announces plus the
// inclusive range its SECOND byte must fall in, or 0 when the byte cannot
// lead a sequence at all.
//
// 0x80..0xC1 (a stray continuation, or an overlong two-byte lead) and
// 0xF5..0xFF (beyond U+10FFFF) lead nothing, which is why CPython calls
// them "invalid start byte" rather than reporting a truncation. E0, ED,
// F0 and F4 carry narrowed second-byte ranges — they are the overlong and
// surrogate and out-of-range exclusions.
func seqShape(c byte) (want int, lo, hi byte) {
	switch {
	case c >= 0xC2 && c <= 0xDF:
		return 2, 0x80, 0xBF
	case c == 0xE0:
		return 3, 0xA0, 0xBF
	case c >= 0xE1 && c <= 0xEC:
		return 3, 0x80, 0xBF
	case c == 0xED:
		return 3, 0x80, 0x9F
	case c >= 0xEE && c <= 0xEF:
		return 3, 0x80, 0xBF
	case c == 0xF0:
		return 4, 0x90, 0xBF
	case c >= 0xF1 && c <= 0xF3:
		return 4, 0x80, 0xBF
	case c == 0xF4:
		return 4, 0x80, 0x8F
	}
	return 0, 0, 0
}

func hexByte(c byte) string {
	const digits = "0123456789abcdef"
	return string([]byte{digits[c>>4], digits[c&0xF]})
}
