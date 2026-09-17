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

Plan identity (durable plan-node ids, 2026-09-16 chunk 4): a plan node IS
its NEXT.md item. The ORIGINAL plan's numbering — what `[after:N]` names —
is bound to items exactly once, when the loop mirrors the plan to NEXT.md
(`plan_items[k-1]` = item of plan step k; `LoopContext.plan_items`), and
the binding is persisted verbatim in every checkpoint and restored on
resume. With a binding, `prerequisite_verdict` resolves a tag's number to
an ITEM and reads that item's latest outcome — carried-in rows included —
so a resumed suffix keeps its declared edges even though its steps are
re-numbered from 1, and `remap_suffix_deps` re-keys the DAG lane's edges
from original numbers to suffix positions before scheduling. The binding
is only made when the shaped plan has one entry per parsed step and no
item repeats (`plan_identity_intact`); without one the gate resolves
POSITIONALLY through `step_indices` as before, degrades every edge to
SOFT on a resume / reshaped plan / duplicate item, and the loop warns —
refusing work on a mis-numbered edge is the worse error.

Stdlib-only, like terrain.py / world_facts.py: imports nothing from the
loop so the loop can import it freely. planner.py imports the grammar
from here so the two cannot drift.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

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


def _item_int(value: Any) -> Optional[int]:
    """Integer item IDENTITY or None — never a guess: bools, None,
    non-integral numbers (10.9 is not item 10) and garbage never resolve."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def bound_plan_numbers(plan_items: Sequence[int]) -> Dict[int, int]:
    """Map NEXT.md item → ORIGINAL plan number under a durable binding.

    `plan_items[k-1]` is the item plan step k bound to. Unmirrored (-1)
    entries and a repeated item resolve to nothing: a number that names
    two items is no identity.
    """
    out: Dict[int, int] = {}
    dup: set = set()
    for pos, item in enumerate(plan_items or (), 1):
        item_i = _item_int(item)
        if item_i is None or item_i < 0:
            continue
        if item_i in out or item_i in dup:
            dup.add(item_i)
            out.pop(item_i, None)
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
    plan_items: Optional[Sequence[int]] = None,
) -> GateVerdict:
    """Decide whether `step_text` may execute given what its prerequisites did.

    `plan_items` (durable binding, see module doc): when given, a plan
    number k resolves to item `plan_items[k-1]` — on a fresh run that is
    `step_indices` itself; on a resume it is the ORIGINAL plan's binding
    carried by the checkpoint, so the suffix's re-numbering is irrelevant
    and carried-in rows answer for steps that ran before the crash. The
    implicit edge under a binding is the original plan's sequential
    default (step k waits on k-1), not `deps` (which a resume parsed over
    the suffix). Without one, numbers resolve positionally through
    `step_indices` (pre-chunk-4 rule).

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
    bound = plan_items is not None and len(plan_items) > 0
    if bound:
        by_item = bound_plan_numbers(plan_items)
        resolve: Sequence[int] = list(plan_items)
    else:
        by_item = plan_numbers(step_indices)
        resolve = list(step_indices or ())
    item_i = _item_int(item_index)
    plan_no = by_item.get(item_i, 0) if item_i is not None else 0
    verdict.plan_no = plan_no

    tagged = explicit_deps(step_text)
    if tagged is not None:
        edges = set(tagged)
        verdict.explicit = True
    elif plan_no and bound:
        edges = {plan_no - 1} if plan_no > 1 else set()
        verdict.explicit = False
    elif plan_no and deps:
        edges = set(deps.get(plan_no, set()) or set())
        verdict.explicit = False
    else:
        return verdict  # nothing to check

    n_plan = len(resolve)
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
        item_k = _item_int(resolve[k - 1])
        if item_k is None:
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


def remap_suffix_deps(
    tagged_steps: Sequence[str],
    step_items: Sequence[int],
    plan_items: Sequence[int],
    carried_outcomes: Iterable[Any],
    *,
    gate_implicit: bool = False,
) -> Tuple[Dict[int, set], Dict[int, set], Dict[int, str]]:
    """Re-key a resumed suffix's edges from ORIGINAL plan numbers to
    suffix positions (the shape `planner.parse_dependencies` returns for a
    fresh plan), so the DAG lane schedules the suffix by its real edges
    instead of by tags that self-depend after re-numbering.

    `tagged_steps[j-1]` is suffix step j (tags intact), `step_items[j-1]`
    its item, `plan_items` the carried binding, `carried_outcomes` the
    rows restored from the checkpoint (latest status per item wins).

    Returns (deps, declared, pre_gated):
      deps      suffix position → suffix positions it waits on;
      declared  the subset of those edges that came from an [after:] tag
                (the DAG gate enforces exactly these, plus every edge when
                `gate_implicit`);
      pre_gated suffix position → blocked-row reason, for a step whose
                ENFORCED prerequisite is a carried row that ended
                blocked/skipped and is not in the suffix — the caller
                records it blocked without running it. (A blocked row never
                finishes a position, so in practice this is a skipped one
                or a legacy file's row.)

    Per suffix step j with original number o (0 = unbound: a sub-step or
    interrupt addition): a tag names original numbers; untagged, the edge
    is the original plan's sequential default {o-1}, or for an unbound step
    the previous SUFFIX step (queue order, as parse_dependencies would).
    A named number outside 1..len(plan_items), at or after o, or naming a
    suffix position at or after j is planner noise and is dropped. A
    prerequisite in the suffix becomes an edge to its position; one that
    finished before the crash is satisfied (dropped); one with no carried
    row is unknown (dropped — never gates); one carried blocked/skipped is
    pre-gated when enforced, soft (dropped) otherwise.
    """
    n = len(tagged_steps)
    items = [_item_int(x) for x in step_items]
    by_item = bound_plan_numbers(plan_items)
    pos_of_item: Dict[int, int] = {}
    for j, it in enumerate(items, 1):
        if it is not None and it >= 0 and it not in pos_of_item:
            pos_of_item[it] = j
    status_of = latest_status_by_item(carried_outcomes)
    n_plan = len(plan_items)
    deps: Dict[int, set] = {}
    declared: Dict[int, set] = {}
    pre_gated: Dict[int, str] = {}
    for j in range(1, n + 1):
        it = items[j - 1] if j - 1 < len(items) else None
        o = by_item.get(it, 0) if (it is not None and it >= 0) else 0
        tagged = explicit_deps(tagged_steps[j - 1])
        edges: set = set()
        if tagged is not None:
            named = set(tagged)
            explicit = True
        elif o:
            named = {o - 1} if o > 1 else set()
            explicit = False
        else:
            deps[j] = {j - 1} if j > 1 else set()
            continue
        unmet: List[str] = []
        for k in sorted(named):
            if k < 1 or k > n_plan or (o and k >= o):
                continue
            item_k = _item_int(plan_items[k - 1])
            if item_k is None or item_k < 0:
                continue
            pos_k = pos_of_item.get(item_k)
            if pos_k is not None:
                if pos_k < j:
                    edges.add(pos_k)
                continue
            status = status_of.get(item_k)
            if status in UNMET_STATUSES:
                if explicit or gate_implicit:
                    unmet.append(f"step {k} ended {status}")
                else:
                    log.info("resume remap (soft): suffix step %d runs despite "
                             "original step %d ended %s", j, k, status)
        deps[j] = edges
        if explicit and edges:
            declared[j] = set(edges)
        if unmet:
            kind = "declared" if explicit else "sequential"
            pre_gated[j] = (f"not executed — {kind} prerequisite not met: "
                            + "; ".join(unmet) + " (before this resume)")
    return deps, declared, pre_gated


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
