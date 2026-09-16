"""Prerequisite gate for the sequential execute loop (LoopsBench follow-up, 2026-09-16).

The planner already parses `[after:N,M]` tags into an explicit dependency
map and computes a ready-frontier (`planner.parse_dependencies` /
`build_execution_levels`). Until this module, the SEQUENTIAL lane consulted
neither: a step whose declared prerequisite ended blocked still ran — after
a token-runaway "step dies, run advances", a milestone-advisor skip, or a
stuck-streak fall-through — and produced work on top of a foundation that
never landed. The DAG lane (`loop_parallel._run_steps_dag`) had the same
hole — a completed dep was released to its dependents regardless of its
outcome — and now marks dependents of a blocked dep blocked without
running them (explicit edges hard, sequential edges soft, same classes as
here). Both lanes read this module's grammar.

Doctrine (LoopsBench "Dependency Planning Gap", Microsoft arXiv:2608.00267):
the plan's own prerequisite edges are an execution contract, not a hint.

Two edge classes, gated differently:

- EXPLICIT edges — the step text carries `[after:N,M]`. The author declared
  the prerequisite; an unmet one is a HARD gate: the dependent is recorded
  blocked ("not executed — prerequisite step N ended blocked") without an
  adapter call, and the loop moves to the next step so independent branches
  still run. Closure sees the blocked row like any other.
- IMPLICIT edges — the sequential default (untagged step N depends on N-1).
  Soft by default: logged, never enforced, because every "advance past a
  dead step" path the loop already has would otherwise cascade into
  blocking the rest of the run. `execution.gate_implicit_prerequisites`
  (docs/DEFAULTS.md) hardens them for operators who want LoopsBench-strict
  semantics.

"Unmet" means the prerequisite's LATEST recorded outcome is blocked or
skipped. A prerequisite with NO recorded outcome (replaced by sub-steps,
skipped by the milestone advisor before this gate existed, or a plan number
outside the plan) is UNKNOWN and never gates — the gate errs toward running
work, never toward refusing it on absent evidence (positive-evidence rule).

Plan identity: the gate resolves a tag's plan number POSITIONALLY through
`step_indices` (shaped step i+1 ↔ NEXT.md item). That mapping is only the
planner's numbering when the shaped plan has one entry per parsed step,
the run is not a resume (a resumed suffix is re-numbered from 1 while its
tags still name the original plan), and no item index repeats.
`plan_identity_intact` checks exactly that; when it fails the gate
degrades every edge to SOFT (logged, never enforced) and the loop warns —
refusing work on a mis-numbered edge is the worse error. Durable plan-node
ids (a tag naming an item rather than a position) are the design residue
that closes this for good; see BACKLOG.

Stdlib-only, like terrain.py / world_facts.py: imports nothing from the
loop so the loop can import it freely. planner.py imports the grammar
from here so the two cannot drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# THE [after:N,M] grammar — planner.py and scripts/prereq-census.py import
# it from here. Whitespace-tolerant inside the tag (`[after: 1, 3]` is the
# same edge as `[after:1,3]`): a planner that spaces its list must not
# silently lose the edge. The tag must be terminal.
AFTER_RE = re.compile(r'\[after:\s*(\d+(?:\s*,\s*\d+)*)\s*\]\s*$')
_AFTER_RE = AFTER_RE


def after_numbers(match_group: str) -> set:
    """Plan numbers from AFTER_RE group(1), tolerant of spaces."""
    return {int(x) for x in match_group.split(",") if x.strip()}


def plan_identity_intact(step_indices: Sequence[int], deps: Optional[Dict[int, Any]],
                         *, resumed: bool = False) -> Tuple[bool, str]:
    """Can plan numbers be resolved positionally through `step_indices`?

    Returns (ok, reason). Not ok when: the run resumed from a checkpoint
    (suffix re-numbered), the shaped step count differs from the parsed
    plan (`deps` has one key per parsed step), or an item index repeats.
    """
    if resumed:
        return False, "resumed run: remaining steps are re-numbered from 1"
    n_shaped = len(step_indices or ())
    n_parsed = len(deps) if deps else n_shaped
    if deps and n_parsed != n_shaped:
        return False, f"plan reshaped: {n_parsed} parsed step(s) became {n_shaped}"
    seen = set()
    for item in step_indices or ():
        try:
            item_i = int(item)
        except (TypeError, ValueError):
            continue
        if item_i < 0:
            continue
        if item_i in seen:
            return False, f"item index {item_i} appears twice in the plan"
        seen.add(item_i)
    return True, ""

UNMET_STATUSES = frozenset({"blocked", "skipped"})


@dataclass
class GateVerdict:
    """Result of `prerequisite_verdict` for one step about to execute."""
    ready: bool = True
    # True when an unmet edge is enforced (explicit, or implicit + config).
    hard: bool = False
    # Whether the checked edges came from an [after:] tag (True) or the
    # sequential default (False). None when the step had no plan number and
    # no tag — nothing to check.
    explicit: Optional[bool] = None
    plan_no: int = 0
    # (prerequisite plan number, its latest status, its text) per unmet edge.
    unmet: List[Tuple[int, str, str]] = field(default_factory=list)
    # Prerequisite plan numbers with no recorded outcome — reported, never gated.
    unknown: List[int] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if not self.unmet:
            return ""
        parts = [f"step {k} ended {status}" for k, status, _ in self.unmet]
        kind = "declared" if self.explicit else "sequential"
        return f"{kind} prerequisite not met: " + "; ".join(parts)


def explicit_deps(step_text: str) -> Optional[set]:
    """Per-step [after:N,M] reader. None when the step carries no tag."""
    m = AFTER_RE.search(step_text or "")
    if not m:
        return None
    return after_numbers(m.group(1))


def plan_numbers(step_indices: Sequence[int]) -> Dict[int, int]:
    """Map NEXT.md item index → 1-based plan number for the ORIGINAL plan.

    `step_indices[i]` is the item index the loop assigned to plan step i+1.
    Recovery sub-steps carry item index -1 and never map (they inherit no
    plan number, so they neither gate nor satisfy anything by themselves).
    """
    out: Dict[int, int] = {}
    for pos, item in enumerate(step_indices or (), 1):
        try:
            item_i = int(item)
        except (TypeError, ValueError):
            continue
        if item_i < 0 or item_i in out:
            continue
        out[item_i] = pos
    return out


def latest_status_by_item(step_outcomes: Iterable[Any]) -> Dict[int, str]:
    """Latest recorded status per item index (a retry's later row wins)."""
    out: Dict[int, str] = {}
    for oc in step_outcomes or ():
        idx = getattr(oc, "index", None)
        if idx is None and isinstance(oc, dict):
            idx = oc.get("index")
        status = getattr(oc, "status", None)
        if status is None and isinstance(oc, dict):
            status = oc.get("status")
        try:
            idx_i = int(idx)
        except (TypeError, ValueError):
            continue
        if idx_i < 0:
            continue
        out[idx_i] = str(status or "")
    return out


def prerequisite_verdict(
    step_text: str,
    item_index: int,
    *,
    deps: Optional[Dict[int, Any]],
    step_indices: Sequence[int],
    step_outcomes: Iterable[Any],
    plan_steps: Optional[Sequence[str]] = None,
    gate_implicit: bool = False,
    superseded: Optional[Iterable[int]] = None,
    identity_intact: bool = True,
) -> GateVerdict:
    """Decide whether `step_text` may execute given what its prerequisites did.

    `deps` is the planner's plan-number-keyed map (explicit AND sequential
    edges). The step's OWN tag, read from its text, is authoritative for the
    explicit class — the text survives retries and queue mutation where a
    positional lookup would not. Without a tag the sequential edges for the
    step's plan number apply (soft unless `gate_implicit`).

    `superseded`: item indices whose step was replaced by recovery
    sub-steps (split / re-decompose). Their blocked row records the
    replacement, not a failed prerequisite; they read as UNKNOWN. When
    `identity_intact` is False (see `plan_identity_intact`) no edge is
    hard — the verdict still reports, the loop only logs.
    """
    verdict = GateVerdict()
    by_item = plan_numbers(step_indices)
    try:
        plan_no = by_item.get(int(item_index), 0)
    except (TypeError, ValueError):
        plan_no = 0
    verdict.plan_no = plan_no

    tagged = explicit_deps(step_text)
    if tagged is not None:
        edges = set(tagged)
        verdict.explicit = True
    elif plan_no and deps:
        edges = set(deps.get(plan_no, set()) or set())
        verdict.explicit = False
    else:
        return verdict  # nothing to check

    n_plan = len(step_indices or ())
    status_of = latest_status_by_item(step_outcomes)
    superseded_set = set()
    for item in superseded or ():
        try:
            superseded_set.add(int(item))
        except (TypeError, ValueError):
            continue
    for k in sorted(edges):
        # Self / forward / out-of-plan references are planner noise, not edges.
        if k < 1 or k > n_plan or (plan_no and k >= plan_no):
            continue
        item_k = step_indices[k - 1]
        try:
            item_k = int(item_k)
        except (TypeError, ValueError):
            item_k = -1
        status = status_of.get(item_k) if item_k >= 0 else None
        if item_k in superseded_set:
            status = None
        if status is None:
            verdict.unknown.append(k)
            continue
        if status in UNMET_STATUSES:
            text_k = ""
            if plan_steps and 0 <= k - 1 < len(plan_steps):
                text_k = str(plan_steps[k - 1])[:120]
            verdict.unmet.append((k, status, text_k))

    if verdict.unmet:
        verdict.ready = False
        verdict.hard = identity_intact and (bool(verdict.explicit) or bool(gate_implicit))
    return verdict


def gate_implicit_enabled() -> bool:
    """Config read: harden the sequential-default edges (default off)."""
    try:
        from config import get_bool as _cfg_get_bool
        return _cfg_get_bool("execution.gate_implicit_prerequisites", False)
    except Exception:
        return False


def gate_result_text(verdict: GateVerdict) -> str:
    """The blocked row's result text: what was not run and why."""
    return f"not executed — {verdict.reason}"
