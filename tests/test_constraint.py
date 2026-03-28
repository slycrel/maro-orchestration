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
    assert classify_action_tier("Write the summary to docs/output.md") == ACTION_TIER_WRITE


def test_tier_write_mkdir():
    assert classify_action_tier("Create a new directory for the build artifacts") == ACTION_TIER_WRITE


def test_tier_write_update():
    assert classify_action_tier("Update the config file with the new API endpoint") == ACTION_TIER_WRITE


def test_tier_destroy_rm():
    assert classify_action_tier("Remove the temporary files from /tmp/cache") == ACTION_TIER_DESTROY


def test_tier_destroy_delete():
    assert classify_action_tier("Delete all stale sessions from the database") == ACTION_TIER_DESTROY


def test_tier_destroy_wipe():
    assert classify_action_tier("Wipe the old data partition") == ACTION_TIER_DESTROY


def test_tier_destroy_overrides_write():
    """DESTROY takes precedence over WRITE when both patterns appear."""
    assert classify_action_tier("Write and then delete the temp file") == ACTION_TIER_DESTROY


def test_tier_external_curl():
    assert classify_action_tier("Send the payload via curl to the webhook endpoint") == ACTION_TIER_EXTERNAL


def test_tier_external_git_push():
    assert classify_action_tier("Push the changes to the remote repository with git push") == ACTION_TIER_EXTERNAL


def test_tier_external_notify():
    assert classify_action_tier("Notify the Slack channel about the completion") == ACTION_TIER_EXTERNAL


def test_tier_external_overrides_write():
    """EXTERNAL takes precedence over WRITE when both appear."""
    assert classify_action_tier("Write the post then publish to the blog") == ACTION_TIER_EXTERNAL


# ---------------------------------------------------------------------------
# Phase 35 P2 — HITL gating taxonomy: hitl_policy()
# ---------------------------------------------------------------------------

def test_hitl_policy_read_step_no_gate():
    p = hitl_policy("Summarise all findings from the research file")
    assert p["tier"] == ACTION_TIER_READ
    assert p["gate"] == "none"
    assert p["allowed"] is True


def test_hitl_policy_write_step_warn():
    p = hitl_policy("Save the updated config to workspace/poe.json")
    assert p["tier"] == ACTION_TIER_WRITE
    assert p["gate"] == "warn"
    assert p["allowed"] is True


def test_hitl_policy_destroy_step_blocked():
    p = hitl_policy("Delete all old log files from the workspace")
    assert p["tier"] == ACTION_TIER_DESTROY
    assert p["gate"] == "block"
    assert p["allowed"] is False


def test_hitl_policy_external_step_confirm():
    p = hitl_policy("Send a notification to the Telegram channel with results")
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
