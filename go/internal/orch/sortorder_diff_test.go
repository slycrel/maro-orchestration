package orch

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The four names every fixture in this file uses, and why these four:
// raw-byte order and surrogateescape code-point order AGREE for all valid
// UTF-8 and for a bad byte against ASCII. They part only when a bad byte
// meets a MULTI-byte valid sequence, which is why this class survived so
// many rounds of reading the Go tree.
//
//	"z"        z       = U+007A = 122      first byte 0x7a = 122
//	"\xc3\xa9" e-acute = U+00E9 = 233      first byte 0xc3 = 195
//	"\x80"     bad     = U+DC80 = 56448    first byte 0x80 = 128
//
// CPython: z, e-acute, \x80.   Raw bytes: z, \x80, e-acute.
var orderMarkers = []string{"z", "\xc3\xa9", "\x80"}

const pyOrchOrderSrc = `
import json, os, sys
ws = sys.argv[1]
assert ws.startswith("/tmp/"), "refusing to touch a non-tmp store: " + ws
assert "/.maro/" not in ws, "refusing to touch the live store: " + ws
os.environ["MARO_WORKSPACE"] = ws
import orch_items, mission
assert str(orch_items.projects_root()).startswith(ws), "projects root escaped"
print(json.dumps({
    "projects": [list(os.fsencode(s)) for s in orch_items.list_projects()],
    "blocked": [list(os.fsencode(s.slug))
                for s in orch_items.list_blocked_projects()],
    "missions": [list(os.fsencode(m["project"]))
                 for m in mission.list_missions()],
}))
`

// seedOrderedProjects builds one project per marker, each with an
// identical NEXT.md holding a single blocked item and no priority file, so
// that priority and blocked count TIE for every project and the slug alone
// decides list_blocked_projects' order. A fixture where the slug is not
// the deciding key would pass against a byte sort and prove nothing.
func seedOrderedProjects(t *testing.T, ws, prefix string, withMission bool) {
	t.Helper()
	root := filepath.Join(ws, "projects")
	for _, m := range orderMarkers {
		dir := filepath.Join(root, prefix+m)
		if err := os.MkdirAll(dir, 0o777); err != nil {
			t.Fatal(err)
		}
		next := "# NEXT\n\n- [!] a blocked item\n"
		if err := os.WriteFile(filepath.Join(dir, "NEXT.md"), []byte(next), 0o644); err != nil {
			t.Fatal(err)
		}
		if withMission {
			body := `{"mission_id": "m1", "goal": "g", "status": "active", "milestones": []}`
			if err := os.WriteFile(filepath.Join(dir, "mission.json"),
				[]byte(body), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
}

func decodeByteRows(rows [][]int) []string {
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

// sameStrings lives in mission.go; these fixtures reuse it rather than
// growing a second copy in the same package.

// TestProjectListingOrdersMatchCPython covers the three orderings that
// come out of one projects/ directory, in one probe run.
//
//   - list_projects        — already on FSLess; the CONTROL. If it ever
//     goes red the other two rows are measuring the wrong thing.
//   - list_missions        — mission.py:1116 `sorted(iterdir())`; the port
//     took os.ReadDir's raw-byte order under a comment asserting the two
//     were the same order (adversarial r8, MEDIUM).
//   - list_blocked_projects — orch_items.py:779 sorts by
//     `(priority, blocked, slug)` with reverse=True. The slug is IN the
//     key, so this is a real Python string comparison, and the port used a
//     bare `>`. ListProjects hands this function a correctly ordered list
//     and the sort threw that order away one call later.
func TestProjectListingOrdersMatchCPython(t *testing.T) {
	ws := t.TempDir()
	seedOrderedProjects(t, ws, "p", true)

	var py struct {
		Projects [][]int `json:"projects"`
		Blocked  [][]int `json:"blocked"`
		Missions [][]int `json:"missions"`
	}
	pyprobe.Probe{Marker: "orch_items.py", Workspace: ws}.
		RunJSON(t, pyOrchOrderSrc, &py, ws)

	// State the expected orders independently of BOTH the port and the
	// interpreter, then check the interpreter against them. An expected
	// value that came only out of the reference implementation silently
	// re-baselines when the interpreter changes.
	ascending := []string{"pz", "p\xc3\xa9", "p\x80"}
	descending := []string{"p\x80", "p\xc3\xa9", "pz"}
	byteAscending := []string{"pz", "p\x80", "p\xc3\xa9"}
	if sameStrings(ascending, byteAscending) {
		t.Fatal("the code-point order and the raw-byte order coincide; " +
			"these fixtures cannot detect the bug they were written for")
	}

	for _, c := range []struct {
		name string
		got  []string
		want []string
	}{
		{"list_projects", decodeByteRows(py.Projects), ascending},
		{"list_missions", decodeByteRows(py.Missions), ascending},
		{"list_blocked_projects", decodeByteRows(py.Blocked), descending},
	} {
		if !sameStrings(c.got, c.want) {
			t.Fatalf("CPython's %s returned %q; the surrogateescape code "+
				"points say %q. Either the fixture's premise is wrong or "+
				"this interpreter does not decode filenames the documented "+
				"way.", c.name, c.got, c.want)
		}
	}

	slugs, err := ListProjects(ws)
	if err != nil {
		t.Fatalf("ListProjects: %v", err)
	}
	if want := decodeByteRows(py.Projects); !sameStrings(slugs, want) {
		t.Errorf("ListProjects ordered %q, CPython %q (the CONTROL — if "+
			"this row fails, the two below are not measuring what they say)",
			slugs, want)
	}

	missions := ListMissions(ws)
	var gotMissions []string
	for _, m := range missions {
		gotMissions = append(gotMissions, m.Project)
	}
	if want := decodeByteRows(py.Missions); !sameStrings(gotMissions, want) {
		t.Errorf("ListMissions ordered %q, CPython %q.\n"+
			"mission.py:1116 is sorted(projects_root.iterdir()), which "+
			"compares surrogateescape-decoded code points; os.ReadDir "+
			"sorts by raw byte, in the standard library where no scan of "+
			"this tree can see it.", gotMissions, want)
	}

	blocked, err := ListBlockedProjects(ws)
	if err != nil {
		t.Fatalf("ListBlockedProjects: %v", err)
	}
	var gotBlocked []string
	for _, b := range blocked {
		gotBlocked = append(gotBlocked, b.Slug)
	}
	if want := decodeByteRows(py.Blocked); !sameStrings(gotBlocked, want) {
		t.Errorf("ListBlockedProjects ordered %q, CPython %q.\n"+
			"orch_items.py:779 sorts by (priority, blocked, slug) with "+
			"reverse=True — the slug is IN the key, so it is a Python "+
			"string comparison and not a byte compare.", gotBlocked, want)
	}
}
