#!/usr/bin/env bash
# Triage a dirty working tree: whose work is this, actually?
#
# A dirty path in a SHARED checkout is a two-valued signal, and reading it as
# one-valued is how landed work gets silently reverted (2026-08-06: a session
# saw three files dirty, correctly thought "someone is mid-chunk, stay away",
# and later committed them — reverting an executor tag scheme and a
# pytest-in-image change that had already landed and been pushed):
#
#   REAL   — another session's uncommitted work. Leave it alone.
#   STALE  — work that LANDED from elsewhere and was never materialized into
#            this tree. It looks identical to REAL in `git status`, but
#            committing it reverts whatever landed. Restore it.
#   BEHIND — the tree copy matches NO ancestor blob, but its diff against
#            HEAD is deletion-only: everything in it is already in HEAD,
#            and what's missing are lines other commits landed. This is
#            the stale-MIX shape (2026-08-15: a session's own edit was
#            committed and replayed onto newer main while the tree copy
#            kept the pre-rebase base — old base + own edit hashes like
#            "real work" but is strictly behind HEAD). A deliberate
#            deletion-in-progress looks identical, so --fix leaves BEHIND
#            paths alone; the report shows which commits last touched the
#            missing lines so you can judge, and --fix-behind restores
#            them once you have.
#
# The discriminator is mechanical: hash the working-tree file and look for
# that exact blob in recent ancestor commits. If the content is one an
# ancestor already recorded, this tree is behind, not ahead. No blob match
# + deletion-only diff vs HEAD → BEHIND (see above).
#
# Report-only by default. `--fix` restores just the STALE paths, never the
# REAL or BEHIND ones. See CLAUDE.md "Concurrent sessions — shared tree
# rules" (2).
#
#   scripts/tree-triage.sh              # report
#   scripts/tree-triage.sh --fix        # report, then restore STALE paths
#   scripts/tree-triage.sh --fix-behind # --fix, plus BEHIND paths too
#   scripts/tree-triage.sh --depth 100  # search further back (default 40)
#
# Portability: macOS ships bash 3.2, so no mapfile, and no bare "${arr[@]}"
# on a possibly-empty array under `set -u`. Stale paths accumulate in a
# NUL-delimited temp file rather than an array so paths with spaces (or
# newlines — git allows them) survive the round trip.

set -euo pipefail

DEPTH=40
FIX=0
FIX_BEHIND=0
while [ $# -gt 0 ]; do
    case "$1" in
        --fix)   FIX=1; shift ;;
        --fix-behind) FIX=1; FIX_BEHIND=1; shift ;;
        --depth) DEPTH="${2:?--depth needs a number}"; shift 2 ;;
        -h|--help) sed -n '2,43p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo" >&2; exit 2; }
cd "$(git rev-parse --show-toplevel)"

# Ancestors of HEAD only. A blob that appears solely on some unmerged branch
# says nothing about whether THIS tree is behind.
COMMITS="$(git rev-list HEAD -n "$DEPTH" 2>/dev/null || true)"

STALE_LIST="$(mktemp)"
BEHIND_LIST="$(mktemp)"
trap 'rm -f "$STALE_LIST" "$BEHIND_LIST"' EXIT
n_stale=0
n_real=0
n_behind=0

# Which recent commits last touched the lines a BEHIND file is missing?
# Blame HEAD over each removed hunk range; compact unique-sha summary so
# the "is this recently-landed work or an old deliberate cut?" judgment
# has evidence attached. Never fails the triage (blame is best-effort).
_behind_blame_summary() {
    _path="$1"
    # Hunk headers come out as "-N" or "-N,COUNT"; capture the whole
    # range token and split in shell — a sed backreference into an
    # unmatched optional group is undefined under POSIX BRE and BSD sed
    # (macOS) has diverged from GNU on it (review r1).
    git diff --unified=0 HEAD -- "$_path" 2>/dev/null \
      | sed -n 's/^@@ -\([0-9][0-9,]*\) .*/\1/p' \
      | while read -r _range; do
            _start="${_range%%,*}"
            _count="${_range#*,}"
            [ "$_count" = "$_range" ] && _count=1
            [ "$_count" -eq 0 ] && continue
            git blame -l -L "$_start,$((_start + _count - 1))" HEAD -- "$_path" 2>/dev/null \
              | awk '{print $1}'
        done \
      | awk '!seen[$1]++' | head -3 \
      | while read -r _sha; do
            git log -1 --format='      missing lines from %h %ad %s' \
                --date=format:'%m-%d' "$_sha" 2>/dev/null
        done
    # first-appearance dedup, not sort -u: hash-lexicographic order would
    # sample an arbitrary 3-of-N and could drop the actual landing commit
    # on multi-commit ranges (review r1).
}

# -z: NUL-delimited, so paths with spaces/newlines survive. Tracked changes
# only — an untracked file is nobody's stale copy by definition.
while IFS= read -r -d '' entry; do
    xy="${entry:0:2}"
    path="${entry:3}"
    case "$xy" in
        '??'|'!!') continue ;;
    esac

    if [ ! -e "$path" ]; then
        # Present in the index, absent from the tree. Two readers again
        # (adversarial review 2026-08-06 R3-5): `git reset --mixed` onto a
        # newer ref does exactly this to files the new commits ADDED — but
        # another session's intentional `rm` looks identical, and restoring
        # it violates the "real work untouched" contract. Discriminate
        # symmetrically to the modify case: absence is STALE only when some
        # recent ancestor also lacked the path (the tree can be an honest
        # snapshot of that pre-add state). If every ancestor has it, no
        # historical state explains the absence — that's a deletion in
        # progress. And a staged change (X != space) can never be a reset
        # artifact: reset --mixed leaves index == HEAD.
        if [ "${xy:0:1}" != " " ]; then
            n_real=$((n_real + 1))
            printf '%-46s %s\n' "$path" "REAL uncommitted work (staged) — leave alone"
            continue
        fi
        match=""
        for c in $COMMITS; do
            if ! git cat-file -e "$c:$path" 2>/dev/null; then
                match="$c"
                break
            fi
        done
        if [ -n "$match" ]; then
            printf '%s\0' "$path" >> "$STALE_LIST"
            n_stale=$((n_stale + 1))
            printf '%-46s %s\n' "$path" "STALE (absent here and in ancestor ${match:0:7})"
        else
            n_real=$((n_real + 1))
            printf '%-46s %s\n' "$path" "REAL uncommitted work (deleted; every recent ancestor has it) — leave alone"
        fi
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
        continue
    fi

    # No ancestor blob match. Before calling it REAL, check for the
    # stale-MIX shape (2026-08-15): deletion-only vs HEAD means every
    # line here is already in HEAD and the tree is strictly behind —
    # a landed-elsewhere state this tree never materialized. numstat
    # "added deleted"; binary files report "-" and fall through to REAL.
    read -r n_added n_deleted _ <<EOF || true
$(git diff --numstat HEAD -- "$path" 2>/dev/null)
EOF
    if [ "${n_added:-x}" = "0" ] && [ "${n_deleted:-0}" != "0" ] \
        && [ "${n_deleted:-x}" != "-" ]; then
        printf '%s\0' "$path" >> "$BEHIND_LIST"
        n_behind=$((n_behind + 1))
        printf '%-46s %s\n' "$path" \
            "BEHIND (nothing new vs HEAD; missing landed lines) — judge, then --fix-behind"
        _behind_blame_summary "$path" || true
    else
        n_real=$((n_real + 1))
        printf '%-46s %s\n' "$path" "REAL uncommitted work — leave alone"
    fi
done < <(git status --porcelain -z)

echo
echo "$n_stale stale, $n_behind behind, $n_real real."

[ "$n_stale" -eq 0 ] && [ "$n_behind" -eq 0 ] && exit 0

# A STALE verdict means "an ancestor recorded this exact content". That is
# strong evidence the tree is behind — but a DELIBERATE revert back to older
# content looks the same. Eyeball the list before --fix. BEHIND is weaker
# evidence still (a deletion-in-progress is indistinguishable by content),
# so plain --fix never touches it — restoring BEHIND takes the explicit
# --fix-behind, after the blame summary has been judged.
if [ "$FIX" -eq 1 ]; then
    if [ "$n_stale" -gt 0 ]; then
        echo "restoring stale paths (real ones untouched):"
        while IFS= read -r -d '' p; do
            git checkout -- "$p"
            echo "  restored $p"
        done < "$STALE_LIST"
    fi
    if [ "$n_behind" -gt 0 ]; then
        if [ "$FIX_BEHIND" -eq 1 ]; then
            echo "restoring behind paths (--fix-behind):"
            while IFS= read -r -d '' p; do
                git checkout -- "$p"
                echo "  restored $p"
            done < "$BEHIND_LIST"
        else
            echo "BEHIND paths NOT restored (needs --fix-behind after judging the blame summary)"
        fi
    fi
else
    if [ "$n_stale" -gt 0 ]; then
        echo "to restore stale:   scripts/tree-triage.sh --fix"
        echo "(a deliberate revert to older content also reads as STALE — skim the list first)"
    fi
    if [ "$n_behind" -gt 0 ]; then
        echo "to restore behind:  scripts/tree-triage.sh --fix-behind (judge the blame lines first)"
    fi
fi
