package worktree

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The sweep is the one function in this module that DELETES a repository
// clone, and the whole design is a retention argument: a clone is removed
// only when its owner is verifiably dead AND its work provably reached
// the live repo. So the differential compares three things and not one —
// the classification (as_dict), the operator-facing log channel, and the
// SURVIVING TREE. A port that classified everything correctly and removed
// one extra directory would pass the first two.
//
// Everything runs against a scripted git and a tree built identically for
// both engines under two separate t.TempDir() roots. Nothing here can
// reach a real worktree of a real repository; the only `git worktree` in
// this file is a string in a rule table.

const pySweepSrc = `
import json, sys, logging
from pathlib import Path
import worktree

payload = json.loads(sys.argv[1])
WS = payload["ws"]
CALLS = []
LOGS = []

class _R:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err

rules = []
for r in payload["rules"]:
    rules.append((tuple(x.replace("{{WS}}", WS) for x in r["prefix"]),
                  r.get("rc", 0), r.get("stdout", ""), r.get("stderr", "")))

def fake_run(argv, capture_output=None, text=None, timeout=None, **kw):
    a = [str(x) for x in argv]
    CALLS.append(" ".join(a))
    for p, rc, out, err in rules:
        if len(p) <= len(a) and all(x == "*" or x == a[i] for i, x in enumerate(p)):
            return _R(rc, out, err)
    return _R(0, "", "")

worktree.subprocess.run = fake_run

class _H(logging.Handler):
    def emit(self, rec):
        LOGS.append(rec.levelname.lower() + "|" + rec.getMessage())

_lg = logging.getLogger("maro.worktree")
_lg.setLevel(logging.DEBUG)
_lg.addHandler(_H())
_lg.propagate = False

ALIVE = payload["alive_pid"]
err = None
res = None
try:
    r = worktree.sweep_stranded_clones(
        pid_alive=lambda p: p == ALIVE or p == 1,
        min_age_s=payload["min_age_s"],
        process_token=lambda p: ("TOK-ALIVE" if p == ALIVE else None))
    # The RENDERING, not the structure. as_dict's key order is insertion
    # order and heartbeat serialises it straight into its result, so a
    # comparison that decoded it into a Go map would alphabetise the keys
    # and turn every pid into a float before it ever got compared.
    res = {"as_dict_json": json.dumps(r.as_dict(), indent=2), "acted": r.acted()}
except BaseException as exc:
    err = {"class": type(exc).__name__, "msg": str(exc)}

survivors = []
root = Path(WS) / "worktrees"
if root.is_dir():
    for p in sorted(root.rglob("*")):
        survivors.append(str(p.relative_to(root)))

print(json.dumps({"result": res, "error": err, "calls": CALLS,
                  "logs": LOGS, "survivors": survivors}))
`

const alivePID = 4242

// sidecar is one owner-manifest fixture.
type sidecar struct {
	name    string // the clone directory name, e.g. "b-clone"
	loop    string // the loop directory it sits in
	raw     string // verbatim sidecar bytes; "" means write no sidecar
	makeDir bool
	ageS    int // how far in the past to set the clone dir's mtime
	// asFile puts a regular FILE where the clone directory would be.
	// is_dir() is False for it — but only a real is_dir() call knows
	// that, which is what makes this row different from "nothing there":
	// a stat that merely SUCCEEDS answers yes.
	asFile bool
}

func ownerJSON(pid string, token string, repo, base, branch string) string {
	tok := "null"
	if token != "" {
		tok = `"` + token + `"`
	}
	return `{"owner_pid": ` + pid + `, "owner_start": ` + tok +
		`, "repo_dir": "` + repo + `", "base_ref": "` + base +
		`", "branch": "` + branch + `", "created": 1756000000.5}`
}

// sweepFixtures is the tree. Each row names the branch of the sweep it is
// there to reach; a row that stops reaching its branch shows up as a
// changed as_dict on BOTH sides, which the CPython-claim check catches.
func sweepFixtures() []sidecar {
	const repo = "/srv/repo"
	return []sidecar{
		// owner alive -> skipped_live, never touched
		{"a-clone", "L1", ownerJSON("4242", "TOK-ALIVE", repo, "main", "maro/L1/a"), true, 7200, false},
		// owner dead but the clone is younger than the grace -> skipped_young
		{"b-clone", "L1", ownerJSON("9999", "TOK-DEAD", repo, "main", "maro/L1/b"), true, 0, false},
		// owner dead, old, nothing to merge -> removed_empty (and REMOVED)
		{"c-clone", "L1", ownerJSON("9999", "TOK-DEAD", repo, "main", "maro/L1/c"), true, 7200, false},
		// owner dead, old, a conflict -> preserved (and KEPT)
		{"d-clone", "L1", ownerJSON("9998", "TOK-DEAD", repo, "main", "maro/L1/d"), true, 7200, false},
		// a sidecar whose clone directory is gone -> debug only, no row
		{"e-clone", "L1", ownerJSON("9999", "", repo, "main", "maro/L1/e"), false, 0, false},
		// a sidecar that is not JSON -> surfaced, KEPT
		{"f-clone", "L1", "{not json", true, 7200, false},
		// no sidecar at all -> surfaced by the SECOND glob, KEPT
		{"g-clone", "L1", "", true, 7200, false},
		// owner_pid is a FLOAT, so isinstance(x, int) is False and the
		// liveness test never runs: it falls to the age branch carrying
		// the float verbatim into the result
		{"h-clone", "L2", ownerJSON("1.5", "TOK-DEAD", repo, "main", "maro/L2/h"), true, 0, false},
		// owner_pid is `true`, which IS an int in Python
		{"i-clone", "L2", ownerJSON("true", "TOK-ALIVE", repo, "main", "maro/L2/i"), true, 7200, false},
		// a sidecar with no base_ref -> surfaced, KEPT
		{"j-clone", "L2", `{"owner_pid": 9999, "repo_dir": "` + repo + `"}`, true, 7200, false},
		// a non-ASCII name, to keep the sort comparison honest about
		// code points rather than bytes
		{"Zé-clone", "L2", ownerJSON("9999", "TOK-DEAD", repo, "main", "maro/L2/z"), true, 7200, false},
		// a sidecar that IS a JSON object but carries no owner_pid ->
		// _read_clone_owner answers None (the `"owner_pid" not in data`
		// arm, which no malformed-JSON fixture reaches) -> surfaced, KEPT
		{"k-clone", "L2", `{"repo_dir": "` + repo + `", "base_ref": "main"}`, true, 7200, false},
		// a sidecar with no BRANCH -> the `or f"maro/stale/{name}"`
		// fallback names the branch after the clone directory, and that
		// name is what CleanupClone would delete, so it is in as_dict
		{"l-clone", "L2", `{"owner_pid": 9999, "owner_start": "TOK-DEAD", "repo_dir": "` +
			repo + `", "base_ref": "main"}`, true, 7200, false},
		// a sidecar whose "clone" is a regular FILE. is_dir() is False,
		// so this takes the orphan-sidecar branch and lands in NO list —
		// which is why only the log channel and the surviving tree can
		// see it. A stat-succeeded test would take it for a clone dir
		// and hand a regular file to merge_back_clone.
		{"m-clone", "L2", ownerJSON("9999", "TOK-DEAD", repo, "main",
			"maro/L2/m"), false, 7200, true},
	}
}

func buildSweepTree(t *testing.T, ws string) {
	t.Helper()
	root := filepath.Join(ws, "worktrees")
	past := time.Now()
	for _, f := range sweepFixtures() {
		dir := filepath.Join(root, f.loop)
		if err := os.MkdirAll(dir, 0o777); err != nil {
			t.Fatal(err)
		}
		clone := filepath.Join(dir, f.name)
		if f.asFile {
			if err := os.WriteFile(clone, []byte("not a clone\n"), 0o666); err != nil {
				t.Fatal(err)
			}
		}
		if f.makeDir {
			// A .git/hooks the sanitiser will delete, so that path runs.
			if err := os.MkdirAll(filepath.Join(clone, ".git", "hooks"), 0o777); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(clone, ".git", "hooks", "pre-commit"),
				[]byte("#!/bin/sh\n"), 0o755); err != nil {
				t.Fatal(err)
			}
			mt := past.Add(-time.Duration(f.ageS) * time.Second)
			if err := os.Chtimes(clone, mt, mt); err != nil {
				t.Fatal(err)
			}
		}
		if f.raw != "" {
			if err := os.WriteFile(clone+ownerSidecarSuffix, []byte(f.raw), 0o666); err != nil {
				t.Fatal(err)
			}
		}
	}
}

func sweepRules() []gitRule {
	const repo = "/srv/repo"
	return []gitRule{
		rule(0, "true\n", "", "git", "-C", repo, "rev-parse", "--is-inside-work-tree"),
		// c-clone: HEAD resolves, nothing ahead -> "no changes"
		rule(0, "cc11\n", "", "git", "-C", "{{WS}}/worktrees/L1/c-clone", "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "rev-parse", "HEAD"),
		rule(0, "0\n", "", "git", "-C", "{{WS}}/worktrees/L1/c-clone", "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "rev-list"),
		// d-clone: two commits ahead, and the fetch into the parent fails
		rule(0, "dd22\n", "", "git", "-C", "{{WS}}/worktrees/L1/d-clone", "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "rev-parse", "HEAD"),
		rule(0, "2\n", "", "git", "-C", "{{WS}}/worktrees/L1/d-clone", "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "rev-list"),
		rule(128, "", "fatal: could not read from remote", "git", "-C", repo, "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "fetch",
			"{{WS}}/worktrees/L1/d-clone"),
		// Ze-clone: ahead, fetch succeeds, and the merge lands -> RECOVERED
		rule(0, "zz33\n", "", "git", "-C", "{{WS}}/worktrees/L2/Zé-clone", "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "rev-parse", "HEAD"),
		rule(0, "1\n", "", "git", "-C", "{{WS}}/worktrees/L2/Zé-clone", "-c",
			"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "rev-list"),
		rule(0, "main\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"),
		rule(0, "merged9999\n", "", "git", "-C", repo, "rev-parse", "HEAD"),
	}
}

type sweepProbeOut struct {
	Result *struct {
		AsDictJSON string `json:"as_dict_json"`
		Acted      bool   `json:"acted"`
	} `json:"result"`
	Error     map[string]any `json:"error"`
	Calls     []string       `json:"calls"`
	Logs      []string       `json:"logs"`
	Survivors []string       `json:"survivors"`
}

func TestSweepStrandedClonesMatchesCPython(t *testing.T) {
	pyWS := t.TempDir()
	goWS := t.TempDir()
	buildSweepTree(t, pyWS)
	buildSweepTree(t, goWS)

	const minAge = 600.0
	payload := map[string]any{
		"ws": pyWS, "rules": sweepRules(),
		"min_age_s": minAge, "alive_pid": alivePID,
	}
	var out sweepProbeOut
	pyprobeWorktree(pyWS).RunJSON(t, pySweepSrc, &out, pyprobeArg(t, payload))

	if out.Error != nil {
		t.Fatalf("CPython raised out of the sweep: %v", out.Error)
	}
	if out.Result == nil {
		t.Fatal("CPython produced no result")
	}

	// --- the CLAIM about CPython, stated before either side ran. Every
	// branch of the sweep is named here; a row that silently stopped
	// being reached fails right here instead of agreeing with a Go side
	// that stopped reaching it too.
	//
	// Three of these rows are counter-intuitive and each one is a
	// measured Python behaviour rather than a guess:
	//
	//   - i-clone carries `"owner_pid": true`. A bool IS an int in
	//     Python, so isinstance passes and the pid handed to alive() is
	//     1 — not 4242. This fixture makes pid 1 alive, so the sidecar
	//     is read as an owner that is still running and the clone is
	//     skipped: `true` in a sidecar is a working pid, which is the
	//     divergence candidate this row exists to hold still.
	//   - surfaced is NOT sorted. The sidecar-pass rows come first in
	//     path order; g-clone comes last because it is found by the
	//     SECOND glob, after every sidecar-carrying clone has been
	//     processed and some have been deleted.
	//   - the L2 pass runs Zé before h..l: 'Z' is U+005A and 'h' is
	//     U+0068, and the sort is by code point.
	wantClasses := map[string][]string{
		"recovered":     {"L2/Zé-clone"},
		"removed_empty": {"L1/c-clone"},
		"preserved":     {"L1/d-clone", "L2/l-clone"},
		"skipped_live":  {"L1/a-clone", "L2/i-clone"},
		"skipped_young": {"L1/b-clone", "L2/h-clone"},
		"surfaced":      {"L1/f-clone", "L2/j-clone", "L2/k-clone", "L1/g-clone"},
	}
	gotClasses := out.AsDictClasses(t, pyWS)
	for k, want := range wantClasses {
		if !reflect.DeepEqual(gotClasses[k], want) {
			t.Fatalf("the claim about CPython's %s list is stale.\n want %v\n got  %v\n"+
				"(full: %v)", k, want, gotClasses[k], gotClasses)
		}
	}
	// The retention invariant, claimed: exactly the two clones whose work
	// provably reached the repo (or never existed) are gone.
	for _, gone := range []string{"L1/c-clone", "L2/Zé-clone"} {
		if survivorHas(out.Survivors, gone) {
			t.Fatalf("CPython did not remove %s — the fixture no longer "+
				"exercises the removal branch", gone)
		}
	}
	for _, kept := range []string{"L1/a-clone", "L1/b-clone", "L1/d-clone",
		"L1/f-clone", "L1/g-clone", "L2/h-clone", "L2/i-clone", "L2/j-clone",
		"L2/k-clone", "L2/l-clone"} {
		if !survivorHas(out.Survivors, kept) {
			t.Fatalf("CPython removed %s — retention claim is stale", kept)
		}
	}

	// --- the comparison ---
	t.Setenv("MARO_WORKSPACE", goWS)
	var calls []recordedCall
	prevRun := subprocessRun
	subprocessRun = scriptRunnerWS(sweepRules(), goWS, &calls)
	defer func() { subprocessRun = prevRun }()
	var logs []logLine
	prevLog := SetLog(func(level, msg string) { logs = append(logs, logLine{level, msg}) })
	defer SetLog(prevLog)

	minAgeCopy := minAge
	got, err := SweepStrandedClones(SweepOptions{
		// pid 1 is alive on purpose: it is the pid a `"owner_pid": true`
		// sidecar resolves to, and unless SOMETHING is alive at 1 the bool
		// arm of the isinstance test changes no outcome and cannot be
		// measured. (Measured: deleting that arm failed nothing until this
		// was here.)
		PIDAlive: func(p int) bool { return p == alivePID || p == 1 },
		MinAgeS:  &minAgeCopy,
		ProcessToken: func(p int) (string, bool) {
			if p == alivePID {
				return "TOK-ALIVE", true
			}
			return "", false
		},
	})
	if err != nil {
		t.Fatalf("Go raised where CPython did not: %v", err)
	}

	pyJSON := out.Result.AsDictJSON
	goJSON, err := pyval.DumpsIndent2(got.AsDict())
	if err != nil {
		t.Fatal(err)
	}
	if normalizeWS(pyJSON, pyWS) != normalizeWS(goJSON, goWS) {
		t.Errorf("as_dict differs.\nCPython:\n%s\nGo:\n%s",
			normalizeWS(pyJSON, pyWS), normalizeWS(goJSON, goWS))
	}
	if got.Acted() != out.Result.Acted {
		t.Errorf("acted() differs: CPython %v, Go %v", out.Result.Acted, got.Acted())
	}

	pyCalls := make([]string, len(out.Calls))
	for i, c := range out.Calls {
		pyCalls[i] = normalizeWS(c, pyWS)
	}
	goCalls := []string{}
	for _, c := range calls {
		goCalls = append(goCalls, normalizeWS(strings.Join(c.Argv, " "), goWS))
	}
	if !reflect.DeepEqual(pyCalls, goCalls) {
		t.Errorf("git calls differ.\nCPython %#v\nGo      %#v", pyCalls, goCalls)
	}

	pyLogs := make([]string, len(out.Logs))
	for i, l := range out.Logs {
		pyLogs[i] = normalizeWS(l, pyWS)
	}
	goLogs := renderLogs(logs, goWS)
	if !reflect.DeepEqual(pyLogs, goLogs) {
		t.Errorf("log lines differ.\nCPython %#v\nGo      %#v", pyLogs, goLogs)
	}

	// The surviving tree — the half a classification comparison cannot see.
	goSurv := walkRel(t, filepath.Join(goWS, "worktrees"))
	pySurv := append([]string(nil), out.Survivors...)
	sort.Strings(pySurv)
	sort.Strings(goSurv)
	if !reflect.DeepEqual(pySurv, goSurv) {
		t.Errorf("the surviving tree differs.\nCPython %v\nGo      %v", pySurv, goSurv)
	}
	// Vacuity floor: a comparison of two empty trees would pass.
	if len(goSurv) < 10 {
		t.Fatalf("only %d surviving paths — the tree comparison is not measuring much", len(goSurv))
	}
}

// AsDictClasses folds CPython's as_dict into "list name -> clone paths
// relative to worktrees/", which is what the claim is written in.
//
// It re-parses the RENDERED json with pyval.LoadsOrdered rather than
// letting encoding/json build a map, because the comparison that matters
// is over the rendering and a second decoding path here would be a second
// chance to lose the key order.
func (o sweepProbeOut) AsDictClasses(t *testing.T, ws string) map[string][]string {
	t.Helper()
	v, err := pyval.LoadsOrdered(o.Result.AsDictJSON)
	if err != nil {
		t.Fatalf("CPython's as_dict is not decodable: %v", err)
	}
	obj, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("CPython's as_dict is not an object: %T", v)
	}
	out := map[string][]string{}
	for _, f := range obj {
		rows, _ := f.Val.(pyval.List)
		names := []string{}
		for _, row := range rows {
			cells, _ := row.(pyval.List)
			if len(cells) == 0 {
				continue
			}
			s, _ := cells[0].(string)
			names = append(names, strings.TrimPrefix(s, filepath.Join(ws, "worktrees")+"/"))
		}
		out[f.Key] = names
	}
	return out
}

func survivorHas(survivors []string, rel string) bool {
	for _, s := range survivors {
		if s == rel {
			return true
		}
	}
	return false
}

func walkRel(t *testing.T, root string) []string {
	t.Helper()
	var out []string
	err := filepath.Walk(root, func(p string, _ os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if p == root {
			return nil
		}
		rel, _ := filepath.Rel(root, p)
		out = append(out, rel)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// scriptRunnerWS is scriptRunner with the {{WS}} token in rule prefixes
// resolved against this side's own workspace root, so one rule table
// drives two runtimes whose temp directories differ.
func scriptRunnerWS(rules []gitRule, ws string, calls *[]recordedCall) func([]string, int) (Completed, error) {
	resolved := make([]gitRule, len(rules))
	for i, r := range rules {
		p := make([]string, len(r.Prefix))
		for j, x := range r.Prefix {
			p[j] = strings.ReplaceAll(x, "{{WS}}", ws)
		}
		resolved[i] = gitRule{Prefix: p, RC: r.RC, Stdout: r.Stdout,
			Stderr: r.Stderr, Raise: r.Raise}
	}
	return scriptRunner(resolved, calls)
}
