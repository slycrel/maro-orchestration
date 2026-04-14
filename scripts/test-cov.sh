#!/usr/bin/env bash
# Coverage test runner for openclaw-orchestration.
#
# Measures line coverage over src/ and fails if below the floor set in
# .coveragerc (currently 70%). The baseline after session 20.5 is ~73%;
# the floor is a ratchet — tighten it upward as coverage improves.
#
# Usage:
#   scripts/test-cov.sh                 # run full suite with coverage
#   scripts/test-cov.sh tests/test_foo  # run single file with coverage
#   scripts/test-cov.sh --html          # also produce HTML report in output/coverage_html
#
# Why a separate script: coverage adds ~30–50% runtime overhead. We don't
# want to pay it for every `pytest tests/foo.py` during normal dev, only
# when checking the overall health of the suite.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HTML=""
TARGET="tests/"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --html) HTML="--cov-report=html"; shift ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) TARGET="$1"; shift ;;
    esac
done

# Run with coverage. --cov-fail-under is read from .coveragerc but we pass
# it explicitly here so it's obvious when the floor is being enforced.
exec python3 -m pytest "$TARGET" \
    --ignore=tests/integration \
    --cov=src \
    --cov-report=term-missing:skip-covered \
    ${HTML} \
    -q --tb=line
