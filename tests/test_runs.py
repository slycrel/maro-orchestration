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
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
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


def test_current_handle_id_from_pinned_run_dir(workspace):
    from runs import current_handle_id
    rd = create_run_dir("abcd1234", prompt="p")
    set_current_run_dir(rd)
    assert current_handle_id() == "abcd1234"
    set_current_run_dir(None)
    assert current_handle_id() is None


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
    # No run-dir set, no project_root_fn — must default into MARO_WORKSPACE.
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


# ---------------------------------------------------------------------------
# Environment snapshot + skills manifest + metadata stamp (2026-07-09,
# per-run attribution inputs — the verify->learn prerequisite)
# ---------------------------------------------------------------------------

def test_environment_snapshot_writes_config_era(workspace):
    from runs import create_run_dir, set_current_run_dir, write_environment_snapshot
    rd = create_run_dir("envtest1", prompt="p")
    set_current_run_dir(rd)
    try:
        out = write_environment_snapshot()
        assert out is not None and out.exists()
        snap = json.loads(out.read_text())
        assert "captured_at" in snap
        assert "config" in snap                     # scrubbed effective config
        assert "host" in snap and snap["host"].get("python")
        # this repo is a git checkout, so the sha should resolve
        assert snap.get("maro_git_sha")
        # MARO_WORKSPACE is set by the fixture -> must appear as an override
        assert "MARO_WORKSPACE" in snap.get("env_overrides", {})
    finally:
        set_current_run_dir(None)


def test_environment_snapshot_scrubs_secret_env_values(workspace, monkeypatch):
    from runs import create_run_dir, set_current_run_dir, write_environment_snapshot
    secret = "sk-ant-api03-verysecretvalue1234567890abcdef"
    monkeypatch.setenv("MARO_FAKE_KEY", secret)
    rd = create_run_dir("envtest2", prompt="p")
    set_current_run_dir(rd)
    try:
        out = write_environment_snapshot()
        assert secret not in out.read_text()
    finally:
        set_current_run_dir(None)


def test_environment_snapshot_none_without_run_dir():
    from runs import set_current_run_dir, write_environment_snapshot
    set_current_run_dir(None)
    assert write_environment_snapshot() is None


def test_skills_manifest_appends_per_stage(workspace):
    from runs import create_run_dir, set_current_run_dir, append_skills_manifest
    rd = create_run_dir("skmtest1", prompt="p")
    set_current_run_dir(rd)
    try:
        append_skills_manifest(
            [{"id": "s1", "name": "deploy-check", "content_hash": "abc123",
              "variant_of": None, "tier": 2, "routing_key": "deadbeef"}],
            stage="decompose",
        )
        append_skills_manifest(
            [{"name": "curated-one", "file_path": "/x/curated-one.md"}],
            stage="curated_summaries",
        )
        lines = (rd / "source" / "skills_manifest.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        first, second = json.loads(lines[0]), json.loads(lines[1])
        assert first["stage"] == "decompose"
        assert first["skills"][0]["name"] == "deploy-check"
        assert first["skills"][0]["routing_key"] == "deadbeef"
        assert second["stage"] == "curated_summaries"
    finally:
        set_current_run_dir(None)


def test_skills_manifest_noop_on_empty_or_no_run_dir(workspace):
    from runs import create_run_dir, set_current_run_dir, append_skills_manifest
    set_current_run_dir(None)
    assert append_skills_manifest([{"name": "x"}], stage="decompose") is None
    rd = create_run_dir("skmtest2", prompt="p")
    set_current_run_dir(rd)
    try:
        assert append_skills_manifest([], stage="decompose") is None
        assert not (rd / "source" / "skills_manifest.jsonl").exists()
    finally:
        set_current_run_dir(None)


def test_stamp_run_metadata_merges_without_clobbering(workspace):
    from runs import create_run_dir, set_current_run_dir, stamp_run_metadata
    rd = create_run_dir("stamptest", prompt="the goal")
    set_current_run_dir(rd)
    try:
        stamp_run_metadata({"persona": "poe", "persona_confidence": 0.91,
                            "persona_fallback": False, "skip_me": None})
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["persona"] == "poe"
        assert meta["persona_confidence"] == 0.91
        assert "skip_me" not in meta            # None values don't stamp
        assert meta["prompt"] == "the goal"     # core fields untouched
    finally:
        set_current_run_dir(None)


def test_environment_snapshot_records_backend_order(workspace):
    from runs import create_run_dir, set_current_run_dir, write_environment_snapshot
    rd = create_run_dir("envtest3", prompt="p")
    set_current_run_dir(rd)
    try:
        snap = json.loads(write_environment_snapshot().read_text())
        assert isinstance(snap.get("backends"), list) and snap["backends"]
        b = snap["backends"][0]
        assert set(b) == {"name", "usable", "detail"}
    finally:
        set_current_run_dir(None)


# ---------------------------------------------------------------------------
# open_run / close_run — the shared "own a run" lifecycle (BACKLOG #18).
# ---------------------------------------------------------------------------

def test_open_run_creates_pins_and_captures(workspace):
    from runs import open_run, current_run_dir, run_dir, set_current_run_dir
    try:
        rd = open_run("openrun1", prompt="build a thing", model="mid",
                      lane="agenda", origin={"source": "cli-run"})
        # Created at the deterministic path and pinned as current-run context.
        assert rd == run_dir("openrun1")
        assert rd.is_dir()
        assert current_run_dir() == rd
        # Attribution seeded: prompt + environment snapshot + metadata fields.
        assert (rd / "source" / "prompt.txt").read_text() == "build a thing"
        assert (rd / "source" / "environment.json").is_file()
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["lane"] == "agenda"
        assert meta["model"] == "mid"
        assert meta["origin"] == {"source": "cli-run"}
    finally:
        set_current_run_dir(None)


def test_close_run_finalizes_and_curates(workspace):
    from runs import open_run, close_run, run_dir, set_current_run_dir
    try:
        open_run("closerun1", prompt="do it", lane="agenda")
    finally:
        set_current_run_dir(None)
    card = close_run("closerun1", status="done")
    rd = run_dir("closerun1")
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["status"] == "done"
    assert meta.get("ended_at")
    # run_card.json written by curation, returned to the caller.
    assert (rd / "run_card.json").is_file()
    assert card is not None and card.get("handle_id") == "closerun1"


def test_close_run_stamps_backend_error(workspace):
    from runs import open_run, close_run, run_dir, set_current_run_dir

    class _BE:
        error_class = "auth"
        backend = "anthropic"
        user_action = "refresh your API key"

    try:
        open_run("closerun2", prompt="do it")
    finally:
        set_current_run_dir(None)
    close_run("closerun2", status="error", backend_error=_BE())
    meta = json.loads((run_dir("closerun2") / "metadata.json").read_text())
    assert meta["status"] == "error"
    assert meta["backend_error"]["error_class"] == "auth"
    assert meta["backend_error"]["user_action"] == "refresh your API key"


def test_resolve_run_dir_by_handle_and_loop_id(workspace):
    from runs import (open_run, run_dir, resolve_run_dir,
                      stamp_run_metadata, set_current_run_dir)
    try:
        rd = open_run("resolveme", prompt="g", lane="agenda")
        # Stamp a loop_id, as the CLI run lane does post-loop.
        stamp_run_metadata({"loop_id": "loopABCD"})
    finally:
        set_current_run_dir(None)
    # By handle_id (O(1) dir-name hit).
    assert resolve_run_dir("resolveme") == rd
    # By loop_id (metadata scan).
    assert resolve_run_dir("loopABCD") == rd
    # Unknown ref → None.
    assert resolve_run_dir("nope") is None
    assert resolve_run_dir("") is None


def test_resolve_run_dir_by_pre_resume_loop_id(workspace):
    """A resumed run overwrites metadata.loop_id with the new attempt's id
    (see cli._cmd_resume) — the crash-time loop_id the operator actually has
    in hand must still resolve via origin.resumed_from (adversarial-review
    batch-2 finding, architect lens)."""
    from runs import open_run, resolve_run_dir, stamp_run_metadata, set_current_run_dir
    try:
        rd = open_run(
            "resumecase", prompt="g", lane="agenda",
            origin={"source": "cli-resume", "resumed_from": "loopCRASHED"},
        )
        stamp_run_metadata({"loop_id": "loopRESUMED"})
    finally:
        set_current_run_dir(None)
    # The new (post-resume) loop_id resolves directly.
    assert resolve_run_dir("loopRESUMED") == rd
    # The old (crash-time) loop_id — no longer metadata.loop_id — still
    # resolves via the origin.resumed_from breadcrumb.
    assert resolve_run_dir("loopCRASHED") == rd


def test_indexed_loop_lookup_never_scans_metadata(workspace, monkeypatch):
    import runs as runs_mod
    from runs import open_run, resolve_run_dir, stamp_run_metadata, set_current_run_dir

    try:
        rd = open_run("indexedcase", prompt="g")
        stamp_run_metadata({"loop_id": "loopINDEXED"})
    finally:
        set_current_run_dir(None)

    def no_scan(root):
        raise AssertionError("indexed lookup scanned legacy metadata")

    monkeypatch.setattr(runs_mod, "_scan_legacy_run_dirs", no_scan)
    assert resolve_run_dir("loopINDEXED") == rd


def test_legacy_lookup_migrates_once_then_unknown_misses_are_bounded(
        workspace, monkeypatch):
    import runs as runs_mod
    from runs import resolve_run_dir

    root = workspace / "runs"
    legacy = root / "legacy-layout"
    legacy.mkdir(parents=True)
    (legacy / "metadata.json").write_text(json.dumps({
        "handle_id": "legacyhandle",
        "loop_id": "loopLEGACY",
        "origin": {"resumed_from": "loopBEFORE"},
    }))

    assert resolve_run_dir("loopLEGACY") == legacy
    assert resolve_run_dir("loopBEFORE") == legacy
    assert (runs_mod._index_dir(root) / runs_mod._RUN_INDEX_MARKER).is_file()

    def no_scan(root):
        raise AssertionError("post-migration miss scanned legacy metadata")

    monkeypatch.setattr(runs_mod, "_scan_legacy_run_dirs", no_scan)
    assert resolve_run_dir("unknown-loop") is None


def test_corrupt_index_entry_repairs_from_legacy_metadata(workspace):
    import runs as runs_mod
    from runs import open_run, resolve_run_dir, stamp_run_metadata, set_current_run_dir

    try:
        rd = open_run("repaircase", prompt="g")
        stamp_run_metadata({"loop_id": "loopREPAIR"})
    finally:
        set_current_run_dir(None)
    entry = runs_mod._index_entry_path("loopREPAIR")
    entry.write_text("not-json")

    assert resolve_run_dir("loopREPAIR") == rd
    assert json.loads(entry.read_text())["run_dir"] == rd.name


def test_stale_index_entry_is_removed(workspace):
    import shutil
    import runs as runs_mod
    from runs import open_run, resolve_run_dir, stamp_run_metadata, set_current_run_dir

    try:
        rd = open_run("stalecase", prompt="g")
        stamp_run_metadata({"loop_id": "loopSTALE"})
    finally:
        set_current_run_dir(None)
    entry = runs_mod._index_entry_path("loopSTALE")
    shutil.rmtree(rd)

    assert resolve_run_dir("loopSTALE") is None
    assert not entry.exists()


def test_index_failure_falls_back_to_legacy_scan(workspace, monkeypatch):
    import runs as runs_mod
    from runs import resolve_run_dir

    root = workspace / "runs"
    legacy = root / "fallback-layout"
    legacy.mkdir(parents=True)
    (legacy / "metadata.json").write_text(json.dumps({
        "loop_id": "loopFALLBACK",
    }))

    def index_failed(root):
        raise OSError("index unavailable")

    monkeypatch.setattr(runs_mod, "_ensure_run_index", index_failed)
    assert resolve_run_dir("loopFALLBACK") == legacy


def test_partial_migration_does_not_repeat_or_drop_failed_ref(
        workspace, monkeypatch):
    import runs as runs_mod
    from runs import resolve_run_dir

    root = workspace / "runs"
    legacy = root / "partial-layout"
    legacy.mkdir(parents=True)
    (legacy / "metadata.json").write_text(json.dumps({
        "handle_id": "partialhandle",
        "loop_id": "loopPARTIALINDEX",
    }))
    real_write = runs_mod._write_index_entry
    attempts = {"count": 0}

    def fail_loop_ref(ref, rd):
        attempts["count"] += 1
        if ref == "loopPARTIALINDEX":
            raise OSError("index leaf unavailable")
        real_write(ref, rd)

    monkeypatch.setattr(runs_mod, "_write_index_entry", fail_loop_ref)
    assert resolve_run_dir("loopPARTIALINDEX") == legacy
    first_attempts = attempts["count"]
    assert first_attempts > 0
    marker = runs_mod._index_dir(root) / runs_mod._RUN_INDEX_MARKER
    assert json.loads(marker.read_text())["complete"] is False

    # The missing ref still uses its legacy availability fallback, but the
    # O(all runs) index rewrite does not repeat indefinitely.
    assert resolve_run_dir("loopPARTIALINDEX") == legacy
    assert attempts["count"] == first_attempts


def test_legacy_duplicate_ref_preserves_alphabetical_first_match(workspace):
    from runs import resolve_run_dir

    root = workspace / "runs"
    first = root / "aaa-first"
    second = root / "zzz-second"
    for rd in (first, second):
        rd.mkdir(parents=True)
        (rd / "metadata.json").write_text(json.dumps({
            "loop_id": "loopDUPLICATE",
        }))

    assert resolve_run_dir("loopDUPLICATE") == first


def test_concurrent_first_lookup_runs_one_migration(workspace, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import runs as runs_mod

    root = workspace / "runs"
    rd = root / "legacy-concurrent"
    rd.mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps({"loop_id": "loopCONCURRENT"}))
    original_scan = runs_mod._scan_legacy_run_dirs
    entered = threading.Event()
    release = threading.Event()
    scans = {"count": 0}

    def held_scan(scan_root):
        scans["count"] += 1
        entered.set()
        assert release.wait(timeout=5)
        yield from original_scan(scan_root)

    monkeypatch.setattr(runs_mod, "_scan_legacy_run_dirs", held_scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runs_mod._ensure_run_index, root)
        assert entered.wait(timeout=5)
        second = pool.submit(runs_mod._ensure_run_index, root)
        assert not second.done()
        release.set()
        assert first.result(timeout=5) is True
        assert second.result(timeout=5) is True
    assert scans["count"] == 1
