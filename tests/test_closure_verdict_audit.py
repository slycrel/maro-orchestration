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


def _run(monkeypatch, tmp_path, adapter, *, all_pass=True, audit_on=True,
         **kwargs):
    def fake_run(cmd, **kw):
        proc = MagicMock()
        proc.returncode = 0 if all_pass else 1
        proc.stdout = "ok\n"
        proc.stderr = ""
        return proc

    monkeypatch.setattr("subprocess.run", fake_run)
    # Config default is OFF (fresh-installs-conservative); the box opts in
    # via workspace config, tests opt in here.
    monkeypatch.setattr(closure_verify, "_verdict_audit_enabled",
                        lambda: audit_on)
    return closure_verify.verify_goal_completion(
        goal="evaluate the taxonomy against maro and write the crosscheck",
        steps=[{"result": "wrote artifacts/crosscheck.md and closure-note.md"}],
        adapter=adapter,
        workspace_path=str(tmp_path),
        **kwargs,
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
        adapter = _adapter(PLAN, ACHIEVED)
        v = _run(monkeypatch, tmp_path, adapter, audit_on=False)
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


class TestReviewRegressions:
    """Regression tests for the 2026-08-09 adversarial review findings."""

    def test_retry_crash_preserves_original_verdict(
            self, monkeypatch, tmp_path):
        """Review HIGH (unanimous): a retry adapter failure used to escape to
        the function-wide handler and return the null 'verification did not
        run' verdict — erasing the real negative and its checks."""
        adapter = MagicMock()
        adapter.complete.side_effect = [
            _resp(PLAN), _resp(JUDGE_FALSE), _resp(AUDIT_DISAGREES),
            RuntimeError("backend down")]
        v = _run(monkeypatch, tmp_path, adapter)
        assert v.complete is False
        assert v.checks_run == 1            # the real verdict survived
        assert v.judged is True
        assert "backend down" in v.verdict_audit["retry_failed"]
        assert v.verdict_audit["disputed"] is True

    def test_retry_string_false_is_not_a_flip(self, monkeypatch, tmp_path):
        """Review HIGH: `"complete": "false"` is truthy — only an exact JSON
        boolean true may overturn."""
        stringy = {"complete": "false", "confidence": 0.0,
                   "gaps": [], "summary": "nope"}
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, JUDGE_FALSE, AUDIT_DISAGREES, stringy))
        assert v.complete is False
        assert v.verdict_audit["disputed"] is True
        assert "overturned" not in v.verdict_audit

    def test_nonbool_agrees_is_inert(self, monkeypatch, tmp_path):
        """Review HIGH: `"agrees": 0` coerced to disagreement via bool()."""
        adapter = _adapter(
            PLAN, ACHIEVED,
            {"agrees": 0, "reason": "typed wrong", "confidence": 0.9})
        v = _run(monkeypatch, tmp_path, adapter)
        assert v.complete is False          # downgrade stands
        assert v.verdict_audit.get("parse_failed") is True
        assert adapter.complete.call_count == 3   # no retry either

    def test_low_confidence_disagreement_is_inert(
            self, monkeypatch, tmp_path):
        """Review: an auditor announcing near-zero confidence in its own
        disagreement must not overturn anything."""
        timid = {"agrees": False, "reason": "not sure", "confidence": 0.2}
        v = _run(monkeypatch, tmp_path, _adapter(PLAN, ACHIEVED, timid))
        assert v.complete is False          # downgrade stands
        assert "overturned" not in v.verdict_audit

    def test_signal3_downgrade_is_never_cancelled(self, monkeypatch, tmp_path):
        """Review HIGH (unanimous): for a declared runtime deliverable with
        no behavioral probe, the missing probe IS the finding — the auditor's
        'nothing failed' doctrine is its blind spot, not a rebuttal."""
        from scope import Deliverable, ResolvedIntent
        ri = ResolvedIntent()
        ri.deliverables = [Deliverable(
            name="cmd/server/main.go", description="HTTP server binary",
            shape="runtime")]
        clean = {"complete": True, "confidence": 0.9, "gaps": [],
                 "summary": "Achieved. Binary present."}
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, clean, AUDIT_DISAGREES),
                 resolved_intent=ri)
        assert v.complete is False          # Signal 3 downgrade stands
        assert "runtime" in v.downgrade_reason
        assert v.verdict_audit["disputed"] is True
        assert "overturned" not in v.verdict_audit

    def test_retry_flip_goes_through_deterministic_guards(
            self, monkeypatch, tmp_path):
        """Review HIGH (unanimous): a retry that flips to achieved while
        ADMITTING runtime wasn't exercised must be re-downgraded — the
        detectors originally ran while complete=False and stood down."""
        confessing_retry = {
            "complete": True, "confidence": 0.9, "gaps": [],
            "summary": ("Goal achieved structurally, though runtime "
                        "validation was not performed.")}
        v = _run(monkeypatch, tmp_path,
                 _adapter(PLAN, JUDGE_FALSE, AUDIT_DISAGREES,
                          confessing_retry))
        assert v.complete is False
        assert v.verdict_audit["overturned"] == "judge-retry"
        assert v.verdict_audit["retry_redowngraded"] is True
        assert "not exercised" in v.downgrade_reason

    def test_evidence_outside_workspace_is_not_quoted(
            self, monkeypatch, tmp_path):
        """Review MEDIUM: the audit lane quotes workspace files only."""
        outside = tmp_path / "secret.txt"
        outside.write_text("s3cret-t0ken")
        ws = tmp_path / "ws"
        (ws / "artifacts").mkdir(parents=True)
        (ws / "artifacts" / "crosscheck.md").write_text("tally ok")
        plan = {"checks": [
            {"description": "leak", "command": f"cat {outside}"},
            {"description": "real", "command": "cat artifacts/crosscheck.md"},
        ]}
        def fake_run(cmd, **kw):
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "ok\n"
            proc.stderr = ""
            return proc
        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr(closure_verify, "_verdict_audit_enabled",
                            lambda: True)
        adapter = _adapter(plan, ACHIEVED, AUDIT_DISAGREES)
        closure_verify.verify_goal_completion(
            goal="write the crosscheck",
            steps=[{"result": "wrote artifacts/crosscheck.md"}],
            adapter=adapter,
            workspace_path=str(ws),
        )
        audit_msg = adapter.complete.call_args_list[2].args[0][1].content
        assert "s3cret-t0ken" not in audit_msg
        assert "tally ok" in audit_msg

    def test_flagged_excerpt_is_withheld(self, monkeypatch, tmp_path):
        """Review HIGH: worker-authored artifact text is scanned before it
        reaches the auditor; a flagged excerpt is visibly withheld."""
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "crosscheck.md").write_text("tally ok")
        _finding = MagicMock()
        _finding.findings = ["instruction-to-judge pattern"]
        import injection_guard
        monkeypatch.setattr(injection_guard, "scan_content",
                            lambda text: _finding)
        adapter = _adapter(PLAN, ACHIEVED, AUDIT_DISAGREES)
        _run(monkeypatch, tmp_path, adapter)
        audit_msg = adapter.complete.call_args_list[2].args[0][1].content
        assert "tally ok" not in audit_msg
        assert "withheld" in audit_msg


# ---------------------------------------------------------------------------
# Pass-side audit (MH #1 Specification Gaming v1, 2026-08-10): all-static
# ACHIEVED verdicts get one adversarial refutation call. Detection degrades
# trust (confidence cap below the 0.7 learning floor); it never flips the
# verdict.
# ---------------------------------------------------------------------------

# Clean achieved payload: no admission phrases ("not executed" in ACHIEVED
# triggers the Signal-1 behavioral-gap downgrade, which routes through the
# NEGATIVE audit lane — not the shape under test here).
PASS_ACHIEVED = {"complete": True, "confidence": 0.9, "gaps": [],
                 "summary": ("Achieved. The crosscheck document exists and "
                             "covers the taxonomy comparison.")}

PASS_AUDIT_REFUTES = {"agrees": False,
                      "reason": "the artifact asserts success but demonstrates nothing",
                      "confidence": 0.9}
PASS_AUDIT_AGREES = {"agrees": True,
                     "reason": "excerpts substantively satisfy the goal",
                     "confidence": 0.85}


def _run_pass(monkeypatch, tmp_path, adapter, *, pass_audit_on=True, **kwargs):
    monkeypatch.setattr(closure_verify, "_pass_audit_enabled",
                        lambda: pass_audit_on)
    # Negative-audit lane off so payload order stays deterministic.
    return _run(monkeypatch, tmp_path, adapter, audit_on=False, **kwargs)


class TestPassAudit:
    def test_refuted_all_static_pass_caps_confidence_not_verdict(
            self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, PASS_ACHIEVED, PASS_AUDIT_REFUTES)
        v = _run_pass(monkeypatch, tmp_path, adapter)
        assert v.complete is True  # never flips
        assert v.confidence == pytest.approx(0.6)  # below the 0.7 floor
        assert v.verdict_audit["pass_audit"] is True
        assert v.verdict_audit["refuted"] is True
        assert v.verdict_audit["mh_edge"] == "model-grader"
        assert v.verdict_audit["mh_class"] == "specification_gaming_candidate"
        assert "Pass-audit refutation" in v.summary

    def test_agreeing_audit_leaves_verdict_untouched(self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, PASS_ACHIEVED, PASS_AUDIT_AGREES)
        v = _run_pass(monkeypatch, tmp_path, adapter)
        assert v.complete is True
        assert v.confidence == pytest.approx(0.9)
        assert v.verdict_audit["pass_audit"] is True
        assert "refuted" not in v.verdict_audit

    def test_killswitch_off_no_audit_call(self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, PASS_ACHIEVED)
        v = _run_pass(monkeypatch, tmp_path, adapter, pass_audit_on=False)
        assert adapter.complete.call_count == 2
        assert v.verdict_audit == {}

    def test_low_confidence_refutation_does_not_act(self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, PASS_ACHIEVED,
                           {**PASS_AUDIT_REFUTES, "confidence": 0.4})
        v = _run_pass(monkeypatch, tmp_path, adapter)
        assert v.confidence == pytest.approx(0.9)
        assert v.verdict_audit.get("refuted") is None
        # The audit record still persists for analysis.
        assert v.verdict_audit["pass_audit"] is True

    def test_auditor_sees_declared_purpose_beside_command(
            self, monkeypatch, tmp_path):
        # MH #4 instruction—grader mismatch (2026-08-11): a check that
        # verifies something other than what it claims is only visible if
        # the auditor sees the DECLARED purpose next to the command — a
        # bare command line hides the binding.
        adapter = _adapter(PLAN, PASS_ACHIEVED, PASS_AUDIT_AGREES)
        _run_pass(monkeypatch, tmp_path, adapter)
        audit_msgs = adapter.complete.call_args_list[-1][0][0]
        audit_user = audit_msgs[-1].content
        assert "crosscheck exists" in audit_user  # the declared purpose
        assert "cmd: cat artifacts/crosscheck.md" in audit_user

    def test_behavioral_check_present_skips_pass_audit(self, monkeypatch, tmp_path):
        plan = {"checks": [
            {"description": "runs", "command": "python3 thing.py && grep ok out.txt"},
        ]}
        adapter = _adapter(plan, PASS_ACHIEVED)
        v = _run_pass(monkeypatch, tmp_path, adapter)
        assert adapter.complete.call_count == 2  # no third (audit) call
        assert v.verdict_audit == {}

    def test_negative_verdict_never_pass_audited(self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, JUDGE_FALSE)
        v = _run_pass(monkeypatch, tmp_path, adapter)
        assert adapter.complete.call_count == 2
        assert v.verdict_audit.get("pass_audit") is None

    def test_non_bool_agrees_is_non_actionable(self, monkeypatch, tmp_path):
        adapter = _adapter(PLAN, PASS_ACHIEVED,
                           {"agrees": "false", "reason": "x", "confidence": 0.9})
        v = _run_pass(monkeypatch, tmp_path, adapter)
        assert v.confidence == pytest.approx(0.9)
        assert v.verdict_audit.get("refuted") is None
        assert v.verdict_audit.get("parse_failed") is True
