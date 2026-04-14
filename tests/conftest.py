"""Global test fixtures — workspace isolation + recursion guard.

Every test gets its own tmp workspace so nothing leaks into ~/.poe/workspace/.
Individual tests that already set OPENCLAW_WORKSPACE via monkeypatch will
override this (monkeypatch wins over os.environ in the same scope).

The recursion guard (pytest_configure) refuses to start a second pytest
session if one is already active in the process tree. See rationale below.
"""

import os
import pytest


# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------
#
# History: session 18 regression runs hit a process-leak scenario where a
# subprocess `claude -p` spawned 160+ pytest workers against the wrong
# codebase, consuming 12GB RAM. Evolver's verify_post_apply path also shells
# out to `pytest` on this same tree after auto-applying mutations. If either
# of those fires *from inside* a pytest session, we get nested/recursive full-
# suite runs — at best wasting minutes, at worst fork-bombing the box.
#
# Scoped subprocess pytest (a skill validator running pytest against a tmp
# test dir) is fine — that child won't load this conftest.py. The guard only
# catches children that re-enter *this* project's test tree.

_ACTIVE_ENV = "POE_PYTEST_ACTIVE"


def pytest_configure(config):
    active = os.environ.get(_ACTIVE_ENV)
    if active and active != str(os.getpid()):
        pytest.exit(
            f"Recursive pytest blocked: parent pytest pid={active} is already "
            "running against this tree. Full-suite recursion causes runaway "
            "fork-bombs (session 18: 160+ workers / 12GB RAM). If a test needs "
            "to invoke pytest, target a scoped tmp directory whose tree does "
            "not include tests/conftest.py — that child won't trip this guard.",
            returncode=2,
        )
    os.environ[_ACTIVE_ENV] = str(os.getpid())


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get(_ACTIVE_ENV) == str(os.getpid()):
        os.environ.pop(_ACTIVE_ENV, None)

# API key env vars that should never leak into tests.  Tests that need a real
# adapter explicitly set these; everything else gets isolation for free.
_API_KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)

# Env vars that point at credential files — redirect to nowhere so tests
# never discover real keys from disk (e.g. legacy ~/.openclaw/ fallback).
_CREDENTIAL_PATH_VARS = (
    "POE_ENV_FILE",
    "OPENCLAW_CFG",
)


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path):
    """Route all workspace resolution to a per-test tmp dir.

    Sets POE_WORKSPACE (highest priority in config.workspace_root()) so that
    memory_dir(), output_dir(), projects_dir(), and everything downstream
    writes to tmp_path instead of the real workspace.

    Also hides API keys so tests never accidentally hit real LLM endpoints.
    Tests that explicitly need a key can set it via monkeypatch.
    """
    saved = {}
    # Workspace isolation
    saved["POE_WORKSPACE"] = os.environ.get("POE_WORKSPACE")
    os.environ["POE_WORKSPACE"] = str(tmp_path)

    # API key isolation — stash and remove
    for var in _API_KEY_VARS:
        saved[var] = os.environ.pop(var, None)

    # Credential file isolation — point at nonexistent paths so legacy
    # fallbacks (e.g. ~/.openclaw/) never feed real keys to build_adapter().
    for var in _CREDENTIAL_PATH_VARS:
        saved[var] = os.environ.get(var)
        os.environ[var] = str(tmp_path / "no-such-credentials")

    yield

    # Restore everything
    for var, val in saved.items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val
