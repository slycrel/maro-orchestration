package runs

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// runs.py:199 is `for d in sorted(root.iterdir())`, which compares
// surrogateescape-DECODED names; the port took os.ReadDir's order, which
// is a RAW BYTE sort performed inside the standard library where no scan
// of this tree can see it. The comment that used to sit on the port's loop
// asserted "UTF-8 byte order agrees with code-point order" -- true of every
// valid UTF-8 name, and false for exactly the names this class is about.
//
// The order is not cosmetic here. `_legacy_run_dir` returns the FIRST
// directory claiming the reference, so when two run directories carry the
// same loop_id -- which is what a duplicate-reference workspace IS, and the
// only reason the legacy scan still exists -- the sort decides which run
// the migration resolves to. A W23 ordering difference in the scan is a
// W24 different-answer difference at the caller.
//
//	"\xc3\xa9"  e-acute  U+00E9 = 233     first byte 0xc3 = 195
//	"\x80"      bad      U+DC80 = 56448   first byte 0x80 = 128
const pyLegacyScanOrderSrc = `
import json, sys
import runs
from pathlib import Path
root = Path(sys.argv[1])
scanned = [list(d.name.encode("utf-8", "surrogateescape"))
           for d, _meta in runs._scan_legacy_run_dirs(root)]
hit = runs._legacy_run_dir("dup-loop", root)
print(json.dumps({
    "scanned": scanned,
    "hit": None if hit is None else list(hit.name.encode("utf-8", "surrogateescape")),
}))
`

// seedDuplicateRefRuns writes two run directories that claim the SAME
// loop_id. Equal references are the point: with distinct refs the scan
// order cannot change the answer, and a fixture built that way would pass
// against a byte sort and prove nothing.
func seedDuplicateRefRuns(t *testing.T, runsRoot string) {
	t.Helper()
	// The metadata body is ASCII and IDENTICAL in both directories. An
	// earlier draft interpolated the directory name into handle_id, which
	// put a lone 0x80 inside metadata.json; CPython's
	// `read_text(encoding="utf-8")` then raised UnicodeDecodeError, the
	// scan's `except Exception: continue` swallowed it, and the fixture
	// silently measured a ONE-entry scan. Only the directory name is
	// allowed to carry the bad byte here.
	for _, name := range []string{"r\x80un", "r\xc3\xa9un"} {
		dir := filepath.Join(runsRoot, name)
		if err := os.MkdirAll(dir, 0o777); err != nil {
			t.Fatal(err)
		}
		body := `{"loop_id": "dup-loop", "handle_id": "h-dup"}`
		if err := os.WriteFile(filepath.Join(dir, "metadata.json"),
			[]byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func namesFromByteRows(rows [][]int) []string {
	out := make([]string, len(rows))
	for i, bs := range rows {
		b := make([]byte, len(bs))
		for j, v := range bs {
			b[j] = byte(v)
		}
		out[i] = string(b)
	}
	return out
}

func sameNames(a, b []string) bool {
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

func TestLegacyRunScanOrderMatchesCPython(t *testing.T) {
	ws := t.TempDir()
	runsRoot := filepath.Join(ws, "runs")
	if err := os.MkdirAll(runsRoot, 0o777); err != nil {
		t.Fatal(err)
	}
	seedDuplicateRefRuns(t, runsRoot)

	var py struct {
		Scanned [][]int `json:"scanned"`
		Hit     []int   `json:"hit"`
	}
	pyprobe.Probe{Marker: "runs.py"}.
		RunJSON(t, pyLegacyScanOrderSrc, &py, runsRoot)

	// Stated independently of both implementations. The byte order is
	// named too, so the fixture cannot quietly stop separating them.
	wantOrder := []string{"r\xc3\xa9un", "r\x80un"}
	byteOrder := []string{"r\x80un", "r\xc3\xa9un"}
	if sameNames(wantOrder, byteOrder) {
		t.Fatal("the code-point order and the raw-byte order coincide over " +
			"these names; the fixture cannot detect the bug it exists for")
	}

	if got := namesFromByteRows(py.Scanned); !sameNames(got, wantOrder) {
		t.Fatalf("CPython's _scan_legacy_run_dirs yielded %q; the "+
			"surrogateescape code points say %q", got, wantOrder)
	}
	if got := namesFromByteRows([][]int{py.Hit}); got[0] != wantOrder[0] {
		t.Fatalf("CPython's _legacy_run_dir resolved to %q, want the first "+
			"scanned directory %q", got[0], wantOrder[0])
	}

	var scanned []string
	scanLegacyRunDirs(runsRoot, func(dir string, meta map[string]any) bool {
		scanned = append(scanned, filepath.Base(dir))
		return true
	})
	if want := namesFromByteRows(py.Scanned); !sameNames(scanned, want) {
		t.Errorf("scanLegacyRunDirs yielded %q, CPython %q.\n"+
			"runs.py:199 is sorted(root.iterdir()) over decoded names; "+
			"os.ReadDir sorts by raw byte.", scanned, want)
	}

	got := filepath.Base(legacyRunDir("dup-loop", runsRoot))
	want := namesFromByteRows([][]int{py.Hit})[0]
	if got != want {
		t.Errorf("legacyRunDir resolved the duplicate reference to %q, "+
			"CPython to %q -- the FIRST HIT WINS, so the scan order decides "+
			"which run a duplicate-reference migration reaches, and the two "+
			"runtimes reach DIFFERENT RUNS.", got, want)
	}
}
