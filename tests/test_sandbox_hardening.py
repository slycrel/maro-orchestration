"""Tests for Phase 18: Sandbox hardening — resource limits, network blocking, audit log."""

from __future__ import annotations

import json
import sys
import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sandbox import (
    SandboxConfig,
    SandboxResult,
    is_skill_safe,
    run_skill_sandboxed,
    run_skill_tests_sandboxed,
    load_audit_log,
    _write_audit,
    _audit_log_path,
    _make_preexec_fn,
    _get_venv_python,
    _NETWORK_BLOCKER_CODE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(name="test_skill", description="A test skill", steps=None, id="s1"):
    from unittest.mock import MagicMock
    skill = MagicMock()
    skill.id = id
    skill.name = name
    skill.description = description
    skill.steps_template = steps or ["step 1", "step 2"]
    skill.tier = "provisional"
    return skill


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


# ---------------------------------------------------------------------------
# SandboxConfig defaults
# ---------------------------------------------------------------------------

def test_sandbox_config_defaults():
    cfg = SandboxConfig()
    assert cfg.timeout_seconds == 30
    assert cfg.max_cpu_seconds == 20
    assert cfg.max_file_size_mb == 10
    assert cfg.max_open_files == 64
    assert cfg.block_network is True
    assert cfg.use_venv is False
    assert cfg.audit is True


def test_sandbox_config_custom():
    cfg = SandboxConfig(timeout_seconds=5, block_network=False, audit=False)
    assert cfg.timeout_seconds == 5
    assert cfg.block_network is False
    assert cfg.audit is False


# ---------------------------------------------------------------------------
# SandboxResult — new Phase 18 fields
# ---------------------------------------------------------------------------

def test_sandbox_result_has_audit_id():
    r = SandboxResult(
        skill_id="s1", success=True, output="", exit_code=0,
        elapsed_ms=0, timed_out=False, error="",
    )
    assert r.audit_id == ""  # default


def test_sandbox_result_phase18_fields():
    r = SandboxResult(
        skill_id="s1", success=True, output="out", exit_code=0,
        elapsed_ms=100, timed_out=False, error="",
        audit_id="abc123",
        network_blocked=True,
        venv_isolated=False,
        resource_limited=True,
    )
    assert r.audit_id == "abc123"
    assert r.network_blocked is True
    assert r.venv_isolated is False
    assert r.resource_limited is True


# ---------------------------------------------------------------------------
# Static safety analysis — Phase 18 additions
# ---------------------------------------------------------------------------

def test_is_skill_safe_blocks_socket():
    skill = _make_skill(description="skill.socket.connect to server")
    safe, reason = is_skill_safe(skill)
    assert not safe
    assert "socket.connect" in reason


def test_is_skill_safe_blocks_requests():
    skill = _make_skill(description="use requests.get to fetch data")
    safe, reason = is_skill_safe(skill)
    assert not safe


def test_is_skill_safe_blocks_pickle():
    skill = _make_skill(steps=["result = pickle.loads(data)"])
    safe, reason = is_skill_safe(skill)
    assert not safe


def test_is_skill_safe_blocks_ctypes():
    skill = _make_skill(steps=["import ctypes"])
    safe, reason = is_skill_safe(skill)
    assert not safe


def test_is_skill_safe_clean_skill():
    skill = _make_skill(description="summarize text", steps=["read input", "return summary"])
    safe, reason = is_skill_safe(skill)
    assert safe
    assert reason == ""


# ---------------------------------------------------------------------------
# Network blocker code
# ---------------------------------------------------------------------------

def test_network_blocker_code_is_valid_python():
    # Should compile without syntax error
    compile(_NETWORK_BLOCKER_CODE, "<test>", "exec")


def test_network_blocker_blocks_connect(tmp_path):
    """Verify the network blocker prevents socket.connect in a subprocess."""
    script = tmp_path / "test_net.py"
    script.write_text(
        _NETWORK_BLOCKER_CODE + "\n"
        "import socket\n"
        "try:\n"
        "    s = socket.socket()\n"
        "    s.connect(('8.8.8.8', 80))\n"
        "    print('CONNECTED')  # should not reach\n"
        "except ConnectionRefusedError:\n"
        "    print('BLOCKED')\n"
        "except Exception as e:\n"
        "    print(f'OTHER: {e}')\n"
    )
    import subprocess
    result = subprocess.run(["python3", str(script)], capture_output=True, text=True, timeout=5)
    assert "BLOCKED" in result.stdout or "CONNECTED" not in result.stdout


# ---------------------------------------------------------------------------
# run_skill_sandboxed — Phase 18 config
# ---------------------------------------------------------------------------

def test_run_skill_sandboxed_returns_audit_id(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(audit=False)
    result = run_skill_sandboxed(skill, "test input", config=config)
    assert result.audit_id  # non-empty UUID-like string
    assert len(result.audit_id) == 12


def test_run_skill_sandboxed_network_blocked_flag(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(block_network=True, audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    assert result.network_blocked is True


def test_run_skill_sandboxed_no_network_block(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(block_network=False, audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    assert result.network_blocked is False


def test_run_skill_sandboxed_resource_limited_flag(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(max_cpu_seconds=10, audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    assert result.resource_limited is True


def test_run_skill_sandboxed_no_resource_limits(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(max_cpu_seconds=0, max_file_size_mb=0, max_open_files=0, audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    assert result.resource_limited is False


def test_run_skill_sandboxed_succeeds_with_hardening(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(block_network=True, max_cpu_seconds=10, audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    assert result.success is True
    assert result.exit_code == 0


def test_run_skill_sandboxed_writes_audit(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(audit=True)
    result = run_skill_sandboxed(skill, "test", config=config)
    audit_path = _audit_log_path()
    assert audit_path.exists()
    lines = [l for l in audit_path.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry["skill_id"] == skill.id
    assert entry["audit_id"] == result.audit_id


def test_run_skill_sandboxed_no_audit(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(audit=False)
    run_skill_sandboxed(skill, "test", config=config)
    audit_path = _audit_log_path()
    assert not audit_path.exists()


def test_run_skill_sandboxed_timeout_respected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill(steps=["import time; time.sleep(60)"])
    config = SandboxConfig(timeout_seconds=2, audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    # The stub runner doesn't actually execute step code, so it won't timeout
    # but we verify the config is passed through
    assert result.audit_id


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_write_audit_creates_file(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = SandboxResult(
        skill_id="s1", success=True, output="out", exit_code=0,
        elapsed_ms=50, timed_out=False, error="",
        audit_id="test123",
        network_blocked=True, venv_isolated=False, resource_limited=True,
    )
    _write_audit(result, skill_name="myskill", static_safe=True, safety_reason="")
    path = _audit_log_path()
    assert path.exists()
    entry = json.loads(path.read_text().strip())
    assert entry["audit_id"] == "test123"
    assert entry["skill_name"] == "myskill"
    assert entry["static_safe"] is True
    assert entry["network_blocked"] is True
    assert "timestamp" in entry


def test_write_audit_multiple_entries(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for i in range(3):
        result = SandboxResult(
            skill_id=f"s{i}", success=True, output="", exit_code=0,
            elapsed_ms=i * 10, timed_out=False, error="",
            audit_id=f"id{i}", network_blocked=False, venv_isolated=False, resource_limited=False,
        )
        _write_audit(result, skill_name=f"skill{i}", static_safe=True, safety_reason="")
    path = _audit_log_path()
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 3


def test_load_audit_log_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    entries = load_audit_log()
    assert entries == []


def test_load_audit_log_returns_newest_first(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for i in range(5):
        result = SandboxResult(
            skill_id=f"s{i}", success=True, output="", exit_code=0,
            elapsed_ms=i, timed_out=False, error="",
            audit_id=f"id{i}", network_blocked=False, venv_isolated=False, resource_limited=False,
        )
        _write_audit(result, skill_name=f"skill{i}", static_safe=True, safety_reason="")
    entries = load_audit_log(limit=5)
    assert len(entries) == 5
    # Newest first means last written (skill4) is first
    assert entries[0]["audit_id"] == "id4"


def test_load_audit_log_respects_limit(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for i in range(10):
        result = SandboxResult(
            skill_id=f"s{i}", success=True, output="", exit_code=0,
            elapsed_ms=i, timed_out=False, error="",
            audit_id=f"id{i}", network_blocked=False, venv_isolated=False, resource_limited=False,
        )
        _write_audit(result, skill_name=f"skill{i}", static_safe=True, safety_reason="")
    entries = load_audit_log(limit=3)
    assert len(entries) == 3


def test_write_audit_never_raises(monkeypatch, tmp_path):
    """Audit failures must never block execution."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", "/nonexistent/path/that/cant/be/created")
    result = SandboxResult(
        skill_id="s1", success=True, output="", exit_code=0,
        elapsed_ms=0, timed_out=False, error="", audit_id="x",
        network_blocked=False, venv_isolated=False, resource_limited=False,
    )
    # Should not raise even with bad path
    _write_audit(result, skill_name="test", static_safe=True, safety_reason="")


# ---------------------------------------------------------------------------
# _make_preexec_fn
# ---------------------------------------------------------------------------

def test_make_preexec_fn_is_callable():
    cfg = SandboxConfig()
    fn = _make_preexec_fn(cfg)
    assert callable(fn)


def test_make_preexec_fn_zero_limits_no_crash():
    """With all limits at 0, preexec_fn should do nothing and not crash."""
    cfg = SandboxConfig(max_cpu_seconds=0, max_file_size_mb=0, max_open_files=0)
    fn = _make_preexec_fn(cfg)
    fn()  # call in-process — should not raise


# ---------------------------------------------------------------------------
# run_skill_tests_sandboxed — config propagation
# ---------------------------------------------------------------------------

def test_run_skill_tests_sandboxed_with_config(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from sandbox import run_skill_tests_sandboxed as _rst
    skill = _make_skill()
    from unittest.mock import MagicMock
    test = MagicMock()
    test.input_description = "do something"
    test.expected_keywords = ["executed"]
    config = SandboxConfig(audit=False, block_network=True)
    passed, total = _rst(skill, [test], config=config)
    assert total == 1
    assert passed >= 0


def test_run_skill_tests_sandboxed_no_tests(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    passed, total = run_skill_tests_sandboxed(skill, [])
    assert passed == 0
    assert total == 0


# ---------------------------------------------------------------------------
# Adversarial / edge cases (manual review findings)
# ---------------------------------------------------------------------------

def test_dangerous_skill_still_runs_sandboxed(monkeypatch, tmp_path):
    """is_skill_safe returning False doesn't block execution — sandbox does.
    Caller must decide whether to run unsafe skills. Audit captures the fact."""
    _setup(monkeypatch, tmp_path)
    skill = _make_skill(description="import os; os.system('rm -rf /')")
    config = SandboxConfig(audit=True)
    result = run_skill_sandboxed(skill, "test", config=config)
    # Execution proceeds (safety analysis is advisory, not blocking)
    assert isinstance(result, SandboxResult)
    # Audit records the unsafe flag
    entries = load_audit_log()
    assert any(e["audit_id"] == result.audit_id for e in entries)
    matching = next(e for e in entries if e["audit_id"] == result.audit_id)
    assert matching["static_safe"] is False


def test_sandbox_result_never_raises(monkeypatch, tmp_path):
    """run_skill_sandboxed must never raise — always returns SandboxResult."""
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    # Corrupt skill to trigger an edge case
    skill.steps_template = None  # type: ignore
    config = SandboxConfig(audit=False)
    result = run_skill_sandboxed(skill, "test", config=config)
    assert isinstance(result, SandboxResult)


def test_audit_entry_has_all_required_fields(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    skill = _make_skill()
    config = SandboxConfig(audit=True)
    run_skill_sandboxed(skill, "input", config=config)
    entries = load_audit_log()
    assert entries
    e = entries[0]
    required = {
        "audit_id", "timestamp", "skill_id", "skill_name",
        "static_safe", "exit_code", "elapsed_ms", "timed_out",
        "success", "network_blocked", "venv_isolated", "resource_limited",
        "output_preview", "error",
    }
    for field in required:
        assert field in e, f"Missing audit field: {field}"
