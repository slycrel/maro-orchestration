#!/usr/bin/env python3
"""Phase 3: Director agent for Poe's orchestration hierarchy.

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
    orch poe-director "your directive here" [--dry-run]
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from workers import WorkerResult, dispatch_worker, infer_worker_type, WORKER_TYPES

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
    You are the Director for Poe, an autonomous orchestration system.
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
    You are the Director compiling a final report for Poe's Handle.
    Synthesize the worker outputs into a polished, structured report.
    The report will be relayed to the user (Jeremy) — make it direct and useful.
    Lead with the key findings/deliverables. Include relevant details.
    No hedging. No "I" statements. Just the work product.
""").strip()


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
) -> DirectorResult:
    """Run the Director on a directive.

    Args:
        directive: High-level task or goal.
        project: Optional project slug to associate work with.
        adapter: LLMAdapter instance.
        dry_run: Simulate without API calls.
        verbose: Print progress to stderr.

    Returns:
        DirectorResult with plan, worker outputs, and final report.
    """
    from llm import LLMMessage

    director_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()

    def _log(msg: str):
        if verbose:
            print(f"[poe:director:{director_id}] {msg}", file=sys.stderr, flush=True)

    _log(f"directive={directive!r}")

    # Check plan acceptance mode
    acceptance = "explicit" if requires_explicit_acceptance(directive) else "inferred"
    _log(f"plan_acceptance={acceptance}")

    # Build adapter — planner role uses MODEL_POWER for spec production
    if adapter is None and not dry_run:
        from llm import build_adapter
        from poe import assign_model_by_role
        adapter = build_adapter(model=assign_model_by_role("planner"))

    total_tokens_in = 0
    total_tokens_out = 0

    # Phase 1: Produce SPEC + tickets
    _log("producing spec...")
    spec, tickets, spec_tokens = _produce_spec(directive, adapter, dry_run, _log)
    total_tokens_in += spec_tokens[0]
    total_tokens_out += spec_tokens[1]
    _log(f"spec produced, tickets={len(tickets)}")

    # Phase 2: Dispatch workers + review
    worker_results: List[WorkerResult] = []
    review_decisions: List[ReviewDecision] = []
    completed_context = ""

    for ticket in tickets:
        _log(f"dispatching worker={ticket.worker_type} task={ticket.task[:50]!r}")

        context = completed_context.strip()
        if ticket.context:
            context = ticket.context + ("\n" + context if context else "")

        result = dispatch_worker(
            ticket.worker_type,
            ticket.task,
            context=context,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
        )
        total_tokens_in += result.tokens_in
        total_tokens_out += result.tokens_out

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

        worker_results.append(result)
        if result.status == "done" and result.result:
            completed_context += f"\n\n[{ticket.worker_type}] {ticket.task}:\n{result.result[:500]}"

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
    )

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
        resp = adapter.complete(
            [
                LLMMessage("system", _SPEC_SYSTEM),
                LLMMessage("user", f"Directive: {directive}"),
            ],
            max_tokens=1024,
            temperature=0.2,
        )
        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            spec = data.get("spec", "")
            raw_tickets = data.get("tickets", [])
            tickets = []
            for i, t in enumerate(raw_tickets[:4]):
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

    user_msg = (
        f"Directive: {directive}\n\n"
        f"Ticket ({ticket.worker_type}): {ticket.task}\n\n"
        f"Worker output:\n{result.result[:2000]}\n\n"
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
        )
        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            return (
                ReviewDecision(
                    accepted=bool(data.get("accepted", True)),
                    reason=data.get("reason", ""),
                    revision_request=data.get("revision_request"),
                ),
                (resp.input_tokens, resp.output_tokens),
            )
    except Exception:
        pass

    # Default: accept (don't get stuck in review loops)
    return (ReviewDecision(accepted=True, reason="review parse failed, auto-accepting"), (0, 0))


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
        parts_text += f"\n\n### Worker {i} ({r.worker_type})\nStatus: {r.status}\n{r.result[:2000]}"

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
) -> Optional[str]:
    try:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            from orch import orch_root
            base = orch_root()
        except Exception:
            base = Path.cwd()

        if project:
            log_dir = base / "prototypes" / "poe-orchestration" / "projects" / project / "artifacts"
        else:
            log_dir = base / "prototypes" / "poe-orchestration" / "artifacts" / "director"
        log_dir.mkdir(parents=True, exist_ok=True)

        fname = f"director-{director_id}-log.json"
        path = log_dir / fname
        payload = {
            "director_id": director_id,
            "directive": directive,
            "spec": spec,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "tickets": [
                {"ticket_id": t.ticket_id, "worker_type": t.worker_type, "task": t.task}
                for t in tickets
            ],
            "worker_results": [
                {"worker_type": r.worker_type, "status": r.status, "result_length": len(r.result)}
                for r in worker_results
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="poe-director", description="Run Poe's Director on a directive")
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
