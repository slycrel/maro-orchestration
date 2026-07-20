#!/usr/bin/env bash
# land.sh — landing half of the Hermes propose lane (see PROPOSE_LANE.md).
#
# Fetches one hermes/* proposal branch from the Mini's persistent clone,
# publishes it to GitHub, and fast-forwards main when the change is
# docs-only (every touched file *.md). Operates on refs only — never
# touches this checkout's working tree or index, so it is safe to run
# while other sessions work in the repo.
#
# Invoked by maro-ssh-gate.sh as:  land.sh hermes/<topic>
# Emits one JSON object on stdout — that is Hermes's receipt.
set -euo pipefail

REPO="/home/clawd/claude/maro-orchestration"
# "mini2" is an alias in ~/.ssh/config (key-authed, non-interactive).
MINI2_CLONE="mini2:/Users/clawd/.hermes/repos/maro-orchestration"

BRANCH="${1:-}"
if ! [[ "$BRANCH" =~ ^hermes/[A-Za-z0-9._-]{1,64}$ ]]; then
    echo '{"error": "branch must match hermes/<topic>"}' >&2
    exit 2
fi

cd "$REPO"

git fetch -q origin main
git fetch -q "$MINI2_CLONE" "+refs/heads/$BRANCH:refs/hermes-inbox/$BRANCH"

SHA="$(git rev-parse "refs/hermes-inbox/$BRANCH")"
MAIN="$(git rev-parse refs/remotes/origin/main)"

emit() { # status note [pr_url]
    STATUS="$1" NOTE="$2" PR_URL="${3:-}" BRANCH="$BRANCH" SHA="$SHA" \
    FILES="${FILES:-}" python3 -c '
import json, os
out = {
    "status": os.environ["STATUS"],
    "branch": os.environ["BRANCH"],
    "sha": os.environ["SHA"],
    "files": [f for f in os.environ["FILES"].splitlines() if f],
    "note": os.environ["NOTE"],
}
if os.environ["PR_URL"]:
    out["pr_url"] = os.environ["PR_URL"]
print(json.dumps(out))
'
}

if git merge-base --is-ancestor "$SHA" "$MAIN"; then
    emit "already-landed" "This proposal is already contained in main — nothing to do."
    exit 0
fi

# Publish the proposal branch. hermes/* is a proposal namespace — an
# amended re-send may legitimately rewrite it, so fall back to force for
# these refs only. main is never force-pushed by this script.
if ! git push -q origin "refs/hermes-inbox/$BRANCH:refs/heads/$BRANCH" 2>/dev/null; then
    git push -q --force origin "refs/hermes-inbox/$BRANCH:refs/heads/$BRANCH"
fi

BASE="$(git merge-base "$MAIN" "$SHA")"
FILES="$(git diff --name-only "$BASE" "$SHA")"

DOCS_ONLY=true
while IFS= read -r f; do
    [[ "$f" == *.md ]] || DOCS_ONLY=false
done <<< "$FILES"

PR_URL="https://github.com/slycrel/maro-orchestration/pull/new/$BRANCH"

if $DOCS_ONLY && [ "$BASE" = "$MAIN" ]; then
    # Plain push: GitHub rejects anything that isn't a fast-forward, so
    # this cannot clobber concurrent pushes — it either lands or fails.
    if git push -q origin "$SHA:refs/heads/main" 2>/dev/null; then
        git fetch -q origin main
        emit "landed-main" "Docs-only change fast-forwarded to main and pushed to GitHub."
    else
        emit "branch-pushed" "Docs-only, but main moved while landing — branch is on GitHub; re-send after 'maro-propose start' from fresh main, or merge via PR." "$PR_URL"
    fi
elif $DOCS_ONLY; then
    emit "branch-pushed" "Docs-only but based on a stale main — branch is on GitHub; re-create the proposal from fresh main ('maro-propose start') and re-send, or merge via PR." "$PR_URL"
else
    emit "branch-pushed" "Contains non-doc changes, so it will not auto-land — branch is on GitHub awaiting human review via PR." "$PR_URL"
fi
