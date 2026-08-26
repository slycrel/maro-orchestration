package orch

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The mkdir inside projects_root(), and the enumerators that reach it.
//
// orch_items.projects_root() creates the directory on BOTH branches — the
// config.projects_dir() one and the MARO_ORCH_ROOT one — so resolving the
// projects root is not a pure operation in the original either. Three
// call sites in the Python read it and then immediately test
// `if not <root>.exists()`, a guard that cannot fire because the line
// above just created the directory (orch_items.list_projects,
// sheriff.check_all_projects, mission.list_missions).
//
// The three do NOT agree about what a failure means, which is why this
// measures them separately rather than trusting one to stand for the
// others:
//
//	list_projects         no try   -> raises
//	list_missions         no try   -> raises
//	resolve_project_slug  own try  -> swallows, still returns its slug
//
// EVERY ROW GETS ITS OWN WORKSPACE, and that is load-bearing rather than
// tidy. The first version of this test ran both enumerators against one
// workspace and asserted `projects/` existed at the end. Battery mutant
// PJ-4 reverted ListMissions to a pure join and SURVIVED: ListProjects
// had already created the directory two lines earlier, so the assertion
// was satisfied by the wrong call. A side-effect test where something
// else performs the side effect first is measuring nothing.
//
// The MODE is compared too, for the reason the memory-dir differential
// gives: Path.mkdir passes 0o777 and lets the umask narrow it, so a
// literal here would pin the umask of whoever ran the test. PJ-5 survived
// the first version because this test had no mode assertion at all.
const projectsPySrc = `
import json, os, stat, sys

out = []
for i, pair in enumerate(json.loads(sys.argv[2])):
    mode, which = pair
    ws = _pyprobe_use(os.path.join(sys.argv[1], "ws%d" % i))
    os.makedirs(ws, exist_ok=True)
    if mode == "shadowed":
        open(os.path.join(ws, "projects"), "w").close()
    elif mode == "populated":
        alpha = os.path.join(ws, "projects", "alpha")
        os.makedirs(alpha, exist_ok=True)
        open(os.path.join(alpha, "NEXT.md"), "w").close()
        with open(os.path.join(alpha, "mission.json"), "w") as fh:
            fh.write(json.dumps({"id": "m1", "goal": "g", "milestones": []}))
        os.makedirs(os.path.join(ws, "projects", "beta"), exist_ok=True)
    row = {"mode": mode, "which": which, "slugs": [], "count": -1}
    if which == "list_projects":
        import orch_items
        try:
            row["slugs"] = orch_items.list_projects()
            row["raised"] = ""
        except Exception as e:
            row["slugs"] = None
            row["raised"] = type(e).__name__
    else:
        import mission
        try:
            row["count"] = len(mission.list_missions())
            row["raised"] = ""
        except Exception as e:
            row["raised"] = type(e).__name__
    p = os.path.join(ws, "projects")
    row["exists"] = os.path.isdir(p)
    row["perm"] = oct(stat.S_IMODE(os.stat(p).st_mode)) if row["exists"] else ""
    out.append(row)
print(json.dumps(out))
`

type projectsRow struct {
	Mode   string   `json:"mode"`
	Which  string   `json:"which"`
	Slugs  []string `json:"slugs"`
	Count  int      `json:"count"`
	Raised string   `json:"raised"`
	Exists bool     `json:"exists"`
	Perm   string   `json:"perm"`
}

func TestTheProjectsEnumeratorsCreateWhatPythonCreates(t *testing.T) {
	modes := []string{"fresh", "populated", "shadowed"}
	whichs := []string{"list_projects", "list_missions"}

	type pair struct{ mode, which string }
	var pairs []pair
	for _, m := range modes {
		for _, w := range whichs {
			pairs = append(pairs, pair{m, w})
		}
	}

	root := t.TempDir()
	spaces := make([]string, len(pairs))
	argPairs := make([][2]string, len(pairs))
	for i, p := range pairs {
		spaces[i] = filepath.Join(root, "ws"+strconv.Itoa(i))
		argPairs[i] = [2]string{p.mode, p.which}
	}

	var want []projectsRow
	pyprobe.Probe{Marker: "orch_items.py", Workspaces: spaces}.
		RunJSON(t, projectsPySrc, &want, root, pyprobe.Arg(t, argPairs))
	if len(want) != len(pairs) {
		t.Fatalf("probe answered %d rows, want %d", len(want), len(pairs))
	}

	// Anti-vacuity, read out of CPython's own answers rather than by eye.
	// Four ways this table could agree while proving nothing: the fresh
	// rows not creating anything (the property under test), the shadowing
	// file failing to land (nothing would raise), the populated fixture
	// producing no project (the NEXT.md filter would go untested), and
	// list_missions never seeing a mission (its count would be 0 either
	// way).
	byKey := map[string]projectsRow{}
	for _, w := range want {
		byKey[w.Mode+"/"+w.Which] = w
	}
	for _, which := range whichs {
		if w := byKey["fresh/"+which]; !w.Exists || w.Raised != "" || w.Perm == "" {
			t.Fatalf("CPython's fresh/%s row is %+v — this test is about a "+
				"fresh workspace gaining projects/ from a READ, and it did not",
				which, w)
		}
		if w := byKey["shadowed/"+which]; w.Raised == "" {
			t.Fatalf("CPython's shadowed/%s row is %+v — the file that is "+
				"supposed to block the mkdir is not blocking it", which, w)
		}
	}
	if w := byKey["populated/list_projects"]; len(w.Slugs) != 1 || w.Slugs[0] != "alpha" {
		t.Fatalf("CPython's populated row is %+v — want exactly [alpha]; "+
			"beta has no NEXT.md and must not count", w)
	}
	if w := byKey["populated/list_missions"]; w.Count != 1 {
		t.Fatalf("CPython's populated/list_missions row is %+v — want 1 "+
			"mission; a count of 0 would agree with any port at all", w)
	}

	for i, p := range pairs {
		t.Run(p.mode+"/"+p.which, func(t *testing.T) {
			ws := filepath.Join(t.TempDir(), "ws")
			if err := os.MkdirAll(ws, 0o775); err != nil {
				t.Fatal(err)
			}
			switch p.mode {
			case "shadowed":
				if err := os.WriteFile(filepath.Join(ws, "projects"), nil, 0o644); err != nil {
					t.Fatal(err)
				}
			case "populated":
				alpha := filepath.Join(ws, "projects", "alpha")
				if err := os.MkdirAll(alpha, 0o775); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(alpha, "NEXT.md"), nil, 0o644); err != nil {
					t.Fatal(err)
				}
				mj := `{"id": "m1", "goal": "g", "milestones": []}`
				if err := os.WriteFile(filepath.Join(alpha, "mission.json"), []byte(mj), 0o644); err != nil {
					t.Fatal(err)
				}
				if err := os.MkdirAll(filepath.Join(ws, "projects", "beta"), 0o775); err != nil {
					t.Fatal(err)
				}
			}
			w := want[i]

			switch p.which {
			case "list_projects":
				slugs, err := ListProjects(ws)
				if (err != nil) != (w.Raised != "") {
					t.Fatalf("ListProjects err=%v; CPython raised %q", err, w.Raised)
				}
				if err == nil && !sameStrings(slugs, w.Slugs) {
					t.Errorf("ListProjects = %v; CPython %v", slugs, w.Slugs)
				}
			case "list_missions":
				// No error channel: where CPython raises, the port answers
				// empty. That is the named residual, and asserting it here
				// keeps it a recorded fact.
				got := len(ListMissions(ws))
				if w.Raised != "" {
					if got != 0 {
						t.Errorf("ListMissions = %d rows on the workspace "+
							"CPython raised %s on; the documented answer is "+
							"empty", got, w.Raised)
					}
				} else if got != w.Count {
					t.Errorf("ListMissions = %d; CPython %d", got, w.Count)
				}
			}

			fi, serr := os.Stat(filepath.Join(ws, "projects"))
			gotExists := serr == nil && fi.IsDir()
			if gotExists != w.Exists {
				t.Errorf("projects/ isdir=%v after %s; CPython %v — "+
					"projects_root() mkdirs, so a READ creates it",
					gotExists, p.which, w.Exists)
			}
			if gotExists {
				// Against CPython's own answer, never a literal: both
				// runtimes pass 0o777 and share a umask.
				got := "0o" + strconv.FormatUint(uint64(fi.Mode().Perm()), 8)
				if got != w.Perm {
					t.Errorf("projects/ mode %s; CPython produced %s", got, w.Perm)
				}
			}
		})
	}
}

// TestProjectDirIsStillAPureJoin pins the residual this slice did NOT
// close, so it is a recorded fact with a discriminating input rather than
// a comment asserting one.
//
// In Python `project_dir(slug)` is `projects_root() / slug`, so resolving
// ANY per-project path creates projects/. In this port ProjectDir and its
// path family (NextPath, DecisionsPath, RisksPath, ProvenancePath,
// PriorityPath) are pure joins — 32 non-test call sites, which is a
// signature change of its own and is filed in BACKLOG rather than ridden
// in here.
//
// The divergence is confined to READ-ONLY paths: any caller that goes on
// to WRITE creates the per-project directory with parents, and projects/
// appears anyway. If this test starts failing because ProjectDir grew the
// side effect, that is the fix landing — delete it, do not weaken it.
func TestProjectDirIsStillAPureJoin(t *testing.T) {
	for _, tc := range []struct {
		name string
		call func(ws string)
	}{
		{"ProjectDir", func(ws string) { _ = ProjectDir(ws, "alpha") }},
		{"NextPath", func(ws string) { _ = NextPath(ws, "alpha") }},
		{"PriorityPath", func(ws string) { _ = PriorityPath(ws, "alpha") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := t.TempDir()
			tc.call(ws)
			if _, err := os.Stat(filepath.Join(ws, "projects")); !os.IsNotExist(err) {
				t.Errorf("%s created projects/ (stat err: %v) — if that is "+
					"deliberate, the BACKLOG entry and this pin are both "+
					"stale", tc.name, err)
			}
		})
	}
	if got, want := ProjectDir("/w", "alpha"), filepath.Join("/w", "projects", "alpha"); got != want {
		t.Errorf("ProjectDir = %q, want %q", got, want)
	}
}
