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
