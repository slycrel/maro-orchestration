package workspace

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func announced(t *testing.T) (*Root, *bytes.Buffer) {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "ws")
	t.Setenv(EnvOverride, dir)
	r, err := Resolve()
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	r.Announce(&out)
	if err := r.Ensure(); err != nil {
		t.Fatal(err)
	}
	return r, &out
}

// The path is printed before any write, by construction: an unannounced
// Root refuses to hand out a path at all.
func TestUnannouncedRootRefusesPaths(t *testing.T) {
	t.Setenv(EnvOverride, t.TempDir())
	r, _ := Resolve()
	if _, err := r.Path("thoughts"); !errors.Is(err, ErrUnannounced) {
		t.Fatalf("want ErrUnannounced, got %v", err)
	}
	if err := r.Ensure(); !errors.Is(err, ErrUnannounced) {
		t.Fatalf("Ensure before Announce must refuse, got %v", err)
	}
}

func TestAnnouncePrintsPathAndSource(t *testing.T) {
	r, out := announced(t)
	if !strings.Contains(out.String(), r.String()) || !strings.Contains(out.String(), "(env)") {
		t.Fatalf("announce line: %q", out.String())
	}
}

func TestDefaultRootIsUnderHome(t *testing.T) {
	t.Setenv(EnvOverride, "")
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

// D12: one process per root. A second Acquire against a live holder refuses.
func TestSecondProcessRefused(t *testing.T) {
	r, _ := announced(t)
	l1, err := Acquire(r)
	if err != nil {
		t.Fatal(err)
	}
	defer l1.Release()
	// Simulate "another process": the holder pid is us, so pretend the pid differs.
	lp, _ := r.Path(leaseFile)
	raw, _ := os.ReadFile(lp)
	raw = bytes.Replace(raw, []byte(`"pid":`+itoa(os.Getpid())), []byte(`"pid":999999`), 1)
	os.WriteFile(lp, raw, 0o644)
	old := alive
	alive = func(pid int) bool { return pid == 999999 }
	defer func() { alive = old }()
	if _, err := Acquire(r); !errors.Is(err, ErrLeaseHeld) {
		t.Fatalf("want ErrLeaseHeld, got %v", err)
	}
}

// A dead holder is taken over, and the epoch only ever increases.
func TestStaleLeaseTakenOverWithHigherEpoch(t *testing.T) {
	r, _ := announced(t)
	l1, err := Acquire(r)
	if err != nil {
		t.Fatal(err)
	}
	old := alive
	alive = func(int) bool { return false }
	defer func() { alive = old }()
	l2, err := Acquire(r)
	if err != nil {
		t.Fatalf("stale lease must be taken over: %v", err)
	}
	if l2.Epoch != l1.Epoch+1 {
		t.Fatalf("epoch %d → %d", l1.Epoch, l2.Epoch)
	}
	// Releasing the OLD lease must not remove the new holder's file.
	if err := l1.Release(); err != nil {
		t.Fatal(err)
	}
	cur, _, err := Current(r)
	if err != nil || cur == nil || cur.Epoch != l2.Epoch {
		t.Fatalf("old Release clobbered the new lease: %+v %v", cur, err)
	}
}

func TestEpochSurvivesRelease(t *testing.T) {
	r, _ := announced(t)
	l1, _ := Acquire(r)
	l1.Release()
	l2, _ := Acquire(r)
	if l2.Epoch != l1.Epoch+1 {
		t.Fatalf("epoch must be monotonic across release: %d → %d", l1.Epoch, l2.Epoch)
	}
	l2.Release()
}

func itoa(n int) string {
	b := []byte{}
	if n == 0 {
		return "0"
	}
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}
