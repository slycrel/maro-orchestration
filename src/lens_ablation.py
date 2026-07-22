"""Triad ablation harness — do N review seats diverge, or agree at Nx cost?

The era-04 (2026-04) experiment that never ran: "before building
PM/engineer/architect inversion, run the cheap test: do three personas draw
DIFFERENT constraint lines, or near-identical lists at 3x cost?" (Phase 65
review demand; killed by the 04-26 A/B pause — knowledge-journey era 04.)
Chunk 5b ships it as the sizing instrument for the council's graduation
decision: the `strict:` gate on the evidence-path council comes off on
divergence data, not vibes.

Two arms over the SAME payload:
  costume  — the three retired council framings (devil's advocate / domain
             skeptic / implementation critic): same evidence, different
             prompt costume. Control arm, preserved verbatim from the
             pre-chunk-5b quality_gate council.
  evidence — the three evidence-path lenses quality_gate now runs
             (transcript-aware / artifact-only / probe-armed): same
             adjudication contract, different evidence per seat.

For each arm: per-seat verdicts, mean pairwise concern overlap (token-set
Jaccard), distinct-catch count per seat, typed finding codes. The reading:
high overlap = the extra seats are costume (paying Nx for one opinion);
low overlap = genuine angle diversity. The harness reports; it does not
adjudicate.

Offline instrument, no runtime wiring. Runs on the hosted-free tier by
default via persona_dispatch ($0); --model spends explicitly.

Usage:
    PYTHONPATH=src python3 -m lens_ablation --goal "..." --file output.txt
    PYTHONPATH=src python3 -m lens_ablation --goal "..." --file out.txt \\
        --steps-json steps.json --out output/ablation.md

steps.json: [{"text": "step text", "result": "step result"}, ...] — gives
the transcript-aware lens a real trail. Without it the payload is a single
synthetic step and the transcript arm degenerates to artifact-only (the
report says so; degenerate arms are labeled, never silently averaged).
"""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass, field
from itertools import combinations
from types import SimpleNamespace
from typing import List, Optional

log = logging.getLogger("maro.lens_ablation")


# ---------------------------------------------------------------------------
# Control arm: the retired same-context costume framings, verbatim from the
# pre-chunk-5b quality_gate._COUNCIL_FRAMINGS (2026-04 era).
# ---------------------------------------------------------------------------

_COSTUME_FRAMINGS = [
    (
        "devil_advocate",
        textwrap.dedent("""\
            You are the devil's advocate. Assume the output is fundamentally flawed.
            Find what's missing, what assumptions are unjustified, and what conclusions
            the research failed to reach that it should have.

            Be specific. Name gaps. Don't say "could be more thorough" — say exactly
            what was omitted and why it matters for the stated goal.

            Respond with JSON:
            {
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": ["specific concern 1", "specific concern 2"],
              "most_critical_gap": "the single biggest missing piece"
            }
        """).strip(),
    ),
    (
        "domain_skeptic",
        textwrap.dedent("""\
            You are a domain skeptic. Challenge the methodology and assumptions.
            Identify where the research draws on weak evidence, misapplies domain
            knowledge, or reaches conclusions a domain expert would dispute.

            Focus on: wrong evidence tiers (animal vs human), confounded variables,
            contested mechanisms, population mismatch, missing context.

            Respond with JSON:
            {
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": ["specific concern 1", "specific concern 2"],
              "most_critical_gap": "the single biggest methodological flaw"
            }
        """).strip(),
    ),
    (
        "implementation_critic",
        textwrap.dedent("""\
            You are the implementation critic. Focus on actionability.
            Is this output actually usable? Can someone act on it?
            Are there missing specifics (doses, timelines, tools, steps) that block
            real-world use? Are recommendations internally consistent?

            Respond with JSON:
            {
              "verdict": "WEAK" or "ACCEPTABLE" or "STRONG",
              "concerns": ["specific concern 1", "specific concern 2"],
              "most_critical_gap": "what would block someone from actually using this"
            }
        """).strip(),
    ),
]


# ---------------------------------------------------------------------------
# Divergence math
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is",
    "are", "was", "be", "with", "not", "no", "that", "this", "it", "its",
    "as", "at", "by", "from",
}


def _tokens(text: str) -> set:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 2}


def concern_overlap(concerns_a: List[str], concerns_b: List[str]) -> float:
    """Token-set Jaccard between two seats' pooled concern text (0..1)."""
    ta, tb = _tokens(" ".join(concerns_a)), _tokens(" ".join(concerns_b))
    if not ta and not tb:
        return 1.0  # both empty = perfect agreement (nothing caught)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class SeatReading:
    seat: str
    verdict: str            # WEAK/ACCEPTABLE/STRONG, or "(no verdict)"
    concerns: List[str] = field(default_factory=list)
    finding_codes: List[str] = field(default_factory=list)
    source: str = ""
    error: str = ""


@dataclass
class ArmResult:
    arm: str                # "costume" | "evidence"
    seats: List[SeatReading]
    calls: int = 0
    degenerate_note: str = ""

    @property
    def mean_pairwise_overlap(self) -> Optional[float]:
        ok = [s for s in self.seats if not s.error]
        if len(ok) < 2:
            return None
        pairs = list(combinations(ok, 2))
        return sum(concern_overlap(a.concerns, b.concerns) for a, b in pairs) / len(pairs)

    @property
    def distinct_catches(self) -> dict:
        """Per seat: concerns whose token sets overlap <0.3 with every other seat."""
        ok = [s for s in self.seats if not s.error]
        out = {}
        for s in ok:
            others = [o for o in ok if o is not s]
            distinct = 0
            for c in s.concerns:
                if all(concern_overlap([c], o.concerns) < 0.3 for o in others):
                    distinct += 1
            out[s.seat] = distinct
        return out


# ---------------------------------------------------------------------------
# Arm runners
# ---------------------------------------------------------------------------

def _fake_steps(output_text: str) -> list:
    return [SimpleNamespace(index=1, status="done",
                            text="(synthetic step — ablation harness)",
                            result=output_text)]


def _steps_from_dicts(steps: list) -> list:
    return [
        SimpleNamespace(index=i + 1, status="done",
                        text=str(d.get("text", "")), result=str(d.get("result", "")))
        for i, d in enumerate(steps)
    ]


def _reading_from_data(seat: str, data: Optional[dict], source: str, error: str) -> SeatReading:
    from llm_parse import safe_str, safe_list
    from finding_codes import parse_finding_codes
    if not data:
        return SeatReading(seat=seat, verdict="(no verdict)", source=source,
                           error=error or "unparsable response")
    raw_concerns = data.get("concerns", [])
    concerns: List[str] = []
    if isinstance(raw_concerns, list):
        for c in raw_concerns:
            if isinstance(c, str) and c.strip():
                concerns.append(c.strip())
            elif isinstance(c, dict) and c.get("claim"):
                concerns.append(safe_str(c.get("claim")))
    return SeatReading(
        seat=seat,
        verdict=safe_str(data.get("verdict", "")).upper() or "(no verdict)",
        concerns=concerns[:6],
        finding_codes=parse_finding_codes("\n".join(concerns), strict=False),
        source=source,
    )


def run_costume_arm(goal: str, steps: list, *, adapter=None) -> ArmResult:
    """Three costume seats over the same last-3-steps evidence (the old council)."""
    from persona_dispatch import dispatch_prompt

    review = steps[-3:]
    summary = "\n\n".join(
        f"Step {getattr(s, 'index', i + 1)}: {getattr(s, 'text', '?')[:80]}\n"
        f"Result: {(getattr(s, 'result', '') or '')[:500]}"
        for i, s in enumerate(review)
    )
    payload = f"Goal: {goal[:300]}\n\nOutput to review:\n{summary}"

    seats = []
    for name, system in _COSTUME_FRAMINGS:
        r = dispatch_prompt(payload, system=system, adapter=adapter, expect="json",
                            max_tokens=700, temperature=0.4,
                            purpose=f"lens ablation costume {name}")
        seats.append(_reading_from_data(name, r.data, r.source, r.error))
    return ArmResult(arm="costume", seats=seats, calls=len(seats))


def run_evidence_arm(goal: str, steps: list, *, adapter=None) -> ArmResult:
    """The three evidence-path lenses quality_gate now runs, reused verbatim."""
    from persona_dispatch import dispatch_prompt
    from quality_gate import _EVIDENCE_LENSES

    seats = []
    for name, system, builder in _EVIDENCE_LENSES:
        r = dispatch_prompt(builder(goal, steps), system=system, adapter=adapter,
                            expect="json", max_tokens=700, temperature=0.4,
                            purpose=f"lens ablation evidence {name}")
        seats.append(_reading_from_data(name, r.data, r.source, r.error))
    note = ""
    if len(steps) < 2:
        note = ("transcript_aware ran on a single synthetic step — it "
                "degenerates to artifact-only here; pass --steps-json for a "
                "real trail")
    return ArmResult(arm="evidence", seats=seats, calls=len(seats), degenerate_note=note)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_report(goal: str, arms: List[ArmResult]) -> str:
    lines = [
        "# Triad ablation — seat divergence report",
        "",
        f"Goal: {goal[:200]}",
        "",
        "Question (era 04, verbatim intent): do N seats draw DIFFERENT lines,",
        "or near-identical lists at Nx cost? High overlap = costume; low",
        "overlap = genuine angle diversity. This report measures; the",
        "graduation call stays human.",
        "",
    ]
    for arm in arms:
        lines.append(f"## Arm: {arm.arm} ({arm.calls} calls)")
        if arm.degenerate_note:
            lines.append(f"\n> NOTE: {arm.degenerate_note}")
        lines.append("")
        for s in arm.seats:
            if s.error:
                lines.append(f"- **{s.seat}**: ERROR — {s.error}")
                continue
            codes = f" codes={','.join(s.finding_codes)}" if s.finding_codes else ""
            lines.append(f"- **{s.seat}** [{s.verdict}] ({s.source}){codes}")
            for c in s.concerns:
                lines.append(f"    - {c[:200]}")
        overlap = arm.mean_pairwise_overlap
        lines.append("")
        if overlap is None:
            lines.append("Mean pairwise concern overlap: n/a (<2 seats answered)")
        else:
            reading = ("near-identical (costume territory)" if overlap >= 0.8
                       else "diverged (genuine angles)" if overlap < 0.5
                       else "mixed")
            lines.append(f"Mean pairwise concern overlap: {overlap:.2f} — {reading}")
        dc = arm.distinct_catches
        if dc:
            lines.append("Distinct catches per seat: "
                         + ", ".join(f"{k}={v}" for k, v in sorted(dc.items())))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_main(argv=None) -> int:
    import argparse
    import json as _json
    from pathlib import Path

    p = argparse.ArgumentParser(
        prog="lens_ablation",
        description="Era-04 triad ablation: costume seats vs evidence-path lenses on one payload.",
    )
    p.add_argument("--goal", required=True, help="The goal the output was produced for")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="File containing the output text to review")
    src.add_argument("--text", help="Output text inline")
    p.add_argument("--steps-json", help="JSON file: [{'text':..., 'result':...}, ...]")
    p.add_argument("--model", default="",
                   help="Paid model tier (explicit spend). Default: hosted-free tier.")
    p.add_argument("--arm", choices=["costume", "evidence", "both"], default="both")
    p.add_argument("--out", help="Also write the report to this path")
    args = p.parse_args(argv)

    if args.file:
        try:
            output_text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: could not read {args.file}: {exc}", flush=True)
            return 1
    else:
        output_text = args.text

    if args.steps_json:
        try:
            steps = _steps_from_dicts(_json.loads(Path(args.steps_json).read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"ERROR: could not read steps json: {exc}", flush=True)
            return 1
    else:
        steps = _fake_steps(output_text)

    adapter = None
    if args.model:
        from llm import build_adapter
        adapter = build_adapter(model=args.model)

    arms: List[ArmResult] = []
    if args.arm in ("costume", "both"):
        arms.append(run_costume_arm(args.goal, steps, adapter=adapter))
    if args.arm in ("evidence", "both"):
        arms.append(run_evidence_arm(args.goal, steps, adapter=adapter))

    report = render_report(args.goal, arms)
    print(report)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report + "\n", encoding="utf-8")
        print(f"\n[written to {out_path}]")
    errored = sum(1 for a in arms for s in a.seats if s.error)
    return 1 if errored == sum(len(a.seats) for a in arms) else 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())
