package runs

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// runs.py:200 is `d.is_dir()` over a sorted `iterdir()`, and
// `Path.is_dir()` FOLLOWS a symlink where `os.DirEntry.IsDir()` reads the
// entry's own type bits (adversarial r10, MEDIUM).
//
// This is the caller that makes it a W24 rather than a W23: the legacy
// scan feeds `_legacy_run_dir`, which returns the FIRST directory claiming
// a loop_id. A run reached through a symlink that CPython sees and the
// port does not is not merely ordered differently — the two runtimes
// resolve a duplicate reference to DIFFERENT RUNS.
const pyLegacyScanLinkSrc = `
import json, sys
import runs
from pathlib import Path
root = Path(sys.argv[1])
scanned = [d.name for d, _meta in runs._scan_legacy_run_dirs(root)]
hit = runs._legacy_run_dir("dup-loop", root)
print(json.dumps({
    "scanned": scanned,
    "hit": None if hit is None else hit.name,
}))
`

// seedSymlinkedRuns builds, under one root:
//
//	real           a genuine run directory with metadata.json
//	alink       -> real       a symlink to a directory
//	bdangling   -> nowhere    a symlink to nothing
//	.chidden       a real directory whose name starts with a dot
//
// `alink` sorts FIRST, so it decides `_legacy_run_dir`'s answer and the
// difference is a different run id rather than a different order.
// `.chidden` is the control for the loop's other half: the dot test and
// the is_dir test share one `if`, and a fix that reached only one of them
// would show up here.
func seedSymlinkedRuns(t *testing.T, root string) {
	t.Helper()
	meta := `{"loop_id": "dup-loop", "handle_id": "h-dup"}`
	for _, name := range []string{"real", ".chidden"} {
		dir := filepath.Join(root, name)
		if err := os.MkdirAll(dir, 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "metadata.json"),
			[]byte(meta), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink("real", filepath.Join(root, "alink")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("nowhere", filepath.Join(root, "bdangling")); err != nil {
		t.Fatal(err)
	}
}

func TestLegacyScanFollowsDirectorySymlinksLikeCPython(t *testing.T) {
	root := t.TempDir()
	seedSymlinkedRuns(t, root)

	// Stated ahead of both implementations: the link resolves to a
	// directory carrying metadata.json so it is scanned; the dangling link
	// is not a directory to `Path.is_dir()`; the dot-prefixed one is a
	// directory but is excluded by name.
	wantScanned := []string{"alink", "real"}
	wantHit := "alink"

	var py struct {
		Scanned []string `json:"scanned"`
		Hit     *string  `json:"hit"`
	}
	pyprobe.Probe{Marker: "runs.py", Workspace: root}.
		RunJSON(t, pyLegacyScanLinkSrc, &py, root)

	if !sameNames(py.Scanned, wantScanned) {
		t.Fatalf("CPython's _scan_legacy_run_dirs returned %q, want %q — "+
			"the fixture's premise is wrong and nothing below it measures "+
			"anything", py.Scanned, wantScanned)
	}
	if py.Hit == nil || *py.Hit != wantHit {
		t.Fatalf("CPython's _legacy_run_dir returned %v, want %q",
			py.Hit, wantHit)
	}

	var got []string
	scanLegacyRunDirs(root, func(dir string, _ map[string]any) bool {
		got = append(got, filepath.Base(dir))
		return true
	})
	if !sameNames(got, py.Scanned) {
		t.Errorf("scanLegacyRunDirs = %q, CPython = %q — a run reached "+
			"through a directory symlink is missing", got, py.Scanned)
	}

	hit := legacyRunDir("dup-loop", root)
	if hit == "" || filepath.Base(hit) != *py.Hit {
		t.Errorf("legacyRunDir resolved %q, CPython resolved %q — the two "+
			"runtimes disagree about WHICH run a duplicate reference names",
			hit, *py.Hit)
	}
}
