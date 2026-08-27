package pack

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// Quarantine exists so a human -- or the OTHER runtime -- can come and
// look at what was refused. A quarantined artifact published at 0600 is
// one only the writing uid can open, which defeats the point of the
// directory it lands in.
//
// CPython's _write_quarantine calls file_lock.atomic_write, which fchmods
// the temp before publishing it. The port grew its own os.CreateTemp +
// Rename, and os.CreateTemp creates 0600.
//
// Driven against the real _write_quarantine, so neither arm is argued
// from a reading of the other.

func srcDirPack(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestAQuarantinedFileGetsTheModeCPythonGivesIt(t *testing.T) {
	const seed = os.FileMode(0o664)

	out, perr := exec.Command("python3", "-c",
		"import json,os,sys,stat,tempfile,pathlib\n"+
			"sys.path.insert(0, sys.argv[1])\n"+
			"import pack\n"+
			"d = tempfile.mkdtemp()\n"+
			"seed = int(sys.argv[2], 8)\n"+
			"a = pathlib.Path(d, 'q', 'skills', 'a.md')\n"+
			"a.parent.mkdir(parents=True)\n"+
			"a.write_text('old\\n'); os.chmod(a, seed)\n"+
			"pack._write_quarantine(a, 'new\\n')\n"+
			"b = pathlib.Path(d, 'q', 'skills', 'b.md')\n"+
			"pack._write_quarantine(b, 'new\\n')\n"+
			"print(json.dumps([stat.S_IMODE(a.stat().st_mode),\n"+
			"                  stat.S_IMODE(b.stat().st_mode)]))",
		srcDirPack(t), fmt.Sprintf("%o", uint32(seed))).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []uint32
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	wantExisting, wantFresh := os.FileMode(want[0]), os.FileMode(want[1])

	// Vacuity floor: os.CreateTemp publishes 0600, so a CPython answer of
	// 0600 would make the defect pass green.
	if wantExisting == 0o600 || wantFresh == 0o600 {
		t.Fatalf("CPython answered 0600 (existing=%04o fresh=%04o) — under "+
			"this umask the test cannot fail and must not claim to pass",
			wantExisting, wantFresh)
	}

	ws := t.TempDir()
	im := &importer{ws: ws, store: nil, packName: "p", label: "l",
		packTag: "t", now: "2026-08-27T00:00:00Z"}

	dir := filepath.Join(ws, "quarantine", "skills")
	if err := os.MkdirAll(dir, 0o777); err != nil {
		t.Fatal(err)
	}

	// The already-exists arm: seeded at 0664, rewritten with new content.
	existing := filepath.Join(dir, "a.md")
	if err := os.WriteFile(existing, []byte("old\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(existing, seed); err != nil {
		t.Fatal(err)
	}
	already, err := im.writeQuarantine(existing, "new\n")
	if err != nil {
		t.Fatal(err)
	}
	if already {
		t.Fatal("the content matched, so no write happened and the mode " +
			"below would be the seed's by accident")
	}
	st, err := os.Stat(existing)
	if err != nil {
		t.Fatal(err)
	}
	if got := st.Mode().Perm(); got != wantExisting {
		t.Errorf("a REWRITTEN quarantine file came out %04o where CPython "+
			"leaves it %04o", got, wantExisting)
	}

	// The fresh arm.
	fresh := filepath.Join(dir, "b.md")
	if _, err := im.writeQuarantine(fresh, "new\n"); err != nil {
		t.Fatal(err)
	}
	st2, err := os.Stat(fresh)
	if err != nil {
		t.Fatal(err)
	}
	if got := st2.Mode().Perm(); got != wantFresh {
		t.Errorf("a NEW quarantine file came out %04o where CPython leaves "+
			"it %04o — quarantine exists so someone can come and read it",
			got, wantFresh)
	}
}
