package dispatch

import (
	"encoding/json"
	"os"
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
// `a/.`, `f.` and `..f` never appear — and both `pathName -> filepath.Base`
// and `pathSuffix -> the CPython 3.13 rule` are mutations the whole
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
		if got := pathName(s); got != w.Name {
			t.Errorf("pathName(%q) = %q, CPython %q", s, got, w.Name)
		}
		if got := pathStr(s); got != w.Str {
			t.Errorf("pathStr(%q) = %q, CPython %q", s, got, w.Str)
		}
		// suffix and stem are asked of the NAME, which is how the envelope
		// calls them: `Path(base).stem` where base is already a bare name.
		if got := pathSuffix(w.Name); got != w.Suffix {
			t.Errorf("pathSuffix(%q) = %q, CPython %q", w.Name, got, w.Suffix)
		}
		if got := pathStem(w.Name); got != w.Stem {
			t.Errorf("pathStem(%q) = %q, CPython %q", w.Name, got, w.Stem)
		}
		got, err := expandUser(pathStr(s))
		if w.Expanded == nil {
			raisedSeen++
			if err == nil {
				t.Errorf("expandUser(%q) = %q; CPython raised %q", s, got, w.Raised)
			} else if err.Error() != w.Raised {
				t.Errorf("expandUser(%q) raised %q, CPython %q", s, err.Error(), w.Raised)
			}
			continue
		}
		if err != nil {
			t.Errorf("expandUser(%q): %v; CPython answered %q", s, err, *w.Expanded)
			continue
		}
		if got != *w.Expanded {
			t.Errorf("expandUser(%q) = %q, CPython %q", s, got, *w.Expanded)
		}
	}
	if raisedSeen < 2 {
		t.Errorf("only %d input(s) reached expanduser's RuntimeError; the "+
			"table is meant to drive both `~~` and an unknown user", raisedSeen)
	}
}
