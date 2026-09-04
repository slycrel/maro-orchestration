package workspace

import (
	"bytes"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func announced(t *testing.T) (*Announced, *bytes.Buffer) {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "ws")
	t.Setenv(EnvOverride, dir)
	r, err := Resolve()
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	a, err := r.Announce(&out)
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Ensure(); err != nil {
		t.Fatal(err)
	}
	return a, &out
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }

// The path is printed before any write, by construction: only a successful
// Announce yields the capability a writer needs.
func TestAnnounceMustSucceedToYieldACapability(t *testing.T) {
	t.Setenv(EnvOverride, t.TempDir())
	r, _ := Resolve()
	if a, err := r.Announce(failingWriter{}); err == nil || a != nil {
		t.Fatalf("failed announce must not yield a capability: %v %v", a, err)
	}
	var out bytes.Buffer
	a, err := r.Announce(&out)
	if err != nil || a == nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), r.String()) || !strings.Contains(out.String(), "(env)") {
		t.Fatalf("announce line: %q", out.String())
	}
}

func TestResolveCleansAndTreatsEmptyOverrideAsUnset(t *testing.T) {
	dir := t.TempDir()
	t.Setenv(EnvOverride, dir+"/")
	r, _ := Resolve()
	if r.String() != filepath.Clean(dir) {
		t.Fatalf("trailing slash not cleaned: %q", r.String())
	}
	t.Setenv(EnvOverride, "   ")
	home := t.TempDir()
	t.Setenv("HOME", home)
	r, err := Resolve()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(r.String(), home) || !strings.HasSuffix(r.String(), DefaultRel) || r.source != "default" {
		t.Fatalf("default root %q source %q", r.String(), r.source)
	}
}

// D12: one process per root. The lock is the proof; a second acquire in the
// same process (a second open file description) is refused too.
func TestSecondAcquireRefusedWhileHeld(t *testing.T) {
	a, _ := announced(t)
	l1, err := Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	defer l1.Release()
	if _, err := Acquire(a); !errors.Is(err, ErrLeaseHeld) {
		t.Fatalf("want ErrLeaseHeld, got %v", err)
	}
	_, live, err := Status(a)
	if err != nil || !live {
		t.Fatalf("Status must report live while held: live=%v err=%v", live, err)
	}
}

// Two real processes race for the same root: exactly one wins, and the
// winner's durable lease equals what it returned.
func TestConcurrentAcquireExactlyOneSucceeds(t *testing.T) {
	if os.Getenv("MARO_GO_LEASE_HELPER") == "1" {
		helperHold()
		return
	}
	a, _ := announced(t)
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	const n = 6
	procs := make([]*exec.Cmd, n)
	outs := make([]*bytes.Buffer, n)
	for i := range procs {
		outs[i] = &bytes.Buffer{}
		c := exec.Command(exe, "-test.run", "^TestConcurrentAcquireExactlyOneSucceeds$")
		c.Env = append(os.Environ(), "MARO_GO_LEASE_HELPER=1", EnvOverride+"="+a.String())
		c.Stdout, c.Stderr = outs[i], outs[i]
		procs[i] = c
	}
	for _, c := range procs {
		if err := c.Start(); err != nil {
			t.Fatal(err)
		}
	}
	wins, held := 0, 0
	for i, c := range procs {
		_ = c.Wait()
		s := outs[i].String()
		switch {
		case strings.Contains(s, "HELPER:WON"):
			wins++
		case strings.Contains(s, "HELPER:HELD"):
			held++
		default:
			t.Fatalf("helper %d unexpected output:\n%s", i, s)
		}
	}
	if wins != 1 || held != n-1 {
		t.Fatalf("wins=%d held=%d", wins, held)
	}
}

func helperHold() {
	r, err := Resolve()
	if err != nil {
		os.Stdout.WriteString("HELPER:ERR " + err.Error())
		return
	}
	a, err := r.Announce(io.Discard)
	if err != nil {
		os.Stdout.WriteString("HELPER:ERR " + err.Error())
		return
	}
	// Barrier: all helpers spin until the same wall-clock instant.
	target := time.Now().Truncate(200 * time.Millisecond).Add(400 * time.Millisecond)
	for time.Now().Before(target) {
	}
	l, err := Acquire(a)
	if errors.Is(err, ErrLeaseHeld) {
		os.Stdout.WriteString("HELPER:HELD")
		return
	}
	if err != nil {
		os.Stdout.WriteString("HELPER:ERR " + err.Error())
		return
	}
	cur, live, serr := Status(a)
	if serr != nil || !live || cur == nil || cur.PID != l.PID || cur.Epoch != l.Epoch {
		os.Stdout.WriteString("HELPER:ERR durable lease disagrees with returned lease")
		return
	}
	time.Sleep(300 * time.Millisecond)
	l.Release()
	os.Stdout.WriteString("HELPER:WON")
}

// A dead holder is taken over (the kernel released its lock); the epoch only
// ever increases; releasing the old lease cannot clobber the new one.
func TestDeadHolderTakenOverWithHigherEpoch(t *testing.T) {
	a, _ := announced(t)
	l1, err := Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	// Simulate death: drop the lock without removing lease.json.
	l1.lock.Close()
	l1.lock = nil
	l2, err := Acquire(a)
	if err != nil {
		t.Fatalf("dead holder must be taken over: %v", err)
	}
	defer l2.Release()
	if l2.Epoch != l1.Epoch+1 {
		t.Fatalf("epoch %d → %d", l1.Epoch, l2.Epoch)
	}
	if err := l1.Release(); err != nil {
		t.Fatalf("old release: %v", err)
	}
	cur, live, _ := Status(a)
	if cur == nil || cur.Epoch != l2.Epoch || !live {
		t.Fatalf("old Release clobbered the new lease: %+v live=%v", cur, live)
	}
}

func TestEpochMonotonicAcrossRelease(t *testing.T) {
	a, _ := announced(t)
	l1, _ := Acquire(a)
	l1.Release()
	l2, _ := Acquire(a)
	defer l2.Release()
	if l2.Epoch != l1.Epoch+1 {
		t.Fatalf("%d → %d", l1.Epoch, l2.Epoch)
	}
}

// Lease doctrine: ambiguity refuses. A malformed epoch never resets the
// counter; MaxUint64 never wraps; a directory at lease.json is corruption.
func TestCorruptStateRefusesRatherThanGuessing(t *testing.T) {
	a, _ := announced(t)
	l, _ := Acquire(a)
	l.Release()
	os.WriteFile(a.Path(epochFile), []byte("garbage\n"), 0o644)
	if _, err := Acquire(a); !errors.Is(err, ErrLeaseCorrupt) {
		t.Fatalf("malformed epoch must refuse, got %v", err)
	}
	os.WriteFile(a.Path(epochFile), []byte("18446744073709551615\n"), 0o644)
	if _, err := Acquire(a); !errors.Is(err, ErrLeaseCorrupt) {
		t.Fatalf("MaxUint64 epoch must refuse, got %v", err)
	}
	os.WriteFile(a.Path(epochFile), []byte("41\n"), 0o644)
	os.Remove(a.Path(leaseFile))
	os.Mkdir(a.Path(leaseFile), 0o755)
	if _, err := Acquire(a); !errors.Is(err, ErrLeaseCorrupt) {
		t.Fatalf("directory at lease.json must refuse, got %v", err)
	}
	os.Remove(a.Path(leaseFile))
	// A malformed lease.json with a FREE lock is a dead holder's debris:
	// taken over, and the recovery is recorded on the lease, not hidden.
	os.WriteFile(a.Path(leaseFile), []byte("{not json"), 0o644)
	l2, err := Acquire(a)
	if err != nil {
		t.Fatalf("malformed lease under a free lock must be recoverable: %v", err)
	}
	defer l2.Release()
	if l2.Recovered == "" || l2.Epoch != 42 {
		t.Fatalf("recovery not recorded or epoch wrong: %+v", l2)
	}
}

// Unknown keys in lease.json are malformed (strict decode), not silently
// accepted.
func TestLeaseFileStrictDecode(t *testing.T) {
	a, _ := announced(t)
	os.WriteFile(a.Path(leaseFile), []byte(`{"pid":4242,"epoch":1,"host":"x","started":"2026-09-04T00:00:00Z","extra":true}`), 0o644)
	if _, err := readLeaseFile(a.Path(leaseFile)); !errors.Is(err, errMalformed) {
		t.Fatalf("unknown key accepted: %v", err)
	}
}
