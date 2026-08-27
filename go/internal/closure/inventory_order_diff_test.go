package closure

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// closure_verify._project_file_inventory sorts TWICE per directory --
// `dirnames[:] = sorted(...)` at closure_verify.py:697 and
// `for fn in sorted(filenames)` at :700 -- and CPython compares the
// surrogateescape-DECODED name. The port compared raw bytes.
//
// The names below are the minimum pair that separates the two orders:
// raw-byte order and code-point order AGREE for all valid UTF-8 and for a
// bad byte against ASCII, and part only where a bad byte meets a MULTI-byte
// valid sequence.
//
//	"\xc3\xa9"  e-acute  U+00E9 = 233     first byte 0xc3 = 195
//	"\x80"      bad      U+DC80 = 56448   first byte 0x80 = 128
//
// So CPython puts e-acute first and a byte sort puts \x80 first, in both
// the filename sort and the dirname sort.
//
// The cap=1 case is the reason this is a MEDIUM and not a cosmetic
// ordering nit. At the cap the listing is TRUNCATED, so the two runtimes do
// not merely order the same files differently -- they name DIFFERENT FILES.
// This listing is ground truth for the closure plan, so a truncated
// inventory naming a different file produces a false-negative closure
// verdict that appears on one runtime only.
const pyInventoryOrderSrc = `
import json, sys
import closure_verify as cv
root = sys.argv[1]
print(json.dumps({
    "full": list(cv._project_file_inventory(root, 10).encode("utf-8", "surrogateescape")),
    "capped": list(cv._project_file_inventory(root, 1).encode("utf-8", "surrogateescape")),
}))
`

// seedInventoryTree writes the four-entry tree both engines walk: two files
// in the root that separate the FILENAME sort, and two subdirectories with
// identical contents that separate the DIRNAME sort. A fixture with only
// the first pair would leave closure_verify.py:697 untested, and that line
// is a real sorted() the port also got wrong.
func seedInventoryTree(t *testing.T, root string) {
	t.Helper()
	for _, name := range []string{"a\x80z.txt", "a\xc3\xa9z.txt"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, dir := range []string{"d\x80", "d\xc3\xa9"} {
		p := filepath.Join(root, dir)
		if err := os.MkdirAll(p, 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(p, "k.txt"), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func bytesToString(bs []int) string {
	b := make([]byte, len(bs))
	for i, v := range bs {
		b[i] = byte(v)
	}
	return string(b)
}

// TestProjectFileInventoryClassifiesLinksLikeOsWalk covers the OTHER half
// of the same walk: not the order of the names, but which names are names
// at all.
//
// os.ReadDir reports each entry's OWN type bits, so a symlink pointing at a
// directory is not a directory to it. os.walk splits on `entry.is_dir()`,
// which follows the link. The port took the byte-typed answer and emitted
// the link as a FILE — and at the cap that means the two runtimes name
// different things again, this time a directory link where CPython names a
// real file (adversarial r9, MEDIUM, codex seat).
//
// The dangling link is the control that keeps the fix honest in the other
// direction: scandir's is_dir() is False when the stat fails, so a dangling
// link IS a file to CPython, and a fix that simply followed every link
// would have dropped it.
func TestProjectFileInventoryClassifiesLinksLikeOsWalk(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "real"), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "real", "inner.txt"),
		[]byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "z.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	// A link to a directory: emitted by neither runtime, descended by
	// neither. Named "aa" so a byte order would put it FIRST and the cap
	// case below can see the difference.
	if err := os.Symlink(filepath.Join(root, "real"),
		filepath.Join(root, "aalink")); err != nil {
		t.Fatal(err)
	}
	// A dangling link: a file to both.
	if err := os.Symlink(filepath.Join(root, "nowhere"),
		filepath.Join(root, "abdangling")); err != nil {
		t.Fatal(err)
	}

	var py struct {
		Full   []int `json:"full"`
		Capped []int `json:"capped"`
	}
	pyprobe.Probe{Marker: "closure_verify.py"}.
		RunJSON(t, pyInventoryOrderSrc, &py, root)

	wantFull := "abdangling\nz.txt\nreal/inner.txt"
	wantCapped := "abdangling\n... (truncated at 1 files)"
	if got := bytesToString(py.Full); got != wantFull {
		t.Fatalf("CPython listed %q, want %q — either os.walk's link "+
			"handling changed or this fixture's premise is wrong",
			got, wantFull)
	}
	if got := bytesToString(py.Capped); got != wantCapped {
		t.Fatalf("CPython at cap=1 listed %q, want %q", got, wantCapped)
	}

	if got := projectFileInventory(root, 10); got != wantFull {
		t.Errorf("projectFileInventory listed\n  %q\nCPython listed\n  %q\n"+
			"os.ReadDir reports the entry's own type bits; os.walk follows "+
			"the link to decide dirnames vs filenames.", got, wantFull)
	}
	if got := projectFileInventory(root, 1); got != wantCapped {
		t.Errorf("projectFileInventory at cap=1 listed %q, CPython %q — a "+
			"directory link consumed the inventory slot a real file should "+
			"have had.", got, wantCapped)
	}
}

func TestProjectFileInventoryOrderMatchesCPython(t *testing.T) {
	root := t.TempDir()
	seedInventoryTree(t, root)

	var py struct {
		Full   []int `json:"full"`
		Capped []int `json:"capped"`
	}
	pyprobe.Probe{Marker: "closure_verify.py"}.
		RunJSON(t, pyInventoryOrderSrc, &py, root)

	// Both answers are stated here independently of BOTH implementations,
	// so a change in either one is a failure rather than a re-baseline.
	wantFull := "a\xc3\xa9z.txt\na\x80z.txt\nd\xc3\xa9/k.txt\nd\x80/k.txt"
	wantCapped := "a\xc3\xa9z.txt\n... (truncated at 1 files)"
	// The byte-sorted answers, named so the fixture cannot silently stop
	// separating the two orders (if these ever equal the wanted ones, the
	// test would pass against the very bug it was written for).
	byteFull := "a\x80z.txt\na\xc3\xa9z.txt\nd\x80/k.txt\nd\xc3\xa9/k.txt"
	byteCapped := "a\x80z.txt\n... (truncated at 1 files)"
	if wantFull == byteFull || wantCapped == byteCapped {
		t.Fatal("the code-point order and the raw-byte order coincide over " +
			"this tree; the fixture cannot detect the bug it exists for")
	}

	if got := bytesToString(py.Full); got != wantFull {
		t.Fatalf("CPython's _project_file_inventory(cap=10) returned %q; the "+
			"surrogateescape code points say %q. Either the premise is wrong "+
			"or this interpreter does not decode filenames the documented way.",
			got, wantFull)
	}
	if got := bytesToString(py.Capped); got != wantCapped {
		t.Fatalf("CPython's _project_file_inventory(cap=1) returned %q, want %q",
			got, wantCapped)
	}

	if got := projectFileInventory(root, 10); got != bytesToString(py.Full) {
		t.Errorf("projectFileInventory(cap=10) listed\n  %q\nCPython listed\n  %q\n"+
			"closure_verify.py:697 and :700 are two sorted() calls over "+
			"surrogateescape-decoded names.", got, bytesToString(py.Full))
	}
	if got := projectFileInventory(root, 1); got != bytesToString(py.Capped) {
		t.Errorf("projectFileInventory(cap=1) listed\n  %q\nCPython listed\n  %q\n"+
			"AT THE CAP the two runtimes name DIFFERENT FILES -- this is the "+
			"W24 shape, not a W23 ordering difference, and the inventory is "+
			"ground truth for the closure plan.", got, bytesToString(py.Capped))
	}
}
