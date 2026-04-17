#!/usr/bin/env bash
# Blind test for slycrel-go headless-server goal.
#
# Sets up a sterilized working copy of the repo (no remote, no feature
# branches) at a known path, clears any prior project workspace, then
# launches `handle` with a prompt that points at the local path.
#
# This only sets up the repo for the test. It does NOT wipe captain's log,
# memory, or self-evolved skills/personas — those are learned state across
# runs and should carry forward. It also does NOT sweep other stale
# slycrel clones on the box; that was a one-time cleanup.
#
# Re-runnable: safe to invoke multiple times. The working dir is torn down
# and rebuilt from the tarball each time.
#
# Usage:
#   scripts/blind-test-slycrel.sh              # setup + run
#   scripts/blind-test-slycrel.sh --setup-only # setup only, print next cmd
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURES_DIR="$REPO_ROOT/scripts/blind-test-slycrel"
SNAPSHOT_TAR="$REPO_ROOT/artifacts/blind-test-slycrel/slycrel-go-main.tar.gz"
PROMPT_FILE="$FIXTURES_DIR/prompt.txt"
SHA_FILE="$FIXTURES_DIR/snapshot-sha.txt"
UPSTREAM_URL="git@github.com:slycrel/slycrel-go.git"
WORK_ROOT="/tmp/slycrel-blind-run"
REPO_DIR="$WORK_ROOT/repo"
LOG_FILE="/tmp/slycrel-blind-run.log"
WORKSPACE_DIR_PATTERN="/home/clawd/.poe/workspace/projects/for-this-project-*"

setup_only=0
for arg in "$@"; do
  case "$arg" in
    --setup-only) setup_only=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "ERROR: prompt file missing: $PROMPT_FILE" >&2
  exit 1
fi
if [[ ! -f "$SHA_FILE" ]]; then
  echo "ERROR: snapshot SHA missing: $SHA_FILE" >&2
  exit 1
fi
SNAPSHOT_SHA="$(tr -d '[:space:]' < "$SHA_FILE")"

echo "=== Blind test setup ==="
echo "pinned SHA: $SNAPSHOT_SHA"

# 1. Fresh working dir — prefer local tarball, fall back to clone-at-sha
rm -rf "$WORK_ROOT"
mkdir -p "$WORK_ROOT"
if [[ -f "$SNAPSHOT_TAR" ]]; then
  echo "source    : local tarball"
  tar xzf "$SNAPSHOT_TAR" -C "$WORK_ROOT"
  mv "$WORK_ROOT/slycrel-snapshot" "$REPO_DIR"
else
  echo "source    : fresh clone (tarball absent, rebuilding)"
  git clone --quiet "$UPSTREAM_URL" "$REPO_DIR"
  (cd "$REPO_DIR" && git checkout --quiet "$SNAPSHOT_SHA")
  # Rebuild tarball for next time (mirrors the original sterilization)
  mkdir -p "$(dirname "$SNAPSHOT_TAR")"
  (cd "$REPO_DIR" && git remote remove origin 2>/dev/null || true
   rm -rf .git/refs/remotes .git/packed-refs 2>/dev/null || true)
  (cd "$WORK_ROOT" && mv repo slycrel-snapshot && \
   tar czf "$SNAPSHOT_TAR" slycrel-snapshot && mv slycrel-snapshot repo)
fi

# 2. Sterilize the git repo: no remote, no leftover feature branches
cd "$REPO_DIR"
git remote remove origin 2>/dev/null || true
# Delete any non-main branches (feature branches, archives)
for br in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
  if [[ "$br" != "main" ]]; then
    git branch -D "$br" >/dev/null 2>&1 || true
  fi
done
# Remove any stray remote-tracking refs
rm -rf .git/refs/remotes .git/packed-refs 2>/dev/null || true
git gc --quiet --prune=now 2>/dev/null || true

echo "repo  : $REPO_DIR"
echo "head  : $(git rev-parse --short HEAD)  ($(git log -1 --pretty=%s))"
echo "branches:"
git branch -a | sed 's/^/  /'
echo "remote:"
git remote -v | sed 's/^/  /' || echo "  (none)"

# 3. Clear any prior project workspace from previous runs with this prompt
cd "$REPO_ROOT"
for d in $WORKSPACE_DIR_PATTERN; do
  if [[ -d "$d" && "$d" != *.archive-* ]]; then
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    mv "$d" "${d}.archive-${ts}"
    echo "archived prior workspace: $(basename "$d") -> $(basename "${d}.archive-${ts}")"
  fi
done

echo
echo "prompt:"
cat "$PROMPT_FILE" | sed 's/^/  /'
echo

if [[ "$setup_only" -eq 1 ]]; then
  echo "--setup-only: skipping run."
  echo "to launch manually:"
  echo "  cd $REPO_ROOT && PYTHONPATH=src python3 -m handle \"\$(cat $PROMPT_FILE)\" > $LOG_FILE 2>&1 &"
  exit 0
fi

# 4. Launch
cd "$REPO_ROOT"
prompt=$(cat "$PROMPT_FILE")
echo "launching handle (log: $LOG_FILE)..."
PYTHONPATH=src python3 -m handle "$prompt" > "$LOG_FILE" 2>&1 &
pid=$!
echo "PID: $pid"
echo "tail: tail -f $LOG_FILE"
