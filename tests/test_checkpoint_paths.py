"""Checkpoint storage location pins — census item 5b (2026-08-06).

Non-run-dir checkpoints live under the workspace (config.workspace_root()/
checkpoints), not orch_root — the old anchoring wrote them into whatever
orch_root resolved to, the repo checkout in production. Pre-move files at
orch_root()/checkpoints stay readable and consumable (retention decree:
never moved or deleted automatically).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import checkpoint as ckpt_module
from checkpoint import Checkpoint, load_checkpoint, mark_checkpoint_consumed


def _write_old_location_checkpoint(loop_id: str) -> Path:
    """Seed a checkpoint at the pre-move orch_root()/checkpoints location."""
    import orch_items

    old_dir = orch_items.orch_root() / "checkpoints"
    old_dir.mkdir(parents=True, exist_ok=True)
    c = Checkpoint(loop_id=loop_id, goal="old goal", project="proj",
                   steps=["step 1", "step 2"], completed=[])
    path = old_dir / f"ckpt_{loop_id}.json"
    path.write_text(json.dumps(c.to_dict()), encoding="utf-8")
    return path


def test_checkpoint_dir_is_workspace_rooted(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    assert ckpt_module._checkpoint_dir() == tmp_path / "checkpoints"


def test_load_falls_back_to_old_orch_root_location(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _write_old_location_checkpoint("old12345")
    loaded = load_checkpoint("old12345")
    assert loaded is not None
    assert loaded.loop_id == "old12345"
    assert loaded.goal == "old goal"


def test_consume_works_on_old_location_file(tmp_path, monkeypatch):
    # A resumed pre-move checkpoint must still be consumable in place —
    # otherwise its replay protection silently no-ops.
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    old_path = _write_old_location_checkpoint("old67890")
    assert mark_checkpoint_consumed("old67890", resumed_to_loop_id="new00001")
    data = json.loads(old_path.read_text(encoding="utf-8"))
    assert data["consumed_at"]
    assert data["resumed_to_loop_id"] == "new00001"


def test_old_dir_never_created(tmp_path, monkeypatch):
    # The fallback is read-only: resolving paths must not mkdir the old
    # location back into existence.
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import orch_items

    old_dir = orch_items.orch_root() / "checkpoints"
    assert not old_dir.exists()
    ckpt_module._checkpoint_dir()
    assert load_checkpoint("nope0000") is None
    assert not old_dir.exists()


def _pin_orch_root_only(tmp_path, monkeypatch):
    """MARO_ORCH_ROOT with no workspace var — repo-local/container mode."""
    for var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT",
                "MARO_MEMORY_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))


def test_orch_root_only_pin_keeps_checkpoints_local(tmp_path, monkeypatch):
    # 2026-08-06 adversarial-review fix: MARO_ORCH_ROOT-only (build-loop.sh
    # no-arg, containers) must NOT leak checkpoints into the production
    # workspace — data rides the orch root.
    _pin_orch_root_only(tmp_path, monkeypatch)
    assert ckpt_module._checkpoint_dir() == tmp_path / "checkpoints"
    # And the "old location" is the same dir here, so no fallback candidates.
    assert ckpt_module._old_checkpoint_dirs() == []


def test_list_checkpoints_dedups_by_loop_id(tmp_path, monkeypatch):
    # Same loop_id present at both the current and the pre-move location
    # (operator copied files forward) → one entry, newest copy wins.
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import os
    import time

    old_path = _write_old_location_checkpoint("dup00001")
    new_dir = ckpt_module._checkpoint_dir()
    c = Checkpoint(loop_id="dup00001", goal="new goal", project="proj",
                   steps=["step 1"], completed=[])
    new_path = new_dir / "ckpt_dup00001.json"
    new_path.write_text(json.dumps(c.to_dict()), encoding="utf-8")
    now = time.time()
    os.utime(old_path, (now - 100, now - 100))
    os.utime(new_path, (now, now))

    ckpts = [c for c in ckpt_module.list_checkpoints()
             if c.loop_id == "dup00001"]
    assert len(ckpts) == 1
    assert ckpts[0].goal == "new goal"
