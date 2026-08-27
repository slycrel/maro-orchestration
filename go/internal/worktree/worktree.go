// Package worktree ports src/worktree.py — git worktree isolation for
// concurrent workers, and the scratch-clone lane the containerized
// executor runs self-development in.
//
// The incident class it kills: parallel fan-out steps (and, opt-in, whole
// concurrent runs) executing in the SAME checkout — git-stash races,
// forks writing over each other's working tree, half-committed states.
// Each parallel worker gets its own worktree (private working tree +
// index, shared object store), works on branch maro/<loop_id>/<name>, and
// merge-back into the base branch is serialized under a per-repo file
// lock as workers complete.
//
// Merge conflicts never silently drop work: the merge is aborted, the
// branch is kept, and the caller gets a structured failure naming the
// branch.
//
// Non-git directories return a nil Worktree from Provision — callers fall
// through to executing in place, byte-identical to pre-3b behavior.
//
// # What "returns None" means in this port
//
// Every function that answers None in Python answers a NIL POINTER here,
// and the second return value is reserved for the exceptions Python does
// NOT catch. Nine of this module's call sites catch
// `(OSError, subprocess.SubprocessError)` and none of them catch
// ValueError, so a git invocation whose output is not UTF-8 propagates
// out of provision() to its caller in CPython. Collapsing that onto the
// same nil the not-a-repo case uses would make the two runtimes disagree
// about whether a run continues. See gitrun.go's PyError.
package worktree

import (
	"path/filepath"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Worktree is the checkout one worker runs in.
//
// Python's fields are Paths; these are the `str()` of those Paths, which
// is what every consumer of them (an argv entry, a log line, a
// MergeResult detail) actually uses. Constructing them through
// pypath.Str/pypath.Join rather than filepath.Join is load-bearing:
// pathlib's `/` lets an ABSOLUTE right-hand side replace the left, and
// loop_id reaches this module unsanitised.
type Worktree struct {
	Path    string // the worktree checkout workers run in
	Branch  string // maro/<loop_id>/<name>
	RepoDir string // the original checkout
	BaseRef string // branch (or sha when detached) the run started on
}

// ScratchClone is a throwaway full clone of a repo the container edits
// (design §4 self-dev).
//
// Containers never mount a live repo rw — a prompt-injected worker could
// only corrupt its OWN copy. The worker edits/commits this clone (a
// separate object store, `--no-hardlinks`); merge-back is a HOST-side
// `git fetch` from it into the parent under the SAME serialized-merge
// semantics as Worktree.
type ScratchClone struct {
	Path    string // the clone checkout the container mounts rw + runs in
	Branch  string // maro/<loop_id>/<name>
	RepoDir string // the live repo it was cloned from (never mounted)
	BaseRef string // branch (or sha) the parent was on at clone time
}

// MergeResult is the dataclass, defaults included: every field but OK
// defaults to the zero value in both runtimes.
type MergeResult struct {
	OK           bool
	Conflict     bool
	Branch       string
	Detail       string
	MergedCommit string
}

// ---------------------------------------------------------------------
// The module logger
// ---------------------------------------------------------------------

// LogFunc receives one already-rendered line at one level ("warning",
// "info", "debug"), standing for `logging.getLogger("maro.worktree")`.
//
// The lines are rendered by CONCATENATION rather than through a format
// engine, and that is exact rather than approximate: every argument at
// every call site in worktree.py is interpolated with `%s`, which is
// `str(arg)` — a Path renders as its path, an exception as str(exc), an
// int as its digits (all measured). A `%r` or a `%d` anywhere here would
// need a different treatment, and there is none.
//
// The log channel is part of the contract, not decoration: the sweep's
// warnings are the only durable record that a clone was PRESERVED rather
// than recovered, and an operator's grep keyed on the Python spelling
// has to match the Go one character for character.
type LogFunc func(level, msg string)

var (
	logMu   sync.RWMutex
	logSink LogFunc
)

// SetLog installs the sink and returns the previous one, so a test can
// restore it. A nil sink discards, which is what a library with no
// configured handler does.
func SetLog(fn LogFunc) LogFunc {
	logMu.Lock()
	defer logMu.Unlock()
	prev := logSink
	logSink = fn
	return prev
}

func logAt(level, msg string) {
	logMu.RLock()
	fn := logSink
	logMu.RUnlock()
	if fn != nil {
		fn(level, msg)
	}
}

func warn(msg string)  { logAt("warning", msg) }
func info(msg string)  { logAt("info", msg) }
func debug(msg string) { logAt("debug", msg) }

// ---------------------------------------------------------------------
// Small Python spellings this module leans on
// ---------------------------------------------------------------------

// firstOr is Python's `(a or b)` over two strings: the FIRST is taken
// only when it is non-empty. `(r.stderr or r.stdout)` appears seven
// times and it is a truthiness test, not a nil test — an empty stderr
// falls through to stdout.
func firstOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// clip is `.strip()[:N]` — Python's Unicode strip, then a CODE POINT
// slice. Both halves matter for a git message carrying a non-ASCII path:
// strip removes U+001C..U+001F and U+0085 that strings.TrimSpace does
// not, and a byte slice at 300 would cut a rune in half.
func clip(s string, n int) string { return pyval.Clip(pytext.Strip(s), n) }

// lastRunes is Python's `s[-n:]` — the LAST n code points, and the whole
// string when it is shorter. `"ab"[-100:]` is `"ab"`, not an error.
func lastRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[len(r)-n:])
}

// sanitizeName is `"".join(c if c.isalnum() or c in "-_." else "-" for c
// in name)[:60]`.
//
// `str.isalnum()` is UNICODE — 'é', '漢', '７' (fullwidth seven), '²' and
// 'Ⅷ' all pass it (measured), so the safe name keeps them. A port
// spelling this as an ASCII test would replace them with dashes and put
// the worker on a DIFFERENTLY NAMED branch in a shared repo. pytext's
// `\w` predicate is exactly `isalnum() or "_"`, which is why the
// underscore is subtracted back out here rather than a second table
// being written.
func sanitizeName(name string) string {
	var b strings.Builder
	for _, c := range name {
		switch {
		case pytext.IsWordChar(c) && c != '_':
			b.WriteRune(c)
		case c == '-' || c == '_' || c == '.':
			b.WriteRune(c)
		default:
			b.WriteRune('-')
		}
	}
	return pyval.Clip(b.String(), 60)
}

// ---------------------------------------------------------------------
// Hardening the untrusted control plane
// ---------------------------------------------------------------------

// execConfigKeys is `_EXEC_CONFIG_KEYS` — local git config keys that can
// execute a command. A worker with rw on a scratch clone's .git could
// plant these to run code on our host-side git.
//
// Spelled as a set literal in Python and compared against a LOWERCASED
// key, which is why every entry here is lowercase and `core.sshCommand`
// appears as `core.sshcommand`.
var execConfigKeys = map[string]bool{
	"core.fsmonitor": true, "core.sshcommand": true, "core.pager": true,
	"core.editor": true, "core.askpass": true, "core.hookspath": true,
	"uploadpack.packobjectshook": true, "sequence.editor": true,
	"credential.helper": true,
}

// sanitizeUntrustedGit is `_sanitize_untrusted_git`: neutralize a
// worker-controlled clone's git control plane BEFORE any host-side git
// command touches it (design §4; adversarial-review 2026-07-13, findings
// C/M1/A3). By finalize the container has exited, so nothing races us.
//
// Removes planted hooks and strips exec-capable local config (fsmonitor,
// ssh/pager/editor, aliases which can be `!shell`, filter clean/smudge
// which fire on `git add`, uploadpack.packObjectsHook which fires on the
// merge-back `git fetch`, credential helpers, textconv). With the filter
// config gone, a hostile in-tree `.gitattributes` referencing a filter is
// inert (no matching driver). Belt-and-suspenders with runGitHard's
// command-line overrides.
//
// TWO spellings here are not interchangeable with their obvious Go
// equivalents:
//
//   - `listing.stdout.splitlines()` splits on EIGHT separators, not one.
//     A config key carrying U+0085 or U+2028 — and a key is
//     worker-controlled, which is the entire premise of this function —
//     is TWO keys to CPython and one to `strings.Split(s, "\n")`. The
//     hostile half would then never be unset.
//   - `key.lower()` is Python's full-case folding, not ASCII. Turkish
//     dotted-I in `CORE.SSHCOMMAND` is the standing example.
//
// The `key` passed to `--unset-all` is the STRIPPED one, not the
// lowercased one: git config keys are case-insensitive in their section
// and name but not in a subsection, so unsetting the lowercased spelling
// would miss `filter.MyFilter.clean`.
func sanitizeUntrustedGit(workDir string) error {
	gitdir := filepath.Join(workDir, ".git")
	// shutil.rmtree(..., ignore_errors=True) inside a try/except OSError:
	// the ignore_errors already swallows everything the except would.
	removeTree(filepath.Join(gitdir, "hooks"))

	listing, err := runGit([]string{"config", "--local", "--list", "--name-only"}, workDir, 15)
	if err != nil {
		if IsOSOrSubprocessError(err) {
			return nil
		}
		return err
	}
	if listing.ReturnCode != 0 {
		return nil
	}
	for _, raw := range pytext.SplitLines(listing.Stdout) {
		key := pytext.Strip(raw)
		k := pytext.Lower(key)
		if k == "" {
			continue
		}
		if execConfigKeys[k] ||
			strings.HasPrefix(k, "filter.") ||
			strings.HasPrefix(k, "alias.") ||
			strings.HasSuffix(k, ".command") ||
			strings.HasSuffix(k, ".process") ||
			strings.HasSuffix(k, "hook") ||
			strings.HasSuffix(k, ".textconv") ||
			strings.HasSuffix(k, ".helper") {
			if _, err := runGit([]string{"config", "--local", "--unset-all", key}, workDir, 15); err != nil {
				if IsOSOrSubprocessError(err) {
					continue
				}
				return err
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------
// Repo probes
// ---------------------------------------------------------------------

// IsGitRepo is `is_git_repo`.
//
// The error return is not the "not a repo" answer — that is (false, nil).
// It is the UnicodeDecodeError this except clause does not catch.
func IsGitRepo(path string) (bool, error) {
	r, err := runGit([]string{"rev-parse", "--is-inside-work-tree"}, path, 15)
	if err != nil {
		if IsOSOrSubprocessError(err) {
			return false, nil
		}
		return false, err
	}
	return r.ReturnCode == 0 && pytext.Strip(r.Stdout) == "true", nil
}

// worktreesRoot is `_worktrees_root()`.
//
// NAMED DIVERGENCE. Python wraps the config import in `except
// Exception` and falls back to `~/.maro/workspace/worktrees`. Nothing
// reachable makes config.workspace_root() raise (it env-reads, expands
// and resolves), and Go's config.Workspace() has no error channel at
// all, so the fallback arm is not ported. The one input that separates
// them is a process with NO home directory and no MARO_WORKSPACE: Python
// raises RuntimeError out of the except arm's own Path.home(), and Go
// answers the RELATIVE path ".maro/workspace/worktrees". Neither
// runtime does anything useful there; they fail differently.
func worktreesRoot() string {
	return filepath.Join(config.Workspace(), "worktrees")
}

// currentRef is `_current_ref`: the branch name, or the sha when
// detached, or "" for Python's None.
//
// It is NOT inside a try at either call site, so a timeout here escapes
// provision() rather than becoming a nil worktree.
func currentRef(repoDir string) (string, error) {
	r, err := runGit([]string{"rev-parse", "--abbrev-ref", "HEAD"}, repoDir, 15)
	if err != nil {
		return "", err
	}
	if r.ReturnCode != 0 {
		return "", nil
	}
	ref := pytext.Strip(r.Stdout)
	if ref == "HEAD" { // detached — pin to the sha
		r2, err := runGit([]string{"rev-parse", "HEAD"}, repoDir, 15)
		if err != nil {
			return "", err
		}
		if r2.ReturnCode == 0 {
			ref = pytext.Strip(r2.Stdout)
		} else {
			// Python assigns None here, and the `ref or None` below
			// then answers None either way — but the two spellings are
			// not the same statement and only one of them is what runs.
			ref = ""
		}
	}
	return ref, nil // `ref or None`: "" IS the None answer
}

// ---------------------------------------------------------------------
// The worktree lane
// ---------------------------------------------------------------------

// Provision is `provision`: create an isolated worktree of projectDir for
// one worker.
//
// Returns nil (no behavior change) when projectDir isn't a git repo or
// provisioning fails — isolation is an upgrade, never a gate on the work.
func Provision(projectDir, name, loopID string) (*Worktree, error) {
	repoDir := pypath.Str(projectDir)
	ok, err := IsGitRepo(repoDir)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, nil
	}

	baseRef, err := currentRef(repoDir)
	if err != nil {
		return nil, err
	}
	if baseRef == "" {
		warn("worktree provision: cannot resolve HEAD of " + repoDir)
		return nil, nil
	}

	safeName := sanitizeName(name)
	branch := "maro/" + loopID + "/" + safeName
	// pathlib's `/`, not filepath.Join: loop_id arrives from the caller
	// unsanitised, and an ABSOLUTE loop_id replaces the workspace root
	// under CPython. The two runtimes would otherwise provision into two
	// different directories for the same input.
	wtPath := pypath.Join(pypath.Join(worktreesRoot(), loopID), safeName)

	if err := makeDirs(parentOf(wtPath)); err != nil {
		warn("worktree provision failed for " + repoDir + ": " + err.Error())
		return nil, nil
	}
	r, err := runGit([]string{"worktree", "add", wtPath, "-b", branch, "HEAD"}, repoDir, gitTimeoutS)
	if err != nil {
		if !IsOSOrSubprocessError(err) {
			return nil, err
		}
		warn("worktree provision failed for " + repoDir + ": " + err.Error())
		return nil, nil
	}
	if r.ReturnCode != 0 {
		warn("worktree provision failed for " + repoDir + ": " +
			clip(firstOr(r.Stderr, r.Stdout), 300))
		return nil, nil
	}
	info("worktree provisioned: " + wtPath + " on " + branch + " (base " + baseRef + ")")
	return &Worktree{Path: wtPath, Branch: branch, RepoDir: repoDir, BaseRef: baseRef}, nil
}

// mergeLockPath is `_merge_lock_path`.
//
// Sidecar in the workspace, not inside .git (bare-ish setups, and the
// repo may be the user's own checkout — don't litter it).
//
// The key is built from the RESOLVED repo path with every non-alnum code
// point replaced by a dash, then the LAST 100 code points. Both the
// Unicode isalnum and the negative slice are reproduced: a repo under a
// non-ASCII directory keeps those characters in its lock name, and a deep
// path keeps its TAIL (which is what distinguishes two repos) rather than
// its head.
func mergeLockPath(repoDir string) (string, error) {
	resolved, ok := pypath.Realpath(repoDir)
	if !ok {
		return "", &PyError{class: "OSError", msg: "cannot resolve " + repoDir}
	}
	var b strings.Builder
	for _, c := range resolved {
		if pytext.IsWordChar(c) && c != '_' {
			b.WriteRune(c)
		} else {
			b.WriteRune('-')
		}
	}
	return filepath.Join(worktreesRoot(), "merge-"+lastRunes(b.String(), 100)), nil
}

// commitDirty is `_commit_dirty`: commit any uncommitted work in workDir
// to its CURRENT HEAD.
//
// Returns a terminal MergeResult ONLY on failure (caller retains the
// source) — every git-command failure is a failure, never silently
// treated as "clean" or "no changes" (adversarial-review 2026-07-13,
// finding S3: a swallowed status error could lead to deleting the only
// object store). A nil result on success. The runner parameter is what
// lets merge_back_clone hand in the hardened one.
//
// The three timeouts are 30, 120, 120 — `status` names its own and the
// other two take the module default. They are not a stylistic choice: the
// timeout value is interpolated into TimeoutExpired's message, which
// lands in the Detail an operator reads.
func commitDirty(workDir, branch, message string, git gitRunner) (*MergeResult, error) {
	res, err := runCommitDirty(workDir, branch, message, git)
	if err != nil {
		if !IsOSOrSubprocessError(err) {
			return nil, err
		}
		return &MergeResult{Branch: branch, Detail: "autocommit error: " + err.Error()}, nil
	}
	return res, nil
}

func runCommitDirty(workDir, branch, message string, git gitRunner) (*MergeResult, error) {
	r, err := git([]string{"status", "--porcelain"}, workDir, 30)
	if err != nil {
		return nil, err
	}
	if r.ReturnCode != 0 {
		return &MergeResult{Branch: branch,
			Detail: "cannot read status; work preserved: " +
				clip(firstOr(r.Stderr, r.Stdout), 200)}, nil
	}
	if pytext.Strip(r.Stdout) == "" {
		return nil, nil
	}
	a, err := git([]string{"add", "-A"}, workDir, gitTimeoutS)
	if err != nil {
		return nil, err
	}
	if a.ReturnCode != 0 {
		return &MergeResult{Branch: branch,
			Detail: "git add failed; work preserved: " +
				clip(firstOr(a.Stderr, a.Stdout), 200)}, nil
	}
	// `message or f"wt: {branch}"` — a truthiness default, so an empty
	// message takes the fallback and a message of "0" does not.
	msg := message
	if msg == "" {
		msg = "wt: " + branch
	}
	c, err := git([]string{"commit", "-m", msg}, workDir, gitTimeoutS)
	if err != nil {
		return nil, err
	}
	if c.ReturnCode != 0 {
		return &MergeResult{Branch: branch,
			Detail: "autocommit failed: " + clip(firstOr(c.Stderr, c.Stdout), 300)}, nil
	}
	return nil, nil
}

// commitLeftovers is `_commit_leftovers`: commit leftovers, then report
// whether branch is ahead of baseRef.
//
// Returns a terminal MergeResult (autocommit failure, or `ok "no
// changes"`) or nil when there ARE commits to merge. Used by the worktree
// path, where the worker stays on branch.
//
// The `ahead.returncode == 0` guard is doing real work: a rev-list that
// FAILS falls through to the merge rather than reporting "no changes",
// which is the fail-safe direction — never drop possibly-real work.
func commitLeftovers(workDir, branch, baseRef, message string) (*MergeResult, error) {
	fail, err := commitDirty(workDir, branch, message, runGit)
	if err != nil {
		return nil, err
	}
	if fail != nil {
		return fail, nil
	}
	ahead, err := runGit([]string{"rev-list", "--count", baseRef + ".." + branch}, workDir, 30)
	if err != nil {
		if !IsOSOrSubprocessError(err) {
			return nil, err
		}
		return &MergeResult{Branch: branch, Detail: "ahead-check error: " + err.Error()}, nil
	}
	if ahead.ReturnCode == 0 && pytext.Strip(ahead.Stdout) == "0" {
		return &MergeResult{OK: true, Branch: branch, Detail: "no changes"}, nil
	}
	return nil, nil
}

// lockedMerge is `_locked_merge`: merge branch into baseRef in repoDir,
// serialized per-repo.
//
// The branch ref must already exist in repoDir (a worktree shares the
// object store; a scratch clone fetches it in first). The file lock
// serializes concurrent finishers. On conflict: merge --abort, branch
// preserved, structured failure naming it. Never silently drops work.
//
// file_lock.FileLockTimeout is an OSError SUBCLASS in Python (its
// docstring says so explicitly, so that callers' narrow `except OSError`
// blocks keep working), which means a lock that cannot be acquired lands
// in the same "merge error:" detail as a git failure rather than
// propagating. Reproduced through IsOSOrSubprocessError's FileLockTimeout
// arm.
func lockedMerge(repoDir, branch, baseRef string) (MergeResult, error) {
	var out MergeResult
	lockPath, err := mergeLockPath(repoDir)
	if err != nil {
		return MergeResult{Branch: branch, Detail: "merge error: " + err.Error()}, nil
	}
	var inner error
	lockErr := record.Locked(lockPath, func() error {
		out, inner = lockedMergeBody(repoDir, branch, baseRef)
		return nil
	})
	if inner != nil {
		if !IsOSOrSubprocessError(inner) {
			return MergeResult{}, inner
		}
		return MergeResult{Branch: branch, Detail: "merge error: " + inner.Error()}, nil
	}
	if lockErr != nil {
		return MergeResult{Branch: branch, Detail: "merge error: " + lockErr.Error()}, nil
	}
	return out, nil
}

func lockedMergeBody(repoDir, branch, baseRef string) (MergeResult, error) {
	cur, err := currentRef(repoDir)
	if err != nil {
		return MergeResult{}, err
	}
	if cur != baseRef {
		// `f"... ({base_ref} -> {cur}); "` where cur may be None, which
		// renders as the four characters "None" and not as "".
		curText := cur
		if cur == "" {
			curText = "None"
		}
		return MergeResult{Branch: branch,
			Detail: "repo moved off base ref (" + baseRef + " -> " + curText + "); " +
				"work preserved on " + branch}, nil
	}
	dirty, err := runGit([]string{"status", "--porcelain"}, repoDir, 30)
	if err != nil {
		return MergeResult{}, err
	}
	if dirty.ReturnCode == 0 && pytext.Strip(dirty.Stdout) != "" {
		// Merging into a dirty checkout risks entangling the user's
		// in-flight edits with the merge — keep the branch instead.
		return MergeResult{Branch: branch,
			Detail: "base checkout dirty; work preserved on " + branch}, nil
	}
	m, err := runGit([]string{"merge", "--no-ff", branch, "-m", "merge " + branch}, repoDir, gitTimeoutS)
	if err != nil {
		return MergeResult{}, err
	}
	if m.ReturnCode != 0 {
		if _, err := runGit([]string{"merge", "--abort"}, repoDir, gitTimeoutS); err != nil {
			return MergeResult{}, err
		}
		return MergeResult{Conflict: true, Branch: branch,
			Detail: "merge conflict; work preserved on " + branch + ": " +
				clip(firstOr(m.Stderr, m.Stdout), 300)}, nil
	}
	sha, err := runGit([]string{"rev-parse", "HEAD"}, repoDir, 15)
	if err != nil {
		return MergeResult{}, err
	}
	merged := ""
	if sha.ReturnCode == 0 {
		merged = pytext.Strip(sha.Stdout)
	}
	return MergeResult{OK: true, Branch: branch, MergedCommit: merged}, nil
}

// MergeBack is `merge_back`: commit the worker's leftovers and merge its
// branch into the base ref.
//
// Serialized per-repo via the file lock — workers finishing
// simultaneously merge one at a time. On conflict: merge --abort, branch
// preserved, structured failure naming it. Never silently drops work.
func MergeBack(wt Worktree, message string) (MergeResult, error) {
	prep, err := commitLeftovers(wt.Path, wt.Branch, wt.BaseRef, message)
	if err != nil {
		return MergeResult{}, err
	}
	if prep != nil {
		return *prep, nil
	}
	return lockedMerge(wt.RepoDir, wt.Branch, wt.BaseRef)
}

// Cleanup is `cleanup`: remove the worktree (and its branch on success).
//
// keepOnFailure=true leaves both worktree and branch for inspection — the
// failure detail names the branch, so nothing is lost.
//
// Both git calls live in ONE try, so a failure of the first skips the
// second entirely. Splitting them would delete a branch whose worktree
// removal had just raised.
func Cleanup(wt Worktree, keepOnFailure bool) error {
	if keepOnFailure {
		warn("worktree kept for inspection: " + wt.Path + " (branch " + wt.Branch + ")")
		return nil
	}
	r, err := runGit([]string{"worktree", "remove", "--force", wt.Path}, wt.RepoDir, gitTimeoutS)
	if err != nil {
		return cleanupErr(err, wt.Path)
	}
	if r.ReturnCode != 0 {
		warn("worktree remove failed: " + clip(firstOr(r.Stderr, r.Stdout), 200))
	}
	b, err := runGit([]string{"branch", "-D", wt.Branch}, wt.RepoDir, 30)
	if err != nil {
		return cleanupErr(err, wt.Path)
	}
	if b.ReturnCode != 0 {
		debug("branch delete failed: " + clip(firstOr(b.Stderr, b.Stdout), 200))
	}
	return nil
}

func cleanupErr(err error, path string) error {
	if !IsOSOrSubprocessError(err) {
		return err
	}
	warn("worktree cleanup error for " + path + ": " + err.Error())
	return nil
}

// Prune is `prune`: best-effort `git worktree prune` at loop finalize.
//
// is_git_repo is called OUTSIDE the try, so its escaping exception is
// not swallowed by the bare `pass` below it.
func Prune(repoDir string) error {
	repo := pypath.Str(repoDir)
	ok, err := IsGitRepo(repo)
	if err != nil {
		return err
	}
	if !ok {
		return nil
	}
	if _, err := runGit([]string{"worktree", "prune"}, repo, 60); err != nil {
		if !IsOSOrSubprocessError(err) {
			return err
		}
	}
	return nil
}
