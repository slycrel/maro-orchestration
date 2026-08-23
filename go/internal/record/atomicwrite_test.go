package record

import (
	"os"
	"path/filepath"
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
	// A NEW file gets the default.
	fresh := filepath.Join(t.TempDir(), "new.jsonl")
	if err := AtomicWrite(fresh, []byte("z\n")); err != nil {
		t.Fatal(err)
	}
	st, _ = os.Stat(fresh)
	if st.Mode().Perm() != 0o644 {
		t.Fatalf("new file mode: %v", st.Mode().Perm())
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
	// A directory where the temp must go.
	if err := os.Mkdir(path+".tmp", 0o755); err != nil {
		t.Fatal(err)
	}
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
