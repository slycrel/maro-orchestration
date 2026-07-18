#!/usr/bin/env python3
"""Phase 3: Director agent for Maro's orchestration hierarchy.

The Director:
- Takes a directive (high-level goal or task)
- Produces a SPEC (plan + worker tickets)
- Dispatches tickets to specialized workers
- Reviews worker output and accepts or requests revision
- Compiles a final polished report for the Handle to relay

Director contract (from spec):
- Plans and reviews. Does NOT execute.
- plan_acceptance modes:
    "explicit" — public/irreversible actions require explicit gate
    "inferred" — low-risk/reversible proceed automatically
- Reviews up to MAX_REVIEW_ROUNDS times before accepting or escalating
- Checkpoints after each major phase

Usage:
    from director import run_director
    result = run_director("research winning polymarket strategies", adapter=adapter)
    print(result.report)

CLI:
    orch maro-director "your directive here" [--dry-run]
"""

from __future__ import annotations

import json
import logging
import random
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

from ancestry import Origin

if TYPE_CHECKING:
    from conversation import ConversationChannel

log = logging.getLogger("maro.director")

from workers import WorkerResult, dispatch_worker, infer_worker_type, WORKER_TYPES
from llm_parse import extract_json, safe_float, safe_str, safe_list, content_or_empty
from planner import _is_large_scope_review
from config import get as config_get
from captains_log import log_event

MAX_REVIEW_ROUNDS = 2  # Director reviews each worker output up to this many times

# ---------------------------------------------------------------------------
# Plan acceptance
# ---------------------------------------------------------------------------

_EXPLICIT_TRIGGERS = {
    "post", "tweet", "publish", "send", "email", "delete", "remove",
    "deploy", "push to production", "merge to main", "drop", "wipe",
    "transfer", "pay", "purchase", "buy", "sell", "execute trade",
}


def requires_explicit_acceptance(directive: str) -> bool:
    """Return True if this directive requires explicit user confirmation."""
    lower = directive.lower()
    return any(trigger in lower for trigger in _EXPLICIT_TRIGGERS)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    """A unit of work dispatched to a worker."""
    ticket_id: str
    worker_type: str
    task: str
    context: str = ""
    revision_of: Optional[str] = None   # ticket_id this is a revision of


@dataclass
class ReviewDecision:
    accepted: bool
    reason: str
    revision_request: Optional[str] = None  # if not accepted, what to redo


@dataclass
class DirectorResult:
    director_id: str
    directive: str
    plan_acceptance: str              # "explicit" | "inferred"
    status: str                       # "done" | "stuck" | "needs_approval"
    spec: str                         # Director's plan text
    tickets: List[Ticket]
    worker_results: List[WorkerResult]
    review_decisions: List[ReviewDecision]
    report: str                       # Final polished output
    project: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    log_path: Optional[str] = None
    worker_slice: bool = False  # was memory.worker_slice active for this run? (default-on since 2026-07-08)

    def summary(self) -> str:
        done = sum(1 for r in self.worker_results if r.status == "done")
        lines = [
            f"director_id={self.director_id}",
            f"directive={self.directive!r}",
            f"plan_acceptance={self.plan_acceptance}",
            f"status={self.status}",
            f"tickets={len(self.tickets)} workers_done={done}/{len(self.worker_results)}",
            f"tokens={self.tokens_in}in+{self.tokens_out}out elapsed_ms={self.elapsed_ms}",
        ]
        if self.log_path:
            lines.append(f"log={self.log_path}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Director prompts
# ---------------------------------------------------------------------------

_SPEC_SYSTEM = textwrap.dedent("""\
    You are the Director for Maro, an autonomous orchestration system.
    Your job: take a directive and produce a structured work plan.
    You PLAN and REVIEW. You do NOT execute.

    Worker types available:
    - research: information gathering, analysis, synthesis
    - build: code, scripts, configurations, structured artifacts
    - ops: infrastructure, automation, diagnostics, system tasks
    - general: everything else

    Respond with a JSON object:
    {
      "spec": "one paragraph describing the overall approach",
      "tickets": [
        {"worker_type": "research|build|ops|general", "task": "specific task for this worker"}
      ]
    }

    Rules:
    - 1-4 tickets maximum. Each must be independently executable.
    - Worker tickets must be concrete and specific (not vague meta-tasks).
    - Order tickets so each one can use previous results as context.
    - Pick the right worker type for each ticket.
    - Take a position on scope and approach. If the directive is ambiguous, name the
      assumption you're making rather than hedging. State what would change your plan.
""").strip()

_REVIEW_SYSTEM = textwrap.dedent("""\
    You are the Director reviewing a worker's output.
    Your job: decide whether the output meets the requirements.
    Accept if it's complete, relevant, and useful.
    Reject ONLY if it's clearly incomplete, off-topic, or failed.

    Respond with a JSON object:
    {
      "accepted": true or false,
      "reason": "one sentence",
      "revision_request": "specific request if rejected, null if accepted"
    }
""").strip()

_COMPILE_SYSTEM = textwrap.dedent("""\
    You are the Director compiling a final report for Maro's Handle.
    Synthesize the worker outputs into a polished, structured report.
    The report will be relayed to the user (Jeremy) — make it direct and useful.
    Lead with the key findings/deliverables. Include relevant details.
    No hedging. No "I" statements. Just the work product.
""").strip()

_CHALLENGER_SYSTEM = textwrap.dedent("""\
    You are a skeptical plan reviewer. Your job: challenge a proposed work plan
    before it is locked in. Find gaps, risks, and wrong assumptions.

    Given a directive and a proposed plan, identify 2-3 specific failure modes:
    - Steps that are vague, unverifiable, or assume access not guaranteed
    - Missing steps that are obviously needed to achieve the goal
    - Steps that will produce noise, not signal (e.g. raw dumps instead of insights)

    Respond with a JSON object:
    {
      "critiques": ["specific issue 1", "specific issue 2"],
      "revised_spec": "one paragraph: the improved approach that addresses these critiques"
    }

    Be specific. If the plan is solid, say so briefly and keep revised_spec identical.
""").strip()


# ---------------------------------------------------------------------------
# Skip-Director experiment: complexity classifier
# ---------------------------------------------------------------------------

# Keywords that indicate multi-step coordination — Director adds value here.
_COMPLEX_KEYWORDS = frozenset({
    "and then", "after that", "coordinate", "for each", "pipeline",
    "phase 1", "phase 2", "multiple", "sequence", "in order to",
    "followed by", "then ", "first ", "second ", "finally ",
    "compare across", "across all", "synthesize", "orchestrate",
    "build and test", "fetch and", "analyze and", "research and",
})

# Keywords that signal a goal that definitely needs the Director (high complexity).
_DEFINITELY_COMPLEX = frozenset({
    "mission", "milestone", "roadmap", "multi-day", "long-term",
    "design system", "architecture", "refactor", "deploy", "release",
})

# _is_large_scope_review is imported from planner.py — this used to be a
# local copy of the keyword set that drifted out of sync with planner's.

# Spec system prompt for large-scope reviews — raises ticket cap to 6 and
# instructs domain-area splitting so each worker handles a bounded slice.
_LARGE_SCOPE_SPEC_SYSTEM = textwrap.dedent("""\
    You are the Director for Maro, an autonomous orchestration system.
    Your job: take a large-scope review directive and produce a staged work plan.
    You PLAN and REVIEW. You do NOT execute.

    The goal is too large to complete in a single pass. Split it into 4-6 domain-area
    worker tickets, each covering a bounded, independently-reviewable slice.
    Order tickets so a final synthesis ticket can draw on all prior results.

    Worker types available:
    - research: information gathering, analysis, synthesis
    - build: code, scripts, configurations, structured artifacts
    - ops: infrastructure, automation, diagnostics, system tasks
    - general: everything else

    Respond with a JSON object:
    {
      "spec": "one paragraph describing the staged approach",
      "tickets": [
        {"worker_type": "research|build|ops|general", "task": "specific bounded task for this domain area"}
      ]
    }

    Rules:
    - 4-6 tickets. Each covers one domain area (e.g. docs/architecture, core execution, tests, integrations, security).
    - Last ticket is always synthesis: "Compile findings from all prior passes into a structured report with severity ratings."
    - Each ticket must be independently executable with a bounded file/scope set.
    - Concrete file names or module areas are better than vague descriptions.
    - Pick the right worker type for each ticket.
    - Take a position on which domain areas matter most. Don't produce equal-weight coverage
      if the directive implies a specific concern — lead with the riskiest area.
    - State what evidence would change the staged decomposition (e.g. if the repo is small,
      fewer passes; if security is the stated concern, security gets its own early ticket).
""").strip()


def _is_simple_directive(directive: str) -> bool:
    """Return True if the directive is simple enough to skip the Director.

    Simple = single-scope, ≤ 12 words, no multi-step coordination.
    The Director adds overhead (SPEC + challenge + dispatch + review) that's
    wasted when the goal is already clear and bounded.

    Used by run_director(skip_if_simple=True) for the Skip-Director experiment.
    """
    lower = directive.lower().strip()

    # Long directives imply complexity
    word_count = len(lower.split())
    if word_count > 15:
        return False

    # Explicit complexity signals
    if any(kw in lower for kw in _DEFINITELY_COMPLEX):
        return False

    # Large-scope reviews always need the Director for staged-pass routing
    if _is_large_scope_review(lower):
        return False

    # Multi-step coordination signals
    if any(kw in lower for kw in _COMPLEX_KEYWORDS):
        return False

    # Multiple sentences = likely multi-step
    if lower.count(".") >= 2 or lower.count(";") >= 1:
        return False

    return True


# ---------------------------------------------------------------------------
# Core director function
# ---------------------------------------------------------------------------

def run_director(
    directive: str,
    *,
    project: Optional[str] = None,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
    skip_if_simple: bool = False,
    run_dir: Optional[Path] = None,
    thread_id: Optional[str] = None,
) -> DirectorResult:
    """Run the Director on a directive.

    Args:
        directive: High-level task or goal.
        project: Optional project slug to associate work with.
        adapter: LLMAdapter instance.
        dry_run: Simulate without API calls.
        verbose: Print progress to stderr.
        run_dir: Optional run directory for this thread — source of the parent
            goal_brain summary for the worker_slice experiment (memory.worker_slice).
            Unused when the flag is off.
        thread_id: Optional thread handle id — scopes worker_slice memory recall
            to "thread/<id>" instead of global. Unused when the flag is off.

    Returns:
        DirectorResult with plan, worker outputs, and final report.
    """
    from llm import LLMMessage

    director_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()
    log.info("director_start id=%s directive=%r dry_run=%s skip_if_simple=%s",
             director_id, directive[:60], dry_run, skip_if_simple)

    def _log(msg: str):
        if verbose:
            print(f"[maro:director:{director_id}] {msg}", file=sys.stderr, flush=True)

    _log(f"directive={directive!r}")

    # Skip-Director experiment: route simple goals to run_agent_loop directly.
    # Skips SPEC production + challenge + dispatch + review overhead.
    # Controlled by skip_if_simple=True (opt-in, not default).
    if skip_if_simple and _is_simple_directive(directive):
        log.info("director_skip id=%s directive=%r (simple goal → run_agent_loop direct)",
                 director_id, directive[:60])
        _log("skip: simple directive → routing direct to run_agent_loop")
        try:
            from agent_loop import run_agent_loop
            if adapter is None and not dry_run:
                from llm import build_adapter, MODEL_MID
                adapter = build_adapter(model=MODEL_MID)
            loop_result = run_agent_loop(
                directive,
                project=project,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
            )
            elapsed = int((time.monotonic() - started_at) * 1000)
            done_steps = [s for s in loop_result.steps if s.status == "done"]
            report = "\n\n".join(s.result for s in done_steps if s.result) or "[no output]"
            log.info("director_skip_done id=%s loop_status=%s steps=%d elapsed=%dms",
                     director_id, loop_result.status, len(done_steps), elapsed)
            return DirectorResult(
                director_id=director_id,
                directive=directive,
                plan_acceptance="inferred",
                status=loop_result.status,
                spec="[skip-director: routed direct to run_agent_loop]",
                tickets=[],
                worker_results=[],
                review_decisions=[],
                report=report,
                project=loop_result.project,
                tokens_in=loop_result.total_tokens_in,
                tokens_out=loop_result.total_tokens_out,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            log.warning("director_skip failed, falling back to full Director: %s", exc)
            _log(f"skip failed ({exc}), running full Director")
            # Fall through to full Director run

    # Check plan acceptance mode
    acceptance = "explicit" if requires_explicit_acceptance(directive) else "inferred"
    _log(f"plan_acceptance={acceptance}")

    # Build adapter — planner role uses MODEL_POWER for spec production
    if adapter is None and not dry_run:
        from llm import build_adapter
        from conductor import assign_model_by_role
        adapter = build_adapter(model=assign_model_by_role("planner"))

    total_tokens_in = 0
    total_tokens_out = 0

    # Phase 1: Produce SPEC + tickets
    _log("producing spec...")
    spec, tickets, spec_tokens = _produce_spec(directive, adapter, dry_run, _log)
    total_tokens_in += spec_tokens[0]
    total_tokens_out += spec_tokens[1]
    _log(f"spec produced, tickets={len(tickets)}")

    # Phase 1b: Pre-plan challenger — one skeptic critique before locking
    if not dry_run and adapter is not None:
        try:
            spec, challenge_tokens = _challenge_spec(directive, spec, tickets, adapter)
            total_tokens_in += challenge_tokens[0]
            total_tokens_out += challenge_tokens[1]
            _log("pre-plan challenger: spec reviewed")
        except Exception:
            pass  # challenger is non-fatal

    # Phase 2: Dispatch workers + review
    worker_results: List[WorkerResult] = []
    review_decisions: List[ReviewDecision] = []
    completed_context = ""

    # Worker memory slice — DEFAULT ON since 2026-07-08 (§7 A/B verdict, Jeremy's
    # flip: 16 runs, every measure favored the slice or tied; record in
    # docs/history/2026-07-08-worker-slice-ab.md). Set memory.worker_slice: false
    # to disable; the off path stays byte-identical to pre-slice prompts.
    worker_slice_enabled = config_get("memory.worker_slice", True)
    worker_slice_store = None
    worker_thread_scope = f"thread/{thread_id}" if thread_id else ""
    parent_goal_brain = ""
    if worker_slice_enabled:
        try:
            from memory_bridge import ingest_lessons_to_store, recall_for_worker, format_worker_memory_block, stamp_items_with_age
            from memory_sqlite import SqliteMemoryStore
            from memory_bridge import _memory_store_path

            worker_slice_store = SqliteMemoryStore(_memory_store_path())
            ingest_stats = ingest_lessons_to_store(worker_slice_store, verbose=verbose)
            _log(f"memory: ingested {ingest_stats['ingested']} items from {len(ingest_stats['sources'])} sources")
        except Exception as exc:
            log.warning("director: worker_slice ingest failed: %s; continuing without memory", exc)
            worker_slice_enabled = False
            worker_slice_store = None

        if run_dir is not None:
            try:
                import thread_brain as _tb
                parent_goal_brain = _tb.load_thread_brain(run_dir)
            except Exception:
                parent_goal_brain = ""

    for ticket in tickets:
        _log(f"dispatching worker={ticket.worker_type} task={ticket.task[:50]!r}")

        context = completed_context.strip()
        if ticket.context:
            context = ticket.context + ("\n" + context if context else "")

        # A/B: Inject worker memory slice if enabled
        worker_slice_injected = False
        if worker_slice_enabled and worker_slice_store is not None:
            try:
                items = recall_for_worker(ticket.task, thread_scope=worker_thread_scope, k=5, store=worker_slice_store)
                # Time-blindness hook (a): age-stamp recalled items from their
                # stored timestamps (memory.age_stamps; off/no-timestamp paths
                # return the list unchanged — byte-identical injection).
                items, age_stamped = stamp_items_with_age(items)
                if items or parent_goal_brain:
                    memory_block = format_worker_memory_block(items, goal_brain=parent_goal_brain, max_chars=1200)
                    if memory_block:
                        # Prepend memory block to context (highest priority)
                        context = memory_block + ("\n\n" + context if context else "")
                        worker_slice_injected = True
                        _slice_event_ctx = {
                            "ticket_id": ticket.ticket_id,
                            "worker_type": ticket.worker_type,
                            "items_count": len(items),
                            "thread_scope": worker_thread_scope,
                            "goal_brain_included": bool(parent_goal_brain),
                            "memory_block_len": len(memory_block),
                        }
                        # A/B observability: only injections that actually
                        # stamped ages carry the field.
                        if age_stamped:
                            _slice_event_ctx["age_stamped"] = True
                        log_event(
                            "WORKER_SLICE_INJECTED",
                            subject=f"worker:{ticket.worker_type}",
                            summary=f"Injected {len(items)} recalled items",
                            context=_slice_event_ctx,
                        )
            except Exception as exc:
                log.warning("director: worker_slice recall failed for ticket %s: %s", ticket.ticket_id, exc)

        result = dispatch_worker(
            ticket.worker_type,
            ticket.task,
            context=context,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
        )
        result.memory_slice_injected = worker_slice_injected
        total_tokens_in += result.tokens_in
        total_tokens_out += result.tokens_out

        # Spot-check: worker result should reference the requested worker_type
        if result.worker_type != ticket.worker_type:
            log.warning(
                "WorkerResult.worker_type mismatch: expected %r, got %r for ticket=%s",
                ticket.worker_type, result.worker_type, ticket.ticket_id,
            )
        if not result.ticket:
            log.warning(
                "WorkerResult.ticket is empty for ticket=%s worker=%s",
                ticket.ticket_id, ticket.worker_type,
            )

        # Review worker output
        review, rev_tokens = _review_worker_output(
            directive=directive,
            ticket=ticket,
            result=result,
            adapter=adapter,
            dry_run=dry_run,
        )
        total_tokens_in += rev_tokens[0]
        total_tokens_out += rev_tokens[1]
        review_decisions.append(review)

        if not review.accepted and review.revision_request and not dry_run:
            # Request revision (max MAX_REVIEW_ROUNDS attempts)
            for _ in range(MAX_REVIEW_ROUNDS - 1):
                _log(f"requesting revision: {review.revision_request[:60]!r}")
                revised_ticket = Ticket(
                    ticket_id=str(uuid.uuid4())[:8],
                    worker_type=ticket.worker_type,
                    task=f"{ticket.task}\n\nRevision request: {review.revision_request}",
                    context=context,
                    revision_of=ticket.ticket_id,
                )
                result = dispatch_worker(
                    revised_ticket.worker_type,
                    revised_ticket.task,
                    context=context,
                    adapter=adapter,
                    dry_run=dry_run,
                    verbose=verbose,
                )
                result.memory_slice_injected = worker_slice_injected
                total_tokens_in += result.tokens_in
                total_tokens_out += result.tokens_out
                review, rev_tokens = _review_worker_output(
                    directive=directive,
                    ticket=revised_ticket,
                    result=result,
                    adapter=adapter,
                    dry_run=dry_run,
                )
                total_tokens_in += rev_tokens[0]
                total_tokens_out += rev_tokens[1]
                review_decisions.append(review)
                if review.accepted:
                    break
            else:
                # Exhausted MAX_REVIEW_ROUNDS without acceptance.
                # Proceed with best-effort (last revision) rather than blocking.
                log.warning("director review loop exhausted %d rounds for ticket=%s — using best-effort result",
                            MAX_REVIEW_ROUNDS, ticket.ticket_id)
                _log(f"review exhausted ({MAX_REVIEW_ROUNDS} rounds) — best-effort result accepted")

        worker_results.append(result)
        if result.status == "done" and result.result:
            completed_context += f"\n\n[{ticket.worker_type}] {ticket.task}:\n{result.result[:2000]}"

    # Phase 3: Compile final report
    _log("compiling final report...")
    report, compile_tokens = _compile_report(directive, spec, worker_results, adapter, dry_run)
    total_tokens_in += compile_tokens[0]
    total_tokens_out += compile_tokens[1]

    # Determine overall status
    all_done = all(r.status == "done" for r in worker_results)
    status = "done" if all_done else "stuck"

    elapsed = int((time.monotonic() - started_at) * 1000)

    # Write log
    log_path = _write_director_log(
        project=project,
        director_id=director_id,
        directive=directive,
        spec=spec,
        tickets=tickets,
        worker_results=worker_results,
        status=status,
        elapsed_ms=elapsed,
        worker_slice=worker_slice_enabled,
    )

    result = DirectorResult(
        director_id=director_id,
        directive=directive,
        plan_acceptance=acceptance,
        status=status,
        spec=spec,
        tickets=tickets,
        worker_results=worker_results,
        review_decisions=review_decisions,
        report=report,
        project=project,
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        elapsed_ms=elapsed,
        log_path=log_path,
        worker_slice=worker_slice_enabled,
    )

    log.info("director_done id=%s status=%s tickets=%d tokens=%d elapsed=%dms",
             director_id, status, len(tickets), total_tokens_in + total_tokens_out, elapsed)
    _log(f"done: {result.summary()}")
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _produce_spec(
    directive: str,
    adapter,
    dry_run: bool,
    log,
) -> Tuple[str, List[Ticket], Tuple[int, int]]:
    """Ask the Director LLM to produce a spec + tickets."""
    from llm import LLMMessage

    if dry_run or adapter is None:
        tickets = [
            Ticket(
                ticket_id=str(uuid.uuid4())[:8],
                worker_type=infer_worker_type(directive),
                task=f"[dry-run] {directive[:60]}",
            )
        ]
        return (f"[dry-run spec] Plan for: {directive[:80]}", tickets, (0, 0))

    try:
        _large_scope = _is_large_scope_review(directive)
        _spec_system = _LARGE_SCOPE_SPEC_SYSTEM if _large_scope else _SPEC_SYSTEM
        _max_tickets = 6 if _large_scope else 4

        # Inject lat.md knowledge graph nodes relevant to this directive (same TF-IDF
        # pattern as planner.py). Silently skipped if lat.md has no relevant nodes.
        _lat_ctx = ""
        try:
            from lat_inject import inject_relevant_nodes as _lat_inject
            _lat_ctx = _lat_inject(directive)
        except Exception:
            pass
        _user_msg = f"Directive: {directive}"
        if _lat_ctx:
            _user_msg += f"\n\n{_lat_ctx}"

        resp = adapter.complete(
            [
                LLMMessage("system", _spec_system),
                LLMMessage("user", _user_msg),
            ],
            max_tokens=1024,
            temperature=0.2,
            no_tools=True,
            purpose="director spec",
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="director._produce_spec")
        if data:
            spec = safe_str(data.get("spec"))
            raw_tickets = safe_list(data.get("tickets", []), element_type=dict, max_items=_max_tickets)
            tickets = []
            for i, t in enumerate(raw_tickets):
                wtype = t.get("worker_type", WORKER_TYPES[-1])
                if wtype not in WORKER_TYPES:
                    wtype = infer_worker_type(t.get("task", ""))
                tickets.append(Ticket(
                    ticket_id=str(uuid.uuid4())[:8],
                    worker_type=wtype,
                    task=t.get("task", ""),
                ))
            if not tickets:
                tickets = [Ticket(
                    ticket_id=str(uuid.uuid4())[:8],
                    worker_type=infer_worker_type(directive),
                    task=directive,
                )]
            return (spec, tickets, (resp.input_tokens, resp.output_tokens))
    except Exception as exc:
        log(f"spec LLM call failed, using single-ticket fallback: {exc}")

    # Fallback: one ticket for the whole directive
    tickets = [Ticket(
        ticket_id=str(uuid.uuid4())[:8],
        worker_type=infer_worker_type(directive),
        task=directive,
    )]
    return (f"Single-worker fallback for: {directive[:80]}", tickets, (0, 0))


def _challenge_spec(
    directive: str,
    spec: str,
    tickets: List[Ticket],
    adapter,
) -> Tuple[str, Tuple[int, int]]:
    """Run one skeptic critique pass on the proposed spec.

    Returns (revised_spec, token_counts). On any failure returns original spec.
    Uses cheap model — this is a quality gate, not synthesis.
    """
    from llm import LLMMessage
    try:
        from llm import build_adapter as _build
        _cheap_adapter = _build(model=None)  # uses MODEL_CHEAP default
    except Exception:
        _cheap_adapter = adapter

    tickets_text = "\n".join(f"  [{t.worker_type}] {t.task}" for t in tickets)
    user_msg = (
        f"Directive: {directive}\n\n"
        f"Proposed spec: {spec}\n\n"
        f"Proposed tickets:\n{tickets_text}\n\n"
        "Identify 2-3 failure modes, then provide a revised spec."
    )

    try:
        resp = _cheap_adapter.complete(
            [
                LLMMessage("system", _CHALLENGER_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=512,
            temperature=0.3,
            no_tools=True,
            purpose="spec challenge",
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="director._challenge_spec")
        if data:
            revised = safe_str(data.get("revised_spec"))
            critiques = safe_list(data.get("critiques", []), element_type=str)
            if critiques:
                log.info("pre-plan challenger: %d critiques → spec revised", len(critiques))
                for c in critiques:
                    log.debug("challenger critique: %s", c)
            if revised:
                return revised, (resp.input_tokens, resp.output_tokens)
    except Exception as exc:
        log.debug("pre-plan challenger failed (non-fatal): %s", exc)

    return spec, (0, 0)


def _review_worker_output(
    directive: str,
    ticket: Ticket,
    result: WorkerResult,
    adapter,
    dry_run: bool,
) -> Tuple[ReviewDecision, Tuple[int, int]]:
    """Director reviews worker output. Returns ReviewDecision + token counts."""
    from llm import LLMMessage

    if dry_run or adapter is None:
        return (ReviewDecision(accepted=True, reason="[dry-run] auto-accepted"), (0, 0))

    _r_result = result.result if isinstance(result.result, str) else json.dumps(result.result)
    user_msg = (
        f"Directive: {directive}\n\n"
        f"Ticket ({ticket.worker_type}): {ticket.task}\n\n"
        f"Worker output:\n{_r_result[:2000]}\n\n"
        f"Worker status: {result.status}"
        + (f"\nStuck reason: {result.stuck_reason}" if result.stuck_reason else "")
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _REVIEW_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=256,
            temperature=0.1,
            no_tools=True,
            purpose="worker review",
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="director._review_worker_output")
        if data:
            return (
                ReviewDecision(
                    accepted=bool(data.get("accepted", True)),
                    reason=safe_str(data.get("reason")),
                    revision_request=data.get("revision_request"),
                ),
                (resp.input_tokens, resp.output_tokens),
            )
    except Exception as exc:
        log.warning("director review parse failed: %s — rejecting (not auto-accepting)", exc)

    # Default: reject on parse failure. Auto-accepting hides bad output.
    return (ReviewDecision(accepted=False, reason="review parse failed, rejecting for safety"), (0, 0))


def _compile_report(
    directive: str,
    spec: str,
    worker_results: List[WorkerResult],
    adapter,
    dry_run: bool,
) -> Tuple[str, Tuple[int, int]]:
    """Compile worker outputs into a final polished report."""
    from llm import LLMMessage

    if dry_run or adapter is None:
        parts = [f"**{r.worker_type.title()} ({r.status})**\n{r.result}" for r in worker_results]
        return ("\n\n---\n\n".join(parts) or "[dry-run: no output]", (0, 0))

    parts_text = ""
    for i, r in enumerate(worker_results, 1):
        _r_text = r.result if isinstance(r.result, str) else json.dumps(r.result)
        parts_text += f"\n\n### Worker {i} ({r.worker_type})\nStatus: {r.status}\n{_r_text[:2000]}"

    user_msg = (
        f"Directive: {directive}\n\n"
        f"Spec: {spec}\n\n"
        f"Worker outputs:{parts_text}\n\n"
        "Compile a final report."
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _COMPILE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=4096,
            temperature=0.3,
            no_tools=True,
            purpose="report compile",
        )
        return (resp.content.strip(), (resp.input_tokens, resp.output_tokens))
    except Exception as exc:
        # Fallback: concatenate worker outputs
        parts = [f"**{r.worker_type.title()} ({r.status})**\n{r.result}" for r in worker_results]
        return ("\n\n---\n\n".join(parts), (0, 0))


# ---------------------------------------------------------------------------
# Log writing
# ---------------------------------------------------------------------------

def _write_director_log(
    project: Optional[str],
    director_id: str,
    directive: str,
    spec: str,
    tickets: List[Ticket],
    worker_results: List[WorkerResult],
    status: str,
    elapsed_ms: int,
    worker_slice: bool = False,
) -> Optional[str]:
    try:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            from orch import orch_root, projects_root
            if project:
                log_dir = projects_root() / project / "artifacts"
            else:
                log_dir = orch_root() / "artifacts" / "director"
        except Exception:
            base = Path.cwd()
            if project:
                log_dir = base / "projects" / project / "artifacts"
            else:
                log_dir = base / "artifacts" / "director"
        log_dir.mkdir(parents=True, exist_ok=True)

        fname = f"director-{director_id}-log.json"
        path = log_dir / fname
        payload = {
            "director_id": director_id,
            "directive": directive,
            "spec": spec,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "worker_slice": worker_slice,  # A/B experiment: memory.worker_slice active for this run?
            "tickets": [
                {"ticket_id": t.ticket_id, "worker_type": t.worker_type, "task": t.task}
                for t in tickets
            ],
            "worker_results": [
                {
                    "worker_type": r.worker_type,
                    "status": r.status,
                    "result_length": len(r.result),
                    "memory_slice_injected": r.memory_slice_injected,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                }
                for r in worker_results
            ],
        }
        from file_lock import atomic_write
        atomic_write(path, json.dumps(payload, indent=2))
        try:
            from orch import relative_display_path
            return relative_display_path(path)
        except Exception:
            return str(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Escalation consumer
# ---------------------------------------------------------------------------

_ESCALATION_SYSTEM = textwrap.dedent("""\
    You are the Director for Maro, an autonomous orchestration system.
    A task has been through multiple continuation passes without completing.
    Your job: decide what happens next. You are not an executor — you are a judge.

    You will receive:
    - The original goal
    - What has been accomplished (completed steps)
    - What remains (incomplete steps)
    - The continuation depth (how many passes have been attempted)

    DECISION TAXONOMY — classify your decision before choosing an action:

    MECHANICAL: The right move is obvious from the evidence. No human judgment needed.
      Auto-decide. Log reasoning but do not surface. Examples: scope is clearly bounded,
      completed work clearly answers the core question, goal is unambiguously too large.

    TASTE: A reasonable person could disagree with this call, but you have a defensible position.
      Auto-decide. Surface your reasoning prominently in summary_for_user so the operator
      can override if they disagree. Examples: close vs. continue is a judgment call,
      narrowing strategy has trade-offs, partial result quality is debatable.

    USER_CHALLENGE: This requires human judgment. Cannot be auto-decided.
      Always output action="surface". Provide a clear framing of the decision the operator
      needs to make. Examples: contradictory signals, ethical/policy questions, scope ambiguity
      that depends on unstated operator preferences, risk of destroying prior work.

    ACTIONS:
    - "continue": remaining work is valid and worth pursuing; spawn another focused pass
    - "narrow": scope is still too broad; rewrite the goal to a smaller, achievable target
      (provide a revised_goal in your response)
    - "close": partial result is sufficient; accept what's been done
    - "surface": requires human judgment; escalate to the operator with a summary

    Rules:
    - "continue" only if the remaining work is distinct and bounded (not the same breadth as the original)
    - "narrow" when the original goal was genuinely too large but a smaller slice would be valuable
    - "close" when the completed work already answers the core question even if incomplete
    - "surface" when there is no clear automated path forward (USER_CHALLENGE cases always surface)
    - Never "continue" indefinitely — prefer "close" or "surface" over a fifth+ continuation

    CONFIDENCE SCORE (1–10):
    Rate your confidence in this decision. Be calibrated — not all decisions are equally clear.
    - 8–10: Mechanical decisions with strong evidence. Act without caveat.
    - 5–7: Taste decisions. Flag uncertainty in summary_for_user.
    - 1–4: Genuine uncertainty. Override to "surface" regardless of your action choice.

    ANTI-SYCOPHANCY RULES (non-negotiable):
    - Take a position. State your decision clearly — never answer with "it depends" alone.
    - If the escalation context contains a bad assumption, name it.
    - State what information would change this decision.
    - Never open with affirmations: no "Great!", "Certainly!", "Of course!", "Happy to help!".
    - Prefer honest uncertainty over false confidence. If you don't know, score low and surface.

    Respond with a JSON object:
    {
      "action": "continue" | "narrow" | "close" | "surface",
      "decision_class": "mechanical" | "taste" | "user_challenge",
      "confidence": <integer 1-10>,
      "reasoning": "one or two sentences explaining the decision",
      "revised_goal": "narrowed goal string (only if action == 'narrow')",
      "summary_for_user": "brief status summary for operator/user (always include)"
    }
""").strip()


@dataclass
class EscalationDecision:
    action: str                          # "continue" | "narrow" | "close" | "surface"
    reasoning: str
    followup_task_id: Optional[str] = None   # task enqueued as a result, if any
    summary_for_user: str = ""
    decision_class: str = "mechanical"   # "mechanical" | "taste" | "user_challenge"
    confidence: int = 5                  # 1–10 calibrated confidence score


# ---------------------------------------------------------------------------
# Recursive-goal check-in (docs/RECURSIVE_CHECKIN_DESIGN.md)
#
# The continue/narrow branches of handle_escalation re-enqueue a fresh
# continuation task with continuation_depth+1 — a chain of *sequential distinct
# goal executions* (mechanism 2 in the design doc), distinct from a single
# loop's retry cap (loop_post_step.py's MAX_RESTART_DEPTH, mechanism 1).
# Jeremy's decree: at the 3rd goal pass (new_depth==2) and every jittered 4-7
# goal-passes after, fire a NON-BLOCKING progress check-in so the user can
# redirect or stop — but the goal keeps running regardless (ralph-style
# optimistic default). This is deliberately NOT the `escalate` navigator move,
# which parks the goal. Redirect/stop rides existing InterruptQueue plumbing;
# nothing new is built inbound (design §3).
# ---------------------------------------------------------------------------

def _checkin_first_depth() -> int:
    """Depth at which the first recursion check-in fires (decree: 2 == pass 3)."""
    try:
        val = int(config_get("recursion.checkin_first_depth", 2))
    except (TypeError, ValueError):
        return 2
    return max(val, 1)


def _checkin_jitter() -> int:
    """Random 4-7-goal cadence for check-ins after the first (jittered, not fixed)."""
    try:
        lo = int(config_get("recursion.checkin_jitter_min", 4))
        hi = int(config_get("recursion.checkin_jitter_max", 7))
    except (TypeError, ValueError):
        lo, hi = 4, 7
    if hi < lo:
        lo, hi = hi, lo
    lo = max(lo, 1)
    hi = max(hi, lo)
    return random.randint(lo, hi)


def _fire_checkin(task, new_depth, action, reasoning, summary_for_user, origin) -> None:
    """Non-blocking progress notification at deep recursion. Never raises.

    Composes a payload from data already in hand — the original ask (walked
    back through the `origin` ancestry, else this chain's escalation reason),
    which goal-pass this is, and the director's OWN reasoning/summary_for_user
    from this escalation decision (which already explains how the work serves
    the original ask — no second LLM call). Emits a `recursion_checkin` notify
    event. A notify failure must never affect whether the continuation gets
    enqueued (design §2) — hence the blanket try/except.
    """
    try:
        # Original ask: prefer the root goal carried in ancestry; fall back to
        # this chain's escalation reason. Don't block the check-in on being
        # able to reconstruct full lineage (design §2).
        original_goal = origin.get("parent_goal") or task.get("reason", "")
        # origin["checkins_sent"] is already advanced (to include THIS
        # check-in) by _advance_origin_with_checkin before this runs — don't
        # add another +1 or the payload reports one check-in ahead of the
        # count actually carried in origin (adversarial-review final pass,
        # 2026-07-13, all 3 reviewers independently).
        checkin_number = int(origin.get("checkins_sent", 0))
        # How the user steers this — whatever inbound channel is live rides the
        # same notify substrate; no reply means "continue" (ralph default).
        redirect_hint = (
            "This goal is still running in the background. Reply on your "
            "configured channel (Telegram / Slack / CLI) to redirect or stop "
            "it — no reply means keep going."
        )
        payload = {
            "handle_id": str(origin.get("parent_handle_id") or ""),
            "blocking": False,  # distinguishes this from a park-the-goal escalation
            "goal": str(original_goal)[:400],
            "reason": str(original_goal)[:400],
            "continuation_depth": new_depth,
            "goal_pass": new_depth + 1,  # pass 1 == depth 0
            "checkin_number": checkin_number,
            "action": action,
            "reasoning": str(reasoning),
            "summary_for_user": str(summary_for_user),
            "job_id": task.get("job_id", ""),
            "parent_job_id": task.get("parent_job_id", ""),
            "redirect_hint": redirect_hint,
            "status": "running",
        }
        from notify import emit as _notify_emit
        _notify_emit("recursion_checkin", payload, run_dir=None)
        log.info("recursion_checkin fired: depth=%d pass=%d checkin=%d action=%s",
                 new_depth, new_depth + 1, checkin_number, action)
    except Exception:
        log.debug("recursion check-in emit failed (non-fatal)", exc_info=True)


def _advance_origin_with_checkin(task, new_depth) -> tuple:
    """Return (origin, should_fire) — advance the check-in cadence state on
    the origin ancestry dict without firing the notification itself.

    The caller enqueues the continuation first and fires the check-in only
    after that enqueue succeeds — otherwise a failed enqueue would still
    tell the user "still running" for a chain that just silently died.

    Rides the existing `origin` dict (design §1):
    - origin["next_checkin_depth"]: depth of the *next* check-in (first = 2)
    - origin["checkins_sent"]:      count so far, for cadence + summary
    """
    origin = Origin(task.get("origin") or {})
    next_checkin = origin.get("next_checkin_depth", _checkin_first_depth())
    should_fire = new_depth >= next_checkin
    if should_fire:
        origin["next_checkin_depth"] = new_depth + _checkin_jitter()
        origin["checkins_sent"] = origin.get("checkins_sent", 0) + 1
    return origin, should_fire


def handle_escalation(
    task: dict,
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
    channel: Optional["ConversationChannel"] = None,
) -> EscalationDecision:
    """Process a loop_escalation task and decide what happens next.

    The director reads the escalation context (goal, accomplished, remaining,
    depth) and makes one of four decisions:
    - continue: spawn a focused continuation with depth+1
    - narrow: rewrite the goal to a smaller target, spawn as new task
    - close: accept the partial result, no further work
    - surface: write a human-readable summary for operator review

    This is the closure mechanism for the dynamic tree traversal — escalations
    don't silently accumulate, they get a reasoned decision from a higher layer.
    """
    from llm import LLMMessage

    reason = task.get("reason", "")
    depth = task.get("continuation_depth", 0)
    job_id = task.get("job_id", "unknown")
    parent_id = task.get("parent_job_id", "")

    log.info("escalation_start job_id=%s depth=%d", job_id, depth)

    if verbose:
        print(f"[maro:director:escalation] job={job_id} depth={depth}", file=sys.stderr, flush=True)

    # Dry-run: close the escalation without further work
    if dry_run or adapter is None:
        return EscalationDecision(
            action="close",
            reasoning="[dry-run] escalation acknowledged, closing",
            summary_for_user=f"Dry-run escalation for job {job_id} at depth {depth}",
        )

    try:
        if adapter is None:
            from llm import build_adapter
            from conductor import assign_model_by_role
            adapter = build_adapter(model=assign_model_by_role("planner"))

        resp = adapter.complete(
            [
                LLMMessage("system", _ESCALATION_SYSTEM),
                LLMMessage("user",
                    f"Escalation context:\n\n{reason}\n\n"
                    f"Continuation depth: {depth}\n\n"
                    "What should happen next? Respond with JSON only."),
            ],
            max_tokens=512,
            temperature=0.1,
            no_tools=True,
            purpose="escalation decision",
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="director.handle_escalation")
    except Exception as exc:
        log.warning("escalation LLM call failed, defaulting to surface: %s", exc)
        data = None

    if not data:
        return EscalationDecision(
            action="surface",
            reasoning="LLM call failed during escalation processing",
            summary_for_user=f"Escalation for job {job_id} (depth {depth}) could not be processed automatically.",
        )

    action = safe_str(data.get("action", "surface")).strip().lower()
    if action not in ("continue", "narrow", "close", "surface"):
        action = "surface"
    reasoning = safe_str(data.get("reasoning", ""))
    summary_for_user = safe_str(data.get("summary_for_user", ""))
    revised_goal = safe_str(data.get("revised_goal", ""))
    decision_class = safe_str(data.get("decision_class", "mechanical")).strip().lower()
    if decision_class not in ("mechanical", "taste", "user_challenge"):
        decision_class = "mechanical"
    try:
        confidence = int(data.get("confidence", 5))
        confidence = max(1, min(10, confidence))
    except (TypeError, ValueError):
        confidence = 5

    # Confidence-gated enforcement: low confidence overrides to surface
    if confidence < 5:
        log.info("escalation confidence=%d < 5, overriding action=%s to surface", confidence, action)
        action = "surface"
        summary_for_user = (
            f"[Low confidence ({confidence}/10) — escalating to operator] " + summary_for_user
        )
    elif confidence <= 6:
        # Taste-level uncertainty: add caveat
        summary_for_user = f"[Confidence {confidence}/10] " + summary_for_user

    # User-challenge decisions always surface regardless of LLM action choice
    if decision_class == "user_challenge" and action != "surface":
        log.info("escalation decision_class=user_challenge, overriding action=%s to surface", action)
        action = "surface"

    # Notify channel of risky judgment calls (confidence <= 7 = < 70% sure)
    if channel is not None and confidence <= 7 and action != "surface":
        try:
            channel.notify_low_confidence(
                decision=f"{action}: {summary_for_user[:120]}",
                confidence=confidence / 10.0,
                reasoning=reasoning[:200],
            )
        except Exception:
            pass  # channel notifications must never block escalation logic

    # Log calibration event for self-improvement
    try:
        import json as _json
        import time as _time
        from orch_items import memory_dir as _mem_dir
        from file_lock import locked_append
        _cal_path = _mem_dir() / "calibration.jsonl"
        locked_append(_cal_path, _json.dumps({
            "ts": _time.time(),
            "event": "escalation_decision",
            "job_id": job_id,
            "depth": depth,
            "action": action,
            "decision_class": decision_class,
            "confidence": confidence,
        }))
    except Exception as _exc:
        log.debug("calibration log failed (non-fatal): %s", _exc)

    log.info("escalation_decision job_id=%s action=%s reasoning=%r", job_id, action, reasoning[:80])

    followup_task_id = None

    if action == "continue":
        # Spawn a focused continuation with depth+1
        try:
            from task_store import enqueue as _ts_enqueue
            new_depth = depth + 1
            # Deep-recursion check-in cadence (non-blocking) + carry ancestry.
            _origin, _should_checkin = _advance_origin_with_checkin(task, new_depth)
            _cont_task = _ts_enqueue(
                lane="agenda",
                source="loop_continuation",
                reason=reason,  # original escalation context becomes continuation context
                parent_job_id=job_id,
                continuation_depth=new_depth,
                origin=_origin,  # carry ancestry forward (+ check-in cadence state)
            )
            followup_task_id = _cont_task["job_id"]
            log.info("escalation_continue: enqueued %s depth=%d", followup_task_id, depth + 1)
            # Fire only after the continuation is confirmed enqueued — a failed
            # enqueue must never tell the user "still running" (adversarial-
            # review batch-1, Skeptic finding #1, 2026-07-13).
            if _should_checkin:
                _fire_checkin(task, new_depth, "continue", reasoning, summary_for_user, _origin)
        except Exception as exc:
            log.warning("escalation continue: failed to enqueue continuation: %s", exc)
            # Suppressing the misleading check-in (above) isn't enough — the
            # chain is now dead with no continuation and no operator signal
            # beyond a warning log. Fall back to the existing "surface"
            # disposition so handle_queue.py's action=="surface" notify path
            # actually tells someone (adversarial-review final pass,
            # 2026-07-13: Architect High + Minimalist Medium, independently).
            action = "surface"
            summary_for_user = (
                f"This recursive goal chain stopped: the continuation could "
                f"not be enqueued ({exc}). No follow-up task exists — "
                f"original reasoning was: {summary_for_user}"
            )
            reasoning = f"enqueue failed: {exc}"

    elif action == "narrow" and not revised_goal:
        # LLM chose narrow but forgot to provide a revised goal — fall back to surface
        log.warning("escalation narrow: no revised_goal from LLM, falling back to surface")
        action = "surface"

    elif action == "narrow" and revised_goal:
        # Spawn a new task with the narrowed goal
        try:
            from task_store import enqueue as _ts_enqueue
            new_depth = depth + 1
            # Deep-recursion check-in cadence (non-blocking) + carry ancestry.
            _origin, _should_checkin = _advance_origin_with_checkin(task, new_depth)
            _narrow_task = _ts_enqueue(
                lane="agenda",
                source="loop_continuation",
                reason=f"NARROWED from escalation {job_id}:\n\n{revised_goal}",
                parent_job_id=job_id,
                continuation_depth=new_depth,
                origin=_origin,  # carry ancestry forward (+ check-in cadence state)
            )
            followup_task_id = _narrow_task["job_id"]
            log.info("escalation_narrow: enqueued %s with revised goal %r",
                     followup_task_id, revised_goal[:60])
            # Fire only after the continuation is confirmed enqueued (see the
            # matching comment in the continue branch above).
            if _should_checkin:
                _fire_checkin(task, new_depth, "narrow", reasoning, summary_for_user, _origin)
        except Exception as exc:
            log.warning("escalation narrow: failed to enqueue narrowed task: %s", exc)
            # Same rationale as the continue branch above: a dead chain must
            # surface to an operator, not disappear behind a warning log.
            action = "surface"
            summary_for_user = (
                f"This recursive goal chain stopped: the narrowed continuation "
                f"could not be enqueued ({exc}). No follow-up task exists — "
                f"revised goal was: {revised_goal[:200]}"
            )
            reasoning = f"enqueue failed: {exc}"

    elif action in ("close", "surface"):
        # Write a summary artifact for the operator
        try:
            import os
            from orch import project_dir as _proj_dir
            _art_dir = _proj_dir(f"escalation-{job_id[:8]}") / "artifacts"
            _art_dir.mkdir(parents=True, exist_ok=True)
            _summary_path = _art_dir / f"escalation-{job_id[:8]}-{action}.md"
            from file_lock import atomic_write
            atomic_write(
                _summary_path,
                f"# Escalation {action.title()} — {job_id}\n\n"
                f"**Depth:** {depth}\n"
                f"**Action:** {action}\n"
                f"**Reasoning:** {reasoning}\n\n"
                f"## Summary for operator\n{summary_for_user}\n\n"
                f"## Full escalation context\n{reason}\n",
            )
            log.info("escalation_%s: wrote summary to %s", action, _summary_path)
        except Exception as exc:
            log.warning("escalation %s: failed to write summary: %s", action, exc)

    if verbose:
        print(f"[maro:director:escalation] {action}: {reasoning[:80]}", file=sys.stderr, flush=True)

    # Emit observable event for dashboard visibility into escalation decisions
    try:
        from observe import write_event as _write_event
        _write_event(
            "escalation_processed",
            goal=task.get("reason", "")[:80],
            project=task.get("parent_job_id", ""),
            loop_id=job_id,
            status=action,
            detail=f"depth={depth} followup={followup_task_id or 'none'} | {reasoning[:100]}",
        )
    except Exception:
        pass

    return EscalationDecision(
        action=action,
        reasoning=reasoning,
        followup_task_id=followup_task_id,
        summary_for_user=summary_for_user,
        decision_class=decision_class,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Director Closure Check — moved to closure_verify.py (docs/REFACTOR_PLAN.md
# Tier 3). Re-exported here so `director.verify_goal_completion` and
# `from director import ...` call sites keep working unchanged.
# ---------------------------------------------------------------------------

from closure_verify import (  # noqa: E402
    ClosureVerdict,
    verify_goal_completion,
    _classify_precondition,
    _run_precondition_preflight,
    _check_outcome,
    _classify_probe_modality,
    _detect_behavioral_gap,
    _detect_diagnosis_gap,
    _detect_next_ledger_gap,
    _CLOSURE_PLAN_SYSTEM,
    _CLOSURE_VERDICT_SYSTEM,
)


# ---------------------------------------------------------------------------
# Adaptive Execution — Phase 64
# ---------------------------------------------------------------------------

_ADAPTIVE_SYSTEM = textwrap.dedent("""\
    You are the Director evaluating mid-execution state for a running agent loop.

    Available actions:
    - "continue"  — current approach is fine, proceed
    - "adjust"    — tactically revise remaining steps based on discoveries (sharpening)
    - "replan"    — current approach has strategic problems; step back and describe a better one
    - "restart"   — current work is not worth preserving; start fresh with what was learned
    - "escalate"  — human decision required; you cannot resolve this autonomously

    Decision guidelines:
    - Choose "adjust" for tactical corrections: wrong next steps, missed edge case.
    - Choose "replan" for strategic course changes: fundamentally wrong approach.
    - Choose "restart" only when completed work is counterproductive (not just insufficient).
    - Choose "escalate" only for genuine decision points: conflicting goals, irreversible
      actions, or deep ambiguity that cannot be resolved from context alone.
    - Default is autonomous. Escalate sparingly.
    - Trigger "injection" means the operator injected new information mid-run. Any
      step additions/reordering they explicitly requested are ALREADY applied to the
      remaining steps — your job is only to judge whether the plan still serves the
      goal in light of the injection. "continue" is correct when it does.
    - When the convergence budget is 0, you MUST return "continue" (no more replans/restarts).

    Field rules:
    - "adjust": provide revised_steps replacing the remaining tail. Minimal changes only.
      Empty or missing revised_steps falls back to "continue".
    - "replan": provide new_approach — 1–3 sentences describing the better strategy.
      Do NOT provide steps — the planner generates them.
    - "restart": provide restart_context — what was learned and why fresh start is needed.
    - "escalate": provide user_question — the specific question for the human.
    - "continue": all optional fields may be omitted.
    - next_check_in: steps before next mandatory check (integer, 1–10, default 3).

    Respond with JSON only:
    {
      "action": "continue" | "adjust" | "replan" | "restart" | "escalate",
      "reasoning": "one sentence",
      "revised_steps": ["step 1", "step 2", ...],
      "new_approach": "narrative description of better strategy",
      "restart_context": "what was learned; why starting fresh",
      "user_question": "specific question for the human",
      "next_check_in": 3
    }
""").strip()


@dataclass
class EvaluationContext:
    """Compact execution state snapshot for director_evaluate().

    Not the full LoopContext — only what the director needs to make a decision.
    Serializable; no live references to loop internals.
    """
    goal: str
    current_pass_scope: str
    steps_completed: List[str]
    steps_remaining: List[str]
    step_results_summary: str          # last 3 completed step results, ≤600 chars each
    verify_failure_count: int
    total_steps_taken: int
    max_steps: int
    current_approach: str = ""         # "" until ExecutionPlan introduced (Phase B+)
    convergence_budget_remaining: int = 2  # 0 means no more replans allowed
    injected_context: str = ""         # operator injection(s) applied this boundary (trigger="injection")


@dataclass
class DirectorDecision:
    """Decision returned by director_evaluate()."""
    action: str                                   # continue | adjust | replan | restart | escalate
    reasoning: str                                # one sentence — logged + shown in channel
    revised_steps: Optional[List[str]] = None     # for 'adjust'
    new_approach: Optional[str] = None            # for 'replan' (Phase B+)
    restart_context: Optional[str] = None         # for 'restart' (Phase C+)
    user_question: Optional[str] = None           # for 'escalate' (Phase C+)
    next_check_in: int = 3                        # steps before next mandatory check


def director_evaluate(
    goal: str,
    eval_ctx: EvaluationContext,
    trigger: str,
    adapter,
    *,
    dry_run: bool = False,
) -> DirectorDecision:
    """Director mid-execution evaluation.

    Called from agent_loop on verify failure streak, step threshold, or stuck signal.
    Supported actions: continue, adjust, replan, restart, escalate.
    Budget enforcement (replan/restart clamped to continue when budget exhausted) is in agent_loop.

    Non-fatal — returns 'continue' on any exception.

    Args:
        goal:     original goal (immutable)
        eval_ctx: compact snapshot of current execution state
        trigger:  what fired this call — "verify_failure" | "step_threshold" | "stuck" | "injection"
        adapter:  LLM adapter (cheap model is sufficient)
        dry_run:  skip LLM call and return continue
    """
    _continue = DirectorDecision(action="continue", reasoning="evaluation skipped")
    _continue_on_error = DirectorDecision(action="continue", reasoning="evaluation failed — treated as continue")

    if dry_run or adapter is None:
        return _continue

    completed_str = (
        "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(eval_ctx.steps_completed))
        or "  (none yet)"
    )
    remaining_str = (
        "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(eval_ctx.steps_remaining))
        or "  (none — may be near end)"
    )

    budget_note = (
        "Convergence budget: 0 — you MUST return continue (no more replans allowed)."
        if eval_ctx.convergence_budget_remaining <= 0
        else f"Convergence budget remaining: {eval_ctx.convergence_budget_remaining} replan(s)."
    )

    injection_block = (
        f"Operator injection(s) just applied at this boundary:\n{eval_ctx.injected_context}\n\n"
        if eval_ctx.injected_context else ""
    )

    user_msg = (
        f"Goal: {goal}\n\n"
        f"Trigger: {trigger}\n"
        f"{injection_block}"
        f"Steps completed ({eval_ctx.total_steps_taken}/{eval_ctx.max_steps}):\n{completed_str}\n\n"
        f"Steps remaining:\n{remaining_str}\n\n"
        f"Recent step results:\n{eval_ctx.step_results_summary or '(none)'}\n\n"
        f"Consecutive verify failures: {eval_ctx.verify_failure_count}\n"
        f"{budget_note}"
    )

    try:
        from llm import LLMMessage

        resp = adapter.complete(
            [
                LLMMessage("system", _ADAPTIVE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=512,
            temperature=0.1,
            no_tools=True,
            purpose="adaptive supervision",
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="director.adaptive")
        if not data:
            return _continue_on_error

        raw_action = safe_str(data.get("action", "continue")).lower().strip()
        _valid_actions = ("continue", "adjust", "replan", "restart", "escalate")
        action = raw_action if raw_action in _valid_actions else "continue"

        reasoning = safe_str(data.get("reasoning", ""))

        try:
            next_check_in = max(1, int(data.get("next_check_in", 3)))
        except (TypeError, ValueError):
            next_check_in = 3

        revised_steps: Optional[List[str]] = None
        new_approach: Optional[str] = None
        restart_context: Optional[str] = None
        user_question: Optional[str] = None

        if action == "adjust":
            raw_steps = safe_list(data.get("revised_steps"), element_type=str)
            revised_steps = [s for s in raw_steps if s] or None
            if not revised_steps:
                # Empty revised_steps on adjust → treat as continue (per design spec)
                action = "continue"
        elif action == "replan":
            new_approach = safe_str(data.get("new_approach", "")) or None
        elif action == "restart":
            restart_context = safe_str(data.get("restart_context", "")) or None
        elif action == "escalate":
            user_question = safe_str(data.get("user_question", "")) or None

        decision = DirectorDecision(
            action=action,
            reasoning=reasoning,
            revised_steps=revised_steps,
            new_approach=new_approach,
            restart_context=restart_context,
            user_question=user_question,
            next_check_in=next_check_in,
        )
        log.info(
            "director_evaluate [%s]: action=%s next_check_in=%d — %s",
            trigger, action, next_check_in, reasoning[:120],
        )
        return decision

    except Exception:
        log.debug("director_evaluate error — returning continue", exc_info=True)
        return _continue_on_error


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="maro-director", description="Run Maro's Director on a directive")
    parser.add_argument("directive", nargs="+", help="The directive to execute")
    parser.add_argument("--project", "-p", help="Project slug")
    parser.add_argument("--model", "-m", help="LLM model string")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument("--format", choices=["text", "json"], default="text")

    args = parser.parse_args(argv)
    directive = " ".join(args.directive)

    result = run_director(
        directive,
        project=args.project,
        dry_run=args.dry_run,
        verbose=True,
    )

    if args.format == "json":
        print(json.dumps({
            "director_id": result.director_id,
            "status": result.status,
            "plan_acceptance": result.plan_acceptance,
            "tickets": len(result.tickets),
            "report": result.report,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "elapsed_ms": result.elapsed_ms,
        }, indent=2))
    else:
        print(result.summary())
        print()
        print("=== REPORT ===")
        print(result.report)

    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
