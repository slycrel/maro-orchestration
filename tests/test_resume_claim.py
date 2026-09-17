"""A resume CLAIMS its source before it executes (2026-09-16, LoopsBench
chunk 7 — chunk-6 r3 finding 2): after a `done` resume whose consumption
failed, the source was intact, unconsumed and resumable again. Now the
source records {successor handle, claimant pid+token} BEFORE the loop
runs; a claim that cannot be written refuses (nothing ran). A later
resume of a claimed, unconsumed file: claimant alive → refuse (in
progress); successor checkpoint exists → refuse (superseded, resume the
successor); claimant dead with no record → UNRESOLVED, refuse unless the
operator passes `--reclaim` (unknown is not "nothing ran").
"""
import json
import os
from types import SimpleNamespace

import pytest

import checkpoint as ckmod
from checkpoint import (find_checkpoint, load_checkpoint, write_checkpoint,
                        resume_claim_state, resume_claim_status, mark_checkpoint_claimed,
                        LOOKUP_FOUND, LOOKUP_INVALID)


class _Row:
    def __init__(self, index, text, status="done"):
        self.index, self.text, self.status, self.result = index, text, status, "r"


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import introspect
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)
    monkeypatch.setattr(introspect, "plan_recovery", lambda diag: None)
    return ckmod._checkpoint_dir()


class _CountingAdapter:
    model_key = "test"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        from llm import LLMResponse, ToolCall
        self.calls += 1
        return LLMResponse(content="", tool_calls=[ToolCall(
            name="complete_step", arguments={"result": "ok", "summary": "ok"})],
            input_tokens=1, output_tokens=1)


def _cli_harness(monkeypatch, src=None):
    """`src`: the exact source file the CLI selected — the spy reads THAT
    address (not the lossy id lookup) at the moment the loop is entered."""
    import agent_loop as al
    import cli
    import llm
    seen = {}
    real = al.run_agent_loop

    def spy(goal, **kw):
        seen["kw"] = kw
        if kw.get("resume_checkpoint") is not None:
            seen["permit"] = kw["resume_checkpoint"].resume_permit   # consumed by admission
            seen["source"] = kw["resume_checkpoint"].resume_source
        if src is not None:
            seen["claim_on_disk"] = ckmod._read_candidate(src, None).ckpt.resume_claim
        res = real(goal, **kw)
        seen["result"] = res
        return res
    monkeypatch.setattr(al, "run_agent_loop", spy)
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _CountingAdapter())
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None)
    return seen


def _legacy(loop_id):
    write_checkpoint(loop_id, "g", "", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch")], step_indices=[1, 2])
    return ckmod._checkpoint_dir() / f"ckpt_{loop_id}.json"


def _dead_pid():
    """A pid that is not running (a just-reaped child's)."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


def _claimed(loop_id, *, handle, pid, token=None):
    p = _legacy(loop_id)
    data = json.loads(p.read_text())
    data["resume_claim"] = {"handle_id": handle, "pid": pid, "claimed_at": "t",
                            **({"token": token} if token else {})}
    p.write_text(json.dumps(data))
    return p


def test_claim_is_durable_before_the_loop_runs_and_cleared_by_consumption(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    import cli
    src = _legacy("lp-c1")
    seen = _cli_harness(monkeypatch, src)
    synced = []
    real_sync = ckmod._fsync_dir
    monkeypatch.setattr(ckmod, "_fsync_dir", lambda p: synced.append(str(p)) or real_sync(p))
    rc = cli.main(["resume", "lp-c1"])
    assert rc == 0, capsys.readouterr()
    claim = seen["claim_on_disk"]
    assert claim and claim["pid"] == os.getpid() and claim["handle_id"] == seen["kw"]["handle_id"]
    assert claim.get("token") and claim.get("nonce")           # pid-reuse guard + permit
    # the claim names the successor loop, and that IS the loop that ran
    assert claim["successor_loop_id"] == seen["kw"]["loop_id"] == seen["result"].loop_id
    # the object handed to the loop is the claimed snapshot, carrying its permit
    handed = seen["kw"]["resume_checkpoint"]
    assert handed.resume_claim == claim and seen["permit"] == claim["nonce"]
    assert seen["source"] == src and handed.resume_permit is None       # one-shot: consumed
    # the claim write is power-loss durable (directory entry fsynced)
    assert synced and synced[0] == str(src), synced
    after = load_checkpoint("lp-c1")
    # consumed dominates: the claim stays as the record of WHO resumed it,
    # but it no longer gates anything
    assert after.is_consumed() and after.resume_claim == claim
    assert after.resumed_to_loop_id == seen["result"].loop_id
    assert resume_claim_state(after) is None


def test_a_claim_that_cannot_be_written_refuses_before_anything_runs(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    import cli
    src = _legacy("lp-c2")
    before = src.read_text()
    seen = _cli_harness(monkeypatch)
    monkeypatch.setattr(ckmod, "mark_checkpoint_claimed", lambda *a, **k: None)
    rc = cli.main(["resume", "lp-c2"])
    err = capsys.readouterr().err
    assert rc != 0 and "could not record the resume claim" in err and "nothing ran" in err
    assert "kw" not in seen and src.read_text() == before


def test_a_live_claim_refuses_everywhere(monkeypatch, tmp_path, capsys):
    """Claimant alive (this test's parent process): the CLI refuses, the API
    loader refuses, the heartbeat does not advertise the run."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    import heartbeat
    from process_identity import process_start_token
    ppid = os.getppid()
    _claimed("lp-c3", handle="h-live", pid=ppid, token=process_start_token(ppid))
    ck = load_checkpoint("lp-c3")
    assert resume_claim_state(ck) == "live"
    seen = _cli_harness(monkeypatch)
    assert cli.main(["resume", "lp-c3"]) != 0
    assert "in progress as run h-live" in capsys.readouterr().err and "kw" not in seen
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2,
                            max_iterations=4, resume_from_loop_id="lp-c3")
    assert res.status == "stuck" and "in progress" in res.stuck_reason and adapter.calls == 0
    ck.handle_id = "h-live"
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [ck])
    monkeypatch.setattr(heartbeat, "_lease_owner_alive", lambda loop_id: None)
    assert heartbeat._find_resumable_runs() == []
    # a second attempt in the SAME process is not "our own" either: the
    # permit is the nonce, not the pid (r1 Skeptic 5)
    _claimed("lp-c3b", handle="h-self", pid=os.getpid(),
             token=process_start_token(os.getpid()))
    assert resume_claim_state(load_checkpoint("lp-c3b")) == "live"
    seen.clear()
    assert cli.main(["resume", "lp-c3b"]) != 0
    assert "in progress as run h-self" in capsys.readouterr().err and "kw" not in seen


def test_a_dead_claimant_with_a_successor_checkpoint_is_superseded(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    import cli
    from runs import run_dir
    dead = _dead_pid()
    _claimed("lp-c4", handle="h-succ", pid=dead)
    # the successor run wrote its own (incomplete) checkpoint under the claimed handle
    succ = run_dir("h-succ") / "build"
    succ.mkdir(parents=True)
    body = json.loads(_legacy("lp-succ").read_text())
    body["handle_id"] = "h-succ"
    (succ / "checkpoint.json").write_text(json.dumps(body))
    (run_dir("h-succ") / "metadata.json").write_text(json.dumps({"handle_id": "h-succ"}))
    assert resume_claim_state(load_checkpoint("lp-c4")) == "superseded"
    seen = _cli_harness(monkeypatch)
    assert cli.main(["resume", "lp-c4"]) != 0
    err = capsys.readouterr().err
    assert "already resumed as run h-succ" in err and "maro resume h-succ" in err and "kw" not in seen
    # --reclaim does NOT override a superseded claim (there is a record: use it)
    assert cli.main(["resume", "lp-c4", "--reclaim"]) != 0
    assert "kw" not in seen


def test_a_dead_claimant_with_no_record_is_unresolved_until_reclaimed(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    dead = _dead_pid()
    _claimed("lp-c5", handle="h-gone", pid=dead)
    assert resume_claim_state(load_checkpoint("lp-c5")) == "unresolved"
    seen = _cli_harness(monkeypatch)
    assert cli.main(["resume", "lp-c5"]) != 0
    err = capsys.readouterr().err
    assert "left no checkpoint" in err and "--reclaim" in err and "kw" not in seen
    # the API path never guesses
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2,
                            max_iterations=4, resume_from_loop_id="lp-c5")
    assert res.status == "stuck" and "left no checkpoint" in res.stuck_reason and adapter.calls == 0
    # the operator's override re-claims and resumes from THIS checkpoint
    rc = cli.main(["resume", "lp-c5", "--reclaim"])
    assert rc == 0, capsys.readouterr()
    handed = seen["kw"]["resume_checkpoint"]
    assert handed.resume_claim["pid"] == os.getpid() and handed.resume_claim["handle_id"] != "h-gone"
    assert seen["permit"] == handed.resume_claim["nonce"]
    assert load_checkpoint("lp-c5").is_consumed()


def test_pid_reuse_is_not_a_live_claim(monkeypatch, tmp_path):
    """A recorded token that no longer matches the pid's incarnation means
    the claimant is gone even though the pid number is alive."""
    _env(monkeypatch, tmp_path)
    _claimed("lp-c6", handle="h-reuse", pid=os.getppid(), token="not-the-real-token")
    assert resume_claim_state(load_checkpoint("lp-c6")) == "unresolved"


@pytest.mark.parametrize("raw", ["x", {}, {"handle_id": "", "pid": 3},
                                 {"handle_id": "h", "pid": "3"}, {"handle_id": "h", "pid": True},
                                 {"handle_id": "h", "pid": 0},
                                 {"handle_id": "../outside", "pid": 3},        # path escape
                                 {"handle_id": "h", "pid": 3, "successor_loop_id": "/abs"},
                                 {"handle_id": "h", "pid": 3, "nonce": 5}])
def test_malformed_claims_make_the_file_invalid_not_unclaimed(raw, monkeypatch, tmp_path):
    """A PRESENT but unreadable claim is not "no claim" (r1 Architect 5):
    the file reads as INVALID and an explicit resume refuses."""
    _env(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        ckmod._claim_dict(raw)
    with pytest.raises(ValueError):
        ckmod.Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["a"],
                                    "completed": [], "resume_claim": raw})
    p = _legacy("lp-bad")
    data = json.loads(p.read_text()); data["resume_claim"] = raw
    p.write_text(json.dumps(data))
    assert find_checkpoint("lp-bad").state == LOOKUP_INVALID
    assert ckmod.branch_checkpoint("lp-bad") is None


def test_claim_shape_round_trips_and_null_is_no_claim():
    assert ckmod._claim_dict(None) is None
    ck = ckmod.Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["a"],
                                     "completed": [], "resume_claim": None})
    assert ck.resume_claim is None and resume_claim_state(ck) is None
    good = ckmod.Checkpoint.from_dict({"loop_id": "l", "goal": "g", "steps": ["a"], "completed": [],
                                       "resume_claim": {"handle_id": "h", "pid": 7, "claimed_at": "t",
                                                        "token": "tk", "nonce": "n1",
                                                        "successor_loop_id": "s1", "extra": 1}})
    assert good.resume_claim == {"handle_id": "h", "pid": 7, "claimed_at": "t", "token": "tk",
                                 "nonce": "n1", "successor_loop_id": "s1"}
    assert good.to_dict()["resume_claim"] == good.resume_claim
    assert "resume_permit" not in good.to_dict()               # the permit never serializes
    # the permit is what makes a claim read as our own
    good.resume_permit = "n1"
    assert resume_claim_status(good) == (None, "")
    good.resume_permit = "other"
    assert resume_claim_state(good) is not None


def test_claim_is_a_compare_and_swap_against_the_admitted_bytes(monkeypatch, tmp_path):
    """r1 Skeptic 3 / Architect 1, r2 finding 3: the CAS key is the digest
    of the bytes admitted — a writer advancing, completing, consuming or
    claiming the file between admission and the claim write refuses, a
    legacy file with no `timestamp` (which parses as "now" every read)
    does NOT — and the object returned is the file as read back."""
    _env(monkeypatch, tmp_path)
    src = _legacy("lp-cas")
    admitted = find_checkpoint("lp-cas")
    assert admitted.found and admitted.digest
    # (a) a writer completed step two after admission
    write_checkpoint("lp-cas", "g", "", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch"), _Row(2, "Step two: report")], step_indices=[1, 2])
    assert mark_checkpoint_claimed("lp-cas", path=src, handle_id="h1", expected=admitted) is None
    assert load_checkpoint("lp-cas").resume_claim is None            # nothing written
    # (b) same bytes → claimed; the returned object is the read-back file
    src = _legacy("lp-cas2"); admitted = find_checkpoint("lp-cas2")
    got = mark_checkpoint_claimed("lp-cas2", path=src, handle_id="h2", expected=admitted,
                                  successor_loop_id="succ0001")
    assert got is not None and got.resume_permit == got.resume_claim["nonce"]
    assert got.resume_source == src and got.resume_claim["successor_loop_id"] == "succ0001"
    assert ckmod._read_candidate(src, None).ckpt.resume_claim == got.resume_claim
    # (c) a foreign live claim is not overwritten, reclaim or not (and the
    # bytes changed anyway)
    assert mark_checkpoint_claimed("lp-cas2", path=src, handle_id="h3",
                                   expected=find_checkpoint("lp-cas2"), reclaim=True) is None
    # (d) consumed / complete files refuse the claim outright
    ckmod.mark_checkpoint_consumed("lp-cas2", resumed_to_loop_id="x", path=src)
    assert mark_checkpoint_claimed("lp-cas2", path=src, handle_id="h4",
                                   expected=find_checkpoint("lp-cas2")) is None
    # (e) the read-back must carry OUR claim: a replaced file after the write refuses
    src = _legacy("lp-cas3"); admitted = find_checkpoint("lp-cas3")
    import file_lock
    real_write = file_lock.atomic_write

    def _clobber(path, content, **kw):
        real_write(path, content, **kw)
        data = json.loads(open(path, encoding="utf-8").read()); data.pop("resume_claim", None)
        real_write(path, json.dumps(data))
    monkeypatch.setattr(file_lock, "atomic_write", _clobber)          # the locked writer's
    assert mark_checkpoint_claimed("lp-cas3", path=src, handle_id="h5", expected=admitted) is None
    monkeypatch.setattr(file_lock, "atomic_write", real_write)
    # (f) a legacy file with no timestamp is the SAME bytes on every read
    p = ckmod._checkpoint_path("lp-legacy")
    p.write_text(json.dumps({"loop_id": "lp-legacy", "goal": "g", "project": "",
                             "steps": ["A", "B"], "completed": []}))
    lk = find_checkpoint("lp-legacy")
    assert lk.ckpt.to_dict() != find_checkpoint("lp-legacy").ckpt.to_dict()   # the trap
    assert mark_checkpoint_claimed("lp-legacy", path=p, handle_id="h6", expected=lk) is not None
    # (g) a proposed claim that would not parse is refused BEFORE any write (r2 finding 4)
    src = _legacy("lp-cas4"); before = src.read_text()
    assert mark_checkpoint_claimed("lp-cas4", path=src, handle_id="../escape",
                                   expected=find_checkpoint("lp-cas4")) is None
    assert src.read_text() == before


def test_unreadable_successor_is_indeterminate_and_not_reclaimable(monkeypatch, tmp_path, capsys):
    """r1 Skeptic 6 / Architect 4: only a PROVEN-absent successor is
    `unresolved`; a damaged or unreadable successor record refuses even
    with --reclaim."""
    _env(monkeypatch, tmp_path)
    import cli
    from runs import run_dir
    dead = _dead_pid()
    _claimed("lp-c8", handle="h-torn", pid=dead)
    succ = run_dir("h-torn") / "build"; succ.mkdir(parents=True)
    (succ / "checkpoint.json").write_text('{"loop_id": "other", "st')          # torn
    state, extra = resume_claim_status(load_checkpoint("lp-c8"))
    assert state == "indeterminate" and "checkpoint.json" in extra
    seen = _cli_harness(monkeypatch)
    assert cli.main(["resume", "lp-c8", "--reclaim"]) != 0
    err = capsys.readouterr().err
    assert "cannot be read" in err and "--reclaim does not apply" in err and "kw" not in seen
    # a successor behind an unreadable directory is the same class
    _claimed("lp-c9", handle="h-eacces", pid=dead)
    bd = run_dir("h-eacces") / "build"; bd.mkdir(parents=True)
    (bd / "checkpoint.json").write_text("{}")
    bd.chmod(0)
    try:
        if os.geteuid() != 0:
            assert resume_claim_state(load_checkpoint("lp-c9")) == "indeterminate"
    finally:
        bd.chmod(0o755)


def test_successor_at_its_id_address_is_found_when_the_run_dir_never_opened(monkeypatch, tmp_path):
    """r1 Skeptic 8 / Architect 6: the claim names the successor loop, so a
    successor checkpoint written to the id-addressed home (no run dir)
    still proves supersession."""
    _env(monkeypatch, tmp_path)
    dead = _dead_pid()
    p = _claimed("lp-c10", handle="h-norundir", pid=dead)
    data = json.loads(p.read_text()); data["resume_claim"]["successor_loop_id"] = "succ0002"
    p.write_text(json.dumps(data))
    assert resume_claim_state(load_checkpoint("lp-c10")) == "unresolved"
    _legacy("succ0002")                                    # the successor's own file
    assert resume_claim_state(load_checkpoint("lp-c10")) == "superseded"


def test_api_resume_claims_its_source_and_a_second_api_resume_refuses(monkeypatch, tmp_path):
    """r1 Skeptic 1 / Architect 3: the loader claims too."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    src = _legacy("lp-api")
    seen = {}
    import loop_planning as lp
    real_restore = ckmod.resume_from

    def spy_restore(ckpt):
        seen["claim"] = ckmod._read_candidate(src, None).ckpt.resume_claim
        seen["permit"] = ckpt.resume_permit
        return real_restore(ckpt)
    monkeypatch.setattr(ckmod, "resume_from", spy_restore)
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["Step one: fetch", "Step two: report"],
                            max_steps=2, max_iterations=6, resume_from_loop_id="lp-api")
    assert res.status == "done" and adapter.calls >= 1
    assert seen["claim"]["successor_loop_id"] == res.loop_id and seen["claim"]["pid"] == os.getpid()
    assert seen["permit"] == seen["claim"]["nonce"]
    # the source is not consumed by the API path (the CLI owns consumption)
    # — but it IS claimed by a process that is alive: a second resume refuses
    adapter2 = _CountingAdapter()
    res2 = al.run_agent_loop("g", adapter=adapter2, preset_steps=["Step one: fetch", "Step two: report"],
                             max_steps=2, max_iterations=6, resume_from_loop_id="lp-api")
    assert res2.status == "stuck" and "in progress" in res2.stuck_reason and adapter2.calls == 0
    # an API claim that cannot be written refuses with nothing run
    _legacy("lp-api2")
    monkeypatch.setattr(ckmod, "mark_checkpoint_claimed", lambda *a, **k: None)
    adapter3 = _CountingAdapter()
    res3 = al.run_agent_loop("g", adapter=adapter3, preset_steps=["Step one: fetch"],
                             max_steps=1, max_iterations=3, resume_from_loop_id="lp-api2")
    assert res3.status == "stuck" and "could not record the resume claim" in res3.stuck_reason
    assert adapter3.calls == 0


def test_branching_a_claimed_source_is_refused(monkeypatch, tmp_path):
    """r1 Skeptic 2: a branch would drop the claim and replay the plan."""
    _env(monkeypatch, tmp_path)
    _claimed("lp-br", handle="h-br", pid=os.getppid())
    assert ckmod.branch_checkpoint("lp-br") is None
    _claimed("lp-br2", handle="h-br2", pid=_dead_pid())     # unresolved: still no
    assert ckmod.branch_checkpoint("lp-br2") is None
    _legacy("lp-br3")
    assert ckmod.branch_checkpoint("lp-br3")                # unclaimed: branches


def test_heartbeat_surfaces_unresolved_claims_without_auto_resuming(monkeypatch, tmp_path):
    """r1 Skeptic 9: unresolved is not hidden — the row carries the claim."""
    _env(monkeypatch, tmp_path)
    import heartbeat
    from runs import run_dir
    dead = _dead_pid()
    _claimed("lp-hb", handle="h-hb", pid=dead)
    ck = load_checkpoint("lp-hb"); ck.handle_id = "h-orig"
    (run_dir("h-orig")).mkdir(parents=True)
    (run_dir("h-orig") / "metadata.json").write_text(json.dumps({"handle_id": "h-orig", "status": None}))
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [ck])
    monkeypatch.setattr(heartbeat, "_lease_owner_alive", lambda loop_id: None)
    rows = heartbeat._find_resumable_runs()
    assert len(rows) == 1 and rows[0]["claim_state"] == "unresolved" and rows[0]["claim_handle"] == "h-hb"


def test_handle_resume_overwrites_its_own_claim_with_the_successor(monkeypatch, tmp_path, capsys):
    """A handle resume shares the file with its successor: the claim is on
    disk when the loop starts and gone (successor's complete checkpoint)
    when it finishes — no consume needed, no stale claim left behind."""
    _env(monkeypatch, tmp_path)
    import cli
    from runs import run_dir
    body = json.loads(_legacy("lp-c7").read_text())
    body["handle_id"] = "h-own"
    rd = run_dir("h-own"); (rd / "build").mkdir(parents=True)
    src = rd / "build" / "checkpoint.json"
    src.write_text(json.dumps(body))
    (rd / "metadata.json").write_text(json.dumps({"handle_id": "h-own", "loop_ids": ["lp-c7"]}))
    (ckmod._checkpoint_dir() / "ckpt_lp-c7.json").unlink()
    seen = _cli_harness(monkeypatch, src)
    assert cli.main(["resume", "h-own"]) == 0, capsys.readouterr()
    assert seen["claim_on_disk"] and seen["claim_on_disk"]["handle_id"] == "h-own"
    after = ckmod._read_candidate(src, None)
    assert after.found and after.ckpt.loop_id != "lp-c7" and after.ckpt.is_complete()
    assert after.ckpt.resume_claim is None


def test_a_refusal_before_the_first_step_releases_the_claim(monkeypatch, tmp_path, capsys):
    """r2 findings 5/7: a run refused before anything executed must not
    leave its claim behind (it would read as unresolved and demand
    --reclaim for a run that did nothing)."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    from loop_types import LoopResult
    # (a) API path: cross-project refusal happens BEFORE the claim — no claim at all
    write_checkpoint("lp-xp", "g", "orig-proj", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch")], step_indices=[1, 2])
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            project="other-proj", resume_from_loop_id="lp-xp")
    assert res.status == "stuck" and "orig-proj" in res.stuck_reason and adapter.calls == 0
    assert load_checkpoint("lp-xp").resume_claim is None
    # (b) API path: a restore failure AFTER the claim releases it
    _legacy("lp-rf")
    monkeypatch.setattr(ckmod, "resume_from", lambda ckpt: (_ for _ in ()).throw(RuntimeError("boom")))
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_from_loop_id="lp-rf")
    assert res.status == "stuck" and "restore failed" in res.stuck_reason and adapter.calls == 0
    assert load_checkpoint("lp-rf").resume_claim is None
    monkeypatch.undo(); _env(monkeypatch, tmp_path)
    # (c) CLI path: the loop refuses at initialization (before any resource)
    src = _legacy("lp-init")
    seen = _cli_harness(monkeypatch, src)
    monkeypatch.setattr(al, "_initialize_loop", lambda goal, **kw: (
        None, LoopResult(loop_id="", project="", goal=goal, status="stuck",
                         stuck_reason="kill switch")))
    assert cli.main(["resume", "lp-init"]) != 0
    assert seen["claim_on_disk"] and seen["claim_on_disk"]["handle_id"]     # it WAS claimed
    assert load_checkpoint("lp-init").resume_claim is None                  # …and released
    # (d) the release itself failing keeps the claim (fail closed) — the
    # refusal still stands
    monkeypatch.undo(); _env(monkeypatch, tmp_path)
    src = _legacy("lp-init2")
    seen = _cli_harness(monkeypatch, src)
    monkeypatch.setattr(al, "_initialize_loop", lambda goal, **kw: (
        None, LoopResult(loop_id="", project="", goal=goal, status="stuck", stuck_reason="busy")))
    monkeypatch.setattr(ckmod, "release_checkpoint_claim", lambda *a, **k: False)
    assert cli.main(["resume", "lp-init2"]) != 0
    assert load_checkpoint("lp-init2").resume_claim is not None
    capsys.readouterr()


def test_a_permitted_object_admits_exactly_one_run(monkeypatch, tmp_path):
    """r2 finding 1: the handed-in object is a capability — unclaimed
    objects refuse, the permit is consumed on first admission, and the
    on-disk claim must still be ours."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    src = _legacy("lp-one")
    plain = load_checkpoint("lp-one")
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_checkpoint=plain)
    assert res.status == "stuck" and "carries no resume claim" in res.stuck_reason
    assert adapter.calls == 0 and load_checkpoint("lp-one").resume_claim is None
    claimed = mark_checkpoint_claimed("lp-one", path=src, handle_id="h-one",
                                      expected=find_checkpoint("lp-one"))
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_checkpoint=claimed)
    assert res.status == "done" and adapter.calls >= 1 and claimed.resume_permit is None
    calls = adapter.calls
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_checkpoint=claimed)
    assert res.status == "stuck" and "carries no resume claim" in res.stuck_reason
    assert adapter.calls == calls
    # a claimed object whose on-disk claim was replaced is refused too
    src2 = _legacy("lp-two")
    claimed2 = mark_checkpoint_claimed("lp-two", path=src2, handle_id="h-two",
                                       expected=find_checkpoint("lp-two"))
    data = json.loads(src2.read_text()); data["resume_claim"]["nonce"] = "someone-else"
    src2.write_text(json.dumps(data))
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_checkpoint=claimed2)
    assert res.status == "stuck" and "no longer this run's" in res.stuck_reason
    assert adapter.calls == calls


def test_id_addressed_successor_must_name_the_successor(monkeypatch, tmp_path):
    """r2 finding 6: a stale file at the successor's id address naming a
    third loop is not proof of supersession — it is indeterminate."""
    _env(monkeypatch, tmp_path)
    dead = _dead_pid()
    p = _claimed("lp-c11", handle="h-x", pid=dead)
    data = json.loads(p.read_text()); data["resume_claim"]["successor_loop_id"] = "succ0003"
    p.write_text(json.dumps(data))
    body = json.loads(_legacy("lp-third").read_text())
    ckmod._checkpoint_path("succ0003").write_text(json.dumps(body))      # names lp-third
    state, extra = resume_claim_status(load_checkpoint("lp-c11"))
    assert state == "indeterminate" and "names loop lp-third" in extra


def test_heartbeat_surfaces_a_claimed_legacy_source(monkeypatch, tmp_path):
    """r2 finding 8: the source had no handle; the claim names the successor."""
    _env(monkeypatch, tmp_path)
    import heartbeat
    _claimed("lp-hb2", handle="h-hb2", pid=_dead_pid())
    ck = load_checkpoint("lp-hb2")
    assert not ck.handle_id
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [ck])
    monkeypatch.setattr(heartbeat, "_lease_owner_alive", lambda loop_id: None)
    rows = heartbeat._find_resumable_runs()
    assert len(rows) == 1 and rows[0]["claim_state"] == "unresolved" and rows[0]["claim_handle"] == "h-hb2"
    # an unclaimed legacy source is still stale history, not resumable
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [load_checkpoint(_legacy("lp-plain").stem[5:])])
    assert heartbeat._find_resumable_runs() == []


def test_pre_minted_loop_id_has_a_grammar(monkeypatch, tmp_path):
    """r2 finding 10."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    with pytest.raises(ValueError):
        al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["x"], max_steps=1,
                          max_iterations=2, loop_id="../escape")
    res = al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["x"], max_steps=1,
                            max_iterations=3, loop_id="pre00001")
    assert res.loop_id == "pre00001"


def test_api_admission_is_serialized_on_the_resume_lock(monkeypatch, tmp_path):
    """r2 finding 2 (partial): the loader admits under the CLI's lock."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    from proc_lock import acquire_pidfile
    from checkpoint import resume_lock_name
    _legacy("lp-lock")
    held = acquire_pidfile(resume_lock_name("lp-lock"), payload={"pid": os.getpid()})
    assert held.status == "acquired"
    try:
        adapter = _CountingAdapter()
        res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2,
                                max_iterations=4, resume_from_loop_id="lp-lock")
        assert res.status == "stuck" and "being admitted" in res.stuck_reason and adapter.calls == 0
        assert load_checkpoint("lp-lock").resume_claim is None
    finally:
        held.handle.close()
    # released after admission: a later resume proceeds (and the lock is free again)
    res = al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["x"], max_steps=2,
                            max_iterations=4, resume_from_loop_id="lp-lock")
    assert res.status == "done"
    again = acquire_pidfile(resume_lock_name("lp-lock"))
    assert again.status == "acquired"; again.handle.close()


def test_cli_resumes_a_legacy_file_with_no_timestamp(monkeypatch, tmp_path, capsys):
    """r3 finding 2: the between-reads check compares BYTES too."""
    _env(monkeypatch, tmp_path)
    import cli
    p = ckmod._checkpoint_path("lp-legacy2")
    p.write_text(json.dumps({"loop_id": "lp-legacy2", "goal": "g", "project": "",
                             "steps": ["Step one: fetch", "Step two: report"],
                             "completed": [{"index": 1, "text": "Step one: fetch", "status": "done",
                                            "result": "r"}]}))
    seen = _cli_harness(monkeypatch, p)
    assert cli.main(["resume", "lp-legacy2"]) == 0, capsys.readouterr()
    assert seen["claim_on_disk"] and load_checkpoint("lp-legacy2").is_consumed()


def test_failures_between_claim_and_loop_leave_no_claim(monkeypatch, tmp_path, capsys):
    """r3 findings 3/6: the adapter is built BEFORE the claim; an exception
    out of loop initialization releases it."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    import llm
    src = _legacy("lp-adapter")
    before = src.read_text()
    _cli_harness(monkeypatch, src)
    monkeypatch.setattr(llm, "build_adapter",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("adapter unavailable")))
    assert cli.main(["resume", "lp-adapter"]) != 0
    err = capsys.readouterr().err
    assert "adapter unavailable" in err and "unclaimed" in err and src.read_text() == before
    # initialization raising (a bad pre-minted id) releases a claimed object
    src2 = _legacy("lp-initexc")
    claimed = mark_checkpoint_claimed("lp-initexc", path=src2, handle_id="h-ie",
                                      expected=find_checkpoint("lp-initexc"))
    with pytest.raises(ValueError):
        al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["x"], max_steps=2,
                          max_iterations=4, resume_checkpoint=claimed, loop_id="../bad")
    assert load_checkpoint("lp-initexc").resume_claim is None


def test_api_resume_refuses_while_the_source_loop_is_alive(monkeypatch, tmp_path):
    """r3 finding 4: the loader probes the source's owner before claiming."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import run_lease
    _legacy("lp-owner")
    monkeypatch.setattr(run_lease, "probe_owner_alive", lambda loop_id: True)
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_from_loop_id="lp-owner")
    assert res.status == "stuck" and "run lease is held" in res.stuck_reason and adapter.calls == 0
    assert load_checkpoint("lp-owner").resume_claim is None
    # no lease record: the in-flight pid decides
    monkeypatch.setattr(run_lease, "probe_owner_alive", lambda loop_id: None)
    p = _legacy("lp-owner2")
    data = json.loads(p.read_text()); data["in_flight"] = {"index": 2, "pid": os.getppid()}
    p.write_text(json.dumps(data))
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2, max_iterations=4,
                            resume_from_loop_id="lp-owner2")
    assert res.status == "stuck" and "is alive" in res.stuck_reason and adapter.calls == 0
    assert load_checkpoint("lp-owner2").resume_claim is None


def test_api_claim_names_the_address_its_successor_writes_to(monkeypatch, tmp_path):
    """r3 finding 7: under an ambient run dir the successor's checkpoint
    lands in THAT dir; the claim records it and supersession is proven
    there."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import runs
    src = _legacy("lp-amb")
    rd = runs.open_run("h-parent", prompt="p", lane="agenda")
    runs.set_current_run_dir(None)
    seen = {}
    real_restore = ckmod.resume_from

    def spy_restore(ckpt):
        seen["claim"] = ckmod._read_candidate(src, None).ckpt.resume_claim
        return real_restore(ckpt)
    monkeypatch.setattr(ckmod, "resume_from", spy_restore)
    with runs.scoped_run_dir(rd):
        res = al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["x"], max_steps=2,
                                max_iterations=6, resume_from_loop_id="lp-amb")
    assert res.status == "done"
    assert seen["claim"]["successor_path"] == str(rd / "build" / "checkpoint.json")
    assert (rd / "build" / "checkpoint.json").exists()
    # with the claimant gone, that address proves supersession
    data = json.loads(src.read_text()); data["resume_claim"]["pid"] = _dead_pid()
    data["resume_claim"].pop("token", None)
    src.write_text(json.dumps(data))
    assert resume_claim_state(load_checkpoint("lp-amb")) == "superseded"


def test_heartbeat_reports_a_finalized_claimed_run_as_finalized(monkeypatch, tmp_path):
    """r3 finding 8: a demoted resume's source is surfaced as "finalized
    without proving the source consumed", not as "died mid-loop"."""
    _env(monkeypatch, tmp_path)
    import heartbeat
    from runs import run_dir
    _claimed("lp-fin", handle="h-fin-succ", pid=_dead_pid())
    ck = load_checkpoint("lp-fin"); ck.handle_id = "h-fin"
    run_dir("h-fin").mkdir(parents=True)
    (run_dir("h-fin") / "metadata.json").write_text(json.dumps({"handle_id": "h-fin", "status": "incomplete"}))
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [ck])
    monkeypatch.setattr(heartbeat, "_lease_owner_alive", lambda loop_id: None)
    rows = heartbeat._find_resumable_runs()
    assert len(rows) == 1 and rows[0]["claim_state"] == "unresolved"
    assert rows[0]["finalized_status"] == "incomplete"
    # an UNclaimed finalized run is not resumable (unchanged)
    ck2 = load_checkpoint(_legacy("lp-fin2").stem[5:]); ck2.handle_id = "h-fin"
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [ck2])
    assert heartbeat._find_resumable_runs() == []


def test_two_threads_cannot_both_take_one_permit(monkeypatch, tmp_path):
    """r3 finding 1: take-and-clear is atomic and precedes the disk read."""
    import threading
    _env(monkeypatch, tmp_path)
    import loop_planning as lp
    from loop_types import LoopContext
    src = _legacy("lp-thr")
    claimed = mark_checkpoint_claimed("lp-thr", path=src, handle_id="h-thr",
                                      expected=find_checkpoint("lp-thr"))
    import time
    real_read = ckmod._read_candidate

    def slow_read(path, loop_id):
        time.sleep(0.3)                      # the taker is still inside the branch
        return real_read(path, loop_id)
    monkeypatch.setattr(ckmod, "_read_candidate", slow_read)
    outcomes = []

    def go():
        ctx = LoopContext(goal="g", project="", loop_id="lp-t", verbose=False)
        restored, refusal = lp._load_resume(ctx, "lp-thr", preloaded=claimed)
        outcomes.append(restored is not None and refusal is None)
    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert sorted(outcomes) == [False, True], outcomes
