"""Explicit resume: one discriminated loader, the CLI hands over its
validated checkpoint, every pre-execution refusal releases what init
acquired (2026-09-16, LoopsBench chunk 6).

Chunk 3 made an explicit resume fail closed on a torn id-addressed file;
two leads stayed open: a torn `<run-dir>/build/checkpoint.json` carries its
loop_id INSIDE the JSON, so the scan could not attribute it and the loop
read it as absent → fresh; and `maro resume` validated one file, then the
loop re-read the id (a torn write in between took the fresh branch). And
both pre-execution refusals (cost gate, resume refusal) returned with the
project slot, run lease and running marker still held.
"""
import json
import os
from types import SimpleNamespace

import pytest

import checkpoint as ckmod
from checkpoint import (find_checkpoint, load_checkpoint, write_checkpoint,
                        LOOKUP_ABSENT, LOOKUP_FOUND, LOOKUP_INVALID,
                        LOOKUP_MISMATCH, LOOKUP_IO_ERROR)


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


def _run_dir_with(handle, *, body, loop_ids=None, metadata=True):
    from runs import run_dir
    rd = run_dir(handle)
    (rd / "build").mkdir(parents=True, exist_ok=True)
    (rd / "build" / "checkpoint.json").write_text(body, encoding="utf-8")
    if metadata:
        (rd / "metadata.json").write_text(
            json.dumps({"handle_id": handle, "loop_ids": list(loop_ids or [])}), encoding="utf-8")
    return rd / "build" / "checkpoint.json"


def _good_body(loop_id):
    write_checkpoint(loop_id, "g", "", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch")], step_indices=[1, 2])
    return (ckmod._checkpoint_dir() / f"ckpt_{loop_id}.json").read_text(encoding="utf-8")


# ------------------------------------------------------------ the loader's states

def test_lookup_states_of_the_id_addressed_file(monkeypatch, tmp_path):
    d = _env(monkeypatch, tmp_path)
    assert find_checkpoint("nothing1").state == LOOKUP_ABSENT
    write_checkpoint("good0001", "g", "", ["A"], [], step_indices=[1])
    lk = find_checkpoint("good0001")
    assert lk.found and lk.ckpt.loop_id == "good0001" and lk.path == d / "ckpt_good0001.json"
    (d / "ckpt_torn0001.json").write_text('{"loop_id": "torn0001", "st', encoding="utf-8")
    lk = find_checkpoint("torn0001")
    assert lk.state == LOOKUP_INVALID and lk.ckpt is None and "ckpt_torn0001.json" in lk.detail
    write_checkpoint("other001", "g", "", ["A"], [], step_indices=[1])
    (d / "ckpt_wanted01.json").write_text((d / "ckpt_other001.json").read_text(), encoding="utf-8")
    lk = find_checkpoint("wanted01")
    assert lk.state == LOOKUP_MISMATCH and "names loop other001" in lk.detail
    (d / "ckpt_isdir001.json").mkdir()          # exists, cannot be read as a file
    lk = find_checkpoint("isdir001")
    assert lk.state == LOOKUP_IO_ERROR and "ckpt_isdir001.json" in lk.detail
    # the lossy wrapper keeps its contract: a checkpoint only when FOUND
    assert load_checkpoint("good0001") is not None
    assert all(load_checkpoint(x) is None for x in ("nothing1", "torn0001", "wanted01", "isdir001"))


def test_run_dir_scan_attributes_a_damaged_file_by_its_metadata(monkeypatch, tmp_path):
    """A torn run-dir checkpoint cannot be attributed by its body. Its run
    dir's metadata decides: names this loop → damaged OURS (refuse); names
    only others → not ours; cannot say → unattributable, reported with
    the path (it cannot rule this loop out) — never silently absent."""
    _env(monkeypatch, tmp_path)
    torn = '{"loop_id": "??'
    # ours, damaged
    p_ours = _run_dir_with("h-ours", body=torn, loop_ids=["mine0001"])
    lk = find_checkpoint("mine0001")
    assert lk.state == LOOKUP_INVALID and lk.path == p_ours, lk
    # theirs, damaged: not ours → absent
    _run_dir_with("h-theirs", body=torn, loop_ids=["other001"])
    assert find_checkpoint("someone1").state == LOOKUP_ABSENT
    # unattributable (no metadata): reported, not absent
    p_unk = _run_dir_with("h-unknown", body=torn, metadata=False)
    lk = find_checkpoint("someone1")
    assert lk.state == LOOKUP_INVALID and lk.path == p_unk and "cannot say" in lk.detail, lk
    # a good run-dir file is still found through the scan
    _run_dir_with("h-good", body=_good_body("found001"), loop_ids=["found001"])
    (ckmod._checkpoint_dir() / "ckpt_found001.json").unlink()
    lk = find_checkpoint("found001")
    assert lk.found and lk.ckpt.loop_id == "found001"


def test_a_lookup_that_raises_is_io_error_not_absent(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ckmod, "_checkpoint_path",
                        lambda loop_id: (_ for _ in ()).throw(PermissionError("EACCES")))
    lk = find_checkpoint("lp-x")
    assert lk.state == LOOKUP_IO_ERROR and "lookup" in lk.detail and "EACCES" in lk.detail


# ------------------------------------------------------------ the loop's use of it

def test_unattributable_damaged_run_dir_file_refuses_an_explicit_resume(monkeypatch, tmp_path):
    """The chunk-3 gap: through the real loop, an explicit resume whose only
    candidate is a torn run-dir file with no metadata refuses (names the
    file) instead of starting fresh and replaying every step."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    p = _run_dir_with("h-unk2", body='{"loop_id": "', metadata=False)
    adapter = _CountingAdapter()
    res = al.run_agent_loop("torn run-dir", adapter=adapter, preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3, resume_from_loop_id="lp-torn2")
    assert res.status == "stuck" and str(p) in (res.stuck_reason or "") and "refusing" in res.stuck_reason
    assert adapter.calls == 0
    # attributed to another loop: not ours → fresh, as before
    _run_dir_with("h-unk2", body='{"loop_id": "', loop_ids=["someone-else"])
    res = al.run_agent_loop("torn theirs", adapter=adapter, preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3, resume_from_loop_id="lp-torn2")
    assert res.status == "done" and adapter.calls >= 1


def test_a_preloaded_checkpoint_is_used_without_a_second_read(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    write_checkpoint("lp-pre1", "pre", "", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch")], step_indices=[1, 2])
    # chunk 7: a handed-in object must be the read-back of a CLAIM; it is
    # never re-resolved by id — its EXACT source path is re-read ONCE to
    # prove the claim on disk is still ours (r3 finding 9)
    src = ckmod._checkpoint_path("lp-pre1")
    ck = ckmod.mark_checkpoint_claimed("lp-pre1", path=src, handle_id="h-pre1",
                                       expected=ckmod.find_checkpoint("lp-pre1"))
    assert ck is not None
    monkeypatch.setattr(ckmod, "find_checkpoint",
                        lambda loop_id: (_ for _ in ()).throw(AssertionError("re-read")))
    reads = []
    real_read = ckmod._read_candidate
    monkeypatch.setattr(ckmod, "_read_candidate",
                        lambda path, loop_id: reads.append((str(path), loop_id)) or real_read(path, loop_id))
    adapter = _CountingAdapter()
    res = al.run_agent_loop("pre", adapter=adapter, preset_steps=["x"], max_steps=2,
                            max_iterations=4, resume_checkpoint=ck)
    assert res.status == "done" and adapter.calls >= 1
    assert reads[0] == (str(src), "lp-pre1") and len([r for r in reads if r[0] == str(src)]) == 1
    assert [st.text for st in res.steps] == ["Step one: fetch", "Step two: report"]
    assert res.steps[0].result == "r"                            # the carried row, not re-run
    calls_after_first = adapter.calls
    # a handed-over checkpoint naming another loop is refused, not trusted
    res = al.run_agent_loop("pre", adapter=adapter, preset_steps=["x"], max_steps=2,
                            max_iterations=4, resume_from_loop_id="lp-other",
                            resume_checkpoint=ck)
    assert res.status == "stuck" and "names loop 'lp-pre1'" in res.stuck_reason
    assert adapter.calls == calls_after_first


# ------------------------------------------------------------ the CLI

def test_cli_resume_names_the_damaged_file_and_hands_the_object_over(monkeypatch, tmp_path, capsys):
    import argparse
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    import llm
    # a handle whose run-dir checkpoint is torn: WHY, not "no checkpoint found"
    p = _run_dir_with("h-torn", body='{"loop_id": "h', loop_ids=["lp-t"])
    rc = cli._cmd_resume(argparse.Namespace(run_id="h-torn", verbose=False, format="text"))
    err = capsys.readouterr().err
    assert rc != 0 and "not a readable checkpoint" in err and str(p) in err, err
    # a good one: the loop receives the object the CLI validated
    write_checkpoint("lp-cli1", "cli goal", "", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch")], step_indices=[1, 2])
    seen = {}
    real = al.run_agent_loop

    def spy(goal, **kw):
        seen["resume_checkpoint"] = kw.get("resume_checkpoint")
        seen["resume_from_loop_id"] = kw.get("resume_from_loop_id")
        return real(goal, **kw)
    monkeypatch.setattr(al, "run_agent_loop", spy)
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _CountingAdapter())
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None)
    rc = cli._cmd_resume(argparse.Namespace(run_id="lp-cli1", verbose=False, format="text"))
    assert rc == 0, capsys.readouterr()
    assert seen["resume_from_loop_id"] == "lp-cli1"
    assert getattr(seen["resume_checkpoint"], "loop_id", None) == "lp-cli1"


# ------------------------------------------------------------ refusals release

def _held_ctx(monkeypatch, project="p"):
    from loop_types import LoopContext
    ctx = LoopContext(loop_id="l-ref", goal="g", project=project)
    released = []
    ctx.project_slot = SimpleNamespace(release=lambda: released.append("slot"))
    ctx.run_lease = SimpleNamespace(release=lambda: released.append("lease"))
    ctx.run_worktree = SimpleNamespace(repo_dir=str(monkeypatch), branch="b")
    ctx.container_clone = SimpleNamespace(path="/x/clone", branch="c")
    import interrupt
    monkeypatch.setattr(interrupt, "clear_loop_running", lambda: released.append("running"))
    import runs
    stamped = []
    monkeypatch.setattr(runs, "stamp_run_stop_verdict",
                        lambda **kw: stamped.append((kw["stop_verdict"], kw["stop_evidence"])))
    import worktree
    monkeypatch.setattr(worktree, "cleanup", lambda wt, **kw: released.append(("wt", kw)))
    monkeypatch.setattr(worktree, "cleanup_clone", lambda clone, **kw: released.append("clone"))
    monkeypatch.setattr(worktree, "prune", lambda repo: None)
    return ctx, released, stamped


def test_resume_refusal_ends_like_a_finished_run(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    import loop_planning as lp
    ctx, released, stamped = _held_ctx(monkeypatch)
    res = lp._refuse_resume(ctx, "lp-z", "checkpoint lp-z exists at /x but could not be read")
    assert res.status == "stuck" and res.stop_verdict == "external-interrupt"
    # clone first (it is cut from the worktree), then worktree, then what
    # init acquired: slot → lease → running marker
    assert released == ["clone", ("wt", {"keep_on_failure": False}), "slot", "lease", "running"], released
    assert ctx.project_slot is None and ctx.run_lease is None
    assert ctx.run_worktree is None and ctx.container_clone is None
    assert stamped == [("external-interrupt", "checkpoint lp-z exists at /x but could not be read")]


def test_cost_gate_refusal_ends_like_a_finished_run(monkeypatch, tmp_path):
    """The other pre-execution refusal, through the real loop: the running
    marker is cleared and the verdict is typed (`out-of-budget`)."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import interrupt
    import metrics
    import runs
    cleared = []
    stamped = []
    monkeypatch.setattr(interrupt, "clear_loop_running", lambda: cleared.append(1))
    monkeypatch.setattr(runs, "stamp_run_stop_verdict",
                        lambda **kw: stamped.append(kw["stop_verdict"]))
    monkeypatch.setattr(metrics, "estimate_loop_cost", lambda n, **kw: 999.0)
    adapter = _CountingAdapter()
    res = al.run_agent_loop("pricey", adapter=adapter, preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3, cost_budget=1.0)
    assert res.status == "stuck" and "exceeds budget" in res.stuck_reason
    assert res.stop_verdict == "out-of-budget" and "out-of-budget" in stamped
    assert cleared and adapter.calls == 0


def test_fence_refusal_releases_through_the_shared_helper(monkeypatch, tmp_path):
    """The third pre-execution refusal (execution-fence setup failure) had
    its own inline slot/lease/marker trio; it now ends through the same
    `release_loop_resources` as the other two."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import loop_finalize
    import runs
    import heartbeat
    seen = []
    stamped = []
    monkeypatch.setattr(loop_finalize, "release_loop_resources", lambda ctx: seen.append(ctx.loop_id))
    monkeypatch.setattr(runs, "stamp_run_stop_verdict", lambda **kw: stamped.append(kw["stop_verdict"]))
    monkeypatch.setattr(heartbeat, "post_heartbeat_event",
                        lambda event_type="generic", payload=None: seen.append(event_type))
    import llm
    monkeypatch.setattr(llm, "set_default_subprocess_cwd",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("fence boom")))
    adapter = _CountingAdapter()
    res = al.run_agent_loop("fenced", adapter=adapter, preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3)
    assert res.status == "stuck" and "fence boom" in res.stuck_reason
    assert res.stop_verdict == "external-interrupt" and stamped == ["external-interrupt"]
    # released, then the heartbeat woken — the same ending as the other refusals
    assert seen == [res.loop_id, "loop_done"] and adapter.calls == 0


# ------------------------------------------------------------ r1 fixes: identity and shadowing

@pytest.mark.parametrize("body", [
    b'{"a": 1}',                                                    # JSON, not a checkpoint
    b'{"loop_id": "", "goal": "g", "steps": []}',                   # empty identity
    b'{"loop_id": 7, "goal": "g", "steps": []}',                    # non-string identity
    b'{"loop_id": "x", "goal": ["g"], "steps": []}',                # non-string goal
    b'{"loop_id": "x", "goal": "g", "steps": ["a"], "completed": "\xff\xfe"}',  # bad UTF-8
])
def test_json_valid_or_undecodable_non_checkpoints_are_invalid(monkeypatch, tmp_path, body):
    """A file that parses as JSON but is not a checkpoint — or cannot be
    decoded at all — is INVALID (refuse, name the file), never FOUND with
    defaults (an empty loop_id used to resume as loop '' → fresh) and
    never IO_ERROR-by-accident (UnicodeDecodeError is not an OSError)."""
    d = _env(monkeypatch, tmp_path)
    (d / "ckpt_lp-j.json").write_bytes(body.replace(b"\\xff\\xfe", b"\xff\xfe"))
    lk = find_checkpoint("lp-j")
    assert lk.state == LOOKUP_INVALID and "ckpt_lp-j.json" in lk.detail, lk
    assert load_checkpoint("lp-j") is None


def test_empty_loop_id_never_reaches_the_loop_as_a_resume(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    # handle-addressed file with an empty identity: INVALID at the CLI seam
    p = _run_dir_with("h-empty", body='{"loop_id": "", "goal": "g", "steps": ["a"]}', loop_ids=[])
    lk = cli._lookup_resume_checkpoint("h-empty")
    assert lk.state == LOOKUP_INVALID and lk.path == p and cli._load_resume_checkpoint("h-empty") is None
    # a handed-over object with an empty id (or no Checkpoint type at all) is refused, not skipped
    adapter = _CountingAdapter()
    ck = ckmod.Checkpoint(loop_id="", goal="g", project="", steps=["Step one: fetch"], completed=[])
    res = al.run_agent_loop("empty id", adapter=adapter, preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3, resume_checkpoint=ck)
    assert res.status == "stuck" and "refusing" in res.stuck_reason and adapter.calls == 0
    res = al.run_agent_loop("not a ckpt", adapter=adapter, preset_steps=["Step one: fetch"],
                            max_steps=1, max_iterations=3,
                            resume_checkpoint=SimpleNamespace(loop_id="lp-fake"))
    assert res.status == "stuck" and "not a Checkpoint" in res.stuck_reason and adapter.calls == 0


def test_malformed_metadata_loop_ids_cannot_attribute(monkeypatch, tmp_path):
    """A string `loop_ids` used to be iterated character by character and
    attribute the run to loops 'a', 'b', … — ruling the real loop OUT
    (absent → fresh). Any shape the writer never produces is 'cannot say'."""
    _env(monkeypatch, tmp_path)
    from runs import run_dir
    torn = '{"loop_id": "'
    for meta in ({"loop_ids": "abc"}, {"loop_ids": ["ok", 5]}, {"loop_id": 12}, ["not", "a", "dict"]):
        _run_dir_with("h-meta", body=torn, metadata=False)
        (run_dir("h-meta") / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        for asked in ("a", "someone1", "ok"):
            lk = find_checkpoint(asked)
            assert lk.state == LOOKUP_INVALID and "cannot say" in lk.detail, (meta, asked, lk)
    assert ckmod._run_dir_loop_ids(run_dir("h-meta") / "build" / "checkpoint.json") is None


def test_unreadable_runs_root_is_io_error_not_absent(monkeypatch, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    _env(monkeypatch, tmp_path)
    from runs import runs_root
    _run_dir_with("h-perm", body=_good_body("lp-perm"), loop_ids=["lp-perm"])
    (ckmod._checkpoint_dir() / "ckpt_lp-perm.json").unlink()
    root = runs_root()
    root.chmod(0)
    try:
        lk = find_checkpoint("lp-perm")
    finally:
        root.chmod(0o755)
    assert lk.state == LOOKUP_IO_ERROR and "lookup" in lk.detail, lk
    assert find_checkpoint("lp-perm").found


def _cli_success_harness(monkeypatch):
    import agent_loop as al
    import cli
    import llm
    seen = {}
    real = al.run_agent_loop

    def spy(goal, **kw):
        seen["resume_checkpoint"] = kw.get("resume_checkpoint")
        seen["resume_from_loop_id"] = kw.get("resume_from_loop_id")
        return real(goal, **kw)
    monkeypatch.setattr(al, "run_agent_loop", spy)
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _CountingAdapter())
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None)
    return seen


def test_a_valid_handle_file_is_not_shadowed_by_unrelated_damage(monkeypatch, tmp_path, capsys):
    """Negative control for the conservative scan: an unattributable torn
    checkpoint in SOME OTHER run dir must not block `maro resume <handle>`
    when the handle's own file is fine — the handle path decides first,
    through the real parser."""
    _env(monkeypatch, tmp_path)
    import cli
    _run_dir_with("h-junk", body='{"loop_id": "', metadata=False)
    body = json.loads(_good_body("lp-hv"))
    body["handle_id"] = "h-valid"                 # as write_checkpoint stamps it under a run dir
    _run_dir_with("h-valid", body=json.dumps(body), loop_ids=["lp-hv"])
    (ckmod._checkpoint_dir() / "ckpt_lp-hv.json").unlink()
    # by the HANDLE ref, the loop-id scan alone finds no file naming loop
    # "h-valid" and conservatively reports the junk — the old resolution
    # order refused here
    assert find_checkpoint("h-valid").state == LOOKUP_INVALID
    assert cli._lookup_resume_checkpoint("h-valid").found              # the handle path decides
    seen = _cli_success_harness(monkeypatch)
    rc = cli.main(["resume", "h-valid"])
    assert rc == 0, capsys.readouterr()
    assert seen["resume_from_loop_id"] == "lp-hv"
    assert getattr(seen["resume_checkpoint"], "loop_id", None) == "lp-hv"
    import runs
    assert runs.current_run_dir() is None, "run-dir scope leaked out of the CLI resume"
    # and a handle file that embeds ANOTHER run's handle is a mismatch, not a resume
    body = json.loads(_good_body("lp-hm"))
    body["handle_id"] = "h-someone-else"
    p = _run_dir_with("h-mine", body=json.dumps(body), loop_ids=["lp-hm"])
    lk = cli._lookup_resume_checkpoint("h-mine")
    assert lk.state == LOOKUP_MISMATCH and lk.path == p and "names run h-someone-else" in lk.detail
    rc = cli.main(["resume", "h-mine"])
    assert rc != 0 and "names run h-someone-else" in capsys.readouterr().err


def test_cli_refuses_when_the_reread_changes_lock_identity(monkeypatch, tmp_path, capsys):
    """The admission lock guards ONE identity. If the read under the lock
    resolves to a different run than the read that chose the lock, the
    lock does not cover it — refuse, release, and never start the loop."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    first = ckmod.Checkpoint(loop_id="lp-a", goal="g", project="", steps=["A"], completed=[],
                             handle_id="h-a")
    second = ckmod.Checkpoint(loop_id="lp-b", goal="g", project="", steps=["A"], completed=[],
                              handle_id="h-b")
    reads = iter((first, second))
    monkeypatch.setattr(cli, "_lookup_resume_checkpoint",
                        lambda ref: ckmod.CheckpointLookup(LOOKUP_FOUND, ckpt=next(reads),
                                                           path=tmp_path / "same.json"))
    monkeypatch.setattr(al, "run_agent_loop",
                        lambda *a, **k: pytest.fail("identity changed under the lock"))
    rc = cli.main(["resume", "h-a"])
    err = capsys.readouterr().err
    assert rc != 0 and "changed identity" in err and "'h-a'" in err and "'h-b'" in err, err
    from proc_lock import try_hold_pidfile
    released = try_hold_pidfile(cli._resume_lock_name("h-a"), fail_open=False)
    assert released is not None
    released.close()


# ------------------------------------------------------------ r2 fixes

def test_dangling_link_and_unreadable_child_are_io_error_not_absent(monkeypatch, tmp_path):
    """`Path.exists()` / `is_file()` turn a dangling link and EACCES into
    False; both used to read as absent → fresh. Every address is READ."""
    d = _env(monkeypatch, tmp_path)
    (d / "ckpt_lp-dang.json").symlink_to(d / "nowhere.json")
    lk = find_checkpoint("lp-dang")
    assert lk.state == LOOKUP_IO_ERROR and "dangling" in lk.detail, lk
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    from runs import run_dir
    _run_dir_with("h-child", body=_good_body("lp-child"), loop_ids=["lp-child"])
    (d / "ckpt_lp-child.json").unlink()
    (run_dir("h-child") / "build").chmod(0)
    try:
        lk = find_checkpoint("lp-child")
    finally:
        (run_dir("h-child") / "build").chmod(0o755)
    assert lk.state == LOOKUP_IO_ERROR, lk
    assert find_checkpoint("lp-child").found
    # a run dir whose checkpoint address is a dangling link: io_error too
    _run_dir_with("h-dang", body="x", metadata=False)
    p = run_dir("h-dang") / "build" / "checkpoint.json"
    p.unlink(); p.symlink_to(run_dir("h-dang") / "build" / "gone.json")
    assert find_checkpoint("lp-child").state == LOOKUP_IO_ERROR


def test_resume_ref_grammar_and_a_handle_without_a_checkpoint(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    import cli
    from runs import run_dir
    for bad in ("/tmp/owned", "../victim", "a/b", ".hidden", ""):
        lk = cli._lookup_resume_checkpoint(bad)
        assert lk.state == LOOKUP_INVALID and "not a loop or handle id" in lk.detail, (bad, lk)
    assert cli.main(["resume", "../victim"]) != 0
    assert "not a loop or handle id" in capsys.readouterr().err
    # an existing run dir with no checkpoint is ABSENT for that handle —
    # the string is not re-read as a loop id, so unrelated damage cannot shadow it
    _run_dir_with("h-junk2", body='{"loop_id": "', metadata=False)
    run_dir("h-nockpt").mkdir(parents=True)
    lk = cli._lookup_resume_checkpoint("h-nockpt")
    assert lk.state == LOOKUP_ABSENT and "no checkpoint" in lk.detail, lk
    assert cli.main(["resume", "h-nockpt"]) != 0
    assert "h-nockpt has no checkpoint" in capsys.readouterr().err
    # …while a loop-id ref (no run dir) still reaches the conservative scan
    assert cli._lookup_resume_checkpoint("lp-nowhere").state == LOOKUP_INVALID


def test_cli_refuses_a_same_identity_snapshot_that_lost_finished_positions(monkeypatch, tmp_path, capsys):
    """Same handle, same loop, second read has FEWER finished positions: a
    stale writer replaced the file between the reads — refuse, do not hand
    the loop the older snapshot (it would re-execute step 1)."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    row = ckmod.CompletedStep(index=1, text="A", status="done", position=1)
    first = ckmod.Checkpoint(loop_id="lp-s", goal="g", project="", steps=["A", "B"],
                             completed=[row], positioned=True, handle_id="h-s")
    second = ckmod.Checkpoint(loop_id="lp-s", goal="g", project="", steps=["A", "B"],
                              completed=[], positioned=True, handle_id="h-s")
    reads = iter((first, second))
    monkeypatch.setattr(cli, "_lookup_resume_checkpoint",
                        lambda ref: ckmod.CheckpointLookup(LOOKUP_FOUND, ckpt=next(reads),
                                                           path=tmp_path / "same.json"))
    monkeypatch.setattr(al, "run_agent_loop", lambda *a, **k: pytest.fail("older snapshot handed over"))
    assert cli.main(["resume", "h-s"]) != 0
    assert "changed between reads" in capsys.readouterr().err
    # same finished set, only the in-flight marker gone (r3): still refused
    first = ckmod.Checkpoint(loop_id="lp-s", goal="g", project="", steps=["A", "B"],
                             completed=[row], positioned=True, handle_id="h-s",
                             in_flight={"index": 2, "pid": 999, "started_at": "t"})
    second = ckmod.Checkpoint(loop_id="lp-s", goal="g", project="", steps=["A", "B"],
                              completed=[row], positioned=True, handle_id="h-s")
    reads = iter((first, second))
    monkeypatch.setattr(cli, "_lookup_resume_checkpoint",
                        lambda ref: ckmod.CheckpointLookup(LOOKUP_FOUND, ckpt=next(reads),
                                                           path=tmp_path / "same.json"))
    assert cli.main(["resume", "h-s"]) != 0
    assert "changed between reads" in capsys.readouterr().err
    # a different SOURCE file between the reads is refused too
    reads = iter((first, first))
    paths = iter((tmp_path / "one.json", tmp_path / "two.json"))
    monkeypatch.setattr(cli, "_lookup_resume_checkpoint",
                        lambda ref: ckmod.CheckpointLookup(LOOKUP_FOUND, ckpt=next(reads), path=next(paths)))
    assert cli.main(["resume", "h-s"]) != 0
    assert "different file between reads" in capsys.readouterr().err


def test_cli_reports_the_observation_that_refused(monkeypatch, tmp_path, capsys):
    """One lookup per read: the damaged path the FIRST read saw is what the
    operator hears, even if the damage is repaired a moment later."""
    _env(monkeypatch, tmp_path)
    import cli
    good = ckmod.Checkpoint(loop_id="lp-o", goal="g", project="", steps=["A"], completed=[])
    reads = iter((ckmod.CheckpointLookup(LOOKUP_INVALID, path=tmp_path / "damaged.json",
                                         detail=f"checkpoint {tmp_path / 'damaged.json'} is torn"),
                  ckmod.CheckpointLookup(LOOKUP_FOUND, ckpt=good, path=tmp_path / "damaged.json")))
    calls = []
    monkeypatch.setattr(cli, "_lookup_resume_checkpoint", lambda ref: (calls.append(ref), next(reads))[1])
    assert cli.main(["resume", "lp-o"]) != 0
    assert "damaged.json is torn" in capsys.readouterr().err and calls == ["lp-o"]


def test_handle_resume_whose_final_write_failed_is_not_done(monkeypatch, tmp_path, capsys):
    """A handle resume writes the successor's checkpoint over the source;
    checkpoint writes swallow their own failures, so success must be
    PROVEN by re-reading the source path — else the source is consumed in
    place, and if that fails too the run is not reported done."""
    _env(monkeypatch, tmp_path)
    import cli
    from runs import run_dir
    body = json.loads(_good_body("lp-fw"))
    body["handle_id"] = "h-fw"
    src = _run_dir_with("h-fw", body=json.dumps(body), loop_ids=["lp-fw"])
    (ckmod._checkpoint_dir() / "ckpt_lp-fw.json").unlink()
    seen = _cli_success_harness(monkeypatch)
    # every checkpoint write fails as on a full disk: the CLAIM (written
    # through the locked writer) cannot be written, so nothing executes
    # (chunk 7) — the source is untouched
    import file_lock
    _enospc = lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left on device"))
    monkeypatch.setattr(file_lock, "atomic_write", _enospc)
    monkeypatch.setattr(ckmod, "atomic_write", _enospc)
    rc = cli.main(["resume", "h-fw"])
    out = capsys.readouterr()
    assert rc != 0 and "could not record the resume claim" in out.err and "nothing ran" in out.err, out
    assert "resume_checkpoint" not in seen
    after = ckmod._read_candidate(src, None)
    assert after.found and after.ckpt.loop_id == "lp-fw" and after.ckpt.resume_claim is None
    # the claim lands (locked writer intact), every LATER write fails (the
    # loop's checkpoints, the consume — the checkpoint module's writer):
    # the run is not reported done, and the source keeps the claim
    monkeypatch.undo()
    _env(monkeypatch, tmp_path)
    seen = _cli_success_harness(monkeypatch)
    monkeypatch.setattr(ckmod, "atomic_write", _enospc)
    rc = cli.main(["resume", "h-fw"])
    out = capsys.readouterr()
    assert rc != 0 and "could not be marked consumed" in (out.out + out.err), out
    assert "resume_checkpoint" in seen                              # it DID run
    after = ckmod._read_candidate(src, None)
    assert after.found and after.ckpt.loop_id == "lp-fw" and not after.ckpt.is_consumed()
    assert after.ckpt.resume_claim and after.ckpt.resume_claim["handle_id"] == "h-fw"
    # …and THAT is the chunk-7 closure: the demoted run's source cannot be
    # resumed again (its claim stands; the claimant — this process — is alive)
    monkeypatch.undo()
    _env(monkeypatch, tmp_path)
    seen = _cli_success_harness(monkeypatch)
    assert cli.main(["resume", "h-fw"]) != 0
    assert "in progress as run h-fw" in capsys.readouterr().err and "resume_checkpoint" not in seen
    # the healthy twin (a fresh source): the successor overwrote the source
    # → done, source not consumed
    body = json.loads(_good_body("lp-fw2"))
    body["handle_id"] = "h-fw2"
    src = _run_dir_with("h-fw2", body=json.dumps(body), loop_ids=["lp-fw2"])
    (ckmod._checkpoint_dir() / "ckpt_lp-fw2.json").unlink()
    rc = cli.main(["resume", "h-fw2"])
    assert rc == 0, capsys.readouterr()
    after = ckmod._read_candidate(src, None)
    assert after.found and after.ckpt.loop_id != "lp-fw2" and after.ckpt.is_complete()


# ------------------------------------------------------------ r3 fixes

def test_a_dangling_ancestor_is_io_error_not_absent(monkeypatch, tmp_path):
    """`lexists` on the final path alone misses `build -> missing-dir`;
    the first component that cannot be stat'ed decides (top-down)."""
    import shutil
    d = _env(monkeypatch, tmp_path)
    from runs import run_dir
    # scan: a run dir whose build/ is a dangling link
    rd = run_dir("h-mid"); rd.mkdir(parents=True)
    (rd / "build").symlink_to(rd / "gone")
    lk = find_checkpoint("lp-mid")
    assert lk.state == LOOKUP_IO_ERROR and "lookup" in lk.detail, lk
    (rd / "build").unlink()
    assert find_checkpoint("lp-mid").state == LOOKUP_ABSENT
    # id address: the checkpoint dir itself is a dangling link
    shutil.rmtree(d); d.symlink_to(d.parent / "gone-dir")
    lk = find_checkpoint("lp-mid")
    assert lk.state == LOOKUP_IO_ERROR and "dangling" in lk.detail, lk


def test_handle_and_loop_id_share_one_generator(monkeypatch, tmp_path):
    """`deadbeef` can be a run handle AND another loop's id. Both resumable
    → ambiguous (refuse); handle dir empty + loop file → the loop file;
    handle dir empty, no loop file → absent for the handle."""
    _env(monkeypatch, tmp_path)
    import cli
    from runs import run_dir
    body = json.loads(_good_body("otherloop"))
    body["handle_id"] = "deadbeef"
    p_handle = _run_dir_with("deadbeef", body=json.dumps(body), loop_ids=["otherloop"])
    write_checkpoint("deadbeef", "g", "", ["A"], [], step_indices=[1])
    lk = cli._lookup_resume_checkpoint("deadbeef")
    assert lk.state == LOOKUP_MISMATCH and "ambiguous" in lk.detail and "'deadbeef'" in lk.detail, lk
    p_handle.unlink()
    lk = cli._lookup_resume_checkpoint("deadbeef")
    assert lk.found and lk.ckpt.loop_id == "deadbeef" and lk.path.name == "ckpt_deadbeef.json"
    (ckmod._checkpoint_dir() / "ckpt_deadbeef.json").write_text("{torn", encoding="utf-8")
    lk = cli._lookup_resume_checkpoint("deadbeef")
    assert lk.state == LOOKUP_INVALID and lk.path.name == "ckpt_deadbeef.json"   # about this ref
    (ckmod._checkpoint_dir() / "ckpt_deadbeef.json").unlink()
    assert cli._lookup_resume_checkpoint("deadbeef").state == LOOKUP_ABSENT
    assert run_dir("deadbeef").is_dir()


def test_consumed_checkpoints_are_refused_everywhere(monkeypatch, tmp_path):
    """Consumption is policy at the loader, not only in the CLI: the API
    resume refuses, `branch_checkpoint` refuses, the heartbeat's
    resumable-run scan skips."""
    _env(monkeypatch, tmp_path)
    import agent_loop as al
    import heartbeat
    write_checkpoint("lp-cons", "g", "", ["Step one: fetch", "Step two: report"],
                     [_Row(1, "Step one: fetch")], step_indices=[1, 2])
    assert ckmod.mark_checkpoint_consumed("lp-cons", resumed_to_loop_id="lp-new")
    adapter = _CountingAdapter()
    res = al.run_agent_loop("g", adapter=adapter, preset_steps=["x"], max_steps=2,
                            max_iterations=4, resume_from_loop_id="lp-cons")
    assert res.status == "stuck" and "already resumed successfully as lp-new" in res.stuck_reason
    assert adapter.calls == 0
    assert ckmod.branch_checkpoint("lp-cons") is None
    # heartbeat: a consumed, handle-linked, stranded run is not advertised
    consumed = load_checkpoint("lp-cons")
    consumed.handle_id = "h-cons"
    twin = ckmod.Checkpoint(loop_id="lp-twin", goal="g", project="", steps=["A"],
                            completed=[], handle_id="h-cons")
    _run_dir_with("h-cons", body="{}", metadata=False)
    from runs import run_dir
    (run_dir("h-cons") / "metadata.json").write_text(json.dumps({"status": "stranded"}), encoding="utf-8")
    monkeypatch.setattr(ckmod, "list_checkpoints", lambda: [consumed, twin])
    monkeypatch.setattr(heartbeat, "_lease_owner_alive", lambda loop_id: None)
    assert [r["loop_id"] for r in heartbeat._find_resumable_runs()] == ["lp-twin"]


def test_consumption_marks_the_target_of_a_symlinked_address(monkeypatch, tmp_path):
    d = _env(monkeypatch, tmp_path)
    write_checkpoint("lp-tgt", "g", "", ["A"], [], step_indices=[1])
    link = d / "ckpt_alias.json"
    link.symlink_to(d / "ckpt_lp-tgt.json")
    assert ckmod.mark_checkpoint_consumed("lp-tgt", resumed_to_loop_id="n", path=link)
    assert link.is_symlink()                                      # the link was not replaced
    assert load_checkpoint("lp-tgt").is_consumed()                # the target was consumed
