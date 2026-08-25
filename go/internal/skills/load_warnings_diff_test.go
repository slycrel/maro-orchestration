package skills

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyLoadWarnSrc seeds skills.jsonl with EXACT BYTES and reports every
// warning `load_skills` produces, in emission order.
//
// The handler goes on the ROOT logger, not on "skills": half of these lines
// come from `jsonl_utils` (the announced reader, fired mid-read) and half
// from `skills` (fired inside and after the loop). Capturing one module
// would show a consistent-looking subset and hide the interleaving, which is
// the only part a port can get wrong by construction.
//
// `%r` on the name and the two `[:12]` hash slices are the reason this is a
// differential rather than a Go-side table: they are CPython spellings, and
// a port that guesses them produces a warning that reads right and greps
// wrong beside the Python runtime writing into the same operator log.
const pyLoadWarnSrc = `
import json, logging, sys
import skills
from orch_items import memory_dir

_seen = []
class _Cap(logging.Handler):
    def emit(self, r):
        _seen.append(r.getMessage())
root = logging.getLogger()
root.addHandler(_Cap())
root.setLevel(logging.WARNING)

_argv = json.loads(sys.argv[1])
p = memory_dir() / "skills.jsonl"
p.parent.mkdir(parents=True, exist_ok=True)
# A LIST OF BYTE VALUES, not a string: one fixture carries a byte that is
# not valid UTF-8, and a JSON string cannot transport it.
p.write_bytes(bytes(_argv["body"]))

loaded = skills.load_skills()
print(json.dumps({
    "warnings": list(_seen),
    "ids": [s.id for s in loaded],
}, sort_keys=True))
`

// tamper rewrites one field of a stored row, leaving it valid JSON and still
// admissible as a Skill — the shape a content_hash check exists to catch.
func tamper(t *testing.T, line string, set map[string]any) string {
	t.Helper()
	var d map[string]any
	if err := json.Unmarshal([]byte(line), &d); err != nil {
		t.Fatal(err)
	}
	for k, v := range set {
		d[k] = v
	}
	b, err := json.Marshal(d)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// storedRow is one skill as the port's own writer emits it, so a fixture
// built on top of it starts from a row BOTH runtimes admit. Building the row
// by hand instead would risk a fixture that CPython rejects for a reason the
// test is not about, and a differential over two rejections agrees while
// measuring nothing (lens 1).
func storedRow(t *testing.T, id, name string) string {
	t.Helper()
	s := base(id, name)
	ws := t.TempDir()
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(skillsPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimRight(string(raw), "\n")
}

func TestLoadSkillsWarningsMatchCPython(t *testing.T) {
	ok := storedRow(t, "ok", "Fine")
	tamperedName := tamper(t, storedRow(t, "t1", "Tampered"),
		map[string]any{"name": "Renamed After Stamping"})
	// A name CPython's %r must quote-switch on, and a non-ASCII one. repr()
	// picks the quote character from the content and does not escape
	// printable non-ASCII; a port reaching for %q gets both wrong.
	quoted := tamper(t, storedRow(t, "t2", `it's "quoted"`),
		map[string]any{"description": "moved"})
	unicoded := tamper(t, storedRow(t, "t3", "naïve — ünicode"),
		map[string]any{"description": "moved"})
	// A stored hash SHORTER than 12, and one whose first 12 code points are
	// multi-byte. Both go through Python's `[:12]`, which counts code
	// points; a byte slice cuts the second one mid-rune.
	shortHash := tamper(t, storedRow(t, "t4", "Short"),
		map[string]any{"content_hash": "abc"})
	wideHash := tamper(t, storedRow(t, "t5", "Wide"),
		map[string]any{"content_hash": strings.Repeat("é", 20)})

	cases := []struct {
		name  string
		lines []string
	}{
		{"a clean store says nothing", []string{ok}},
		{"a blank frame is not a record", []string{ok, "", "   ", ok}},
		{"a torn byte", []string{ok, "{\"id\":\"torn\",\"name\":\"\xff\"}"}},
		{"not JSON at all", []string{ok, "{not json"}},
		// A clean JSON array: the reader's bucket for it is "non-dict" and
		// NOT "malformed", and the two are different operator problems.
		{"valid JSON of the wrong shape", []string{ok, `[1, 2]`}},
		{"all three buckets at once", []string{
			ok, "{\"id\":\"a\",\"name\":\"\xff\"}", "{not json", `"a string"`}},
		{"schema drift", []string{ok, `{"id":"drift","name":123}`}},
		// Loss AND drift: two sentences from two modules, and the order
		// between them is the reader's line first because it fires during
		// the read. A port that assembles both at the end can get the
		// content right and the order wrong.
		{"loss and drift together", []string{
			ok, "{not json", `{"id":"drift","name":123}`}},
		{"a tampered row", []string{ok, tamperedName}},
		{"a name repr must quote-switch", []string{quoted}},
		{"a non-ASCII name", []string{unicoded}},
		{"a stored hash shorter than the slice", []string{shortHash}},
		{"a stored hash of multi-byte runes", []string{wideHash}},
		// Two tampered rows: Python warns inside `for d in reversed(rows)`,
		// so the LAST row in the file is announced first. The port iterates
		// the same direction; nothing else in the suite would notice if it
		// stopped.
		{"two tampered rows announce newest first", []string{
			tamperedName, quoted}},
		// Every kind of loss in one store, so the three-way ORDER across
		// modules is pinned and not just each sentence on its own.
		{"reader loss, tampering and drift", []string{
			"{not json", tamperedName, `{"id":"drift","name":123}`}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body := strings.Join(tc.lines, "\n") + "\n"
			pyWS, goWS := t.TempDir(), t.TempDir()
			if err := os.MkdirAll(filepath.Dir(skillsPath(goWS)), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(skillsPath(goWS), []byte(body), 0o644); err != nil {
				t.Fatal(err)
			}
			bytesArg := make([]int, 0, len(body))
			for i := 0; i < len(body); i++ {
				bytesArg = append(bytesArg, int(body[i]))
			}
			var want struct {
				Warnings []string `json:"warnings"`
				IDs      []string `json:"ids"`
			}
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyLoadWarnSrc, &want,
				pyprobe.Arg(t, map[string]any{"body": bytesArg}))

			load := LoadSkills(goWS)
			var gotIDs []string
			for _, s := range load.Skills {
				gotIDs = append(gotIDs, s.ID)
			}
			if strings.Join(gotIDs, ",") != strings.Join(want.IDs, ",") {
				t.Errorf("skills: go %v, CPython %v", gotIDs, want.IDs)
			}
			got := normWarnPaths(load.Announce(), goWS)
			wantW := normWarnPaths(want.Warnings, pyWS)
			if len(got) != len(wantW) {
				t.Fatalf("warning COUNT: go %d %#v, CPython %d %#v",
					len(got), got, len(wantW), wantW)
			}
			for i := range got {
				if got[i] != wantW[i] {
					t.Errorf("warning %d:\n go: %s\n py: %s", i, got[i], wantW[i])
				}
			}
		})
	}
}

// normWarnPaths replaces the workspace prefix so two runs of the same store
// under two temp dirs compare. The path is REPLACED and not stripped: it is
// part of every one of these sentences, and a port that dropped it would
// otherwise pass here while telling an operator which corpus is short
// without telling them which file.
func normWarnPaths(ws []string, dir string) []string {
	out := make([]string, 0, len(ws))
	for _, w := range ws {
		out = append(out, strings.ReplaceAll(w, dir, "<WS>"))
	}
	return out
}
