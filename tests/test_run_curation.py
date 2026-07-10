"""Tests for the post-goal curation pass (run_curation).

Curation classifies a finished run (done≠achieved aware) and inventories what's
mineable into <run-dir>/run_card.json, so later passes can act on the paid-for
capture instead of discarding it. list_runs/prune_run are the user-visible
surface ("show me my runs", "clean that up").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runs
from runs import create_run_dir, finalize_run, set_current_run_dir, record_llm_call
from run_curation import curate_run, list_runs, prune_run, classify_outcome


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    runs._CALL_COUNTERS.clear()
    yield tmp_path
    set_current_run_dir(None)


def _finish(handle_id, prompt, status, *, achieved=None, verdict=None):
    extra = {}
    if achieved is not None:
        extra["goal_achieved"] = achieved
    if verdict is not None:
        extra["goal_verdict_summary"] = verdict
    rd = create_run_dir(handle_id, prompt=prompt, lane="now", model="claude",
                        extra_metadata=extra or None)
    finalize_run(handle_id, status=status)
    return rd


def test_curate_writes_run_card(workspace):
    _finish("h0000001", "build the thing", "done", achieved=True)
    card = curate_run("h0000001")
    assert card is not None
    rd = runs.run_dir("h0000001")
    assert (rd / "run_card.json").is_file()
    assert card["goal"] == "build the thing"
    assert card["success_class"] == "success"


def test_classify_done_not_achieved(workspace):
    _finish("h0000002", "g", "done", achieved=False)
    card = curate_run("h0000002")
    assert card["success_class"] == "done-not-achieved"


def test_classify_done_unverified(workspace):
    _finish("h0000003", "g", "done")  # no goal_achieved key
    card = curate_run("h0000003")
    assert card["success_class"] == "done-unverified"


def test_classify_failed(workspace):
    _finish("h0000004", "g", "stuck")
    card = curate_run("h0000004")
    assert card["success_class"] == "failed"


def test_classify_partial(workspace):
    _finish("h0000005", "g", "partial")
    card = curate_run("h0000005")
    assert card["success_class"] == "partial"


def test_classify_incomplete_is_partial(workspace):
    # closure-demoted runs (status "incomplete") were falling to "unknown"
    _finish("h000000a", "g", "incomplete", achieved=False)
    card = curate_run("h000000a")
    assert card["success_class"] == "partial"


def test_card_costs_from_loop_ids(workspace, monkeypatch):
    import metrics
    rd = create_run_dir("h000000b", prompt="g", lane="agenda", model="claude",
                        extra_metadata={"loop_ids": ["loopAAAA", "loopBBBB"]})
    finalize_run("h000000b", status="done")
    seen = {}

    def _fake_spend(lids):
        seen["lids"] = lids
        return 1.23

    monkeypatch.setattr(metrics, "spend_for_loops", _fake_spend)
    card = curate_run("h000000b")
    assert card["total_cost_usd"] == pytest.approx(1.23)
    assert seen["lids"] == ["loopAAAA", "loopBBBB"]


def test_card_cost_none_without_loop_ids(workspace):
    _finish("h000000c", "g", "done", achieved=True)
    card = curate_run("h000000c")
    assert card["total_cost_usd"] is None


def test_inventory_counts_calls(workspace):
    rd = _finish("h0000006", "g", "done", achieved=True)
    set_current_run_dir(rd)
    record_llm_call("p1", "r1")
    record_llm_call("p2", "r2")
    card = curate_run("h0000006")
    assert card["inventory"]["n_calls"] == 2
    assert card["mineable"] is True


def test_inventory_flags_scripts(workspace):
    rd = _finish("h0000007", "g", "done", achieved=True)
    (rd / "build" / "helper.py").write_text("print('hi')\n")
    card = curate_run("h0000007")
    assert any(s.endswith("helper.py") for s in card["inventory"]["scripts"])
    assert card["mineable"] is True


def test_empty_run_not_mineable(workspace):
    _finish("h0000008", "g", "done", achieved=True)
    card = curate_run("h0000008")
    assert card["inventory"]["n_calls"] == 0
    assert card["mineable"] is False


def test_curate_missing_run_returns_none(workspace):
    assert curate_run("nope9999") is None


def test_list_runs_includes_curated(workspace):
    _finish("h0000009", "alpha goal", "done", achieved=True)
    curate_run("h0000009")
    cards = list_runs()
    assert any(c["handle_id"] == "h0000009" for c in cards)


def test_list_runs_synthesizes_uncurated(workspace):
    _finish("h0000010", "beta goal", "done", achieved=True)
    # not curated — list should still surface it as "uncurated"
    cards = list_runs()
    match = [c for c in cards if c["handle_id"] == "h0000010"]
    assert match and match[0]["success_class"] == "uncurated"


def test_prune_removes_run_dir(workspace):
    rd = _finish("h0000011", "g", "done", achieved=True)
    assert rd.is_dir()
    assert prune_run("h0000011") is True
    assert not rd.is_dir()


def test_prune_missing_returns_false(workspace):
    assert prune_run("nope0000") is False


def test_classify_outcome_is_pure(workspace):
    # Direct curator call — registry functions are pure (rd, meta, card)->None.
    card = {}
    classify_outcome(Path("/nonexistent"), {"status": "done", "goal_achieved": True}, card)
    assert card["success_class"] == "success"


class TestSpendTransparency:
    """BACKLOG #11: above budget.transparency_usd the card carries the full
    build/artifact bundle (absolute paths + sizes) — no grep required."""

    def _expensive_run(self, workspace, monkeypatch, cost=5.0, threshold=None):
        import metrics
        rd = create_run_dir("h00000c1", prompt="big expensive goal",
                            lane="agenda", model="claude",
                            extra_metadata={"loop_ids": ["loopCCCC"]})
        (rd / "artifact").mkdir(exist_ok=True)
        (rd / "artifact" / "report.md").write_text("the deliverable")
        (rd / "build").mkdir(exist_ok=True)
        (rd / "build" / "helper.py").write_text("print('x')")
        finalize_run("h00000c1", status="done")
        monkeypatch.setattr(metrics, "spend_for_loops", lambda lids: cost)
        if threshold is not None:
            import config
            _orig = config.get
            monkeypatch.setattr(
                config, "get",
                lambda key, default=None: threshold if key == "budget.transparency_usd"
                else _orig(key, default))
        return rd

    def test_above_threshold_bundle_present(self, workspace, monkeypatch):
        rd = self._expensive_run(workspace, monkeypatch, cost=5.0)
        card = curate_run("h00000c1")
        st = card.get("spend_transparency")
        assert st is not None
        assert st["threshold_usd"] == pytest.approx(2.0)
        bundle = st["bundle"]
        assert bundle["run_dir"] == str(rd)
        paths = [f["path"] for f in bundle["files"]]
        assert str(rd / "artifact" / "report.md") in paths
        assert str(rd / "build" / "helper.py") in paths
        assert all("bytes" in f for f in bundle["files"])
        assert bundle["truncated"] is False

    def test_below_threshold_no_bundle(self, workspace, monkeypatch):
        self._expensive_run(workspace, monkeypatch, cost=0.4)
        card = curate_run("h00000c1")
        assert "spend_transparency" not in card

    def test_unknown_cost_no_bundle(self, workspace, monkeypatch):
        rd = create_run_dir("h00000c2", prompt="g", lane="agenda", model="claude")
        finalize_run("h00000c2", status="done")  # no loop_ids -> cost None
        card = curate_run("h00000c2")
        assert "spend_transparency" not in card

    def test_configured_threshold_respected(self, workspace, monkeypatch):
        self._expensive_run(workspace, monkeypatch, cost=0.5, threshold=0.25)
        card = curate_run("h00000c1")
        assert card.get("spend_transparency") is not None
        assert card["spend_transparency"]["threshold_usd"] == pytest.approx(0.25)

    def test_zero_threshold_disables(self, workspace, monkeypatch):
        self._expensive_run(workspace, monkeypatch, cost=9.9, threshold=0)
        card = curate_run("h00000c1")
        assert "spend_transparency" not in card


class TestSkillsLite:
    """Rider A (Jeremy 2026-07-09): skill-shaped .md artifacts from successful
    runs promote into the workspace skills overlay immediately (tier:
    skills-lite) + register a companion provisional Skill so normal decay/
    circuit-breaker degradation applies; human review gates only ship-set
    graduation. degrade_skills_lite() quarantines the .md when the companion
    trips."""

    SKILL_MD = (
        "---\n"
        "name: fetch_release_notes\n"
        'description: "Fetch and summarize release notes for a repo"\n'
        "roles_allowed: [worker]\n"
        "triggers: ['release notes', 'changelog summary']\n"
        "---\n"
        "\n# Fetch release notes\n\n1. Locate the CHANGELOG\n2. Summarize\n"
    )

    def _run_with_artifact(self, handle_id, status="done", achieved=True,
                           content=None, fname="skill.md"):
        rd = create_run_dir(handle_id, prompt="build a release-notes skill",
                            lane="agenda", model="claude",
                            extra_metadata={"goal_achieved": achieved,
                                            "loop_ids": ["loopSK01"]})
        (rd / "artifact").mkdir(exist_ok=True)
        (rd / "artifact" / fname).write_text(content or self.SKILL_MD)
        finalize_run(handle_id, status=status)
        return rd

    def _fresh_loader(self):
        from skill_loader import skill_loader as loader
        loader.invalidate()
        return loader

    def test_promotes_skill_artifact(self, workspace):
        import config
        self._run_with_artifact("h00000d1")
        card = curate_run("h00000d1")
        sl = card.get("skills_lite")
        assert sl and [p["name"] for p in sl["promoted"]] == ["fetch_release_notes"]
        dest = config.skills_dir() / "fetch_release_notes.md"
        assert dest.is_file()
        text = dest.read_text()
        assert "tier: skills-lite" in text
        assert "promoted_from: h00000d1" in text
        # locally usable immediately: the loader serves it
        loader = self._fresh_loader()
        assert any(s.name == "fetch_release_notes"
                   for s in loader.find_matching("summarize the release notes"))
        # companion runtime Skill registered = the degradation hook
        from skills import load_skills, load_skill_provenance
        comp = [s for s in load_skills() if s.name == "fetch_release_notes"]
        assert comp and comp[0].tier == "provisional"
        assert comp[0].trigger_patterns == ["release notes", "changelog summary"]
        recs = load_skill_provenance("fetch_release_notes")
        assert recs and recs[0]["decision"] == "create"

    def test_failed_run_promotes_nothing(self, workspace):
        import config
        self._run_with_artifact("h00000d2", status="stuck", achieved=None)
        card = curate_run("h00000d2")
        assert "skills_lite" not in card
        assert not (config.skills_dir() / "fetch_release_notes.md").exists()

    def test_done_not_achieved_promotes_nothing(self, workspace):
        import config
        self._run_with_artifact("h00000d3", status="done", achieved=False)
        card = curate_run("h00000d3")
        assert "skills_lite" not in card
        assert not (config.skills_dir() / "fetch_release_notes.md").exists()

    def test_dangerous_content_skipped(self, workspace):
        import config
        bad = self.SKILL_MD + "\n```python\nimport subprocess\n```\n"
        self._run_with_artifact("h00000d4", content=bad)
        card = curate_run("h00000d4")
        sl = card["skills_lite"]
        assert sl["promoted"] == []
        assert "dangerous pattern" in sl["skipped"][0]["reason"]
        assert not (config.skills_dir() / "fetch_release_notes.md").exists()

    def test_name_collision_skipped(self, workspace):
        import config
        (config.skills_dir() / "fetch_release_notes.md").write_text(self.SKILL_MD)
        self._fresh_loader()
        self._run_with_artifact("h00000d5")
        card = curate_run("h00000d5")
        sl = card["skills_lite"]
        assert sl["promoted"] == []
        assert "collision" in sl["skipped"][0]["reason"]

    def test_non_skill_md_ignored(self, workspace):
        self._run_with_artifact("h00000d6", content="# just a report\n\nprose\n",
                                fname="report.md")
        card = curate_run("h00000d6")
        assert "skills_lite" not in card

    def test_config_off_disables(self, workspace, monkeypatch):
        import config
        import run_curation
        monkeypatch.setattr(run_curation, "_lite_enabled", lambda: False)
        self._run_with_artifact("h00000d7")
        card = curate_run("h00000d7")
        assert "skills_lite" not in card
        assert not (config.skills_dir() / "fetch_release_notes.md").exists()

    def test_open_circuit_quarantines_md(self, workspace):
        import config
        from run_curation import degrade_skills_lite
        from skills import load_skills, save_skill, load_skill_provenance
        self._run_with_artifact("h00000d8")
        curate_run("h00000d8")
        comp = next(s for s in load_skills() if s.name == "fetch_release_notes")
        comp.circuit_state = "open"
        save_skill(comp)
        assert degrade_skills_lite() == ["fetch_release_notes"]
        assert not (config.skills_dir() / "fetch_release_notes.md").exists()
        q = config.skills_dir() / "_quarantine" / "fetch_release_notes.md"
        assert q.is_file()
        loader = self._fresh_loader()
        assert not any(s.name == "fetch_release_notes"
                       for s in loader.load_summaries())
        recs = load_skill_provenance("fetch_release_notes")
        assert recs[0]["decision"] == "demote"

    def test_missing_companion_quarantines_md(self, workspace):
        import config
        from run_curation import degrade_skills_lite
        (config.skills_dir() / "orphan_lite.md").write_text(
            "---\nname: orphan_lite\ndescription: \"x\"\n"
            "triggers: ['orphan']\ntier: skills-lite\n---\nbody\n")
        assert degrade_skills_lite() == ["orphan_lite"]
        assert (config.skills_dir() / "_quarantine" / "orphan_lite.md").is_file()

    def test_healthy_companion_not_quarantined(self, workspace):
        import config
        from run_curation import degrade_skills_lite
        self._run_with_artifact("h00000d9")
        curate_run("h00000d9")
        assert degrade_skills_lite() == []
        assert (config.skills_dir() / "fetch_release_notes.md").is_file()
