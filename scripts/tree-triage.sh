#!/usr/bin/env bash
# Triage a dirty working tree: whose work is this, actually?
#
# A dirty path in a SHARED checkout is a two-valued signal, and reading it as
# one-valued is how landed work gets silently reverted (2026-08-06: a session
# saw three files dirty, correctly thought "someone is mid-chunk, stay away",
# and later committed them — reverting an executor tag scheme and a
# pytest-in-image change that had already landed and been pushed):
#
#   REAL  — another session's uncommitted work. Leave it alone.
#   STALE — work that LANDED from elsewhere and was never materialized into
#           this tree. It looks identical to REAL in `git status`, but
#           committing it reverts whatever landed. Restore it.
#
# The discriminator is mechanical: hash the working-tree file and look for
# that exact blob in recent ancestor commits. If the content is one an
# ancestor already recorded, this tree is behind, not ahead.
#
# Report-only by default. `--fix` restores just the STALE paths, never the
# REAL ones. See CLAUDE.md "Concurrent sessions — shared tree rules" (2).
#
#   scripts/tree-triage.sh              # report
#   scripts/tree-triage.sh --fix        # report, then restore STALE paths
#   scripts/tree-triage.sh --depth 100  # search further back (default 40)
#
# Portability: macOS ships bash 3.2, so no mapfile, and no bare "${arr[@]}"
# on a possibly-empty array under `set -u`. Stale paths accumulate in a
# NUL-delimited temp file rather than an array so paths with spaces (or
# newlines — git allows them) survive the round trip.

set -euo pipefail

DEPTH=40
FIX=0
while [ $# -gt 0 ]; do
    case "$1" in
        --fix)   FIX=1; shift ;;
        --depth) DEPTH="${2:?--depth needs a number}"; shift 2 ;;
        -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo" >&2; exit 2; }
cd "$(git rev-parse --show-toplevel)"

# Ancestors of HEAD only. A blob that appears solely on some unmerged branch
# says nothing about whether THIS tree is behind.
COMMITS="$(git rev-list HEAD -n "$DEPTH" 2>/dev/null || true)"

STALE_LIST="$(mktemp)"
trap 'rm -f "$STALE_LIST"' EXIT
n_stale=0
n_real=0

# -z: NUL-delimited, so paths with spaces/newlines survive. Tracked changes
# only — an untracked file is nobody's stale copy by definition.
while IFS= read -r -d '' entry; do
    xy="${entry:0:2}"
    path="${entry:3}"
    case "$xy" in
        '??'|'!!') continue ;;
    esac

    if [ ! -e "$path" ]; then
        # Present in HEAD, absent from the tree. `git reset --mixed` onto a
        # newer ref does exactly this to files the new commits ADDED: HEAD and
        # index move, the working tree does not. Always stale.
        printf '%s\0' "$path" >> "$STALE_LIST"
        n_stale=$((n_stale + 1))
        printf '%-46s %s\n' "$path" "STALE (missing from tree, present in HEAD)"
        continue
    fi

    hash="$(git hash-object -- "$path")"
    match=""
    for c in $COMMITS; do
        if [ "$(git rev-parse "$c:$path" 2>/dev/null || true)" = "$hash" ]; then
            match="$c"
            break
        fi
    done

    if [ -n "$match" ]; then
        printf '%s\0' "$path" >> "$STALE_LIST"
        n_stale=$((n_stale + 1))
        printf '%-46s %s\n' "$path" "STALE (matches ancestor ${match:0:7})"
    else
        n_real=$((n_real + 1))
        printf '%-46s %s\n' "$path" "REAL uncommitted work — leave alone"
    fi
done < <(git status --porcelain -z)

echo
echo "$n_stale stale, $n_real real."

[ "$n_stale" -eq 0 ] && exit 0

# A STALE verdict means "an ancestor recorded this exact content". That is
# strong evidence the tree is behind — but a DELIBERATE revert back to older
# content looks the same. Eyeball the list before --fix.
if [ "$FIX" -eq 1 ]; then
    echo "restoring stale paths (real ones untouched):"
    while IFS= read -r -d '' p; do
        git checkout -- "$p"
        echo "  restored $p"
    done < "$STALE_LIST"
else
    echo "to restore just those:  scripts/tree-triage.sh --fix"
    echo "(a deliberate revert to older content also reads as STALE — skim the list first)"
fi
