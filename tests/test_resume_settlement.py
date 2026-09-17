"""LoopsBench chunk 9 (2026-09-17): the loop's own finalize settles the
source a resume ran from — ONE consumer for the CLI and the API path.

Chunk 7 r1 Architect 3: the CLI proved-overwritten-or-consumed after a
done resume, but a library resume (`resume_from_loop_id=`) claimed its
source and never consumed it, so a finished API resume left the source
claimed — refused later as live/superseded (correct, but opaque, and a
different answer from the CLI's "already resumed as X"). Now
`loop_finalize.settle_resume_claim` runs at the head of Phase G (and for
the parallel lane, which bypasses Phase G): done → the source must be
overwritten by the complete successor or consumed in place, else the run
is `incomplete`; any other ending keeps the claim as the replay barrier.
Round 1 moved it to the LAST status decision (the end of Phase G after
the merge-backs; the CLI defers it past its closure pass and settles with
the same function; an auto-recovery child is settled by the frame that
holds the permit), refused dry-run resumes before any claim, and made
consumption a compare-and-consume on the claim nonce under the file lock
(`consume_claimed`). `ctx.resume_claim_release` is a typed
`checkpoint.ResumePermit` (source, nonce, source_loop_id).
"""
from __future__ import annotations

import json
import os

import pytest

import checkpoint as ckmod
from checkpoint import ResumePermit, load_checkpoint, resume_claim_state, write_checkpoint
from loop_types import LoopResult

from test_resume_claim import _CountingAdapter, _Row, _cli_harness, _dead_pid, _env, _legacy  # noqa: F401

PLAN2 = ["Step one: fetch", "Step two: report"]


class _StuckAdapter(_CountingAdapter):
    def complete(self, messages, **kwargs):
        from llm import LLMResponse, ToolCall
        self.calls += 1
        return LLMResponse(content="", tool_calls=[ToolCall(
            name="flag_stuck", arguments={"reason": "no"})], input_tokens=1, output_tokens=1)


def _api_resume(loop_id, adapter=None, **kw):
    import agent_loop as al
    return al.run_agent_loop("g", adapter=adapter or _CountingAdapter(), preset_steps=PLAN2,
                             max_steps=2, max_iterations=6, resume_from_loop_id=loop_id, **kw)


def test_a_done_api_resume_consumes_its_source_and_names_the_successor(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _legacy("lp-s1")
    res = _api_resume("lp-s1")
    assert res.status == "done", res.stuck_reason
    after = load_checkpoint("lp-s1")
    assert after.is_consumed() and after.resumed_to_loop_id == res.loop_id
    assert after.resume_claim and after.resume_claim["successor_loop_id"] == res.loop_id
    assert resume_claim_state(after) is None                      # consumed dominates
    # the negative control: a fresh run (no resume) touches no source
    import agent_loop as al
    res2 = al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=PLAN2, max_steps=2, max_iterations=6)
    assert res2.status == "done" and not load_checkpoint(res2.loop_id).is_consumed()


def test_a_resume_that_did_not_end_done_keeps_its_claim_as_the_barrier(monkeypatch, tmp_path):
    """Not done → nothing is consumed; the claim stands (live while this
    process runs; superseded once the claimant is gone, since the
    successor's file exists)."""
    _env(monkeypatch, tmp_path)
    src = _legacy("lp-s2")
    res = _api_resume("lp-s2", adapter=_StuckAdapter())
    assert res.status != "done"
    after = load_checkpoint("lp-s2")
    assert not after.is_consumed() and after.resume_claim
    assert resume_claim_state(after) == "live"
    data = json.loads(src.read_text()); data["resume_claim"]["pid"] = _dead_pid()
    data["resume_claim"].pop("token", None); src.write_text(json.dumps(data))
    assert resume_claim_state(load_checkpoint("lp-s2")) == "superseded"


def test_an_unsettled_source_demotes_the_run_to_incomplete(monkeypatch, tmp_path, caplog):
    """The consume fails (full disk) and the successor wrote elsewhere:
    the run is not reported done, and the source keeps its claim."""
    _env(monkeypatch, tmp_path)
    _legacy("lp-s3")
    monkeypatch.setattr(ckmod, "consume_claimed", lambda *a, **k: False)
    res = _api_resume("lp-s3")
    assert res.status == "incomplete" and "could not be marked consumed" in res.stuck_reason
    after = load_checkpoint("lp-s3")
    assert not after.is_consumed() and after.resume_claim["successor_loop_id"] == res.loop_id
    assert resume_claim_state(after) == "live"
    # a consume that RAISES is the same ending
    monkeypatch.setattr(ckmod, "consume_claimed",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left")))
    _legacy("lp-s3b")
    res = _api_resume("lp-s3b")
    assert res.status == "incomplete" and not load_checkpoint("lp-s3b").is_consumed()
    # and a consume that reports success but left no record is not trusted
    monkeypatch.setattr(ckmod, "consume_claimed", lambda *a, **k: True)
    _legacy("lp-s3c")
    res = _api_resume("lp-s3c")
    assert res.status == "incomplete" and not load_checkpoint("lp-s3c").is_consumed()


def test_the_parallel_lane_settles_its_source_too(monkeypatch, tmp_path):
    """The DAG lane returns before Phase G; the settlement runs on its
    result in agent_loop."""
    _env(monkeypatch, tmp_path)
    write_checkpoint("lp-s4", "g", "", ["A", "B"], [_Row(1, "A")], step_indices=[1, 2],
                     parallel_fan_out=2)
    import agent_loop as al
    res = al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["A", "B"], max_steps=2,
                            max_iterations=6, parallel_fan_out=2, resume_from_loop_id="lp-s4")
    assert res.status == "done", res.stuck_reason
    after = load_checkpoint("lp-s4")
    assert after.is_consumed() and after.resumed_to_loop_id == res.loop_id
    monkeypatch.setattr(ckmod, "consume_claimed", lambda *a, **k: False)
    write_checkpoint("lp-s4b", "g", "", ["A", "B"], [_Row(1, "A")], step_indices=[1, 2],
                     parallel_fan_out=2)
    res = al.run_agent_loop("g", adapter=_CountingAdapter(), preset_steps=["A", "B"], max_steps=2,
                            max_iterations=6, parallel_fan_out=2, resume_from_loop_id="lp-s4b")
    assert res.status == "incomplete" and "could not be marked consumed" in res.stuck_reason


def test_the_cli_settles_after_its_closure_pass(monkeypatch, tmp_path, capsys):
    """The CLI ends the run: the loop defers settlement, closure
    verification runs, and only a run still done afterwards has its source
    settled (r1: settled inside the loop, a closure-demoted run had already
    lost its resumable source)."""
    _env(monkeypatch, tmp_path)
    import cli
    # (a) a closure verdict that refutes the run: NOT consumed, claim kept
    src = _legacy("lp-s5")
    seen = _cli_harness(monkeypatch, src)

    def _refute(goal, result):
        result.status = "incomplete"
        result.stuck_reason = "closure verification: the report was never written"
        return None
    monkeypatch.setattr(cli, "_closure_verdict_pass", _refute)
    rc = cli.main(["resume", "lp-s5", "--format", "json"])
    out = capsys.readouterr()
    assert rc != 0 and seen["kw"]["defer_resume_settlement"] is True
    row = json.loads(out.out.strip().splitlines()[-1])
    assert row["status"] == "incomplete" and "closure verification" in row["stuck_reason"]
    after = load_checkpoint("lp-s5")
    assert not after.is_consumed() and after.resume_claim and after.resume_claim["nonce"]
    assert resume_claim_state(after) == "live"                    # the barrier stands
    # (b) the negative control: done after closure → consumed, output says done
    monkeypatch.undo(); _env(monkeypatch, tmp_path)
    src = _legacy("lp-s6")
    seen = _cli_harness(monkeypatch, src)
    rc = cli.main(["resume", "lp-s6", "--format", "json"])
    out = capsys.readouterr()
    assert rc == 0, out
    row = json.loads(out.out.strip().splitlines()[-1])
    assert row["status"] == "done" and row["resumed_from"] == "lp-s6"
    after = load_checkpoint("lp-s6")
    assert after.is_consumed() and after.resumed_to_loop_id == seen["result"].loop_id == row["loop_id"]
    # (c) a stand-in loop that reports done without touching the source:
    # the CLI settles it (it owns the ending) — the claim it wrote is
    # what authorizes the consume
    monkeypatch.undo(); _env(monkeypatch, tmp_path)
    import agent_loop as al
    src = _legacy("lp-s7")
    seen = _cli_harness(monkeypatch, src)
    monkeypatch.setattr(al, "run_agent_loop", lambda goal, **kw: LoopResult(
        loop_id=kw["loop_id"], project="", goal=goal, status="done"))
    assert cli.main(["resume", "lp-s7"]) == 0, capsys.readouterr()
    assert load_checkpoint("lp-s7").is_consumed()
    # (d) settlement runs BEFORE deferred learning reads the status, and
    # `_status` (what `close_run` stamps) is decided only by it: an
    # interrupt between the loop's return and the settlement closes the
    # run as an error, never as done
    monkeypatch.undo(); _env(monkeypatch, tmp_path)
    src = _legacy("lp-s7b")
    seen = _cli_harness(monkeypatch, src)
    order = []
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning",
                        lambda result, **kw: order.append(("learn", result.status)))
    monkeypatch.setattr(ckmod, "consume_claimed",
                        lambda *a, **k: order.append(("consume", None)) or False)
    rc = cli.main(["resume", "lp-s7b", "--format", "json"])
    out = capsys.readouterr()
    assert rc != 0 and order == [("consume", None), ("learn", "incomplete")], order
    row = json.loads(out.out.strip().splitlines()[-1])
    assert row["status"] == "incomplete" and "could not be marked consumed" in row["stuck_reason"]
    import runs
    monkeypatch.undo(); _env(monkeypatch, tmp_path)
    src = _legacy("lp-s7c")
    seen = _cli_harness(monkeypatch, src)
    closed = []
    real_close = runs.close_run
    monkeypatch.setattr(runs, "close_run", lambda handle, **kw: closed.append(kw.get("status")) or real_close(handle, **kw))
    monkeypatch.setattr(cli, "_closure_verdict_pass",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        cli.main(["resume", "lp-s7c"])
    assert closed == ["error"], closed                              # never "done"
    assert not load_checkpoint("lp-s7c").is_consumed()


def test_the_cli_settles_the_canonical_claimed_file_not_the_alias(monkeypatch, tmp_path, capsys):
    """r1: the claim lives in the file the address pointed at when it was
    written; an alias retargeted while the loop runs must not be what the
    CLI proves — the claimed target is consumed, the new target untouched."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    real_src = _legacy("lp-s8")
    target = ckmod._checkpoint_dir() / "real-lp-s8.json"
    target.write_bytes(real_src.read_bytes())
    real_src.unlink(); real_src.symlink_to(target)
    decoy = ckmod._checkpoint_dir() / "decoy.json"
    seen = _cli_harness(monkeypatch, real_src)
    real_loop = al.run_agent_loop

    def retarget_then_run(goal, **kw):
        res = real_loop(goal, **kw)
        # a complete successor-shaped file at the alias's NEW target
        data = json.loads(target.read_text())
        data["loop_id"] = res.loop_id
        data["completed"] = [dict(data["completed"][0], index=i, position=i, text=t) for i, t in enumerate(PLAN2, 1)]
        data.pop("resume_claim", None); data.pop("in_flight", None)
        decoy.write_text(json.dumps(data))
        assert ckmod._read_candidate(decoy, None).ckpt.is_complete()
        real_src.unlink(); real_src.symlink_to(decoy)
        return res
    monkeypatch.setattr(al, "run_agent_loop", retarget_then_run)
    assert cli.main(["resume", "lp-s8"]) == 0, capsys.readouterr()
    consumed = json.loads(target.read_text())
    assert consumed["consumed_at"] and consumed["resumed_to_loop_id"] == seen["result"].loop_id
    assert "consumed_at" not in json.loads(decoy.read_text())
    # r2: the PINNED canonical address is never resolved again — a symlink
    # appearing there after admission is refused by proof and consume alike
    pinned = ckmod._checkpoint_dir() / "pinned.json"
    pinned.write_bytes(target.read_bytes())
    permit = ResumePermit(pinned, json.loads(pinned.read_text())["resume_claim"]["nonce"], "lp-s8")
    pinned.unlink(); pinned.symlink_to(decoy)
    assert not ckmod.source_is_settled(pinned, source_loop_id="lp-s8", successor_loop_id=seen["result"].loop_id)
    assert not ckmod.consume_claimed(permit, successor_loop_id="x")
    assert "consumed_at" not in json.loads(decoy.read_text())


def test_settlement_is_the_last_status_decision(monkeypatch, tmp_path):
    """r1: Phase-G work that raises (the decision-journal write) or demotes
    must come BEFORE the source is consumed — a run that does not end done
    keeps its resumable source."""
    _env(monkeypatch, tmp_path)
    import orch
    import pytest
    _legacy("lp-s9")
    real_append = orch.append_decision

    def boom(project, lines, *a, **k):
        if any("finished status=" in str(l) for l in lines):
            raise OSError("decision sink down")
        return real_append(project, lines, *a, **k)
    monkeypatch.setattr(orch, "append_decision", boom)
    with pytest.raises(OSError):
        _api_resume("lp-s9")
    after = load_checkpoint("lp-s9")
    assert not after.is_consumed() and after.resume_claim         # the barrier stands
    # and the order in the source: settlement sits after the merge-backs
    import inspect
    import loop_finalize as lf
    body = inspect.getsource(lf._build_result_and_finalize)
    assert body.index("merge_back_clone(") < body.index("merge_back(") < body.index("settle_resume_claim(")
    assert body.index("settle_resume_claim(") < body.index("release_loop_resources(ctx)")


def test_an_auto_recovery_child_settles_the_source_it_finished(monkeypatch, tmp_path):
    """r1: a resumed run that ends stuck keeps its claim; the auto-recovery
    child runs WITHOUT the permit, so the frame that holds it settles the
    source against the child that finished the work."""
    _env(monkeypatch, tmp_path)
    from types import SimpleNamespace
    import introspect
    monkeypatch.setattr(introspect, "diagnose_loop", lambda loop_id: SimpleNamespace(failure_class="stuck"))
    monkeypatch.setattr(introspect, "plan_recovery", lambda diag: SimpleNamespace(
        auto_apply=True, risk="low", action="retry", params={"max_iterations": 6}))

    class _StuckThenDone(_CountingAdapter):
        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(
                    name="flag_stuck", arguments={"reason": "no"})], input_tokens=1, output_tokens=1)
            return super().complete(messages, **kwargs)
    _legacy("lp-s10")
    import agent_loop as al
    res = al.run_agent_loop("g", adapter=_StuckThenDone(), preset_steps=PLAN2, max_steps=2,
                            max_iterations=1, resume_from_loop_id="lp-s10")
    assert res.status == "done", res.stuck_reason
    after = load_checkpoint("lp-s10")
    assert after.is_consumed() and after.resumed_to_loop_id == res.loop_id
    assert after.resume_claim["successor_loop_id"] != res.loop_id     # the child, not the claimant


def test_a_dry_run_cannot_resume(monkeypatch, tmp_path):
    """r1: a dry run simulates steps; a simulated done would consume a real
    checkpoint — refused before any claim."""
    _env(monkeypatch, tmp_path)
    _legacy("lp-s11")
    adapter = _CountingAdapter()
    res = _api_resume("lp-s11", adapter=adapter, dry_run=True)
    assert res.status == "stuck" and "dry run" in res.stuck_reason and adapter.calls == 0
    after = load_checkpoint("lp-s11")
    assert after.resume_claim is None and not after.is_consumed()


def test_consumption_requires_this_runs_claim(monkeypatch, tmp_path):
    """r1 Architect 3: the permit's nonce authorizes the consume — a source
    whose claim was replaced mid-run (another writer) is left as it is and
    the run is not done."""
    _env(monkeypatch, tmp_path)
    src = _legacy("lp-s12")
    real_restore = ckmod.resume_from

    def replace_claim(ckpt):
        data = json.loads(src.read_text()); data["resume_claim"]["nonce"] = "someone-else"
        src.write_text(json.dumps(data))
        return real_restore(ckpt)
    monkeypatch.setattr(ckmod, "resume_from", replace_claim)
    res = _api_resume("lp-s12")
    assert res.status == "incomplete" and "could not be marked consumed" in res.stuck_reason
    after = json.loads(src.read_text())
    assert "consumed_at" not in after and after["resume_claim"]["nonce"] == "someone-else"
    # the unit: loop-id mismatch, missing claim, consumed-by-another all refuse
    p = _legacy("lp-s13")
    permit = ResumePermit(p, "n", "lp-s13")
    assert not ckmod.consume_claimed(permit, successor_loop_id="s")            # no claim at all
    data = json.loads(p.read_text()); data["resume_claim"] = {"nonce": "n", "pid": 1, "handle_id": "h"}
    p.write_text(json.dumps(data))
    assert not ckmod.consume_claimed(ResumePermit(p, "n", "other"), successor_loop_id="s")
    assert ckmod.consume_claimed(permit, successor_loop_id="s")
    assert ckmod.consume_claimed(permit, successor_loop_id="s")                # idempotent for the same successor
    assert not ckmod.consume_claimed(permit, successor_loop_id="t")            # consumed by another
    # r2: the lock is mandatory — the fail-open escape hatch never applies
    import file_lock
    lock_calls = []
    real_lock = file_lock.locked_write

    def spy_lock(path, **kw):
        lock_calls.append(kw)
        return real_lock(path, **kw)
    monkeypatch.setattr(file_lock, "locked_write", spy_lock)
    q = _legacy("lp-s14")
    data = json.loads(q.read_text()); data["resume_claim"] = {"nonce": "n", "pid": 1, "handle_id": "h"}
    q.write_text(json.dumps(data))
    assert ckmod.consume_claimed(ResumePermit(q, "n", "lp-s14"), successor_loop_id="s")
    assert lock_calls and all(c.get("require") is True for c in lock_calls), lock_calls
    monkeypatch.setattr(file_lock, "locked_write",
                        lambda path, **kw: (_ for _ in ()).throw(file_lock.FileLockTimeout("held")))
    r = _legacy("lp-s15")
    data = json.loads(r.read_text()); data["resume_claim"] = {"nonce": "n", "pid": 1, "handle_id": "h"}
    r.write_text(json.dumps(data))
    assert not ckmod.consume_claimed(ResumePermit(r, "n", "lp-s15"), successor_loop_id="s")
    assert "consumed_at" not in json.loads(r.read_text())


def test_source_is_settled_reads_the_exact_file(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    src = _legacy("lp-s7")
    assert not ckmod.source_is_settled(src, source_loop_id="lp-s7", successor_loop_id="succ")
    # consumed to ANOTHER successor is not settled for this one
    assert ckmod.mark_checkpoint_consumed("lp-s7", resumed_to_loop_id="other", path=src)
    assert not ckmod.source_is_settled(src, source_loop_id="lp-s7", successor_loop_id="succ")
    assert ckmod.source_is_settled(src, source_loop_id="lp-s7", successor_loop_id="other")
    # overwritten by an INCOMPLETE successor is not settled; a complete one is
    write_checkpoint("succ", "g", "", ["A", "B"], [_Row(1, "A")], step_indices=[1, 2])
    p2 = ckmod._checkpoint_dir() / "ckpt_succ.json"
    assert not ckmod.source_is_settled(p2, source_loop_id="lp-s7", successor_loop_id="succ")
    write_checkpoint("succ", "g", "", ["A", "B"], [_Row(1, "A"), _Row(2, "B")], step_indices=[1, 2])
    assert ckmod.source_is_settled(p2, source_loop_id="lp-s7", successor_loop_id="succ")
    # unreadable / missing → not settled
    assert not ckmod.source_is_settled(tmp_path / "missing.json", source_loop_id="x", successor_loop_id="y")
    p2.write_text("{torn")
    assert not ckmod.source_is_settled(p2, source_loop_id="lp-s7", successor_loop_id="succ")


def test_the_permit_is_typed_and_a_refusal_releases_through_it(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    src = _legacy("lp-s8")
    import loop_planning as lp
    seen = {}

    def spy(ckpt):
        seen["permit"] = ckpt.resume_permit
        raise RuntimeError("boom")
    monkeypatch.setattr(ckmod, "resume_from", spy)
    res = _api_resume("lp-s8")
    assert res.status == "stuck" and "restore failed" in res.stuck_reason
    assert load_checkpoint("lp-s8").resume_claim is None          # released through the permit
    assert seen["permit"]
    permit = ckmod.permit_of(type("C", (), {"resume_source": src, "resume_permit": "n", "loop_id": "lp-s8"})())
    assert permit == ResumePermit(src, "n", "lp-s8")
    assert ckmod.permit_of(type("C", (), {"resume_source": None, "resume_permit": "n", "loop_id": "x"})()) is None
    assert ckmod.permit_of(type("C", (), {"resume_source": src, "resume_permit": "n", "loop_id": ""})()) is None
