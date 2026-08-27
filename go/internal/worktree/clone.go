package worktree

// Scratch-clone flow — self-development runs under the containerized
// executor (docs/CONTAINER_EXECUTOR_DESIGN.md §4). A live repo is NEVER
// mounted rw into a container; it is cloned into a throwaway scratch the
// worker edits + commits, and merge-back rides the same serialized
// lockedMerge as worktrees.

import (
	"os"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// ownerSidecarSuffix is `_OWNER_SIDECAR_SUFFIX`.
//
// Owner sidecar — the crash-recovery breadcrumb the stale-clone sweep
// keys on. A SIGKILL between provision and finalize leaks a whole-repo
// clone under worktrees/ with no in-memory ScratchClone to merge it back.
// The sidecar records — HOST-side, at provision time — the trusted fields
// the sweep needs to reconstruct that ScratchClone (owner PID + process
// birth for liveness, live repo, base ref, branch). It lives as a SIBLING
// of the clone dir, NOT inside it: the container mounts only the clone
// dir (the cwd), so a hostile worker can't tamper with the sidecar to
// redirect the sweep's host-side merge at an arbitrary repo.
const ownerSidecarSuffix = ".owner.json"

// cloneSweepGraceS is `_CLONE_SWEEP_GRACE_S` — 15 minutes.
//
// Belt-and-suspenders grace: never touch a clone younger than this, even
// with a dead owner PID — guards against racing a clone whose owning run
// is mid-startup (PID briefly not yet visible) or a resume that just
// re-provisioned. Liveness is authoritative; this only narrows the window
// further. Mirrors the heartbeat stranded-run grace.
const cloneSweepGraceS = 15 * 60

// cloneSidecarPath is `_clone_sidecar_path`: the sibling manifest path
// `<clone>.owner.json`, outside the container-mounted clone dir so the
// worker cannot write it.
//
// `p.with_name(...)` RAISES ValueError when p has no name — Path("/") and
// Path(".") both do — and that raise is not caught anywhere in this
// module. Reported rather than papered over with a computed path CPython
// never produces.
func cloneSidecarPath(clonePath string) (string, error) {
	p := pypath.Str(clonePath)
	name := pypath.Name(p)
	if name == "" {
		return "", &PyError{class: "ValueError",
			msg: "PosixPath(" + pytext.Repr(p) + ") has an empty name"}
	}
	return pypath.Join(parentOf(p), name+ownerSidecarSuffix), nil
}

// writeCloneOwner is `_write_clone_owner`: record the owner breadcrumb
// next to the clone (best-effort).
//
// Written LAST, only after provisioning fully succeeds, so a failed
// provision never leaves a sidecar to clean up. A write failure is
// non-fatal: the clone still works; only the sweep loses its recovery
// metadata and will fall back to surfacing (never auto-removing) the
// clone.
//
// The payload is an ORDERED object and json.dumps writes it in INSERTION
// order. A Go `map[string]any` through encoding/json would alphabetise it
// to base_ref, branch, created, owner_pid, owner_start, repo_dir — a
// different file for the same state, which is the port's oldest recurring
// family. The separators are json.dumps' DEFAULTS (", " and ": "), which
// carry spaces; the compact form is something a caller has to ask for and
// this one does not.
func writeCloneOwner(clone ScratchClone, now time.Time) error {
	sidecar, err := cloneSidecarPath(clone.Path)
	if err != nil {
		return err // _clone_sidecar_path runs BEFORE the try
	}
	pid := os.Getpid()
	var startTok any
	if tok, ok := startToken(pid); ok {
		startTok = tok
	} // else nil, which json.dumps writes as null
	payload := pyval.Obj{
		{Key: "owner_pid", Val: pid},
		{Key: "owner_start", Val: startTok},
		{Key: "repo_dir", Val: clone.RepoDir},
		{Key: "base_ref", Val: clone.BaseRef},
		{Key: "branch", Val: clone.Branch},
		{Key: "created", Val: float64(now.UnixNano()) / 1e9},
	}
	body, err := pyval.DumpsCompactPy(payload)
	if err != nil {
		warn("scratch-clone owner sidecar write failed for " + clone.Path + ": " + err.Error())
		return nil
	}
	if err := record.AtomicWrite(sidecar, []byte(body)); err != nil {
		warn("scratch-clone owner sidecar write failed for " + clone.Path + ": " + err.Error())
	}
	return nil
}

// readCloneOwner is `_read_clone_owner`: load a clone owner sidecar; nil
// if absent/unreadable/malformed.
//
// The except clause is `(OSError, ValueError)`, and BOTH halves are load
// bearing: a JSONDecodeError is a ValueError, and so is the
// UnicodeDecodeError a non-UTF-8 sidecar raises. A sidecar that is a
// DIRECTORY is an OSError. All three read as "no metadata", which the
// caller turns into `surfaced`, never into a removal.
func readCloneOwner(sidecar string) pyval.Obj {
	raw, err := os.ReadFile(sidecar)
	if err != nil {
		return nil
	}
	text, derr := decodeText(raw) // read_text(encoding="utf-8") is strict
	if derr != nil {
		return nil
	}
	v, err := pyval.LoadsOrdered(text)
	if err != nil {
		return nil
	}
	obj, ok := v.(pyval.Obj)
	if !ok {
		return nil // `not isinstance(data, dict)`
	}
	if _, present := obj.Get("owner_pid"); !present {
		return nil
	}
	return obj
}

// ProvisionClone is `provision_clone`: clone projectDir into an isolated
// scratch checkout for one run.
//
// Returns nil (caller falls back to mounting the working dir directly)
// when projectDir isn't a git repo or the clone fails — isolation is an
// upgrade, never a gate. The clone uses `--no-hardlinks` so it shares NO
// object-store inode with the live repo (the container runs as the host
// uid and could otherwise reach shared objects).
func ProvisionClone(projectDir, name, loopID string) (*ScratchClone, error) {
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
		warn("scratch-clone provision: cannot resolve HEAD of " + repoDir)
		return nil, nil
	}

	// The clone captures COMMITTED state only; warn if the source has
	// uncommitted work (the worker won't see it, and merge-back later
	// refuses a dirty parent) so the surprise is visible
	// (adversarial-review 2026-07-13, S5).
	d, derr := runGit([]string{"status", "--porcelain"}, repoDir, 30)
	if derr != nil {
		if !IsOSOrSubprocessError(derr) {
			return nil, derr
		}
	} else if d.ReturnCode == 0 && pytext.Strip(d.Stdout) != "" {
		warn("scratch-clone: source " + repoDir + " has uncommitted changes — the clone " +
			"sees only committed state and merge-back will refuse a dirty " +
			"parent; commit or stash before a containerized self-dev run")
	}

	safeName := sanitizeName(name)
	branch := "maro/" + loopID + "/" + safeName
	clonePath := pypath.Join(pypath.Join(worktreesRoot(), loopID), safeName+"-clone")

	failed := func(err error) (*ScratchClone, error) {
		if !IsOSOrSubprocessError(err) {
			return nil, err
		}
		warn("scratch-clone provision error for " + repoDir + ": " + err.Error())
		removeTree(clonePath)
		return nil, nil
	}

	if err := makeDirs(parentOf(clonePath)); err != nil {
		warn("scratch-clone provision error for " + repoDir + ": " + err.Error())
		removeTree(clonePath)
		return nil, nil
	}
	r, err := runGit([]string{"clone", "--no-hardlinks", repoDir, clonePath}, repoDir, gitTimeoutS)
	if err != nil {
		return failed(err)
	}
	if r.ReturnCode != 0 {
		warn("scratch-clone provision failed for " + repoDir + ": " +
			clip(firstOr(r.Stderr, r.Stdout), 300))
		removeTree(clonePath) // drop any partial dest
		return nil, nil
	}
	b, err := runGit([]string{"checkout", "-b", branch}, clonePath, gitTimeoutS)
	if err != nil {
		return failed(err)
	}
	if b.ReturnCode != 0 {
		warn("scratch-clone branch checkout failed for " + clonePath + ": " +
			clip(firstOr(b.Stderr, b.Stdout), 300))
		removeTree(clonePath)
		return nil, nil
	}
	// Carry the parent's commit identity into the clone so the worker's
	// in-container commits (and the host-side leftover commit) are
	// attributed — a fresh clone doesn't inherit the source's *local* git
	// config.
	for _, key := range []string{"user.name", "user.email"} {
		v, err := runGit([]string{"config", "--get", key}, repoDir, 15)
		if err != nil {
			return failed(err)
		}
		if v.ReturnCode == 0 && pytext.Strip(v.Stdout) != "" {
			if _, err := runGit([]string{"config", key, pytext.Strip(v.Stdout)}, clonePath, 15); err != nil {
				return failed(err)
			}
		}
	}

	clone := ScratchClone{Path: clonePath, Branch: branch, RepoDir: repoDir, BaseRef: baseRef}
	// Owner breadcrumb LAST — provisioning fully succeeded, so a leaked
	// clone (crash before finalize) can be recovered by the sweep.
	if err := writeCloneOwner(clone, time.Now()); err != nil {
		return nil, err
	}
	info("scratch clone provisioned: " + clonePath + " on " + branch + " (base " + baseRef + ")")
	return &clone, nil
}

// MergeBackClone is `merge_back_clone`: merge a scratch clone's work back
// into the live repo, host-side.
//
// Steps: (1) neutralize the worker-controlled clone's git control plane
// so our host-side git can't be hijacked (findings C/M1/A3); (2) commit
// leftovers to the clone's CURRENT HEAD; (3) resolve what to merge from
// that ACTUAL HEAD — NOT an assumed branch name — so a worker that
// switched branches inside the container isn't silently treated as "no
// changes" and deleted (finding S3); (4) `git fetch` that commit into the
// parent and merge under the same per-repo lock as MergeBack.
// Conflict/moved-base/dirty-base never drop work — the branch is
// preserved and named in the failure. All clone-side git runs hardened
// (hooks/fsmonitor disabled).
//
// Note which calls are NOT inside a try: the `rev-parse HEAD` and the
// `rev-list --count` both propagate a timeout out of this function, where
// the fetch three lines below is wrapped. That asymmetry is Python's and
// it is reproduced, because the sweep's `except Exception` is what
// catches the difference and it records a DIFFERENT outcome for each.
func MergeBackClone(clone ScratchClone, message string) (MergeResult, error) {
	if err := sanitizeUntrustedGit(clone.Path); err != nil {
		return MergeResult{}, err
	}

	fail, err := commitDirty(clone.Path, clone.Branch, message, runGitHard)
	if err != nil {
		return MergeResult{}, err
	}
	if fail != nil {
		return *fail, nil
	}

	head, err := runGitHard([]string{"rev-parse", "HEAD"}, clone.Path, 15)
	if err != nil {
		return MergeResult{}, err
	}
	if head.ReturnCode != 0 || pytext.Strip(head.Stdout) == "" {
		return MergeResult{Branch: clone.Branch,
			Detail: "cannot resolve clone HEAD; work preserved in " + clone.Path}, nil
	}
	headSHA := pytext.Strip(head.Stdout)

	ahead, err := runGitHard([]string{"rev-list", "--count", clone.BaseRef + ".." + headSHA}, clone.Path, 30)
	if err != nil {
		return MergeResult{}, err
	}
	if ahead.ReturnCode == 0 && pytext.Strip(ahead.Stdout) == "0" {
		return MergeResult{OK: true, Branch: clone.Branch, Detail: "no changes"}, nil
	}
	// ahead rc != 0 -> uncertain: fall through and attempt the merge
	// rather than declaring "no changes" (fail safe — never drop
	// possibly-real work).

	// Bring the clone's actual HEAD commit into the parent under the
	// branch name, then merge. The fetch creates the local branch ref
	// lockedMerge merges.
	fetch, err := runGitHard([]string{"fetch", clone.Path, headSHA + ":" + clone.Branch}, clone.RepoDir, gitTimeoutS)
	if err != nil {
		if !IsOSOrSubprocessError(err) {
			return MergeResult{}, err
		}
		return MergeResult{Branch: clone.Branch,
			Detail: "fetch from scratch clone failed: " + err.Error() +
				"; work preserved in " + clone.Path}, nil
	}
	if fetch.ReturnCode != 0 {
		return MergeResult{Branch: clone.Branch,
			Detail: "fetch from scratch clone failed: " +
				clip(firstOr(fetch.Stderr, fetch.Stdout), 300) +
				"; work preserved in " + clone.Path}, nil
	}
	return lockedMerge(clone.RepoDir, clone.Branch, clone.BaseRef)
}

// CleanupClone is `cleanup_clone`: remove the scratch clone (and the
// branch fetched into the parent).
//
// keepOnFailure=true leaves both the clone dir and the fetched branch for
// inspection — the failure detail names them, so nothing is lost. The
// owner sidecar is kept alongside a kept clone so a later sweep still
// recognizes it.
func CleanupClone(clone ScratchClone, keepOnFailure bool) error {
	if keepOnFailure {
		warn("scratch clone kept for inspection: " + clone.Path +
			" (branch " + clone.Branch + ")")
		return nil
	}
	// The branch was only fetched into the parent on a merge attempt
	// (skipped for the "no changes" path); -D is best-effort and may find
	// nothing.
	b, err := runGit([]string{"branch", "-D", clone.Branch}, clone.RepoDir, 30)
	switch {
	case err != nil && !IsOSOrSubprocessError(err):
		return err
	case err != nil:
		debug("scratch-clone branch delete failed: " + err.Error())
	case b.ReturnCode != 0:
		debug("scratch-clone branch delete: " + clip(firstOr(b.Stderr, b.Stdout), 200))
	}
	// Outside the try in Python: the rmtree and the unlink run even when
	// the branch delete raised.
	removeTree(clone.Path)
	// Drop the owner breadcrumb alongside the now-removed clone (metadata
	// only — the clone's work already merged back, or there was none).
	sidecar, err := cloneSidecarPath(clone.Path)
	if err != nil {
		return err
	}
	return removeFile(sidecar)
}

// ---------------------------------------------------------------------
// Stale-clone sweep — recover-then-remove, retention-safe
// (CONTAINER_EXECUTOR_DESIGN §9 C3 residual). Rides
// heartbeat.stranded_state_sweep next to the container reap.
// ---------------------------------------------------------------------

// CloneSweepResult is the outcome of one stale-clone sweep — every clone
// lands in exactly one list.
//
// Retention invariant: a clone is REMOVED only when its owner is
// verifiably dead AND its work provably reached the live repo (merged) or
// provably never existed ("no changes"). Every other outcome preserves
// the clone on disk.
type CloneSweepResult struct {
	Recovered    []RecoveredClone // merged then removed
	RemovedEmpty []EmptyClone     // no unmerged work, removed
	Preserved    []PreservedClone // dead owner, work KEPT
	SkippedLive  []SkippedClone   // owner still running
	SkippedYoung []SkippedClone   // owner dead but within grace
	Surfaced     []SurfacedClone  // cannot decide, KEPT + logged
}

// RecoveredClone is the `(clone, branch, merged_commit)` tuple.
type RecoveredClone struct{ Clone, Branch, MergedCommit string }

// EmptyClone is the `(clone, branch)` tuple.
type EmptyClone struct{ Clone, Branch string }

// PreservedClone is the `(clone, branch, reason)` tuple.
type PreservedClone struct{ Clone, Branch, Reason string }

// SkippedClone is the `(clone, owner_pid)` tuple.
//
// OwnerPID is `any` and not `int`, because only ONE of the two lists it
// feeds has proved the value is an int. skipped_live is reached through
// `isinstance(owner_pid, int)`; skipped_young is reached when that test
// did NOT fire, so a sidecar carrying `"owner_pid": 1.5`, `"7"` or `null`
// lands there with that value verbatim and heartbeat serialises it into
// its result.
type SkippedClone struct {
	Clone    string
	OwnerPID any
}

// SurfacedClone is the `(clone, reason)` tuple.
type SurfacedClone struct{ Clone, Reason string }

// AsDict is `CloneSweepResult.as_dict()` — an ORDERED object, because
// heartbeat's stranded_state_sweep puts it straight into
// `result["swept_clones"]` and that result is serialised.
//
// The tuples become JSON ARRAYS, which is what json.dumps does with a
// Python tuple; a Go struct would have become an object with field names
// no CPython writer ever emitted.
func (r CloneSweepResult) AsDict() pyval.Obj {
	rec := pyval.List{}
	for _, x := range r.Recovered {
		rec = append(rec, pyval.List{x.Clone, x.Branch, x.MergedCommit})
	}
	emp := pyval.List{}
	for _, x := range r.RemovedEmpty {
		emp = append(emp, pyval.List{x.Clone, x.Branch})
	}
	pre := pyval.List{}
	for _, x := range r.Preserved {
		pre = append(pre, pyval.List{x.Clone, x.Branch, x.Reason})
	}
	live := pyval.List{}
	for _, x := range r.SkippedLive {
		live = append(live, pyval.List{x.Clone, x.OwnerPID})
	}
	young := pyval.List{}
	for _, x := range r.SkippedYoung {
		young = append(young, pyval.List{x.Clone, x.OwnerPID})
	}
	surf := pyval.List{}
	for _, x := range r.Surfaced {
		surf = append(surf, pyval.List{x.Clone, x.Reason})
	}
	return pyval.Obj{
		{Key: "recovered", Val: rec},
		{Key: "removed_empty", Val: emp},
		{Key: "preserved", Val: pre},
		{Key: "skipped_live", Val: live},
		{Key: "skipped_young", Val: young},
		{Key: "surfaced", Val: surf},
	}
}

// Acted is `acted()`: did the sweep do anything worth surfacing to the
// operator?
//
// Note which three lists it asks about. removed_empty and the two skips
// are deliberately NOT in it — an empty clone reaped and a live owner
// left alone are both nothing happening.
func (r CloneSweepResult) Acted() bool {
	return len(r.Recovered) > 0 || len(r.Preserved) > 0 || len(r.Surfaced) > 0
}

// SweepOptions carries the three injectable parameters.
//
// MinAgeS is a POINTER because its Python default is 900 and Go's zero
// value is 0 — and 0 means "no grace at all", which is the difference
// between skipping a two-minute-old clone and merging it back under its
// live owner. A nil pointer is the default.
type SweepOptions struct {
	PIDAlive     func(int) bool
	MinAgeS      *float64
	ProcessToken func(int) (string, bool)
	// Now overrides time.time() for tests. Nil means the real clock.
	Now func() time.Time
}

// SweepStrandedClones is `sweep_stranded_clones`: recover + reap scratch
// clones leaked by crashed self-dev runs.
//
// For each clone under `worktrees/` carrying an owner sidecar:
//
//   - owner PID + birth token match -> skip (never touch in-flight work);
//   - owner dead, clone younger than MinAgeS -> skip (grace);
//   - owner dead, old enough -> reconstruct the trusted ScratchClone from
//     the sidecar and attempt MergeBackClone to RECOVER any unmerged work:
//     merged / "no changes" -> the work is provably safe -> CleanupClone
//     removes the throwaway; conflict / moved-base / dirty-base / error ->
//     the work is NOT recovered -> the clone is PRESERVED (branch kept,
//     reason named) — retention wins.
//
// A clone dir WITHOUT a readable sidecar is only SURFACED (logged), never
// auto-removed: we can't prove whose it is or whether it holds unmerged
// work. Removal happens solely through CleanupClone (the one allowlisted
// deletion site) after a provably-safe merge — this function deletes
// nothing directly.
func SweepStrandedClones(opts SweepOptions) (CloneSweepResult, error) {
	alive := opts.PIDAlive
	if alive == nil {
		alive = pidAlive
	}
	tokenReader := opts.ProcessToken
	if tokenReader == nil {
		tokenReader = startToken
	}
	minAgeS := float64(cloneSweepGraceS)
	if opts.MinAgeS != nil {
		minAgeS = *opts.MinAgeS
	}
	nowFn := opts.Now
	if nowFn == nil {
		nowFn = time.Now
	}
	var result CloneSweepResult

	root := worktreesRoot()
	if !isDir(root) {
		return result, nil
	}

	now := float64(nowFn().UnixNano()) / 1e9
	seenDirs := map[string]bool{}

	// Sidecar-carrying clones first (the recoverable ones).
	for _, sidecar := range globTwoLevel(root, ownerSidecarSuffix) {
		// `sidecar.name[:-len(SUFFIX)]` is a code-point slice, and when
		// the name is EXACTLY the suffix it is the empty string —
		// `with_name("")` then raises ValueError, out of the whole
		// sweep. The glob can produce that name (fnmatch's `*` matches
		// the empty string, measured), so this is reachable, not
		// theoretical. Reproduced rather than corrected.
		name := pypath.Name(sidecar)
		stem := string([]rune(name)[:len([]rune(name))-len([]rune(ownerSidecarSuffix))])
		if stem == "" {
			return CloneSweepResult{}, &PyError{class: "ValueError", msg: "Invalid name ''"}
		}
		clonePath := pypath.Join(parentOf(sidecar), stem)
		if !isDir(clonePath) {
			// Orphan sidecar (clone already gone) — pure litter, no work
			// at risk. Leave it (removing it would be a second,
			// unnecessary deletion site); it is harmless and a future
			// clone at the same path overwrites it.
			debug("stale-clone sweep: orphan owner sidecar (no clone dir): " + sidecar)
			continue
		}
		seenDirs[resolveOrSelf(clonePath)] = true

		meta := readCloneOwner(sidecar)
		if meta == nil {
			result.Surfaced = append(result.Surfaced,
				SurfacedClone{clonePath, "unreadable owner sidecar"})
			warn("stale-clone sweep: unreadable owner sidecar for " + clonePath +
				" — left for inspection")
			continue
		}

		ownerPID, _ := meta.Get("owner_pid")
		ownerStart, _ := meta.Get("owner_start")
		if n, isInt := pyIsInt(ownerPID); isInt &&
			ownerIsCurrent(n, ownerStart, alive, tokenReader) {
			result.SkippedLive = append(result.SkippedLive, SkippedClone{clonePath, ownerPID})
			continue
		}

		age := minAgeS + 1 // can't stat -> don't let a stat error block recovery
		if st, err := os.Stat(clonePath); err == nil {
			age = now - float64(st.ModTime().UnixNano())/1e9
		}
		if age < minAgeS {
			// Owner dead but clone too young — wait out the grace before
			// acting (guards against racing a just-provisioned clone / a
			// fast resume).
			result.SkippedYoung = append(result.SkippedYoung, SkippedClone{clonePath, ownerPID})
			continue
		}

		rawRepo, _ := meta.Get("repo_dir")
		// `Path(str(meta.get("repo_dir") or ""))` — and Path("") is
		// PosixPath('.'), not an empty path. The `if not repo_dir` guard
		// on the next Python line is DEAD CODE: a Path has no __bool__,
		// so `not Path(...)` is always False (measured). What actually
		// gates this branch is is_git_repo, and for a sidecar with no
		// repo_dir that question is asked about the CURRENT WORKING
		// DIRECTORY. Reproduced, and named as a divergence candidate.
		repoDir := pypath.Str(pyval.StrOrEmpty(rawRepo))
		rawBase, _ := meta.Get("base_ref")
		baseRef := pyval.StrOrEmpty(rawBase)
		rawBranch, _ := meta.Get("branch")
		branch := pyval.StrOrEmpty(rawBranch)
		if branch == "" {
			branch = "maro/stale/" + pypath.Name(clonePath)
		}
		isRepo, err := IsGitRepo(repoDir)
		if err != nil {
			return CloneSweepResult{}, err
		}
		if !isRepo || baseRef == "" {
			result.Surfaced = append(result.Surfaced, SurfacedClone{clonePath,
				"live repo unresolved (" + repoDir + ") — cannot merge back"})
			warn("stale-clone sweep: " + clonePath + " owner dead but live repo " +
				repoDir + " unresolved — clone preserved for manual recovery")
			continue
		}

		clone := ScratchClone{Path: clonePath, Branch: branch, RepoDir: repoDir, BaseRef: baseRef}
		info("stale-clone sweep: owner PID " + pyval.Str(ownerPID) + " dead — attempting merge-back of " +
			clonePath + " (branch " + branch + ")")
		merge, err := MergeBackClone(clone, "stale-clone recovery: "+pypath.Name(clonePath))
		if err != nil {
			// `except Exception` — never let one bad clone abort the sweep.
			result.Preserved = append(result.Preserved,
				PreservedClone{clonePath, branch, "merge-back error: " + err.Error()})
			warn("stale-clone sweep: merge-back errored for " + clonePath +
				" — preserved: " + err.Error())
			continue
		}

		switch {
		case merge.OK && merge.Detail == "no changes":
			if err := CleanupClone(clone, false); err != nil {
				return CloneSweepResult{}, err
			}
			result.RemovedEmpty = append(result.RemovedEmpty, EmptyClone{clonePath, branch})
			info("stale-clone sweep: " + clonePath + " had no unmerged work — removed")
		case merge.OK:
			if err := CleanupClone(clone, false); err != nil {
				return CloneSweepResult{}, err
			}
			result.Recovered = append(result.Recovered,
				RecoveredClone{clonePath, branch, merge.MergedCommit})
			info("stale-clone sweep: recovered work from " + clonePath + " into " +
				repoDir + " (" + pyval.Clip(merge.MergedCommit, 12) + ") — removed")
		default:
			// Work NOT recovered — keep the clone + its branch, name the reason.
			if err := CleanupClone(clone, true); err != nil {
				return CloneSweepResult{}, err
			}
			result.Preserved = append(result.Preserved,
				PreservedClone{clonePath, branch, merge.Detail})
			warn("stale-clone sweep: could not merge " + clonePath +
				" back — preserved (branch " + branch + "): " + merge.Detail)
		}
	}

	// Sidecar-less clone dirs (crashed before the sidecar landed, or
	// pre-sweep leaks): SURFACE only. Without the trusted breadcrumb we
	// can't prove ownership/liveness or safely target a merge; retention
	// forbids removing.
	for _, cloneDir := range globTwoLevel(root, "-clone") {
		if !isDir(cloneDir) || seenDirs[resolveOrSelf(cloneDir)] {
			continue
		}
		result.Surfaced = append(result.Surfaced,
			SurfacedClone{cloneDir, "no owner sidecar — cannot recover automatically"})
		warn("stale-clone sweep: clone " + cloneDir + " has no owner sidecar — left for " +
			"manual inspection (cannot prove ownership or unmerged-work state)")
	}

	return result, nil
}

// globTwoLevel is `root.glob("*/*<suffix>")`, sorted the way CPython's
// `sorted()` sorts the Paths it yields.
//
// Four rules, each measured:
//
//   - The intermediate `*` matches DIRECTORIES and it FOLLOWS SYMLINKS —
//     pathlib's selector asks `entry.is_dir()` on an os.DirEntry, whose
//     default is follow_symlinks=True. Go's DirEntry.IsDir() never
//     follows, so a symlinked loop directory would vanish from the sweep
//     and its clones would never be recovered. pypath.EntryIsDir is the
//     shared answer.
//   - `*` matches HIDDEN names. `.owner.json` is matched by
//     `*.owner.json` with the star bound to the empty string, which is
//     what makes the with_name("") raise above reachable.
//   - A directory that cannot be read is SKIPPED, not an error:
//     pathlib's selector swallows the OSError.
//   - The sort is by CODE POINT over the surrogateescape decoding of the
//     whole path, not by byte. pypath.FSLess.
func globTwoLevel(root, suffix string) []string {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		if !pypath.EntryIsDir(root, e) {
			continue
		}
		sub := pypath.Join(root, e.Name())
		inner, err := os.ReadDir(sub)
		if err != nil {
			continue
		}
		for _, f := range inner {
			if len(f.Name()) >= len(suffix) && f.Name()[len(f.Name())-len(suffix):] == suffix {
				out = append(out, pypath.Join(sub, f.Name()))
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return pypath.FSLess(out[i], out[j]) })
	return out
}

// isDir is `Path.is_dir()`: it FOLLOWS symlinks and it is False, not an
// error, when the stat fails.
func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

// resolveOrSelf is `str(p.resolve())`.
//
// CPython's resolve() only raises on a symlink loop; pypath.Realpath's
// false answer is the no-working-directory case, which cannot happen for
// the absolute paths this feeds. The fallback keeps the set membership
// well-defined rather than inventing a third outcome.
func resolveOrSelf(p string) string {
	if r, ok := pypath.Realpath(p); ok {
		return r
	}
	return p
}

// pyIsInt is `isinstance(v, int)` — which in Python INCLUDES bool, and
// that is not pedantry: a sidecar carrying `"owner_pid": true` passes the
// test and is then handed to os.kill as the pid 1. pyval.IsInt
// deliberately excludes bool for its own callers, so the bool arm is
// added back here rather than changing the shared helper.
func pyIsInt(v any) (int, bool) {
	if b, ok := v.(bool); ok {
		if b {
			return 1, true
		}
		return 0, true
	}
	return pyval.IsInt(v)
}
