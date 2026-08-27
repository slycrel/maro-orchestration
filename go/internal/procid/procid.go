// Package procid ports src/process_identity.py — the portable
// process-birth identity that PID-owned ephemeral resources key on.
//
// It exists as its own package for the same reason its Python does: the
// question "is the process that recorded this sidecar still the process
// running under that PID" is asked by more than one owner (the scratch
// clone sweep here, `orch`'s DOING-pid sidecar in a narrower spelling),
// and a hand-ported copy per caller is the family this port has already
// been bitten by (PORT.md, "a hand-ported helper is a family, not a
// line").
//
// Only what worktree.py reaches is ported: StartToken, PIDAlive and
// OwnerIsCurrent. The Darwin arm is a NAMED divergence — see StartToken.
package procid

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Digest is `_digest`: the first 32 hex characters of the SHA-256 of the
// UTF-8 encoding of raw.
//
// The 32 is a slice over the HEX string, not over the digest bytes, so it
// is 16 bytes' worth. Spelling it as `[:16]` over the raw digest would
// produce the same string here and a different one the day someone
// changes the hash.
func Digest(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])[:32]
}

// StartToken is `process_start_token(pid)`: a stable token for ONE PID
// incarnation, or ("", false) where the platform cannot answer — which is
// Python's None.
//
// NAMED DIVERGENCE (darwin). Python reads the kernel's microsecond
// process start time through libproc and digests
// `darwin:{sec}:{usec}`. Go cannot reach proc_pidinfo without cgo, and
// the two available alternatives are not symmetric:
//
//   - Return no token (this port's choice). owner_is_current then reads
//     "platform cannot prove reuse" and RETAINS on ambiguity, so a live
//     owner is never mistaken for a dead one. The cost is that PID reuse
//     goes undetected on macOS.
//   - Fall back to the `ps` arm. That produces a `ps:`-prefixed digest
//     which can never equal the `darwin:`-prefixed token a CPython
//     process wrote into the same sidecar, so EVERY cross-runtime read
//     would answer "not current" — the sweep would treat a live owner as
//     dead, merge its clone back mid-flight and delete it.
//
// The second is the unsafe direction, so the first is what ships. It is a
// divergence and it is written down rather than hidden.
func StartToken(pid int) (string, bool) {
	switch runtime.GOOS {
	case "linux":
		return linuxStartToken(pid)
	case "darwin":
		return "", false
	}
	return psStartToken(pid)
}

// linuxStartToken is the `sys.platform.startswith("linux")` arm.
//
// Every failure inside it answers None, and Python's comment says why:
// "Never cross token namespaces for a live Linux process. A transient
// /proc failure is ambiguity, not proof of PID reuse." The except clause
// is `(OSError, ValueError, IndexError)` — and UnicodeDecodeError is a
// ValueError, so a `comm` field carrying undecodable bytes is caught
// here rather than escaping. Reproduced: the read is STRICT UTF-8.
func linuxStartToken(pid int) (string, bool) {
	raw, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return "", false
	}
	// Path.read_text(encoding="utf-8") is strict, and it is universal
	// newlines. Neither can matter for the FIELD this reads (it lives
	// after the last ")" and is digits), but the decode CAN: an
	// undecodable comm raises before any parsing happens.
	if !utf8.Valid(raw) {
		return "", false
	}
	stat := pytext.TranslateNewlines(string(raw))
	// `stat.rsplit(")", 1)[1]` — the text after the LAST ")". With no ")"
	// at all, rsplit returns a one-element list and [1] is an IndexError,
	// which the except swallows.
	idx := bytes.LastIndexByte([]byte(stat), ')')
	if idx < 0 {
		return "", false
	}
	fields := pytext.Split(pytext.Strip(stat[idx+1:]))
	if len(fields) < 20 {
		return "", false // IndexError on fields[19]
	}
	startTicks := fields[19] // proc(5) field 22; the tail begins at field 3
	bootRaw, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return "", false
	}
	if !utf8.Valid(bootRaw) {
		return "", false
	}
	bootID := pytext.Strip(pytext.TranslateNewlines(string(bootRaw)))
	if bootID == "" {
		return "", false
	}
	return Digest("linux:" + bootID + ":" + startTicks), true
}

// psStartToken is the last arm: `ps -o lstart= -p <pid>` under a
// stabilised environment, two-second timeout, digest of `ps:{started}`.
//
// The environment override is the point of the arm — lstart renders a
// LOCALE- and TIMEZONE-dependent date, so without TZ/LC_ALL/LANG pinned
// the same process yields two different tokens to two readers.
func psStartToken(pid int) (string, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "ps", "-o", "lstart=", "-p", strconv.Itoa(pid))
	// os.environ.copy() then update — the child keeps everything else.
	cmd.Env = append(os.Environ(), "TZ=UTC", "LC_ALL=C", "LANG=C")
	var so, se bytes.Buffer
	cmd.Stdout, cmd.Stderr = &so, &se
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return "", false // TimeoutExpired -> the except -> None
	}
	// `(proc.stdout or "").strip()` runs BEFORE the returncode test, and
	// text=True decoding is what would raise here; an undecodable lstart
	// is a UnicodeDecodeError, which this except clause does NOT list
	// (it catches OSError, ValueError, TimeoutExpired — and
	// UnicodeDecodeError IS a ValueError, so it is caught after all).
	if !utf8.Valid(so.Bytes()) {
		return "", false
	}
	started := pytext.Strip(pytext.TranslateNewlines(so.String()))
	rc := 0
	if err != nil {
		var ee *exec.ExitError
		if ok := asExitError(err, &ee); ok {
			rc = ee.ExitCode()
		} else {
			return "", false // OSError (ps missing) -> the except -> None
		}
	}
	if rc == 0 && started != "" {
		return Digest("ps:" + started), true
	}
	return "", false
}

func asExitError(err error, out **exec.ExitError) bool {
	ee, ok := err.(*exec.ExitError)
	if ok {
		*out = ee
	}
	return ok
}

// PIDAlive is `pid_alive`: os.kill(pid, 0) with ProcessLookupError,
// ValueError and OverflowError reading as dead and PermissionError
// reading as ALIVE.
//
// Two Python behaviours a Go `syscall.Kill` does not reproduce for free:
//
//   - A pid outside C int raises OverflowError, which Python reads as
//     DEAD. Go would silently truncate. Checked explicitly.
//   - pid 0 and negative pids address a process GROUP and succeed, so
//     Python reads them ALIVE. Not corrected — the two runtimes have to
//     agree on which clones a sweep may touch, and "0 means unknown" is
//     a rule neither one has.
func PIDAlive(pid int) bool {
	if pid > maxCInt || pid < minCInt {
		return false // OverflowError
	}
	err := kill(pid, 0)
	if err == nil {
		return true
	}
	return isPermission(err)
}

// OwnerIsCurrent is `owner_is_current`: liveness read conservatively,
// with PROVEN pid reuse the only thing that answers false for a live pid.
//
// recordedToken is `any` on purpose. It arrives from a JSON sidecar a
// previous process wrote, so it can be a string, a number, null, or a
// container, and Python applies two different operations to it: a
// TRUTHINESS gate (`if not recorded_token`) and then `str()`. A port that
// typed it `string` would answer differently for a sidecar carrying
// `"owner_start": 0` (falsy in Python -> retain) versus `"0"`.
func OwnerIsCurrent(
	pid int,
	recordedToken any,
	alive func(int) bool,
	tokenReader func(int) (string, bool),
) bool {
	if alive == nil {
		alive = PIDAlive
	}
	if tokenReader == nil {
		tokenReader = StartToken
	}
	if !alive(pid) {
		return false
	}
	if !pyval.Truthy(recordedToken) {
		return true // legacy ownership record: retain on ambiguity
	}
	current, ok := tokenReader(pid)
	if !ok || current == "" {
		// `if not current` is a truthiness test, so an empty STRING is
		// the same answer as None here. Both mean "platform cannot
		// prove reuse: retain".
		return true
	}
	return pyval.Str(recordedToken) == current
}
