package knowledge

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// A store rewrite must leave the file readable by the OTHER runtime.
//
// The port's foundational decision is that Python stays production and
// the two engines share ONE workspace store. That makes a file's mode
// part of the contract, not a detail: CPython's file_lock.atomic_write
// carries a comment about having already been bitten here once
// ("mkstemp creates 0600 and os.replace keeps the tmp's perms, so
// without correction every file this touches ends up 0600 (data-r2-03:
// rewrites silently narrow existing ledgers)"). The Go port grew its own
// temp-file dance in atomicRewrite and lost the correction, so every
// rewrite of a shared lessons store published it at 0600.
//
// Driven against the real file_lock.atomic_write, so neither arm is
// argued from a reading of the other.

func srcDirKnowledge(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// cpythonAtomicWriteModes returns the mode file_lock.atomic_write leaves
// on (a file that already existed with `seed`, a file that did not).
func cpythonAtomicWriteModes(t *testing.T, seed os.FileMode) (existing, fresh os.FileMode) {
	t.Helper()
	out, err := exec.Command("python3", "-c",
		"import json,os,sys,stat,tempfile,pathlib\n"+
			"sys.path.insert(0, sys.argv[1])\n"+
			"from file_lock import atomic_write\n"+
			"d = tempfile.mkdtemp()\n"+
			"seed = int(sys.argv[2], 8)\n"+
			"a = pathlib.Path(d, 'a.jsonl')\n"+
			"a.write_text('old\\n'); os.chmod(a, seed)\n"+
			"atomic_write(a, 'new\\n')\n"+
			"b = pathlib.Path(d, 'b.jsonl')\n"+
			"atomic_write(b, 'new\\n')\n"+
			"print(json.dumps([stat.S_IMODE(a.stat().st_mode),\n"+
			"                  stat.S_IMODE(b.stat().st_mode)]))",
		srcDirKnowledge(t), fmt.Sprintf("%o", uint32(seed))).Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	var got []uint32
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	return os.FileMode(got[0]), os.FileMode(got[1])
}

func TestAStoreRewriteKeepsTheModeCPythonKeeps(t *testing.T) {
	const seed = os.FileMode(0o664)
	wantExisting, wantFresh := cpythonAtomicWriteModes(t, seed)

	// Vacuity floor. os.CreateTemp publishes 0600, so a probe that
	// ANSWERED 0600 would make the mutation this test exists to catch
	// pass green. That happens under a umask of 0o066 or wider.
	if wantExisting == 0o600 || wantFresh == 0o600 {
		t.Fatalf("CPython answered 0600 (existing=%04o fresh=%04o) — under "+
			"this umask the test cannot fail and must not claim to pass",
			wantExisting, wantFresh)
	}
	if wantExisting != seed {
		t.Fatalf("atomic_write did not preserve the existing mode "+
			"(seed %04o -> %04o); the claim this test is built on is "+
			"wrong, fix the claim before the code", seed, wantExisting)
	}

	ws := t.TempDir()
	s := NewStore(ws)
	path := s.TieredLessonsPath("medium")
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	row := `{"lesson":"always fsync the ledger","tier":"medium","merged_variants":[]}`
	if err := os.WriteFile(path, []byte(row+"\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, seed); err != nil {
		t.Fatal(err)
	}

	if err := s.UnionVariantsIntoLesson("always fsync the ledger",
		[]string{"remember to fsync the ledger"}); err != nil {
		t.Fatal(err)
	}

	// The rewrite must actually have happened, or the mode below is the
	// seed's by accident and the assertion measures nothing.
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(after), "merged_variants\":[\"remember") &&
		!strings.Contains(string(after), "remember to fsync") {
		t.Fatalf("no rewrite happened, so the mode proves nothing:\n%s", after)
	}

	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := st.Mode().Perm(); got != wantExisting {
		t.Errorf("a shared lessons store came out %04o where CPython's "+
			"atomic_write leaves it %04o — the other runtime may no longer "+
			"be able to read a store this one rewrote", got, wantExisting)
	}
}
