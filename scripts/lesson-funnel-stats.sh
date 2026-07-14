#!/usr/bin/env bash
# Rolling lesson-intake funnel report. No jq dependency.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO_DIR/scripts/lesson_funnel_stats.py" "$@"
