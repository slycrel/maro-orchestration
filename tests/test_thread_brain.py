"""Tests for the per-thread goal-brain artifact (src/thread_brain.py) and
its wiring into the run-dir lifecycle (runs.create_run_dir / finalize_run)
and the navigator shadow (goal_brain input)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import thread_brain
from runs import create_run_dir, finalize_run, run_dir


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    return tmp_path


class TestCreate:
    def test_create_seeds_all_sections(self, tmp_path):
        path = thread_brain.create_thread_brain(tmp_path, goal="ship the thing")
        assert path is not None
        text = path.read_text()
        assert "ship the thing" in text
        for header in ("## Intent", "## Compiled truth", "## Decisions",
                       "## Threads", "## Open questions"):
            assert header in text
        assert "thread opened" in text

    def test_goal_is_verbatim_not_paraphrased(self, tmp_path):
        goal = 'Fix the "weird" bug — don\'t touch <config>'
        thread_brain.create_thread_brain(tmp_path, goal=goal)
        assert goal in thread_brain.load_thread_brain(tmp_path)

    def test_first_call_wins(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="first")
        assert thread_brain.create_thread_brain(tmp_path, goal="second") is None
        text = thread_brain.load_thread_brain(tmp_path)
        assert "first" in text
        assert "second" not in text

    def test_origin_ancestry_recorded(self, tmp_path):
        thread_brain.create_thread_brain(
            tmp_path, goal="child goal",
            origin={"parent_handle_id": "abc123",
                    "parent_goal": "parent goal", "source": "fork"},
        )
        text = thread_brain.load_thread_brain(tmp_path)
        assert "Origin:" in text
        assert "abc123" in text
        assert "parent goal" in text

    def test_no_origin_no_origin_line(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="top-level")
        assert "Origin:" not in thread_brain.load_thread_brain(tmp_path)


class TestAppendSeams:
    def test_append_decision_dated_and_ordered(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="g")
        assert thread_brain.append_decision(tmp_path, "chose plan A")
        assert thread_brain.append_decision(tmp_path, "revised to plan B")
        text = thread_brain.load_thread_brain(tmp_path)
        assert text.index("thread opened") < text.index("chose plan A") \
            < text.index("revised to plan B")
        # Decisions stay inside their section — before the Threads header.
        assert text.index("revised to plan B") < text.index("## Threads")

    def test_compiled_truth_replaces_placeholder(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="g")
        assert thread_brain.append_compiled_truth(tmp_path, "X is verified")
        text = thread_brain.load_thread_brain(tmp_path)
        assert "X is verified" in text
        assert "(none yet)" not in text

    def test_record_child_lands_in_threads_section(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="g")
        assert thread_brain.record_child(tmp_path, "kid42", "child goal")
        text = thread_brain.load_thread_brain(tmp_path)
        threads = text[text.index("## Threads"):text.index("## Open questions")]
        assert "kid42" in threads
        assert "child goal" in threads

    def test_record_close_appends_status_and_note(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="g")
        assert thread_brain.record_close(tmp_path, status="done", note="all green")
        text = thread_brain.load_thread_brain(tmp_path)
        assert "thread closed: done — all green" in text

    def test_append_without_brain_returns_false(self, tmp_path):
        assert thread_brain.append_decision(tmp_path, "x") is False
        assert thread_brain.record_close(tmp_path, status="done") is False

    def test_human_edits_survive_appends(self, tmp_path):
        # The artifact is a plain file a human can edit; appends must not
        # eat hand-written content in other sections.
        thread_brain.create_thread_brain(tmp_path, goal="g")
        path = thread_brain.brain_path(tmp_path)
        path.write_text(path.read_text().replace(
            "## Open questions\n\n- (none)",
            "## Open questions\n\n- is the cache warm?"))
        thread_brain.append_decision(tmp_path, "noted")
        text = thread_brain.load_thread_brain(tmp_path)
        assert "is the cache warm?" in text
        assert "noted" in text


class TestLoad:
    def test_load_missing_returns_empty(self, tmp_path):
        assert thread_brain.load_thread_brain(tmp_path) == ""

    def test_load_caps_keep_head_and_tail(self, tmp_path):
        thread_brain.create_thread_brain(tmp_path, goal="HEADMARK goal")
        for i in range(300):
            thread_brain.append_decision(tmp_path, f"decision number {i}")
        text = thread_brain.load_thread_brain(tmp_path, max_chars=2000)
        assert len(text) < 2200  # cap + elision marker slack
        assert "HEADMARK" in text                 # head survives
        assert "decision number 299" in text      # newest tail survives
        assert "middle elided" in text


class TestRunDirWiring:
    def test_create_run_dir_seeds_brain(self, workspace):
        rd = create_run_dir("abcd1234", prompt="ship it")
        text = thread_brain.load_thread_brain(rd)
        assert "ship it" in text
        assert "thread opened" in text

    def test_recreate_does_not_clobber_brain(self, workspace):
        rd = create_run_dir("abcd1234", prompt="first")
        thread_brain.append_decision(rd, "accreted state")
        create_run_dir("abcd1234", prompt="second")
        text = thread_brain.load_thread_brain(rd)
        assert "accreted state" in text
        assert "second" not in text

    def test_child_registers_in_parent_threads(self, workspace):
        parent = create_run_dir("parent01", prompt="parent goal")
        create_run_dir(
            "child001", prompt="child goal",
            extra_metadata={"origin": {
                "parent_handle_id": "parent01",
                "parent_goal": "parent goal", "source": "fork"}},
        )
        parent_text = thread_brain.load_thread_brain(parent)
        threads = parent_text[parent_text.index("## Threads"):]
        assert "child001" in threads
        assert "child goal" in threads

    def test_finalize_records_close(self, workspace):
        rd = create_run_dir("abcd1234", prompt="g")
        finalize_run("abcd1234", status="completed")
        assert "thread closed: completed" in thread_brain.load_thread_brain(rd)

    def test_finalize_without_brain_still_finalizes(self, workspace):
        rd = create_run_dir("abcd1234", prompt="g")
        thread_brain.brain_path(rd).unlink()
        out = finalize_run("abcd1234", status="failed")
        assert out == rd  # close-record failure never blocks finalize


class TestNavigatorShadowWiring:
    def test_standin_prefers_real_brain(self, workspace):
        rd = create_run_dir("abcd1234", prompt="real brain goal")
        (rd / "source" / "resolved_intent.md").write_text("standin intent")
        from navigator_shadow import _goal_brain_standin
        text = _goal_brain_standin(rd)
        assert "real brain goal" in text
        assert "standin intent" not in text

    def test_standin_falls_back_for_pre_brain_runs(self, workspace):
        rd = create_run_dir("abcd1234", prompt="g")
        thread_brain.brain_path(rd).unlink()  # simulate a pre-2026-06-11 run
        (rd / "source" / "resolved_intent.md").write_text("standin intent")
        from navigator_shadow import _goal_brain_standin
        assert "standin intent" in _goal_brain_standin(rd)


class TestLoopDecisionSeam:
    """agent_loop._record_loop_decision — the per-turn maintenance seam (#5):
    live mid-loop supervisor decisions land in the active thread's Decisions."""

    def _fresh_run(self, workspace):
        import runs
        rd = create_run_dir("abcd1234", prompt="ship the thing")
        runs.set_current_run_dir(rd)
        return rd

    def test_director_decision_lands_in_decisions(self, workspace):
        import runs, agent_loop
        rd = self._fresh_run(workspace)
        try:
            assert agent_loop._record_loop_decision(
                "director", "stuck", "replan", "approach not converging") is True
            text = thread_brain.brain_path(rd).read_text()
            assert "director [stuck]: replan" in text
            assert "approach not converging" in text
            # landed under Decisions, not another section
            decisions = text.split("## Decisions", 1)[1].split("## Threads", 1)[0]
            assert "replan" in decisions
        finally:
            runs.set_current_run_dir(None)

    def test_no_active_run_dir_is_safe_noop(self, workspace):
        import runs, agent_loop
        runs.set_current_run_dir(None)
        assert agent_loop._record_loop_decision(
            "director", "stuck", "replan", "x") is False  # no crash, no write

    def test_empty_reasoning_still_records_action(self, workspace):
        import runs, agent_loop
        rd = self._fresh_run(workspace)
        try:
            assert agent_loop._record_loop_decision(
                "director", "verify_failure", "escalate") is True
            assert "director [verify_failure]: escalate" in thread_brain.brain_path(rd).read_text()
        finally:
            runs.set_current_run_dir(None)

    def test_long_reasoning_is_truncated(self, workspace):
        import runs, agent_loop
        rd = self._fresh_run(workspace)
        try:
            agent_loop._record_loop_decision(
                "director", "stuck", "adjust", "z" * 500)
            line = [l for l in thread_brain.brain_path(rd).read_text().splitlines()
                    if "adjust" in l][0]
            assert line.count("z") <= 160
        finally:
            runs.set_current_run_dir(None)

    def test_multiple_decisions_accrue_in_order(self, workspace):
        import runs, agent_loop
        rd = self._fresh_run(workspace)
        try:
            agent_loop._record_loop_decision("director", "stuck", "continue", "first")
            agent_loop._record_loop_decision("director", "step_threshold", "replan", "second")
            text = thread_brain.brain_path(rd).read_text()
            assert text.index("first") < text.index("second")
        finally:
            runs.set_current_run_dir(None)
