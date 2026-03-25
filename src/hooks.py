#!/usr/bin/env python3
"""Phase 11: Hook registry and execution engine for Poe orchestration.

Pluggable callbacks at each hierarchy level (mission / milestone / feature / step).

Hook types:
  reviewer     — LLM critique; can block advancement (BLOCK keyword)
  reporter     — emit summary to Telegram or log (non-blocking)
  coordinator  — LLM routing decision injected into next step context
  script       — shell command (non-blocking, captures first 500 chars)
  notification — Factory System Notifications: injects guidance mid-run

Usage:
    from hooks import load_registry, run_hooks, any_blocking

    registry = load_registry()
    results = run_hooks(SCOPE_STEP, {"goal": ..., "step": ..., "step_result": ...}, registry=registry)
    if any_blocking(results):
        # block advancement
        pass
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Scope + type constants
# ---------------------------------------------------------------------------

SCOPE_MISSION   = "mission"
SCOPE_MILESTONE = "milestone"
SCOPE_FEATURE   = "feature"
SCOPE_STEP      = "step"

TYPE_REVIEWER     = "reviewer"
TYPE_REPORTER     = "reporter"
TYPE_COORDINATOR  = "coordinator"
TYPE_SCRIPT       = "script"
TYPE_NOTIFICATION = "notification"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Hook:
    id: str                       # uuid[:8] or "builtin-*"
    name: str
    scope: str                    # one of SCOPE_*
    hook_type: str                # one of TYPE_*
    enabled: bool = True
    # For reviewer / coordinator / notification: LLM prompt template
    prompt_template: str = ""
    # For script: shell command template (supports {project}, {goal}, {step} placeholders)
    command_template: str = ""
    # For reporter: "telegram" | "log" | "both"
    report_target: str = "log"
    # Model tier for LLM hooks
    model: str = "mid"            # "cheap" | "mid" | "power"
    # When to fire
    fire_on: str = "after"        # "before" | "after"


@dataclass
class HookResult:
    hook_id: str
    hook_name: str
    hook_type: str
    scope: str
    status: str                   # "passed" | "failed" | "skipped" | "notification_sent"
    output: str = ""              # reviewer critique, coordinator decision, script stdout
    should_block: bool = False    # reviewer can block advancement
    injected_context: str = ""    # notification hooks inject this into next LLM call
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# Built-in hooks (defined at module level; NOT auto-registered at import time)
# ---------------------------------------------------------------------------

BUILTIN_HOOKS: List[Hook] = [
    Hook(
        id="builtin-progress-reporter",
        name="Progress Reporter",
        scope=SCOPE_MILESTONE,
        hook_type=TYPE_REPORTER,
        prompt_template=(
            "Milestone '{milestone_title}' completed for goal: {goal}. "
            "Features done: {features_done}/{features_total}."
        ),
        report_target="log",
        fire_on="after",
        enabled=False,
    ),
    Hook(
        id="builtin-step-reviewer",
        name="Step Reviewer",
        scope=SCOPE_STEP,
        hook_type=TYPE_REVIEWER,
        prompt_template=(
            "You are a quality reviewer. Review this step result for goal '{goal}'.\n"
            "Step: {step}\nResult: {step_result}\n\n"
            "Reply with 'PASS: [brief comment]' or 'BLOCK: [reason the work is insufficient]'.\n"
            "Only BLOCK if the result is genuinely wrong or empty. Prefer PASS."
        ),
        model="cheap",
        fire_on="after",
        enabled=False,
    ),
    Hook(
        id="builtin-milestone-validator",
        name="Milestone Validator",
        scope=SCOPE_MILESTONE,
        hook_type=TYPE_REVIEWER,
        prompt_template=(
            "You are validating milestone '{milestone_title}' for mission: {goal}.\n"
            "Validation criteria:\n{validation_criteria}\n\n"
            "Features completed:\n{features_summary}\n\n"
            "Reply 'PASS: [summary]' if criteria are met, or 'BLOCK: [what's missing]'."
        ),
        model="mid",
        fire_on="after",
        enabled=False,
    ),
    Hook(
        id="builtin-plan-alignment",
        name="Plan Alignment Notification",
        scope=SCOPE_FEATURE,
        hook_type=TYPE_NOTIFICATION,
        prompt_template=(
            "System notification: You are working on feature '{feature_title}' as part of "
            "milestone '{milestone_title}'. The overall mission goal is: {goal}. "
            "Stay aligned with this mission — don't expand scope beyond this feature."
        ),
        fire_on="before",
        enabled=False,
    ),
    # Phase 19: Worker Boot Protocol hook
    Hook(
        id="builtin-worker-boot",
        name="Worker Boot Protocol",
        scope=SCOPE_FEATURE,
        hook_type=TYPE_NOTIFICATION,
        prompt_template=(
            "Boot context for feature '{feature_title}': {boot_context}"
        ),
        fire_on="before",
        enabled=False,
    ),
    # Phase 19: Sprint Contract grading hook
    Hook(
        id="builtin-sprint-contract",
        name="Sprint Contract",
        scope=SCOPE_FEATURE,
        hook_type=TYPE_REVIEWER,
        prompt_template=(
            "Grade this feature result against sprint contract criteria:\n"
            "Criteria: {success_criteria}\n"
            "Result: {feature_result}\n"
            "Reply PASS: [summary] or BLOCK: [what's missing]"
        ),
        model="cheap",
        fire_on="after",
        enabled=False,
    ),
]

# Index by id for fast lookup
_BUILTIN_BY_ID: Dict[str, Hook] = {h.id: h for h in BUILTIN_HOOKS}


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------

class HookRegistry:
    """Stores and persists registered hooks.

    Loads from `.hooks/hooks.json` inside the orch root (or `config_path`).
    Creates an empty registry if the file is not found.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._path = config_path or _default_hooks_path()
        self._hooks: List[Hook] = []
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, hook: Hook) -> None:
        """Add hook (replace if same id already exists), then persist."""
        self._hooks = [h for h in self._hooks if h.id != hook.id]
        self._hooks.append(hook)
        self._save()

    def unregister(self, hook_id: str) -> bool:
        """Remove hook by id. Returns True if it was found."""
        before = len(self._hooks)
        self._hooks = [h for h in self._hooks if h.id != hook_id]
        if len(self._hooks) < before:
            self._save()
            return True
        return False

    def list_hooks(
        self,
        scope: Optional[str] = None,
        hook_type: Optional[str] = None,
    ) -> List[Hook]:
        result = list(self._hooks)
        if scope is not None:
            result = [h for h in result if h.scope == scope]
        if hook_type is not None:
            result = [h for h in result if h.hook_type == hook_type]
        return result

    def get_hooks_for_scope(self, scope: str) -> List[Hook]:
        """Return all *enabled* hooks at the given scope."""
        return [h for h in self._hooks if h.scope == scope and h.enabled]

    def enable(self, hook_id: str) -> bool:
        """Enable a hook by id. Returns True if found."""
        for h in self._hooks:
            if h.id == hook_id:
                h.enabled = True
                self._save()
                return True
        return False

    def disable(self, hook_id: str) -> bool:
        """Disable a hook by id. Returns True if found."""
        for h in self._hooks:
            if h.id == hook_id:
                h.enabled = False
                self._save()
                return True
        return False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._hooks = [_hook_from_dict(d) for d in data.get("hooks", [])]
        except Exception:
            self._hooks = []

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"hooks": [asdict(h) for h in self._hooks]}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass  # Persistence failures are non-fatal


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _default_hooks_path() -> Optional[Path]:
    """Return the default hooks.json path under the orch root."""
    try:
        import orch
        return orch.orch_root() / ".hooks" / "hooks.json"
    except Exception:
        return None


def load_registry(config_path: Optional[Path] = None) -> HookRegistry:
    """Convenience function: load registry from default (or given) path."""
    return HookRegistry(config_path=config_path)


def _hook_from_dict(d: Dict[str, Any]) -> Hook:
    return Hook(
        id=d.get("id", str(uuid.uuid4())[:8]),
        name=d.get("name", ""),
        scope=d.get("scope", SCOPE_STEP),
        hook_type=d.get("hook_type", TYPE_REPORTER),
        enabled=d.get("enabled", True),
        prompt_template=d.get("prompt_template", ""),
        command_template=d.get("command_template", ""),
        report_target=d.get("report_target", "log"),
        model=d.get("model", "mid"),
        fire_on=d.get("fire_on", "after"),
    )


# ---------------------------------------------------------------------------
# Hook execution engine
# ---------------------------------------------------------------------------

def run_hooks(
    scope: str,
    context: Dict[str, Any],
    registry: Optional[HookRegistry] = None,
    adapter=None,
    dry_run: bool = False,
    fire_on: Optional[str] = None,
) -> List[HookResult]:
    """Run all enabled hooks at `scope` in registration order.

    Args:
        scope:    One of SCOPE_*.
        context:  Dict with keys like goal, step, step_result, project, etc.
        registry: HookRegistry to use. If None, loads default (and only runs enabled hooks).
        adapter:  LLMAdapter for reviewer/coordinator/notification hooks.
        dry_run:  If True, skip LLM calls (return PASS), still run script/reporter hooks.
        fire_on:  If given, only run hooks with matching fire_on value.

    Returns:
        List of HookResult (one per hook that was eligible to run).
    """
    if registry is None:
        try:
            registry = load_registry()
        except Exception:
            return []

    hooks = registry.get_hooks_for_scope(scope)
    if fire_on is not None:
        hooks = [h for h in hooks if h.fire_on == fire_on]

    results: List[HookResult] = []
    for hook in hooks:
        result = _run_single_hook(hook, context, adapter=adapter, dry_run=dry_run)
        results.append(result)

    return results


def _run_single_hook(
    hook: Hook,
    context: Dict[str, Any],
    adapter=None,
    dry_run: bool = False,
) -> HookResult:
    """Execute one hook. Never raises — all errors → status='skipped'."""
    started = time.monotonic()
    try:
        if hook.hook_type == TYPE_REVIEWER:
            result = _run_reviewer(hook, context, adapter=adapter, dry_run=dry_run)
        elif hook.hook_type == TYPE_REPORTER:
            result = _run_reporter(hook, context)
        elif hook.hook_type == TYPE_COORDINATOR:
            result = _run_coordinator(hook, context, adapter=adapter, dry_run=dry_run)
        elif hook.hook_type == TYPE_SCRIPT:
            result = _run_script(hook, context)
        elif hook.hook_type == TYPE_NOTIFICATION:
            result = _run_notification(hook, context, dry_run=dry_run)
        else:
            result = HookResult(
                hook_id=hook.id,
                hook_name=hook.name,
                hook_type=hook.hook_type,
                scope=hook.scope,
                status="skipped",
                output=f"unknown hook_type={hook.hook_type!r}",
            )
    except Exception as exc:
        result = HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="skipped",
            output=f"hook execution error: {exc}",
        )
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


# ---------------------------------------------------------------------------
# Per-type runners
# ---------------------------------------------------------------------------

def _render_template(template: str, context: Dict[str, Any]) -> str:
    """Render a prompt template with context values. Missing keys → empty string."""
    try:
        return template.format_map(_SafeDict(context))
    except Exception:
        return template


class _SafeDict(dict):
    """dict subclass that returns empty string for missing keys."""
    def __missing__(self, key: str) -> str:
        return ""


def _run_reviewer(
    hook: Hook,
    context: Dict[str, Any],
    adapter=None,
    dry_run: bool = False,
) -> HookResult:
    if dry_run or adapter is None:
        return HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="passed",
            output="[dry-run] PASS: auto-passed",
            should_block=False,
        )

    from llm import LLMMessage, build_adapter, MODEL_CHEAP, MODEL_MID, MODEL_POWER
    _MODEL_MAP = {"cheap": MODEL_CHEAP, "mid": MODEL_MID, "power": MODEL_POWER}
    model_key = _MODEL_MAP.get(hook.model, MODEL_CHEAP)

    review_adapter = adapter
    prompt = _render_template(hook.prompt_template, context)
    try:
        resp = review_adapter.complete(
            [LLMMessage("user", prompt)],
            max_tokens=512,
            temperature=0.1,
        )
        output = resp.content.strip()
    except Exception as exc:
        return HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="skipped",
            output=f"LLM call failed: {exc}",
        )

    upper = output.upper()
    should_block = "BLOCK" in upper or "FAIL" in upper
    status = "failed" if should_block else "passed"
    return HookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        hook_type=hook.hook_type,
        scope=hook.scope,
        status=status,
        output=output,
        should_block=should_block,
    )


def _run_reporter(
    hook: Hook,
    context: Dict[str, Any],
) -> HookResult:
    message = _render_template(hook.prompt_template, context)
    target = hook.report_target

    if target in ("telegram", "both"):
        try:
            import orch as _orch
            notif_path = _orch.orch_root() / "memory" / "hook-notifications.jsonl"
            notif_path.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps({"hook_id": hook.id, "scope": hook.scope, "message": message})
            with open(notif_path, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
        except Exception:
            pass  # Telegram reporter failures are non-fatal

    if target in ("log", "both"):
        try:
            import orch as _orch
            log_path = _orch.orch_root() / "memory" / "hook-log.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps({"hook_id": hook.id, "scope": hook.scope, "message": message})
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
        except Exception:
            pass

    return HookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        hook_type=hook.hook_type,
        scope=hook.scope,
        status="notification_sent",
        output=message,
        should_block=False,
    )


def _run_coordinator(
    hook: Hook,
    context: Dict[str, Any],
    adapter=None,
    dry_run: bool = False,
) -> HookResult:
    if dry_run or adapter is None:
        return HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="passed",
            output="[dry-run] coordinator: continue",
            injected_context="",
        )

    from llm import LLMMessage

    prompt = _render_template(hook.prompt_template, context)
    try:
        resp = adapter.complete(
            [LLMMessage("user", prompt)],
            max_tokens=512,
            temperature=0.2,
        )
        output = resp.content.strip()
    except Exception as exc:
        return HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="skipped",
            output=f"LLM call failed: {exc}",
        )

    return HookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        hook_type=hook.hook_type,
        scope=hook.scope,
        status="passed",
        output=output,
        injected_context=output,
    )


def _run_script(
    hook: Hook,
    context: Dict[str, Any],
) -> HookResult:
    cmd = _render_template(hook.command_template, context)
    if not cmd.strip():
        return HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="skipped",
            output="empty command_template",
        )

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = (proc.stdout + proc.stderr).strip()
        output = raw[:500]
        status = "passed" if proc.returncode == 0 else "failed"
    except Exception as exc:
        return HookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            hook_type=hook.hook_type,
            scope=hook.scope,
            status="skipped",
            output=f"script error: {exc}",
        )

    return HookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        hook_type=hook.hook_type,
        scope=hook.scope,
        status=status,
        output=output,
        should_block=False,  # script hooks are non-blocking
    )


def _run_notification(
    hook: Hook,
    context: Dict[str, Any],
    dry_run: bool = False,
) -> HookResult:
    """Factory System Notifications pattern: inject guidance mid-run."""
    injected = _render_template(hook.prompt_template, context)
    return HookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        hook_type=hook.hook_type,
        scope=hook.scope,
        status="notification_sent",
        output=injected,
        injected_context=injected,
        should_block=False,
    )


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def any_blocking(results: List[HookResult]) -> bool:
    """Return True if any result has should_block=True."""
    return any(r.should_block for r in results)


def get_injected_context(results: List[HookResult]) -> str:
    """Collect all injected_context strings from notification/coordinator hooks."""
    parts = [r.injected_context for r in results if r.injected_context]
    return "\n\n".join(parts)


def format_hook_results(results: List[HookResult]) -> str:
    """Human-readable summary of hook results."""
    if not results:
        return "(no hooks ran)"
    lines = []
    for r in results:
        block_flag = " [BLOCKING]" if r.should_block else ""
        lines.append(
            f"  [{r.status:20s}] {r.hook_name!r} ({r.hook_type}) scope={r.scope}{block_flag}"
        )
        if r.output:
            lines.append(f"           output: {r.output[:120]}")
    return "\n".join(lines)
