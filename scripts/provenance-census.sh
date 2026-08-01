#!/usr/bin/env bash
# Run-paper-trail census (BACKLOG LT-0c/d). Read-only, stdlib-only, no src
# imports — safe to run while the box is doing directed work.
#
# Niced + detached-friendly by default so it never competes with a live run:
#   bash scripts/provenance-census.sh                  # foreground, nice 15
#   setsid nohup bash scripts/provenance-census.sh &   # fully detached
#
# Artifacts land in output/provenance_census/ (census.json + census.txt).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${MARO_CENSUS_OUT:-$REPO_DIR/output/provenance_census}"

exec nice -n 15 python3 "$REPO_DIR/scripts/provenance_census.py" \
    --out "$OUT_DIR" "$@"
