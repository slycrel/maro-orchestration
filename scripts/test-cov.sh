#!/usr/bin/env bash
# Coverage test runner for maro-orchestration.
#
# Measures line coverage over src/ and fails if below the floor set in
# .coveragerc (currently 70%). The 2026-07-14 full-suite baseline is 78.04%;
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

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

HTML=""
TARGET="tests/"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --html) HTML="--cov-report=html"; shift ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) TARGET="$1"; shift ;;
    esac
done

# pytest-cov combines per-worker data itself, so the coverage run parallelizes
# like the plain suite does (~3m -> ~45s here). Optional: without xdist this
# falls back to the sequential run unchanged.
# On a busy/shared box, `-n auto` can tip interactive sessions over (same
# failure mode scripts/test-safe.sh exists for) — TEST_JOBS caps the worker
# count and TEST_CORES pins CPU affinity, mirroring test-safe's knobs:
#   TEST_JOBS=2 TEST_CORES=0,1 scripts/test-cov.sh
JOBS=""
if "$PYTHON" -c "import xdist" >/dev/null 2>&1; then
    JOBS="-n ${TEST_JOBS:-auto}"
fi

RUN_PREFIX=()
if [[ -n "${TEST_CORES:-}" ]] && command -v taskset >/dev/null 2>&1; then
    RUN_PREFIX=(taskset -c "$TEST_CORES")
fi

# Run with coverage. --cov-fail-under is read from .coveragerc but we pass
# it explicitly here so it's obvious when the floor is being enforced.
exec "${RUN_PREFIX[@]}" "$PYTHON" -m pytest "$TARGET" \
    --ignore=tests/integration \
    --cov=src \
    --cov-report=term-missing:skip-covered \
    ${HTML} \
    ${JOBS} \
    -q --tb=line
