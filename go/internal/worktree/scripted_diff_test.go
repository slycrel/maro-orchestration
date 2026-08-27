package worktree

import (
	"encoding/json"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// This file is the argv-level differential: both runtimes run the REAL
// module against a SCRIPTED `subprocess.run`, and the comparison covers
// every observable the module produces — the argv of each git call in
// order, its timeout, the returned dataclass field by field, the log
// lines character for character, and the exception when one escapes.
//
// The seam is `subprocess.run` and not `_git`, and that is a measured
// decision rather than a stylistic one: `_commit_dirty`'s signature is
// `def _commit_dirty(..., *, git=_git)`, and a default argument binds the
// function OBJECT at def time. Patching `worktree._git` therefore does
// not reach the calls `_commit_leftovers` makes through that default —
// half the merge path would have run the real git while the test
// believed it was scripted. Patching one level down reaches everything
// and is the only place the literal "git" and "-C" are visible at all.
//
// What this file cannot see is git itself. The real-git end-to-end
// scenarios live in endtoend_diff_test.go; these two are complements,
// not alternatives.

const pyScriptedSrc = `
import json, sys, logging
from pathlib import Path
import worktree

payload = json.loads(sys.argv[1])
CALLS = []
LOGS = []

class _R:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err

rules = payload["rules"]

def fake_run(argv, capture_output=None, text=None, timeout=None, **kw):
    a = [str(x) for x in argv]
    CALLS.append({"argv": a, "timeout": timeout,
                  "capture_output": bool(capture_output), "text": bool(text)})
    for r in rules:
        p = r["prefix"]
        if len(p) <= len(a) and all(x == "*" or x == a[i] for i, x in enumerate(p)):
            k = r.get("raise")
            if k == "TimeoutExpired":
                raise worktree.subprocess.TimeoutExpired(argv, timeout)
            if k == "FileNotFoundError":
                raise FileNotFoundError(2, "No such file or directory", "git")
            if k == "UnicodeDecodeError":
                b"\xff".decode("utf-8")
            return _R(r.get("rc", 0), r.get("stdout", ""), r.get("stderr", ""))
    return _R(0, "", "")

worktree.subprocess.run = fake_run

class _H(logging.Handler):
    def emit(self, rec):
        LOGS.append({"level": rec.levelname.lower(), "msg": rec.getMessage()})

_lg = logging.getLogger("maro.worktree")
_lg.setLevel(logging.DEBUG)
_lg.addHandler(_H())
_lg.propagate = False

def _mr(m):
    return {"ok": m.ok, "conflict": m.conflict, "branch": m.branch,
            "detail": m.detail, "merged_commit": m.merged_commit}

a = payload["args"]
verb = payload["verb"]
res = None
err = None
try:
    if verb == "provision":
        w = worktree.provision(a["project_dir"], a["name"], loop_id=a["loop_id"])
        if w is not None:
            res = {"path": str(w.path), "branch": w.branch,
                   "repo_dir": str(w.repo_dir), "base_ref": w.base_ref}
    elif verb == "provision_clone":
        c = worktree.provision_clone(a["project_dir"], a["name"], loop_id=a["loop_id"])
        if c is not None:
            res = {"path": str(c.path), "branch": c.branch,
                   "repo_dir": str(c.repo_dir), "base_ref": c.base_ref}
    elif verb == "merge_back":
        wt = worktree.Worktree(path=Path(a["path"]), branch=a["branch"],
                               repo_dir=Path(a["repo_dir"]), base_ref=a["base_ref"])
        res = _mr(worktree.merge_back(wt, message=a["message"]))
    elif verb == "merge_back_clone":
        c = worktree.ScratchClone(path=Path(a["path"]), branch=a["branch"],
                                  repo_dir=Path(a["repo_dir"]), base_ref=a["base_ref"])
        res = _mr(worktree.merge_back_clone(c, message=a["message"]))
    elif verb == "cleanup":
        wt = worktree.Worktree(path=Path(a["path"]), branch=a["branch"],
                               repo_dir=Path(a["repo_dir"]), base_ref=a["base_ref"])
        worktree.cleanup(wt, keep_on_failure=a["keep"])
    elif verb == "cleanup_clone":
        c = worktree.ScratchClone(path=Path(a["path"]), branch=a["branch"],
                                  repo_dir=Path(a["repo_dir"]), base_ref=a["base_ref"])
        worktree.cleanup_clone(c, keep_on_failure=a["keep"])
    elif verb == "prune":
        worktree.prune(a["project_dir"])
    elif verb == "is_git_repo":
        res = {"is_repo": worktree.is_git_repo(Path(a["project_dir"]))}
    else:
        raise SystemExit("unknown verb " + verb)
except BaseException as exc:
    err = {"class": type(exc).__name__, "msg": str(exc)}

print(json.dumps({"result": res, "error": err, "calls": CALLS, "logs": LOGS}))
`

type gitRule struct {
	Prefix []string `json:"prefix"`
	RC     int      `json:"rc,omitempty"`
	Stdout string   `json:"stdout,omitempty"`
	Stderr string   `json:"stderr,omitempty"`
	// Raise names a CPython exception the boundary RAISES instead of
	// returning a CompletedProcess. It is the only way to reach the three
	// arms a returncode cannot express — the 120-second timeout, a missing
	// git binary, and output that is not UTF-8 — and those three are
	// exactly where the port's error TYPE decides whether a caller's
	// `except (OSError, subprocess.SubprocessError)` catches or the
	// exception escapes the module.
	Raise string `json:"raise,omitempty"`
}

// raised is the Go half of a Raise rule: the same exception the Python
// fake raises, built the way execRun builds it, so the comparison covers
// the message TEXT a caller interpolates into a MergeResult.Detail.
func raised(kind string, argv []string, timeoutS int) error {
	switch kind {
	case "TimeoutExpired":
		return &PyError{class: "TimeoutExpired",
			msg: "Command '" + pyval.ReprStrings(argv) + "' timed out after " +
				strconv.Itoa(timeoutS) + " seconds"}
	case "FileNotFoundError":
		return &PyError{class: "FileNotFoundError",
			msg: "[Errno 2] No such file or directory: 'git'"}
	case "UnicodeDecodeError":
		return utf8DecodeError([]byte{0xff})
	}
	panic("unknown raise kind " + kind)
}

type recordedCall struct {
	Argv          []string `json:"argv"`
	Timeout       *float64 `json:"timeout"`
	CaptureOutput bool     `json:"capture_output"`
	Text          bool     `json:"text"`
}

type logLine struct {
	Level string `json:"level"`
	Msg   string `json:"msg"`
}

type probeOut struct {
	Result map[string]any `json:"result"`
	Error  map[string]any `json:"error"`
	Calls  []recordedCall `json:"calls"`
	Logs   []logLine      `json:"logs"`
}

// scenario is one call into the module under one scripted git.
//
// wantCalls is what CPython is CLAIMED to invoke, as a joined-argv list;
// it is checked against the probe BEFORE the port is compared, so a rule
// table that stopped reaching its branch (a renamed subcommand, a
// returncode that no longer selects the arm) fails loudly instead of
// making both engines agree on nothing.
type scenario struct {
	name      string
	verb      string
	args      map[string]any
	rules     []gitRule
	wantCalls []string
	// wantLogs is the same claim for the log channel: level+message.
	wantLogs []string
}

func rule(rc int, stdout, stderr string, prefix ...string) gitRule {
	return gitRule{Prefix: prefix, RC: rc, Stdout: stdout, Stderr: stderr}
}

// scriptRunner is the Go half of the seam: it applies the same rule table
// and records the same fields.
func scriptRunner(rules []gitRule, calls *[]recordedCall) func([]string, int) (Completed, error) {
	return func(argv []string, timeoutS int) (Completed, error) {
		t := float64(timeoutS)
		*calls = append(*calls, recordedCall{Argv: argv, Timeout: &t,
			CaptureOutput: true, Text: true})
		for _, r := range rules {
			if prefixMatches(argv, r.Prefix) {
				if r.Raise != "" {
					return Completed{}, raised(r.Raise, argv, timeoutS)
				}
				return Completed{ReturnCode: r.RC, Stdout: r.Stdout, Stderr: r.Stderr}, nil
			}
		}
		return Completed{}, nil
	}
}

// prefixMatches is the Go half of the rule matcher. "*" stands for any
// one argument, which is what lets a rule name a path whose workspace
// root differs between the two runtimes.
func prefixMatches(argv, prefix []string) bool {
	if len(prefix) > len(argv) {
		return false
	}
	for i, p := range prefix {
		if p != "*" && p != argv[i] {
			return false
		}
	}
	return true
}

func TestScriptedGitDifferential(t *testing.T) {
	// A repo path and a worktrees root that both engines can name. Each
	// side gets its OWN workspace, and both are folded to {{WS}} before
	// comparison — a shared one would hide a path the port built from the
	// wrong root, and a hard-coded one cannot be a temp dir.
	repo := "/srv/repo"
	pyWS := t.TempDir()
	goWS := t.TempDir()

	// git-rev-parse rules every scenario that needs a live repo shares.
	isRepo := rule(0, "true\n", "", "git", "-C", repo, "rev-parse", "--is-inside-work-tree")
	onMain := rule(0, "main\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD")

	scenarios := []scenario{
		{
			name: "provision: not a git repo",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{
				rule(128, "", "fatal: not a git repository",
					"git", "-C", repo, "rev-parse", "--is-inside-work-tree"),
			},
			wantCalls: []string{"git -C /srv/repo rev-parse --is-inside-work-tree|15"},
		},
		{
			name: "provision: rev-parse says false",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{
				rule(0, "false\n", "", "git", "-C", repo, "rev-parse", "--is-inside-work-tree"),
			},
			wantCalls: []string{"git -C /srv/repo rev-parse --is-inside-work-tree|15"},
		},
		{
			// rev-parse EXITS ZERO and says nothing. is_git_repo tests
			// `== "true"`, so this is not a repo; a port that spelled the
			// test as `!= "false"` would answer yes and provision a
			// worktree in a directory git never claimed. The "false" row
			// above cannot tell those two spellings apart — this one is
			// the only input that can. (Measured: the `!= "false"`
			// mutation failed nothing until this row existed.)
			name: "provision: rev-parse exits zero and says nothing",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{
				rule(0, "", "", "git", "-C", repo, "rev-parse", "--is-inside-work-tree"),
			},
			wantCalls: []string{"git -C /srv/repo rev-parse --is-inside-work-tree|15"},
		},
		{
			name: "provision: HEAD unresolvable",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{isRepo,
				rule(128, "", "fatal", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD")},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
			},
			wantLogs: []string{"warning|worktree provision: cannot resolve HEAD of /srv/repo"},
		},
		{
			name: "provision: detached HEAD pins the sha",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{isRepo,
				rule(0, "HEAD\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"),
				rule(0, "deadbeef1234\n", "", "git", "-C", repo, "rev-parse", "HEAD")},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo rev-parse HEAD|15",
				"git -C /srv/repo worktree add {{WS}}/worktrees/L1/w1 -b maro/L1/w1 HEAD|120",
			},
			wantLogs: []string{
				"info|worktree provisioned: {{WS}}/worktrees/L1/w1 on maro/L1/w1 (base deadbeef1234)",
			},
		},
		{
			name: "provision: detached and the sha lookup fails",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{isRepo,
				rule(0, "HEAD\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"),
				rule(1, "", "nope", "git", "-C", repo, "rev-parse", "HEAD")},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo rev-parse HEAD|15",
			},
			wantLogs: []string{"warning|worktree provision: cannot resolve HEAD of /srv/repo"},
		},
		{
			// str.isalnum() is UNICODE. If the port spelled the safe-name
			// filter as ASCII, every one of these characters would become
			// a dash and the two runtimes would put the worker on two
			// differently named branches in one shared object store.
			name: "provision: a Unicode name survives isalnum",
			verb: "provision",
			args: map[string]any{"project_dir": repo,
				"name": "réz_漢-７.x ² Ⅷ/y\\z\tq", "loop_id": "L1"},
			rules: []gitRule{isRepo, onMain},
		},
		{
			// [:60] is a CODE POINT slice. 70 two-byte characters must
			// leave 60 characters, not 60 bytes.
			name: "provision: the 60 cap counts code points",
			verb: "provision",
			args: map[string]any{"project_dir": repo,
				"name": strings.Repeat("é", 70), "loop_id": "L1"},
			rules: []gitRule{isRepo, onMain},
		},
		{
			// pathlib's `/` lets an ABSOLUTE right-hand side REPLACE the
			// left. loop_id reaches provision unsanitised, so an absolute
			// one puts the worktree outside the workspace entirely — and
			// filepath.Join would have put it inside.
			name: "provision: an absolute loop_id escapes the workspace root",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "/tmp/elsewhere"},
			rules: []gitRule{isRepo, onMain,
				rule(1, "", "cannot create", "git", "-C", repo, "worktree", "add")},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo worktree add /tmp/elsewhere/w1 -b maro//tmp/elsewhere/w1 HEAD|120",
			},
			wantLogs: []string{
				"warning|worktree provision failed for /srv/repo: cannot create",
			},
		},
		{
			// (r.stderr or r.stdout) is a TRUTHINESS fallback, .strip() is
			// Unicode, and [:300] is a code-point slice. All three in one
			// row: an empty stderr, a stdout padded with U+0085 and
			// U+001C (which strings.TrimSpace does not strip) and 400
			// two-byte characters of body.
			name: "provision: the failure detail falls back to stdout, strips and clips",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{isRepo, onMain,
				rule(1, " "+strings.Repeat("é", 400)+" ", "",
					"git", "-C", repo, "worktree", "add")},
			wantLogs: []string{
				"warning|worktree provision failed for /srv/repo: " + strings.Repeat("é", 300),
			},
		},
		{
			name: "merge_back: status cannot be read",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(1, "", "  bad status  ", "git", "-C", "/wt", "status", "--porcelain")},
			wantCalls: []string{"git -C /wt status --porcelain|30"},
		},
		{
			name: "merge_back: clean tree and nothing ahead",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "0\n", "", "git", "-C", "/wt", "rev-list", "--count", "main..maro/L1/w1")},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt rev-list --count main..maro/L1/w1|30",
			},
		},
		{
			// The rev-list FAILING must not read as "no changes" — the
			// fail-safe direction is to attempt the merge.
			name: "merge_back: a failed ahead-check falls through to the merge",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(128, "0\n", "", "git", "-C", "/wt", "rev-list"),
				onMain,
				rule(0, "sha999\n", "", "git", "-C", repo, "rev-parse", "HEAD")},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt rev-list --count main..maro/L1/w1|30",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo status --porcelain|30",
				"git -C /srv/repo merge --no-ff maro/L1/w1 -m merge maro/L1/w1|120",
				"git -C /srv/repo rev-parse HEAD|15",
			},
		},
		{
			// An EMPTY message takes the `or` fallback; the committed
			// message is part of the repo's history, so the argv is the
			// contract.
			name: "merge_back: an empty message becomes wt: <branch>",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": ""},
			rules: []gitRule{
				rule(0, " M f.txt\n", "", "git", "-C", "/wt", "status", "--porcelain"),
				rule(0, "3\n", "", "git", "-C", "/wt", "rev-list"),
				rule(0, "other\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD")},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt add -A|120",
				"git -C /wt commit -m wt: maro/L1/w1|120",
				"git -C /wt rev-list --count main..maro/L1/w1|30",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
			},
		},
		{
			name: "merge_back: the base repo moved off the base ref",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "5\n", "", "git", "-C", "/wt", "rev-list"),
				rule(0, "feature\n", "", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD")},
		},
		{
			// _current_ref answers None here, and the f-string renders it
			// as the four characters "None" — not as an empty string.
			name: "merge_back: the base repo's ref is unresolvable",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "5\n", "", "git", "-C", "/wt", "rev-list"),
				rule(1, "", "no", "git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD")},
		},
		{
			name: "merge_back: the base checkout is dirty",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "5\n", "", "git", "-C", "/wt", "rev-list"),
				onMain,
				rule(0, "?? junk\n", "", "git", "-C", repo, "status", "--porcelain")},
		},
		{
			name: "merge_back: a conflict aborts and preserves the branch",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "5\n", "", "git", "-C", "/wt", "rev-list"),
				onMain,
				rule(1, "", "CONFLICT (content): Merge conflict in a.txt\r\nAutomatic merge failed\r",
					"git", "-C", repo, "merge", "--no-ff")},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt rev-list --count main..maro/L1/w1|30",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo status --porcelain|30",
				"git -C /srv/repo merge --no-ff maro/L1/w1 -m merge maro/L1/w1|120",
				"git -C /srv/repo merge --abort|120",
			},
		},
		{
			// BOTH pipes carry text. Every other row here leaves one of
			// them empty, and `(a or b)` and `(b or a)` agree whenever only
			// one is non-empty — so the operand ORDER is unobservable
			// without this fixture. (Measured: a mutation swapping firstOr's
			// operands failed nothing until this row existed.) The order is
			// what an operator reads: git writes the reason to stderr and
			// progress noise to stdout.
			name: "merge_back: both pipes carry text and stderr is the one reported",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "5\n", "", "git", "-C", "/wt", "rev-list"),
				onMain,
				rule(1, "Auto-merging a.txt\n", "CONFLICT (content): Merge conflict in a.txt",
					"git", "-C", repo, "merge", "--no-ff")},
		},
		{
			name: "merge_back: git add fails and the work is preserved",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, " M f\n", "", "git", "-C", "/wt", "status", "--porcelain"),
				rule(1, "", strings.Repeat("x", 250), "git", "-C", "/wt", "add")},
		},
		{
			name: "merge_back: the autocommit fails",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "hello"},
			rules: []gitRule{
				rule(0, " M f\n", "", "git", "-C", "/wt", "status", "--porcelain"),
				rule(1, "", "nothing to commit", "git", "-C", "/wt", "commit")},
		},
		{
			// The 120-second timeout is not a returncode: CPython RAISES
			// TimeoutExpired, `_commit_dirty` catches it as a
			// SubprocessError, and str(exc) — the argv repr and the integer
			// seconds — goes verbatim into a detail an operator reads.
			name: "merge_back: the autocommit times out",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, " M f\n", "", "git", "-C", "/wt", "status", "--porcelain"),
				{Prefix: []string{"git", "-C", "/wt", "add"}, Raise: "TimeoutExpired"}},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt add -A|120",
			},
		},
		{
			// A DIFFERENT except clause, one frame out, with its own prefix.
			// The 30 in the message is the timeout that call was given, not
			// the module default — a port that rendered a constant would
			// pass the row above and fail this one.
			name: "merge_back: the ahead-check times out",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				{Prefix: []string{"git", "-C", "/wt", "rev-list"}, Raise: "TimeoutExpired"}},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt rev-list --count main..maro/L1/w1|30",
			},
		},
		{
			// Inside the lock. The `with locked_write(...)` block is what
			// the except wraps, so the lock is released and the failure is
			// reported rather than escaping through the context manager.
			name: "merge_back: the merge inside the lock times out",
			verb: "merge_back",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "message": "m"},
			rules: []gitRule{
				rule(0, "5\n", "", "git", "-C", "/wt", "rev-list"),
				onMain,
				{Prefix: []string{"git", "-C", repo, "merge", "--no-ff"},
					Raise: "TimeoutExpired"}},
			wantCalls: []string{
				"git -C /wt status --porcelain|30",
				"git -C /wt rev-list --count main..maro/L1/w1|30",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo status --porcelain|30",
				"git -C /srv/repo merge --no-ff maro/L1/w1 -m merge maro/L1/w1|120",
			},
		},
		{
			// git missing altogether. FileNotFoundError is an OSError, so
			// is_git_repo swallows it and provision answers None — the same
			// answer it gives for "this is not a repository". A port that
			// let a spawn failure through would turn a silent no-op into a
			// crash at the top of every run on a box without git.
			name: "provision: the git binary is missing",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{
				{Prefix: []string{"git"}, Raise: "FileNotFoundError"}},
			wantCalls: []string{"git -C /srv/repo rev-parse --is-inside-work-tree|15"},
		},
		{
			// The divergence this file exists to pin. UnicodeDecodeError is
			// a ValueError, NOT an OSError and NOT a SubprocessError, so it
			// passes straight through is_git_repo's except clause and out of
			// provision() to the caller. Both runtimes must propagate it —
			// including the class, because the caller's own except is what
			// decides what happens next.
			name: "provision: undecodable git output ESCAPES the except clause",
			verb: "provision",
			args: map[string]any{"project_dir": repo, "name": "w1", "loop_id": "L1"},
			rules: []gitRule{
				{Prefix: []string{"git"}, Raise: "UnicodeDecodeError"}},
			wantCalls: []string{"git -C /srv/repo rev-parse --is-inside-work-tree|15"},
		},
		{
			name: "cleanup: a timeout is caught and logged, not raised",
			verb: "cleanup",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "keep": false},
			rules: []gitRule{
				{Prefix: []string{"git", "-C", repo, "worktree", "remove"},
					Raise: "TimeoutExpired"}},
			wantCalls: []string{
				"git -C /srv/repo worktree remove --force /wt|120",
			},
		},
		{
			name: "provision_clone: the happy path copies the parent identity",
			verb: "provision_clone",
			args: map[string]any{"project_dir": repo, "name": "job", "loop_id": "L2"},
			rules: []gitRule{isRepo, onMain,
				rule(0, "?? dirty\n", "", "git", "-C", repo, "status", "--porcelain"),
				rule(0, " Jeremy Stone \n", "", "git", "-C", repo, "config", "--get", "user.name"),
				rule(0, "j@example.com\n", "", "git", "-C", repo, "config", "--get", "user.email")},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo status --porcelain|30",
				"git -C /srv/repo clone --no-hardlinks /srv/repo {{WS}}/worktrees/L2/job-clone|120",
				"git -C {{WS}}/worktrees/L2/job-clone checkout -b maro/L2/job|120",
				"git -C /srv/repo config --get user.name|15",
				"git -C {{WS}}/worktrees/L2/job-clone config user.name Jeremy Stone|15",
				"git -C /srv/repo config --get user.email|15",
				"git -C {{WS}}/worktrees/L2/job-clone config user.email j@example.com|15",
			},
		},
		{
			// An empty config value is FALSY, so the copy is skipped
			// entirely rather than writing an empty identity.
			name: "provision_clone: a blank identity value is not copied",
			verb: "provision_clone",
			args: map[string]any{"project_dir": repo, "name": "job", "loop_id": "L2"},
			rules: []gitRule{isRepo, onMain,
				rule(0, "   \n", "", "git", "-C", repo, "config", "--get", "user.name"),
				rule(1, "", "", "git", "-C", repo, "config", "--get", "user.email")},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo rev-parse --abbrev-ref HEAD|15",
				"git -C /srv/repo status --porcelain|30",
				"git -C /srv/repo clone --no-hardlinks /srv/repo {{WS}}/worktrees/L2/job-clone|120",
				"git -C {{WS}}/worktrees/L2/job-clone checkout -b maro/L2/job|120",
				"git -C /srv/repo config --get user.name|15",
				"git -C /srv/repo config --get user.email|15",
			},
		},
		{
			name: "provision_clone: the clone itself fails",
			verb: "provision_clone",
			args: map[string]any{"project_dir": repo, "name": "job", "loop_id": "L2"},
			rules: []gitRule{isRepo, onMain,
				rule(128, "", "fatal: destination exists", "git", "-C", repo, "clone")},
		},
		{
			name: "provision_clone: the branch checkout fails",
			verb: "provision_clone",
			args: map[string]any{"project_dir": repo, "name": "job", "loop_id": "L2"},
			rules: []gitRule{isRepo, onMain,
				rule(1, "", "cannot checkout", "git", "-C", "*", "checkout")},
		},
		{
			// _git_hard's `-c` pair goes AFTER `-C <cwd>`, because Python
			// prepends it to `args`, not to the command line.
			name: "merge_back_clone: every clone-side call is hardened",
			verb: "merge_back_clone",
			args: map[string]any{"path": "/clone", "branch": "maro/L2/job",
				"repo_dir": repo, "base_ref": "main", "message": "msg"},
			rules: []gitRule{
				rule(0, "", "", "git", "-C", "/clone", "config", "--local", "--list"),
				rule(0, "abc123\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-parse", "HEAD"),
				rule(0, "0\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-list")},
			wantCalls: []string{
				"git -C /clone config --local --list --name-only|15",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= status --porcelain|30",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= rev-parse HEAD|15",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= rev-list --count main..abc123|30",
			},
		},
		{
			// The sanitiser's predicate, exercised through the real
			// function. `splitlines()` splits on EIGHT separators and
			// `lower()` is Unicode; a key hidden behind U+0085 must still
			// be unset, and the unset must name the ORIGINAL spelling,
			// not the lowercased one.
			name: "merge_back_clone: the config sanitiser's key predicate",
			verb: "merge_back_clone",
			args: map[string]any{"path": "/clone", "branch": "maro/L2/job",
				"repo_dir": repo, "base_ref": "main", "message": "msg"},
			rules: []gitRule{
				rule(0, "core.fsmonitor\nCore.SSHCommandalias.pwn\n"+
					"  filter.Mine.clean  \nsequence.editor diff.x.textconv\n"+
					"credential.helper\nuploadpack.packObjectsHook\nfoo.bar.command\n"+
					"foo.bar.process\nsomething.hook\ncore.pager\n\n   \n"+
					"branch.main.remote\nremote.origin.url\ncore.hooksPath\n",
					"", "git", "-C", "/clone", "config", "--local", "--list"),
				rule(0, "abc123\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-parse", "HEAD"),
				rule(0, "0\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-list")},
		},
		{
			name: "merge_back_clone: the clone HEAD cannot be resolved",
			verb: "merge_back_clone",
			args: map[string]any{"path": "/clone", "branch": "maro/L2/job",
				"repo_dir": repo, "base_ref": "main", "message": "msg"},
			rules: []gitRule{
				rule(0, "   \n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-parse", "HEAD")},
		},
		{
			name: "merge_back_clone: the fetch into the parent fails",
			verb: "merge_back_clone",
			args: map[string]any{"path": "/clone", "branch": "maro/L2/job",
				"repo_dir": repo, "base_ref": "main", "message": "msg"},
			rules: []gitRule{
				rule(0, "abc123\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-parse", "HEAD"),
				rule(0, "4\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-list"),
				rule(128, "", "fatal: no such ref", "git", "-C", repo, "-c",
					"core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "fetch")},
			wantCalls: []string{
				"git -C /clone config --local --list --name-only|15",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= status --porcelain|30",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= rev-parse HEAD|15",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= rev-list --count main..abc123|30",
				"git -C /srv/repo -c core.hooksPath=/dev/null -c core.fsmonitor= fetch /clone abc123:maro/L2/job|120",
			},
		},
		{
			// The clone path's exception arm has a two-part detail with the
			// exception in the MIDDLE — "fetch from scratch clone failed:
			// {exc}; work preserved in {path}" — so a port that dropped the
			// message or reordered the halves loses the operator's only
			// pointer to where the work actually is.
			name: "merge_back_clone: the fetch into the parent times out",
			verb: "merge_back_clone",
			args: map[string]any{"path": "/clone", "branch": "maro/L2/job",
				"repo_dir": repo, "base_ref": "main", "message": "msg"},
			rules: []gitRule{
				rule(0, "abc123\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-parse", "HEAD"),
				rule(0, "4\n", "", "git", "-C", "/clone", "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "rev-list"),
				{Prefix: []string{"git", "-C", repo, "-c", "core.hooksPath=/dev/null",
					"-c", "core.fsmonitor=", "fetch"}, Raise: "TimeoutExpired"}},
			wantCalls: []string{
				"git -C /clone config --local --list --name-only|15",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= status --porcelain|30",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= rev-parse HEAD|15",
				"git -C /clone -c core.hooksPath=/dev/null -c core.fsmonitor= rev-list --count main..abc123|30",
				"git -C /srv/repo -c core.hooksPath=/dev/null -c core.fsmonitor= fetch /clone abc123:maro/L2/job|120",
			},
		},
		{
			name: "cleanup: keep_on_failure only logs",
			verb: "cleanup",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "keep": true},
			wantCalls: []string{},
			wantLogs: []string{
				"warning|worktree kept for inspection: /wt (branch maro/L1/w1)",
			},
		},
		{
			name: "cleanup: both failures are reported at different levels",
			verb: "cleanup",
			args: map[string]any{"path": "/wt", "branch": "maro/L1/w1",
				"repo_dir": repo, "base_ref": "main", "keep": false},
			rules: []gitRule{
				rule(1, "", "  is not a working tree  ", "git", "-C", repo, "worktree", "remove"),
				rule(1, "not found", "", "git", "-C", repo, "branch", "-D")},
			wantCalls: []string{
				"git -C /srv/repo worktree remove --force /wt|120",
				"git -C /srv/repo branch -D maro/L1/w1|30",
			},
			wantLogs: []string{
				"warning|worktree remove failed: is not a working tree",
				"debug|branch delete failed: not found",
			},
		},
		{
			name: "cleanup_clone: keep_on_failure names the branch",
			verb: "cleanup_clone",
			args: map[string]any{"path": "/clone", "branch": "maro/L2/job",
				"repo_dir": repo, "base_ref": "main", "keep": true},
			wantCalls: []string{},
			wantLogs: []string{
				"warning|scratch clone kept for inspection: /clone (branch maro/L2/job)",
			},
		},
		{
			name: "prune: a non-repo is not pruned",
			verb: "prune",
			args: map[string]any{"project_dir": repo},
			rules: []gitRule{
				rule(128, "", "no", "git", "-C", repo, "rev-parse", "--is-inside-work-tree")},
			wantCalls: []string{"git -C /srv/repo rev-parse --is-inside-work-tree|15"},
		},
		{
			name:  "prune: a repo gets a 60-second prune",
			verb:  "prune",
			args:  map[string]any{"project_dir": repo},
			rules: []gitRule{isRepo},
			wantCalls: []string{
				"git -C /srv/repo rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo worktree prune|60",
			},
		},
		{
			// `Path(project_dir)` normalises before it becomes an argv
			// entry: the trailing slash and the doubled one are gone, and
			// the "." component disappears while ".." survives.
			name: "prune: the project dir is PARSED, not passed through",
			verb: "prune",
			args: map[string]any{"project_dir": "/srv//repo/./sub/../x/"},
			rules: []gitRule{rule(0, "true\n", "", "git", "-C", "/srv/repo/sub/../x",
				"rev-parse", "--is-inside-work-tree")},
			wantCalls: []string{
				"git -C /srv/repo/sub/../x rev-parse --is-inside-work-tree|15",
				"git -C /srv/repo/sub/../x worktree prune|60",
			},
		},
	}

	for _, sc := range scenarios {
		sc := sc
		t.Run(sc.name, func(t *testing.T) {
			runScenario(t, sc, pyWS, goWS)
		})
	}
}

func runScenario(t *testing.T, sc scenario, pyWS, goWS string) {
	t.Helper()
	payload := map[string]any{"verb": sc.verb, "args": sc.args, "rules": sc.rules}
	if sc.rules == nil {
		payload["rules"] = []gitRule{}
	}
	var out probeOut
	pyprobeWorktree(pyWS).RunJSON(t, pyScriptedSrc, &out, pyprobeArg(t, payload))

	// The kwargs the module passes to subprocess.run are part of what
	// makes text=True's decoding and newline translation happen at all.
	// If they ever stop being set, the port's decodeText is reproducing
	// something CPython no longer does.
	for i, c := range out.Calls {
		if !c.CaptureOutput || !c.Text {
			t.Fatalf("call %d ran with capture_output=%v text=%v — the port's "+
				"decode/translate step is premised on both being true",
				i, c.CaptureOutput, c.Text)
		}
	}

	pyCalls := renderCalls(out.Calls, pyWS)
	pyLogs := renderLogs(out.Logs, pyWS)

	// --- the CLAIM about CPython, checked before the port is compared ---
	if sc.wantCalls != nil && !reflect.DeepEqual(pyCalls, sc.wantCalls) {
		t.Fatalf("the claim about CPython's git calls is stale.\n want %#v\n got  %#v",
			sc.wantCalls, pyCalls)
	}
	if sc.wantLogs != nil && !reflect.DeepEqual(pyLogs, sc.wantLogs) {
		t.Fatalf("the claim about CPython's log lines is stale.\n want %#v\n got  %#v",
			sc.wantLogs, pyLogs)
	}

	// --- the comparison ---
	goRes, goErr, goCalls, goLogs := runGoScenario(t, sc, goWS)
	if !reflect.DeepEqual(goCalls, pyCalls) {
		t.Errorf("git calls differ.\n CPython %#v\n Go      %#v", pyCalls, goCalls)
	}
	if !reflect.DeepEqual(goLogs, pyLogs) {
		t.Errorf("log lines differ.\n CPython %#v\n Go      %#v", pyLogs, goLogs)
	}
	if !sameJSON(normalizeAny(out.Result, pyWS), normalizeAny(goRes, goWS)) {
		t.Errorf("result differs.\n CPython %s\n Go      %s",
			mustJSON(normalizeAny(out.Result, pyWS)), mustJSON(normalizeAny(goRes, goWS)))
	}
	if !sameJSON(normalizeAny(out.Error, pyWS), normalizeAny(goErr, goWS)) {
		t.Errorf("escaping exception differs.\n CPython %s\n Go      %s",
			mustJSON(out.Error), mustJSON(goErr))
	}
}

// runGoScenario dispatches the same verb into the port with the same
// scripted git installed.
func runGoScenario(t *testing.T, sc scenario, ws string) (map[string]any, map[string]any, []string, []string) {
	t.Helper()
	t.Setenv("MARO_WORKSPACE", ws)

	var calls []recordedCall
	prevRun := subprocessRun
	subprocessRun = scriptRunner(sc.rules, &calls)
	defer func() { subprocessRun = prevRun }()

	var logs []logLine
	prevLog := SetLog(func(level, msg string) { logs = append(logs, logLine{level, msg}) })
	defer SetLog(prevLog)

	a := sc.args
	str := func(k string) string { s, _ := a[k].(string); return s }
	boolOf := func(k string) bool { b, _ := a[k].(bool); return b }

	var res map[string]any
	var err error
	switch sc.verb {
	case "provision":
		var w *Worktree
		w, err = Provision(str("project_dir"), str("name"), str("loop_id"))
		if w != nil {
			res = map[string]any{"path": w.Path, "branch": w.Branch,
				"repo_dir": w.RepoDir, "base_ref": w.BaseRef}
		}
	case "provision_clone":
		var c *ScratchClone
		c, err = ProvisionClone(str("project_dir"), str("name"), str("loop_id"))
		if c != nil {
			res = map[string]any{"path": c.Path, "branch": c.Branch,
				"repo_dir": c.RepoDir, "base_ref": c.BaseRef}
		}
	case "merge_back":
		var m MergeResult
		m, err = MergeBack(Worktree{Path: str("path"), Branch: str("branch"),
			RepoDir: str("repo_dir"), BaseRef: str("base_ref")}, str("message"))
		if err == nil {
			res = mergeResultMap(m)
		}
	case "merge_back_clone":
		var m MergeResult
		m, err = MergeBackClone(ScratchClone{Path: str("path"), Branch: str("branch"),
			RepoDir: str("repo_dir"), BaseRef: str("base_ref")}, str("message"))
		if err == nil {
			res = mergeResultMap(m)
		}
	case "cleanup":
		err = Cleanup(Worktree{Path: str("path"), Branch: str("branch"),
			RepoDir: str("repo_dir"), BaseRef: str("base_ref")}, boolOf("keep"))
	case "cleanup_clone":
		err = CleanupClone(ScratchClone{Path: str("path"), Branch: str("branch"),
			RepoDir: str("repo_dir"), BaseRef: str("base_ref")}, boolOf("keep"))
	case "prune":
		err = Prune(str("project_dir"))
	case "is_git_repo":
		var b bool
		b, err = IsGitRepo(str("project_dir"))
		res = map[string]any{"is_repo": b}
	default:
		t.Fatalf("unknown verb %q", sc.verb)
	}

	var errMap map[string]any
	if err != nil {
		var pe *PyError
		if ok := asPyError(err, &pe); ok {
			errMap = map[string]any{"class": pe.PyClass(), "msg": pe.Error()}
		} else {
			errMap = map[string]any{"class": "?", "msg": err.Error()}
		}
	}
	return res, errMap, renderCalls(calls, ws), renderLogs(logs, ws)
}

func mergeResultMap(m MergeResult) map[string]any {
	return map[string]any{"ok": m.OK, "conflict": m.Conflict, "branch": m.Branch,
		"detail": m.Detail, "merged_commit": m.MergedCommit}
}

func asPyError(err error, out **PyError) bool {
	pe, ok := err.(*PyError)
	if ok {
		*out = pe
	}
	return ok
}

// renderCalls folds one recorded call into "<argv joined by space>|<timeout>",
// with the workspace root replaced by {{WS}}. Joining is lossy for an
// argument containing a space, which is why the identity-copy scenario
// ("Jeremy Stone") is also pinned by its own row: the joined form would
// look the same for a differently-split argv, and the DeepEqual over the
// whole list is what actually rules that out.
func renderCalls(calls []recordedCall, ws string) []string {
	out := []string{}
	for _, c := range calls {
		to := "?"
		if c.Timeout != nil {
			to = pyval.Repr(*c.Timeout)
			if f := *c.Timeout; f == float64(int64(f)) {
				to = pyval.Str(int(f))
			}
		}
		out = append(out, normalizeWS(strings.Join(c.Argv, " "), ws)+"|"+to)
	}
	return out
}

func renderLogs(logs []logLine, ws string) []string {
	out := []string{}
	for _, l := range logs {
		out = append(out, l.Level+"|"+normalizeWS(l.Msg, ws))
	}
	return out
}

func normalizeWS(s, ws string) string {
	if r, ok := pypath.Realpath(ws); ok && r != ws {
		s = strings.ReplaceAll(s, r, "{{WS}}")
	}
	return strings.ReplaceAll(s, ws, "{{WS}}")
}

func normalizeAny(v map[string]any, ws string) map[string]any {
	if v == nil {
		return nil
	}
	out := map[string]any{}
	for k, val := range v {
		if s, ok := val.(string); ok {
			out[k] = normalizeWS(s, ws)
			continue
		}
		out[k] = val
	}
	return out
}

func sameJSON(a, b any) bool { return mustJSON(a) == mustJSON(b) }

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "<unmarshalable>"
	}
	return string(b)
}
