package worktree

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// This package's subject creates, commits to, merges and DELETES git
// worktrees. Its tests build throwaway repositories under t.TempDir() and
// never name a real one — but "never name a real one" is a promise, and
// this file is the structure that replaces it.
//
// The promise was broken once, during this port's own development
// (2026-08-27). A test run created a linked worktree OF THIS REPOSITORY
// at a temp path, on branch maro/L1/wé-rk, and left a commit on the
// checked-out branch whose message was worktree.py's own autocommit text.
// Nothing under ~/.maro was touched and no work was lost, but the run had
// written into a real repository and every test still reported ok.
//
// The enabling mechanism is measured and is not a typo class:
//
//	git -C '' <cmd>     is a documented NO-OP: it runs in the CALLER's cwd
//	str(Path(""))       is "." — so `-C .` does the same
//
// So ANY empty repo path in this module targets the process's working
// directory, and for `go test` that directory is inside this checkout.
// That is the same shape as the dead `if not repo_dir` guard in
// sweep_stranded_clones (clone.go), which is why it is worth a tripwire
// rather than a promise: a fixture that regresses into passing an empty
// path does not look like a dangerous fixture.
//
// The fingerprint deliberately does NOT include the working tree's dirty
// state: editing source files is what development is, and a guard that
// fires on every edit would be turned off. It includes what only git can
// change — the commit HEAD points at, the branch refs, and the registered
// worktrees.
//
// os/exec is used directly rather than this package's runGit, so that a
// change to the code under test cannot disable the check on it.

func repoFingerprint() (string, bool) {
	var parts []string
	for _, args := range [][]string{
		{"rev-parse", "HEAD"},
		{"for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"},
		{"worktree", "list", "--porcelain"},
	} {
		cmd := exec.Command("git", args...)
		out, err := cmd.Output()
		if err != nil {
			return "", false // not a repo, or no git: nothing to guard
		}
		parts = append(parts, string(out))
	}
	return strings.Join(parts, "\x00"), true
}

func TestMain(m *testing.M) {
	before, ok := repoFingerprint()
	code := m.Run()
	if ok {
		after, ok2 := repoFingerprint()
		if !ok2 || before != after {
			fmt.Fprintf(os.Stderr,
				"\nFATAL: a test in this package changed the git repository it "+
					"is running inside.\nThis package's tests must only ever touch "+
					"throwaway repos under t.TempDir(). The usual cause is a repo\n"+
					"path that reached git as \"\" or \".\", both of which mean "+
					"\"the current directory\".\n\nbefore:\n%s\nafter:\n%s\n",
				before, after)
			if code == 0 {
				code = 1
			}
		}
	}
	os.Exit(code)
}
