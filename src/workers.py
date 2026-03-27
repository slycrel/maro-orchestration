#!/usr/bin/env python3
"""Worker registry for Poe's Director/Worker hierarchy (Phase 3).

Workers are named execution roles, each with:
- A persona system prompt (loaded from personas/ or inline)
- A constrained toolset (task-appropriate)
- A worker type: research | build | ops | general

Workers receive a ticket (task + context) and produce an artifact (text result).
They do NOT plan or review — that's the Director's job.

Usage:
    from workers import dispatch_worker, WORKER_TYPES
    result = dispatch_worker("research", "analyze polymarket resolution patterns", adapter=adapter)
    print(result.result)
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Worker type constants
# ---------------------------------------------------------------------------

WORKER_RESEARCH = "research"
WORKER_BUILD    = "build"
WORKER_OPS      = "ops"
WORKER_GENERAL  = "general"

WORKER_TYPES = (WORKER_RESEARCH, WORKER_BUILD, WORKER_OPS, WORKER_GENERAL)


# ---------------------------------------------------------------------------
# Worker result
# ---------------------------------------------------------------------------

@dataclass
class WorkerResult:
    worker_type: str
    ticket: str
    status: str           # "done" | "blocked"
    result: str
    stuck_reason: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0


# ---------------------------------------------------------------------------
# Inline persona prompts (fallback if file not found)
# ---------------------------------------------------------------------------

_PERSONA_RESEARCH = textwrap.dedent("""\
    You are a Research Worker for Poe, an autonomous agent system.
    Your job: answer research questions with source-grounded, high-signal output.
    Core traits:
    - Context-first: understand the full task before researching.
    - Multi-angle: pursue multiple hypotheses, not one narrative.
    - Source-grounded: tie claims to sources; mark uncertainty explicitly.
    - Synthesis over paste: compress and merge, don't just copy.
    Deliver: structured findings with cited evidence and a clear "so what" conclusion.
    You are a WORKER — do not plan or review. Execute the ticket and produce output.
""").strip()

_PERSONA_BUILD = textwrap.dedent("""\
    You are a Build Worker for Poe, an autonomous agent system.
    Your job: implement code, scripts, configs, or structured artifacts.
    Core traits:
    - Implementation-first: produce working output, not plans.
    - Minimal and correct: write only what's needed, avoid over-engineering.
    - Documented: include inline comments for non-obvious logic.
    - Testable: structure code so it can be verified.
    You are a WORKER — do not plan or review. Execute the ticket and produce output.
""").strip()

_PERSONA_OPS = textwrap.dedent("""\
    You are an Ops Worker for Poe, an autonomous agent system.
    Your job: handle automation, diagnostics, infrastructure, and system tasks.
    Core traits:
    - Safety-first: verify before executing; flag irreversible actions.
    - Diagnostic: explain what you observe and why actions are safe.
    - Idempotent: prefer operations that can be safely retried.
    - Documented: log what was changed and why.
    You are a WORKER — do not plan or review. Execute the ticket and produce output.
""").strip()

_PERSONA_GENERAL = textwrap.dedent("""\
    You are a General Worker for Poe, an autonomous agent system.
    Your job: complete tasks that don't fit the specialist roles.
    Core traits:
    - Direct: produce the requested output without hedging.
    - Complete: finish the whole task, not just part of it.
    - Concise: say what needs to be said, nothing more.
    You are a WORKER — do not plan or review. Execute the ticket and produce output.
""").strip()

_INLINE_PERSONAS: Dict[str, str] = {
    WORKER_RESEARCH: _PERSONA_RESEARCH,
    WORKER_BUILD:    _PERSONA_BUILD,
    WORKER_OPS:      _PERSONA_OPS,
    WORKER_GENERAL:  _PERSONA_GENERAL,
}

# Map from worker type to persona file name (relative to personas/ dir)
_PERSONA_FILES: Dict[str, str] = {
    WORKER_RESEARCH: "research-assistant-deep-synth.md",
    WORKER_BUILD:    None,  # no file — use inline
    WORKER_OPS:      None,
    WORKER_GENERAL:  None,
}


# ---------------------------------------------------------------------------
# Persona loading
# ---------------------------------------------------------------------------

def _load_persona(worker_type: str) -> str:
    """Load persona text for a worker type.

    Resolution order:
    1. PersonaRegistry (uses all 18+ personas/ files including builder, ops, finance-analyst, etc.)
    2. _PERSONA_FILES path lookup (legacy explicit file mapping)
    3. _INLINE_PERSONAS fallback (hardcoded prompts)
    """
    # 1. Try PersonaRegistry — gives access to the full personas/ directory
    try:
        from persona import PersonaRegistry, build_persona_system_prompt
        _registry = PersonaRegistry()
        _spec = _registry.load(worker_type)
        if _spec is not None:
            return build_persona_system_prompt(_spec)
    except Exception:
        pass

    # 2. Explicit file map (legacy)
    fname = _PERSONA_FILES.get(worker_type)
    if fname:
        candidates = [
            Path(__file__).parent.parent / "personas" / fname,
        ]
        try:
            import os
            ws = os.environ.get("OPENCLAW_WORKSPACE")
            if ws:
                candidates.append(Path(ws) / "prototypes" / "poe-orchestration" / "personas" / fname)
        except Exception:
            pass

        for p in candidates:
            if p.exists():
                return p.read_text(encoding="utf-8").strip()

    # 3. Inline fallback
    return _INLINE_PERSONAS.get(worker_type, _PERSONA_GENERAL)


# ---------------------------------------------------------------------------
# Worker execution tools
# ---------------------------------------------------------------------------

_WORKER_TOOLS = [
    {
        "name": "deliver_result",
        "description": "Deliver the completed work product for this ticket.",
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The complete work product, findings, or artifact.",
                },
                "summary": {
                    "type": "string",
                    "description": "One-sentence summary of what was produced.",
                },
            },
            "required": ["result", "summary"],
        },
    },
    {
        "name": "flag_blocked",
        "description": "Signal that this ticket cannot be completed.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this ticket cannot be completed.",
                },
                "partial": {
                    "type": "string",
                    "description": "Any partial work completed before getting blocked.",
                },
            },
            "required": ["reason"],
        },
    },
]


# ---------------------------------------------------------------------------
# Core dispatch function
# ---------------------------------------------------------------------------

def dispatch_worker(
    worker_type: str,
    ticket: str,
    *,
    context: str = "",
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> WorkerResult:
    """Dispatch a ticket to the appropriate worker.

    Args:
        worker_type: One of WORKER_TYPES.
        ticket: The task description for this worker.
        context: Additional context (directive, previous steps, etc.).
        adapter: LLMAdapter instance.
        dry_run: Use stub responses without API calls.
        verbose: Print progress.

    Returns:
        WorkerResult with the worker's output.
    """
    from llm import LLMMessage, LLMTool

    if worker_type not in WORKER_TYPES:
        worker_type = WORKER_GENERAL

    if dry_run or adapter is None:
        return _dry_run_worker(worker_type, ticket)

    if verbose:
        import sys
        print(f"[poe:worker:{worker_type}] ticket={ticket[:60]!r}", file=sys.stderr, flush=True)

    persona = _load_persona(worker_type)
    tools = [LLMTool(**t) for t in _WORKER_TOOLS]

    context_block = f"\n\nContext:\n{context}" if context else ""
    user_msg = f"Ticket: {ticket}{context_block}\n\nComplete this ticket. Call deliver_result when done."

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", persona),
                LLMMessage("user", user_msg),
            ],
            tools=tools,
            tool_choice="required",
            max_tokens=4096,
            temperature=0.3,
        )
    except Exception as exc:
        return WorkerResult(
            worker_type=worker_type,
            ticket=ticket,
            status="blocked",
            result="",
            stuck_reason=f"LLM call failed: {exc}",
        )

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "deliver_result":
            return WorkerResult(
                worker_type=worker_type,
                ticket=ticket,
                status="done",
                result=tc.arguments.get("result", resp.content),
                tokens_in=resp.input_tokens,
                tokens_out=resp.output_tokens,
            )
        elif tc.name == "flag_blocked":
            return WorkerResult(
                worker_type=worker_type,
                ticket=ticket,
                status="blocked",
                result=tc.arguments.get("partial", ""),
                stuck_reason=tc.arguments.get("reason", "unknown"),
                tokens_in=resp.input_tokens,
                tokens_out=resp.output_tokens,
            )

    # Fallback: treat content as result
    if resp.content and len(resp.content) > 20:
        return WorkerResult(
            worker_type=worker_type,
            ticket=ticket,
            status="done",
            result=resp.content,
            tokens_in=resp.input_tokens,
            tokens_out=resp.output_tokens,
        )

    return WorkerResult(
        worker_type=worker_type,
        ticket=ticket,
        status="blocked",
        result=resp.content,
        stuck_reason="Worker produced no useful output",
    )


def _dry_run_worker(worker_type: str, ticket: str) -> WorkerResult:
    """Stub worker result for dry-run/testing."""
    return WorkerResult(
        worker_type=worker_type,
        ticket=ticket,
        status="done",
        result=f"[dry-run:{worker_type}] Completed: {ticket[:80]}",
        tokens_in=60,
        tokens_out=40,
    )


# ---------------------------------------------------------------------------
# Worker type inference
# ---------------------------------------------------------------------------

_RESEARCH_KEYWORDS = {"research", "analyze", "investigate", "study", "find", "search", "look up", "review"}
_BUILD_KEYWORDS = {"build", "implement", "write", "create", "code", "develop", "generate code", "script"}
_OPS_KEYWORDS = {"deploy", "monitor", "run", "execute", "configure", "set up", "install", "check status", "debug"}


def infer_worker_type(ticket: str) -> str:
    """Infer the best worker type for a ticket based on keywords."""
    lower = ticket.lower()
    words = set(lower.split())

    research_score = sum(1 for k in _RESEARCH_KEYWORDS if k in lower)
    build_score = sum(1 for k in _BUILD_KEYWORDS if k in lower)
    ops_score = sum(1 for k in _OPS_KEYWORDS if k in lower)

    best = max(research_score, build_score, ops_score)
    if best == 0:
        return WORKER_GENERAL
    if research_score == best:
        return WORKER_RESEARCH
    if build_score == best:
        return WORKER_BUILD
    return WORKER_OPS


# ---------------------------------------------------------------------------
# Crew composition (Phase 8)
# ---------------------------------------------------------------------------

_SIMPLE_KEYWORDS = {"quick", "simple", "brief"}
_COMPREHENSIVE_KEYWORDS = {"comprehensive", "full", "complete", "detailed"}
_EXHAUSTIVE_KEYWORDS = {"exhaustive", "thorough", "everything"}


def infer_crew_size(directive: str) -> int:
    """Infer optimal crew size (1-4) based on directive complexity.

    Rules:
        1 worker: short directive (<10 words) or contains quick/simple/brief
        2 workers: medium complexity (10-25 words)
        3 workers: contains comprehensive/full/complete/detailed or >25 words
        4 workers: contains exhaustive/thorough/everything or >50 words
    """
    lower = directive.lower()
    words = directive.split()
    word_count = len(words)

    # Check keyword overrides first (highest tier wins)
    if any(kw in lower for kw in _EXHAUSTIVE_KEYWORDS) or word_count > 50:
        return 4

    if any(kw in lower for kw in _COMPREHENSIVE_KEYWORDS) or word_count > 25:
        return 3

    if any(kw in lower for kw in _SIMPLE_KEYWORDS) or word_count < 10:
        return 1

    # Default: medium complexity
    return 2
