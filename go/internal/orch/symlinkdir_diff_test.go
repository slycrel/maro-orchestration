package orch

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// `Path.is_dir()` FOLLOWS a symlink; `os.DirEntry.IsDir()` reads the
// entry's own type bits and never does. Both `orch_items.list_projects`
// (orch_items.py:426) and `mission.list_missions` (mission.py:1117) ask
// `is_dir()` on an `iterdir()` entry, so a project reached through a
// directory symlink is listed by CPython and was silently dropped by the
// port (adversarial r10, MEDIUM).
//
// The same spelling is wrong in the OTHER direction inside an `os.walk`
// port — there CPython puts the link in `dirnames` and names it nowhere —
// which is why `pypath.EntryIsDir` answers the question and the CALLER
// decides which Python call it is reproducing.
const pySymlinkDirSrc = `
import json, os, sys
ws = sys.argv[1]
assert ws.startswith("/tmp/"), "refusing to touch a non-tmp store: " + ws
assert "/.maro/" not in ws, "refusing to touch the live store: " + ws
os.environ["MARO_WORKSPACE"] = ws
import orch_items, mission
assert str(orch_items.projects_root()).startswith(ws), "projects root escaped"
print(json.dumps({
    "projects": orch_items.list_projects(),
    "missions": [m["project"] for m in mission.list_missions()],
}))
`

// seedSymlinkedProject builds three siblings under projects/:
//
//	real        a genuine project directory
//	alink    -> real            a symlink TO a directory
//	bdangling -> nowhere        a symlink to nothing
//
// `alink` sorts first, so a listing that drops it is visibly short at the
// head rather than the tail. `bdangling` is the control for the other half
// of the rule: `Path.is_dir()` is False — not an error — when the stat
// fails, so CPython drops it too and a helper that merely stopped
// consulting the type bits would wrongly keep it.
func seedSymlinkedProject(t *testing.T, ws string, withMission bool) {
	t.Helper()
	root := filepath.Join(ws, "projects")
	real := filepath.Join(root, "real")
	if err := os.MkdirAll(real, 0o777); err != nil {
		t.Fatal(err)
	}
	next := "# NEXT\n\n- [ ] a thing\n"
	if err := os.WriteFile(filepath.Join(real, "NEXT.md"), []byte(next), 0o644); err != nil {
		t.Fatal(err)
	}
	if withMission {
		body := `{"mission_id": "m1", "goal": "g", "status": "active", "milestones": []}`
		if err := os.WriteFile(filepath.Join(real, "mission.json"),
			[]byte(body), 0o644); err != nil {
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

func TestProjectListingsFollowDirectorySymlinksLikeCPython(t *testing.T) {
	ws := t.TempDir()
	seedSymlinkedProject(t, ws, true)

	// Stated before either side runs: the link resolves to a directory
	// that has both a NEXT.md and a mission.json, so it qualifies for both
	// listings, and the dangling link qualifies for neither.
	want := []string{"alink", "real"}

	var py struct {
		Projects []string `json:"projects"`
		Missions []string `json:"missions"`
	}
	pyprobe.Probe{Marker: "orch_items.py", Workspace: ws}.
		RunJSON(t, pySymlinkDirSrc, &py, ws)

	for _, c := range []struct {
		name string
		got  []string
	}{
		{"list_projects", py.Projects},
		{"list_missions", py.Missions},
	} {
		if !sameStrings(c.got, want) {
			t.Fatalf("CPython's %s returned %q, want %q — the fixture's "+
				"premise about Path.is_dir() following a symlink is wrong, "+
				"so nothing below it measures anything", c.name, c.got, want)
		}
	}

	slugs, err := ListProjects(ws)
	if err != nil {
		t.Fatalf("ListProjects: %v", err)
	}
	if !sameStrings(slugs, py.Projects) {
		t.Errorf("ListProjects = %q, CPython list_projects = %q — a project "+
			"reached through a directory symlink is missing", slugs, py.Projects)
	}

	var got []string
	missions := ListMissions(ws)
	for _, m := range missions {
		got = append(got, m.Project)
	}
	if !sameStrings(got, py.Missions) {
		t.Errorf("ListMissions projects = %q, CPython list_missions = %q",
			got, py.Missions)
	}
}
