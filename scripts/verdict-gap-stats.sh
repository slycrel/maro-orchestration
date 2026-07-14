#!/usr/bin/env bash
# Prospective organic done-vs-achieved re-audit gate. No jq dependency.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO_DIR/scripts/verdict_gap_stats.py" "$@"
