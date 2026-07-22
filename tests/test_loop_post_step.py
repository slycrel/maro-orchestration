"""Focused tests for post-step session safety boundaries."""

from types import SimpleNamespace

from loop_post_step import _post_step_checks


def _ctx():
    return SimpleNamespace(
        goal="inspect external material",
        project="demo",
        loop_id="loop-demo",
        adapter=SimpleNamespace(model_key="test"),
        hook_registry=None,
        dry_run=False,
    )


def test_high_risk_external_result_taints_executor_session():
    outcome = {}
    report = SimpleNamespace(risk=3, sanitized="[redacted]", signals=["override"])
    risk = SimpleNamespace(HIGH=3)

    status, result, _ = _post_step_checks(
        _ctx(),
        "Fetch https://example.com/data",
        1,
        "done",
        "hostile external content " * 20,
        "fetched",
        10,
        outcome,
        security_available=True,
        scan_content_fn=lambda text, log_fn: report,
        injection_risk_cls=risk,
    )

    assert status == "done"
    assert result == "[redacted]"
    assert outcome["executor_session_tainted"] == "high-risk external content"


def test_external_scan_failure_taints_executor_session():
    outcome = {}

    def fail_scan(text, log_fn):
        raise RuntimeError("scanner unavailable")

    _post_step_checks(
        _ctx(),
        "Fetch https://example.com/data",
        1,
        "done",
        "external content " * 20,
        "fetched",
        10,
        outcome,
        security_available=True,
        scan_content_fn=fail_scan,
        injection_risk_cls=SimpleNamespace(HIGH=3),
    )

    assert outcome["executor_session_tainted"] == "external-content scan failed"


# ---------------------------------------------------------------------------
# Artifact evidence note for the ralph verifier (run 75a88777)
# ---------------------------------------------------------------------------

def test_artifacts_evidence_note_lists_files(tmp_path, monkeypatch):
    import orch_items
    from loop_post_step import _artifacts_evidence_note

    proj = tmp_path / "demo-project"
    art = proj / "artifacts"
    art.mkdir(parents=True)
    (art / "step-2-output.txt").write_text("root post body: 271 FREE skills\n")
    (art / "claims.md").write_text("| claim | verdict |\n")
    monkeypatch.setattr(orch_items, "project_dir", lambda slug: proj)

    note = _artifacts_evidence_note("demo-project")
    assert "step-2-output.txt" in note
    assert "claims.md" in note
    assert "271 FREE skills" in note   # head excerpt included
    assert " B, " in note              # size present


def test_artifacts_evidence_note_missing_dir_empty(tmp_path, monkeypatch):
    import orch_items
    from loop_post_step import _artifacts_evidence_note
    monkeypatch.setattr(orch_items, "project_dir",
                        lambda slug: tmp_path / "nope")
    assert _artifacts_evidence_note("nope") == ""


def test_decision_directive_fans_out(tmp_path, monkeypatch):
    """Swarm-review chunk 3: a step's DECISION directive reaches the durable
    journal, the uncompressed shared-context carry, and the thread brain."""
    import threading

    import knowledge_lens as kl
    import loop_post_step as lps
    import runs as runs_module
    import thread_brain

    monkeypatch.setattr(kl, "_memory_dir", lambda: tmp_path)
    monkeypatch.setattr(lps, "_orch", lambda: SimpleNamespace(
        mark_item=lambda *a, **k: None, STATE_DONE="done"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    thread_brain.create_thread_brain(run_dir, goal="demo goal")
    monkeypatch.setattr(runs_module, "current_run_dir", lambda: run_dir)

    ctx = SimpleNamespace(
        goal="demo goal", project="demo-project", loop_id="loop-x",
        adapter=SimpleNamespace(model_key="test"), hook_registry=None,
        dry_run=False, verbose=False, step_callback=None,
    )
    shared = {}
    outcome = {
        "result": "did the thing",
        "decisions": [{"decision": "Use CSV output",
                       "rationale": "Stable schema for later steps"}],
    }
    lps._process_done_step(
        ctx, "produce the report", 2, "did the thing", "done it", 10,
        outcome, item_index=-1, iteration=1,
        completed_context=[], remaining_steps=[], remaining_indices=[],
        loop_shared_ctx=shared, scratchpad={},
        scratchpad_lock=threading.Lock(),
    )

    # 1. Durable journal (recall substrate #3 reads this in future runs)
    journal = tmp_path / "decisions.jsonl"
    assert journal.exists()
    row = __import__("json").loads(journal.read_text().splitlines()[0])
    assert row["decision"] == "Use CSV output"
    assert row["domain"] == "demo-project"
    assert row["goal_context"] == "demo goal"
    # 2. Uncompressed carry
    assert shared["decision:2:0"] == "Use CSV output — Stable schema for later steps"
    # 3. Thread brain Decisions line
    brain = thread_brain.brain_path(run_dir).read_text()
    assert "step 2 [executor]: Use CSV output" in brain


def test_decision_journal_failure_never_perturbs_loop(tmp_path, monkeypatch):
    import threading

    import knowledge_lens as kl
    import loop_post_step as lps

    def _boom():
        raise RuntimeError("journal dir gone")

    monkeypatch.setattr(kl, "_memory_dir", _boom)
    monkeypatch.setattr(lps, "_orch", lambda: SimpleNamespace(
        mark_item=lambda *a, **k: None, STATE_DONE="done"))

    ctx = SimpleNamespace(
        goal="g", project="p", loop_id="l",
        adapter=SimpleNamespace(model_key="test"), hook_registry=None,
        dry_run=False, verbose=False, step_callback=None,
    )
    shared = {}
    outcome = {"result": "r",
               "decisions": [{"decision": "D", "rationale": "R"}]}
    result = lps._process_done_step(
        ctx, "step", 1, "r", "s", 5, outcome, item_index=-1, iteration=1,
        completed_context=[], remaining_steps=[], remaining_indices=[],
        loop_shared_ctx=shared, scratchpad={},
        scratchpad_lock=threading.Lock(),
    )
    assert result == "r"                      # loop unperturbed
    assert shared["decision:1:0"] == "D — R"  # carry still happens
