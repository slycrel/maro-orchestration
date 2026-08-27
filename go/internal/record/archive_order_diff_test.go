package record

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// captains_log._archive_paths is `sorted(dir.glob("captains_log.*.jsonl"))`
// over Path objects, and the list it returns is the corpus the archaeology
// readers replay IN ORDER. The port took `filepath.Glob`'s order, which is
// a raw-byte sort performed inside the standard library, and its comment
// said so in as many words: "filepath.Glob already returns sorted names,
// and Python's sorted() over Path objects sorts on the same bytes."
//
// The second half of that sentence is false for any archive name that is
// not valid UTF-8, and the sentence is the reason the site survived the
// class guard written one round earlier — the guard looks for a SORT, and
// this function does not contain one.
const pyArchiveOrderSrc = `
import json, os, sys, pathlib
ws = sys.argv[1]
assert ws.startswith("/tmp/"), "refusing to touch a non-tmp store: " + ws
assert "/.maro/" not in ws, "refusing to touch the live store: " + ws
os.environ["MARO_WORKSPACE"] = ws
os.environ["MARO_USER_DIR"] = ws + "/userdir"
pathlib.Path(ws + "/userdir").mkdir(parents=True, exist_ok=True)
import captains_log as cl
active = pathlib.Path(ws) / "memory" / "captains_log.jsonl"
cl.set_log_path(active)
assert str(cl._log_path()).startswith(ws), "log path escaped the fixture"
print(json.dumps({
    "names": [list(os.fsencode(p.name)) for p in cl._archive_paths()],
}))
`

// TestArchivePathsOrderMatchesPythons builds four archives whose stamps
// span the divergence and compares the two orders.
//
// The stamps are not real timestamps on purpose. A real rotation only ever
// writes ASCII stamps, and on ASCII the two orders agree — which is
// exactly why this shipped. What decides the class is whether the NAME can
// be non-UTF-8, and a directory is a place other things write: a rotation
// from another tool, a restored backup, a filename that survived a
// filesystem move. The port's job is to read whatever is there the way
// CPython reads it, not to assume only its own writer was here.
//
//	"z"        z      = U+007A = 122       first byte 0x7a = 122
//	"\xc3\xa9" e-acute= U+00E9 = 233       first byte 0xc3 = 195
//	"\x80"     bad    = U+DC80 = 56448     first byte 0x80 = 128
//	"\xff"     bad    = U+DCFF = 56575     first byte 0xff = 255
//
// CPython: z, e-acute, \x80, \xff.   Raw bytes: z, \x80, e-acute, \xff.
func TestArchivePathsOrderMatchesPythons(t *testing.T) {
	ws := t.TempDir()
	memDir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(memDir, 0o755); err != nil {
		t.Fatal(err)
	}
	stamps := []string{"z", "\xc3\xa9", "\x80", "\xff"}
	for _, s := range stamps {
		p := filepath.Join(memDir, "captains_log."+s+".jsonl")
		if err := os.WriteFile(p, []byte("{}\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// The active file must be present and must NOT appear in the answer —
	// the pattern's dot makes that true in both runtimes, and a fixture
	// that omitted it would stop checking it.
	if err := os.WriteFile(filepath.Join(memDir, "captains_log.jsonl"),
		[]byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	var py struct {
		Names [][]int `json:"names"`
	}
	pyprobe.Probe{Marker: "captains_log.py", Workspace: ws}.
		RunJSON(t, pyArchiveOrderSrc, &py, ws)

	pyNames := make([]string, len(py.Names))
	for i, bs := range py.Names {
		b := make([]byte, len(bs))
		for j, v := range bs {
			b[j] = byte(v)
		}
		pyNames[i] = string(b)
	}

	// State the expected order independently of both the subject and the
	// oracle, then check the oracle against it. An assertion whose only
	// expected value came out of the thing under test cannot fail (P12);
	// the same is true one level up when the expected value comes only out
	// of the reference implementation.
	want := []string{
		"captains_log.z.jsonl",
		"captains_log.\xc3\xa9.jsonl",
		"captains_log.\x80.jsonl",
		"captains_log.\xff.jsonl",
	}
	byteOrder := []string{
		"captains_log.z.jsonl",
		"captains_log.\x80.jsonl",
		"captains_log.\xc3\xa9.jsonl",
		"captains_log.\xff.jsonl",
	}
	if eqNames(want, byteOrder) {
		t.Fatal("the code-point order and the raw-byte order coincide; " +
			"this fixture cannot detect the bug it was written for")
	}
	if !eqNames(pyNames, want) {
		t.Fatalf("CPython ordered %q; the surrogateescape code points say "+
			"%q. Either the fixture's premise is wrong or this interpreter "+
			"does not decode filenames the documented way.", pyNames, want)
	}

	got := ArchivePaths(memDir)
	gotNames := make([]string, len(got))
	for i, p := range got {
		gotNames[i] = filepath.Base(p)
	}
	if !eqNames(gotNames, pyNames) {
		t.Errorf("ArchivePaths ordered %q, CPython ordered %q.\n"+
			"filepath.Glob's order is a raw-byte sort inside the standard "+
			"library; sorted() compares surrogateescape-decoded code points. "+
			"The order must come from pypath.FSLess.", gotNames, pyNames)
	}
}

func eqNames(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
