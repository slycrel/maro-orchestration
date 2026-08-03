"""The ungrounded-False confidence cap in closure_verify.

Specimen: run 2738d9c0 (2026-08-02, LT-1 #4). Every closure check passed.
The judge quoted the artifact's OPTIONAL `notes` array and attributed that
prose to the scalar fields the notes merely mention -- it never saw
`"cutover_date": "2026-10-15"` -- then stamped complete=False at 0.80.
That cleared VERDICT_CONFIDENCE_FLOOR, so a fully correct run was demoted
to incomplete AND counted at FULL trust in learning.

A confident False requires evidence of failure. All-checks-passed with no
file content in evidence is not evidence of failure.
"""
import json
from unittest.mock import MagicMock

import pytest

import closure_verify
from closure_verify import (
    _UNGROUNDED_FALSE_CONFIDENCE,
    _UNGROUNDED_FALSE_FLOOR,
)


def _adapter(plan_checks, verdict):
    """Adapter returning a closure plan then a closure verdict."""
    adapter = MagicMock()
    responses = []
    for payload in ({"checks": plan_checks}, verdict):
        resp = MagicMock()
        resp.content = json.dumps(payload)
        resp.input_tokens = 1
        resp.output_tokens = 1
        responses.append(resp)
    adapter.complete.side_effect = responses
    return adapter


def _run(monkeypatch, tmp_path, verdict, *, all_pass=True, file_content=None):
    """Drive verify_goal_completion with a stubbed check runner."""
    def fake_run(cmd, **kwargs):
        proc = MagicMock()
        proc.returncode = 0 if all_pass else 1
        proc.stdout = "SCHEMA OK\n"
        proc.stderr = ""
        return proc

    # closure_verify imports subprocess inside the function, so patch the
    # module itself rather than an attribute that does not exist on it.
    monkeypatch.setattr("subprocess.run", fake_run)
    if file_content is not None:
        monkeypatch.setattr(closure_verify, "_failed_check_file_evidence",
                            lambda cmd, cwd: file_content)

    return closure_verify.verify_goal_completion(
        goal="extract fields into extracted.json",
        steps=[{"result": "wrote extracted.json; notes explain each field"}],
        adapter=_adapter(
            [{"description": "validator passes", "command": "python3 validate.py x.json"}],
            verdict,
        ),
        workspace_path=str(tmp_path),
    )


UNGROUNDED = {
    "complete": False,
    "confidence": 0.8,
    "gaps": ["extracted.json's cutover_date and api_version appear to hold "
             "long explanatory sentences rather than the extracted values"],
    "summary": "Goal not achieved. Fields hold rationale text.",
}


class TestCapApplies:
    def test_confident_false_with_all_checks_passing_is_capped(self, monkeypatch, tmp_path):
        """The 2738d9c0 shape, exactly."""
        v = _run(monkeypatch, tmp_path, UNGROUNDED)
        assert v.complete is False                      # verdict preserved
        assert v.confidence == _UNGROUNDED_FALSE_CONFIDENCE
        assert v.confidence < _UNGROUNDED_FALSE_FLOOR

    def test_the_gap_is_not_hidden(self, monkeypatch, tmp_path):
        """Capping standing is not suppression -- the judge's reasoning
        survives verbatim so a human can still read it."""
        v = _run(monkeypatch, tmp_path, UNGROUNDED)
        assert any("cutover_date" in g for g in v.gaps)
        # (the verdict prefix is normalized downstream to "Not achieved:",
        # so assert on the substance the judge wrote, not its opening words)
        assert "rationale text" in v.summary

    def test_summary_says_why_it_was_capped(self, monkeypatch, tmp_path):
        v = _run(monkeypatch, tmp_path, UNGROUNDED)
        assert "capped" in v.summary
        assert "narration" in v.summary

    def test_capped_verdict_is_directional_not_full_trust(self, monkeypatch, tmp_path):
        """The consumer-side point of the whole fix: below the floor,
        verdict_trust must refuse to let this gate learning."""
        from memory_ledger import (VERDICT_TRUST_DIRECTIONAL,
                                   VERDICT_CONFIDENCE_FLOOR, verdict_trust)
        v = _run(monkeypatch, tmp_path, UNGROUNDED)
        row = {"goal_verdict_source": "closure", "goal_achieved": False,
               "goal_verdict_confidence": v.confidence}
        assert verdict_trust(row) == VERDICT_TRUST_DIRECTIONAL
        # and the uncapped original would NOT have been
        row["goal_verdict_confidence"] = 0.8
        assert verdict_trust(row) != VERDICT_TRUST_DIRECTIONAL
        assert _UNGROUNDED_FALSE_FLOOR == VERDICT_CONFIDENCE_FLOOR


class TestCapDoesNotApply:
    def test_complete_true_untouched(self, monkeypatch, tmp_path):
        v = _run(monkeypatch, tmp_path,
                 {"complete": True, "confidence": 0.9, "gaps": [],
                  "summary": "Goal achieved."})
        assert v.complete is True
        assert v.confidence == pytest.approx(0.9)

    def test_false_with_a_failing_check_keeps_its_standing(self, monkeypatch, tmp_path):
        """A False backed by a probe that actually failed is grounded."""
        v = _run(monkeypatch, tmp_path,
                 {"complete": False, "confidence": 0.9,
                  "gaps": ["validator exits 1"], "summary": "Goal not achieved."},
                 all_pass=False)
        assert v.complete is False
        assert v.confidence == pytest.approx(0.9)

    def test_false_with_file_content_in_evidence_keeps_its_standing(
            self, monkeypatch, tmp_path):
        """target_file_content means the judge SAW the file. That is
        exactly the evidence whose absence the cap is about."""
        v = _run(monkeypatch, tmp_path,
                 {"complete": False, "confidence": 0.9,
                  "gaps": ["the file is empty"], "summary": "Goal not achieved."},
                 all_pass=False, file_content="<<actual file bytes>>")
        assert v.confidence == pytest.approx(0.9)

    def test_already_low_confidence_is_not_raised(self, monkeypatch, tmp_path):
        """The cap only ever lowers -- it must never manufacture confidence."""
        v = _run(monkeypatch, tmp_path,
                 {"complete": False, "confidence": 0.2, "gaps": ["thin"],
                  "summary": "Goal not achieved."})
        assert v.confidence == pytest.approx(0.2)


class TestPromptContract:
    def test_verdict_prompt_forbids_asserting_unseen_file_content(self):
        text = " ".join(closure_verify._CLOSURE_VERDICT_SYSTEM.split())
        assert "only assert what a file CONTAINS when that content is in front of you" in text
        assert "those quotations are not the file" in text
