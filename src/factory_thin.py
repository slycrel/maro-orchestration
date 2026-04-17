"""Factory Mode — Thin Loop variant.

Keeps the step execution loop (decompose → execute → adversarial review → compile) but strips
all Mode 2 scaffolding: no Director, no Inspector, no persona routing,
no lesson injection, no skill matching, no multi-plan comparison,
no pre-plan challenger, no user context injection.

Just a goal, a clean system prompt, and the loop mechanics.

The system prompts are written as behavior descriptions ("here is what
good looks like") rather than rule systems ("do X then Y then Z").
This is the Bitter Lesson framing: describe the desired outcome,
let the model figure out the how.

Usage:
    python3 factory_thin.py "your goal here" [--model cheap|mid|power]
    poe-factory-thin "your goal here"
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from llm_parse import extract_json

sys.path.insert(0, str(Path(__file__).resolve().parent))

log = logging.getLogger("poe.factory.thin")


# ---------------------------------------------------------------------------
# System prompts — behavior descriptions, not rule systems
# ---------------------------------------------------------------------------

FACTORY_DECOMPOSE = textwrap.dedent("""\
    You are an autonomous agent. Break the goal into 4-8 concrete steps
    that together produce a complete, high-quality result.

    Good steps: specific, independently executable, produce a clear artifact.
    Bad steps: vague, bundled ("research and analyze"), or procedural fluff.

    Steps that involve research should produce specific findings, not "gather info".
    The final step should synthesize everything into a usable deliverable.

    Respond ONLY with a JSON array of step strings. No prose.
    Each step under 20 words.
""").strip()

FACTORY_STEP = textwrap.dedent("""\
    You are an autonomous execution agent completing one step of a larger goal.

    Do the work. Produce a specific, complete result — not a plan for doing it.
    If the step involves research: cite evidence quality (RCT, meta-analysis,
    observational, animal/in-vitro). Grade each claim: Tier 1/2/3.
    If the step involves synthesis: make concrete recommendations, not hedges.

    TOKEN EFFICIENCY — CRITICAL:
    Target under 500 tokens for your complete_step result. Use bullet points and
    structured data (JSON where appropriate). Never quote long passages verbatim —
    extract the 2-3 key facts and discard the rest. No preamble, no sign-offs.
    If you need more than 500 tokens, you're probably quoting instead of summarizing.

    Use complete_step() with your result when done.
    Use flag_stuck() only if you're genuinely blocked (missing data, access issue).
    Never flag stuck because the task is hard.
""").strip()

FACTORY_ADVERSARIAL = textwrap.dedent("""\
    You are an adversarial reviewer. A research task just completed. Your job:
    challenge the claims before they get compiled into a final report.

    For each significant claim in the step results:
    - Is the evidence actually what it claims to be? (RCT vs observational vs animal?)
    - Is the mechanism sound, or is it extrapolation?
    - Are there competing studies, frameworks, or interpretations not mentioned?
    - Is the dose, population, or context applicable to the goal?
    - For code/build claims: does the file exist at the named path with the
      claimed shape? Does the command named actually succeed on this machine?

    Grade each finding: CONFIRMED / DOWNGRADED / CONTESTED / OVERCLAIMED.
    Be specific — cite what's wrong, not just that something is uncertain.
    Skip claims that are clearly solid. Focus on what would change a decision.

    For each contested claim, ALSO supply `settled_by_command`: a single-line
    shell command whose exit code decisively settles whether your contestation
    is correct. Convention: exit 0 means your contestation was WRONG (the
    original claim stands); non-zero means your contestation was RIGHT.
    Examples: `test -f docs/protocol.md`, `command -v go`,
    `wc -l < web/index.html | awk '{exit ($1>=500)?0:1}'`.
    Set `settled_by_command` to null when the claim is genuinely un-probe-able
    (interpretations, dose-vs-context questions, scientific uncertainty).
    Don't invent commands that can't run — null is correct when you can't name
    a concrete check.

    Respond ONLY with JSON:
    {"contested_claims": [
        {"claim": "<what the step asserted>",
         "verdict": "CONFIRMED | DOWNGRADED | CONTESTED | OVERCLAIMED",
         "reason": "<one sentence>",
         "settled_by_command": "<single-line shell command>" or null}
    ]}
    Empty list is fine when nothing is worth contesting.
""").strip()

FACTORY_COMPILE = textwrap.dedent("""\
    Compile the step results into a final polished deliverable.
    Lead with the key findings. Be specific and actionable.
    No filler. No restating what was asked. Just the work product.

    If adversarial review findings are provided, incorporate them:
    downgrade contested claims, flag overclaimed mechanisms, and note where
    the evidence is weaker than the initial steps suggested.
""").strip()


# ---------------------------------------------------------------------------
# Minimal step data types
# ---------------------------------------------------------------------------

@dataclass
class ThinStep:
    index: int
    text: str
    status: str = "pending"   # pending | done | stuck
    result: str = ""
    tokens: int = 0


@dataclass
class ThinLoopResult:
    loop_id: str
    goal: str
    status: str           # done | stuck | partial
    steps: List[ThinStep]
    final_report: str
    total_tokens: int
    cost_usd: float
    elapsed_ms: int
    model: str


# ---------------------------------------------------------------------------
# Core thin loop
# ---------------------------------------------------------------------------

def run_factory_thin(
    goal: str,
    *,
    model: str = "cheap",
    max_steps: int = 8,
    verify: bool = False,
    max_retries: int = 2,
    step_timeout: Optional[int] = None,
    verbose: bool = False,
) -> ThinLoopResult:
    """Run the thin factory loop: decompose → execute → adversarial review → compile."""
    from llm import build_adapter, LLMMessage, ToolCall

    loop_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()
    total_tokens_in = 0
    total_tokens_out = 0

    def _log(msg: str):
        if verbose:
            print(f"[factory:thin:{loop_id}] {msg}", file=sys.stderr, flush=True)

    _log(f"model={model} goal={goal[:60]!r}")

    adapter = build_adapter(model=model, timeout=step_timeout)

    # --- Step 1: Decompose ---
    _log("decomposing...")
    decompose_resp = adapter.complete(
        [
            LLMMessage("system", FACTORY_DECOMPOSE),
            LLMMessage("user", f"Goal: {goal}\n\nDecompose into {max_steps} or fewer steps."),
        ],
        max_tokens=512,
        temperature=0.3,
    )
    total_tokens_in += decompose_resp.input_tokens
    total_tokens_out += decompose_resp.output_tokens

    step_texts = _parse_steps(decompose_resp.content, max_steps)
    if not step_texts:
        step_texts = [goal]
    _log(f"decomposed into {len(step_texts)} steps")

    steps = [ThinStep(index=i+1, text=t) for i, t in enumerate(step_texts)]

    # --- Step 2: Execute each step ---
    from llm import LLMTool
    from step_exec import EXECUTE_TOOLS_SHORT
    _tools = [LLMTool(name=t["name"], description=t["description"], parameters=t["parameters"])
              for t in EXECUTE_TOOLS_SHORT]

    completed_context = ""
    if verify:
        from step_exec import verify_step as _verify_step

    for step in steps:
        _log(f"executing step {step.index}: {step.text[:50]!r}")

        for _attempt in range(max_retries if verify else 1):
            context_block = f"\n\nCompleted so far:\n{completed_context}" if completed_context else ""
            retry_hint = f"\n\nPrevious attempt was rejected by verifier — produce more specific, complete output." if _attempt > 0 else ""
            resp = adapter.complete(
                [
                    LLMMessage("system", FACTORY_STEP),
                    LLMMessage("user",
                        f"Overall goal: {goal}\n\n"
                        f"Current step ({step.index}/{len(steps)}): {step.text}"
                        + context_block + retry_hint
                    ),
                ],
                tools=_tools,
                tool_choice="required",
                max_tokens=4096,
                temperature=0.2,
            )
            total_tokens_in += resp.input_tokens
            total_tokens_out += resp.output_tokens
            step.tokens += resp.input_tokens + resp.output_tokens

            candidate_result = ""
            candidate_status = "stuck"

            if resp.tool_calls:
                tc = resp.tool_calls[0]
                if tc.name == "complete_step":
                    candidate_status = "done"
                    _r = tc.arguments.get("result", "") or tc.arguments.get("summary", "")
                    candidate_result = _r if isinstance(_r, str) else str(_r) if _r else ""
                elif tc.name == "flag_stuck":
                    candidate_status = "stuck"
                    _r = tc.arguments.get("reason", "")
                    candidate_result = _r if isinstance(_r, str) else str(_r) if _r else ""
            elif resp.content and len(resp.content) > 20:
                candidate_status = "done"
                candidate_result = resp.content

            if candidate_status == "done" and verify:
                vresult = _verify_step(step.text, candidate_result, adapter)
                total_tokens_in += 0  # verify uses cheap adapter call; tokens not tracked separately
                if not vresult["passed"] and _attempt < max_retries - 1:
                    _log(f"step {step.index} verify RETRY ({_attempt+1}/{max_retries-1}): {vresult['reason'][:60]}")
                    continue  # retry
                elif not vresult["passed"]:
                    _log(f"step {step.index} verify FAILED after {max_retries} attempts — accepting anyway")

            step.status = candidate_status
            step.result = candidate_result
            if candidate_status == "done":
                completed_context += f"\nStep {step.index} ({step.text[:40]}): {step.result[:200]}"
                _log(f"step {step.index} done tokens={step.tokens}")
            else:
                _log(f"step {step.index} stuck: {step.result[:60]}")
            break  # exit retry loop

    # --- Step 3: Adversarial review ---
    done_steps = [s for s in steps if s.status == "done"]
    _log(f"running adversarial review on {len(done_steps)} steps...")

    step_summaries = "\n\n".join(
        f"Step {s.index}: {s.text}\n{s.result[:800]}"
        for s in done_steps
    )

    adversarial_findings = ""
    try:
        adv_resp = adapter.complete(
            [
                LLMMessage("system", FACTORY_ADVERSARIAL),
                LLMMessage("user",
                    f"Goal: {goal}\n\nStep results to challenge:\n{step_summaries}"
                ),
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        total_tokens_in += adv_resp.input_tokens
        total_tokens_out += adv_resp.output_tokens
        adversarial_findings = _ground_adversarial_findings(adv_resp.content)
        _log(f"adversarial review done tokens={adv_resp.input_tokens + adv_resp.output_tokens}")
    except Exception as exc:
        _log(f"adversarial review failed (non-fatal): {exc}")

    # --- Step 4: Compile final report ---
    _log(f"compiling report from {len(done_steps)}/{len(steps)} done steps...")

    adv_block = f"\n\nAdversarial review findings:\n{adversarial_findings}" if adversarial_findings else ""
    compile_resp = adapter.complete(
        [
            LLMMessage("system", FACTORY_COMPILE),
            LLMMessage("user",
                f"Goal: {goal}\n\nStep results:\n{step_summaries}"
                + adv_block +
                "\n\nCompile into a final structured report."
            ),
        ],
        max_tokens=4096,
        temperature=0.1,
    )
    total_tokens_in += compile_resp.input_tokens
    total_tokens_out += compile_resp.output_tokens
    final_report = compile_resp.content

    # --- Metrics ---
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    total_tokens = total_tokens_in + total_tokens_out

    try:
        from metrics import estimate_cost
        cost = estimate_cost(total_tokens_in, total_tokens_out, model=model)
    except Exception:
        cost = 0.0

    stuck_count = sum(1 for s in steps if s.status == "stuck")
    if len(done_steps) == 0:
        status = "stuck"
    elif stuck_count > 0:
        status = "partial"
    else:
        status = "done"

    _log(f"loop done status={status} tokens={total_tokens:,} cost=${cost:.4f} elapsed={elapsed_ms//1000}s")

    return ThinLoopResult(
        loop_id=loop_id,
        goal=goal,
        status=status,
        steps=steps,
        final_report=final_report,
        total_tokens=total_tokens,
        cost_usd=cost,
        elapsed_ms=elapsed_ms,
        model=model,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_steps(content: str, max_steps: int) -> List[str]:
    steps = extract_json(content, list, log_tag="factory_thin._parse_steps")
    if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
        return [s.strip() for s in steps if s.strip()][:max_steps]
    return []


def _ground_adversarial_findings(raw: str) -> str:
    """Parse reviewer JSON, probe each contestation, re-render as text.

    Falls back to the raw text when the reviewer didn't emit parseable JSON
    — the compiler can still consume prose findings. When parsing succeeds,
    every claim with a `settled_by_command` gets probed by
    quality_gate._probe_contested_claims, which mutates verdict to
    DISMISSED_BY_PROBE on exit 0 and emits a CLAIM_PROBED event.
    """
    if not raw:
        return ""
    parsed = extract_json(raw, dict, log_tag="factory_thin._ground_adversarial")
    if not isinstance(parsed, dict):
        return raw
    claims = parsed.get("contested_claims")
    if not isinstance(claims, list) or not claims:
        return raw

    try:
        from quality_gate import _probe_contested_claims
        grounded = _probe_contested_claims(claims)
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort
        log.warning("adversarial grounding failed (non-fatal): %s", exc)
        return raw

    lines = []
    for c in grounded:
        if not isinstance(c, dict):
            continue
        verdict = str(c.get("verdict") or "CONTESTED")
        claim_txt = str(c.get("claim") or "").strip()
        reason = str(c.get("reason") or "").strip()
        probe_status = c.get("probe_status")
        suffix = ""
        if probe_status == "dismissed":
            suffix = " (settled by probe — original contestation was wrong)"
        elif probe_status == "validated":
            suffix = " (probe confirmed contestation)"
        elif probe_status == "unrunnable":
            suffix = " (probe un-runnable; verdict stands)"
        lines.append(f"- [{verdict}] {claim_txt} — {reason}{suffix}")
    return "\n".join(lines) if lines else raw


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(description="Factory thin loop: decompose→execute→compile")
    p.add_argument("goal", help="Goal to execute")
    p.add_argument("--model", default="cheap", choices=["cheap", "mid", "power",
                   "claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"],
                   help="Model tier or full model ID")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--verify", action="store_true", help="Enable per-step Ralph verify loop")
    p.add_argument("--max-retries", type=int, default=2, help="Max retries per step when --verify is set")
    p.add_argument("--step-timeout", type=int, default=None,
                   help="Per-step subprocess timeout in seconds (default: 300). Increase for long research steps.")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--out", help="Write final report to file")
    args = p.parse_args()

    result = run_factory_thin(
        args.goal,
        model=args.model,
        max_steps=args.max_steps,
        verify=args.verify,
        max_retries=args.max_retries,
        step_timeout=args.step_timeout,
        verbose=args.verbose,
    )

    print(result.final_report)
    if args.out:
        Path(args.out).write_text(result.final_report)

    done = sum(1 for s in result.steps if s.status == "done")
    print(
        f"\n--- factory:thin stats ---\n"
        f"status: {result.status}  steps: {done}/{len(result.steps)} done\n"
        f"tokens: {result.total_tokens:,}  cost: ${result.cost_usd:.4f}  "
        f"time: {result.elapsed_ms//1000}s",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
