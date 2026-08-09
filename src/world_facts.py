#!/usr/bin/env python3
"""Run-scoped world-fact ledger — plan-native home for non-action findings.

WORLD_FACTS_DESIGN slice 1 (Jeremy 2026-08-02: *"we need to add non-action
types to the planner. I think 2 types of world facts: anecdotal/accidentally
found and hypothesis type findings (pattern recognition/ideas)"*; §7
decisions taken 2026-08-06).

A run that stumbles onto a fact ("archive X is blocked", "the dataset has a
second sheet") or forms a pattern hypothesis ("these failures cluster by
transport") previously had no plan-native place to put it — it leaked into
step prose or died with the step.

Design notes (mirrors terrain.py deliberately — same seam, same discipline):
- **Declared, not scanned.** Facts arrive via a `world_facts` side-channel
  on the executor's completion tool (mirroring the `decisions` directive):
  the declarer says what kind it is; no LLM classification pass.
- **Run-scoped with checkpoint carry.** The ledger lives on LoopContext and
  rides the run checkpoint, so a resume or replan sees the facts, not just
  the surviving steps (the plan-native half of the ask).
- **Hypothesis quarantine (§7.1, decided).** `render()` emits ANECDOTAL
  facts only. An unconfirmed pattern guess injected as fact is exactly the
  self-referential-hedging failure §4c routed around; hypotheses are
  ledgered (forward-compatible, slice-2 lands them in `observe_pattern`)
  but never enter step prompts until confirmed.
- **Observation only.** Nothing here changes step status or routing; the
  render is one advisory contribution through the §6 ledger seam. Empty
  render when nothing is known — the ContributionLedger's hard contract is
  that zero contributions leave prompts byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

KIND_ANECDOTAL = "anecdotal"
KIND_HYPOTHESIS = "hypothesis"
KINDS = (KIND_ANECDOTAL, KIND_HYPOTHESIS)

# Per-step declaration cap (§7.3: build-time tuning). Malformed entries must
# not consume it — cap applied AFTER validation, same rule the decisions
# directive learned in chunk-3 review.
MAX_FACTS_PER_STEP = 3
# Cap the rendered block: facts are a hint, not a wall of text (§4
# injection-regression falsifier watches this).
MAX_RENDERED_FACTS = 10
MAX_FACT_CHARS = 300
MAX_EVIDENCE_CHARS = 300


@dataclass
class WorldFact:
    """One declared fact, with the evidence that says so."""
    kind: str
    fact: str
    evidence: str
    first_step: int
    hits: int = 1
    steps: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "fact": self.fact, "evidence": self.evidence,
            "first_step": self.first_step, "hits": self.hits,
            "steps": list(self.steps),
        }


@dataclass
class WorldFactLedger:
    """Run-scoped accumulator. Lives on LoopContext; rides the checkpoint."""
    facts: Dict[str, WorldFact] = field(default_factory=dict)

    @staticmethod
    def _key(kind: str, fact: str) -> str:
        # Keyed by kind + normalized text: the same finding restated in a
        # later step bumps hits instead of duplicating a ledger row.
        return f"{kind}:{' '.join(fact.lower().split())}"

    def observe(self, kind: str, fact: str, evidence: str,
                step_idx: int) -> bool:
        """Record one declared fact. Returns True if newly ledgered.

        First evidence wins, matching terrain's first-reason-wins: the
        original observation is the one later corroborations point back to.
        """
        fact = (fact or "").strip()
        if not fact or kind not in KINDS:
            return False
        key = self._key(kind, fact)
        existing = self.facts.get(key)
        if existing is None:
            self.facts[key] = WorldFact(
                kind=kind, fact=fact[:MAX_FACT_CHARS],
                evidence=(evidence or "").strip()[:MAX_EVIDENCE_CHARS],
                first_step=step_idx, steps=[step_idx])
            return True
        existing.hits += 1
        if step_idx not in existing.steps:
            existing.steps.append(step_idx)
        return False

    def anecdotal(self) -> List[WorldFact]:
        return [f for f in self.facts.values() if f.kind == KIND_ANECDOTAL]

    def hypotheses(self) -> List[WorldFact]:
        """Slice-2 seam: finalize routes these into observe_pattern()."""
        return [f for f in self.facts.values() if f.kind == KIND_HYPOTHESIS]

    def render(self) -> str:
        """Advisory known-this-run block — ANECDOTAL ONLY (§7.1 quarantine).

        Empty render is load-bearing: zero contributions must leave prompts
        byte-identical.
        """
        rows = sorted(self.anecdotal(), key=lambda f: (-f.hits, f.fact))
        if not rows:
            return ""
        lines = [
            "Facts already established THIS RUN — treat as known; do not "
            "re-derive them:"
        ]
        for f in rows[:MAX_RENDERED_FACTS]:
            line = f"  - {f.fact}"
            if f.evidence:
                line += f" (evidence: {f.evidence})"
            if f.hits > 1:
                line += f" [{f.hits}× since step {f.first_step}]"
            lines.append(line)
        if len(rows) > MAX_RENDERED_FACTS:
            lines.append(f"  …and {len(rows) - MAX_RENDERED_FACTS} more.")
        return "\n".join(lines)

    # -- checkpoint carry -------------------------------------------------

    def to_list(self) -> List[Dict[str, Any]]:
        return [f.to_dict() for f in self.facts.values()]

    @classmethod
    def from_list(cls, rows: Optional[Iterable[Any]]) -> "WorldFactLedger":
        """Rebuild from checkpoint rows. Malformed rows are dropped, never
        raised — a corrupt checkpoint must not block a resume."""
        ledger = cls()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            kind = row.get("kind", "")
            fact = str(row.get("fact", "")).strip()
            if kind not in KINDS or not fact:
                continue
            wf = WorldFact(
                kind=kind, fact=fact[:MAX_FACT_CHARS],
                evidence=str(row.get("evidence", "")).strip()[:MAX_EVIDENCE_CHARS],
                first_step=int(row.get("first_step", 0) or 0),
                hits=max(1, int(row.get("hits", 1) or 1)),
                steps=[int(s) for s in row.get("steps", [])
                       if isinstance(s, (int, float))],
            )
            ledger.facts[cls._key(kind, wf.fact)] = wf
        return ledger


def clean_declared(raw: Any) -> List[Dict[str, str]]:
    """Validate one step's declared world_facts payload.

    Returns up to MAX_FACTS_PER_STEP validated {kind, fact, evidence} dicts.
    Cap applied AFTER validation so malformed entries don't consume the
    budget. Unknown kinds are dropped (the declarer says which kind it is —
    no reclassification, no guessing). Never raises.
    """
    cleaned: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return cleaned
    for entry in raw:
        if len(cleaned) >= MAX_FACTS_PER_STEP:
            break
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        fact = str(entry.get("fact", "")).strip()[:MAX_FACT_CHARS]
        evidence = str(entry.get("evidence", "")).strip()[:MAX_EVIDENCE_CHARS]
        if kind in KINDS and fact:
            cleaned.append({"kind": kind, "fact": fact, "evidence": evidence})
    return cleaned


def world_facts_enabled() -> bool:
    """Single kill switch for capture (render of an empty ledger is already
    a no-op, so gating capture gates everything)."""
    try:
        from config import get as _cfg_get
        return bool(_cfg_get("world_facts.enabled", True))
    except Exception:
        return True
