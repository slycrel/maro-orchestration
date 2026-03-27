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
