"""Tests for reanchor.py — §9.5 mid-meander coherence re-anchor."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

from reanchor import (
    ReanchorVerdict,
    check_reanchor,
    read_committed_interpretation,
    run_milestone_reanchor,
)


def _adapter_returning(payload: dict):
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = json.dumps(payload)
    adapter.complete.return_value = resp
    return adapter


def _outcome(status="done", result="did the thing", text="step"):
    return SimpleNamespace(status=status, result=result, text=text)


# ---------------------------------------------------------------------------
# read_committed_interpretation
# ---------------------------------------------------------------------------

class TestReadCommittedInterpretation:
    def test_extracts_binding_section_bullets(self, tmp_path):
        (tmp_path / "resolved_intent.md").write_text(
            "# Resolved intent\n\n"
            "## Resolved interpretation (binding goal definition)\n"
            "- count only tracked markdown files\n"
            "- Rationale: the repo is the unit the user named\n\n"
            "## Deliverables\n"
            "- report.md\n",
            encoding="utf-8",
        )
        got = read_committed_interpretation(tmp_path)
        assert "count only tracked markdown files" in got
        assert "Rationale" in got
        # The next section's bullets must NOT leak in.
        assert "report.md" not in got

    def test_missing_file_returns_empty(self, tmp_path):
        assert read_committed_interpretation(tmp_path) == ""

    def test_missing_section_returns_empty(self, tmp_path):
        (tmp_path / "resolved_intent.md").write_text(
            "## Deliverables\n- report.md\n", encoding="utf-8")
        assert read_committed_interpretation(tmp_path) == ""

    def test_none_dir_returns_empty(self):
        assert read_committed_interpretation(None) == ""


# ---------------------------------------------------------------------------
# check_reanchor
# ---------------------------------------------------------------------------

class TestCheckReanchor:
    def test_on_course_parsed(self):
        adapter = _adapter_returning(
            {"on_course": True, "drift_summary": "", "anchor_note": ""})
        v = check_reanchor("goal", "the commitment", "work", "milestone", [], adapter)
        assert v.on_course is True
        assert v.drift_summary == ""
        assert v.error == ""

    def test_drift_parsed(self):
        adapter = _adapter_returning({
            "on_course": False,
            "drift_summary": "polishing the CLI instead of the report",
            "anchor_note": "the commitment is the report; the CLI is out of scope",
        })
        v = check_reanchor("goal", "commit", "work", "milestone", ["s1"], adapter)
        assert v.on_course is False
        assert "polishing" in v.drift_summary
        assert "commitment" in v.anchor_note

    def test_no_adapter_is_on_course_with_error(self):
        v = check_reanchor("goal", "", "", "milestone", [], None)
        assert v.on_course is True
        assert v.error == "no adapter"

    def test_call_failure_is_on_course_with_error(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("boom")
        v = check_reanchor("goal", "", "", "milestone", [], adapter)
        assert v.on_course is True
        assert "boom" in v.error

    def test_garbage_answer_is_on_course_with_error(self):
        adapter = MagicMock()
        resp = MagicMock()
        resp.content = "definitely not json"
        adapter.complete.return_value = resp
        v = check_reanchor("goal", "", "", "milestone", [], adapter)
        assert v.on_course is True
        assert v.error == "unparseable answer"

    def test_commitment_in_prompt_when_present(self):
        adapter = _adapter_returning({"on_course": True})
        check_reanchor("goal", "THE BINDING READING", "work", "m", [], adapter)
        sent = adapter.complete.call_args[0][0]
        user_msg = sent[1].content
        assert "THE BINDING READING" in user_msg

    def test_goal_is_commitment_when_no_interpretation(self):
        adapter = _adapter_returning({"on_course": True})
        check_reanchor("goal", "", "work", "m", [], adapter)
        user_msg = adapter.complete.call_args[0][0][1].content
        assert "no binding interpretation was recorded" in user_msg


# ---------------------------------------------------------------------------
# run_milestone_reanchor (the full seam)
# ---------------------------------------------------------------------------

class TestRunMilestoneReanchor:
    def _run_dir(self, tmp_path):
        rd = tmp_path / "run-x"
        (rd / "build").mkdir(parents=True)
        return rd

    def test_on_course_returns_empty_and_records(self, tmp_path):
        rd = self._run_dir(tmp_path)
        adapter = _adapter_returning({"on_course": True})
        with patch("runs.current_run_dir", return_value=rd), \
             patch("runs.source_dir", return_value=None):
            note = run_milestone_reanchor(
                goal="g", milestone_step="big step",
                step_outcomes=[_outcome()], remaining_steps=["next"],
                adapter=adapter, loop_id="lid123", step_idx=2,
            )
        assert note == ""
        rows = [json.loads(l) for l in
                (rd / "build" / "reanchor.jsonl").read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["on_course"] is True
        assert rows[0]["step_idx"] == 2
        assert rows[0]["anchor_source"] == "goal"

    def test_drift_returns_anchor_note_and_records(self, tmp_path):
        rd = self._run_dir(tmp_path)
        adapter = _adapter_returning({
            "on_course": False,
            "drift_summary": "went sideways",
            "anchor_note": "return to the ask",
        })
        with patch("runs.current_run_dir", return_value=rd), \
             patch("runs.source_dir", return_value=None):
            note = run_milestone_reanchor(
                goal="g", milestone_step="big step",
                step_outcomes=[], remaining_steps=[],
                adapter=adapter, loop_id="lid123", step_idx=3,
            )
        assert note == "return to the ask"
        rows = [json.loads(l) for l in
                (rd / "build" / "reanchor.jsonl").read_text().splitlines()]
        assert rows[0]["on_course"] is False
        assert rows[0]["drift_summary"] == "went sideways"

    def test_interpretation_read_from_source_dir(self, tmp_path):
        rd = self._run_dir(tmp_path)
        src = tmp_path / "source"
        src.mkdir()
        (src / "resolved_intent.md").write_text(
            "## Resolved interpretation (binding goal definition)\n"
            "- the strict reading\n", encoding="utf-8")
        adapter = _adapter_returning({"on_course": True})
        with patch("runs.current_run_dir", return_value=rd), \
             patch("runs.source_dir", return_value=src):
            run_milestone_reanchor(
                goal="g", milestone_step="m", step_outcomes=[],
                remaining_steps=[], adapter=adapter, loop_id="l", step_idx=1,
            )
        user_msg = adapter.complete.call_args[0][0][1].content
        assert "the strict reading" in user_msg
        row = json.loads(
            (rd / "build" / "reanchor.jsonl").read_text().splitlines()[0])
        assert row["anchor_source"] == "interpretation"

    def test_no_run_dir_still_returns_note(self, tmp_path):
        """Recording is best-effort; the anchor note must survive a missing
        run-dir context (the corrective matters more than the record)."""
        adapter = _adapter_returning({
            "on_course": False, "drift_summary": "d", "anchor_note": "fix it"})
        with patch("runs.current_run_dir", return_value=None), \
             patch("runs.source_dir", return_value=None):
            note = run_milestone_reanchor(
                goal="g", milestone_step="m", step_outcomes=[],
                remaining_steps=[], adapter=adapter, loop_id="l", step_idx=1,
            )
        assert note == "fix it"

    def test_check_error_never_raises_and_is_recorded(self, tmp_path):
        rd = self._run_dir(tmp_path)
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("dead key")
        with patch("runs.current_run_dir", return_value=rd), \
             patch("runs.source_dir", return_value=None):
            note = run_milestone_reanchor(
                goal="g", milestone_step="m", step_outcomes=[],
                remaining_steps=[], adapter=adapter, loop_id="l", step_idx=1,
            )
        assert note == ""
        row = json.loads(
            (rd / "build" / "reanchor.jsonl").read_text().splitlines()[0])
        assert row["on_course"] is True
        assert "dead key" in row["error"]

    def test_recent_work_keeps_all_statuses_tagged(self, tmp_path):
        """Round-1 review (Architect): a blocked stretch is often exactly
        where drift lives — the summary keeps every status, tagged."""
        rd = self._run_dir(tmp_path)
        adapter = _adapter_returning({"on_course": True})
        outcomes = [
            _outcome(result="oldest — outside the window"),
            _outcome(result="first"),
            _outcome(status="blocked", result="hit the wall"),
            _outcome(result="second"),
        ]
        with patch("runs.current_run_dir", return_value=rd), \
             patch("runs.source_dir", return_value=None):
            run_milestone_reanchor(
                goal="g", milestone_step="m", step_outcomes=outcomes,
                remaining_steps=[], adapter=adapter, loop_id="l", step_idx=1,
            )
        user_msg = adapter.complete.call_args[0][0][1].content
        assert "[done] first" in user_msg
        assert "[blocked] hit the wall" in user_msg
        assert "[done] second" in user_msg
        assert "oldest" not in user_msg

    def test_upcoming_steps_truncation_is_marked(self):
        """Round-1 review (Minimalist): >5 upcoming steps get an explicit
        marker, never a silent cap."""
        adapter = _adapter_returning({"on_course": True})
        check_reanchor("g", "", "", "m",
                       [f"step {i}" for i in range(8)], adapter)
        user_msg = adapter.complete.call_args[0][0][1].content
        assert "... (3 more)" in user_msg
        assert "step 4" in user_msg and "step 5" not in user_msg
