package pypath

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
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

	raw := pyprobe.Probe{Stdlib: true}.Run(
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
		// The shapes artifactcheck reaches now that its own second
		// implementation is gone (r4). Its normaliser answered "/x/a" for
		// the first of these and "" for the last; CPython answers "//x/a"
		// and ".", and "." vs "" is the difference between a path that
		// exists and one that cannot be opened.
		{"//x", "a"},
		{"///x", "a"},
		{"", ""},
		{"", "a"},
		{"a", ""},
		{".", "a"},
		{"a/.", "b"},
		{"/a/b", ".."},
	}

	var want []struct {
		OK    bool   `json:"ok"`
		Value string `json:"value"`
		Cls   string `json:"cls"`
		Msg   string `json:"msg"`
	}
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyJoinSrc, &want,
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

// pyRealpathSrc builds a symlink farm and asks CPython to resolve each
// entry. The farm is built on the PYTHON side and read by both, so the two
// runtimes are answering about the same inodes rather than about two
// separately-constructed approximations of the same shape.
const pyRealpathSrc = `
import json, os, os.path, sys

d = sys.argv[1]
# a two-cycle, a self-link, a relative two-cycle, a chain that ENTERS a
# three-cycle, a 60-deep chain, a dangling link, and a plain file.
os.symlink(os.path.join(d, "b"), os.path.join(d, "a"))
os.symlink(os.path.join(d, "a"), os.path.join(d, "b"))
os.symlink(os.path.join(d, "c"), os.path.join(d, "c"))
os.symlink("e", os.path.join(d, "f"))
os.symlink("f", os.path.join(d, "e"))
for x, y in (("g", "h"), ("h", "i"), ("i", "g")):
    os.symlink(os.path.join(d, y), os.path.join(d, x))
os.symlink(os.path.join(d, "g"), os.path.join(d, "start"))
for i in range(60):
    os.symlink(os.path.join(d, "L%d" % (i + 1)), os.path.join(d, "L%d" % i))
os.symlink(os.path.join(d, "nowhere"), os.path.join(d, "dangling"))
open(os.path.join(d, "plain"), "w").close()

out = {}
for n in ("a", "b", "c", "e", "f", "g", "start", "L0", "dangling", "plain"):
    out[n] = os.path.realpath(os.path.join(d, n), strict=False)
print(json.dumps(out))
`

// TestRealpathMatchesCPythonOnCyclesAndDeepChains pins the arm round 8
// found untested — and it was untested AND wrong, in both directions.
//
// os.path.realpath is pure Python walking lstat/readlink, so the kernel's
// ELOOP never applies to it: there is no depth limit at all, and a cycle is
// detected with a seen dict whose strict=False answer is the path at which
// the repeat occurred. This port had a 40-hop counter that refused both.
// A refusal is not conservative here — every caller reads it as "no such
// path" and skips work CPython does.
func TestRealpathMatchesCPythonOnCyclesAndDeepChains(t *testing.T) {
	dir := t.TempDir()
	var want map[string]string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyRealpathSrc, &want, dir)
	if len(want) != 10 {
		t.Fatalf("the probe answered %d entries, want 10", len(want))
	}

	// Anti-vacuity from the CPython side: if the farm ever stops being a
	// farm — a symlink call that silently no-ops, a tmpdir that is itself
	// a link — every entry would resolve to itself and the whole table
	// would agree for the wrong reason.
	moved := 0
	for n, got := range want {
		if got != dir+"/"+n {
			moved++
		}
	}
	if moved < 3 {
		t.Fatalf("only %d of 10 entries resolved to a DIFFERENT path; the "+
			"symlink farm is not a farm: %v", moved, want)
	}

	for n, w := range want {
		got, ok := Realpath(dir + "/" + n)
		if !ok {
			t.Errorf("Realpath(%s) refused; CPython answers %q", n, w)
			continue
		}
		if got != w {
			t.Errorf("Realpath(%s)\n go: %q\n py: %q", n, got, w)
		}
	}
}

// pyRealpathTableSrc is the SECOND Realpath probe, and it exists because
// the first one asks only about single-component paths under a directory
// that already exists. Every entry in that farm is `<dir>/<name>`, so it
// could not see either of the two things this one is about:
//
//   - a MISSING intermediate component. CPython keeps resolving lexically
//     past it; a resolver built on filepath.EvalSymlinks cannot, because
//     EvalSymlinks fails outright on a path that is not there.
//   - `..` AFTER a symlink. CPython follows the link and then pops the
//     resolved path, so a link pointing DEEPER than itself answers its
//     target's parent. Anything that cleans the path first — filepath.Abs
//     does, and so does filepath.Clean — pops the LINK's parent instead
//     and lands somewhere else entirely.
//
// Both matter here and not only in the abstract: config.workspace_root()
// is `Path(val).expanduser().resolve()` over an operator-supplied
// MARO_WORKSPACE, which is exactly a path that may not exist yet and may
// be reached through a symlink.
//
// (Measured first: Path.resolve() and os.path.realpath(strict=False)
// agree on all 24 rows of this table on 3.14.3, so the probe may ask the
// cheaper of the two.)
const pyRealpathTableSrc = `
import json, os, os.path, sys

root = sys.argv[1]
os.makedirs(os.path.join(root, "real", "deep"), exist_ok=True)
open(os.path.join(root, "real", "deep", "file.txt"), "w").close()

def link(name, target):
    p = os.path.join(root, name)
    if not os.path.islink(p) and not os.path.exists(p):
        os.symlink(target, p)

link("link_to_real", os.path.join(root, "real"))
link("link_to_missing", os.path.join(root, "nope"))
link("deeplink", os.path.join(root, "real", "deep"))
link("updown", "../real/deep")
link("loop_a", os.path.join(root, "loop_b"))
link("loop_b", os.path.join(root, "loop_a"))
# A link whose target passes THROUGH a directory that does not exist, and
# a dangling link with real components after it -- the middle-of-path
# shapes the single-component farm cannot express.
link("via_missing", os.path.join(root, "gone", "onward"))
link("dangle_mid", os.path.join(root, "nowhere"))

os.chdir(sys.argv[2])
print(json.dumps([os.path.realpath(p, strict=False)
                  for p in json.loads(sys.argv[3])]))
`

func TestRealpathMatchesCPythonOnMissingComponentsAndDotDot(t *testing.T) {
	root := t.TempDir()
	// The probe chdirs here for the relative rows; the Go side chdirs to
	// the same place, so "relative to cwd" means one thing in this test.
	cwd := filepath.Join(root, "real")

	type row struct{ name, in string }
	rows := []row{
		{"an absolute existing dir", root + "/real"},
		{"an absolute missing dir", root + "/missing"},
		{"a missing dir several levels deep", root + "/missing/a/b"},
		{"a relative path", "./relws"},
		{"a bare relative name", "relws"},
		{"a parent-relative path", "../relws"},
		{"dot segments through an existing dir", root + "/real/deep/../other"},
		{"dot segments through a missing dir", root + "/gone/../other"},
		{"a symlink to a directory", root + "/link_to_real"},
		{"a symlink to a missing target", root + "/link_to_missing"},
		{"a symlink with a suffix under it", root + "/link_to_real/deep"},
		{"a trailing slash", root + "/real/"},
		{"a doubled slash", root + "//real"},
		{"a symlink loop", root + "/loop_a"},
		{"a path through a FILE", root + "/real/deep/file.txt/under"},
		{"the root directory", "/"},
		{"a single dot", "."},
		// The discriminating pair. deeplink -> <root>/real/deep, so
		// follow-then-pop answers <root>/real while clean-then-follow
		// answers <root>.
		{"dot-dot after a symlink pointing deeper", root + "/deeplink/.."},
		{"dot-dot twice after that symlink", root + "/deeplink/../.."},
		{"a relative symlink target with its own dot-dot", root + "/updown"},
		{"more dot-dots than components", root + "/../../../../../../../../.."},
		{"dot-dot at the filesystem root", "/.."},
		{"dot-dot through a symlink to a missing target", root + "/link_to_missing/.."},
		{"an empty component in the middle", root + "/real//deep"},
		{"a single-dot component in the middle", root + "/real/./deep"},
		{"a leading double slash", "//" + strings.TrimPrefix(root, "/") + "/real"},
		{"the empty string", ""},
		{"a symlink whose target passes through a missing dir", root + "/via_missing"},
		{"a dangling symlink with components after it", root + "/dangle_mid/a/b"},
		{"dot-dot after a dangling mid-path symlink", root + "/dangle_mid/a/.."},
	}

	ins := make([]string, len(rows))
	for i, r := range rows {
		ins[i] = r.in
	}
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyRealpathTableSrc, &want,
		root, cwd, pyprobe.Arg(t, ins))
	if len(want) != len(rows) {
		t.Fatalf("probe answered %d rows, want %d", len(want), len(rows))
	}

	// Anti-vacuity, from CPython's own answers: this table is worthless
	// unless the two discriminating rows actually landed where
	// follow-then-pop puts them. If the farm failed to build, deeplink is
	// not a link, and both rows would come back as the lexical answer —
	// agreeing with a wrong implementation.
	byName := map[string]string{}
	for i, r := range rows {
		byName[r.name] = want[i]
	}
	for _, check := range []struct{ name, want string }{
		{"dot-dot after a symlink pointing deeper", root + "/real"},
		{"dot-dot twice after that symlink", root},
		{"a missing dir several levels deep", root + "/missing/a/b"},
	} {
		// Looked up by NAME, not by index. An earlier version pinned
		// positions 17 and 18, which silently stops guarding the moment a
		// row is inserted above them — and rows were inserted above them.
		if got, ok := byName[check.name]; !ok || got != check.want {
			t.Fatalf("the fixture tree did not produce the case this test is "+
				"about: %q -> %q (want %q)", check.name, got, check.want)
		}
	}

	t.Chdir(cwd)
	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			got, ok := Realpath(r.in)
			if !ok {
				t.Fatalf("Realpath(%q) refused; CPython answers %q", r.in, want[i])
			}
			if got != want[i] {
				t.Errorf("Realpath(%q)\n go: %q\n py: %q", r.in, got, want[i])
			}
		})
	}
}

// fsSortSrc is `sorted()` over names as CPython holds them — every byte
// that is not valid UTF-8 decoded to the lone surrogate 0xDC00+b by the
// filesystem encoding's surrogateescape handler.
//
// The corpus arrives as lists of BYTE VALUES rather than as strings, and
// the obstacle is the GO end. This note used to blame json.dumps; measured,
// json.dumps("\udc80b") returns "\udc80b" under the default
// ensure_ascii=True and json.loads round-trips it exactly. Go's
// encoding/json is what cannot: it decodes `\udc80` to U+FFFD (ef bf bd)
// and reports no error, so a probe that took strings would be comparing
// laundered input against the very rule it is measuring.
const fsSortSrc = `
import json, os, sys
vals = json.loads(sys.argv[1])
names = [os.fsdecode(bytes(v)) for v in vals]
order = [list(os.fsencode(n)) for n in sorted(names)]
less = [[bool(a < b) for b in names] for a in names]
sys.stdout.write(json.dumps({"order": order, "less": less}))
`

// TestFSLessOrdersNamesTheWayCPythonSortsThem pins FSDecode and FSLess
// against the interpreter over every ordered pair in a corpus.
//
// `sort.Strings` compares raw bytes; CPython compares the surrogateescape
// DECODING code point by code point. The two agree for all valid UTF-8,
// and they agree for a bad byte against ASCII and against an astral
// character, which is why the divergence survived three review rounds of
// artifactcheck.FilesModifiedSince. They part in the two-byte range:
// against `é` CPython compares 0xDC80 to 0x00E9 and Go compares 0x80 to
// 0xC3, and the answers are opposite.
//
// The whole pair matrix is driven, not just the sorted order, because the
// sorted order of a corpus can be right while the comparator is wrong —
// the length tie-break (a name that is a strict PREFIX of another) is
// reachable from `sorted()` only when the two land adjacent, and a
// mutation battery found exactly that gap: FSLess returning false for
// every prefix pair passed the end-to-end fixtures.
func TestFSLessOrdersNamesTheWayCPythonSortsThem(t *testing.T) {
	raw := [][]byte{
		[]byte("zz.txt"),
		[]byte("zz.tx"),                 // a strict prefix of the row above
		[]byte("zz.txt2"),               // and a strict extension of it
		[]byte("a"),                     // the prefix rule again, one byte long
		[]byte("ab"),                    //
		{'a', 0x80},                     // a prefix plus an undecodable byte: 0xDC80
		{'a', 0xC3, 0xA9},               // a prefix plus `é`: 0x00E9, so BELOW the row
		{0x80, 'z', '.', 't', 'x', 't'}, // above `é` for CPython, below for bytes
		{0x80, 'z'},                     // its prefix
		{0xFF, 'q'},                     // 0xDCFF, the top of the escape range
		{0xC3, 0xA9, 'a'},               // "éa"
		{0xC3, 0xA9},                    // its prefix
		{0xE1, 0x80, 0x80, 'b'},         // U+1000 + "b": astral-ish, above 0xDCFF
		{0xF0, 0x9F, 0x98, 0x80},        // U+1F600, above every surrogate
		{},                              // the empty name: a prefix of everything
	}
	vals := make([][]int, len(raw))
	for i, b := range raw {
		row := make([]int, len(b))
		for j := range b {
			row[j] = int(b[j])
		}
		vals[i] = row
	}
	names := make([]string, len(raw))
	for i, b := range raw {
		names[i] = string(b)
	}

	out := pyprobe.Probe{Stdlib: true}.Run(t, fsSortSrc, pyprobe.Arg(t, vals))
	var want struct {
		Order [][]int  `json:"order"`
		Less  [][]bool `json:"less"`
	}
	if err := json.Unmarshal([]byte(out), &want); err != nil {
		t.Fatalf("decoding the probe output: %v\nraw: %s", err, out)
	}
	if len(want.Order) != len(names) || len(want.Less) != len(names) {
		t.Fatalf("%d/%d rows for %d names", len(want.Order), len(want.Less), len(names))
	}

	// The guard: a corpus on which byte order and code-point order agree
	// would pass this test against `sort.Strings`, and would therefore
	// assert nothing. Refuse to run silently in that state.
	byteOrder := append([]string(nil), names...)
	sort.Strings(byteOrder)
	pyOrder := make([]string, len(want.Order))
	for i, row := range want.Order {
		b := make([]byte, len(row))
		for j, v := range row {
			b[j] = byte(v)
		}
		pyOrder[i] = string(b)
	}
	if strings.Join(byteOrder, "\x01") == strings.Join(pyOrder, "\x01") {
		t.Fatal("sort.Strings and CPython agree on this corpus, so the " +
			"corpus cannot tell a byte-order port from a correct one")
	}

	diffs := 0
	for i := range names {
		for j := range names {
			if got := FSLess(names[i], names[j]); got != want.Less[i][j] {
				t.Errorf("FSLess(%q, %q) = %v, CPython %v", names[i], names[j], got, want.Less[i][j])
			}
			if (names[i] < names[j]) != want.Less[i][j] {
				diffs++
			}
		}
	}
	if diffs == 0 {
		t.Error("no pair in the matrix separates code-point order from byte order")
	}

	got := append([]string(nil), names...)
	sort.SliceStable(got, func(i, j int) bool { return FSLess(got[i], got[j]) })
	if strings.Join(got, "\x01") != strings.Join(pyOrder, "\x01") {
		t.Errorf("sorted order\n go: %q\n py: %q", got, pyOrder)
	}
}
