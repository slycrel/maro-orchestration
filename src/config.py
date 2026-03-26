"""Centralized configuration and path resolution for poe-orchestration.

Priority order for workspace root:
  1. POE_WORKSPACE env var (new canonical name)
  2. OPENCLAW_WORKSPACE env var (backward compat)
  3. WORKSPACE_ROOT env var (backward compat)
  4. ~/.poe/workspace (default — no OpenClaw dependency)

Credentials env file priority:
  1. POE_ENV_FILE env var
  2. <workspace_root>/secrets/.env
  3. ~/.openclaw/workspace/secrets/recovered/runtime-credentials/.env (legacy)

This module must import only stdlib — no other poe modules — to avoid cycles.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Workspace root
# ---------------------------------------------------------------------------

def workspace_root() -> Path:
    """Return the canonical workspace root directory."""
    for var in ("POE_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        val = os.environ.get(var)
        if val:
            return Path(val).expanduser().resolve()
    return Path.home() / ".poe" / "workspace"


def memory_dir() -> Path:
    p = workspace_root() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def secrets_dir() -> Path:
    return workspace_root() / "secrets"


# ---------------------------------------------------------------------------
# Credentials / env file
# ---------------------------------------------------------------------------

def credentials_env_file() -> Path:
    """Return path to the .env credentials file (may not exist)."""
    custom = os.environ.get("POE_ENV_FILE")
    if custom:
        return Path(custom).expanduser()

    # Workspace-local secrets (preferred)
    local = secrets_dir() / ".env"
    if local.exists():
        return local

    # Legacy OpenClaw location
    legacy = Path.home() / ".openclaw" / "workspace" / "secrets" / "recovered" / "runtime-credentials" / ".env"
    if legacy.exists():
        return legacy

    # Return the local path (may not exist — callers check)
    return local


def load_credentials_env() -> dict[str, str]:
    """Load key=value pairs from credentials env file."""
    result: dict[str, str] = {}
    path = credentials_env_file()
    if not path.exists():
        return result
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# OpenClaw config (legacy compat — used by gateway + telegram_listener)
# ---------------------------------------------------------------------------

def openclaw_cfg_path() -> Path:
    """Return path to openclaw.json (may not exist on non-OpenClaw setups)."""
    custom = os.environ.get("OPENCLAW_CFG")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".openclaw" / "openclaw.json"


def load_openclaw_cfg() -> dict:
    """Load openclaw.json. Returns empty dict if not present."""
    path = openclaw_cfg_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Service / deploy paths
# ---------------------------------------------------------------------------

def deploy_dir() -> Path:
    """Return the deploy/ directory next to src/."""
    return Path(__file__).resolve().parent.parent / "deploy"
