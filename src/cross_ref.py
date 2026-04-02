"""Cross-reference check — second-source fact verification.

For factual claims in research output, queries a second source (fresh LLM
context with no prior output) to check whether the claim holds. Flags
disagreements so they surface alongside the output.

The key defense against hallucination: the verifier never sees the original
response context, so it can't pattern-match against the first answer.

Architecture:
  1. Extract verifiable claims from text (numbers, names, dates, mechanisms)
  2. For each claim: query fresh LLM with claim only (no source context)
  3. Classify: confirmed / disputed / unknown
  4. Return CrossRefReport with dispute details

Integration:
  - Called from quality_gate.py Pass 3 (optional)
  - Also usable standalone: `poe-cross-ref --text "..." --model cheap`
  - Results appended to quality output as [cross-ref-disputed:] annotations

Usage:
    from cross_ref import run_cross_ref
    report = run_cross_ref(step_result_text, adapter=adapter)
    if report.has_disputes:
        print(report.dispute_summary())

CLI:
    poe-cross-ref --text "The study found X in N=200 subjects..."
    poe-cross-ref --file result.txt
"""

from __future__ import annotations

import json
import logging
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.cross_ref")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_EXTRACT_CLAIMS_SYSTEM = textwrap.dedent("""\
    You are a fact extractor. Given a text, identify the specific verifiable
    factual claims — things that can be independently confirmed or refuted.

    Focus on:
    - Specific numbers, percentages, statistics (not ranges or approximations)
    - Named studies, papers, or data sources with specific findings
    - Historical or scientific facts stated with confidence
    - Mechanism claims ("X causes Y because Z")
    - Comparisons ("X is 3x faster than Y")

    Do NOT include:
    - Opinions or assessments ("this suggests...")
    - Definitions or explanations
    - Claims with obvious hedges ("might", "could", "some evidence")
    - General knowledge that's unambiguously true (water is wet)

    Respond with JSON:
    {
      "claims": [
        {
          "claim": "the exact claim as a standalone sentence",
          "category": "statistic" | "citation" | "mechanism" | "comparison" | "historical",
          "confidence_in_text": "high" | "medium"
        }
      ]
    }

    Return at most 5 claims. If no verifiable claims exist, return {"claims": []}.
""").strip()

_VERIFY_CLAIM_SYSTEM = textwrap.dedent("""\
    You are an independent fact-checker. You will be given a specific factual
    claim. Assess whether it is correct based on your knowledge.

    Be strict:
    - "confirmed": you have high confidence this is accurate
    - "disputed": you have reason to believe this is incorrect or significantly overstated
    - "unknown": you don't have enough knowledge to assess this confidently

    Do NOT look for reasons to confirm or deny — just assess honestly.
    If a claim is approximately right but numerically off, that's "disputed."

    Respond with JSON:
    {
      "status": "confirmed" | "disputed" | "unknown",
      "confidence": 0.0-1.0,
      "note": "one sentence explaining your assessment"
    }
""").strip()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ClaimVerification:
    claim: str
    category: str              # "statistic" | "citation" | "mechanism" | "comparison" | "historical"
    status: str                # "confirmed" | "disputed" | "unknown"
    confidence: float          # 0.0-1.0
    note: str
    elapsed_ms: int = 0


@dataclass
class CrossRefReport:
    verified: List[ClaimVerification]
    claims_extracted: int
    claims_checked: int
    disputes: List[ClaimVerification] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def has_disputes(self) -> bool:
        return bool(self.disputes)

    def dispute_summary(self) -> str:
        if not self.disputes:
            return "[cross-ref: no disputes found]"
        lines = [f"[cross-ref-disputed: {len(self.disputes)} claim(s)]"]
        for d in self.disputes:
            lines.append(f"  - DISPUTED ({d.confidence:.0%}): {d.claim}")
            if d.note:
                lines.append(f"    → {d.note}")
        return "\n".join(lines)

    def full_summary(self) -> str:
        total = self.claims_checked
        confirmed = sum(1 for v in self.verified if v.status == "confirmed")
        disputed = len(self.disputes)
        unknown = sum(1 for v in self.verified if v.status == "unknown")
        lines = [
            f"CrossRefReport: {total} claims checked — "
            f"{confirmed} confirmed / {disputed} disputed / {unknown} unknown",
            f"  extracted={self.claims_extracted} checked={self.claims_checked} elapsed={self.elapsed_ms}ms",
        ]
        if self.disputes:
            lines.append("")
            lines.extend(self.dispute_summary().splitlines())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Dict[str, Any]:
    """Pull first {...} block from text and parse."""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def extract_verifiable_claims(
    text: str,
    adapter,
    *,
    max_claims: int = 5,
) -> List[Dict[str, str]]:
    """Ask LLM to identify verifiable factual claims in text.

    Returns list of {"claim": ..., "category": ..., "confidence_in_text": ...} dicts.
    Never raises — returns empty list on any failure.
    """
    if not text or not text.strip():
        return []

    # Truncate if too long (claim extraction doesn't need everything)
    excerpt = text[:3000] if len(text) > 3000 else text

    try:
        from llm import LLMMessage
        messages = [
            LLMMessage(role="system", content=_EXTRACT_CLAIMS_SYSTEM),
            LLMMessage(role="user", content=f"TEXT:\n{excerpt}"),
        ]
        resp = adapter.complete(messages, tools=[])
        parsed = _extract_json(resp.content or "")
        claims = parsed.get("claims", [])
        # Safety: limit and validate
        result = []
        for c in claims[:max_claims]:
            if isinstance(c, dict) and "claim" in c:
                result.append({
                    "claim": str(c["claim"]),
                    "category": str(c.get("category", "general")),
                    "confidence_in_text": str(c.get("confidence_in_text", "medium")),
                })
        return result
    except Exception as exc:
        log.warning("cross_ref: claim extraction failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Claim verification
# ---------------------------------------------------------------------------

def verify_single_claim(
    claim: str,
    category: str,
    adapter,
) -> ClaimVerification:
    """Verify a single claim using a fresh LLM context.

    The adapter's LLM never sees the original text — just the claim itself.
    This prevents confirmation bias from the original response.

    Never raises — returns "unknown" on any failure.
    """
    t0 = time.monotonic()
    try:
        from llm import LLMMessage
        messages = [
            LLMMessage(role="system", content=_VERIFY_CLAIM_SYSTEM),
            LLMMessage(role="user", content=f"CLAIM: {claim}"),
        ]
        resp = adapter.complete(messages, tools=[])
        parsed = _extract_json(resp.content or "")
        elapsed = int((time.monotonic() - t0) * 1000)
        return ClaimVerification(
            claim=claim,
            category=category,
            status=str(parsed.get("status", "unknown")),
            confidence=float(parsed.get("confidence", 0.5)),
            note=str(parsed.get("note", "")),
            elapsed_ms=elapsed,
        )
    except Exception as exc:
        log.warning("cross_ref: verify claim failed (%r): %s", claim[:40], exc)
        elapsed = int((time.monotonic() - t0) * 1000)
        return ClaimVerification(
            claim=claim,
            category=category,
            status="unknown",
            confidence=0.0,
            note=f"(verification failed: {exc})",
            elapsed_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_cross_ref(
    text: str,
    *,
    adapter=None,
    dry_run: bool = False,
    max_claims: int = 5,
    dispute_threshold: float = 0.6,
) -> CrossRefReport:
    """Run the full cross-reference pipeline on a text.

    Args:
        text: The research output or step result to check
        adapter: LLM adapter (builds cheap adapter if None)
        dry_run: If True, return stub report without LLM calls
        max_claims: Max claims to extract and verify (default 5)
        dispute_threshold: confidence threshold for marking a claim "disputed" (default 0.6)

    Returns:
        CrossRefReport with verified claims and dispute list
    """
    t0 = time.monotonic()

    if dry_run or not text or not text.strip():
        elapsed = int((time.monotonic() - t0) * 1000)
        return CrossRefReport(
            verified=[],
            claims_extracted=0,
            claims_checked=0,
            elapsed_ms=elapsed,
        )

    if adapter is None:
        try:
            from llm import build_adapter
            adapter = build_adapter("cheap")
        except Exception as exc:
            log.warning("cross_ref: could not build adapter: %s", exc)
            elapsed = int((time.monotonic() - t0) * 1000)
            return CrossRefReport(
                verified=[],
                claims_extracted=0,
                claims_checked=0,
                elapsed_ms=elapsed,
            )

    # Step 1: Extract claims
    raw_claims = extract_verifiable_claims(text, adapter, max_claims=max_claims)
    claims_extracted = len(raw_claims)

    if not raw_claims:
        elapsed = int((time.monotonic() - t0) * 1000)
        return CrossRefReport(
            verified=[],
            claims_extracted=0,
            claims_checked=0,
            elapsed_ms=elapsed,
        )

    # Step 2: Verify each claim
    verified: List[ClaimVerification] = []
    for raw in raw_claims:
        v = verify_single_claim(raw["claim"], raw["category"], adapter)
        verified.append(v)

    # Step 3: Identify disputes
    disputes = [
        v for v in verified
        if v.status == "disputed" and v.confidence >= dispute_threshold
    ]

    elapsed = int((time.monotonic() - t0) * 1000)
    return CrossRefReport(
        verified=verified,
        claims_extracted=claims_extracted,
        claims_checked=len(verified),
        disputes=disputes,
        elapsed_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Quality gate integration helper
# ---------------------------------------------------------------------------

def cross_ref_annotation(report: CrossRefReport) -> str:
    """Format CrossRefReport as an annotation string for appending to step output.

    Returns empty string if no disputes, so callers can safely always call this.
    """
    if not report.has_disputes:
        return ""
    return "\n\n" + report.dispute_summary()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="poe-cross-ref",
        description="Cross-reference factual claims in research output against a fresh LLM context.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="Text to check")
    group.add_argument("--file", metavar="FILE", help="File containing text to check")
    p.add_argument("--model", default="cheap", help="Model tier (default: cheap)")
    p.add_argument("--max-claims", type=int, default=5, help="Max claims to check")
    p.add_argument("--dispute-threshold", type=float, default=0.6,
                   help="Confidence threshold for dispute (default: 0.6)")
    p.add_argument("--dry-run", action="store_true", help="No LLM calls, stub output")
    args = p.parse_args(argv)

    if args.file:
        from pathlib import Path
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: could not read {args.file}: {exc}", flush=True)
            return 1
    else:
        text = args.text

    adapter = None
    if not args.dry_run:
        try:
            from llm import build_adapter
            adapter = build_adapter(args.model)
        except Exception as exc:
            print(f"WARNING: could not build adapter ({exc}), using dry-run mode", flush=True)
            args.dry_run = True

    report = run_cross_ref(
        text,
        adapter=adapter,
        dry_run=args.dry_run,
        max_claims=args.max_claims,
        dispute_threshold=args.dispute_threshold,
    )

    print(report.full_summary())
    return 1 if report.has_disputes else 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())
