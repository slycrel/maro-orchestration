#!/usr/bin/env bash
# land.sh — land the current branch's commits onto main by fast-forward.
#
# This box's blessed path for getting a finished chunk onto main WITHOUT a PR.
# Policy (Jeremy, 2026-07-20): PRs are the Poe/Hermes lane (deploy/hermes/,
# mini2, dispatched-autonomous work under human review); the maro box lands its
# own directed work directly. Reuses the SSH `origin` remote — no GitHub API
# token needed (the gh token on this box is dead and stays moot for this path).
#
# Safety by construction:
#   - pushes to main ONLY as a fast-forward; GitHub itself rejects a non-ff, and
#     this script never uses --force on main.
#   - refuses a dirty working tree when landing HEAD (commit first).
#   - refuses a branch that has diverged from origin/main (rebase first).
#   - touches refs only — never mutates a working tree, so it is safe to run
#     while other sessions work in the repo (same discipline as
#     deploy/hermes/land.sh).
#
# Usage:
#   scripts/land.sh                # land current HEAD onto main
#   scripts/land.sh <ref>          # land a specific branch / sha
#   scripts/land.sh --dry-run      # show what would land, push nothing
#   scripts/land.sh --skip-checks  # bypass the pre-land structural gate
set -euo pipefail

DRY=false
SKIP_CHECKS=false
REF=HEAD
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=true ;;
        --skip-checks) SKIP_CHECKS=true ;;
        -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
        -*) echo "unknown flag: $a" >&2; exit 2 ;;
        *)  REF="$a" ;;
    esac
done

cd "$(git rev-parse --show-toplevel)"

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
if ! git merge-base --is-ancestor "$MAIN" "$SHA"; then
    echo "refuse: ${REF} has diverged from origin/main (not a fast-forward)." >&2
    echo "       rebase onto fresh main first:  git fetch origin main && git rebase origin/main" >&2
    exit 1
fi

N="$(git rev-list --count "${MAIN}..${SHA}")"
echo "landing ${N} commit(s) onto main:"
git --no-pager log --oneline "${MAIN}..${SHA}"
echo "files:"
git diff --name-only "$MAIN" "$SHA" | sed 's/^/  /'

if $DRY; then
    echo "(dry-run — nothing pushed)"
    exit 0
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
    CHANGED="$(git diff --name-only "$MAIN" "$SHA")"
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
        if ! (cd "$GATE_WT" && python3 -m pytest -q "${CHECKS[@]}" >"$GATE_LOG" 2>&1); then
            tail -30 "$GATE_LOG" >&2
            echo "refuse: pre-land structural gate failed for ${SHA} (full log: ${GATE_LOG})" >&2
            echo "       fix the census failure, or bypass with --skip-checks (CI will still catch it)." >&2
            exit 1
        fi
    fi
fi

# ff-only push to main over SSH. Never --force on main.
git push origin "${SHA}:refs/heads/main"
echo "landed: origin/main -> ${SHA}"
