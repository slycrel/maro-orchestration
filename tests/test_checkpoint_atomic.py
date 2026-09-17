"""Checkpoint writes are crash-safe (2026-09-16, LoopsBench chunk 3).

`write_checkpoint` and `branch_checkpoint` used `Path.write_text` — a kill
mid-write left a torn file that `load_checkpoint` read as "no checkpoint",
so a crashed run resumed as nothing-done. Both now write through
`file_lock.atomic_write` (mkstemp + fsync + os.replace), the helper
`mark_checkpoint_consumed` already used. Must-detect: a write that fails
part-way leaves the PREVIOUS checkpoint intact and loadable, and a
successful write leaves no temp file behind.
"""
import json
import os

import checkpoint as ckmod
from checkpoint import load_checkpoint, write_checkpoint


class _Row:
    def __init__(self, index, text, status="done"):
        self.index, self.text, self.status, self.result = index, text, status, "r"


def _ckpt_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    return ckmod._checkpoint_dir()


def test_successful_write_leaves_no_temp_files(monkeypatch, tmp_path):
    d = _ckpt_dir(tmp_path, monkeypatch)
    write_checkpoint("lp-a", "g", "p", ["one", "two"], [_Row(3, "one")], step_indices=[3, 4])
    names = sorted(p.name for p in d.iterdir())
    assert names == ["ckpt_lp-a.json"], names
    ck = load_checkpoint("lp-a")
    assert ck is not None and ck.remaining_steps == ["two"]


def test_failed_write_keeps_previous_checkpoint_intact(monkeypatch, tmp_path, caplog):
    """Simulated crash inside the helper (the rename never happens): the
    file on disk is still the previous, complete checkpoint."""
    d = _ckpt_dir(tmp_path, monkeypatch)
    write_checkpoint("lp-b", "g", "p", ["one", "two"], [_Row(3, "one")], step_indices=[3, 4])
    before = (d / "ckpt_lp-b.json").read_text(encoding="utf-8")

    attempts = []

    def no_replace(src, dst, *a, **k):
        attempts.append((str(src), str(dst)))
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", no_replace)
    with caplog.at_level("WARNING", logger="maro.checkpoint"):
        write_checkpoint("lp-b", "g", "p", ["one", "two"],
                         [_Row(3, "one"), _Row(4, "two")], step_indices=[3, 4])   # non-fatal by contract
    monkeypatch.undo()
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    # Negative control (review r1 finding 4): the write DID reach the rename
    # — a writer that skips existing targets would pass the byte check too.
    assert len(attempts) == 1 and attempts[0][1] == str(d / "ckpt_lp-b.json"), attempts
    assert attempts[0][0].startswith(str(d / "ckpt_lp-b.json.tmp"))
    assert any("checkpoint write failed for lp-b at " in r.message
               and "ckpt_lp-b.json" in r.message for r in caplog.records)
    assert (d / "ckpt_lp-b.json").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in d.iterdir()) == ["ckpt_lp-b.json"]   # temp cleaned up
    ck = load_checkpoint("lp-b")
    assert ck is not None and ck.remaining_steps == ["two"]


def test_write_goes_through_the_atomic_helper(monkeypatch, tmp_path):
    """Mechanism pin: the checkpoint path is only ever produced by
    atomic_write — Path.write_text on it is the torn-file bug."""
    d = _ckpt_dir(tmp_path, monkeypatch)
    seen = []
    real = ckmod.atomic_write

    def spy(path, content, **kw):
        seen.append(str(path))
        return real(path, content, **kw)

    monkeypatch.setattr(ckmod, "atomic_write", spy)
    write_checkpoint("lp-c", "g", "p", ["one"], [_Row(1, "one")], step_indices=[1])
    new_id = ckmod.branch_checkpoint("lp-c")
    assert seen == [str(d / "ckpt_lp-c.json"), str(d / f"ckpt_{new_id}.json")], seen
    assert json.loads((d / f"ckpt_{new_id}.json").read_text())["parent_loop_id"] == "lp-c"


# ---------------------------------------------------------------- resume fails closed

def _loop_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import introspect
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)
    monkeypatch.setattr(introspect, "plan_recovery", lambda diag: None)


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


def test_resume_of_an_unreadable_checkpoint_fails_closed(monkeypatch, tmp_path):
    """r1 finding 1: a checkpoint file that EXISTS but cannot be parsed must
    not turn an explicit resume into a fresh run (replaying every step)."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    d = ckmod._checkpoint_dir()
    (d / "ckpt_torn1234.json").write_text('{"loop_id": "torn1234", "goal": "g", "st', encoding="utf-8")
    assert load_checkpoint("torn1234") is None
    adapter = _CountingAdapter()
    result = al.run_agent_loop("torn resume", adapter=adapter,
                               preset_steps=["Step one: fetch", "Step two: report"],
                               max_steps=2, max_iterations=4, resume_from_loop_id="torn1234")
    assert result.status == "stuck", result.status
    assert "refusing to start fresh" in (result.stuck_reason or "") and "ckpt_torn1234.json" in result.stuck_reason
    assert adapter.calls == 0, "a step ran despite the unreadable checkpoint"


def test_resume_with_no_checkpoint_still_starts_fresh(monkeypatch, tmp_path):
    """Absent (not torn) keeps the documented behaviour: start fresh."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    adapter = _CountingAdapter()
    result = al.run_agent_loop("fresh", adapter=adapter, preset_steps=["Step one: fetch"],
                               max_steps=1, max_iterations=3, resume_from_loop_id="nope0000")
    assert result.status == "done" and adapter.calls >= 1


def test_resume_whose_restore_raises_fails_closed(monkeypatch, tmp_path):
    """The checkpoint loaded but restoring it blew up: same direction."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    write_checkpoint("lp-r", "restore fail", "", ["Step one: fetch", "Step two: report"],
                     [_Row(3, "Step one: fetch")], step_indices=[3, 4])
    monkeypatch.setattr(ckmod, "resume_from", lambda ckpt: (_ for _ in ()).throw(RuntimeError("boom")))
    adapter = _CountingAdapter()
    result = al.run_agent_loop("restore fail", adapter=adapter,
                               preset_steps=["Step one: fetch", "Step two: report"],
                               max_steps=2, max_iterations=4, resume_from_loop_id="lp-r")
    assert result.status == "stuck" and "restore failed" in (result.stuck_reason or "")
    assert adapter.calls == 0


def test_fallback_file_naming_another_loop_is_refused(monkeypatch, tmp_path):
    """r2 finding 2: `ckpt_<requested>.json` whose body is another loop's
    checkpoint must neither load as the requested loop nor start fresh."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    d = ckmod._checkpoint_dir()
    write_checkpoint("other-loop", "other goal", "", ["Other step"], [], step_indices=[1])
    (d / "ckpt_wanted01.json").write_text((d / "ckpt_other-loop.json").read_text(), encoding="utf-8")
    assert load_checkpoint("wanted01") is None
    adapter = _CountingAdapter()
    result = al.run_agent_loop("wanted goal", adapter=adapter, preset_steps=["Step one: fetch"],
                               max_steps=1, max_iterations=3, resume_from_loop_id="wanted01")
    assert result.status == "stuck" and "ckpt_wanted01.json" in (result.stuck_reason or "")
    assert adapter.calls == 0


def test_lookup_error_is_not_absent(monkeypatch, tmp_path):
    """r2 finding 1: a lookup that raises (unreadable checkpoint dir) refuses
    rather than reading as 'no checkpoint'."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    monkeypatch.setattr(ckmod, "load_checkpoint", lambda loop_id: None)
    # chunk 6: the lookup READS each id address (no existence preflight via
    # _find_checkpoint_path) — the address computation raising is the same class
    monkeypatch.setattr(ckmod, "_checkpoint_path",
                        lambda loop_id: (_ for _ in ()).throw(PermissionError("EACCES")))
    adapter = _CountingAdapter()
    result = al.run_agent_loop("lookup", adapter=adapter, preset_steps=["Step one: fetch"],
                               max_steps=1, max_iterations=3, resume_from_loop_id="lp-x")
    assert result.status == "stuck" and "lookup" in (result.stuck_reason or "")
    assert adapter.calls == 0


def test_refusal_stamps_stop_verdict_and_edge(monkeypatch, tmp_path):
    """r2 finding 4: the refusal is typed (stop verdict) and traced."""
    _loop_env(monkeypatch, tmp_path)
    import loop_planning as lp
    from loop_types import LoopContext
    edges = []
    import run_trace
    monkeypatch.setattr(run_trace, "record_edge", lambda a, b, **kw: edges.append((a, b, kw)))
    ctx = LoopContext(loop_id="l1", goal="g", project="")
    stamped = []
    monkeypatch.setattr(ctx, "stamp_stop", lambda v, e="": stamped.append((v, e)))
    res = lp._refuse_resume(ctx, "lp-z", "checkpoint lp-z exists at /x but could not be read")
    assert res.status == "stuck" and res.loop_id == "l1"
    assert stamped == [("external-interrupt", "checkpoint lp-z exists at /x but could not be read")]
    assert edges and edges[0][:2] == ("plan.resume", "plan.resume_refused")
    assert edges[0][2]["resume_from"] == "lp-z"
