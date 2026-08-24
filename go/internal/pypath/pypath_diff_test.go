package pypath

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

const pyPathSrc = `
import json, sys
from pathlib import PurePosixPath, Path

out = []
for s in json.loads(sys.argv[1]):
    p = PurePosixPath(s)
    row = {"name": p.name, "str": str(p),
           "suffix": PurePosixPath(p.name).suffix,
           "stem": PurePosixPath(p.name).stem}
    try:
        row["expanded"] = str(Path(str(p)).expanduser())
    except RuntimeError as exc:
        row["expanded"] = None
        row["raised"] = str(exc)
    out.append(row)
sys.stdout.write(json.dumps(out))
`

// TestThePathlibHelpersMatchCPython pins pypath.go against the interpreter
// directly, on the shapes the envelope fixtures cannot reach.
//
// This exists because a mutation read of the FILE found two rules whose
// distinguishing inputs no differential in this package drives. Every
// attachment name in those tables is a real filename on a real disk, so
// `a/.`, `f.` and `..f` never appear — and both `Name -> filepath.Base`
// and `Suffix -> the CPython 3.13 rule` are mutations the whole
// dispatch suite passes. A helper is only pinned where its inputs go.
//
// The interpreter is the oracle rather than a table of expected strings,
// because the 3.13/3.14 suffix change is precisely the kind of thing a
// hand-written table records once and then stops tracking.
func TestThePathlibHelpersMatchCPython(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)

	inputs := []string{
		// The `name` rule: trailing "." components are dropped and ".." is not.
		"a/.", "a/..", "a/./.", "a//", "a/b/", ".", "..", "", "/", "//", "///",
		// The `str` rule, including the POSIX double-slash root.
		"/a//b", "//a/b", "///a/b", "./a/./b", "a/./b//c",
		// The suffix/stem rule. `f.` and `..f` are the two shapes where
		// CPython 3.13 and 3.14 disagree; the rest are the ordinary cases
		// that must keep working while those two are pinned.
		"f.", "..f", ".f", "...", ".hidden", "a.tar.gz", "noext",
		"a.", ".a.b", "x..y", "..", "f..",
		// expanduser: the passthrough, the two tilde forms, and the raise.
		"plain/path", "~", "~/", "~//a", "~/a/../b", "~~", "~nosuchuser-maro-goport/x",
	}

	raw := pyprobe.Probe{Marker: "dispatch_envelope.py"}.Run(
		t, pyPathSrc, pyprobe.Arg(t, inputs))
	var want []struct {
		Name     string  `json:"name"`
		Str      string  `json:"str"`
		Suffix   string  `json:"suffix"`
		Stem     string  `json:"stem"`
		Expanded *string `json:"expanded"`
		Raised   string  `json:"raised"`
	}
	if err := json.Unmarshal([]byte(raw), &want); err != nil {
		t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
	}

	// The probe inherits this process's HOME, and the assertions below are
	// worthless if it did not: every tilde row would compare one runtime's
	// real home against the other's.
	if len(want) < len(inputs) {
		t.Fatalf("%d rows for %d inputs", len(want), len(inputs))
	}
	if e := want[len(inputs)-6].Expanded; e == nil || *e != home {
		t.Fatalf("the probe expanded `~` to %v, not the fixture's HOME %q; "+
			"the tilde rows are comparing two different homes", e, home)
	}
	if os.Getenv("HOME") != home {
		t.Fatalf("HOME is %q, not %q", os.Getenv("HOME"), home)
	}

	raisedSeen := 0
	for i, s := range inputs {
		w := want[i]
		if got := Name(s); got != w.Name {
			t.Errorf("Name(%q) = %q, CPython %q", s, got, w.Name)
		}
		if got := Str(s); got != w.Str {
			t.Errorf("Str(%q) = %q, CPython %q", s, got, w.Str)
		}
		// suffix and stem are asked of the NAME, which is how the envelope
		// calls them: `Path(base).stem` where base is already a bare name.
		if got := Suffix(w.Name); got != w.Suffix {
			t.Errorf("Suffix(%q) = %q, CPython %q", w.Name, got, w.Suffix)
		}
		if got := Stem(w.Name); got != w.Stem {
			t.Errorf("Stem(%q) = %q, CPython %q", w.Name, got, w.Stem)
		}
		got, err := ExpandUser(Str(s))
		if w.Expanded == nil {
			raisedSeen++
			if err == nil {
				t.Errorf("ExpandUser(%q) = %q; CPython raised %q", s, got, w.Raised)
			} else if err.Error() != w.Raised {
				t.Errorf("ExpandUser(%q) raised %q, CPython %q", s, err.Error(), w.Raised)
			}
			continue
		}
		if err != nil {
			t.Errorf("ExpandUser(%q): %v; CPython answered %q", s, err, *w.Expanded)
			continue
		}
		if got != *w.Expanded {
			t.Errorf("ExpandUser(%q) = %q, CPython %q", s, got, *w.Expanded)
		}
	}
	if raisedSeen < 2 {
		t.Errorf("only %d input(s) reached expanduser's RuntimeError; the "+
			"table is meant to drive both `~~` and an unknown user", raisedSeen)
	}
}

// pyJoinSrc is `PurePosixPath(base) / rhs`, which is the operation
// `task_path` and `_archive_dir() / ...` are built from.
const pyJoinSrc = `
import json, sys
from pathlib import PurePosixPath

out = []
for base, rhs in json.loads(sys.argv[1]):
    try:
        out.append({"ok": True, "value": str(PurePosixPath(base) / rhs)})
    except BaseException as e:
        out.append({"ok": False, "cls": type(e).__name__, "msg": str(e)})
sys.stdout.write(json.dumps(out))
`

// TestJoinMatchesCPython pins the one rule that separates pathlib's `/`
// from filepath.Join: an absolute right-hand side REPLACES the left one.
//
// The rule reaches a durable surface. `task_path(job_id)` is this join, and
// `job_id` arrives from `blocked_by` — a field any foreign producer writes.
// CPython opens /etc/passwd.json for a dependency named "/etc/passwd"; a
// port using filepath.Join opens a file inside the workspace, finds nothing,
// and blocks a task CPython claims.
func TestJoinMatchesCPython(t *testing.T) {
	const base = "/ws/output/queues/tasks"
	pairs := [][2]string{
		// The ordinary case, and the one that differs.
		{base, "plain.json"},
		{base, "/etc/passwd.json"},
		// POSIX keeps exactly two leading slashes and collapses more.
		{base, "//x/y.json"},
		{base, "///x/y.json"},
		// NOT cleaned: pathlib keeps the dots, filepath.Join removes them.
		// Both spellings open the same file; the difference is lexical and
		// it reaches the lock path and the temp-file directory.
		{base, "../../x.json"},
		{base, "./x.json"},
		{base, "a/b.json"},
		// The shapes a job id can degenerate to: f"{''}.json" is ".json",
		// f"{None}.json" is "None.json", f"{5}.json" is "5.json".
		{base, ".json"},
		{base, "None.json"},
		{base, "5.json"},
		{base, "..json"},
		// A trailing slash on the base, and a relative base.
		{base + "/", "plain.json"},
		{"rel/dir", "plain.json"},
		{"rel/dir", "/abs.json"},
		{"/", "plain.json"},
		{"//", "plain.json"},
	}

	var want []struct {
		OK    bool   `json:"ok"`
		Value string `json:"value"`
		Cls   string `json:"cls"`
		Msg   string `json:"msg"`
	}
	pyprobe.Probe{Marker: "task_store.py"}.RunJSON(t, pyJoinSrc, &want,
		pyprobe.Arg(t, pairs))
	if len(want) != len(pairs) {
		t.Fatalf("probe answered %d rows for %d pairs", len(want), len(pairs))
	}
	// A table where every row is the same answer is a table that proves
	// nothing: at least one pair must ESCAPE the base, or the rule under
	// test is not being exercised.
	escaped := 0
	for i, p := range pairs {
		w := want[i]
		if !w.OK {
			t.Fatalf("Join(%q, %q): CPython raised %s(%s) — the fixture set "+
				"assumes every pair joins", p[0], p[1], w.Cls, w.Msg)
		}
		if got := Join(p[0], p[1]); got != w.Value {
			t.Errorf("Join(%q, %q) = %q, CPython %q", p[0], p[1], got, w.Value)
		}
		if !strings.HasPrefix(w.Value, base) {
			escaped++
		}
	}
	if escaped == 0 {
		t.Fatal("no fixture left the base directory; the absolute-rhs rule " +
			"is not under test")
	}
}
