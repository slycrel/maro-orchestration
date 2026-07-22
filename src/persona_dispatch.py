"""Persona dispatch — the owned "run this prompt with this persona" verb.

Swarm-review chunk 5b (2026-07-22). This pattern was re-improvised by hand
at least four times (2026-07-07 memory bake-off's three parallel research
agents; the 2026-07 r2 audit caught itself doing it a 4th time while
documenting the loss — knowledge-journey eras 09/11, hist-r2-02) and never
owned. This module is the owner: one-shot, persona-framed LLM dispatch with
attribution, no agent loop, no tools.

Distinct from its neighbors:
  - persona.spawn_persona — runs a full agent loop under a persona (steps,
    tools, memory). This module is a single completion.
  - workers.dispatch_worker — role-typed ticket execution. This is
    persona × prompt, caller-shaped.

Adapter policy (no silent spend): an explicit ``adapter`` wins; otherwise
the hosted-free tier is used when configured/opted-in; otherwise the
dispatch returns an error result. Falling back to a paid adapter is always
the caller's explicit choice.

``no_tools=True`` is pinned on every dispatch — one-shot judgments must not
tool-use (standing rule, tests/test_no_tools_contract.py lineage).

Consumers (chunk 5b): quality_gate's evidence-path council lenses,
lens_ablation's divergence harness, and the CLI verb below.

Usage:
    from persona_dispatch import dispatch_prompt, dispatch_panel
    r = dispatch_prompt("Critique this plan: ...", persona="critic")
    results = dispatch_panel("Same prompt", ["critic", "simplifier"])

CLI:
    PYTHONPATH=src python3 -m persona_dispatch "prompt" --persona critic
    PYTHONPATH=src python3 -m persona_dispatch "prompt" --panel critic,simplifier
    PYTHONPATH=src python3 -m persona_dispatch "prompt" --persona critic --model mid
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

log = logging.getLogger("maro.persona_dispatch")


@dataclass
class DispatchResult:
    """One persona × prompt completion, with attribution."""
    persona: str                 # persona name, or "(system)" for raw-system dispatch
    content: str                 # raw completion text ("" on failure)
    data: Optional[Any] = None   # parsed JSON when expect="json", else None
    source: str = ""             # model attribution (e.g. hosted_free:groq:llama-3.1-8b-instant)
    elapsed_ms: int = 0
    error: str = ""              # non-empty when the dispatch failed

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.content)


def _adapter_source(adapter) -> str:
    """Attribution string for an adapter — hosted-free gets provider:model."""
    provider = getattr(adapter, "_active_provider", "") or ""
    model_key = getattr(adapter, "model_key", "") or type(adapter).__name__
    if provider:
        return f"hosted_free:{provider}:{model_key}"
    return model_key


def _resolve_adapter(adapter):
    """Explicit adapter wins; else hosted-free; else (None, reason)."""
    if adapter is not None:
        return adapter, ""
    try:
        import hosted_free as _hf
        if _hf.available():
            hosted = _hf.build_hosted_free_adapter()
            if hosted is not None:
                return hosted, ""
    except Exception as exc:
        return None, f"hosted-free resolution failed: {exc}"
    return None, ("no adapter — pass one explicitly or configure the "
                  "hosted-free tier (validate.hosted_free.enabled)")


def dispatch_prompt(
    prompt: str,
    *,
    persona: Union[str, Any, None] = None,
    system: str = "",
    goal: str = "",
    adapter=None,
    registry=None,
    expect: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    purpose: str = "persona dispatch",
) -> DispatchResult:
    """One-shot completion of ``prompt`` framed by a persona and/or raw system text.

    Args:
        prompt:   The user-turn text to run.
        persona:  Persona name (resolved via PersonaRegistry) or a PersonaSpec.
        system:   Raw system text — used alone, or appended after the persona
                  prompt when both are given (persona frames, system contracts).
        goal:     Optional goal for persona template rendering ({{ goal }} etc.).
        adapter:  Explicit LLM adapter. None → hosted-free or error result.
        registry: PersonaRegistry override (tests).
        expect:   "json" → parse a JSON object into ``.data`` (extract_json).
        purpose:  Metrics purpose label.

    Never raises — failures land in ``DispatchResult.error``.
    """
    if persona is None and not system.strip():
        return DispatchResult(persona="(none)", content="",
                              error="dispatch needs a persona and/or system text")

    # --- Resolve persona framing ---
    persona_name = "(system)"
    system_parts: List[str] = []
    if persona is not None:
        try:
            from persona import PersonaRegistry, PersonaSpec, build_persona_system_prompt
            if isinstance(persona, PersonaSpec):
                spec = persona
            else:
                reg = registry or PersonaRegistry()
                spec = reg.load(str(persona))
                if spec is None:
                    return DispatchResult(
                        persona=str(persona), content="",
                        error=f"persona not found: {persona!r}")
            persona_name = spec.name
            system_parts.append(build_persona_system_prompt(spec, goal=goal))
        except Exception as exc:
            return DispatchResult(persona=str(persona), content="",
                                  error=f"persona load failed: {exc}")
    if system.strip():
        system_parts.append(system.strip())

    resolved, why_not = _resolve_adapter(adapter)
    if resolved is None:
        return DispatchResult(persona=persona_name, content="", error=why_not)

    t0 = time.monotonic()
    try:
        from llm import LLMMessage
        resp = resolved.complete(
            [
                LLMMessage("system", "\n\n".join(system_parts)),
                LLMMessage("user", prompt),
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            no_tools=True,  # pinned — one-shot judgments must not tool-use
            purpose=purpose,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        content = (getattr(resp, "content", "") or "").strip()
        data = None
        if expect == "json" and content:
            from llm_parse import extract_json
            data = extract_json(content, dict, log_tag="persona_dispatch")
        return DispatchResult(
            persona=persona_name,
            content=content,
            data=data,
            source=_adapter_source(resolved),
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.debug("dispatch failed persona=%s: %s", persona_name, exc)
        return DispatchResult(persona=persona_name, content="",
                              source=_adapter_source(resolved),
                              elapsed_ms=elapsed_ms, error=str(exc))


def dispatch_panel(
    prompt: str,
    personas: List[Union[str, Any]],
    **kwargs,
) -> List[DispatchResult]:
    """Run the same prompt through several personas; one result per seat.

    Serial by design (box rule: don't fan out subprocess/LLM work in
    parallel here; hosted-free seats are sub-second anyway). Failures are
    per-seat — one seat erroring never drops the others.
    """
    return [dispatch_prompt(prompt, persona=p, **kwargs) for p in personas]


# ---------------------------------------------------------------------------
# CLI — the dev-side verb that kept getting re-improvised
# ---------------------------------------------------------------------------

def _cli_main(argv=None) -> int:
    import argparse
    import json as _json

    p = argparse.ArgumentParser(
        prog="persona_dispatch",
        description="Run a prompt through one persona (or a panel) as a one-shot dispatch.",
    )
    p.add_argument("prompt", help="Prompt text, or '-' to read stdin")
    seat = p.add_mutually_exclusive_group(required=True)
    seat.add_argument("--persona", help="Single persona name")
    seat.add_argument("--panel", help="Comma-separated persona names")
    p.add_argument("--goal", default="", help="Goal text for persona template rendering")
    p.add_argument("--model", default="",
                   help="Paid model tier (explicit spend). Default: hosted-free tier.")
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Emit results as JSON")
    args = p.parse_args(argv)

    prompt = args.prompt
    if prompt == "-":
        import sys as _sys
        prompt = _sys.stdin.read()

    adapter = None
    if args.model:
        from llm import build_adapter
        adapter = build_adapter(model=args.model)

    names = [args.persona] if args.persona else [
        n.strip() for n in args.panel.split(",") if n.strip()]
    results = dispatch_panel(
        prompt, names, goal=args.goal, adapter=adapter,
        max_tokens=args.max_tokens, temperature=args.temperature,
    )

    if args.as_json:
        print(_json.dumps([{
            "persona": r.persona, "ok": r.ok, "source": r.source,
            "elapsed_ms": r.elapsed_ms, "error": r.error, "content": r.content,
        } for r in results], indent=2, ensure_ascii=False))
    else:
        for r in results:
            header = f"=== {r.persona} ({r.source or 'no adapter'}, {r.elapsed_ms}ms) ==="
            print(header)
            print(r.content if r.ok else f"ERROR: {r.error}")
            print()
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())
