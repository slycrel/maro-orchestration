package record

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// The fsync is the point. Every caller is a DESTRUCTIVE whole-store
// rewrite, so a rename that is durable ahead of its data loses the whole
// store, not one appended line — and three unsynced temp+rename copies had
// grown across this port before they were collapsed into this one.
func TestAtomicWriteReplacesContentAndLeavesNoTemp(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nested", "store.jsonl")
	if err := AtomicWrite(path, []byte("one\n")); err != nil {
		t.Fatal(err)
	}
	if err := AtomicWrite(path, []byte("two\n")); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "two\n" {
		t.Fatalf("content: %q", raw)
	}
	if _, err := os.Stat(path + ".tmp"); err == nil {
		t.Fatal("a temp file was stranded beside the store")
	}
}

// A store an operator chmod'd stays chmod'd across a rewrite — Python's
// atomic_write preserves the target's mode, and a rewrite that silently
// widened permissions on a workspace file would be a quiet regression.
func TestAtomicWritePreservesAnExistingMode(t *testing.T) {
	path := filepath.Join(t.TempDir(), "store.jsonl")
	if err := os.WriteFile(path, []byte("x\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := AtomicWrite(path, []byte("y\n")); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Fatalf("mode became %v", st.Mode().Perm())
	}
	// A NEW file gets whatever a plain open() would have given it — the
	// umask-derived mode, not a hardcoded 0644 (adversarial r4, L2). The
	// expectation is MEASURED against a real open on this host rather than
	// written as an octal literal: an octal literal is what made the bug,
	// since 0644 is indistinguishable from correct under umask 022 and
	// silently narrows a group-shared workspace under umask 002.
	dir := t.TempDir()
	probe := filepath.Join(dir, "probe.txt")
	pf, err := os.OpenFile(probe, os.O_CREATE|os.O_WRONLY, 0o666)
	if err != nil {
		t.Fatal(err)
	}
	pf.Close()
	pst, err := os.Stat(probe)
	if err != nil {
		t.Fatal(err)
	}
	want := pst.Mode().Perm()

	fresh := filepath.Join(dir, "new.jsonl")
	if err := AtomicWrite(fresh, []byte("z\n")); err != nil {
		t.Fatal(err)
	}
	st, _ = os.Stat(fresh)
	if st.Mode().Perm() != want {
		t.Fatalf("new file mode: got %v, want %v (what a plain open gives here)",
			st.Mode().Perm(), want)
	}
	// And a new DIRECTORY the write had to create gets the same treatment.
	nested := filepath.Join(dir, "sub", "deep.jsonl")
	if err := AtomicWrite(nested, []byte("q\n")); err != nil {
		t.Fatal(err)
	}
	dst, err := os.Stat(filepath.Join(dir, "sub"))
	if err != nil {
		t.Fatal(err)
	}
	if got, wantDir := dst.Mode().Perm(), os.FileMode(0o777&^processUmask()); got != wantDir {
		t.Fatalf("new dir mode: got %v, want %v", got, wantDir)
	}
}

// Two writers of the same store must not share one temp file. They used to
// share `path + ".tmp"`: both opened it O_TRUNC, both wrote, and whichever
// renamed second published a file the other had already truncated under it
// (adversarial r4, L6 — deferred in r3 on the false premise that every
// caller holds the store lock).
func TestConcurrentAtomicWritesNeverPublishATornFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "store.jsonl")
	const writers = 8
	payloads := make([][]byte, writers)
	for i := range payloads {
		// Long enough that a partial write is detectable, and each
		// writer's content is uniform so any MIXTURE is provably torn.
		payloads[i] = []byte(strings.Repeat(string(rune('a'+i)), 40000) + "\n")
	}
	var wg sync.WaitGroup
	// A reader racing the writers: every observation must be exactly one
	// writer's payload, never a prefix and never a blend.
	stop := make(chan struct{})
	bad := make(chan string, 16)
	go func() {
		for {
			select {
			case <-stop:
				return
			default:
			}
			raw, err := os.ReadFile(path)
			if err != nil || len(raw) == 0 {
				continue
			}
			matched := false
			for _, p := range payloads {
				if string(raw) == string(p) {
					matched = true
					break
				}
			}
			if !matched {
				select {
				case bad <- fmt.Sprintf("torn read: %d bytes, head %q", len(raw), raw[:min(24, len(raw))]):
				default:
				}
			}
		}
	}()
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			for r := 0; r < 25; r++ {
				if err := AtomicWrite(path, payloads[i]); err != nil {
					select {
					case bad <- "write failed: " + err.Error():
					default:
					}
					return
				}
			}
		}(i)
	}
	wg.Wait()
	close(stop)
	select {
	case msg := <-bad:
		t.Fatal(msg)
	default:
	}
	// No temp files stranded beside the store.
	entries, err := os.ReadDir(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			t.Fatalf("stranded temp file: %s", e.Name())
		}
	}
}

// A failed write must not leave the intact store shadowed by a partial
// temp, and must not touch the store itself.
func TestAtomicWriteLeavesTheStoreIntactWhenItCannotWrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "store.jsonl")
	if err := AtomicWrite(path, []byte("original\n")); err != nil {
		t.Fatal(err)
	}
	// Make the temp uncreatable. This used to plant a DIRECTORY at
	// `path + ".tmp"`, which stopped injecting anything the moment the
	// temp name became unique — the injection was coupled to the
	// implementation it was testing. A read-only parent blocks any temp
	// name, so the guard survives the next change to how one is picked.
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(dir, 0o755) })
	if err := AtomicWrite(path, []byte("replacement\n")); err == nil {
		t.Fatal("the failure must be returned")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "original\n" {
		t.Fatalf("the store was damaged: %q", raw)
	}
}
