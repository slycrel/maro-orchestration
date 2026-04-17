"""Tests for factory_thin adversarial-review grounding.

Mirrors the probe-grounding test surface in test_quality_gate.py but for
the path factory_thin takes — reviewer emits JSON with settled_by_command,
_ground_adversarial_findings calls _probe_contested_claims, then re-renders
a text block for the compile step.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from factory_thin import _ground_adversarial_findings  # noqa: E402


class TestGroundAdversarialFindings:
    def test_empty_raw_returns_empty(self):
        assert _ground_adversarial_findings("") == ""

    def test_non_json_raw_passes_through(self):
        raw = "Some freeform prose the reviewer emitted."
        assert _ground_adversarial_findings(raw) == raw

    def test_json_without_contested_claims_passes_through(self):
        raw = '{"other_key": "value"}'
        assert _ground_adversarial_findings(raw) == raw

    def test_empty_claim_list_passes_through(self):
        raw = '{"contested_claims": []}'
        assert _ground_adversarial_findings(raw) == raw

    def test_dismissed_probe_labels_claim(self):
        # `true` always exits 0 → claim should dismiss
        raw = (
            '{"contested_claims": [{"claim": "Go not installed", '
            '"verdict": "CONTESTED", "reason": "go missing", '
            '"settled_by_command": "true"}]}'
        )
        out = _ground_adversarial_findings(raw)
        assert "[DISMISSED_BY_PROBE]" in out
        assert "settled by probe" in out

    def test_validated_probe_keeps_verdict(self):
        # `false` always exits 1 → contestation stands
        raw = (
            '{"contested_claims": [{"claim": "file missing", '
            '"verdict": "OVERCLAIMED", "reason": "not on disk", '
            '"settled_by_command": "false"}]}'
        )
        out = _ground_adversarial_findings(raw)
        assert "[OVERCLAIMED]" in out
        assert "probe confirmed contestation" in out

    def test_unprobed_claim_rendered_without_probe_suffix(self):
        raw = (
            '{"contested_claims": [{"claim": "interpretation is off", '
            '"verdict": "DOWNGRADED", "reason": "context-dependent", '
            '"settled_by_command": null}]}'
        )
        out = _ground_adversarial_findings(raw)
        assert "[DOWNGRADED]" in out
        assert "settled by probe" not in out
        assert "un-runnable" not in out

    def test_invalid_parsed_claims_returns_raw(self):
        raw = '{"contested_claims": "not a list"}'
        assert _ground_adversarial_findings(raw) == raw
