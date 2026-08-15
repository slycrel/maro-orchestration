"""
backchain.py — §9.9 backward-chaining as a recon generator
(COMPOUND_THINKING_DESIGN §3, §9.9).

Goal regression: "for the goal to hold at the end, what must be true just
before it?" chained back a few links. The forward planner already answers
"what next?"; this is the backward half of bidirectional search, and per
the design doc it is a RECON flavor — its output is map edits (landmarks,
preconditions, unknowns), never deliverable progress.

The chain is consumed two ways, both immediately (no store — §12 nudge 4):

1. A precondition that is cheaply VERIFIABLE and not established by any
   named plan step becomes a `[recon: ...]` probe step prepended to the
   plan — riding the §9.2 recon machinery wholesale (tag grammar,
   step_flavor, map-edit verification, map lens glyphs). Capped at 2,
   mirroring draw_cuts' probe cap.
2. The full chain is recorded to build/backchain.jsonl in the run dir
   (append — restart/escalation loops re-plan in the same run dir, and a
   later chain must not erase an earlier loop's record) and a
   BACKCHAIN_DRAWN event fires — the frontier-convergence evidence the
   doc wants ("forward reaches it AND backward regresses to it" = the
   possibility estimate), adjudicable offline, rendered by the map lens.

Recorded step numbers refer to the FINAL plan (post-injection): when
probes are prepended, established links' step refs are shifted by the
probe count before recording, so the record, the milestone indices, the
loop log, and the map all share one numbering (r1 review, both lenses).

Naming: "backchain", not "regression" — EvalRegression/
detect_eval_regressions already own that word in the eval subsystem.

Non-fatal everywhere: any failure returns None and the plan proceeds
untouched, matching draw_cuts' fail-open posture.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("maro.backchain")

# Same cap as draw_cuts probes (planner.py) — the two generators share the
# "at most 2 cheap information buys before committing" budget philosophy.
MAX_PROBE_STEPS = 2

# Chain length asked of the model; more links than this rarely survive
# honest classification and just pad the prompt.
_MAX_LINKS = 5

_LINK_CLASSES = ("established", "verifiable", "unknown")

_BACKCHAIN_SYSTEM = """\
You are the backward half of a bidirectional planner. A goal and a
forward-drawn plan follow. Regress from the GOAL: for the goal to hold at
the end, what must be true immediately before? And before that? Chain
back up to {max_links} links, nearest-to-goal first.

Classify every link honestly:
- "established" — a specific step in the forward plan makes it true.
  Name that step's 1-based number.
- "verifiable" — nobody has confirmed it, but ONE cheap action (one
  command, one file read, one lookup) would. Give that action as
  "probe". Do NOT invent probes for things only the full work can
  establish.
- "unknown" — not established and no single cheap probe exists.

Respond ONLY with this JSON (no prose, no markdown):
{{
  "links": [
    {{"condition": "<what must be true>",
      "class": "established" | "verifiable" | "unknown",
      "step": <1-based int or null>,
      "probe": "<the one cheap action, only when class=verifiable, else null>"}}
  ]
}}"""


@dataclass
class BackchainLink:
    condition: str
    link_class: str          # established | verifiable | unknown
    step: Optional[int] = None    # 1-based forward-plan step, established only
    probe: str = ""               # the cheap action, verifiable only


@dataclass
class Backchain:
    links: List[BackchainLink] = field(default_factory=list)

    @property
    def probe_links(self) -> List[BackchainLink]:
        return [l for l in self.links
                if l.link_class == "verifiable" and l.probe.strip()]

    def to_record(self, injected: int = 0, step_offset: int = 0) -> dict:
        # "injected" marks which verifiable links actually became plan
        # steps — derived from the caller's REAL injection count, not by
        # re-deriving probe_steps' cap rule here (r1 review: without the
        # flag, capped-out links render identically to acted-on ones; and
        # a positional re-derivation would be a guard duplicating its
        # detector). probe_steps takes probe links in order, so the first
        # `injected` probe links are the acted-on ones.
        #
        # "step_offset" shifts established refs to the FINAL plan
        # numbering (prepended probes move every original step down).
        # Arithmetic lives HERE, at serialization, so the in-memory links
        # always hold the numbering draw_backchain validated against —
        # r2 review: the first cut mutated .step in place, making
        # correctness hang on an unenforced must-run-before-record
        # ordering.
        injected_ids = {id(l) for l in self.probe_links[:max(0, injected)]}
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "links": [
                {"condition": l.condition, "class": l.link_class,
                 "step": (l.step + step_offset
                          if l.step is not None else None),
                 "probe": l.probe or None,
                 **({"injected": id(l) in injected_ids}
                    if l.link_class == "verifiable" else {})}
                for l in self.links
            ],
        }


def draw_backchain(goal: str, steps: List[str], adapter) -> Optional[Backchain]:
    """One goal-regression call. None on any failure (fail-open, like
    draw_cuts) — the forward plan proceeds untouched."""
    if adapter is None or not steps:
        return None
    try:
        from llm import LLMMessage
        from llm_parse import extract_json, safe_str, content_or_empty

        steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
        resp = adapter.complete(
            [
                LLMMessage("system", _BACKCHAIN_SYSTEM.format(max_links=_MAX_LINKS)),
                LLMMessage("user", f"Goal: {goal}\n\nForward plan:\n{steps_text}"),
            ],
            max_tokens=700,
            temperature=0.2,
            timeout=45,
            no_tools=True,
            purpose="backchain",
        )
        raw = content_or_empty(resp)
        data = extract_json(raw, dict, log_tag="backchain")
        if not data:
            return None
        links: List[BackchainLink] = []
        for row in (data.get("links") or [])[:_MAX_LINKS]:
            if not isinstance(row, dict):
                continue
            cond = safe_str(row.get("condition", ""), max_len=300)
            klass = safe_str(row.get("class", "")).lower()
            if not cond or klass not in _LINK_CLASSES:
                continue
            step_no: Optional[int] = None
            if klass == "established":
                try:
                    step_no = int(row.get("step"))
                    if not (1 <= step_no <= len(steps)):
                        # A step reference outside the plan is a fabricated
                        # establishment — downgrade honestly.
                        step_no = None
                        klass = "unknown"
                except (TypeError, ValueError):
                    step_no = None
                    klass = "unknown"
            probe = safe_str(row.get("probe", "") or "", max_len=300) \
                if klass == "verifiable" else ""
            if klass == "verifiable" and not probe.strip():
                klass = "unknown"  # a verifiable claim needs its action
            links.append(BackchainLink(condition=cond, link_class=klass,
                                       step=step_no, probe=probe))
        if not links:
            return None
        return Backchain(links=links)
    except Exception as exc:
        log.debug("backchain: draw failed (fail-open): %s", exc)
        return None


def probe_steps(chain: Backchain) -> List[str]:
    """Render the chain's verifiable links as [recon:] probe steps —
    capped at MAX_PROBE_STEPS, nearest-to-goal first (the model was asked
    for that order, and the nearest unmet precondition is the highest-VOI
    probe)."""
    out: List[str] = []
    for link in chain.probe_links[:MAX_PROBE_STEPS]:
        cond = " ".join(link.condition.split())
        probe = " ".join(link.probe.split())
        # Brackets corrupt the tag grammar — and not only inside the tag:
        # parse_dependencies/_AFTER_RE search the WHOLE step string, so a
        # model-authored "[after:2]" in the probe body would forge a
        # dependency edge. Scrub both fields (r1 review, three lenses:
        # the original scrub guarded cond only).
        probe = probe.replace("[", "(").replace("]", ")")
        voi = f"verifies precondition — {cond}".replace("[", "(").replace("]", ")")
        out.append(f"{probe} [recon: {voi}]")
    return out


def record_backchain(chain: Backchain, injected: int) -> None:
    """Persist the chain beside the run (build/backchain.jsonl, append)
    + event. Best-effort — never raises.

    Append, not overwrite: restart/escalation loops re-plan in the same
    run dir (real runs carry multiple loop_ids), and each re-plan draws
    its own chain — a later chain must not erase an earlier loop's
    record (r1 review, two lenses independently).

    Recorded step refs are shifted to post-injection numbering
    (step_offset == injected: each prepended probe moves every original
    step down one)."""
    rec = chain.to_record(injected=injected, step_offset=injected)
    rec["probes_injected"] = injected
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd is not None:
            build = Path(rd) / "build"
            build.mkdir(parents=True, exist_ok=True)
            with (build / "backchain.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("backchain: record failed (non-blocking): %s", exc)
    try:
        from captains_log import log_event, BACKCHAIN_DRAWN
        counts = {k: sum(1 for l in chain.links if l.link_class == k)
                  for k in _LINK_CLASSES}
        log_event(
            BACKCHAIN_DRAWN,
            subject="backchain",
            summary=(f"Goal regression: {len(chain.links)} link(s) — "
                     f"{counts['established']} established, "
                     f"{counts['verifiable']} verifiable, "
                     f"{counts['unknown']} unknown; "
                     f"{injected} probe step(s) injected."),
            context=rec,
        )
    except Exception:
        pass


def apply_backchain(goal: str, steps: List[str], adapter) -> List[str]:
    """The full seam: draw the chain, prepend probe steps for unmet
    verifiable preconditions, record everything. Returns the (possibly
    unchanged) step list. Never raises.

    Skips itself when the plan is a cuts boundary plan (probes + boundary
    marker — already an information-buying plan, and draw_cuts owns that
    lane) or trivially small.
    """
    try:
        try:
            from config import get as _cfg_get
            if not bool(_cfg_get("planner.backchain", False)):
                return steps
        except Exception:
            return steps  # no config machinery → fail closed, no spend
        if len(steps) < 2:
            return steps
        try:
            from planner import BOUNDARY_TAG, goal_step_ceiling
            if any(BOUNDARY_TAG in s for s in steps):
                return steps
            # An operator-stated step ceiling ("in at most 3 steps") binds
            # the WHOLE plan; decompose enforced it before this splice, so
            # injecting past it would override explicit operator intent —
            # skip entirely. (Cuts probes need no such reconciliation:
            # _cuts_plan always emits its boundary step, so the skip above
            # structurally excludes cuts plans from this lane.)
            if goal_step_ceiling(goal) is not None:
                return steps
        except Exception as _excl_exc:
            # Can't prove the plan ISN'T a boundary/ceiling plan — skip
            # rather than fire on the excluded lane (fail closed, same
            # direction as the config gate; r1 review, two lenses). Logged
            # because a broken import here would otherwise silently kill
            # the feature while looking like a legitimate skip (r2, both
            # lenses).
            log.debug("backchain: exclusion checks unavailable — "
                      "skipping (fail closed): %s", _excl_exc)
            return steps
        chain = draw_backchain(goal, steps, adapter)
        if chain is None:
            return steps
        probes = probe_steps(chain)
        record_backchain(chain, injected=len(probes))
        if probes:
            log.info("backchain: %d precondition probe(s) prepended (%d link(s))",
                     len(probes), len(chain.links))
            return probes + list(steps)
        return steps
    except Exception as exc:
        log.debug("backchain: apply failed (fail-open): %s", exc)
        return steps
