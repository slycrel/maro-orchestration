"""Global test fixtures — workspace isolation.

Every test gets its own tmp workspace so nothing leaks into ~/.poe/workspace/.
Individual tests that already set OPENCLAW_WORKSPACE via monkeypatch will
override this (monkeypatch wins over os.environ in the same scope).
"""

import os
import pytest

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
