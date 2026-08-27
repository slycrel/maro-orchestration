package recall

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// recall.py:407-410 is
//
//	sorted((d for d in root.iterdir() if d.is_dir()),
//	       key=lambda d: d.stat().st_mtime, reverse=True)
//
// and BOTH calls follow symlinks. The port asked `os.DirEntry.IsDir()` and
// `os.DirEntry.Info()`, which are the Lstat-shaped pair: a run reached
// through a directory symlink was dropped entirely, and had it survived it
// would have been ordered by the LINK's own mtime rather than the run's
// (adversarial r10, MEDIUM).
//
// Both halves fail independently — filtering alone would still order the
// survivor wrongly — so the fixture is built to separate them.
const pyRecallLinkSrc = `
import json, recall, sys
rows = recall.find_prior_attempts(
    sys.argv[1], window_hours=24.0, project="", exclude_handle_id="")
print(json.dumps([{"handle_id": a.handle_id} for a in rows]))
`

// seedLinkedRuns builds a runs/ directory holding exactly two entries:
//
//	zother       a real run directory, mtime set to T-10m
//	alink     -> ../realtarget, a run directory OUTSIDE runs/
//
// The link target lives outside runs/ on purpose. Pointing it at a sibling
// inside runs/ would make the same directory reachable twice with the SAME
// mtime, and the tie between them is broken by `iterdir()`'s raw readdir
// order — which r8 established has no CPython order to reproduce. A
// fixture resting on that tie would be measuring the platform.
//
// The target is given the OLDEST mtime and the link keeps its own
// creation-time mtime, which is the newest thing in the directory. So:
//
//	following the link (CPython)   zother, alink     — T-10m beats T-99h
//	reading the link's own mtime   alink, zother     — "now" beats T-10m
//
// The two orders are reverses of each other, so a sort key read the wrong
// way cannot pass by luck.
func seedLinkedRuns(t *testing.T, ws, goal string) {
	t.Helper()
	now := time.Now().UTC()
	stamp := now.Add(-time.Hour).Format("2006-01-02T15:04:05+00:00")

	write := func(dir, handle string) {
		if err := os.MkdirAll(dir, 0o777); err != nil {
			t.Fatal(err)
		}
		b, err := json.Marshal(map[string]any{
			"handle_id":  handle,
			"prompt":     goal,
			"status":     "done",
			"started_at": stamp,
		})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "metadata.json"), b, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	root := filepath.Join(ws, "runs")
	target := filepath.Join(ws, "realtarget")
	write(filepath.Join(root, "zother"), "h-zother")
	write(target, "h-target")
	if err := os.Symlink("../realtarget", filepath.Join(root, "alink")); err != nil {
		t.Fatal(err)
	}

	middle := now.Add(-10 * time.Minute)
	oldest := now.Add(-99 * time.Hour)
	if err := os.Chtimes(filepath.Join(root, "zother"), middle, middle); err != nil {
		t.Fatal(err)
	}
	// os.Chtimes FOLLOWS the link, which is exactly what is wanted: this
	// sets the TARGET's mtime and leaves the link's own alone.
	if err := os.Chtimes(target, oldest, oldest); err != nil {
		t.Fatal(err)
	}
}

func TestFindPriorAttemptsFollowsDirectorySymlinksLikeCPython(t *testing.T) {
	ws := t.TempDir()
	const goal = "port the write path and compare both engines"
	seedLinkedRuns(t, ws, goal)

	// Stated before either side runs: both runs match the goal exactly,
	// and the mtime read THROUGH the link is T-99h, so `zother` (T-10m)
	// comes first. A port reading the link's own mtime would return the
	// reverse; a port dropping the link would return one row.
	want := []string{"h-zother", "h-target"}

	var py []struct {
		HandleID string `json:"handle_id"`
	}
	pyprobe.Probe{Marker: "recall.py", Workspace: ws}.
		RunJSON(t, pyRecallLinkSrc, &py, goal)

	var pyIDs []string
	for _, r := range py {
		pyIDs = append(pyIDs, r.HandleID)
	}
	if !sameStringSlice(pyIDs, want) {
		t.Fatalf("CPython's find_prior_attempts returned %q, want %q — the "+
			"fixture's premise about is_dir()/stat() following a symlink is "+
			"wrong, so nothing below it measures anything", pyIDs, want)
	}

	got, skipped, err := FindPriorAttempts(ws, goal, 24.0, "", "")
	if err != nil {
		t.Fatalf("FindPriorAttempts: %v", err)
	}
	if skipped != 0 {
		t.Errorf("skipped = %d, want 0", skipped)
	}
	var gotIDs []string
	for _, a := range got {
		gotIDs = append(gotIDs, a.HandleID)
	}
	if !sameStringSlice(gotIDs, pyIDs) {
		t.Errorf("FindPriorAttempts = %q, CPython = %q — either a run "+
			"reached through a directory symlink is missing, or it was "+
			"ordered by the LINK's mtime instead of the run's",
			gotIDs, pyIDs)
	}
}

func sameStringSlice(a, b []string) bool {
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
