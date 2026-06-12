#!/usr/bin/env bash
# Install Poe's git guard hooks into this repo's .git/hooks.
# Part of harness install — safe to re-run (overwrites our hooks only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_SRC="$REPO_ROOT/scripts/hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

if [[ ! -d "$HOOKS_DST" ]]; then
  echo "ERROR[E_NOT_A_REPO] no .git/hooks at $HOOKS_DST" >&2
  exit 2
fi

for hook in "$HOOKS_SRC"/*; do
  name="$(basename "$hook")"
  cp "$hook" "$HOOKS_DST/$name"
  chmod +x "$HOOKS_DST/$name"
  echo "installed: $name"
done
