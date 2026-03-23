#!/usr/bin/env python3
"""Poe's Handle — unified entry point for all incoming requests.

Routes to NOW lane (1-shot) or AGENDA lane (multi-step loop) based on
intent classification. This is the interface Jeremy sends messages through.

Response timing contract:
    - Immediate ack printed within the call (before execution starts)
    - Status updates printed as execution progresses (--verbose)
    - Substantive result in HandleResult.result

Usage:
    from handle import handle
    result = handle("research winning polymarket strategies")
    print(result.format())

CLI:
    python -m handle "your request here" [--project SLUG] [--dry-run]
    orch poe-handle "your request here"
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class HandleResult:
    handle_id: str
    lane: str                   # "now" | "agenda"
    lane_confidence: float
    classification_reason: str
    message: str
    status: str                 # "done" | "stuck" | "error"
    result: str                 # The substantive response / work product
    project: Optional[str] = None
    loop_result: Any = None     # LoopResult if AGENDA
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    artifact_path: Optional[str] = None

    def format(self, mode: str = "text") -> str:
        if mode == "json":
            return json.dumps({
                "handle_id": self.handle_id,
                "lane": self.lane,
                "classification_reason": self.classification_reason,
                "status": self.status,
                "result": self.result,
                "project": self.project,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "elapsed_ms": self.elapsed_ms,
                "artifact_path": self.artifact_path,
            }, indent=2)
        lines = [
            f"handle_id={self.handle_id}",
            f"lane={self.lane} (confidence={self.lane_confidence:.2f})",
            f"status={self.status}",
            f"tokens={self.tokens_in}in+{self.tokens_out}out elapsed={self.elapsed_ms}ms",
        ]
        if self.project:
            lines.append(f"project={self.project}")
        if self.artifact_path:
            lines.append(f"artifact={self.artifact_path}")
        lines.append("")
        lines.append(self.result)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NOW lane executor
# ---------------------------------------------------------------------------

_NOW_SYSTEM = """You are Poe, an autonomous AI assistant.
Answer the user's request directly and completely. Be thorough but concise.
If the request is a question, answer it. If it's a task, complete it.
Do not hedge or defer — just do the work.
"""


def _run_now(
    message: str,
    handle_id: str,
    adapter,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Execute a NOW-lane task: single LLM call, returns result dict."""
    from llm import LLMMessage

    if verbose:
        print(f"[poe:{handle_id}] NOW lane — executing...", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _NOW_SYSTEM),
                LLMMessage("user", message),
            ],
            max_tokens=2048,
            temperature=0.4,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        content = resp.content.strip()
        if not content:
            content = "[no response]"
        return {
            "status": "done",
            "result": content,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
            "elapsed_ms": elapsed,
        }
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return {
            "status": "error",
            "result": f"NOW lane error: {exc}",
            "tokens_in": 0,
            "tokens_out": 0,
            "elapsed_ms": elapsed,
        }


# ---------------------------------------------------------------------------
# Core handle function
# ---------------------------------------------------------------------------

def handle(
    message: str,
    *,
    project: Optional[str] = None,
    model: Optional[str] = None,
    adapter=None,
    force_lane: Optional[str] = None,   # "now" | "agenda" | None (auto)
    dry_run: bool = False,
    verbose: bool = False,
) -> HandleResult:
    """Process an incoming request through Poe's handle.

    Args:
        message: The natural language request.
        project: Project slug to attach AGENDA work to.
        model: LLM model override.
        adapter: Pre-built LLMAdapter (skips build_adapter).
        force_lane: Override classification ("now" or "agenda").
        dry_run: Simulate without API calls.
        verbose: Print progress.

    Returns:
        HandleResult with routing info and substantive result.
    """
    from intent import classify
    from llm import build_adapter, MODEL_CHEAP
    from agent_loop import run_agent_loop, _DryRunAdapter

    handle_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()

    if verbose:
        print(f"[poe:{handle_id}] handle: {message!r}", file=sys.stderr, flush=True)

    # Build adapter
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or MODEL_CHEAP)
    elif dry_run:
        adapter = _DryRunAdapter()

    # Classify intent
    if force_lane:
        lane = force_lane
        confidence = 1.0
        reason = f"forced to {force_lane}"
    else:
        lane, confidence, reason = classify(message, adapter=adapter if not dry_run else None, dry_run=dry_run)

    if verbose:
        print(f"[poe:{handle_id}] classified lane={lane} confidence={confidence:.2f}: {reason}", file=sys.stderr, flush=True)

    # Route to lane
    if lane == "now":
        outcome = _run_now(message, handle_id, adapter, verbose=verbose)
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Write artifact
        artifact_path = _write_now_artifact(handle_id, message, outcome.get("result", ""), elapsed)

        return HandleResult(
            handle_id=handle_id,
            lane="now",
            lane_confidence=confidence,
            classification_reason=reason,
            message=message,
            status=outcome["status"],
            result=outcome["result"],
            tokens_in=outcome["tokens_in"],
            tokens_out=outcome["tokens_out"],
            elapsed_ms=elapsed,
            artifact_path=artifact_path,
        )

    else:  # agenda
        if verbose:
            print(f"[poe:{handle_id}] AGENDA lane — starting loop...", file=sys.stderr, flush=True)

        loop_result = run_agent_loop(
            message,
            project=project,
            model=model,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
        )
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Build result text from completed steps
        result_parts = []
        for s in loop_result.steps:
            if s.status == "done" and s.result:
                result_parts.append(f"**Step {s.index}: {s.text}**\n{s.result}")
        result_text = "\n\n---\n\n".join(result_parts) if result_parts else "[no output produced]"
        if loop_result.status == "stuck":
            result_text += f"\n\n⚠️ Stuck: {loop_result.stuck_reason}"

        return HandleResult(
            handle_id=handle_id,
            lane="agenda",
            lane_confidence=confidence,
            classification_reason=reason,
            message=message,
            status=loop_result.status,
            result=result_text,
            project=loop_result.project,
            loop_result=loop_result,
            tokens_in=loop_result.total_tokens_in,
            tokens_out=loop_result.total_tokens_out,
            elapsed_ms=elapsed,
            artifact_path=loop_result.log_path,
        )


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def _write_now_artifact(
    handle_id: str,
    message: str,
    result: str,
    elapsed_ms: int,
) -> Optional[str]:
    """Write NOW-lane result to a shared artifacts directory."""
    try:
        # Use orch_root if available, else cwd
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            from orch import orch_root
            base = orch_root()
        except Exception:
            base = Path.cwd()

        artifacts_dir = base / "prototypes" / "poe-orchestration" / "artifacts" / "now"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"now-{handle_id}.json"
        path = artifacts_dir / fname
        payload = {
            "handle_id": handle_id,
            "lane": "now",
            "message": message,
            "result": result,
            "elapsed_ms": elapsed_ms,
            "created_at": datetime.now(timezone.utc).isoformat(),
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

    parser = argparse.ArgumentParser(prog="poe-handle", description="Poe's unified request handler")
    parser.add_argument("message", nargs="+", help="The request to handle")
    parser.add_argument("--project", "-p", help="Project slug for AGENDA work")
    parser.add_argument("--model", "-m", help="LLM model string")
    parser.add_argument("--lane", choices=["now", "agenda"], help="Force a specific lane")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument("--format", choices=["text", "json"], default="text")

    args = parser.parse_args(argv)
    msg = " ".join(args.message)

    result = handle(
        msg,
        project=args.project,
        model=args.model,
        force_lane=args.lane,
        dry_run=args.dry_run,
        verbose=args.verbose or True,
    )

    print(result.format(mode=args.format))
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
