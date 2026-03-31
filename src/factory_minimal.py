"""Factory Mode — Minimal variant.

Single completion call. No loops, no scaffolding, no step tracking.
The model is handed the goal and a system prompt that describes the
desired output format. Everything else is up to the model.

This is the Bitter Lesson baseline: does the model outperform our
entire Mode 2 stack when given just a good prompt?

Usage:
    python3 factory_minimal.py "your goal here" [--model cheap|mid|power]
    poe-factory-minimal "your goal here"
"""

from __future__ import annotations

import sys
import time
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


FACTORY_SYSTEM = textwrap.dedent("""\
    You are an autonomous research and analysis agent.

    When given a goal, produce a complete, high-quality response in one pass.
    Do not hedge with "I would need to search..." — reason from your knowledge,
    flag uncertainty explicitly with evidence grades, and synthesize directly.

    Structure your response as:
    1. PLAN — how you'll approach this (2-4 bullet points)
    2. RESEARCH — findings per topic/compound/question. For each:
       - What is claimed
       - What evidence actually shows (cite study types: RCT, meta-analysis, case series, animal only)
       - Evidence grade: Tier 1 (RCT/meta), Tier 2 (observational/cohort), Tier 3 (animal/in-vitro/anecdotal)
       - Regulatory/legal status
       - Known risks
    3. INTERACTIONS — cross-compound and drug interactions, flagging severity
    4. SYNTHESIS — risk/benefit assessment, alternatives for weak-evidence items,
       key questions to raise with a domain expert
    5. CONFIDENCE — overall confidence in this response (high/medium/low) and
       what would change it

    Be specific. No generic warnings. No "consult a doctor" filler.
    Flag what you genuinely don't know with [UNCERTAIN] markers.
    This is research to inform a conversation with an expert, not medical advice.
""").strip()


def run_factory_minimal(
    goal: str,
    *,
    model: str = "cheap",
    verbose: bool = False,
) -> dict:
    """Run the minimal factory: one prompt, one completion, done."""
    from llm import build_adapter, LLMMessage

    started_at = time.monotonic()

    if verbose:
        print(f"[factory:minimal] model={model} goal={goal[:60]!r}", file=sys.stderr, flush=True)

    adapter = build_adapter(model=model)

    resp = adapter.complete(
        [
            LLMMessage("system", FACTORY_SYSTEM),
            LLMMessage("user", f"Goal: {goal}"),
        ],
        max_tokens=8192,
        temperature=0.2,
    )

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    tokens = resp.input_tokens + resp.output_tokens

    try:
        from metrics import estimate_cost
        cost = estimate_cost(resp.input_tokens, resp.output_tokens, model=model)
    except Exception:
        cost = 0.0

    if verbose:
        print(
            f"[factory:minimal] done tokens={tokens:,} cost=${cost:.4f} elapsed={elapsed_ms//1000}s",
            file=sys.stderr, flush=True,
        )

    return {
        "variant": "minimal",
        "model": model,
        "tokens": tokens,
        "tokens_in": resp.input_tokens,
        "tokens_out": resp.output_tokens,
        "cost_usd": cost,
        "elapsed_ms": elapsed_ms,
        "result": resp.content,
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="Factory minimal: single-shot completion")
    p.add_argument("goal", help="Goal to execute")
    p.add_argument("--model", default="cheap", choices=["cheap", "mid", "power",
                   "claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"],
                   help="Model tier or full model ID")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--out", help="Write result to file")
    args = p.parse_args()

    result = run_factory_minimal(args.goal, model=args.model, verbose=args.verbose)

    output = result["result"]
    if args.out:
        Path(args.out).write_text(output)
        print(f"[factory:minimal] wrote {len(output)} chars to {args.out}")
    else:
        print(output)

    print(
        f"\n--- factory:minimal stats ---\n"
        f"tokens: {result['tokens']:,}  cost: ${result['cost_usd']:.4f}  "
        f"time: {result['elapsed_ms']//1000}s",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
