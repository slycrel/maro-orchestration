"""(h) slice 3 — stranded-state sweep, DOING PID stamps, manual resume
(BACKEND_RESILIENCE_DESIGN §3: sweep on the heartbeat tick; resume is
manual-first; auto-resume deliberately deferred past 1.0).
"""

import json
import os
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# DOING PID sidecar (orch_items)
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal project with a NEXT.md, isolated under tmp."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import config
    config.reset_cache() if hasattr(config, "reset_cache") else None
    import orch_items
    slug = "sweeptest"
    pdir = orch_items.projects_root() / slug
    pdir.mkdir(parents=True)
    (pdir / "NEXT.md").write_text(
        "# NEXT\n\n- [ ] first item\n- [ ] second item\n")
    return slug


def test_doing_stamp_written_and_cleared(project):
    from orch_items import (STATE_DOING, STATE_DONE, _doing_pids_path,
                            _read_doing_pids, mark_item, parse_next)
    _lines, items = parse_next(project)
    idx = next(it.index for it in items if "first" in it.text)
    mark_item(project, idx, STATE_DOING)
    pids = _read_doing_pids(project)
    assert pids[str(idx)]["pid"] == os.getpid()
    mark_item(project, idx, STATE_DONE)
    assert str(idx) not in _read_doing_pids(project)


def test_stranded_detects_dead_pid(project):
    from orch_items import (STATE_DOING, _doing_pids_path, mark_item,
                            parse_next, stranded_doing_items)
    _lines, items = parse_next(project)
    idx = next(it.index for it in items if "first" in it.text)
    mark_item(project, idx, STATE_DOING)
    # live PID (this process) → not stranded
    assert stranded_doing_items(project) == []
    # forge a dead PID
    p = _doing_pids_path(project)
    rec = json.loads(p.read_text())
    rec[str(idx)]["pid"] = 999999999
    p.write_text(json.dumps(rec))
    stranded = stranded_doing_items(project)
    assert len(stranded) == 1 and stranded[0].index == idx


def test_stranded_detects_missing_stamp(project):
    """Pre-stamp-era DOING items (no sidecar entry) are stranded too."""
    from orch_items import (STATE_DOING, _doing_pids_path, mark_item,
                            parse_next, stranded_doing_items)
    _lines, items = parse_next(project)
    idx = next(it.index for it in items if "second" in it.text)
    mark_item(project, idx, STATE_DOING)
    _doing_pids_path(project).unlink()  # simulate pre-era item
    stranded = stranded_doing_items(project)
    assert [it.index for it in stranded] == [idx]


# ---------------------------------------------------------------------------
# Sweep (heartbeat)
# ---------------------------------------------------------------------------


def test_sweep_reverts_dead_doing(project):
    from orch_items import (STATE_DOING, STATE_TODO, _doing_pids_path,
                            get_item, mark_item, parse_next)
    from heartbeat import stranded_state_sweep
    _lines, items = parse_next(project)
    idx = next(it.index for it in items if "first" in it.text)
    mark_item(project, idx, STATE_DOING)
    p = _doing_pids_path(project)
    rec = json.loads(p.read_text())
    rec[str(idx)]["pid"] = 999999999
    p.write_text(json.dumps(rec))

    result = stranded_state_sweep(verbose=False)
    assert f"{project}#{idx}" in result["reverted_doing"]
    assert get_item(project, idx).state == STATE_TODO


def test_sweep_spares_live_doing(project):
    from orch_items import STATE_DOING, get_item, mark_item, parse_next
    from heartbeat import stranded_state_sweep
    _lines, items = parse_next(project)
    idx = next(it.index for it in items if "first" in it.text)
    mark_item(project, idx, STATE_DOING)  # stamped with THIS live pid
    result = stranded_state_sweep(verbose=False)
    assert f"{project}#{idx}" not in result["reverted_doing"]
    assert get_item(project, idx).state == STATE_DOING


def test_sweep_surfaces_orphaned_checkpoint(project, tmp_path, monkeypatch):
    """Checkpoint + dead in-flight PID + unfinalized run → resumable_runs."""
    import checkpoint as ckpt_module
    from heartbeat import stranded_state_sweep

    rd = tmp_path / "runs" / "hdead-red-fern"
    (rd / "build").mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": "hdead", "status": None}))
    ckpt = {
        "loop_id": "loopdead", "goal": "g", "project": "", "handle_id": "hdead",
        "steps": ["s1", "s2", "s3"],
        "completed": [{"index": 1, "text": "s1", "status": "done", "result": "",
                       "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 0}],
        "timestamp": "2026-07-09T00:00:00",
        "in_flight": {"index": 2, "started_at": "2026-07-09T00:01:00",
                      "pid": 999999999},
    }
    (rd / "build" / "checkpoint.json").write_text(json.dumps(ckpt))
    monkeypatch.setattr(ckpt_module, "_runs_root", lambda: rd.parent)
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir",
                        lambda: tmp_path / "empty-legacy")
    import runs as runs_module
    monkeypatch.setattr(runs_module, "run_dir",
                        lambda h: rd.parent / f"{h}-red-fern")

    emitted = []
    import notify
    monkeypatch.setattr(notify, "emit",
                        lambda ev, payload, **kw: emitted.append((ev, payload)))

    result = stranded_state_sweep(verbose=False)
    hits = [r for r in result["resumable_runs"] if r["loop_id"] == "loopdead"]
    assert len(hits) == 1
    assert hits[0]["in_flight"] == 2
    assert any(ev == "stranded_run" and "maro resume loopdead" in p["message"]
               for ev, p in emitted)


def test_sweep_skips_finalized_run(project, tmp_path, monkeypatch):
    import checkpoint as ckpt_module
    from heartbeat import stranded_state_sweep

    rd = tmp_path / "runs" / "hdone-red-fern"
    (rd / "build").mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": "hdone", "status": "stuck"}))
    ckpt = {"loop_id": "loopdone", "goal": "g", "project": "",
            "handle_id": "hdone", "steps": ["s1", "s2"],
            "completed": [], "timestamp": "2026-07-09T00:00:00"}
    (rd / "build" / "checkpoint.json").write_text(json.dumps(ckpt))
    monkeypatch.setattr(ckpt_module, "_runs_root", lambda: rd.parent)
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir",
                        lambda: tmp_path / "empty-legacy")
    import runs as runs_module
    monkeypatch.setattr(runs_module, "run_dir",
                        lambda h: rd.parent / f"{h}-red-fern")

    result = stranded_state_sweep(verbose=False)
    assert not any(r["loop_id"] == "loopdone" for r in result["resumable_runs"])


# ---------------------------------------------------------------------------
# Stranded run-card backfill (specimen 51b09271: SIGTERM'd handle leaves
# status/ended_at null forever; sweep stamps non-terminal "stranded")
# ---------------------------------------------------------------------------


def _make_run_card(tmp_path, name, *, status=None, started_ago_secs=3600,
                   pid=None, ckpt_pid=None):
    from datetime import datetime, timedelta, timezone
    rd = tmp_path / "runs" / name
    (rd / "build").mkdir(parents=True, exist_ok=True)
    started = (datetime.now(timezone.utc)
               - timedelta(seconds=started_ago_secs)).isoformat()
    meta = {"handle_id": name.split("-")[0], "status": status,
            "started_at": started, "ended_at": None}
    if pid is not None:
        meta["pid"] = pid
    (rd / "metadata.json").write_text(json.dumps(meta))
    if ckpt_pid is not None:
        (rd / "build" / "checkpoint.json").write_text(json.dumps(
            {"loop_id": "lp", "goal": "g", "project": "",
             "handle_id": meta["handle_id"],
             "steps": ["s1", "s2"], "completed": [],
             "timestamp": "2026-07-09T00:00:00",
             "in_flight": {"index": 1, "started_at": "2026-07-09T00:01:00",
                           "pid": ckpt_pid}}))
    return rd


@pytest.fixture
def runs_env(tmp_path, monkeypatch):
    import runs as runs_module
    monkeypatch.setattr(runs_module, "runs_root", lambda: tmp_path / "runs")
    (tmp_path / "runs").mkdir(exist_ok=True)
    return tmp_path


def test_backfill_stamps_dead_pid_run(runs_env):
    from heartbeat import _backfill_stranded_run_cards
    rd = _make_run_card(runs_env, "hstr-a", pid=999999999)
    hits = _backfill_stranded_run_cards()
    assert "hstr" in hits
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["status"] == "stranded"
    assert meta["ended_at"]
    assert meta["stranded_detected_at"]


def test_backfill_uses_checkpoint_pid_for_legacy_rows(runs_env):
    # Pre-pid-era metadata (like 51b09271): owner evidence comes from the
    # checkpoint's in_flight pid.
    from heartbeat import _backfill_stranded_run_cards
    rd = _make_run_card(runs_env, "hleg-a", ckpt_pid=999999999)
    assert "hleg" in _backfill_stranded_run_cards()
    assert json.loads((rd / "metadata.json").read_text())["status"] == "stranded"


def test_backfill_spares_live_and_young_and_finalized(runs_env):
    from heartbeat import _backfill_stranded_run_cards
    live = _make_run_card(runs_env, "hlive-a", pid=os.getpid())
    young = _make_run_card(runs_env, "hyng-a", pid=999999999,
                           started_ago_secs=60)
    done = _make_run_card(runs_env, "hdone-a", status="done", pid=999999999)
    nopid_recent = _make_run_card(runs_env, "hnop-a")  # no pid, only 1h old
    hits = _backfill_stranded_run_cards()
    assert hits == []
    for rd in (live, young, done, nopid_recent):
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["status"] != "stranded"


def test_backfill_stamps_ancient_no_pid_run(runs_env):
    from heartbeat import _backfill_stranded_run_cards
    rd = _make_run_card(runs_env, "hold-a", started_ago_secs=25 * 3600)
    assert "hold" in _backfill_stranded_run_cards()
    assert json.loads((rd / "metadata.json").read_text())["status"] == "stranded"


def test_stranded_status_stays_resumable(runs_env, tmp_path, monkeypatch):
    """The sweep's own stamp must not hide a run from _find_resumable_runs."""
    import checkpoint as ckpt_module
    from heartbeat import _find_resumable_runs
    rd = _make_run_card(tmp_path, "hres-red-fern", ckpt_pid=999999999)
    meta = json.loads((rd / "metadata.json").read_text())
    meta["status"] = "stranded"
    (rd / "metadata.json").write_text(json.dumps(meta))
    monkeypatch.setattr(ckpt_module, "_runs_root", lambda: rd.parent)
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir",
                        lambda: tmp_path / "empty-legacy")
    import runs as runs_module
    monkeypatch.setattr(runs_module, "run_dir",
                        lambda h: rd.parent / f"{h}-red-fern")
    assert any(r["handle_id"] == "hres" for r in _find_resumable_runs())


def test_new_run_metadata_carries_owner_pid(tmp_path):
    from runs import write_metadata
    rd = tmp_path / "runs" / "hnew-a"
    rd.mkdir(parents=True)
    write_metadata(rd, handle_id="hnew", prompt="p")
    assert json.loads((rd / "metadata.json").read_text())["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# FS-diff helper (artifact_check)
# ---------------------------------------------------------------------------


def test_files_modified_since(tmp_path):
    from artifact_check import files_modified_since
    old = tmp_path / "old.txt"
    old.write_text("x")
    os.utime(old, (1000000000, 1000000000))
    new = tmp_path / "new.txt"
    new.write_text("y")
    changed = files_modified_since(tmp_path, "2026-01-01T00:00:00+00:00")
    assert changed == ["new.txt"]
    assert files_modified_since(tmp_path, "not-a-date") == []


# ---------------------------------------------------------------------------
# maro resume (cli)
# ---------------------------------------------------------------------------


def test_resume_refuses_unknown(capsys, monkeypatch, tmp_path):
    import checkpoint as ckpt_module
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir",
                        lambda: tmp_path / "empty")
    monkeypatch.setattr(ckpt_module, "_runs_root", lambda: tmp_path / "noruns")
    import cli
    rc = cli.main(["resume", "nosuchloop"])
    assert rc != 0


def test_resume_refuses_complete(monkeypatch, tmp_path):
    import checkpoint as ckpt_module
    d = tmp_path / "legacy"
    d.mkdir()
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    ckpt = {"loop_id": "loopfull", "goal": "g", "project": "",
            "steps": ["s1"], "completed": [
                {"index": 1, "text": "s1", "status": "done", "result": "",
                 "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 0}],
            "timestamp": "2026-07-09T00:00:00"}
    (d / "ckpt_loopfull.json").write_text(json.dumps(ckpt))
    import cli
    rc = cli.main(["resume", "loopfull"])
    assert rc != 0


def test_resume_runs_agent_loop(monkeypatch, tmp_path):
    import checkpoint as ckpt_module
    d = tmp_path / "legacy"
    d.mkdir()
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    ckpt = {"loop_id": "loophalf", "goal": "finish the thing", "project": "",
            "steps": ["s1", "s2"], "completed": [
                {"index": 1, "text": "s1", "status": "done", "result": "",
                 "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 0}],
            "timestamp": "2026-07-09T00:00:00",
            "in_flight": {"index": 2, "started_at": "2026-07-09T00:01:00",
                          "pid": 999999999}}
    (d / "ckpt_loophalf.json").write_text(json.dumps(ckpt))

    calls = {}
    adapter = object()
    learned = {}

    import llm
    monkeypatch.setattr(llm, "build_adapter", lambda **kw: adapter)
    import loop_finalize
    monkeypatch.setattr(
        loop_finalize, "finalize_deferred_learning",
        lambda result, **kw: learned.update(kw),
    )

    def fake_loop(goal, **kw):
        calls["goal"] = goal
        calls.update(kw)
        return SimpleNamespace(loop_id="loophalf", status="done", project="")

    import agent_loop
    monkeypatch.setattr(agent_loop, "run_agent_loop", fake_loop)
    import cli
    rc = cli.main(["resume", "loophalf"])
    assert rc == 0
    assert calls["goal"] == "finish the thing"
    assert calls["resume_from_loop_id"] == "loophalf"
    assert calls["defer_learning"] is True
    assert calls["adapter"] is adapter
    assert learned["adapter"] is adapter
    consumed = cli._load_resume_checkpoint("loophalf")
    assert consumed is not None and consumed.is_consumed()
    assert consumed.resumed_to_loop_id == "loophalf"
    from proc_lock import try_hold_pidfile
    released = try_hold_pidfile(
        cli._resume_lock_name("loophalf"), fail_open=False)
    assert released is not None
    released.close()


def test_concurrent_resume_refuses_immediately_and_notifies(
        monkeypatch, tmp_path, capsys):
    import checkpoint as ckpt_module
    import cli
    import notify
    from proc_lock import try_hold_pidfile

    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    d = tmp_path / "legacy"
    d.mkdir()
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    ckpt = {
        "loop_id": "loopbusy", "handle_id": "handle-busy", "goal": "finish",
        "project": "", "steps": ["s1", "s2"],
        "completed": [{"index": 1, "text": "s1", "status": "done",
                       "result": "", "tokens_in": 0, "tokens_out": 0,
                       "elapsed_ms": 0}],
        "timestamp": "2026-07-09T00:00:00",
        "in_flight": {"index": 2, "started_at": "2026-07-09T00:01:00",
                      "pid": 999999999},
    }
    (d / "ckpt_loopbusy.json").write_text(json.dumps(ckpt))
    holder = try_hold_pidfile(
        cli._resume_lock_name("handle-busy"), fail_open=False,
        payload={"handle_id": "handle-busy", "loop_id": "loopbusy"})
    assert holder is not None
    events = []
    monkeypatch.setattr(notify, "emit",
                        lambda event, payload: events.append((event, payload)) or True)
    import agent_loop
    monkeypatch.setattr(
        agent_loop, "run_agent_loop",
        lambda *a, **k: pytest.fail("busy resume must not enter the loop"),
    )

    try:
        rc = cli.main(["resume", "loopbusy"])
    finally:
        holder.close()

    assert rc != 0
    assert "E_RESUME_BUSY" in capsys.readouterr().err
    assert events and events[0][0] == "resume_refused_busy"
    assert events[0][1]["status"] == "refused_busy"
    assert events[0][1]["holder"]["handle_id"] == "handle-busy"


def test_resume_lock_environment_failure_has_distinct_code_and_event(
        monkeypatch, tmp_path, capsys):
    import checkpoint as ckpt_module
    import cli
    import notify
    import proc_lock

    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    d = tmp_path / "legacy"
    d.mkdir()
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    ckpt = {
        "loop_id": "looplockfail", "goal": "finish", "project": "",
        "steps": ["s1", "s2"],
        "completed": [{"index": 1, "text": "s1", "status": "done",
                       "result": "", "tokens_in": 0, "tokens_out": 0,
                       "elapsed_ms": 0}],
        "timestamp": "2026-07-09T00:00:00",
    }
    (d / "ckpt_looplockfail.json").write_text(json.dumps(ckpt))
    monkeypatch.setattr(
        proc_lock, "acquire_pidfile",
        lambda *a, **k: proc_lock.PidfileAcquireResult(
            "unavailable", error="read-only filesystem"),
    )
    events = []
    monkeypatch.setattr(notify, "emit",
                        lambda event, payload: events.append((event, payload)) or True)

    rc = cli.main(["resume", "looplockfail"])

    assert rc != 0
    assert "E_RESUME_LOCK" in capsys.readouterr().err
    assert events[0][0] == "resume_lock_unavailable"
    assert events[0][1]["status"] == "lock_unavailable"
    assert events[0][1]["blocking"] is False


def test_resume_reloads_checkpoint_after_admission(monkeypatch, tmp_path):
    import cli
    import agent_loop

    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))

    class FakeCheckpoint:
        loop_id = "loopfresh"
        handle_id = "handle-fresh"
        goal = "finish"
        project = ""
        in_flight = None
        completed = []
        steps = ["s1"]

        def __init__(self, complete):
            self._complete = complete

        def is_complete(self):
            return self._complete

    checkpoints = iter((FakeCheckpoint(False), FakeCheckpoint(True)))
    monkeypatch.setattr(cli, "_load_resume_checkpoint",
                        lambda ref: next(checkpoints))
    monkeypatch.setattr(
        agent_loop, "run_agent_loop",
        lambda *a, **k: pytest.fail("fresh completed checkpoint must refuse"),
    )

    rc = cli.main(["resume", "loopfresh"])

    assert rc != 0


def test_resume_consume_failure_never_reports_done(monkeypatch, tmp_path, capsys):
    import checkpoint as ckpt_module
    import agent_loop
    import cli
    import llm

    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    d = tmp_path / "legacy"
    d.mkdir()
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    ckpt = {
        "loop_id": "loopconsume", "goal": "finish", "project": "",
        "steps": ["s1", "s2"],
        "completed": [{"index": 1, "text": "s1", "status": "done",
                       "result": "", "tokens_in": 0, "tokens_out": 0,
                       "elapsed_ms": 0}],
        "timestamp": "2026-07-09T00:00:00",
    }
    (d / "ckpt_loopconsume.json").write_text(json.dumps(ckpt))
    monkeypatch.setattr(llm, "build_adapter", lambda **kw: object())
    monkeypatch.setattr(
        agent_loop, "run_agent_loop",
        lambda *a, **k: SimpleNamespace(
            loop_id="loopnew", status="done", project="", steps=[]),
    )
    monkeypatch.setattr(ckpt_module, "mark_checkpoint_consumed",
                        lambda *a, **k: False)

    rc = cli.main(["resume", "loopconsume", "--format", "json"])

    output = capsys.readouterr().out
    payload = json.loads(output[output.index("{"):])
    assert rc != 0
    assert payload["status"] == "incomplete"
