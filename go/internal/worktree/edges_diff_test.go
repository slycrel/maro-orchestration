package worktree

import (
	"encoding/hex"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// ---------------------------------------------------------------------
// text=True's strict decode
// ---------------------------------------------------------------------

const pyDecodeSrc = `
import binascii, json, sys
out = []
for h in json.loads(sys.argv[1]):
    b = binascii.unhexlify(h)
    try:
        b.decode("utf-8")
        out.append(None)
    except UnicodeDecodeError as e:
        out.append(str(e))
print(json.dumps(out))
`

// TestTheStrictDecodeErrorMatchesCPython sweeps the decoder's THREE
// verdicts, which Go's own decoder cannot tell apart: every one-byte
// input, every two-byte input whose first byte is not ASCII, and a
// curated set of three- and four-byte ones.
//
// This matters because `text=True` decodes strictly and
// UnicodeDecodeError is a ValueError — which is in NONE of worktree.py's
// nine `except (OSError, subprocess.SubprocessError)` clauses. A git
// invocation whose output is not UTF-8 therefore propagates out of
// provision() rather than becoming a nil worktree, and getting the
// CLASSIFICATION wrong is what would let the port swallow it.
func TestTheStrictDecodeErrorMatchesCPython(t *testing.T) {
	var inputs [][]byte
	for b := 0; b < 256; b++ {
		inputs = append(inputs, []byte{byte(b)})
	}
	// Every non-ASCII lead byte crossed with the BOUNDARIES of every
	// second-byte range seqShape distinguishes (0x80/0x8f/0x90/0x9f/
	// 0xa0/0xbf are the four narrowed leads' edges) plus the values just
	// outside the continuation range. The full 128x256 cross was the
	// first draft and it exceeded ARG_MAX; the boundaries are where the
	// verdict can change, and hitting all of them is what the sweep is
	// for. Nothing between two boundaries can differ from its neighbours.
	seconds := []byte{0x00, 0x41, 0x7f, 0x80, 0x81, 0x8e, 0x8f, 0x90, 0x91,
		0x9e, 0x9f, 0xa0, 0xa1, 0xbe, 0xbf, 0xc0, 0xc2, 0xe0, 0xf4, 0xff}
	for a := 0x80; a < 0x100; a++ {
		for _, b := range seconds {
			inputs = append(inputs, []byte{byte(a), b})
		}
	}
	for _, extra := range [][]byte{
		{0xe0, 0x80, 0x80}, {0xe0, 0xa0, 0x80}, {0xed, 0xa0, 0x80}, {0xed, 0x9f, 0xbf},
		{0xf0, 0x80, 0x80, 0x80}, {0xf0, 0x90, 0x80, 0x80}, {0xf4, 0x90, 0x80, 0x80},
		{0xf4, 0x8f, 0xbf, 0xbf}, {'a', 'b', 'c', 0xe2, 0x82}, {'a', 0xff, 'b'},
		{0xe2, 0x82, 0xac, 0xff}, {0xc2}, {0xe0}, {0xf0}, {0xf0, 0x9f}, {0xf0, 0x9f, 0x98},
		{'o', 'k', '\r', '\n', 0xc3},
	} {
		inputs = append(inputs, extra)
	}

	hexes := make([]string, len(inputs))
	for i, b := range inputs {
		hexes[i] = hex.EncodeToString(b)
	}
	var want []*string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyDecodeSrc, &want, pyprobe.Arg(t, hexes))
	if len(want) != len(inputs) {
		t.Fatalf("probe returned %d rows for %d inputs", len(want), len(inputs))
	}

	// The CLAIM: CPython produces all three reasons, and it accepts some
	// inputs. A sweep in which every row failed the same way would pass a
	// port that only knows one message.
	seen := map[string]int{}
	for _, w := range want {
		switch {
		case w == nil:
			seen["ok"]++
		case strings.HasSuffix(*w, "invalid start byte"):
			seen["start"]++
		case strings.HasSuffix(*w, "invalid continuation byte"):
			seen["cont"]++
		case strings.HasSuffix(*w, "unexpected end of data"):
			seen["end"]++
		default:
			t.Fatalf("CPython produced a reason this test does not know: %q", *w)
		}
	}
	for _, k := range []string{"ok", "start", "cont", "end"} {
		if seen[k] < 5 {
			t.Fatalf("only %d rows in class %q — the sweep is not exercising "+
				"the three-way split (%v)", seen[k], k, seen)
		}
	}

	bad := 0
	for i, in := range inputs {
		got, err := decodeText(in)
		if want[i] == nil {
			if err != nil {
				t.Errorf("%x: CPython decoded it, Go raised %v", in, err)
			} else if got != translatedString(in) {
				t.Errorf("%x: decoded text differs: %q vs %q", in, got, translatedString(in))
			}
			continue
		}
		if err == nil {
			t.Errorf("%x: CPython raised %q, Go decoded it as %q", in, *want[i], got)
			continue
		}
		var pe *PyError
		if !asPyError(err, &pe) || pe.PyClass() != "UnicodeDecodeError" {
			t.Errorf("%x: Go's error is %T/%v, not a UnicodeDecodeError", in, err, err)
			continue
		}
		if pe.Error() != *want[i] {
			bad++
			if bad <= 10 {
				t.Errorf("%x: message differs.\n CPython %s\n Go      %s",
					in, *want[i], pe.Error())
			}
		}
	}
	if bad > 10 {
		t.Errorf("... and %d more message mismatches", bad-10)
	}
}

// translatedString is what decodeText should have produced for input the
// decoder ACCEPTS: the same bytes with universal newlines applied.
func translatedString(b []byte) string {
	s := string(b)
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n")
}

// ---------------------------------------------------------------------
// The sidecar whose name IS the suffix
// ---------------------------------------------------------------------

const pySweepRaiseSrc = `
import json, sys, logging
import worktree
logging.getLogger("maro.worktree").addHandler(logging.NullHandler())
out = {"error": None, "rows": None}
try:
    r = worktree.sweep_stranded_clones(pid_alive=lambda p: False, min_age_s=0)
    out["rows"] = json.dumps(r.as_dict())
except BaseException as exc:
    out["error"] = {"class": type(exc).__name__, "msg": str(exc)}
print(json.dumps(out))
`

// TestASidecarNamedExactlyTheSuffixRaisesInBothRuntimes pins a REACHABLE
// crash in worktree.py, not a hypothetical one.
//
// The glob is `*/*.owner.json` and fnmatch's `*` matches the EMPTY
// string, so a file named literally `.owner.json` is yielded. The next
// line slices its name by `[:-len(suffix)]`, gets "", and hands that to
// `Path.with_name("")`, which raises `ValueError: Invalid name ”` — out
// of the whole sweep, before any clone is examined. heartbeat's blanket
// `except Exception` then swallows it, so the visible symptom is a sweep
// that silently does nothing for as long as the file exists.
//
// The port REPRODUCES it rather than fixing it. Correcting it here would
// be a third behaviour, and the fix decision is not this port's to make.
func TestASidecarNamedExactlyTheSuffixRaisesInBothRuntimes(t *testing.T) {
	build := func(ws string) {
		dir := filepath.Join(ws, "worktrees", "L1")
		if err := os.MkdirAll(dir, 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, ownerSidecarSuffix), []byte("{}"), 0o666); err != nil {
			t.Fatal(err)
		}
		// A perfectly ordinary clone that sorts AFTER it, to show the
		// raise happens before any real work.
		if err := os.MkdirAll(filepath.Join(dir, "z-clone"), 0o777); err != nil {
			t.Fatal(err)
		}
	}
	pyWS, goWS := t.TempDir(), t.TempDir()
	build(pyWS)
	build(goWS)

	var out struct {
		Error map[string]string `json:"error"`
		Rows  *string           `json:"rows"`
	}
	pyprobeWorktree(pyWS).RunJSON(t, pySweepRaiseSrc, &out, pyprobeArg(t, nil))

	// --- the CLAIM, checked first ---
	if out.Error == nil {
		t.Fatalf("CPython did NOT raise — the reachability claim is stale "+
			"(it returned %v). Either pathlib stopped matching the empty "+
			"star or with_name stopped refusing an empty name.", out.Rows)
	}
	if out.Error["class"] != "ValueError" {
		t.Fatalf("CPython raised %s, not ValueError: %v", out.Error["class"], out.Error)
	}

	// --- the comparison ---
	t.Setenv("MARO_WORKSPACE", goWS)
	zero := 0.0
	_, err := SweepStrandedClones(SweepOptions{
		PIDAlive: func(int) bool { return false },
		MinAgeS:  &zero,
	})
	if err == nil {
		t.Fatal("Go swept without raising where CPython raised ValueError")
	}
	var pe *PyError
	if !asPyError(err, &pe) {
		t.Fatalf("Go's error is not classed: %T %v", err, err)
	}
	if pe.PyClass() != out.Error["class"] {
		t.Errorf("class differs: CPython %s, Go %s", out.Error["class"], pe.PyClass())
	}
	if pe.Error() != out.Error["msg"] {
		t.Errorf("message differs.\n CPython %q\n Go      %q", out.Error["msg"], pe.Error())
	}
}

// ---------------------------------------------------------------------
// The glob's two rules that Go's DirEntry gets wrong for free
// ---------------------------------------------------------------------

const pyGlobSrc = `
import json, sys
from pathlib import Path
import worktree
root = worktree._worktrees_root()
print(json.dumps([str(p.relative_to(root))
                  for p in sorted(root.glob("*/*" + worktree._OWNER_SIDECAR_SUFFIX))]))
`

// TestTheSidecarGlobFollowsSymlinksAndSeesHiddenNames pins the two rules
// a transcription of `entry.is_dir()` as `DirEntry.IsDir()` loses.
//
// A symlinked loop directory is a real shape here: worktrees/ is under
// the workspace, and an operator who moves the workspace to another disk
// and leaves a symlink behind would have every leaked clone under it
// become invisible to the Go sweep — clones that hold unmerged work and
// that nothing would ever recover.
func TestTheSidecarGlobFollowsSymlinksAndSeesHiddenNames(t *testing.T) {
	build := func(ws string) {
		root := filepath.Join(ws, "worktrees")
		real := filepath.Join(ws, "elsewhere")
		if err := os.MkdirAll(filepath.Join(real, "a-clone"), 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(real, "a-clone.owner.json"), []byte("{}"), 0o666); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(root, 0o777); err != nil {
			t.Fatal(err)
		}
		// A SYMLINKED loop directory.
		if err := os.Symlink(real, filepath.Join(root, "linked")); err != nil {
			t.Fatal(err)
		}
		// A HIDDEN loop directory holding a HIDDEN clone name.
		hidden := filepath.Join(root, ".hidden")
		if err := os.MkdirAll(filepath.Join(hidden, ".b-clone"), 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(hidden, ".b-clone.owner.json"), []byte("{}"), 0o666); err != nil {
			t.Fatal(err)
		}
		// A plain FILE where a loop directory would be — not descended.
		if err := os.WriteFile(filepath.Join(root, "notadir"), []byte("x"), 0o666); err != nil {
			t.Fatal(err)
		}
		// A directory that is not readable at all — swallowed, not fatal.
		locked := filepath.Join(root, "locked")
		if err := os.MkdirAll(locked, 0o000); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = os.Chmod(locked, 0o777) })
	}
	pyWS, goWS := t.TempDir(), t.TempDir()
	build(pyWS)
	build(goWS)

	var want []string
	pyprobeWorktree(pyWS).RunJSON(t, pyGlobSrc, &want, pyprobeArg(t, nil))

	// --- the CLAIM ---
	claim := []string{".hidden/.b-clone.owner.json", "linked/a-clone.owner.json"}
	if !reflect.DeepEqual(want, claim) {
		t.Fatalf("the claim about CPython's glob is stale.\n want %v\n got  %v", claim, want)
	}

	// --- the comparison ---
	t.Setenv("MARO_WORKSPACE", goWS)
	root := filepath.Join(goWS, "worktrees")
	var got []string
	for _, p := range globTwoLevel(root, ownerSidecarSuffix) {
		rel, err := filepath.Rel(root, p)
		if err != nil {
			t.Fatal(err)
		}
		got = append(got, rel)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("glob differs.\n CPython %v\n Go      %v", want, got)
	}
}

// ---------------------------------------------------------------------
// The merge lock's NAME
// ---------------------------------------------------------------------

const pyLockPathSrc = `
import json, sys
from pathlib import Path
import worktree
print(json.dumps(str(worktree._merge_lock_path(Path(sys.argv[1])))))
`

// TestMergeLockPathMatchesCPython pins _merge_lock_path's two Python
// spellings, neither of which any other fixture reaches: the Unicode
// isalnum over the RESOLVED repo path, and [-100:], which is the LAST
// hundred CODE POINTS.
//
// Both halves decide whether two repos share a lock. A byte slice at 100
// over a non-ASCII path keeps ~50 characters and can cut a rune in half;
// two sibling repos whose paths agree in their last 100 bytes but not
// their last 100 characters would then serialise against each other, and
// a merge would block on an unrelated repo's lock. The tail (not the
// head) is taken on purpose — it is the part that distinguishes two
// checkouts under one parent.
//
// The repo directory is only realpath'd, never written to or run against,
// so both runtimes are given the SAME one: the key is a function of that
// path and comparing two different paths would compare nothing.
func TestMergeLockPathMatchesCPython(t *testing.T) {
	root := t.TempDir()
	// A path whose non-ASCII tail is longer than the 100-code-point cap,
	// so the slice actually cuts and the cut lands inside multi-byte
	// characters.
	deep := filepath.Join(root, "é dossier", strings.Repeat("漢", 40),
		"sub.dir", strings.Repeat("é", 40))
	if err := os.MkdirAll(deep, 0o777); err != nil {
		t.Fatal(err)
	}

	var pyPath string
	pyprobeWorktree(t.TempDir()).RunJSON(t, pyLockPathSrc, &pyPath, deep)

	// --- the CLAIM about CPython, before anything is compared ---
	base := filepath.Base(pyPath)
	if !strings.HasPrefix(base, "merge-") {
		t.Fatalf("CPython's lock file is not the merge- sidecar: %q", base)
	}
	key := strings.TrimPrefix(base, "merge-")
	if n := len([]rune(key)); n != 100 {
		t.Fatalf("the claim that the key is 100 CODE POINTS is stale: %d code "+
			"points, %d bytes — the fixture path may have stopped being long "+
			"enough to cut.\n%q", n, len(key), key)
	}
	if len(key) == len([]rune(key)) {
		t.Fatalf("the claim that the key keeps NON-ASCII characters is stale — "+
			"an ASCII-only key would pass a byte-sliced port too.\n%q", key)
	}

	// --- the comparison ---
	goWS := t.TempDir()
	t.Setenv("MARO_WORKSPACE", goWS)
	got, err := mergeLockPath(deep)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(got) != base {
		t.Errorf("merge lock name differs.\n CPython %q\n Go      %q",
			base, filepath.Base(got))
	}
	if d := filepath.Dir(got); d != filepath.Join(goWS, "worktrees") {
		t.Errorf("the lock does not live in the workspace's worktrees root: %q", d)
	}
}

// ---------------------------------------------------------------------
// The subprocess boundary itself
// ---------------------------------------------------------------------

const pyRunSrc = `
import json, subprocess, sys
out = []
for case in json.loads(sys.argv[1]):
    try:
        r = subprocess.run(case["argv"], capture_output=True, text=True,
                           timeout=case["timeout"])
        out.append({"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr})
    except BaseException as exc:
        out.append({"error": {"class": type(exc).__name__, "msg": str(exc)}})
print(json.dumps(out))
`

type runCase struct {
	Argv    []string `json:"argv"`
	Timeout int      `json:"timeout"`
	why     string
}

type runOut struct {
	RC     int    `json:"rc"`
	Stdout string `json:"stdout"`
	Stderr string `json:"stderr"`
	Error  *struct {
		Class string `json:"class"`
		Msg   string `json:"msg"`
	} `json:"error"`
}

// TestTextModeSubprocessBoundaryMatchesCPython compares execRun against
// subprocess.run on the four things the SCRIPTED differential cannot
// see, because that one replaces this boundary outright:
//
//   - universal newlines — text=True turns CRLF AND a lone CR into \n
//     before any caller sees them, and git writes bare CR progress on
//     every clone;
//   - the strict decode, and the fact that STDOUT is decoded first, so
//     when both pipes are undecodable the exception names the stdout
//     byte;
//   - the two spawn failures, which are OSError subclasses with
//     different classes and different message shapes;
//   - the timeout, whose message carries the argv repr and the integer
//     seconds and lands verbatim in a MergeResult.Detail.
//
// It runs sh, never git, and touches nothing outside t.TempDir().
func TestTextModeSubprocessBoundaryMatchesCPython(t *testing.T) {
	noexec := filepath.Join(t.TempDir(), "noexec")
	if err := os.WriteFile(noexec, []byte("#!/bin/sh\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	cases := []runCase{
		{Argv: []string{"sh", "-c", `printf 'a\r\nb\rc\n'`}, Timeout: 30,
			why: "universal newlines: CRLF and a lone CR both collapse"},
		{Argv: []string{"sh", "-c", `printf 'plain'; printf 'e' >&2; exit 3`}, Timeout: 30,
			why: "a non-zero exit is a returncode, never an exception"},
		{Argv: []string{"sh", "-c", `printf '\377'`}, Timeout: 30,
			why: "undecodable stdout raises UnicodeDecodeError"},
		{Argv: []string{"sh", "-c", `printf 'fine'; printf '\377' >&2`}, Timeout: 30,
			why: "undecodable stderr raises too"},
		{Argv: []string{"sh", "-c", `printf '\376'; printf '\377' >&2`}, Timeout: 30,
			why: "both bad: the message must name the STDOUT byte"},
		{Argv: []string{"definitely-not-a-binary-xyz"}, Timeout: 30,
			why: "a missing executable is FileNotFoundError"},
		{Argv: []string{noexec}, Timeout: 30,
			why: "a non-executable file is PermissionError"},
		{Argv: []string{"sh", "-c", "sleep 5"}, Timeout: 1,
			why: "the timeout raises TimeoutExpired after killing the child"},
	}

	var out []runOut
	pyprobeWorktree(t.TempDir()).RunJSON(t, pyRunSrc, &out, pyprobeArg(t, cases))
	if len(out) != len(cases) {
		t.Fatalf("probe returned %d rows for %d cases", len(out), len(cases))
	}

	// --- the CLAIM about CPython, checked before the port is compared ---
	classes := map[string]int{}
	for _, o := range out {
		if o.Error != nil {
			classes[o.Error.Class]++
		} else {
			classes["ok"]++
		}
	}
	for _, want := range []string{"ok", "UnicodeDecodeError", "FileNotFoundError",
		"PermissionError", "TimeoutExpired"} {
		if classes[want] == 0 {
			t.Fatalf("no case produced %s — the fixture stopped exercising "+
				"that arm.\n%v", want, classes)
		}
	}
	if out[0].Error != nil || out[0].Stdout != "a\nb\nc\n" {
		t.Fatalf("the claim about universal newlines is stale: %+v", out[0])
	}
	if out[1].Error != nil || out[1].RC != 3 || out[1].Stderr != "e" {
		t.Fatalf("the claim that a non-zero exit stays a returncode is stale: %+v", out[1])
	}
	if out[4].Error == nil || !strings.Contains(out[4].Error.Msg, "0xfe") {
		t.Fatalf("the claim that STDOUT is decoded first is stale — the message "+
			"should name 0xfe, not the stderr's 0xff: %+v", out[4])
	}

	// --- the comparison ---
	for i, c := range cases {
		got, err := execRun(c.Argv, c.Timeout)
		want := out[i]
		if err != nil {
			var pe *PyError
			if !asPyError(err, &pe) {
				t.Errorf("case %d (%s): Go returned a non-PyError %v", i, c.why, err)
				continue
			}
			if want.Error == nil {
				t.Errorf("case %d (%s): Go raised %s(%q), CPython returned %+v",
					i, c.why, pe.PyClass(), pe.Error(), want)
				continue
			}
			if pe.PyClass() != want.Error.Class || pe.Error() != want.Error.Msg {
				t.Errorf("case %d (%s): exception differs.\n CPython %s: %s\n Go      %s: %s",
					i, c.why, want.Error.Class, want.Error.Msg, pe.PyClass(), pe.Error())
			}
			continue
		}
		if want.Error != nil {
			t.Errorf("case %d (%s): CPython raised %s(%q), Go returned %+v",
				i, c.why, want.Error.Class, want.Error.Msg, got)
			continue
		}
		if got.ReturnCode != want.RC || got.Stdout != want.Stdout || got.Stderr != want.Stderr {
			t.Errorf("case %d (%s): result differs.\n CPython rc=%d out=%q err=%q\n"+
				" Go      rc=%d out=%q err=%q", i, c.why,
				want.RC, want.Stdout, want.Stderr,
				got.ReturnCode, got.Stdout, got.Stderr)
		}
	}
}

// ---------------------------------------------------------------------
// Module constants, and the two pure functions nothing else reaches
// ---------------------------------------------------------------------

const pyConstantsSrc = `
import json
import worktree
print(json.dumps({
    "sidecar_suffix": worktree._OWNER_SIDECAR_SUFFIX,
    "sweep_grace_s": worktree._CLONE_SWEEP_GRACE_S,
    "git_timeout_s": worktree._GIT_TIMEOUT_S,
    "exec_config_keys": sorted(worktree._EXEC_CONFIG_KEYS),
}))
`

// TestModuleConstantsMatchCPython compares the four module-level values
// that are DEFAULTS rather than arguments.
//
// A default is exactly the thing a differential built on injected
// parameters cannot see: every sweep fixture passes min_age_s explicitly,
// so 900 could have been any number and nothing would have failed
// (measured — a mutation changing it to 15 killed no test until this
// existed). The same holds for the timeout and for the suffix the sweep
// globs on.
//
// _EXEC_CONFIG_KEYS is compared as a SET because the Python side is a set
// literal and has no order to preserve; what matters is membership, since
// each member is one command a hostile clone could otherwise run on the
// host.
func TestModuleConstantsMatchCPython(t *testing.T) {
	var out struct {
		SidecarSuffix  string   `json:"sidecar_suffix"`
		SweepGraceS    float64  `json:"sweep_grace_s"`
		GitTimeoutS    float64  `json:"git_timeout_s"`
		ExecConfigKeys []string `json:"exec_config_keys"`
	}
	pyprobeWorktree(t.TempDir()).RunJSON(t, pyConstantsSrc, &out)

	// --- the CLAIM about CPython, checked before comparing ---
	if out.SweepGraceS != 900 || out.GitTimeoutS != 120 {
		t.Fatalf("the claim about the module's timing constants is stale: "+
			"grace=%v timeout=%v", out.SweepGraceS, out.GitTimeoutS)
	}
	if len(out.ExecConfigKeys) < 5 {
		t.Fatalf("only %d exec-config keys — the set stopped being a set",
			len(out.ExecConfigKeys))
	}

	// --- the comparison ---
	if ownerSidecarSuffix != out.SidecarSuffix {
		t.Errorf("sidecar suffix differs: CPython %q, Go %q",
			out.SidecarSuffix, ownerSidecarSuffix)
	}
	if float64(cloneSweepGraceS) != out.SweepGraceS {
		t.Errorf("sweep grace differs: CPython %v, Go %v",
			out.SweepGraceS, cloneSweepGraceS)
	}
	if float64(gitTimeoutS) != out.GitTimeoutS {
		t.Errorf("git timeout differs: CPython %v, Go %v",
			out.GitTimeoutS, gitTimeoutS)
	}
	var goKeys []string
	for k := range execConfigKeys {
		goKeys = append(goKeys, k)
	}
	sort.Strings(goKeys)
	// CPython's set carries the ORIGINAL spellings (core.sshCommand); the
	// port stores them pre-lowercased because it compares against a
	// lowercased key. Fold both sides before comparing so the difference
	// that is deliberate does not read as the difference that is not.
	pyKeys := append([]string(nil), out.ExecConfigKeys...)
	for i := range pyKeys {
		pyKeys[i] = strings.ToLower(pyKeys[i])
	}
	sort.Strings(pyKeys)
	if !reflect.DeepEqual(pyKeys, goKeys) {
		t.Errorf("_EXEC_CONFIG_KEYS differs.\n CPython %v\n Go      %v", pyKeys, goKeys)
	}
}

const pyPureSrc = `
import json
from pathlib import Path
import worktree

def acted(**kw):
    return worktree.CloneSweepResult(**kw).acted()

one = [("c", "b", "x")]
two = [("c", "b")]
out = {
    "acted": {
        "empty": worktree.CloneSweepResult().acted(),
        "recovered": acted(recovered=one),
        "removed_empty": acted(removed_empty=two),
        "preserved": acted(preserved=one),
        "skipped_live": acted(skipped_live=two),
        "skipped_young": acted(skipped_young=two),
        "surfaced": acted(surfaced=two),
    },
    "as_dict_json": json.dumps(worktree.CloneSweepResult(
        recovered=[("c1", "b1", "sha1")],
        removed_empty=[("c2", "b2")],
        preserved=[("c3", "b3", "why")],
        skipped_live=[("c4", 7)],
        skipped_young=[("c5", None)],
        surfaced=[("c6", "reason")],
    ).as_dict(), indent=2),
    "sidecar_paths": {},
    "sidecar_errors": {},
}
for p in ["/a/b", "/a/b/", "a", "./a", "/", ".", "..", "/a/.."]:
    try:
        out["sidecar_paths"][p] = str(worktree._clone_sidecar_path(Path(p)))
    except BaseException as exc:
        out["sidecar_errors"][p] = type(exc).__name__ + ": " + str(exc)
print(json.dumps(out))
`

// TestActedAndSidecarPathMatchCPython covers the two pure functions the
// tree-driven fixtures cannot reach every branch of.
//
// acted() asks about THREE of the six lists, and the sweep fixture always
// populates several at once — so "did it also count removed_empty" is
// invisible there (measured: adding removed_empty to the disjunction
// failed nothing). Each list is exercised alone here.
//
// _clone_sidecar_path's ValueError arm needs a path with no NAME, which
// is Path("/"), Path(".") and Path("..") — none of which a sweep over a
// real tree can produce, because every clone it finds is a named
// directory. The raise is not caught anywhere in worktree.py, so it is
// the caller's problem in both runtimes and the class is the contract.
func TestActedAndSidecarPathMatchCPython(t *testing.T) {
	var out struct {
		Acted         map[string]bool   `json:"acted"`
		AsDictJSON    string            `json:"as_dict_json"`
		SidecarPaths  map[string]string `json:"sidecar_paths"`
		SidecarErrors map[string]string `json:"sidecar_errors"`
	}
	pyprobeWorktree(t.TempDir()).RunJSON(t, pyPureSrc, &out)

	// --- the CLAIM about CPython ---
	wantActed := map[string]bool{
		"empty": false, "recovered": true, "removed_empty": false,
		"preserved": true, "skipped_live": false, "skipped_young": false,
		"surfaced": true,
	}
	if !reflect.DeepEqual(out.Acted, wantActed) {
		t.Fatalf("the claim about which lists acted() counts is stale.\n want %v\n got  %v",
			wantActed, out.Acted)
	}
	// Exactly TWO of the eight raise, and ".." is deliberately not one of
	// them: on this CPython `Path("..").name` is ".." (measured — it was
	// "" in older versions), so `/a/..` yields the sidecar
	// `/a/...owner.json` rather than an exception. That is a pypath.Name
	// parity check riding along, and it is the kind of thing a claim
	// written from memory would have got wrong.
	if len(out.SidecarErrors) != 2 {
		t.Fatalf("the claim that exactly two of the eight paths RAISE is "+
			"stale: %v", out.SidecarErrors)
	}
	for _, p := range []string{"/", "."} {
		if _, ok := out.SidecarErrors[p]; !ok {
			t.Fatalf("CPython did not raise for %q — the nameless-path arm "+
				"stopped being reachable: %v", p, out.SidecarErrors)
		}
	}

	// --- the comparison: acted() ---
	c3 := []RecoveredClone{{"c", "b", "x"}}
	p3 := []PreservedClone{{"c", "b", "x"}}
	e2 := []EmptyClone{{"c", "b"}}
	s2 := []SkippedClone{{"c", "b"}}
	u2 := []SurfacedClone{{"c", "b"}}
	goActed := map[string]bool{
		"empty":         CloneSweepResult{}.Acted(),
		"recovered":     CloneSweepResult{Recovered: c3}.Acted(),
		"removed_empty": CloneSweepResult{RemovedEmpty: e2}.Acted(),
		"preserved":     CloneSweepResult{Preserved: p3}.Acted(),
		"skipped_live":  CloneSweepResult{SkippedLive: s2}.Acted(),
		"skipped_young": CloneSweepResult{SkippedYoung: s2}.Acted(),
		"surfaced":      CloneSweepResult{Surfaced: u2}.Acted(),
	}
	if !reflect.DeepEqual(goActed, out.Acted) {
		t.Errorf("acted() differs.\n CPython %v\n Go      %v", out.Acted, goActed)
	}

	// --- the comparison: as_dict, every list non-empty at once ---
	full := CloneSweepResult{
		Recovered:    []RecoveredClone{{"c1", "b1", "sha1"}},
		RemovedEmpty: []EmptyClone{{"c2", "b2"}},
		Preserved:    []PreservedClone{{"c3", "b3", "why"}},
		SkippedLive:  []SkippedClone{{"c4", 7}},
		SkippedYoung: []SkippedClone{{"c5", nil}},
		Surfaced:     []SurfacedClone{{"c6", "reason"}},
	}
	goJSON, err := pyval.DumpsIndent2(full.AsDict())
	if err != nil {
		t.Fatal(err)
	}
	if goJSON != out.AsDictJSON {
		t.Errorf("as_dict rendering differs.\nCPython\n%s\nGo\n%s",
			out.AsDictJSON, goJSON)
	}

	// --- the comparison: _clone_sidecar_path ---
	for p, want := range out.SidecarPaths {
		got, err := cloneSidecarPath(p)
		if err != nil {
			t.Errorf("%q: CPython answered %q, Go raised %v", p, want, err)
			continue
		}
		if got != want {
			t.Errorf("%q: CPython %q, Go %q", p, want, got)
		}
	}
	for p, want := range out.SidecarErrors {
		got, err := cloneSidecarPath(p)
		if err == nil {
			t.Errorf("%q: CPython raised %s, Go answered %q", p, want, got)
			continue
		}
		var pe *PyError
		if !asPyError(err, &pe) || pe.PyClass() != strings.SplitN(want, ":", 2)[0] {
			t.Errorf("%q: CPython raised %q, Go raised %v", p, want, err)
		}
	}
}

// ---------------------------------------------------------------------
// Path.unlink(missing_ok=True)
// ---------------------------------------------------------------------

const pyUnlinkSrc = `
import json, os, sys
from pathlib import Path
root = sys.argv[1]
out = {}
for name in json.loads(sys.argv[2]):
    p = Path(root) / name
    if name == "ro/v":
        os.chmod(Path(root) / "ro", 0o500)
    try:
        p.unlink(missing_ok=True)
        out[name] = {"error": None, "still_there": p.exists()}
    except BaseException as exc:
        out[name] = {"error": type(exc).__name__ + "|" + str(exc),
                     "still_there": p.exists()}
    if name == "ro/v":
        os.chmod(Path(root) / "ro", 0o700)
print(json.dumps(out))
`

// buildUnlinkTree lays out the four shapes unlink can meet, identically
// under each runtime's own root.
func buildUnlinkTree(t *testing.T, root string) {
	t.Helper()
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(os.WriteFile(filepath.Join(root, "file"), []byte("x"), 0o666))
	must(os.Mkdir(filepath.Join(root, "emptydir"), 0o777))
	must(os.Mkdir(filepath.Join(root, "fulldir"), 0o777))
	must(os.WriteFile(filepath.Join(root, "fulldir", "x"), []byte("x"), 0o666))
	must(os.Mkdir(filepath.Join(root, "ro"), 0o777))
	must(os.WriteFile(filepath.Join(root, "ro", "v"), []byte("x"), 0o666))
	t.Cleanup(func() { _ = os.Chmod(filepath.Join(root, "ro"), 0o700) })
}

// TestUnlinkMissingOKMatchesCPython pins the sidecar deletion in
// cleanup_clone against Path.unlink.
//
// The row that matters is emptydir. Python's unlink calls os.unlink and
// nothing else, so a directory raises IsADirectoryError whether it is
// empty or not; Go's os.Remove falls back to rmdir and, for an EMPTY
// directory, SUCCEEDS. The first draft of removeFile was a bare
// os.Remove and deleted a directory CPython refuses to touch — measured
// here, both sides, and the reason this test exists at all.
//
// Only the CLASS is compared for the failures, not the message: the
// port reproduces CPython's text for the two errnos it names and does
// not claim to for the rest, and asserting Go's own wording would pin
// the wrong half.
func TestUnlinkMissingOKMatchesCPython(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("running as root: the read-only-parent row cannot fail")
	}
	names := []string{"absent", "file", "emptydir", "fulldir", "ro/v"}
	pyRoot, goRoot := t.TempDir(), t.TempDir()
	buildUnlinkTree(t, pyRoot)
	buildUnlinkTree(t, goRoot)

	var out map[string]struct {
		Error      string `json:"error"`
		StillThere bool   `json:"still_there"`
	}
	pyprobeWorktree(t.TempDir()).RunJSON(t, pyUnlinkSrc, &out, pyRoot,
		pyprobeArg(t, names))

	// --- the CLAIM about CPython ---
	if out["absent"].Error != "" {
		t.Fatalf("missing_ok did not swallow the absent case: %v", out["absent"])
	}
	if out["file"].Error != "" || out["file"].StillThere {
		t.Fatalf("the plain-file row did not remove the file: %v", out["file"])
	}
	for _, dir := range []string{"emptydir", "fulldir"} {
		if !strings.HasPrefix(out[dir].Error, "IsADirectoryError|") {
			t.Fatalf("the claim that unlink refuses a directory is stale for "+
				"%s: %v", dir, out[dir])
		}
		if !out[dir].StillThere {
			t.Fatalf("CPython removed %s after raising — the claim is stale", dir)
		}
	}
	if !strings.HasPrefix(out["ro/v"].Error, "PermissionError|") {
		t.Fatalf("the read-only-parent row did not raise PermissionError: %v",
			out["ro/v"])
	}

	// --- the comparison ---
	for _, name := range names {
		if name == "ro/v" {
			if err := os.Chmod(filepath.Join(goRoot, "ro"), 0o500); err != nil {
				t.Fatal(err)
			}
		}
		p := filepath.Join(goRoot, filepath.FromSlash(name))
		err := removeFile(p)
		_, statErr := os.Stat(p)
		stillThere := statErr == nil
		if name == "ro/v" {
			if err := os.Chmod(filepath.Join(goRoot, "ro"), 0o700); err != nil {
				t.Fatal(err)
			}
		}

		want := out[name]
		gotClass := ""
		if err != nil {
			var pe *PyError
			if !asPyError(err, &pe) {
				t.Errorf("%s: Go returned a non-PyError %v", name, err)
				continue
			}
			gotClass = pe.PyClass()
		}
		wantClass := ""
		if want.Error != "" {
			wantClass = strings.SplitN(want.Error, "|", 2)[0]
		}
		if gotClass != wantClass {
			t.Errorf("%s: exception class differs — CPython %q, Go %q (%v)",
				name, wantClass, gotClass, err)
		}
		if stillThere != want.StillThere {
			t.Errorf("%s: CPython left it there=%v, Go left it there=%v — "+
				"the two runtimes deleted different things",
				name, want.StillThere, stillThere)
		}
	}
}
