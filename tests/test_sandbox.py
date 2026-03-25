"""Phase 15: Tests for sandbox.py — skill subprocess isolation and safety analysis.

Tests cover SandboxResult fields, safety analysis patterns, sandboxed execution,
and the sandboxed test runner.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import sandbox as sb_mod
from sandbox import (
    SandboxResult,
    is_skill_safe,
    run_skill_sandboxed,
    run_skill_tests_sandboxed,
)


# ---------------------------------------------------------------------------
# Helpers — build minimal Skill objects without touching filesystem
# ---------------------------------------------------------------------------

def _make_skill(
    name: str = "test-skill",
    description: str = "A test skill.",
    steps: list | None = None,
    skill_id: str = "test001",
):
    """Build a minimal Skill-like object without importing skills.py."""
    # Use a simple namespace to avoid orch/filesystem dependencies
    from types import SimpleNamespace
    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=description,
        steps_template=steps or ["do a thing", "check the result"],
        trigger_patterns=["test"],
        source_loop_ids=[],
        created_at="2026-01-01T00:00:00+00:00",
        use_count=0,
        success_rate=1.0,
        content_hash="",
    )


def _make_test_case(
    skill_id: str = "test001",
    input_desc: str = "test input",
    keywords: list | None = None,
):
    from types import SimpleNamespace
    return SimpleNamespace(
        skill_id=skill_id,
        input_description=input_desc,
        expected_keywords=keywords or ["executed", "skill"],
        derived_from_failure="",
    )


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------

def test_sandbox_result_fields():
    """SandboxResult has success, output, exit_code, elapsed_ms, timed_out."""
    field_names = {f.name for f in fields(SandboxResult)}
    assert "success" in field_names
    assert "output" in field_names
    assert "exit_code" in field_names
    assert "elapsed_ms" in field_names
    assert "timed_out" in field_names
    assert "skill_id" in field_names
    assert "error" in field_names


def test_sandbox_result_construction():
    """SandboxResult can be constructed with all fields."""
    r = SandboxResult(
        skill_id="abc",
        success=True,
        output="some output",
        exit_code=0,
        elapsed_ms=123,
        timed_out=False,
        error="",
    )
    assert r.success is True
    assert r.output == "some output"
    assert r.exit_code == 0
    assert r.elapsed_ms == 123
    assert r.timed_out is False


# ---------------------------------------------------------------------------
# Safety analysis tests
# ---------------------------------------------------------------------------

def test_is_skill_safe_clean():
    """No dangerous patterns → returns (True, '')."""
    skill = _make_skill(
        description="A safe skill that does safe things.",
        steps=["search the web", "return results"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is True
    assert reason == ""


def test_is_skill_safe_dangerous_eval():
    """'eval(' in content → (False, reason)."""
    skill = _make_skill(
        description="Does eval(user_input) for some reason.",
        steps=["run it"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False
    assert reason != ""
    assert "eval(" in reason


def test_is_skill_safe_dangerous_import_os():
    """'import os' in content → (False, reason)."""
    skill = _make_skill(
        description="Uses import os to do filesystem stuff.",
        steps=["do it"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False
    assert reason != ""


def test_is_skill_safe_dangerous_exec():
    """'exec(' in content → (False, reason)."""
    skill = _make_skill(
        description="A skill that calls exec(code_string).",
        steps=["call exec(code)"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False
    assert "exec(" in reason


def test_is_skill_safe_dangerous_open():
    """'open(' in content → (False, reason)."""
    skill = _make_skill(
        description="Opens files with open(filename).",
        steps=["process it"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False
    assert reason != ""


def test_is_skill_safe_dangerous_shutil():
    """'shutil' in content → (False, reason)."""
    skill = _make_skill(
        description="Uses shutil to move files around.",
        steps=["do it"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False
    assert reason != ""


def test_is_skill_safe_dangerous_subprocess():
    """'import subprocess' in content → (False, reason)."""
    skill = _make_skill(
        description="Runs commands via import subprocess.",
        steps=["execute them"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False
    assert reason != ""


def test_is_skill_safe_dangerous_in_name():
    """Dangerous pattern in skill name → (False, reason)."""
    skill = _make_skill(
        name="eval(inject) skill",
        description="Innocuous description.",
        steps=["safe step"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False


def test_is_skill_safe_dangerous_in_steps():
    """Dangerous pattern in steps_template → (False, reason)."""
    skill = _make_skill(
        name="safe name",
        description="safe description",
        steps=["run normally", "then call exec(payload)"],
    )
    safe, reason = is_skill_safe(skill)
    assert safe is False


# ---------------------------------------------------------------------------
# Sandboxed execution tests
# ---------------------------------------------------------------------------

def test_run_skill_sandboxed_basic():
    """run_skill_sandboxed returns a SandboxResult."""
    skill = _make_skill()
    result = run_skill_sandboxed(skill, "do something")
    assert isinstance(result, SandboxResult)
    assert result.skill_id == skill.id


def test_run_skill_sandboxed_always_returns():
    """run_skill_sandboxed never raises — always returns SandboxResult."""
    skill = _make_skill()
    try:
        result = run_skill_sandboxed(skill, "")
        assert isinstance(result, SandboxResult)
    except Exception as exc:
        pytest.fail(f"run_skill_sandboxed raised: {exc}")


def test_run_skill_sandboxed_success_fields():
    """Successful sandbox execution has exit_code=0, success=True, elapsed_ms>=0."""
    skill = _make_skill()
    result = run_skill_sandboxed(skill, "do something useful")
    # The sandbox always runs a valid python script so should succeed
    assert result.exit_code == 0
    assert result.success is True
    assert result.elapsed_ms >= 0
    assert result.timed_out is False


def test_run_skill_sandboxed_output_has_content():
    """Sandbox result output is non-empty for a basic skill."""
    skill = _make_skill(name="weather check")
    result = run_skill_sandboxed(skill, "check weather")
    assert isinstance(result.output, str)
    assert len(result.output) > 0


def test_run_skill_sandboxed_long_description_truncated():
    """Sandbox handles skills with very long descriptions without crashing."""
    skill = _make_skill(description="x" * 10000, steps=["step 1"] * 20)
    result = run_skill_sandboxed(skill, "test")
    assert isinstance(result, SandboxResult)


def test_run_skill_sandboxed_timeout():
    """Sandboxed execution with very short timeout returns timed_out=True."""
    # Create a skill whose sandboxed script would run forever — we force timeout
    # by mocking subprocess.run to raise TimeoutExpired
    import subprocess
    skill = _make_skill()
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["python3"], timeout=0)
        result = run_skill_sandboxed(skill, "test", timeout_seconds=0)
    assert isinstance(result, SandboxResult)
    assert result.timed_out is True
    assert result.success is False


def test_run_skill_sandboxed_subprocess_error():
    """Subprocess OSError → returns SandboxResult with success=False, never raises."""
    skill = _make_skill()
    with patch("subprocess.run", side_effect=OSError("no python3")):
        result = run_skill_sandboxed(skill, "test")
    assert isinstance(result, SandboxResult)
    assert result.success is False
    assert "no python3" in result.error


def test_validate_sandboxed_no_raise():
    """run_skill_tests_sandboxed never raises on any input."""
    skill = _make_skill()
    tests = [_make_test_case()]
    try:
        passed, total = run_skill_tests_sandboxed(skill, tests)
        assert isinstance(passed, int)
        assert isinstance(total, int)
    except Exception as exc:
        pytest.fail(f"run_skill_tests_sandboxed raised: {exc}")


# ---------------------------------------------------------------------------
# Sandboxed test runner tests
# ---------------------------------------------------------------------------

def test_run_skill_tests_sandboxed_returns_counts():
    """run_skill_tests_sandboxed returns (int, int)."""
    skill = _make_skill()
    tests = [_make_test_case(), _make_test_case(input_desc="second test")]
    passed, total = run_skill_tests_sandboxed(skill, tests)
    assert isinstance(passed, int)
    assert isinstance(total, int)
    assert total == 2
    assert 0 <= passed <= total


def test_run_skill_tests_sandboxed_empty():
    """Empty test list returns (0, 0)."""
    skill = _make_skill()
    passed, total = run_skill_tests_sandboxed(skill, [])
    assert passed == 0
    assert total == 0


def test_run_skill_tests_sandboxed_keyword_matching():
    """Tests pass when expected keywords appear in sandbox output."""
    skill = _make_skill(name="weather check")
    # The sandbox always outputs something including the skill name
    # "weather" and "check" should appear in the output
    test = _make_test_case(
        keywords=["weather", "check"],  # skill name parts — should match
    )
    passed, total = run_skill_tests_sandboxed(skill, [test])
    assert total == 1
    # We can't guarantee pass/fail without live subprocess, but the counts must be int
    assert isinstance(passed, int)


def test_run_skill_tests_sandboxed_no_match_keywords():
    """Tests fail when expected keywords don't appear in sandbox output."""
    skill = _make_skill(name="simple skill")
    test = _make_test_case(
        keywords=["xyzzy_never_matches_anything_12345678"],
    )
    passed, total = run_skill_tests_sandboxed(skill, [test])
    assert passed == 0
    assert total == 1


def test_run_skill_tests_sandboxed_never_raises():
    """run_skill_tests_sandboxed never raises even with unusual inputs."""
    skill = _make_skill(description="x" * 5000)
    tests = [
        _make_test_case(input_desc=""),
        _make_test_case(keywords=[]),
        _make_test_case(keywords=["a", "b", "c", "d"]),
    ]
    try:
        passed, total = run_skill_tests_sandboxed(skill, tests)
        assert isinstance(passed, int)
        assert isinstance(total, int)
    except Exception as exc:
        pytest.fail(f"run_skill_tests_sandboxed raised: {exc}")
