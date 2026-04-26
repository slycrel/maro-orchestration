"""Tests for per-run isolation: nickname + run-dir destination."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from runs import (
    nickname,
    runs_root,
    run_dir,
    create_run_dir,
    write_metadata,
    finalize_run,
    _ADJECTIVES,
    _NOUNS,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    return tmp_path


def test_nickname_is_deterministic():
    assert nickname("abcd1234") == nickname("abcd1234")


def test_nickname_differs_for_different_handle_ids():
    a = nickname("aaaaaaaa")
    b = nickname("bbbbbbbb")
    assert a != b


def test_nickname_format():
    nick = nickname("deadbeef")
    parts = nick.split("-")
    assert len(parts) == 2
    assert parts[0] in _ADJECTIVES
    assert parts[1] in _NOUNS


def test_nickname_empty_handle_id():
    assert nickname("") == "unset-run"


def test_runs_root_honors_workspace_env(workspace):
    assert runs_root() == workspace / "runs"


def test_run_dir_combines_handle_id_and_nickname(workspace):
    rd = run_dir("abcd1234")
    assert rd.parent == workspace / "runs"
    assert rd.name.startswith("abcd1234-")
    assert rd.name == f"abcd1234-{nickname('abcd1234')}"


def test_create_run_dir_seeds_skeleton(workspace):
    rd = create_run_dir(
        "abcd1234",
        prompt="ship the thing",
        lane="agenda",
        model="cheap",
    )
    assert rd.exists()
    assert (rd / "source").is_dir()
    assert (rd / "build").is_dir()
    assert (rd / "artifact").is_dir()
    assert (rd / "source" / "prompt.txt").read_text() == "ship the thing"
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["handle_id"] == "abcd1234"
    assert meta["nickname"] == nickname("abcd1234")
    assert meta["prompt"] == "ship the thing"
    assert meta["lane"] == "agenda"
    assert meta["model"] == "cheap"
    assert meta["started_at"] is not None
    assert meta["ended_at"] is None
    assert meta["status"] is None


def test_create_run_dir_is_idempotent(workspace):
    rd1 = create_run_dir("abcd1234", prompt="first")
    started = json.loads((rd1 / "metadata.json").read_text())["started_at"]
    # Mid-run prompt.txt should not be overwritten — first call wins.
    rd2 = create_run_dir("abcd1234", prompt="second")
    assert rd1 == rd2
    assert (rd2 / "source" / "prompt.txt").read_text() == "first"
    # started_at preserved across re-create
    meta = json.loads((rd2 / "metadata.json").read_text())
    assert meta["started_at"] == started


def test_write_metadata_preserves_prior_fields(workspace):
    rd = create_run_dir("abcd1234", prompt="p", lane="now")
    # Simulate an earlier finalize that recorded ended_at
    finalize_run("abcd1234", status="ok", ended_at="2026-04-26T10:00:00+00:00")
    # Subsequent write_metadata without status/ended_at keeps prior values
    write_metadata(rd, handle_id="abcd1234", prompt="p", lane="now", model="mid")
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["status"] == "ok"
    assert meta["ended_at"] == "2026-04-26T10:00:00+00:00"
    assert meta["model"] == "mid"


def test_finalize_run_sets_status_and_ended_at(workspace):
    create_run_dir("abcd1234", prompt="p")
    finalize_run("abcd1234", status="completed")
    meta = json.loads(((workspace / "runs") / f"abcd1234-{nickname('abcd1234')}" / "metadata.json").read_text())
    assert meta["status"] == "completed"
    assert meta["ended_at"] is not None


def test_finalize_run_returns_none_for_missing_run(workspace):
    assert finalize_run("nonexist", status="x") is None


def test_create_run_dir_extra_metadata(workspace):
    rd = create_run_dir(
        "abcd1234",
        prompt="p",
        extra_metadata={"experiment": "scope-ab", "arm": "treat"},
    )
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["experiment"] == "scope-ab"
    assert meta["arm"] == "treat"


def test_nickname_distribution_smoke():
    """Sanity check: 100 random handle_ids should produce >50 unique nicknames."""
    import secrets
    nicks = {nickname(secrets.token_hex(4)) for _ in range(100)}
    assert len(nicks) > 50
