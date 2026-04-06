"""TeamCreateTool — model-directed dynamic worker creation.

Phase N: Allows the agent to spin up specialist workers mid-step rather than
relying solely on plan-time ticket dispatch. The LLM chooses the role and task
at runtime based on what it has discovered.

Pattern:
    agent calls create_team_worker(role="market-analyst", task="analyze X")
    → specialist worker runs immediately with a custom persona
    → result injected back into agent's conversation as a tool result
    → agent synthesizes and calls complete_step

Roles can be any free-form string. Known standard roles get richer personas;
unknown roles get a generic specialist persona built from the role name.
"""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.team")

# ---------------------------------------------------------------------------
# Known role → persona fragment mappings
# ---------------------------------------------------------------------------

_ROLE_PERSONAS: Dict[str, str] = {
    "market-analyst": (
        "You are a Market Analyst. Your job: evaluate market data, identify trends, "
        "and surface actionable insights. Ground claims in evidence; flag uncertainty. "
        "Be concrete — give numbers, percentages, time ranges where possible."
    ),
    "risk-auditor": (
        "You are a Risk Auditor. Your job: identify failure modes, edge cases, and "
        "unvalidated assumptions. For every claim, ask: what could go wrong? What "
        "evidence is missing? Flag HIGH/MEDIUM/LOW risk items explicitly."
    ),
    "data-extractor": (
        "You are a Data Extractor. Your job: pull structured facts from raw input. "
        "Output clean, minimal JSON or bullet lists. No commentary. No filler. "
        "If data is ambiguous, surface both interpretations."
    ),
    "devil-advocate": (
        "You are a Devil's Advocate. Your job: steelman the opposing view. Find the "
        "best argument against the current position. Be specific — not 'this might be "
        "wrong' but 'this is wrong because X, and here is evidence Y.'"
    ),
    "synthesizer": (
        "You are a Synthesizer. Your job: merge multiple inputs into a coherent, "
        "non-redundant summary. Resolve contradictions. Preserve key evidence. "
        "Output: a single clear conclusion plus supporting points."
    ),
    "fact-checker": (
        "You are a Fact Checker. Your job: verify specific claims. For each claim, "
        "rate: VERIFIED / PLAUSIBLE / UNVERIFIED / FALSE. Cite the basis for each "
        "rating. Do not speculate beyond what the evidence supports."
    ),
    "strategist": (
        "You are a Strategist. Your job: translate findings into concrete next actions. "
        "For each insight, ask: so what? What should change? Rank recommendations by "
        "impact and feasibility."
    ),
    "domain-skeptic": (
        "You are a Domain Skeptic. Your job: challenge domain-specific assumptions. "
        "Flag where conventional wisdom may be wrong, where context is being ignored, "
        "and where the analysis is too narrow. Ask the uncomfortable questions."
    ),
}

_GENERIC_PERSONA_TEMPLATE = textwrap.dedent("""\
    You are a specialist worker with the role: {role}.
    Your job: complete the assigned task with precision and brevity.
    Core traits:
    - Focused: address only what was asked.
    - Evidence-grounded: tie claims to evidence where possible.
    - Concise: under 400 tokens. No filler.
    Call deliver_result when done.
""").strip()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TeamResult:
    """Result from a dynamically-created team worker."""
    role: str
    task: str
    status: str           # "done" | "blocked"
    result: str
    stuck_reason: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0


# ---------------------------------------------------------------------------
# Subagent context firewall (systematicls harness steal)
# ---------------------------------------------------------------------------

def firewall_shared_ctx(
    task: str,
    shared_ctx: Dict[str, Any],
    *,
    max_entries: int = 5,
    max_chars_per_entry: int = 200,
) -> Dict[str, str]:
    """Filter shared_ctx to the most task-relevant entries (subagent context firewall).

    Team workers should receive only context they actually need — not the full
    accumulated history of the parent loop. This prevents context contamination
    and reduces tokens passed to sub-loops at depth ≥ 1.

    Strategy: simple word-overlap relevance ranking (no deps). Each shared_ctx
    entry is scored by how many unique task words appear in its key or value.
    Top-ranked entries are returned, capped at max_chars_per_entry each.

    Args:
        task:                The specific task for this worker.
        shared_ctx:          Full shared context dict from the parent loop.
        max_entries:         Maximum number of entries to pass to the worker.
        max_chars_per_entry: Cap on each entry's value before passing.

    Returns:
        Filtered dict with the most relevant entries (truncated values).
    """
    if not shared_ctx:
        return {}

    def _tok(text: str) -> set:
        return set(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())

    stop = {"the", "a", "an", "and", "or", "for", "to", "in", "of", "is", "it", "this", "that", "step"}
    task_words = _tok(task) - stop

    scored: List[tuple] = []
    for k, v in shared_ctx.items():
        v_str = str(v)[:max_chars_per_entry]
        entry_words = _tok(k + " " + v_str)
        overlap = len(task_words & entry_words)
        scored.append((overlap, k, v_str))

    # Sort descending by overlap, then by insertion order (chronological for ties)
    scored.sort(key=lambda x: -x[0])
    return {k: v for _, k, v in scored[:max_entries]}


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def _build_persona(role: str, persona_override: Optional[str]) -> str:
    """Return system prompt for a worker role."""
    if persona_override:
        return persona_override.strip()
    known = _ROLE_PERSONAS.get(role.lower().replace("_", "-"))
    if known:
        return known + "\nCall deliver_result when done."
    return _GENERIC_PERSONA_TEMPLATE.format(role=role)


def create_team_worker(
    role: str,
    task: str,
    *,
    persona: Optional[str] = None,
    adapter=None,
    dry_run: bool = False,
    shared_ctx: Optional[Dict[str, Any]] = None,
) -> TeamResult:
    """Spin up a specialist worker with the given role and task.

    Args:
        role: Free-form role name (e.g. "market-analyst", "risk-auditor").
        task: The specific task for this worker to execute.
        persona: Optional custom system prompt override.
        adapter: LLMAdapter to use.
        dry_run: Return stub result without LLM call.

    Returns:
        TeamResult with the worker's output.
    """
    if dry_run or adapter is None:
        log.info("team.create_worker dry_run role=%r task=%r", role, task[:60])
        return TeamResult(
            role=role,
            task=task,
            status="done",
            result=f"[dry-run:{role}] {task[:100]}",
        )

    persona_text = _build_persona(role, persona)
    log.info("team.create_worker role=%r task=%r", role, task[:60])

    # Reuse workers._WORKER_TOOLS for consistent deliver_result / flag_blocked interface
    try:
        from llm import LLMMessage, LLMTool
        from workers import _WORKER_TOOLS

        tools = [LLMTool(**t) for t in _WORKER_TOOLS]
        _shared_block = ""
        if shared_ctx:
            # Subagent context firewall: pass only task-relevant entries, not full history
            _filtered_ctx = firewall_shared_ctx(task, shared_ctx, max_entries=5, max_chars_per_entry=200)
            _entries = [f"  [{k}]: {v}" for k, v in _filtered_ctx.items()]
            if _entries:
                _shared_block = "\n\nRelevant context from prior steps:\n" + "\n".join(_entries)
        user_msg = f"Ticket: {task}{_shared_block}\n\nComplete this ticket. Call deliver_result when done."

        resp = adapter.complete(
            [
                LLMMessage("system", persona_text),
                LLMMessage("user", user_msg),
            ],
            tools=tools,
            tool_choice="required",
            max_tokens=2048,
            temperature=0.3,
        )
    except Exception as exc:
        log.warning("team.create_worker failed role=%r: %s", role, exc)
        return TeamResult(
            role=role,
            task=task,
            status="blocked",
            result="",
            stuck_reason=f"LLM call failed: {exc}",
        )

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "deliver_result":
            return TeamResult(
                role=role,
                task=task,
                status="done",
                result=tc.arguments.get("result", resp.content),
                tokens_in=resp.input_tokens,
                tokens_out=resp.output_tokens,
            )
        elif tc.name == "flag_blocked":
            return TeamResult(
                role=role,
                task=task,
                status="blocked",
                result=tc.arguments.get("partial", ""),
                stuck_reason=tc.arguments.get("reason", "unknown"),
                tokens_in=resp.input_tokens,
                tokens_out=resp.output_tokens,
            )

    # Fallback: treat content as result
    if resp.content and len(resp.content) > 20:
        return TeamResult(
            role=role,
            task=task,
            status="done",
            result=resp.content,
            tokens_in=resp.input_tokens,
            tokens_out=resp.output_tokens,
        )

    return TeamResult(
        role=role,
        task=task,
        status="blocked",
        result="",
        stuck_reason="Worker produced no useful output",
    )


# ---------------------------------------------------------------------------
# Utility: format team result for injection into conversation
# ---------------------------------------------------------------------------

def format_team_result_for_injection(result: TeamResult) -> str:
    """Format a TeamResult for injection as a tool-result message."""
    if result.status == "done":
        return (
            f"[team-worker:{result.role}] Task: {result.task}\n"
            f"Result:\n{result.result}"
        )
    return (
        f"[team-worker:{result.role}] Task: {result.task}\n"
        f"Status: BLOCKED — {result.stuck_reason or 'unknown reason'}\n"
        f"Partial: {result.result or '(none)'}"
    )
