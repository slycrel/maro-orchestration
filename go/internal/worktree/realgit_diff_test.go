package worktree

import (
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// The one end-to-end differential: real git, real subprocess, real
// flock, on a repository this test creates from nothing inside
// t.TempDir().
//
// SAFETY. Nothing in this file names a path it did not create. The repo
// is `git init`ed under the test's own temp root, the worktrees land
// under a second temp root exported as MARO_WORKSPACE, and the pyprobe
// guard asserts that worktree._worktrees_root() resolves outside ~/.maro
// before the interpreter runs a line of module code. `git worktree
// remove` is reached only through Cleanup, with a path built by
// Provision from those two roots.
//
// The commit SHAs are compared, not just their shape. Both sides pin
// GIT_AUTHOR_DATE / GIT_COMMITTER_DATE and the same identity, so the two
// repositories are byte-identical object stores and the autocommit and
// merge commits must hash the same in both runtimes. If the port spelled
// the commit message, the `-A`, or the `--no-ff` differently, the sha
// changes and this fails — which no field-by-field comparison of a
// MergeResult would have caught.

const gitAuthorDate = "2026-01-02T03:04:05 +0000"

// gitEnv is the environment both runtimes hand to git.
//
// GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM point at /dev/null so the
// operator's own ~/.gitconfig cannot reach this test — a global
// commit.gpgsign or core.hooksPath would otherwise change the answer on
// one machine and not another.
func gitEnv() []string {
	return []string{
		"GIT_CONFIG_GLOBAL=/dev/null",
		"GIT_CONFIG_SYSTEM=/dev/null",
		"GIT_AUTHOR_DATE=" + gitAuthorDate,
		"GIT_COMMITTER_DATE=" + gitAuthorDate,
		"GIT_AUTHOR_NAME=Port Test",
		"GIT_AUTHOR_EMAIL=port@example.invalid",
		"GIT_COMMITTER_NAME=Port Test",
		"GIT_COMMITTER_EMAIL=port@example.invalid",
	}
}

func mustGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(os.Environ(), gitEnv()...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v in %s: %v\n%s", args, dir, err, out)
	}
	return string(out)
}

// buildRepo creates a one-commit repository under root and returns its path.
func buildRepo(t *testing.T, root string) string {
	t.Helper()
	repo := filepath.Join(root, "repo")
	if err := os.MkdirAll(repo, 0o777); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("git", "init", "-b", "main", repo)
	cmd.Env = append(os.Environ(), gitEnv()...)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git init: %v\n%s", err, out)
	}
	mustGit(t, repo, "config", "user.name", "Port Test")
	mustGit(t, repo, "config", "user.email", "port@example.invalid")
	if err := os.WriteFile(filepath.Join(repo, "a.txt"), []byte("base\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	mustGit(t, repo, "add", "a.txt")
	mustGit(t, repo, "commit", "-m", "init")
	return repo
}

const pyRealGitSrc = `
import json, logging, sys
from pathlib import Path
import worktree

logging.getLogger("maro.worktree").addHandler(logging.NullHandler())
repo = sys.argv[1]
out = {}

wt = worktree.provision(repo, "wé rk", loop_id="L1")
out["wt"] = None if wt is None else {
    "path": str(wt.path), "branch": wt.branch,
    "repo_dir": str(wt.repo_dir), "base_ref": wt.base_ref}
out["wt_exists"] = Path(wt.path).is_dir()

(Path(wt.path) / "b.txt").write_text("hello from the worker\n", encoding="utf-8")

m = worktree.merge_back(wt, message="")
out["merge"] = {"ok": m.ok, "conflict": m.conflict, "branch": m.branch,
                "detail": m.detail, "merged_commit": m.merged_commit}
out["b_in_repo"] = (Path(repo) / "b.txt").read_text(encoding="utf-8") \
    if (Path(repo) / "b.txt").exists() else None
out["log"] = worktree._git(["log", "--format=%H %s"], Path(repo)).stdout

worktree.cleanup(wt)
out["wt_gone"] = not Path(wt.path).exists()
out["branches_after"] = worktree._git(
    ["for-each-ref", "--format=%(refname)", "refs/heads"], Path(repo)).stdout

worktree.prune(repo)
out["worktrees_after"] = worktree._git(["worktree", "list", "--porcelain"], Path(repo)).stdout

print(json.dumps(out))
`

func TestRealGitProvisionMergeCleanupMatchesCPython(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skipf("git unavailable: %v", err)
	}
	pyRoot, goRoot := t.TempDir(), t.TempDir()
	pyRepo := buildRepo(t, pyRoot)
	goRepo := buildRepo(t, goRoot)
	pyWS := filepath.Join(pyRoot, "ws")
	goWS := filepath.Join(goRoot, "ws")

	probe := pyprobeWorktree(pyWS)
	probe.Env = gitEnv()
	if err := os.MkdirAll(pyWS, 0o777); err != nil {
		t.Fatal(err)
	}
	var out struct {
		WT *struct {
			Path    string `json:"path"`
			Branch  string `json:"branch"`
			RepoDir string `json:"repo_dir"`
			BaseRef string `json:"base_ref"`
		} `json:"wt"`
		WTExists       bool           `json:"wt_exists"`
		Merge          map[string]any `json:"merge"`
		BInRepo        *string        `json:"b_in_repo"`
		Log            string         `json:"log"`
		WTGone         bool           `json:"wt_gone"`
		BranchesAfter  string         `json:"branches_after"`
		WorktreesAfter string         `json:"worktrees_after"`
	}
	probe.RunJSON(t, pyRealGitSrc, &out, pyRepo)

	// --- the CLAIM about CPython, checked before the port runs ---
	if out.WT == nil {
		t.Fatal("CPython provisioned nothing — the fixture repo is not a repo")
	}
	if out.WT.Branch != "maro/L1/wé-rk" {
		t.Fatalf("the claim about the sanitised branch name is stale: %q", out.WT.Branch)
	}
	if out.WT.BaseRef != "main" {
		t.Fatalf("the claim that the fixture starts on main is stale: %q", out.WT.BaseRef)
	}
	if !out.WTExists {
		t.Fatal("CPython's worktree directory was never created")
	}
	if out.Merge["ok"] != true || out.Merge["conflict"] != false {
		t.Fatalf("the claim that the merge SUCCEEDS is stale: %v", out.Merge)
	}
	if out.BInRepo == nil || *out.BInRepo != "hello from the worker\n" {
		t.Fatalf("the worker's file did not reach the repo: %v", out.BInRepo)
	}
	if n := len(strings.Split(strings.TrimSpace(out.Log), "\n")); n != 3 {
		t.Fatalf("the claim of three commits (init, autocommit, merge) is stale: %d\n%s",
			n, out.Log)
	}
	if !out.WTGone {
		t.Fatal("CPython's cleanup left the worktree directory")
	}
	if strings.Contains(out.BranchesAfter, "maro/L1") {
		t.Fatalf("CPython's cleanup left the branch: %q", out.BranchesAfter)
	}

	// --- the comparison ---
	t.Setenv("MARO_WORKSPACE", goWS)
	for _, kv := range gitEnv() {
		k, v, _ := strings.Cut(kv, "=")
		t.Setenv(k, v)
	}
	if err := os.MkdirAll(goWS, 0o777); err != nil {
		t.Fatal(err)
	}

	wt, err := Provision(goRepo, "wé rk", "L1")
	if err != nil {
		t.Fatal(err)
	}
	if wt == nil {
		t.Fatal("Go provisioned nothing where CPython provisioned a worktree")
	}
	got := map[string]any{
		"path": rel(t, wt.Path, goWS), "branch": wt.Branch,
		"repo_dir": rel(t, wt.RepoDir, goRoot), "base_ref": wt.BaseRef,
	}
	want := map[string]any{
		"path": rel(t, out.WT.Path, pyWS), "branch": out.WT.Branch,
		"repo_dir": rel(t, out.WT.RepoDir, pyRoot), "base_ref": out.WT.BaseRef,
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Worktree differs.\n CPython %v\n Go      %v", want, got)
	}
	if !isDir(wt.Path) {
		t.Error("Go did not create the worktree directory")
	}

	if err := os.WriteFile(filepath.Join(wt.Path, "b.txt"),
		[]byte("hello from the worker\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	m, err := MergeBack(*wt, "")
	if err != nil {
		t.Fatal(err)
	}
	gotMerge := map[string]any{"ok": m.OK, "conflict": m.Conflict, "branch": m.Branch,
		"detail": m.Detail, "merged_commit": m.MergedCommit}
	if !reflect.DeepEqual(gotMerge, out.Merge) {
		t.Errorf("MergeResult differs — including the SHA, which is the whole\n"+
			"commit (message, tree, parents, --no-ff) compared at once.\n"+
			" CPython %v\n Go      %v", out.Merge, gotMerge)
	}
	body, err := os.ReadFile(filepath.Join(goRepo, "b.txt"))
	if err != nil {
		t.Fatalf("Go's merge did not bring b.txt into the repo: %v", err)
	}
	if string(body) != *out.BInRepo {
		t.Errorf("merged file differs: %q vs %q", body, *out.BInRepo)
	}
	goLog := mustGit(t, goRepo, "log", "--format=%H %s")
	if goLog != out.Log {
		t.Errorf("the resulting history differs.\n CPython\n%s\n Go\n%s", out.Log, goLog)
	}

	if err := Cleanup(*wt, false); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(wt.Path); !os.IsNotExist(err) {
		t.Errorf("Go's cleanup left %s", wt.Path)
	}
	goBranches := mustGit(t, goRepo, "for-each-ref", "--format=%(refname)", "refs/heads")
	if goBranches != out.BranchesAfter {
		t.Errorf("branches differ.\n CPython %q\n Go      %q", out.BranchesAfter, goBranches)
	}
	if err := Prune(goRepo); err != nil {
		t.Fatal(err)
	}
	goWorktrees := normalizeWS(mustGit(t, goRepo, "worktree", "list", "--porcelain"), goRoot)
	if w := normalizeWS(out.WorktreesAfter, pyRoot); goWorktrees != w {
		t.Errorf("worktree list differs.\n CPython %q\n Go      %q", w, goWorktrees)
	}
}

func rel(t *testing.T, p, root string) string {
	t.Helper()
	r, err := filepath.Rel(resolveOrSelf(root), resolveOrSelf(p))
	if err != nil {
		return p
	}
	return r
}
