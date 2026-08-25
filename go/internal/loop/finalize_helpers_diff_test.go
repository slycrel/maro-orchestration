package loop

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
)

// pyStepEvidenceSrc drives loop_finalize._step_evidence.
//
// The steps arrive as plain dicts turned into a stand-in object rather than
// real StepOutcomes, because the Python reads every field through getattr
// with a default — so the FIXTURE has to be able to omit one, which a
// dataclass constructor will not allow.
const pyStepEvidenceSrc = `
import json, sys
import loop_finalize

_argv = json.loads(sys.argv[1])

class _S:
    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)

_steps = [_S(d) for d in _argv["steps"]]
_kw = {}
if _argv.get("total_budget") is not None:
    _kw["total_budget"] = _argv["total_budget"]
if _argv.get("entry_cap") is not None:
    _kw["entry_cap"] = _argv["entry_cap"]
print(json.dumps({"out": loop_finalize._step_evidence(_steps, **_kw)},
                 sort_keys=True))
`

// TestStepEvidenceMatchesCPython pins the rendered evidence block byte for
// byte, because it is a PROMPT: the text goes to lesson extraction, and a
// port that agreed on which steps survived while disagreeing on the spacing
// around them would mint differently-worded lessons from the same run.
func TestStepEvidenceMatchesCPython(t *testing.T) {
	// The separators are spelled as escapes rather than raw bytes: a raw
	// U+001F in a Go source literal survives into the JSON argv, and both
	// runtimes would then be comparing the same broken input.
	const usep = "\u001f"
	const fsep = "\u001c"

	cases := []struct {
		name     string
		steps    []StepOutcome
		total    int
		cap      int
		bounded  bool
		omitStat bool
	}{
		{name: "two ordinary steps", steps: []StepOutcome{
			{Index: 1, Step: "open the url", Status: "done", Result: "got 200"},
			{Index: 2, Step: "read it", Status: "blocked", Result: "timed out"},
		}},
		// text and no result: the trailing newline goes with the strip.
		{name: "a step with no result", steps: []StepOutcome{
			{Index: 1, Step: "open the url", Status: "done"},
		}},
		// result and no text: the space after the bracket SURVIVES, because
		// the strip is on the joined string and the space is interior.
		{name: "a step with no text", steps: []StepOutcome{
			{Index: 1, Status: "done", Result: "got 200"},
		}},
		// Neither: dropped entirely, and it must not consume a budget slot.
		{name: "an empty step is dropped", steps: []StepOutcome{
			{Index: 1, Status: "done"},
			{Index: 2, Step: "real", Status: "done", Result: "r"},
		}},
		// A step whose text is ONLY separators strips to empty and drops —
		// which is the difference between two rendered steps and one.
		{name: "a separator-only step drops", steps: []StepOutcome{
			{Index: 1, Step: usep + fsep, Status: "done", Result: ""},
			{Index: 2, Step: "real", Status: "done", Result: "r"},
		}},
		// Separators INSIDE a step strip from the ends only.
		{name: "separators strip from the ends", steps: []StepOutcome{
			{Index: 1, Step: usep + "a" + usep + "b" + usep, Status: "done",
				Result: fsep + "res" + fsep},
		}},
		{name: "a blank status renders as a question mark", steps: []StepOutcome{
			{Index: 1, Step: "t", Status: "", Result: "r"},
		}},
		{name: "index zero is a real index", steps: []StepOutcome{
			{Index: 0, Step: "t", Status: "done", Result: "r"},
		}},
		{name: "no steps at all"},
		// The BOUNDS. An entry over the cap carries the truncation marker;
		// a set over the total budget evicts oldest-first with a header.
		{name: "one entry over the cap", bounded: true, total: 4000, cap: 40,
			steps: []StepOutcome{
				{Index: 1, Step: strings.Repeat("x", 30), Status: "done",
					Result: strings.Repeat("y", 60)},
			}},
		{name: "eviction over the total budget", bounded: true, total: 120, cap: 4000,
			steps: []StepOutcome{
				{Index: 1, Step: strings.Repeat("a", 50), Status: "done", Result: "r1"},
				{Index: 2, Step: strings.Repeat("b", 50), Status: "done", Result: "r2"},
				{Index: 3, Step: strings.Repeat("c", 50), Status: "done", Result: "r3"},
			}},
		// The newest entry is kept WHOLE even when it alone busts the
		// budget — a breaker never destroys the only copy.
		{name: "the newest entry survives its own budget", bounded: true,
			total: 10, cap: 4000, steps: []StepOutcome{
				{Index: 1, Step: strings.Repeat("a", 50), Status: "done", Result: "r"},
			}},
		// A cap at zero: Python's ContextBudget takes it literally.
		{name: "a zero cap", bounded: true, total: 4000, cap: 0,
			steps: []StepOutcome{
				{Index: 1, Step: "t", Status: "done", Result: "r"},
			}},
		// Runes, not bytes — the cap counts characters in both runtimes.
		{name: "the cap counts runes", bounded: true, total: 4000, cap: 12,
			steps: []StepOutcome{
				{Index: 1, Step: strings.Repeat("é", 20), Status: "done", Result: "r"},
			}},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pySteps := make([]map[string]any, 0, len(c.steps))
			for _, s := range c.steps {
				pySteps = append(pySteps, map[string]any{
					"index": s.Index, "text": s.Step,
					"status": s.Status, "result": s.Result,
				})
			}
			arg := map[string]any{"steps": pySteps}
			if c.bounded {
				arg["total_budget"] = c.total
				arg["entry_cap"] = c.cap
			}
			var want struct {
				Out string `json:"out"`
			}
			pyprobe.Probe{Marker: "loop_finalize.py"}.RunJSON(
				t, pyStepEvidenceSrc, &want, pyprobe.Arg(t, arg))

			var got string
			if c.bounded {
				got = StepEvidenceBounded(c.steps, c.total, c.cap)
			} else {
				got = StepEvidence(c.steps)
			}
			if got != want.Out {
				t.Errorf("evidence block\n go: %q\n py: %q", got, want.Out)
			}
		})
	}
}

// pyPruneSrc drives loop_finalize.cleanup_step_artifacts against a real
// directory, and returns both the count and the SURVIVING names.
//
// The count alone is not the claim. Two implementations that delete the
// same NUMBER of files can delete different files — the exclusion prefix
// and the glob are exactly the places that would differ — and a caller only
// finds out when an audit goes looking for an artifact that is gone.
const pyPruneSrc = `
import json, os, sys, time
from pathlib import Path
import loop_finalize

_argv = json.loads(sys.argv[1])
_dir = Path(_argv["dir"])

# The config knob, injected rather than written to a file: _auto_prune_days
# reads config.get, and the point of this probe is the PRUNING, not the
# config plumbing (which has its own differential).
loop_finalize._auto_prune_days = lambda: _argv["days"]
loop_finalize._project_dir_root = lambda: _dir.parent

# artifact_dir is patched to the fixture directly. Python reaches for it
# through a try/except that falls back to the inline path; both arms land in
# the same place here, so the probe pins the pruning rule rather than the
# resolution order.
import runs as _runs
_runs.artifact_dir = lambda project, project_root_fn=None: _dir

_n = loop_finalize.cleanup_step_artifacts(_argv["project"],
                                          exclude_loop_id=_argv["exclude"])
_left = sorted(p.name for p in _dir.iterdir())
print(json.dumps({"deleted": _n, "left": _left}, sort_keys=True))
`

// TestCleanupStepArtifactsMatchesCPython pins the glob, the exclusion and
// the age boundary — and pins them by the files that SURVIVE.
//
// It is also the one test in this package that deletes things, so it builds
// its own tree under t.TempDir() and never resolves a workspace.
func TestCleanupStepArtifactsMatchesCPython(t *testing.T) {
	type file struct {
		name  string
		ageD  float64
		isDir bool
	}
	cases := []struct {
		name    string
		files   []file
		days    float64
		exclude string
		project string
	}{
		{name: "disabled by default deletes nothing", days: 0,
			project: "p", files: []file{
				{name: "loop-a-step-1.md", ageD: 90},
				{name: "loop-b-step-2.md", ageD: 90},
			}},
		{name: "old ones go, fresh ones stay", days: 7, project: "p",
			files: []file{
				{name: "loop-a-step-1.md", ageD: 30},
				{name: "loop-b-step-2.md", ageD: 1},
			}},
		{name: "the excluded loop is spared regardless of age", days: 7,
			project: "p", exclude: "keepme", files: []file{
				{name: "loop-keepme-step-1.md", ageD: 90},
				{name: "loop-keepme-step-2.md", ageD: 90},
				{name: "loop-other-step-1.md", ageD: 90},
			}},
		// The exclusion is a prefix test, so a loop id that PREFIXES the
		// excluded one is spared too. Safe direction, and pinned so a
		// "smarter" port cannot quietly start deleting it.
		{name: "a prefix of the excluded id is also spared", days: 7,
			project: "p", exclude: "keep", files: []file{
				{name: "loop-keep-step-1.md", ageD: 90},
				{name: "loop-keepmore-step-1.md", ageD: 90},
			}},
		// The exclusion is a PREFIX, not a substring. This name contains
		// "loop-a-" at offset 2 and starts with "loop-b-", so a Contains
		// test would spare it and Python deletes it.
		{name: "the excluded id inside the name does not spare it", days: 7,
			project: "p", exclude: "a", files: []file{
				{name: "loop-b-loop-a-step-1.md", ageD: 90},
				{name: "loop-a-step-1.md", ageD: 90},
			}},
		// The GLOB, at its edges. All six were measured against
		// fnmatch.fnmatch before they were written down.
		{name: "the glob's edges", days: 7, project: "p", files: []file{
			{name: "loop--step-.md", ageD: 90},      // empty ids: MATCHES
			{name: "loop-step-x.md", ageD: 90},      // no id: does NOT
			{name: "loop-a-step.md", ageD: 90},      // no trailing dash: does NOT
			{name: "loop-x-step-y.md.md", ageD: 90}, // MATCHES
			{name: "loop-.md", ageD: 90},            // does NOT
			{name: "step-a-loop-b.md", ageD: 90},    // wrong order: does NOT
			// No "loop-" prefix at all, but everything AFTER it is shaped
			// right. Without the prefix requirement this is deleted, and
			// "step-a-loop-b.md" above cannot see that: its "step" has no
			// dash in front of it either way.
			{name: "x-step-y.md", ageD: 90},       // no prefix: does NOT
			{name: "loop-a-step-1.txt", ageD: 90}, // wrong suffix: does NOT
		}},
		// A DIRECTORY whose name fits the glob. Python's glob returns it and
		// unlink raises IsADirectoryError into the pass; Go's os.Remove
		// would delete an empty one.
		{name: "a directory named like an artifact survives", days: 7,
			project: "p", files: []file{
				{name: "loop-a-step-1.md", ageD: 90, isDir: true},
				{name: "loop-b-step-2.md", ageD: 90},
			}},
		// A FRACTIONAL day, because grace_s is a float multiply.
		{name: "half a day", days: 0.5, project: "p", files: []file{
			{name: "loop-a-step-1.md", ageD: 1},
			{name: "loop-b-step-2.md", ageD: 0.1},
		}},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			// One tree per runtime: both delete, so they cannot share.
			pyDir := filepath.Join(t.TempDir(), "artifacts")
			goDir := filepath.Join(t.TempDir(), "artifacts")
			for _, dir := range []string{pyDir, goDir} {
				if err := os.MkdirAll(dir, 0o755); err != nil {
					t.Fatal(err)
				}
				for _, f := range c.files {
					p := filepath.Join(dir, f.name)
					if f.isDir {
						if err := os.Mkdir(p, 0o755); err != nil {
							t.Fatal(err)
						}
					} else if err := os.WriteFile(p, []byte("x"), 0o644); err != nil {
						t.Fatal(err)
					}
					when := time.Now().Add(-time.Duration(f.ageD * 24 * float64(time.Hour)))
					if err := os.Chtimes(p, when, when); err != nil {
						t.Fatal(err)
					}
				}
			}

			var want struct {
				Deleted int      `json:"deleted"`
				Left    []string `json:"left"`
			}
			pyprobe.Probe{Marker: "loop_finalize.py"}.RunJSON(t, pyPruneSrc, &want,
				pyprobe.Arg(t, map[string]any{
					"dir": pyDir, "days": c.days,
					"project": c.project, "exclude": c.exclude,
				}))

			// AutoPruneDays is bypassed the same way the probe bypasses
			// _auto_prune_days: this test is about the pruning rule. The
			// knob itself has its own differential below.
			// pruneStepArtifacts DIRECTLY — the same seam the CPython probe
			// takes when it replaces `_auto_prune_days` with a lambda.
			//
			// The first cut of this test went through a local wrapper that
			// re-checked `project == ""` before delegating, and that
			// duplicated guard made the production one untestable: the
			// mutant deleting it passed. A test helper is code, and a guard
			// it repeats is a guard nothing pins.
			got := pruneStepArtifacts(c.days, c.project, c.exclude,
				func(string) (string, error) { return goDir, nil })
			if got != want.Deleted {
				t.Errorf("deleted go=%d py=%d", got, want.Deleted)
			}
			left, err := os.ReadDir(goDir)
			if err != nil {
				t.Fatal(err)
			}
			var names []string
			for _, e := range left {
				names = append(names, e.Name())
			}
			if strings.Join(names, ",") != strings.Join(want.Left, ",") {
				t.Errorf("survivors\n go: %v\n py: %v", names, want.Left)
			}
		})
	}
}

// TestCleanupStepArtifactsEndToEnd drives the EXPORTED entry point, config
// read included, so the two guards it holds are pinned where they live.
//
// The pruning differential above enters at pruneStepArtifacts, which is the
// right seam for the glob and the age rule and the wrong one for these: an
// empty project and a zero knob both short-circuit before any file is
// looked at, and neither is reachable from that seam.
func TestCleanupStepArtifactsEndToEnd(t *testing.T) {
	cases := []struct {
		name    string
		yaml    string
		project string
		want    int
	}{
		{name: "an empty project deletes nothing",
			yaml: "artifacts:\n  auto_prune_days: 7\n", project: "", want: 0},
		{name: "the knob unset deletes nothing", yaml: "other: 1\n",
			project: "p", want: 0},
		{name: "the knob set deletes the old ones",
			yaml: "artifacts:\n  auto_prune_days: 7\n", project: "p", want: 2},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			if err := os.WriteFile(filepath.Join(ws, "config.yml"),
				[]byte(c.yaml), 0o644); err != nil {
				t.Fatal(err)
			}
			dir := filepath.Join(t.TempDir(), "artifacts")
			if err := os.MkdirAll(dir, 0o755); err != nil {
				t.Fatal(err)
			}
			for _, n := range []string{"loop-a-step-1.md", "loop-b-step-2.md"} {
				p := filepath.Join(dir, n)
				if err := os.WriteFile(p, []byte("x"), 0o644); err != nil {
					t.Fatal(err)
				}
				old := time.Now().Add(-90 * 24 * time.Hour)
				if err := os.Chtimes(p, old, old); err != nil {
					t.Fatal(err)
				}
			}
			got := CleanupStepArtifacts(ws, c.project, "",
				func(string) (string, error) { return dir, nil })
			if got != c.want {
				t.Errorf("deleted=%d want=%d", got, c.want)
			}
		})
	}
}

// pyAutoPruneSrc drives _auto_prune_days through a REAL config file, so the
// coercion chain (`float(v or 0)`) runs on a value YAML actually produced.
const pyAutoPruneSrc = `
import json, os, sys
from pathlib import Path
import loop_finalize

_argv = json.loads(sys.argv[1])
print(json.dumps({"days": loop_finalize._auto_prune_days()}, sort_keys=True))
`

// TestAutoPruneDaysMatchesCPython walks the coercion chain through the
// config file rather than through a stub, because the interesting values —
// a quoted number, a bare `-1`, `inf` — are ones YAML decides the type of
// before Python's float() ever sees them.
func TestAutoPruneDaysMatchesCPython(t *testing.T) {
	cases := []struct {
		name string
		yaml string
	}{
		{name: "absent", yaml: "other: 1\n"},
		{name: "zero", yaml: "artifacts:\n  auto_prune_days: 0\n"},
		{name: "an integer", yaml: "artifacts:\n  auto_prune_days: 7\n"},
		{name: "a float", yaml: "artifacts:\n  auto_prune_days: 0.5\n"},
		{name: "a quoted number", yaml: "artifacts:\n  auto_prune_days: \"7\"\n"},
		{name: "a quoted float", yaml: "artifacts:\n  auto_prune_days: \"0.5\"\n"},
		{name: "negative clamps to zero", yaml: "artifacts:\n  auto_prune_days: -1\n"},
		{name: "a null", yaml: "artifacts:\n  auto_prune_days: ~\n"},
		{name: "an empty string", yaml: "artifacts:\n  auto_prune_days: \"\"\n"},
		{name: "a word", yaml: "artifacts:\n  auto_prune_days: abc\n"},
		{name: "true", yaml: "artifacts:\n  auto_prune_days: true\n"},
		{name: "false", yaml: "artifacts:\n  auto_prune_days: false\n"},
		{name: "a list", yaml: "artifacts:\n  auto_prune_days: []\n"},
		{name: "whitespace around a number", yaml: "artifacts:\n  auto_prune_days: \" 7 \"\n"},
		{name: "underscore grouping", yaml: "artifacts:\n  auto_prune_days: \"1_000\"\n"},
		{name: "a hex literal", yaml: "artifacts:\n  auto_prune_days: \"0x10\"\n"},
		// THE hex case. "0x10" is an error in BOTH runtimes, so it cannot
		// see the screen that exists for this: Go's ParseFloat accepts the
		// hex-float form with its p-exponent and CPython accepts neither.
		// The fixture above was the one written first and it proved nothing.
		{name: "a hex float", yaml: "artifacts:\n  auto_prune_days: \"0x10p0\"\n"},
		{name: "nan clamps to zero", yaml: "artifacts:\n  auto_prune_days: \"nan\"\n"},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			if err := os.WriteFile(filepath.Join(ws, "config.yml"),
				[]byte(c.yaml), 0o644); err != nil {
				t.Fatal(err)
			}
			var want struct {
				Days float64 `json:"days"`
			}
			pyprobe.Probe{Marker: "loop_finalize.py", Workspace: ws}.RunJSON(
				t, pyAutoPruneSrc, &want, pyprobe.Arg(t, map[string]any{}))
			got := AutoPruneDays(ws)
			if got != want.Days {
				t.Errorf("auto_prune_days go=%v py=%v (yaml %q)", got, want.Days, c.yaml)
			}
		})
	}
}

// pyArtifactDirSrc drives runs.artifact_dir with and without a pinned
// run-dir, and reports whether the directory was CREATED — Python mkdirs
// both arms and callers write into the result unchecked.
const pyArtifactDirSrc = `
import json, os, sys
from pathlib import Path
import runs

_argv = json.loads(sys.argv[1])
_root = Path(_argv["root"])

if _argv["run_dir"] is None:
    runs.set_current_run_dir(None)
else:
    runs.set_current_run_dir(_argv["run_dir"])

_fn = None if _argv["no_root_fn"] else (lambda: _root)
_out = runs.artifact_dir(_argv["project"], project_root_fn=_fn)
print(json.dumps({"dir": str(_out), "exists": _out.is_dir(),
                  "current": (str(runs.current_run_dir())
                              if runs.current_run_dir() is not None else None),
                  "handle": runs.current_handle_id()}, sort_keys=True))
`

// TestArtifactDirMatchesCPython pins the path both arms produce, that the
// directory is created, and the run-dir accessors alongside — including the
// two answers `Path("")` gives that a single Go string could not hold.
func TestArtifactDirMatchesCPython(t *testing.T) {
	cases := []struct {
		name       string
		runDir     string // "" = not pinned unless emptyPin
		emptyPin   bool   // set_current_run_dir("") — which is Path(".")
		project    string
		absProject bool
		noRootFn   bool
	}{
		{name: "no run-dir falls back to the project dir", project: "proj"},
		// An ABSOLUTE project replaces the root outright, because pathlib's
		// `/` does. filepath.Join would append it under the root and plain
		// concatenation would produce a doubled separator; CPython produces
		// neither, and the artifacts of that run live somewhere else.
		// absProject is filled in below from the per-runtime root, so the
		// escape stays inside the test's own tmp tree.
		{name: "an absolute project escapes the root", absProject: true},
		{name: "a pinned run-dir wins", runDir: "RUNDIR", project: "proj"},
		{name: "the handle id comes out of the run-dir name",
			runDir: "abc123-clever-otter", project: "proj"},
		{name: "a run-dir name with no dash is the whole handle id",
			runDir: "abc123", project: "proj"},
		{name: "a leading dash is an EMPTY handle id, not an absent one",
			runDir: "-x", project: "proj"},
		// Path("") is PosixPath("."), which is truthy — so this PINS a
		// run-dir of "." rather than clearing one.
		{name: "the empty string pins dot, it does not clear", emptyPin: true,
			project: "proj"},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			// SEPARATE roots. Sharing one made both mkdir findings invisible:
			// Python ran first and created the directory, so the Go arm's
			// os.Stat succeeded whether or not ArtifactDir had created
			// anything, and the two mutants that deleted the MkdirAll calls
			// both passed. The paths are compared by their TAIL below for
			// the same reason.
			root := t.TempDir()
			goRoot := t.TempDir()
			pyProject, goProject := c.project, c.project
			if c.absProject {
				pyProject = filepath.Join(root, "elsewhere")
				goProject = filepath.Join(goRoot, "elsewhere")
			}
			var pyRunDir any
			pyRunDirS, goRunDir := c.runDir, c.runDir
			switch {
			case c.emptyPin:
				pyRunDir = ""
			case c.runDir != "":
				pyRunDirS = filepath.Join(root, c.runDir)
				goRunDir = filepath.Join(goRoot, c.runDir)
				pyRunDir = pyRunDirS
			default:
				pyRunDir = nil
			}

			var want struct {
				Dir     string  `json:"dir"`
				Exists  bool    `json:"exists"`
				Current *string `json:"current"`
				Handle  *string `json:"handle"`
			}
			pyprobe.Probe{Marker: "runs.py"}.RunJSON(t, pyArtifactDirSrc, &want,
				pyprobe.Arg(t, map[string]any{
					"root": root, "run_dir": pyRunDir,
					"project": pyProject, "no_root_fn": c.noRootFn,
				}))

			ctx := context.Background()
			switch {
			case c.emptyPin:
				ctx = runs.WithRunDir(ctx, "")
			case c.runDir != "":
				ctx = runs.WithRunDir(ctx, goRunDir)
			default:
				ctx = runs.WithoutRunDir(ctx)
			}

			// current_run_dir(), compared with each runtime's own root
			// swapped out — the roots differ on purpose, and comparing the
			// raw strings would report every case as a mismatch.
			gotCur := deroot(runs.CurrentRunDir(ctx), goRoot)
			wantCur := ""
			if want.Current != nil {
				wantCur = deroot(*want.Current, root)
			}
			if gotCur != wantCur {
				t.Errorf("current_run_dir go=%q py=%q", gotCur, wantCur)
			}
			// current_handle_id() — the (value, present) pair against
			// Python's Optional[str], so "" and None stay distinct.
			gotHandle, gotOK := runs.CurrentHandleID(ctx)
			if (want.Handle == nil) == gotOK {
				t.Errorf("current_handle_id presence go=%v py=%v",
					gotOK, want.Handle != nil)
			} else if want.Handle != nil && gotHandle != *want.Handle {
				t.Errorf("current_handle_id go=%q py=%q", gotHandle, *want.Handle)
			}

			var rootFn func() string
			if !c.noRootFn {
				rootFn = func() string { return goRoot }
			}
			gotDir, err := runs.ArtifactDir(ctx, goProject, rootFn)
			if err != nil {
				t.Fatal(err)
			}
			if deroot(gotDir, goRoot) != deroot(want.Dir, root) {
				t.Errorf("artifact_dir\n go: %q\n py: %q",
					deroot(gotDir, goRoot), deroot(want.Dir, root))
			}
			if !want.Exists {
				t.Fatalf("the probe reports Python did not create %q — the "+
					"fixture is wrong, not the port", want.Dir)
			}
			// The Go directory is checked in the GO tree, which the Python
			// probe has never touched. Sharing one root made this assertion
			// vacuous: Python created the path first and this passed whether
			// or not ArtifactDir did anything.
			if fi, err := os.Stat(gotDir); err != nil || !fi.IsDir() {
				t.Errorf("Go did not create %q (err=%v)", gotDir, err)
			}
		})
	}
}

// deroot replaces a runtime's own temp root with a stable token so two
// trees can be compared. It is a helper for the comparison and NOT for the
// existence check — a path is only proof that the runtime which owns the
// tree created it.
func deroot(p, root string) string {
	if root == "" {
		return p
	}
	return strings.Replace(p, root, "<root>", 1)
}

// TestTheRunDirContextScopesLikeAContextVar is not a differential — it is
// the claim the doc comment on runDirKey makes, turned into a test.
//
// A ContextVar set inside a scope is invisible outside it, and a clear
// inside a scope is invisible outside it too. The failure mode this pins is
// the tempting one: storing nothing on a clear, which would make an inner
// WithoutRunDir inherit the outer value rather than blank it.
func TestTheRunDirContextScopesLikeAContextVar(t *testing.T) {
	base := context.Background()
	outer := runs.WithRunDir(base, "/a/one-otter")

	if got := runs.CurrentRunDir(base); got != "" {
		t.Errorf("the base context saw %q — the value leaked upward", got)
	}
	if got := runs.CurrentRunDir(outer); got != "/a/one-otter" {
		t.Errorf("outer = %q", got)
	}

	inner := runs.WithRunDir(outer, "/b/two-badger")
	if got := runs.CurrentRunDir(inner); got != "/b/two-badger" {
		t.Errorf("inner = %q", got)
	}
	if got := runs.CurrentRunDir(outer); got != "/a/one-otter" {
		t.Errorf("outer changed to %q when inner set its own — this is the "+
			"last-writer-wins global the ContextVar exists to avoid", got)
	}

	cleared := runs.WithoutRunDir(outer)
	if got := runs.CurrentRunDir(cleared); got != "" {
		t.Errorf("a cleared context still reports %q — WithoutRunDir stored "+
			"nothing and inherited the parent", got)
	}
	if _, ok := runs.CurrentHandleID(cleared); ok {
		t.Error("a cleared context still reports a handle id")
	}
	if got := runs.CurrentRunDir(outer); got != "/a/one-otter" {
		t.Errorf("clearing the child blanked the parent too (%q)", got)
	}
}

// TestRunStampsOneBasedStepIndices pins the ASSIGNMENT of StepOutcome.Index,
// which the evidence differential cannot reach.
//
// That differential builds its outcomes as literals, so it pins how an index
// RENDERS and says nothing about where the number comes from. Two mutants
// proved it: zeroing the assignment and making it zero-based both passed
// every subtest. A field is two claims — what writes it and what reads it —
// and a test of the reader is not a test of the writer.
func TestRunStampsOneBasedStepIndices(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{
		`["first thing", "second thing", "third thing"]`,
		"did the first", "did the second", "did the third",
	}}
	res, err := Run(context.Background(), fake, record.New(ws), Opts{
		Goal: "do three things in order", MaxSteps: 3, DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Steps) != 3 {
		t.Fatalf("wanted three outcomes, got %d", len(res.Steps))
	}
	for i, s := range res.Steps {
		if s.Index != i+1 {
			t.Errorf("step %d carries Index %d — Python's item_index is "+
				"ONE-based, and the evidence block renders it verbatim",
				i, s.Index)
		}
	}
	// And the rendered block agrees, so the two halves are pinned together.
	got := StepEvidence(res.Steps)
	for _, want := range []string{"Step 1 [", "Step 2 [", "Step 3 ["} {
		if !strings.Contains(got, want) {
			t.Errorf("evidence block has no %q:\n%s", want, got)
		}
	}
}
