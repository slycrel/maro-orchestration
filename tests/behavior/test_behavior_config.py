"""Behavior: config resolution — docs/CONTRACTS.md B1.

The config files are on-disk workspace artifacts; the observable contract
is the resolution order (env pin → workspace tier over user tier → nested
one-level merge → defaults) as seen through the registered reader seam.
"""

import os
from pathlib import Path

from harness import workspace


def test_workspace_env_pin_and_tier_merge(tmp_path):
    """B1: MARO_WORKSPACE is the one env pin an engine must honor; the
    workspace config.yml overrides the user tier; nested dicts merge
    exactly one level deep; a missing/malformed file reads as {}."""
    ws = workspace()
    # The isolation fixture pinned MARO_WORKSPACE → tmp; the engine must
    # resolve exactly that (never ~/.maro/workspace).
    assert ws == Path(os.environ["MARO_WORKSPACE"])

    # User tier (MARO_USER_DIR/config.yml) — set by the operator.
    user_dir = Path(os.environ["MARO_USER_DIR"])
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "config.yml").write_text(
        "shared:\n  from_user: 1\n  overridden: user-wins\n"
        "user_only: alpha\n",
        encoding="utf-8",
    )
    # Workspace tier overrides.
    (ws / "config.yml").write_text(
        "shared:\n  overridden: workspace-wins\n  from_workspace: 2\n"
        "workspace_only: beta\n",
        encoding="utf-8",
    )

    from config import get, workspace_root

    assert workspace_root() == ws

    # Workspace wins on collision; both tiers contribute.
    assert get("shared.overridden") == "workspace-wins"
    assert get("user_only") == "alpha"
    assert get("workspace_only") == "beta"
    # Nested dicts merge one level deep: sibling keys from BOTH tiers
    # survive under the same parent.
    assert get("shared.from_user") == 1
    assert get("shared.from_workspace") == 2
    # Unknown key falls to the caller's hardcoded default.
    assert get("no.such.key", "fallback-default") == "fallback-default"


def test_malformed_config_reads_as_empty(tmp_path):
    """B1: a malformed config file reads as {} — resolution degrades to
    defaults instead of failing the engine."""
    ws = workspace()
    (ws / "config.yml").write_text("{{{: not yaml [", encoding="utf-8")

    from config import get

    assert get("anything.at.all", "safe-default") == "safe-default"
