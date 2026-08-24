package record

import (
	"os"
	"path/filepath"
	"testing"
)

// The lock protocol's SIDE EFFECTS are part of the cross-runtime contract
// and are invisible in every comparison of ledger bytes: which files a
// call locks, which directories it creates, and which ledger is
// deliberately not locked at all. Each of the three below was wrong in
// this port for a whole tranche while every row-level differential passed.

// Python's acquisition mkdirs the lock's parent unconditionally
// (file_lock.py:144), which is the only reason a locked write into a cold
// workspace works there at all.
func TestLockedCreatesItsParentDirectory(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "memory", "deep", "store.jsonl")

	ran := false
	if err := Locked(path, func() error { ran = true; return nil }); err != nil {
		t.Fatalf("Locked into a cold tree: %v", err)
	}
	if !ran {
		t.Fatal("the critical section never ran")
	}
	if _, err := os.Stat(filepath.Join(root, "memory", "deep", "store.jsonl.lock")); err != nil {
		t.Errorf("no lock sidecar: %v", err)
	}
	// The DATA file is not created. Python's is not either: locked_write
	// opens the lock, not the store.
	if _, err := os.Stat(path); err == nil {
		t.Error("taking the lock materialized the store itself")
	}
}

// A stamp against a workspace that has no outcomes ledger is a miss — and
// Python arrives at that miss from INSIDE the lock, so it leaves the lock
// and the memory/ directory behind. An earlier cut of this port answered
// the same miss from a stat above the lock: same return value, different
// workspace, and an unsynchronized window where a row appended between
// the stat and the lock is one Python stamps and this does not.
func TestAMissingOutcomesStoreIsAMissTakenUnderTheLock(t *testing.T) {
	ws := t.TempDir()

	ok, err := StampOutcomeStopVerdict(ws, "loop-1", "reachable-but-not-worth-it", "why")
	if err != nil {
		t.Fatalf("a missing store is not an error: %v", err)
	}
	if ok {
		t.Error("reported a hit against a store that does not exist")
	}

	path := filepath.Join(ws, "memory", "outcomes.jsonl")
	if _, err := os.Stat(path); err == nil {
		t.Error("a failed lookup materialized memory/outcomes.jsonl")
	}
	if _, err := os.Stat(path + ".lock"); err != nil {
		t.Errorf("the miss was decided without taking the lock: %v", err)
	}
}

// The two refusals that come BEFORE the lock in both runtimes: an empty
// id or verdict, and an off-vocabulary verdict. These return without
// touching the filesystem at all, and moving the lock above them would be
// as wrong in the other direction.
func TestTheVocabularyRefusalsNeverTouchTheStore(t *testing.T) {
	for _, c := range []struct{ name, loop, verdict string }{
		{"no loop id", "", "reachable-but-not-worth-it"},
		{"no verdict", "loop-1", ""},
		{"off-vocabulary verdict", "loop-1", "not-a-real-verdict"},
	} {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			ok, err := StampOutcomeStopVerdict(ws, c.loop, c.verdict, "why")
			if ok || err != nil {
				t.Errorf("got (%v, %v), want (false, nil)", ok, err)
			}
			if entries, _ := os.ReadDir(ws); len(entries) != 0 {
				t.Errorf("a refusal before the lock wrote into the workspace: %v", entries)
			}
		})
	}
}

// The one appender that must take no lock. Its rationale is the caller's
// (observe.write_event), and the property is invisible in the appended
// row — only in what is left beside it.
func TestTheUnlockedAppenderLeavesNoLock(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")

	if err := AppendUnlockedLine(path, []byte(`{"a": 1}`)); err != nil {
		t.Fatal(err)
	}
	if err := AppendUnlockedLine(path, []byte(`{"a": 2}`)); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "events.jsonl" {
		names := []string{}
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Errorf("left %v beside the ledger", names)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "{\"a\": 1}\n{\"a\": 2}\n" {
		t.Errorf("appended %q", raw)
	}

	// It also does NOT repair a torn tail, where AppendRawLine does. That
	// asymmetry is the point of having two: the locked appender owns
	// ledgers whose rows can exceed PIPE_BUF and really can tear; this one
	// owns a ledger whose rows cannot, and reproducing Python's plain
	// `f.write(json.dumps(entry) + "\n")` matters more than repairing a
	// tear that this writer cannot produce.
	torn := filepath.Join(dir, "torn.jsonl")
	if err := os.WriteFile(torn, []byte(`{"a": 1`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := AppendUnlockedLine(torn, []byte(`{"a": 2}`)); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(torn)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "{\"a\": 1{\"a\": 2}\n" {
		t.Errorf("the unlocked appender framed a torn tail: %q", got)
	}

	// And it does not create directories — the caller mkdirs, exactly as
	// write_event does before opening the file.
	missing := filepath.Join(dir, "nope", "events.jsonl")
	if err := AppendUnlockedLine(missing, []byte(`{}`)); err == nil {
		t.Error("appended into a directory that does not exist")
	}
}
