#!/usr/bin/env bash
# land.sh — land the current branch's commits onto main by fast-forward.
#
# This box's blessed path for getting a finished chunk onto main WITHOUT a PR.
# Policy (Jeremy, 2026-07-20): PRs are the Poe/Hermes lane (deploy/hermes/,
# mini2, dispatched-autonomous work under human review); the maro box lands its
# own directed work directly. Reuses the SSH `origin` remote — no GitHub API
# token needed for the push itself. A live gh token (re-minted 2026-08-01)
# additionally enables the detached post-land CI watch (scripts/ci-watch.sh);
# landing works fine without one, just blind to Actions.
#
# Safety by construction:
#   - pushes to main ONLY as a fast-forward; GitHub itself rejects a non-ff, and
#     this script never uses --force on main.
#   - refuses a dirty working tree when landing HEAD (commit first).
#   - a ref that has diverged from origin/main is AUTO-REBASED: the commits
#     are replayed onto fresh origin/main in a TEMP worktree (never the
#     caller's tree — shared-tree rule 1) and the replayed sha is landed.
#     Conflicts abort to manual with the recipe printed. --no-rebase
#     restores the old refuse-outright behavior.
#   - caller's tree: refs/index only — after an auto-rebased HEAD landing
#     the local ref is converged onto the landed sha (reset --mixed) and
#     stale paths are materialized via scripts/tree-triage.sh --fix, which
#     restores only content already recorded by ancestor commits. The
#     working tree is never rewritten beyond that (same discipline as
#     deploy/hermes/land.sh).
#
# Usage:
#   scripts/land.sh                # land current HEAD onto main
#   scripts/land.sh <ref>          # land a specific branch / sha
#   scripts/land.sh --dry-run      # show what would land, push nothing
#   scripts/land.sh --skip-checks  # bypass the pre-land structural gate
#   scripts/land.sh --no-rebase    # refuse a diverged ref instead of auto-rebasing
set -euo pipefail

DRY=false
SKIP_CHECKS=false
NO_REBASE=false
REF=HEAD
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=true ;;
        --skip-checks) SKIP_CHECKS=true ;;
        --no-rebase) NO_REBASE=true ;;
        -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
        -*) echo "unknown flag: $a" >&2; exit 2 ;;
        *)  REF="$a" ;;
    esac
done

cd "$(git rev-parse --show-toplevel)"
# Caller's repo root, resolved BEFORE the gate cds into its temp worktree.
# The gate needs it to find a .venv interpreter (the worktree has none).
REPO_DIR="$(pwd)"

# Clean tree required only when landing HEAD — a named ref lands regardless of
# unrelated worktree edits.
if [ "$REF" = "HEAD" ] && ! git diff --quiet HEAD 2>/dev/null; then
    echo "refuse: working tree has uncommitted changes — commit them first." >&2
    exit 1
fi

SHA="$(git rev-parse --verify "${REF}^{commit}")"
git fetch -q origin main
MAIN="$(git rev-parse refs/remotes/origin/main)"

if [ "$SHA" = "$MAIN" ]; then
    echo "nothing to land — ${REF} is already at origin/main."
    exit 0
fi

# Fast-forward only: origin/main must be an ancestor of the ref being landed.
# When it isn't (a landing race was lost), auto-rebase (2026-08-16, Jeremy):
# replay the commits onto fresh origin/main in a TEMP worktree — never the
# caller's tree, per shared-tree rule 1 — and land the replayed sha.
# Conflicts abort to manual with the recipe printed.
ORIG_SHA=""
if ! git merge-base --is-ancestor "$MAIN" "$SHA"; then
    if $NO_REBASE; then
        echo "refuse: ${REF} has diverged from origin/main (not a fast-forward)." >&2
        echo "       rebase onto fresh main first:  git fetch origin main && git rebase origin/main" >&2
        exit 1
    fi
    if [ -z "$(git rev-list "${MAIN}..${SHA}")" ]; then
        # Diverged but contributes nothing new: SHA is strictly behind main.
        echo "nothing to land — all commits of ${REF} are already contained in origin/main."
        exit 0
    fi
    if [ -n "$(git rev-list --merges "${MAIN}..${SHA}")" ]; then
        # cherry-pick can't replay merge commits; this repo's landings are
        # linear by construction, so hitting this means something unusual —
        # a human should look rather than the script guessing -m parents.
        echo "refuse: ${REF} contains merge commits — auto-rebase can't replay those." >&2
        echo "       rebase manually in a worktree, then land the result." >&2
        exit 1
    fi
    RB_N="$(git rev-list --count "${MAIN}..${SHA}")"
    echo "auto-rebase: origin/main moved — replaying ${RB_N} commit(s) onto ${MAIN:0:12} in a temp worktree"
    RB_WT="$(mktemp -d /tmp/land-rebase.XXXXXX)"
    git worktree add --detach -q "$RB_WT" "$MAIN"
    if ! git -C "$RB_WT" cherry-pick "${MAIN}..${SHA}" >/dev/null 2>&1; then
        git -C "$RB_WT" cherry-pick --abort >/dev/null 2>&1 || true
        git worktree remove --force "$RB_WT" >/dev/null 2>&1 || true
        echo "refuse: auto-rebase hit conflicts — resolve manually (in a worktree, not the shared tree):" >&2
        echo "       git worktree add ../maro-wt-land origin/main && cd ../maro-wt-land" >&2
        echo "       git cherry-pick ${MAIN:0:12}..${SHA:0:12}    # resolve, then: git cherry-pick --continue" >&2
        echo "       bash scripts/land.sh HEAD && cd - && git worktree remove ../maro-wt-land" >&2
        exit 1
    fi
    ORIG_SHA="$SHA"
    SHA="$(git -C "$RB_WT" rev-parse HEAD)"
    # The replayed commits live in the shared object store; the worktree
    # has served its purpose. Eager removal (not a trap) because the
    # pre-land gate below installs its own EXIT trap, which would replace
    # ours and leak the directory.
    git worktree remove --force "$RB_WT" >/dev/null 2>&1 || true
    echo "auto-rebase: ${ORIG_SHA:0:12} -> ${SHA:0:12} (clean replay)"
fi

N="$(git rev-list --count "${MAIN}..${SHA}")"
echo "landing ${N} commit(s) onto main:"
git --no-pager log --oneline "${MAIN}..${SHA}"
echo "files:"
LANDED_FILES="$(git diff --name-only "$MAIN" "$SHA")"
sed 's/^/  /' <<<"$LANDED_FILES"

if $DRY; then
    echo "(dry-run — nothing pushed)"
    exit 0
fi

# Red-main check (2026-08-09): a session landed six commits onto a red main
# with no watcher running — nobody was pinged until an unrelated land
# inherited the failure. If main's newest completed CI run is red, say so
# LOUDLY before stacking more commits on it. Warn, don't refuse: the commit
# being landed is often the fix itself. Needs a gh token; silent without one.
if ! $SKIP_CHECKS && gh auth status >/dev/null 2>&1; then
    RED_ROW="$(gh run list --repo slycrel/maro-orchestration --branch main \
        --status completed --limit 1 --json conclusion,headSha,url \
        --jq '.[0] | "\(.conclusion)|\(.headSha)|\(.url)"' 2>/dev/null || true)"
    RED_CONCLUSION="${RED_ROW%%|*}"
    case "$RED_CONCLUSION" in
        success|skipped|cancelled|"") : ;;
        *)
            RED_REST="${RED_ROW#*|}"
            echo "WARNING: newest completed CI run on main is ${RED_CONCLUSION} @ ${RED_REST%%|*}" >&2
            echo "         ${RED_REST#*|}" >&2
            echo "         landing anyway — if this land is not the fix, main stays red on you." >&2
            ;;
    esac
fi

# Pre-land structural gate (2026-08-01; Jeremy's CI-visibility ask after a
# doc-only land shipped frontmatter-less history docs and CI went red for
# an hour). Doc-only commits are exactly the ones that land without a
# suite run, so the structural censuses they'd have failed run HERE,
# against the exact tree being landed (temp worktree — never the caller's
# tree), scoped to what the diff touches. CI stays the authority;
# --skip-checks is the emergency bypass.
if ! $SKIP_CHECKS; then
    CHECKS=()
    CHANGED="$LANDED_FILES"
    if grep -q '^docs/' <<<"$CHANGED"; then
        CHECKS+=(tests/test_docs_frontmatter.py)
    fi
    if grep -Eq '^(src/|docs/DEFAULTS\.md)' <<<"$CHANGED"; then
        CHECKS+=(tests/test_defaults_doc.py)
    fi
    if [ "${#CHECKS[@]}" -gt 0 ]; then
        GATE_WT="$(mktemp -d /tmp/land-gate.XXXXXX)"
        GATE_LOG="${GATE_WT}.log"
        trap 'git worktree remove --force "$GATE_WT" >/dev/null 2>&1 || rm -rf "$GATE_WT"' EXIT
        git worktree add --detach -q "$GATE_WT" "$SHA"
        echo "pre-land gate: ${CHECKS[*]}"
        # Pick an interpreter that actually HAS pytest. On the box that's
        # python3; on the dev Mac the homebrew python3 (3.14) ships without
        # it and only the repo .venv has it, so a bare `python3 -m pytest`
        # made the gate unrunnable there — and an unrunnable gate that
        # refuses the land is indistinguishable from a real census failure.
        # Resolve against the CALLER's repo (the temp worktree has no .venv),
        # then against the PRIMARY checkout. That second lookup is what makes
        # the conflict recipe this script itself prints actually work: it
        # tells you to resolve in `git worktree add ../maro-wt-land` and land
        # from there, but a linked worktree has no .venv either, so on the dev
        # Mac the gate refused and the documented path dead-ended (hit
        # 2026-08-16, landing a conflict resolution). git's common-dir is the
        # primary repo's .git from anywhere in the worktree set, so its parent
        # is the checkout that owns the venv.
        GATE_PY="python3"
        GATE_CANDIDATES=("$REPO_DIR/.venv/bin/python")
        _COMMON="$(git rev-parse --git-common-dir 2>/dev/null || true)"
        if [ -n "$_COMMON" ]; then
            _PRIMARY="$(cd "$(dirname "$_COMMON")" 2>/dev/null && pwd)"
            [ -n "$_PRIMARY" ] && GATE_CANDIDATES+=("$_PRIMARY/.venv/bin/python")
        fi
        GATE_FOUND=false
        for _cand in "${GATE_CANDIDATES[@]}"; do
            if [ -x "$_cand" ] && "$_cand" -c "import pytest" >/dev/null 2>&1; then
                GATE_PY="$_cand"
                GATE_FOUND=true
                break
            fi
        done
        if ! $GATE_FOUND && ! python3 -c "import pytest" >/dev/null 2>&1; then
            echo "refuse: no interpreter with pytest for the pre-land gate" >&2
            echo "       tried python3 and: ${GATE_CANDIDATES[*]}" >&2
            echo "       install pytest, or bypass with --skip-checks." >&2
            exit 1
        fi
        if ! (cd "$GATE_WT" && PYTHONPATH=src "$GATE_PY" -m pytest -q "${CHECKS[@]}" >"$GATE_LOG" 2>&1); then
            tail -30 "$GATE_LOG" >&2
            echo "refuse: pre-land structural gate failed for ${SHA} (full log: ${GATE_LOG})" >&2
            echo "       fix the census failure, or bypass with --skip-checks (CI will still catch it)." >&2
            exit 1
        fi
    fi
fi

# ff-only push to main over SSH. Never --force on main.
git push origin "${SHA}:refs/heads/main" || {
    echo "push rejected — origin/main moved again during this land." >&2
    echo "rerun scripts/land.sh: the auto-rebase will replay onto the new tip." >&2
    exit 1
}
echo "landed: origin/main -> ${SHA}"

# After an auto-rebased HEAD landing, the caller's checked-out ref still
# points at the PRE-replay commits — converge it (ref/index only; the
# working tree is never rewritten, per shared-tree rule 1) and materialize
# paths the upstream commits touched. Without this, the local branch reads
# as diverged-again on the next land and upstream files sit stale in the
# tree — the exact two-valued-dirty-signal trap tree-triage exists for.
# Scoped to REF=HEAD: an explicit-ref land is someone orchestrating by
# hand; moving their checkout out from under them is not this script's call.
if [ -n "$ORIG_SHA" ] && [ "$REF" = "HEAD" ]; then
    git reset --mixed -q "$SHA"
    echo "converged: local ref -> ${SHA:0:12} (ref/index only, tree untouched)"
    # A path whose working copy still MATCHES the pre-replay commit is this
    # session's own content in older clothing — the replay carried it into
    # ${SHA} (verbatim or merged with upstream's edits), but the pre-replay
    # commit is no longer an ancestor of main, so tree-triage's ancestor
    # search cannot prove it stale and conservatively calls it REAL (first
    # live fire, 2026-08-16: GOAL_BRAIN.md, where two sessions' journal
    # entries had auto-merged). The blob compare against ORIG_SHA is exact,
    # so restoring from the landed commit provably loses nothing.
    git diff --name-only | while IFS= read -r p; do
        if [ -f "$p" ] && \
           [ "$(git hash-object -- "$p")" = \
             "$(git rev-parse -q --verify "${ORIG_SHA}:${p}" 2>/dev/null || echo none)" ]; then
            git checkout -q -- "$p"
            echo "materialized: $p (working copy matched the pre-replay commit)"
        fi
    done
    if [ -x "$REPO_DIR/scripts/tree-triage.sh" ]; then
        bash "$REPO_DIR/scripts/tree-triage.sh" --fix || true
    else
        echo "note: paths touched by the replayed-over upstream commits may show" >&2
        echo "      stale in git status until materialized (tree-triage.sh not found)." >&2
    fi
fi

# Post-land reading-page refresh (2026-08-03, Jeremy: he'd assumed the page
# "got updated on commit", and found it three days stale because it only ever
# rendered on run finalize — so a queue row landed in a quiet stretch stayed
# invisible on the surface he actually reads). Fires ONLY when this land
# touched the queue doc, renders from the LANDED blob (not the working tree,
# which can differ when landing a named ref or landing from a worktree), and
# never fails the land — the push already succeeded and a stale page is not
# worth a nonzero exit.
if grep -qx 'docs/READING_QUEUE\.md' <<<"$LANDED_FILES"; then
    RQ_PY="python3"
    if [ -x "$REPO_DIR/.venv/bin/python" ]; then
        RQ_PY="$REPO_DIR/.venv/bin/python"
    fi
    RQ_TMP="$(mktemp /tmp/reading-queue.XXXXXX.md)"
    if git show "${SHA}:docs/READING_QUEUE.md" >"$RQ_TMP" 2>/dev/null && \
       OUT=$(cd "$REPO_DIR" && PYTHONPATH=src "$RQ_PY" -c '
import sys
from pathlib import Path
import loop_report
p = loop_report.write_reading_page(queue_doc=Path(sys.argv[1]))
print(p or "")
' "$RQ_TMP" 2>/dev/null) && [ -n "$OUT" ]; then
        echo "reading page refreshed: ${OUT}"
    else
        echo "reading page refresh skipped (non-fatal)" >&2
    fi
    rm -f "$RQ_TMP"
fi

# Post-land CI watch (2026-08-01): detached watcher polls the Actions run
# for this SHA and Telegram-pings ONLY on a red conclusion (green and
# superseded-by-newer-push runs are silent). Needs a live gh token —
# skipped quietly without one, landing itself never depends on it.
# macOS ships no setsid, and `( setsid ... & ) || true` swallowed the
# command-not-found, so the spawn was silently inert on the dev Mac
# (BACKLOG, fixed 2026-08-16): fall back to bare nohup there — the
# double-fork subshell already detaches from job control, and losing the
# new-session bit only matters for terminal signals nohup blocks anyway.
WATCHER="$(git rev-parse --show-toplevel)/scripts/ci-watch.sh"
if gh auth status >/dev/null 2>&1 && [ -x "$WATCHER" ]; then
    if command -v setsid >/dev/null 2>&1; then
        ( setsid nohup "$WATCHER" "$SHA" \
            >>/tmp/ci-watch.log 2>&1 < /dev/null & ) || true
    else
        ( nohup "$WATCHER" "$SHA" \
            >>/tmp/ci-watch.log 2>&1 < /dev/null & ) || true
    fi
    echo "ci-watch: spawned for ${SHA} (log: /tmp/ci-watch.log)"
fi

# Post-land dev-status line (2026-08-16, Jeremy: "I'm often trapped with less
# visibility in where we actually are at the high level"). Landing is exactly
# when project state changes, so this is the honest moment to recompute — but
# it only PRINTS. It deliberately does not rewrite docs/DEV_LOG.md here: a
# hook that dirties a tracked file makes a stranger appear in another
# session's `git status`, which CLAUDE.md concurrency rule 3 exists to
# prevent. Fold the block in on purpose with `dev-status --write` and commit
# it with your chunk. Never fails the land — the push already succeeded.
DS_PY="python3"
[ -x "$REPO_DIR/.venv/bin/python" ] && DS_PY="$REPO_DIR/.venv/bin/python"
if DS_LINE=$(cd "$REPO_DIR" && PYTHONPATH=src "$DS_PY" src/cli.py dev-status \
        --format line 2>/dev/null) && [ -n "$DS_LINE" ]; then
    echo "$DS_LINE"
    # Say when the written block has drifted from what just landed, rather
    # than letting the readout quietly become another stale surface.
    # Track DEV_STATUS.md, not DEV_LOG.md: the readout moved to its own file
    # (src/dev_status.py DOC, and the rationale above it). Watching DEV_LOG
    # here meant the nag keyed off narrative session entries instead of the
    # generated block, so `dev-status --write` — the very command named in
    # the message — could never clear it. Found 2026-08-20 during a doc
    # hygiene pass, after it fired on four consecutive lands.
    DS_BLOCK_AGE=$(cd "$REPO_DIR" && git log -1 --format=%cr -- docs/DEV_STATUS.md 2>/dev/null)
    [ -n "$DS_BLOCK_AGE" ] && echo "  (dev status last written ${DS_BLOCK_AGE}; refresh with: maro dev-status --write)"
fi
