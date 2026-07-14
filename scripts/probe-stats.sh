#!/usr/bin/env bash
# Rolling CLAIM_PROBED reviewer-calibration report. No jq dependency.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO_DIR/scripts/probe_stats.py" "$@"
