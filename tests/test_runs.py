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


# ---------------------------------------------------------------------------
# Current-run context: artifact_dir / source_dir routing
# ---------------------------------------------------------------------------

from runs import (
    set_current_run_dir,
    current_run_dir,
    artifact_dir,
    source_dir,
)


@pytest.fixture(autouse=True)
def _clear_run_state():
    """Ensure the module-level current-run state doesn't leak between tests."""
    import runs as _runs
    set_current_run_dir(None)
    _runs._run_log_offsets.clear()
    _runs._run_repo_bases.clear()
    yield
    set_current_run_dir(None)
    _runs._run_log_offsets.clear()
    _runs._run_repo_bases.clear()


def test_set_and_get_current_run_dir(workspace):
    rd = create_run_dir("abcd1234", prompt="p")
    set_current_run_dir(rd)
    assert current_run_dir() == rd
    set_current_run_dir(None)
    assert current_run_dir() is None


def test_artifact_dir_uses_run_dir_when_active(workspace):
    rd = create_run_dir("abcd1234", prompt="p")
    set_current_run_dir(rd)
    out = artifact_dir("any-project")
    assert out == rd / "build"
    assert out.exists()


def test_artifact_dir_falls_back_to_project_root_fn(workspace):
    fallback_root = workspace / "fallback_projects"
    out = artifact_dir("my-proj", project_root_fn=lambda: fallback_root)
    assert out == fallback_root / "my-proj" / "artifacts"
    assert out.exists()


def test_artifact_dir_default_fallback_when_no_project_root_fn(workspace):
    # No run-dir set, no project_root_fn — must default into POE_WORKSPACE.
    out = artifact_dir("my-proj")
    assert out == workspace / "projects" / "my-proj" / "artifacts"
    assert out.exists()


def test_source_dir_returns_none_when_no_run_dir():
    assert source_dir() is None


def test_source_dir_returns_run_dir_source_when_active(workspace):
    rd = create_run_dir("abcd1234", prompt="p")
    set_current_run_dir(rd)
    src = source_dir()
    assert src == rd / "source"
    assert src.exists()


# ---------------------------------------------------------------------------
# Captain's log slicing
# ---------------------------------------------------------------------------

from runs import record_log_offset, slice_log_for_run


def test_slice_log_captures_only_run_window(workspace, tmp_path, monkeypatch):
    log_path = tmp_path / "captains_log.jsonl"
    log_path.write_text('{"event":"BEFORE_RUN"}\n', encoding="utf-8")

    # Point captains_log helpers at our test file.
    import captains_log
    monkeypatch.setattr(captains_log, "_log_path_override", log_path)

    create_run_dir("abcd1234", prompt="p")
    record_log_offset("abcd1234")

    # Two events written during the "run"
    with log_path.open("a", encoding="utf-8") as f:
        f.write('{"event":"DURING_1"}\n')
        f.write('{"event":"DURING_2"}\n')

    out = slice_log_for_run("abcd1234")
    assert out is not None
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "BEFORE_RUN" not in content
    assert "DURING_1" in content
    assert "DURING_2" in content


def test_slice_log_when_no_offset_recorded_includes_everything(workspace, tmp_path, monkeypatch):
    log_path = tmp_path / "captains_log.jsonl"
    log_path.write_text('{"event":"X"}\n', encoding="utf-8")
    import captains_log
    monkeypatch.setattr(captains_log, "_log_path_override", log_path)

    create_run_dir("abcd1234", prompt="p")
    # No record_log_offset call — offset defaults to 0 → whole file.
    out = slice_log_for_run("abcd1234")
    assert out is not None
    assert "X" in out.read_text(encoding="utf-8")


def test_slice_log_returns_none_when_run_dir_missing(workspace, tmp_path, monkeypatch):
    log_path = tmp_path / "captains_log.jsonl"
    log_path.write_text('{"event":"X"}\n', encoding="utf-8")
    import captains_log
    monkeypatch.setattr(captains_log, "_log_path_override", log_path)

    # Don't create the run-dir.
    assert slice_log_for_run("nonexist1") is None


def test_slice_log_returns_none_when_log_file_missing(workspace, tmp_path, monkeypatch):
    import captains_log
    monkeypatch.setattr(captains_log, "_log_path_override", tmp_path / "absent.jsonl")
    create_run_dir("abcd1234", prompt="p")
    assert slice_log_for_run("abcd1234") is None


# ---------------------------------------------------------------------------
# Repo bundle
# ---------------------------------------------------------------------------

import subprocess as _sp

from runs import record_repo_base, snapshot_repo_bundle


@pytest.fixture
def git_repo(tmp_path):
    """A git repo with one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _sp.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    _sp.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True, check=True)
    _sp.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("base\n")
    _sp.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    _sp.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    return repo


def test_repo_bundle_captures_state(workspace, git_repo):
    rd = create_run_dir("abcd1234", prompt="p")
    record_repo_base("abcd1234", str(git_repo))

    # Make a change after recording the base.
    (git_repo / "new.txt").write_text("after\n")
    _sp.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    _sp.run(["git", "commit", "-m", "after"], cwd=git_repo, capture_output=True, check=True)

    bundle = snapshot_repo_bundle("abcd1234")
    assert bundle is not None
    assert bundle.exists()
    assert bundle.name == "repo.bundle"
    assert bundle.parent == rd / "artifact"
    assert (rd / "artifact" / "git_log.txt").exists()
    assert (rd / "artifact" / "branch_diff.patch").exists()
    assert (rd / "artifact" / "base_sha.txt").exists()
    # Diff should include the new file content.
    diff = (rd / "artifact" / "branch_diff.patch").read_text()
    assert "new.txt" in diff


def test_repo_bundle_returns_none_when_no_base_recorded(workspace):
    create_run_dir("abcd1234", prompt="p")
    assert snapshot_repo_bundle("abcd1234") is None


def test_record_repo_base_handles_empty_repo_path(workspace):
    record_repo_base("abcd1234", "")  # no-op, no exception
    create_run_dir("abcd1234", prompt="p")
    assert snapshot_repo_bundle("abcd1234") is None


def test_record_repo_base_handles_non_git_dir(workspace, tmp_path):
    bogus = tmp_path / "notarepo"
    bogus.mkdir()
    record_repo_base("abcd1234", str(bogus))
    create_run_dir("abcd1234", prompt="p")
    # rev-parse fails → no entry recorded → snapshot returns None.
    assert snapshot_repo_bundle("abcd1234") is None
