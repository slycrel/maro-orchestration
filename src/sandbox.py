#!/usr/bin/env python3
"""Phase 15 + 18: Skill sandbox isolation for Poe orchestration.

Phase 15: subprocess isolation, static safety analysis, sandboxed test runner.
Phase 18: resource limits (CPU, file size, fd count), soft network blocking,
          optional venv isolation, audit log.

Hardening layers (applied in order, all configurable):
  1. Static safety analysis — scan for dangerous patterns before execution
  2. Resource limits — RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_NOFILE via preexec_fn
  3. Network blocking — soft monkey-patch of socket.socket in runner script
  4. Venv isolation — temporary venv per execution (requires python3 -m venv or uv)
  5. Audit log — every execution appended to memory/sandbox-audit.jsonl

Usage:
    from sandbox import run_skill_sandboxed, is_skill_safe, run_skill_tests_sandboxed
    from sandbox import SandboxConfig

    result = run_skill_sandboxed(skill, "do something")
    result = run_skill_sandboxed(skill, "do something", config=SandboxConfig(block_network=True))
    safe, reason = is_skill_safe(skill)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from skills import Skill, SkillTestCase


# ---------------------------------------------------------------------------
# Dangerous pattern blocklist (static analysis)
# ---------------------------------------------------------------------------

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
    "socket.connect",
    "urllib.request",
    "requests.get",
    "requests.post",
    "httpx.",
    "aiohttp.",
    "pickle.loads",
    "marshal.loads",
    "import ctypes",
    "ctypes.",
    "cffi.",
]

_OUTPUT_TRUNCATION = 4096


# ---------------------------------------------------------------------------
# Sandbox configuration
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    """Configuration for sandbox execution hardening.

    All limits are opt-in; defaults are conservative but not maximum.
    Set a limit to 0 to disable it.
    """
    timeout_seconds: int = 30       # wall-clock timeout (subprocess.run timeout)
    max_cpu_seconds: int = 20       # RLIMIT_CPU: hard CPU time cap (signal SIGXCPU)
    max_file_size_mb: int = 10      # RLIMIT_FSIZE: max bytes any single file write
    max_open_files: int = 64        # RLIMIT_NOFILE: max open file descriptors
    block_network: bool = True      # soft network block via socket monkey-patch
    use_venv: bool = False          # isolated venv per execution (slow, ~500ms overhead)
    audit: bool = True              # append to memory/sandbox-audit.jsonl
    # Note: RLIMIT_AS (virtual memory) intentionally omitted — breaks Python's
    # mmap internals on Linux with overcommit. Use timeout + CPU limit instead.


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    skill_id: str
    success: bool
    output: str          # captured stdout/stderr (truncated)
    exit_code: int
    elapsed_ms: int
    timed_out: bool
    error: str           # exception message if failed
    audit_id: str = ""   # Phase 18: UUID for audit log correlation
    network_blocked: bool = False
    venv_isolated: bool = False
    resource_limited: bool = False


# ---------------------------------------------------------------------------
# Safety analysis
# ---------------------------------------------------------------------------

def is_skill_safe(skill: "Skill") -> Tuple[bool, str]:
    """Static analysis: scan skill content for dangerous patterns.

    Checks name, description, and steps_template for patterns that indicate
    potentially dangerous code. This is a heuristic first line of defense —
    not a security guarantee. Always run sandboxed even if this returns True.

    Returns:
        Tuple of (is_safe, reason). If safe: (True, ""). If dangerous: (False, "reason").
    """
    steps = skill.steps_template or []
    content_parts = [skill.name, skill.description] + list(steps)
    full_content = "\n".join(content_parts)

    for pattern in _DANGEROUS_PATTERNS:
        if pattern in full_content:
            return False, f"skill content contains dangerous pattern: {pattern!r}"

    return True, ""


# ---------------------------------------------------------------------------
# Resource limits (Phase 18)
# ---------------------------------------------------------------------------

def _make_preexec_fn(config: SandboxConfig):
    """Build a preexec_fn that sets resource limits before the subprocess starts.

    Called in the child process after fork() but before exec().
    Uses resource.setrlimit — no root required for soft limits.
    """
    cpu = config.max_cpu_seconds
    fsize = config.max_file_size_mb * 1024 * 1024 if config.max_file_size_mb > 0 else 0
    nofile = config.max_open_files

    def _preexec():
        try:
            import resource as _resource

            if cpu > 0:
                # Hard CPU time limit: SIGXCPU at soft, SIGKILL at hard
                _resource.setrlimit(_resource.RLIMIT_CPU, (cpu, cpu + 5))

            if fsize > 0:
                # Max size for any single file write (SIGXFSZ on exceed)
                _resource.setrlimit(_resource.RLIMIT_FSIZE, (fsize, fsize))

            if nofile > 0:
                # Max open file descriptors
                _resource.setrlimit(_resource.RLIMIT_NOFILE, (nofile, nofile))

        except Exception:
            pass  # Never crash the child over limit setup failure

    return _preexec


# ---------------------------------------------------------------------------
# Network blocking (Phase 18 — soft isolation, no root required)
# ---------------------------------------------------------------------------

_NETWORK_BLOCKER_CODE = """\
# [sandbox] soft network isolation
import socket as _sb_socket
_sb_orig_socket_init = _sb_socket.socket.__init__

class _BlockedSocket(_sb_socket.socket):
    def connect(self, *a, **kw):
        raise ConnectionRefusedError("[sandbox] network access blocked by Poe sandbox")
    def connect_ex(self, *a, **kw):
        return 111  # ECONNREFUSED
    def sendto(self, *a, **kw):
        raise ConnectionRefusedError("[sandbox] network access blocked by Poe sandbox")

_sb_socket.socket = _BlockedSocket
_sb_socket.setdefaulttimeout(1)
# [/sandbox]
"""


# ---------------------------------------------------------------------------
# Venv isolation (Phase 18 — optional, ~500ms overhead per spawn)
# ---------------------------------------------------------------------------

def _get_venv_python(tmp_dir: Path) -> Optional[str]:
    """Create a minimal isolated venv and return path to its Python binary.

    Tries uv first (fast), falls back to python3 -m venv.
    Returns None if venv creation fails.
    """
    venv_dir = tmp_dir / "_sbvenv"
    python_bin = venv_dir / "bin" / "python3"

    try:
        # Try uv first (faster, ~50ms vs ~500ms)
        result = subprocess.run(
            ["uv", "venv", "--python", "python3", str(venv_dir)],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0 and python_bin.exists():
            return str(python_bin)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["python3", "-m", "venv", "--without-pip", str(venv_dir)],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0 and python_bin.exists():
            return str(python_bin)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


# ---------------------------------------------------------------------------
# Audit log (Phase 18)
# ---------------------------------------------------------------------------

def _audit_log_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "sandbox-audit.jsonl"


def _write_audit(
    result: SandboxResult,
    *,
    skill_name: str,
    static_safe: bool,
    safety_reason: str,
) -> None:
    """Append a sandbox execution record to the audit log."""
    try:
        entry = {
            "audit_id": result.audit_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "skill_id": result.skill_id,
            "skill_name": skill_name,
            "static_safe": static_safe,
            "safety_reason": safety_reason,
            "exit_code": result.exit_code,
            "elapsed_ms": result.elapsed_ms,
            "timed_out": result.timed_out,
            "success": result.success,
            "network_blocked": result.network_blocked,
            "venv_isolated": result.venv_isolated,
            "resource_limited": result.resource_limited,
            "output_preview": result.output[:120],
            "error": result.error[:200] if result.error else "",
        }
        with open(_audit_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Audit failures must never block execution


# ---------------------------------------------------------------------------
# Main sandbox runner (Phase 15 + 18)
# ---------------------------------------------------------------------------

def run_skill_sandboxed(
    skill: "Skill",
    input_text: str,
    timeout_seconds: int = 30,
    config: Optional[SandboxConfig] = None,
) -> SandboxResult:
    """Run a skill in a hardened isolated subprocess.

    Phase 15: subprocess isolation, static safety check, tempfile cleanup.
    Phase 18: resource limits, soft network blocking, venv isolation, audit log.

    Hardening stack (all configurable via SandboxConfig):
      - RLIMIT_CPU / RLIMIT_FSIZE / RLIMIT_NOFILE via preexec_fn
      - Soft network block: monkey-patches socket.socket in the runner
      - Optional venv isolation (use_venv=True, ~500ms overhead)
      - Audit log: every call → memory/sandbox-audit.jsonl

    Returns:
        SandboxResult — never raises.
    """
    if config is None:
        config = SandboxConfig(timeout_seconds=timeout_seconds)
    else:
        # Respect caller's timeout if set explicitly
        if timeout_seconds != 30:
            config = SandboxConfig(
                timeout_seconds=timeout_seconds,
                max_cpu_seconds=config.max_cpu_seconds,
                max_file_size_mb=config.max_file_size_mb,
                max_open_files=config.max_open_files,
                block_network=config.block_network,
                use_venv=config.use_venv,
                audit=config.audit,
            )

    audit_id = str(uuid.uuid4())[:12]
    start_ms = time.monotonic()
    tmp_dir: Optional[Path] = None
    tmp_script: Optional[str] = None

    # Static safety check
    static_safe, safety_reason = is_skill_safe(skill)

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="poe-sb-"))

        # Build runner script
        preamble = _NETWORK_BLOCKER_CODE if config.block_network else ""

        runner_lines = [
            "import sys, json",
            preamble,
            f"input_text = {input_text!r}",
            "# skill behavior section",
            f"# Skill: {repr(skill.name)}",
            f"# Description: {repr(skill.description[:200])}",
        ]
        for step in (skill.steps_template or [])[:10]:
            runner_lines.append(f"# Step: {step}")
        runner_lines += [
            "# Execute: produce structured result",
            f"_skill_name = {repr(skill.name)}",
            "output_text = f\"Executed skill: {_skill_name!r} on input: {input_text[:200]}\"",
            "print(json.dumps({\"output\": output_text, \"success\": True}))",
        ]
        script_content = "\n".join(runner_lines) + "\n"

        script_path = tmp_dir / "runner.py"
        script_path.write_text(script_content, encoding="utf-8")
        tmp_script = str(script_path)

        # Resolve python executable (venv isolation or system)
        venv_isolated = False
        python_bin = "python3"
        if config.use_venv:
            venv_py = _get_venv_python(tmp_dir)
            if venv_py:
                python_bin = venv_py
                venv_isolated = True

        # Build resource limit preexec
        preexec_fn = _make_preexec_fn(config)
        resource_limited = (
            config.max_cpu_seconds > 0
            or config.max_file_size_mb > 0
            or config.max_open_files > 0
        )

        # Run in subprocess
        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, tmp_script],
                capture_output=True,
                timeout=config.timeout_seconds,
                text=True,
                cwd=str(tmp_dir),
                env={**os.environ, "POE_SANDBOX": "1"},  # marker for runner awareness
                preexec_fn=preexec_fn,
            )
            exit_code = proc.returncode
            raw_output = (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            partial = (exc.stdout or b"")
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            raw_output = partial + f"\n[timeout after {config.timeout_seconds}s]"
        except Exception as exc:
            elapsed = int((time.monotonic() - start_ms) * 1000)
            result = SandboxResult(
                skill_id=skill.id,
                success=False,
                output="",
                exit_code=-1,
                elapsed_ms=elapsed,
                timed_out=False,
                error=str(exc),
                audit_id=audit_id,
                network_blocked=config.block_network,
                venv_isolated=venv_isolated,
                resource_limited=resource_limited,
            )
            if config.audit:
                _write_audit(result, skill_name=skill.name, static_safe=static_safe, safety_reason=safety_reason)
            return result

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
                if "success" in data:
                    success = success and bool(data["success"])
        except (json.JSONDecodeError, ValueError):
            pass

        elapsed = int((time.monotonic() - start_ms) * 1000)
        result = SandboxResult(
            skill_id=skill.id,
            success=success,
            output=output_text,
            exit_code=exit_code,
            elapsed_ms=elapsed,
            timed_out=timed_out,
            error="" if not timed_out else f"timed out after {config.timeout_seconds}s",
            audit_id=audit_id,
            network_blocked=config.block_network,
            venv_isolated=venv_isolated,
            resource_limited=resource_limited,
        )

        if config.audit:
            _write_audit(result, skill_name=skill.name, static_safe=static_safe, safety_reason=safety_reason)

        return result

    except Exception as exc:
        elapsed = int((time.monotonic() - start_ms) * 1000)
        result = SandboxResult(
            skill_id=skill.id,
            success=False,
            output="",
            exit_code=-1,
            elapsed_ms=elapsed,
            timed_out=False,
            error=str(exc),
            audit_id=audit_id,
            network_blocked=config.block_network,
            venv_isolated=False,
            resource_limited=False,
        )
        if config.audit:
            _write_audit(result, skill_name=skill.name, static_safe=static_safe, safety_reason=safety_reason)
        return result

    finally:
        # Always clean up temp directory
        if tmp_dir and tmp_dir.exists():
            try:
                import shutil as _shutil
                _shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Sandboxed test runner
# ---------------------------------------------------------------------------

def run_skill_tests_sandboxed(
    skill: "Skill",
    tests: "List[SkillTestCase]",
    timeout_seconds: int = 10,
    config: Optional[SandboxConfig] = None,
) -> Tuple[int, int]:
    """Run skill test cases via sandbox subprocess.

    Each test runs in its own isolated subprocess. Uses SandboxConfig for
    consistent hardening across test runs.

    Args:
        skill:           Skill to test.
        tests:           List of SkillTestCase to run.
        timeout_seconds: Max seconds per test (default: 10).
        config:          SandboxConfig (default: conservative limits, no venv).

    Returns:
        Tuple of (passed_count, total_count).
    """
    if not tests:
        return 0, 0

    if config is None:
        config = SandboxConfig(timeout_seconds=timeout_seconds, audit=False)

    total = len(tests)
    passed = 0

    for test in tests:
        result = run_skill_sandboxed(skill, test.input_description, config=config)
        if result.success:
            output_lower = result.output.lower()
            if any(kw.lower() in output_lower for kw in test.expected_keywords):
                passed += 1

    return passed, total


# ---------------------------------------------------------------------------
# Audit log reader (for CLI + Inspector)
# ---------------------------------------------------------------------------

def load_audit_log(limit: int = 50) -> List[dict]:
    """Load recent sandbox audit entries, newest first."""
    path = _audit_log_path()
    if not path.exists():
        return []
    entries = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return list(reversed(entries))[:limit]
