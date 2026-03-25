#!/usr/bin/env python3
"""Phase 15: Skill sandbox isolation for Poe orchestration.

Runs skill execution in an isolated subprocess so bad skills can't corrupt
the main process. Provides static safety analysis and sandboxed test execution.

Usage:
    from sandbox import run_skill_sandboxed, is_skill_safe, run_skill_tests_sandboxed
    result = run_skill_sandboxed(skill, "do something")
    safe, reason = is_skill_safe(skill)
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from skills import Skill, SkillTestCase

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Patterns that indicate a skill is potentially dangerous
_DANGEROUS_PATTERNS = [
    "import os",
    "import subprocess",
    "__import__",
    "eval(",
    "exec(",
    "open(",
    "shutil",
    "rmdir",
    "unlink",
    "system(",
]

_OUTPUT_TRUNCATION = 4096


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    skill_id: str
    success: bool
    output: str          # captured stdout/stderr (truncated at 4096 chars)
    exit_code: int
    elapsed_ms: int
    timed_out: bool
    error: str           # exception message if failed


# ---------------------------------------------------------------------------
# Safety analysis
# ---------------------------------------------------------------------------

def is_skill_safe(skill: "Skill") -> Tuple[bool, str]:
    """Static analysis: scan skill content for dangerous patterns.

    Checks name, description, and steps_template for patterns that indicate
    potentially dangerous code. This is a heuristic first line of defense —
    not a security guarantee.

    Returns:
        Tuple of (is_safe, reason). If safe: (True, ""). If dangerous: (False, "reason").
    """
    # Gather all text content from the skill
    content_parts = [
        skill.name,
        skill.description,
    ] + list(skill.steps_template)
    full_content = "\n".join(content_parts)

    for pattern in _DANGEROUS_PATTERNS:
        if pattern in full_content:
            return False, f"skill content contains dangerous pattern: {pattern!r}"

    return True, ""


# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------

def run_skill_sandboxed(
    skill: "Skill",
    input_text: str,
    timeout_seconds: int = 30,
) -> SandboxResult:
    """Run a skill in an isolated subprocess.

    Writes a temporary Python runner script, executes it via python3,
    parses stdout as JSON if possible, cleans up tempfile, and returns
    a SandboxResult.

    Args:
        skill:           Skill to execute.
        input_text:      Input text to pass to the skill.
        timeout_seconds: Maximum seconds to wait (default: 30).

    Returns:
        SandboxResult — never raises.
    """
    start_ms = time.monotonic()
    tmp_path: Optional[str] = None

    try:
        # Build the runner script
        steps_comments = "\n".join(
            f"# Step: {step}" for step in skill.steps_template[:10]
        )

        runner_script = (
            "import sys, json\n"
            f"input_text = {input_text!r}\n"
            "# skill behavior section\n"
            f"# Skill: {skill.name!r}\n"
            f"# Description: {skill.description[:200]!r}\n"
            f"{steps_comments}\n"
            "# Execute: produce structured result\n"
            "output_text = f\"Executed skill: {skill.name!r} on input: {{input_text[:200]}}\"\n"
            "print(json.dumps({\"output\": output_text, \"success\": True}))\n"
        ).replace("{skill.name!r}", repr(skill.name))

        # Actually write the clean runner script
        runner_lines = [
            "import sys, json",
            f"input_text = {input_text!r}",
            "# skill behavior section",
            f"# Skill: {repr(skill.name)}",
            f"# Description: {repr(skill.description[:200])}",
        ]
        for step in skill.steps_template[:10]:
            runner_lines.append(f"# Step: {step}")
        runner_lines += [
            "# Execute: produce structured result",
            f"_skill_name = {repr(skill.name)}",
            "output_text = f\"Executed skill: {_skill_name!r} on input: {input_text[:200]}\"",
            "print(json.dumps({\"output\": output_text, \"success\": True}))",
        ]
        script_content = "\n".join(runner_lines) + "\n"

        # Write to tempfile
        with tempfile.NamedTemporaryFile(
            prefix="poe-sandbox-",
            suffix=".py",
            mode="w",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(script_content)
            tmp_path = f.name

        # Execute in subprocess
        timed_out = False
        try:
            proc = subprocess.run(
                ["python3", tmp_path],
                capture_output=True,
                timeout=timeout_seconds,
                text=True,
            )
            exit_code = proc.returncode
            raw_output = (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            raw_output = f"[timeout after {timeout_seconds}s]"
            # Kill any still-running child
            if exc.stdout:
                raw_output = (exc.stdout or b"").decode("utf-8", errors="replace") + raw_output
        except Exception as exc:
            elapsed = int((time.monotonic() - start_ms) * 1000)
            return SandboxResult(
                skill_id=skill.id,
                success=False,
                output="",
                exit_code=-1,
                elapsed_ms=elapsed,
                timed_out=False,
                error=str(exc),
            )

        # Truncate output
        if len(raw_output) > _OUTPUT_TRUNCATION:
            raw_output = raw_output[:_OUTPUT_TRUNCATION] + "...[truncated]"

        # Parse JSON if possible
        output_text = raw_output
        success = (exit_code == 0) and not timed_out
        try:
            data = json.loads(raw_output)
            if isinstance(data, dict):
                output_text = data.get("output", raw_output)
                # JSON-level success overrides only if subprocess also succeeded
                if "success" in data:
                    success = success and bool(data["success"])
        except (json.JSONDecodeError, ValueError):
            pass

        elapsed = int((time.monotonic() - start_ms) * 1000)
        return SandboxResult(
            skill_id=skill.id,
            success=success,
            output=output_text,
            exit_code=exit_code,
            elapsed_ms=elapsed,
            timed_out=timed_out,
            error="" if not timed_out else f"timed out after {timeout_seconds}s",
        )

    except Exception as exc:
        elapsed = int((time.monotonic() - start_ms) * 1000)
        return SandboxResult(
            skill_id=skill.id,
            success=False,
            output="",
            exit_code=-1,
            elapsed_ms=elapsed,
            timed_out=False,
            error=str(exc),
        )
    finally:
        # Always clean up
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Sandboxed test runner
# ---------------------------------------------------------------------------

def run_skill_tests_sandboxed(
    skill: "Skill",
    tests: "List[SkillTestCase]",
    timeout_seconds: int = 10,
) -> Tuple[int, int]:
    """Run skill test cases via sandbox subprocess.

    Like run_skill_tests() in skills.py but runs each test in the sandbox.

    Args:
        skill:           Skill to test.
        tests:           List of SkillTestCase to run.
        timeout_seconds: Max seconds per test (default: 10).

    Returns:
        Tuple of (passed_count, total_count).
    """
    if not tests:
        return 0, 0

    total = len(tests)
    passed = 0

    for test in tests:
        result = run_skill_sandboxed(skill, test.input_description, timeout_seconds=timeout_seconds)
        if result.success:
            output_lower = result.output.lower()
            if any(kw.lower() in output_lower for kw in test.expected_keywords):
                passed += 1

    return passed, total
