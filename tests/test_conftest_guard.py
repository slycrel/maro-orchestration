"""Regression test for conftest.py recursion guard.

Rationale: session 18 subprocess leak (160+ pytest workers, 12GB RAM) motivated
a guard that refuses nested pytest sessions on this test tree. This test makes
sure the guard actually fires and that clean invocations aren't affected.
"""

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def _tiny_collect():
    """Collect a single known-fast test — cheapest valid pytest invocation."""
    return [
        sys.executable, "-m", "pytest",
        str(TESTS_DIR / "test_conftest_guard.py"),
        "--collect-only", "-q",
    ]


def test_recursion_guard_blocks_preset_active_env():
    env = os.environ.copy()
    env["POE_PYTEST_ACTIVE"] = "99999"  # pretend a parent pytest is running
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        _tiny_collect(), cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2, f"expected exit=2, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "Recursive pytest blocked" in (result.stdout + result.stderr)


def test_recursion_guard_can_be_cleared_for_legitimate_reentry():
    """If a harness explicitly needs nested pytest, clearing the env unblocks it.

    Documents the intended escape hatch: `del env['POE_PYTEST_ACTIVE']` before
    spawning a child pytest. Scoped subprocess pytest that doesn't pick up this
    conftest wouldn't need this; this is only for something re-entering *this*
    tree deliberately (e.g. a future meta-test of the guard itself).
    """
    env = os.environ.copy()
    env["POE_PYTEST_ACTIVE"] = "99999"  # pretend a parent is running
    # ... but the caller clears it before exec, proving it's the escape hatch.
    env.pop("POE_PYTEST_ACTIVE", None)
    result = subprocess.run(
        _tiny_collect(), cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"expected exit=0, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"


def test_recursion_guard_clean_invocation():
    """No env var set → pytest runs normally."""
    env = os.environ.copy()
    env.pop("POE_PYTEST_ACTIVE", None)
    result = subprocess.run(
        _tiny_collect(), cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"expected exit=0, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
