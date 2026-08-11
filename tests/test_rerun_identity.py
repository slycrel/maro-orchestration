"""Re-run identity — deterministic prior-attempts brief at intake.

Pins for src/rerun_identity.py and its three injection seams (BACKLOG
2026-08-09, Jeremy: "a re-run should know it's a re-run, with prior art").
The load-bearing property: a consumer reading the brief cannot mistake a
superseded (operator_restamp) or contested verdict for plain failure —
that misread is exactly what escalated the 6b14e413 dispatch at 0.95.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from rerun_identity import (
    PriorAttempt,
    brief_for_goal,
    normalize_goal,
    prior_attempts,
    render_brief,
)


def _write_intake(rows):
    """Append rows to the isolated workspace's handle_inputs.jsonl."""
    from orch_items import memory_dir
    mdir = memory_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    with (mdir / "handle_inputs.jsonl").open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _make_run(handle_id, **meta):
    """Create a run dir with metadata for handle_id, runs.py-shaped."""
    from runs import run_dir
    rd = run_dir(handle_id)
    rd.mkdir(parents=True, exist_ok=True)
    payload = {"handle_id": handle_id, **meta}
    (rd / "metadata.json").write_text(json.dumps(payload), encoding="utf-8")
    return rd


class TestNormalize:
    def test_collapses_case_and_whitespace(self):
        assert normalize_goal("  Do  THE\tthing \n") == "do the thing"
        assert normalize_goal("do the thing") == normalize_goal("Do The Thing")

    def test_different_text_differs(self):
        assert normalize_goal("do the thing") != normalize_goal("do the other thing")

    def test_empty_is_empty(self):
        assert normalize_goal("") == ""
        assert normalize_goal(None) == ""


class TestPriorAttempts:
    GOAL = "evaluate the paper against maro and report adopt/skip rulings"

    def test_no_intake_file_is_empty(self):
        assert prior_attempts(self.GOAL) == []

    def test_exact_match_only_newest_first(self):
        _write_intake([
            {"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "2026-08-01T00:00:00+00:00"},
            {"handle_id": "bbbb2222", "raw_input": "a different goal entirely", "ts": "2026-08-02T00:00:00+00:00"},
            {"handle_id": "cccc3333", "raw_input": "  Evaluate the paper  against MARO and report adopt/skip rulings ", "ts": "2026-08-03T00:00:00+00:00"},
        ])
        out = prior_attempts(self.GOAL)
        assert [a.handle_id for a in out] == ["cccc3333", "aaaa1111"]

    def test_excludes_self_and_dedupes(self):
        _write_intake([
            {"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "t"},
            {"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "t"},
            {"handle_id": "self0000", "raw_input": self.GOAL, "ts": "t"},
        ])
        out = prior_attempts(self.GOAL, exclude_handle_id="self0000")
        assert [a.handle_id for a in out] == ["aaaa1111"]

    def test_dry_run_previews_are_not_attempts(self):
        _write_intake([
            {"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "t"},
            {"handle_id": "dddd4444", "raw_input": self.GOAL, "ts": "t"},
        ])
        _make_run("aaaa1111", goal_achieved=True, goal_verdict_source="closure")
        _make_run("dddd4444", dry_run=True)
        out = prior_attempts(self.GOAL)
        assert [a.handle_id for a in out] == ["aaaa1111"]

    def test_no_run_record_still_listed_with_honest_standing(self):
        _write_intake([{"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "t"}])
        out = prior_attempts(self.GOAL)
        assert len(out) == 1
        assert "no run record" in out[0].standing

    def test_malformed_lines_are_skipped(self):
        from orch_items import memory_dir
        mdir = memory_dir()
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "handle_inputs.jsonl").write_text(
            "not json at all\n"
            + json.dumps({"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "t"})
            + "\n{broken\n",
            encoding="utf-8")
        out = prior_attempts(self.GOAL)
        assert [a.handle_id for a in out] == ["aaaa1111"]

    def test_non_ascii_goal_matches_despite_json_escaping(self):
        # json.dumps escapes non-ASCII — the prefilter must not eat these.
        goal = "übersetze die Zusammenfassung ins Deutsche"
        _write_intake([{"handle_id": "aaaa1111", "raw_input": goal, "ts": "t"}])
        out = prior_attempts(goal)
        assert [a.handle_id for a in out] == ["aaaa1111"]


class TestStanding:
    GOAL = "the recurring goal"

    def _one(self, **meta):
        _write_intake([{"handle_id": "aaaa1111", "raw_input": self.GOAL, "ts": "2026-08-09T12:00:00+00:00"}])
        _make_run("aaaa1111", **meta)
        out = prior_attempts(self.GOAL)
        assert len(out) == 1
        return out[0]

    def test_plain_achieved_carries_source_and_conf(self):
        att = self._one(goal_achieved=True, goal_verdict_source="closure",
                        goal_verdict_confidence=0.75)
        assert att.standing.startswith("ACHIEVED")
        assert "closure" in att.standing and "0.75" in att.standing

    def test_operator_restamp_forbids_failure_reading(self):
        att = self._one(goal_achieved=True,
                        goal_verdict_source="operator_restamp")
        assert att.standing.startswith("ACHIEVED")
        assert "operator re-stamp" in att.standing
        assert "do not read it as failure" in att.standing

    def test_restamp_supersedes_stale_contest_flag(self):
        # de790c13 live-smoke find: a re-stamped record still carrying the
        # pre-restamp contested flag must NOT render as disputed — the
        # operator's word is final.
        att = self._one(goal_achieved=True,
                        goal_verdict_source="operator_restamp",
                        goal_verdict_contested={"by": "closure"})
        assert "operator re-stamp" in att.standing
        assert "CONTESTED" not in att.standing

    def test_contested_is_labeled_anecdote(self):
        att = self._one(goal_achieved=False, goal_verdict_source="closure",
                        goal_verdict_contested={"by": "verdict_audit"})
        assert att.standing.startswith("NOT ACHIEVED")
        assert "CONTESTED" in att.standing
        assert "not ground truth" in att.standing

    def test_stop_verdict_rides_along(self):
        att = self._one(goal_achieved=False, goal_verdict_source="closure",
                        stop_verdict="thesis-refuted")
        assert "thesis-refuted" in att.standing

    def test_no_verdict_reports_status(self):
        att = self._one(status="running")
        assert att.achieved is None
        assert "no verdict recorded" in att.standing
        assert "running" in att.standing


class TestRenderBrief:
    def test_empty_attempts_render_nothing(self):
        assert render_brief([]) == ""

    def test_brief_shape_and_guidance(self):
        atts = [PriorAttempt(handle_id="aaaa1111", ts="2026-08-09T12:00:00+00:00",
                             run_name="aaaa1111-olive-wren",
                             standing="ACHIEVED (source: closure, conf 0.75)")]
        brief = render_brief(atts)
        assert "Re-run notice" in brief
        assert "COMPLETE" in brief
        assert "aaaa1111-olive-wren" in brief
        assert "2026-08-09" in brief
        assert "PRIOR ART" in brief
        assert "must not be counted as a failed attempt" in brief

    def test_cap_names_overflow_count(self):
        atts = [PriorAttempt(handle_id=f"h{i:07d}", ts="2026-08-01T00:00:00+00:00",
                             standing="x") for i in range(9)]
        brief = render_brief(atts)
        assert "9 prior attempts" in brief
        assert "4 earlier attempt(s)" in brief

    def test_deliverables_from_shared_project(self, tmp_path):
        from orch_items import projects_root
        proj = projects_root() / "some-proj"
        (proj / "artifacts").mkdir(parents=True)
        (proj / "FINAL_VERDICT.md").write_text("v", encoding="utf-8")
        (proj / "artifacts" / "rulings.json").write_text("{}", encoding="utf-8")
        atts = [PriorAttempt(handle_id="aaaa1111", ts="t", standing="x",
                             project="some-proj")]
        brief = render_brief(atts)
        assert "some-proj" in brief
        assert "FINAL_VERDICT.md" in brief
        assert "artifacts/rulings.json" in brief

    def test_deliverables_skip_locks_and_rank_root_over_artifacts(self, tmp_path):
        import os
        import time
        from orch_items import projects_root
        proj = projects_root() / "busy-proj"
        (proj / "artifacts").mkdir(parents=True)
        now = time.time()
        # Root deliverable is OLDER than every artifact — must still lead.
        (proj / "FINAL_VERDICT.md").write_text("v", encoding="utf-8")
        os.utime(proj / "FINAL_VERDICT.md", (now - 500, now - 500))
        (proj / "NEXT.md.lock").write_text("", encoding="utf-8")
        for i in range(6):
            p = proj / "artifacts" / f"step-{i}.json"
            p.write_text("{}", encoding="utf-8")
            os.utime(p, (now - i, now - i))
        atts = [PriorAttempt(handle_id="aaaa1111", ts="t", standing="x",
                             project="busy-proj")]
        brief = render_brief(atts)
        assert "FINAL_VERDICT.md" in brief
        assert ".lock" not in brief

    def test_traversal_shaped_project_lists_nothing(self):
        atts = [PriorAttempt(handle_id="aaaa1111", ts="t", standing="x",
                             project="../escape")]
        brief = render_brief(atts)
        assert "Existing deliverables" not in brief


class TestKillswitch:
    def test_brief_for_goal_off_returns_empty(self, monkeypatch):
        _write_intake([{"handle_id": "aaaa1111", "raw_input": "goal", "ts": "t"}])
        import config
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None: False if key == "rerun.brief" else default)
        assert brief_for_goal("goal") == ""

    def test_brief_for_goal_on_by_default(self):
        _write_intake([{"handle_id": "aaaa1111", "raw_input": "goal", "ts": "t"}])
        assert "Re-run notice" in brief_for_goal("goal")


class TestNavigatorSeam:
    def test_render_input_carries_block_with_precedence_note(self):
        from navigator import NavigatorInput
        from navigator_prompt import render_input
        ni = NavigatorInput(goal="g", prior_attempts_block="== Re-run notice ==\nbody")
        text = render_input(ni)
        assert "Prior attempts at this exact goal" in text
        assert "THIS wins" in text
        assert "== Re-run notice ==" in text

    def test_render_input_omits_section_when_empty(self):
        from navigator import NavigatorInput
        from navigator_prompt import render_input
        text = render_input(NavigatorInput(goal="g"))
        assert "Prior attempts at this exact goal" not in text

    def test_digest_reports_chars(self):
        from navigator import NavigatorInput
        ni = NavigatorInput(goal="g", prior_attempts_block="abc")
        assert ni.digest()["prior_attempts_chars"] == 3

    def test_shadow_dispatch_live_threads_block(self, monkeypatch):
        import navigator_shadow as ns
        captured = {}

        def _fake_decide(nav_input, **kw):
            captured["block"] = nav_input.prior_attempts_block
            return None, {}

        import config
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None: True if key == "navigator.act_dispatch" else default)
        import navigator_prompt
        monkeypatch.setattr(navigator_prompt, "decide", _fake_decide)
        ns.shadow_dispatch_live("goal", prior_attempts_block="THE BLOCK")
        assert captured.get("block") == "THE BLOCK"


class TestHandleSeam:
    """The AGENDA run receives the brief via ancestry_context_extra —
    same harness shape as TestOperatorContext in test_handle.py."""

    def test_agenda_context_carries_rerun_brief(self, monkeypatch, tmp_path):
        goal = "build the recurring thing"
        _write_intake([{"handle_id": "aaaa1111", "raw_input": goal,
                        "ts": "2026-08-09T00:00:00+00:00"}])
        _make_run("aaaa1111", goal_achieved=True, goal_verdict_source="closure")

        import llm
        adapter = MagicMock()
        adapter.model_key = "cheap"
        monkeypatch.setattr(llm, "build_adapter", lambda *a, **kw: adapter)

        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        loop_kwargs = []

        def _fake_run(g, *a, **kw):
            loop_kwargs.append(kw)
            return LoopResult(
                loop_id="test-rr", project="test-proj", goal=g,
                status="done", stuck_reason=None,
                steps=[StepOutcome(index=0, text="step", status="done",
                                   result="output", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        closure = ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                 summary="verified", checks_run=1,
                                 checks_passed=1)
        from handle import handle
        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=closure), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            handle(goal, force_lane="agenda", dry_run=False)

        assert loop_kwargs, "run_agent_loop was not invoked"
        extra = loop_kwargs[0].get("ancestry_context_extra", "")
        assert "Re-run notice" in extra
        assert "ACHIEVED" in extra

    def test_first_attempt_injects_nothing(self, monkeypatch, tmp_path):
        goal = "a brand new goal nobody has tried"

        import llm
        adapter = MagicMock()
        adapter.model_key = "cheap"
        monkeypatch.setattr(llm, "build_adapter", lambda *a, **kw: adapter)

        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        loop_kwargs = []

        def _fake_run(g, *a, **kw):
            loop_kwargs.append(kw)
            return LoopResult(
                loop_id="test-rr2", project="test-proj", goal=g,
                status="done", stuck_reason=None,
                steps=[StepOutcome(index=0, text="step", status="done",
                                   result="output", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        closure = ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                 summary="verified", checks_run=1,
                                 checks_passed=1)
        from handle import handle
        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=closure), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            handle(goal, force_lane="agenda", dry_run=False)

        assert loop_kwargs, "run_agent_loop was not invoked"
        # The handle's own intake row was written before assembly — it must
        # be excluded, so a first attempt sees no re-run notice.
        extra = loop_kwargs[0].get("ancestry_context_extra", "")
        assert "Re-run notice" not in extra
