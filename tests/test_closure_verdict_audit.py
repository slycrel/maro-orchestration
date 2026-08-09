"""The verdict-audit pass in closure_verify (2026-08-09).

Specimens: run 18773dfa (Signal 1 demoted a research-only run for honestly
noting an OPTIONAL follow-up was "not executed" — 5/5 checks passed, no
contest lane existed for a closure-internal downgrade) and the 2738d9c0
class (judge-asserted False resting on narration alone — f7b775c caps its
standing but nothing could FIX the verdict).

The audit gives one second-opinion call the artifact evidence and asks
whether the evidence supports not-achieved. Disagreement cancels a pending
deterministic downgrade; a judge-asserted False gets one retry with the
objection attached; a retry that maintains False stands, stamped disputed
so handle.py routes the loop into the contested learning holdout.
"""
import json
from unittest.mock import MagicMock

import pytest

import closure_verify


def _resp(payload):
    resp = MagicMock()
    resp.content = json.dumps(payload)
    resp.input_tokens = 1
    resp.output_tokens = 1
    return resp


def _adapter(*payloads):
    """Adapter returning the given JSON payloads in call order."""
    adapter = MagicMock()
    adapter.complete.side_effect = [_resp(p) for p in payloads]
    return adapter


PLAN = {"checks": [
    {"description": "crosscheck exists", "command": "cat artifacts/crosscheck.md"},
]}

ACHIEVED = {"complete": True, "confidence": 0.9, "gaps": [],
            # "not executed" refers to an OPTIONAL follow-up — the 18773dfa shape.
            "summary": ("Achieved. The recommended port was not executed, "
                        "correctly, per the research-only constraint.")}

JUDGE_FALSE = {"complete": False, "confidence": 0.8,
               "gaps": ["fields appear to hold rationale text"],
               "summary": "Goal not achieved. Fields hold rationale text."}

AUDIT_AGREES = {"agrees": True, "reason": "a check contradicts the goal",
                "confidence": 0.8}
AUDIT_DISAGREES = {"agrees": False,
                   "reason": "all checks passed and the artifacts match the goal",
                   "confidence": 0.9}
RETRY_TRUE = {"complete": True, "confidence": 0.85, "gaps": [],
              "summary": "Goal achieved; the audit objection holds."}


def _run(monkeypatch, tmp_path, adapter, *, all_pass=True):
    def fake_run(cmd, **kwargs):
        proc = MagicMock()
        proc.returncode = 0 if all_pass else 1
        proc.stdout = "ok\n"
        proc.stderr = ""
        return proc

    monkeypatch.setattr("subprocess.run", fake_run)
    return closure_verify.verify_goal_completion(
        goal="evaluate the taxonomy against maro and write the crosscheck",
        steps=[{"result": "wrote artifacts/crosscheck.md and closure-note.md"}],
        adapter=adapter,
        workspace_path=str(tmp_path),
    )


class TestDowngradeCancelled:
    def test_audit_disagreement_cancels_deterministic_downgrade(
            self, monkeypatch, tmp_path):
        """The 18773dfa shape: achieved verdict, Signal 1 pending, auditor
        holding the artifact evidence sides with the judge."""
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, ACHIEVED, AUDIT_DISAGREES))
        assert v.complete is True
        assert v.downgrade_reason == ""
        assert "Downgraded" not in v.summary
        assert v.verdict_audit["overturned"] == "downgrade-cancelled"
        assert v.verdict_audit["cancelled_reasons"]
        assert "not executed" in v.verdict_audit["cancelled_reasons"][0]

    def test_audit_agreement_lets_downgrade_stand(self, monkeypatch, tmp_path):
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, ACHIEVED, AUDIT_AGREES))
        assert v.complete is False
        assert v.downgrade_reason
        assert v.summary.startswith("Downgraded to not-achieved")
        assert v.verdict_audit.get("agrees") is True
        assert "overturned" not in v.verdict_audit
        assert "disputed" not in v.verdict_audit


class TestJudgeRetry:
    def test_retry_overturns_judge_false(self, monkeypatch, tmp_path):
        """The 2738d9c0 shape, fixed rather than merely defanged: the judge
        said False on narration, the auditor disagrees, the retry (with the
        objection attached) comes back True."""
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, JUDGE_FALSE, AUDIT_DISAGREES, RETRY_TRUE))
        assert v.complete is True
        assert v.confidence == pytest.approx(0.85)
        assert v.verdict_audit["overturned"] == "judge-retry"
        assert "disputed" not in v.verdict_audit

    def test_retry_maintaining_false_stands_stamped_disputed(
            self, monkeypatch, tmp_path):
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, JUDGE_FALSE, AUDIT_DISAGREES, JUDGE_FALSE))
        assert v.complete is False
        assert v.verdict_audit["disputed"] is True
        assert "overturned" not in v.verdict_audit
        # The judge's reasoning is preserved, not suppressed.
        assert any("rationale" in g for g in v.gaps)


class TestAuditDoesNotRun:
    def test_killswitch_off_skips_audit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(closure_verify, "_verdict_audit_enabled",
                            lambda: False)
        adapter = _adapter(PLAN, ACHIEVED)
        v = _run(monkeypatch, tmp_path, adapter)
        assert v.complete is False          # Signal 1 downgrade stands
        assert v.verdict_audit == {}
        assert adapter.complete.call_count == 2   # plan + verdict only

    def test_clean_check_failure_skips_audit(self, monkeypatch, tmp_path):
        """A negative verdict backed by a cleanly-failed check is real
        evidence — it may demote unaudited."""
        adapter = _adapter(PLAN, JUDGE_FALSE)
        v = _run(monkeypatch, tmp_path, adapter, all_pass=False)
        assert v.complete is False
        assert v.verdict_audit == {}
        assert adapter.complete.call_count == 2

    def test_positive_verdict_skips_audit(self, monkeypatch, tmp_path):
        clean = {"complete": True, "confidence": 0.9, "gaps": [],
                 "summary": "Achieved. All artifacts verified."}
        adapter = _adapter(PLAN, clean)
        v = _run(monkeypatch, tmp_path, adapter)
        assert v.complete is True
        assert v.verdict_audit == {}
        assert adapter.complete.call_count == 2

    def test_audit_crash_never_blocks_closure(self, monkeypatch, tmp_path):
        """Non-fatal contract: an adapter error inside the audit leaves the
        verdict exactly as the audit-off path would."""
        adapter = MagicMock()
        adapter.complete.side_effect = [
            _resp(PLAN), _resp(ACHIEVED), RuntimeError("backend down")]
        v = _run(monkeypatch, tmp_path, adapter)
        assert v.complete is False          # downgrade stands
        assert v.verdict_audit == {}


class TestAuditEvidence:
    def test_audit_prompt_carries_check_outcomes_and_file_evidence(
            self, monkeypatch, tmp_path):
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "crosscheck.md").write_text(
            "tally reconciles: 7/12/22/41")
        adapter = _adapter(PLAN, ACHIEVED, AUDIT_DISAGREES)
        _run(monkeypatch, tmp_path, adapter)
        audit_call = adapter.complete.call_args_list[2]
        user_msg = audit_call.args[0][1].content
        assert "NOT-ACHIEVED" in user_msg
        assert "cat artifacts/crosscheck.md" in user_msg
        assert "tally reconciles" in user_msg          # real file content
        assert "not executed" in user_msg              # the downgrade reason

    def test_retry_prompt_is_same_question_plus_objection(
            self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, JUDGE_FALSE, AUDIT_DISAGREES, RETRY_TRUE)
        _run(monkeypatch, tmp_path, adapter)
        verdict_msg = adapter.complete.call_args_list[1].args[0][1].content
        retry_msg = adapter.complete.call_args_list[3].args[0][1].content
        assert retry_msg.startswith(verdict_msg)
        assert "independent audit" in retry_msg
        assert AUDIT_DISAGREES["reason"] in retry_msg
