"""Tests for the constraint harness (Phase 35 P1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from constraint import (
    ConstraintFlag,
    ConstraintResult,
    check_step_constraints,
    register_constraint,
    CONSTRAINT_REGISTRY,
    classify_action_tier,
    hitl_policy,
    ACTION_TIER_READ,
    ACTION_TIER_WRITE,
    ACTION_TIER_DESTROY,
    ACTION_TIER_EXTERNAL,
)


# ---------------------------------------------------------------------------
# Clean steps — no flags
# ---------------------------------------------------------------------------

def test_clean_research_step():
    result = check_step_constraints("Research what ML papers say about exploration-exploitation tradeoffs")
    assert result.allowed
    assert result.risk_level == "LOW"
    assert result.flags == []


def test_clean_read_file_step():
    result = check_step_constraints("Read the findings from docs/research/backlog-triage.md")
    assert result.allowed
    assert result.flags == []


def test_clean_write_to_workspace():
    result = check_step_constraints("Write the synthesis to docs/research/output.md")
    assert result.allowed


def test_clean_summarize_step():
    result = check_step_constraints("Summarise the tweet content and extract the main claims")
    assert result.allowed


# ---------------------------------------------------------------------------
# HIGH risk — blocked
# ---------------------------------------------------------------------------

def test_high_rm_rf():
    result = check_step_constraints("Clean up by running rm -rf /tmp/old_data")
    assert result.blocked
    assert result.risk_level == "HIGH"
    assert any(f.name == "destructive_op" for f in result.flags)


def test_high_shutil_rmtree():
    result = check_step_constraints("Use shutil.rmtree to delete the old build directory")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_drop_table():
    result = check_step_constraints("Execute DROP TABLE users to clean up the database")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_secret_env():
    result = check_step_constraints("Read credentials from ~/.env to get the API key")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_ssh_key():
    result = check_step_constraints("Copy .ssh/id_rsa to the backup location")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_write_etc():
    result = check_step_constraints("Write the updated config to /etc/poe/config.yaml")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_git_push_force():
    result = check_step_constraints("Push the changes with git push --force to origin")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_eval():
    result = check_step_constraints("Run eval(user_input) to process the dynamic expression")
    assert result.blocked
    assert result.risk_level == "HIGH"


def test_high_wipe_disk():
    result = check_step_constraints("Wipe all data from the disk partition")
    assert result.blocked
    assert result.risk_level == "HIGH"


# ---------------------------------------------------------------------------
# MEDIUM risk — allowed but flagged
# ---------------------------------------------------------------------------

def test_medium_openclaw_json():
    result = check_step_constraints("Read openclaw.json to extract the Telegram token")
    assert result.allowed
    assert result.risk_level == "MEDIUM"
    assert any(f.name == "secret_access" for f in result.flags)


def test_medium_delete_from():
    result = check_step_constraints("Run DELETE FROM sessions WHERE expires < now()")
    assert result.allowed
    assert result.risk_level == "MEDIUM"


def test_medium_send_message():
    result = check_step_constraints("Send a message to the Slack channel with the findings")
    assert result.allowed
    assert result.risk_level == "MEDIUM"


def test_medium_write_to_config():
    result = check_step_constraints("Write the updated preferences to ~/.config/poe/prefs.json")
    assert result.allowed
    assert result.risk_level == "MEDIUM"


# ---------------------------------------------------------------------------
# ConstraintResult helpers
# ---------------------------------------------------------------------------

def test_reason_returns_none_for_clean():
    r = check_step_constraints("Research the topic")
    assert r.reason is None


def test_reason_returns_high_details_first():
    r = check_step_constraints("Read ~/.env and rm -rf /tmp/test")
    assert r.blocked
    # reason should include the HIGH-level detail
    assert r.reason is not None
    assert len(r.reason) > 5


def test_as_dict_shape():
    r = check_step_constraints("rm -rf /data")
    d = r.as_dict()
    assert "allowed" in d
    assert "risk_level" in d
    assert "flags" in d
    assert isinstance(d["flags"], list)


# ---------------------------------------------------------------------------
# Pluggable registry
# ---------------------------------------------------------------------------

def test_register_custom_constraint():
    original_len = len(CONSTRAINT_REGISTRY)
    triggered = []

    def my_constraint(step_text, goal):
        if "VERBOTEN" in step_text:
            triggered.append(True)
            return [ConstraintFlag(name="custom", risk="HIGH", detail="verboten found", pattern="VERBOTEN")]
        return []

    register_constraint(my_constraint)
    try:
        r = check_step_constraints("Do something VERBOTEN here")
        assert r.blocked
        assert triggered
    finally:
        # Clean up
        CONSTRAINT_REGISTRY.clear()
        for _ in range(original_len):
            pass  # registry was empty before test; nothing to restore


def test_constraint_exception_does_not_block():
    """A crashing constraint must not prevent execution."""
    def crashing(step_text, goal):
        raise RuntimeError("constraint exploded")

    CONSTRAINT_REGISTRY.append(crashing)
    try:
        r = check_step_constraints("Do some normal work")
        assert r.allowed  # crashing constraint should be silently skipped
    finally:
        CONSTRAINT_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Phase 35 P2 — HITL gating taxonomy: classify_action_tier()
# ---------------------------------------------------------------------------

def test_tier_read_default():
    assert classify_action_tier("Research the topic and summarise findings") == ACTION_TIER_READ


def test_tier_read_inspect():
    assert classify_action_tier("Inspect the log file and report anomalies") == ACTION_TIER_READ


def test_tier_write_create_file():
    assert classify_action_tier("Write to file /tmp/output.md with the summary") == ACTION_TIER_WRITE


def test_tier_write_mkdir():
    assert classify_action_tier("mkdir -p /tmp/build-artifacts") == ACTION_TIER_WRITE


def test_tier_write_commit():
    assert classify_action_tier("git commit the staged changes") == ACTION_TIER_WRITE


def test_tier_write_natural_language_passes():
    """Natural language 'write', 'save', 'create', 'update' should NOT trigger WRITE."""
    assert classify_action_tier("Write a summary of the findings") == ACTION_TIER_READ
    assert classify_action_tier("Save your analysis for later review") == ACTION_TIER_READ
    assert classify_action_tier("Create a plan for the next sprint") == ACTION_TIER_READ
    assert classify_action_tier("Update the team on progress") == ACTION_TIER_READ


def test_tier_destroy_rm():
    assert classify_action_tier("rm -rf /tmp/cache") == ACTION_TIER_DESTROY


def test_tier_destroy_delete():
    assert classify_action_tier("delete file /var/log/old.log") == ACTION_TIER_DESTROY


def test_tier_destroy_wipe():
    assert classify_action_tier("Wipe disk /dev/sda2") == ACTION_TIER_DESTROY


def test_tier_destroy_overrides_write():
    """DESTROY takes precedence over WRITE when both patterns appear."""
    assert classify_action_tier("Write and then remove package numpy") == ACTION_TIER_DESTROY


def test_tier_destroy_natural_language_passes():
    """Natural language 'remove' and 'delete' should NOT trigger DESTROY."""
    assert classify_action_tier("Remove duplicate findings from the data") == ACTION_TIER_READ
    assert classify_action_tier("Delete irrelevant entries from the results") == ACTION_TIER_READ
    assert classify_action_tier("Analyze trends and remove outliers") == ACTION_TIER_READ


def test_tier_external_curl():
    assert classify_action_tier("curl https://api.example.com/data") == ACTION_TIER_EXTERNAL


def test_tier_external_git_push():
    assert classify_action_tier("Push the changes to the remote repository with git push") == ACTION_TIER_EXTERNAL


def test_tier_external_deploy():
    assert classify_action_tier("deploy to production cluster") == ACTION_TIER_EXTERNAL


def test_tier_external_natural_language_passes():
    """Natural language 'notify', 'publish', 'submit' should NOT trigger EXTERNAL."""
    assert classify_action_tier("Notify the user of completion") == ACTION_TIER_READ
    assert classify_action_tier("Publish a final summary of findings") == ACTION_TIER_READ
    assert classify_action_tier("Submit a report") == ACTION_TIER_READ


# ---------------------------------------------------------------------------
# Phase 35 P2 — HITL gating taxonomy: hitl_policy()
# ---------------------------------------------------------------------------

def test_hitl_policy_read_step_no_gate():
    p = hitl_policy("Summarise all findings from the research file")
    assert p["tier"] == ACTION_TIER_READ
    assert p["gate"] == "none"
    assert p["allowed"] is True


def test_hitl_policy_write_step_warn():
    p = hitl_policy("write to file /workspace/poe.json with the updated config")
    assert p["tier"] == ACTION_TIER_WRITE
    assert p["gate"] == "warn"
    assert p["allowed"] is True


def test_hitl_policy_destroy_step_blocked():
    p = hitl_policy("rm -rf /var/log/old/ and clean up")
    assert p["tier"] == ACTION_TIER_DESTROY
    assert p["gate"] == "block"
    assert p["allowed"] is False


def test_hitl_policy_external_step_confirm():
    p = hitl_policy("Send the payload via curl https://api.example.com/webhook")
    assert p["tier"] == ACTION_TIER_EXTERNAL
    assert p["gate"] == "confirm"
    assert p["allowed"] is True


def test_hitl_policy_high_risk_overrides_gate():
    """HIGH constraint risk should force gate to block even for WRITE tier."""
    p = hitl_policy("Write credentials to ~/.env and update the config")
    # risk=HIGH from secret_access overrides tier gate
    assert p["gate"] == "block"
    assert p["allowed"] is False


def test_hitl_policy_returns_required_keys():
    p = hitl_policy("Read the summary file")
    assert all(k in p for k in ("tier", "gate", "allowed", "risk_level", "flags", "reason"))


def test_hitl_policy_flags_are_dicts():
    p = hitl_policy("rm -rf /tmp/test_dir")
    assert isinstance(p["flags"], list)
    if p["flags"]:
        assert "name" in p["flags"][0]
        assert "risk" in p["flags"][0]


def test_hitl_policy_description_destroys_downgraded_to_write():
    """DESTROY tier downgrades to WRITE when is_description=True (planner step text)."""
    p = hitl_policy("Clone repo (rm -rf first to clean up)", is_description=True)
    # Should warn, not block
    assert p["tier"] != ACTION_TIER_DESTROY
    assert p["allowed"] is True
    assert p["gate"] in ("warn", "none")


def test_hitl_policy_description_false_still_blocks_destroy():
    """Default is_description=False preserves DESTROY-tier blocking behavior."""
    p = hitl_policy("rm -rf /var/log/old/ and clean up")
    assert p["tier"] == ACTION_TIER_DESTROY
    assert p["allowed"] is False
    assert p["gate"] == "block"


def test_hitl_policy_description_downgrade_logs_debug(caplog):
    """Downgrade of DESTROY→WRITE is logged at DEBUG level."""
    import logging
    with caplog.at_level(logging.DEBUG, logger="poe.constraint"):
        hitl_policy("Clone repo (rm -rf first)", is_description=True)
    assert any("downgrade" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Phase 59: ViolationType enum + ViolationReport (NeMo DataDesigner steal)
# ---------------------------------------------------------------------------

class TestViolationType:
    """Tests for ViolationType enum and ViolationReport."""

    def test_all_types_returns_non_empty(self):
        """ViolationType.all_types() returns a list of (cat, desc, severity) tuples."""
        from constraint import ViolationType
        types = ViolationType.all_types()
        assert len(types) > 0
        for t in types:
            assert isinstance(t, tuple)
            assert len(t) == 3

    def test_from_risk_level_high_is_error(self):
        """HIGH risk maps to error severity."""
        from constraint import ViolationType
        cat, desc, sev = ViolationType.from_risk_level("HIGH")
        assert sev == "error"

    def test_from_risk_level_medium_is_warning(self):
        from constraint import ViolationType
        _, _, sev = ViolationType.from_risk_level("MEDIUM")
        assert sev == "warning"

    def test_from_risk_level_low_is_info(self):
        from constraint import ViolationType
        _, _, sev = ViolationType.from_risk_level("LOW")
        assert sev == "info"

    def test_violation_report_is_fatal_for_error(self):
        """ViolationReport.is_fatal is True for error severity."""
        from constraint import ViolationReport
        r = ViolationReport(
            name="test", category="security", description="test",
            severity="error", detail="something bad",
        )
        assert r.is_fatal is True

    def test_violation_report_not_fatal_for_warning(self):
        from constraint import ViolationReport
        r = ViolationReport(
            name="test", category="quality", description="test",
            severity="warning", detail="minor issue",
        )
        assert r.is_fatal is False

    def test_from_constraint_flag_wraps_flag(self):
        """ViolationReport.from_constraint_flag() wraps a ConstraintFlag."""
        from constraint import ViolationReport, ConstraintFlag
        flag = ConstraintFlag(
            name="destructive_op",
            risk="HIGH",
            detail="rm -rf detected",
            pattern="rm -rf",
        )
        report = ViolationReport.from_constraint_flag(flag)
        assert report.name == "destructive_op"
        assert report.is_fatal is True
        assert "rm -rf" in report.detail

    def test_constraint_result_to_violation_reports(self):
        """ConstraintResult.to_violation_reports() returns ViolationReports."""
        from constraint import ConstraintResult, ConstraintFlag, ViolationReport
        result = ConstraintResult(
            allowed=False,
            risk_level="HIGH",
            flags=[
                ConstraintFlag(name="op1", risk="HIGH", detail="d1", pattern="p1"),
                ConstraintFlag(name="op2", risk="MEDIUM", detail="d2", pattern="p2"),
            ],
        )
        reports = result.to_violation_reports()
        assert len(reports) == 2
        assert all(isinstance(r, ViolationReport) for r in reports)

    def test_has_fatal_violations_true_for_high(self):
        """has_fatal_violations() is True when HIGH-risk flags exist."""
        from constraint import ConstraintResult, ConstraintFlag
        result = ConstraintResult(
            allowed=False,
            risk_level="HIGH",
            flags=[ConstraintFlag(name="op1", risk="HIGH", detail="d", pattern="p")],
        )
        assert result.has_fatal_violations() is True

    def test_has_fatal_violations_false_for_medium_only(self):
        """has_fatal_violations() is False when only MEDIUM/LOW flags exist."""
        from constraint import ConstraintResult, ConstraintFlag
        result = ConstraintResult(
            allowed=True,
            risk_level="MEDIUM",
            flags=[ConstraintFlag(name="op1", risk="MEDIUM", detail="d", pattern="p")],
        )
        assert result.has_fatal_violations() is False


# ---------------------------------------------------------------------------
# Fix 3: _check_patterns uses step_text only (not combined with goal)
# ---------------------------------------------------------------------------

class TestGoalTextNotChecked:
    """Goal text must not trigger operational constraints.

    Before the fix, (step_text + goal).lower() caused goal-keyword false-positives.
    A goal like "research cancer treatments" would match a dynamic constraint
    pattern for "research" on every step, even harmless ones.
    """

    def test_goal_keyword_does_not_block_step(self):
        """A HIGH-risk keyword in the goal must not block a clean step."""
        # The goal contains "rm -rf" but the step_text is safe.
        # Built-in destroy patterns check for destructive shell idioms.
        result = check_step_constraints(
            "Summarize the research findings",
            goal="Clean up all old files using rm -rf and delete logs",
        )
        # Step should be allowed because the dangerous text is in goal, not step
        assert result.allowed is True

    def test_step_keyword_still_blocks(self):
        """A HIGH-risk keyword in step_text still blocks (sanity check)."""
        result = check_step_constraints(
            "rm -rf /var/log/production",
            goal="normal goal with no dangerous words",
        )
        assert result.allowed is False


# ---------------------------------------------------------------------------
# Fix 1: Dynamic constraint circuit breaker + TTL
# ---------------------------------------------------------------------------

class TestDynamicConstraintCircuitBreaker:
    """Circuit breaker opens after N consecutive dynamic-only blocks."""

    def setup_method(self):
        import constraint as c
        # Reset circuit breaker state before each test
        c._dynamic_consecutive_blocks = 0
        c._dynamic_circuit_open_until = 0
        c._dynamic_step_counter = 0

    def test_circuit_opens_after_threshold(self):
        import constraint as c
        threshold = c._DYNAMIC_BLOCK_CIRCUIT_BREAKER
        for _ in range(threshold):
            c._record_dynamic_block(True)
        assert c._dynamic_circuit_is_open() is True

    def test_circuit_stays_closed_below_threshold(self):
        import constraint as c
        threshold = c._DYNAMIC_BLOCK_CIRCUIT_BREAKER
        for _ in range(threshold - 1):
            c._record_dynamic_block(True)
        assert c._dynamic_circuit_is_open() is False

    def test_non_block_resets_streak(self):
        import constraint as c
        threshold = c._DYNAMIC_BLOCK_CIRCUIT_BREAKER
        for _ in range(threshold - 1):
            c._record_dynamic_block(True)
        c._record_dynamic_block(False)  # streak resets
        # Now need threshold more consecutive blocks to open
        for _ in range(threshold - 1):
            c._record_dynamic_block(True)
        assert c._dynamic_circuit_is_open() is False

    def test_circuit_closes_after_cooldown(self):
        import constraint as c
        threshold = c._DYNAMIC_BLOCK_CIRCUIT_BREAKER
        for _ in range(threshold):
            c._record_dynamic_block(True)
        assert c._dynamic_circuit_is_open() is True
        # Advance step counter past cooldown
        c._dynamic_step_counter = c._dynamic_circuit_open_until
        assert c._dynamic_circuit_is_open() is False


class TestDynamicConstraintTTL:
    """Expired dynamic constraints are skipped."""

    def _write_constraint_file(self, tmp_path, entry_json: str):
        """Write a dynamic-constraints.jsonl under tmp_path/memory/ (fallback path)."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir(exist_ok=True)
        (mem_dir / "dynamic-constraints.jsonl").write_text(entry_json + "\n")

    def _load_with_tmp_path(self, c, tmp_path):
        """Call _load_dynamic_constraints with orch_items disabled + cwd → tmp_path."""
        import builtins
        from unittest.mock import patch
        real_import = builtins.__import__
        def _no_orch(name, *args, **kwargs):
            if name == "orch_items":
                raise ImportError
            return real_import(name, *args, **kwargs)
        with patch("constraint.Path.cwd", return_value=tmp_path):
            with patch("builtins.__import__", side_effect=_no_orch):
                return c._load_dynamic_constraints()

    def test_expired_constraint_not_loaded(self, tmp_path, monkeypatch):
        import constraint as c
        import json, time
        monkeypatch.setattr(c, "_DYNAMIC_CONSTRAINT_TTL_DAYS", 7)
        stale_ts = time.time() - (8 * 86400)  # 8 days ago
        entry = json.dumps({"pattern": "research", "risk": "HIGH", "added_at": stale_ts})
        self._write_constraint_file(tmp_path, entry)
        loaded = self._load_with_tmp_path(c, tmp_path)
        assert loaded == []

    def test_fresh_constraint_is_loaded(self, tmp_path, monkeypatch):
        import constraint as c
        import json, time
        monkeypatch.setattr(c, "_DYNAMIC_CONSTRAINT_TTL_DAYS", 7)
        fresh_ts = time.time() - (1 * 86400)  # 1 day ago
        entry = json.dumps({"pattern": "research_banned", "risk": "HIGH", "added_at": fresh_ts})
        self._write_constraint_file(tmp_path, entry)
        loaded = self._load_with_tmp_path(c, tmp_path)
        assert len(loaded) == 1
        assert loaded[0][1][0][0] == "research_banned"
