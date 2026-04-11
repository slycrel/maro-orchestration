"""Centralized configuration and path resolution for poe-orchestration.

Two-tier config (like git):
  ~/.poe/config.yml          — user-level (API keys, model prefs, notifications)
  ~/.poe/workspace/config.yml — workspace-level (evolver, inspector, personas)
  Workspace inherits from user; workspace keys override user keys.

Priority order for workspace root:
  1. POE_WORKSPACE env var (new canonical name)
  2. OPENCLAW_WORKSPACE env var (backward compat)
  3. WORKSPACE_ROOT env var (backward compat)
  4. ~/.poe/workspace (default — no OpenClaw dependency)

Credentials env file priority:
  1. POE_ENV_FILE env var
  2. <workspace_root>/secrets/.env
  3. ~/.openclaw/workspace/secrets/recovered/runtime-credentials/.env (legacy)

This module must import only stdlib + yaml — no other poe modules — to avoid cycles.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

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


def output_dir() -> Path:
    p = workspace_root() / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


def projects_dir() -> Path:
    p = workspace_root() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# YAML config — two-tier (user + workspace)
# ---------------------------------------------------------------------------

def _poe_dir() -> Path:
    """~/.poe — user-level Poe directory."""
    return Path.home() / ".poe"


def _user_config_path() -> Path:
    return _poe_dir() / "config.yml"


def _workspace_config_path() -> Path:
    return workspace_root() / "config.yml"


def _load_yaml(path: Path) -> dict:
    """Load a YAML file. Returns {} if missing/malformed."""
    if not path.exists():
        return {}
    try:
        import yaml
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# Cached merged config — loaded once per process
_config_cache: Optional[dict] = None


def load_config(*, reload: bool = False) -> dict:
    """Load merged config: user-level + workspace-level (workspace wins).

    Cached after first call. Pass reload=True to re-read from disk.
    """
    global _config_cache
    if _config_cache is not None and not reload:
        return _config_cache

    user = _load_yaml(_user_config_path())
    workspace = _load_yaml(_workspace_config_path())

    # Shallow merge: workspace keys override user keys.
    # Nested dicts are merged one level deep (e.g. model.default_tier).
    merged = dict(user)
    for k, v in workspace.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v

    _config_cache = merged
    return merged


def get(key: str, default: Any = None) -> Any:
    """Get a config value by dot-separated key path.

    Examples:
        get("model.default_tier", "cheap")
        get("evolver.auto_apply", True)
        get("yolo", False)
    """
    cfg = load_config()
    parts = key.split(".")
    node = cfg
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def config_paths() -> dict:
    """Return both config paths and whether they exist (for diagnostics)."""
    u = _user_config_path()
    w = _workspace_config_path()
    return {
        "user": str(u),
        "user_exists": u.exists(),
        "workspace": str(w),
        "workspace_exists": w.exists(),
    }


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
