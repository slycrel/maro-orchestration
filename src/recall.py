"""recall() — the unified memory read seam (goal-brain sequencing, step 3).

One question, one function: "what do I already know that's relevant right now?"
Behind the signature the substrates compose (run metadata, outcomes, tiered
lessons, standing rules, decisions, knowledge nodes); callers never talk to a
substrate directly. Design: docs/RECALL_DESIGN.md.

Slices (same seam, different depth):
- "dispatch" — identity + history only. No LLM calls, pure local file reads,
  cheap enough for every task dequeue. This is the answer to the 2026-06-10
  pressure-test findings 1+3: the same goal ran ~25x in 35 minutes on
  2026-05-17 because nothing at the requeue boundary asked "have we seen this
  before, and how did it go?"
- "loop" — dispatch plus the eight memory substrates agent_loop injects at
  loop start (lessons, standing rules, decisions, graveyard, failure notes,
  recent learning activity, playbook, knowledge nodes). This is
  `_build_loop_context`'s memory half, relocated here 2026-06-11;
  `as_loop_block()` reassembles it in the historical injection order.
- "navigator" — the loop composition; goal-brain injection + correspondence
  walk are still future work (navigator_shadow builds its own inputs today).

This module writes nothing except its own instrumentation events
(RECALL_PERFORMED). Lifecycle stays in knowledge_web — with one inherited
exception: the graveyard substrate calls search_graveyard(resurrect=True),
which un-decays matched lessons. That side effect predates the seam
(agent_loop behavior, kept identical); it belongs to lesson lifecycle, not
to recall.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("recall")

# Newest-first cap on run-dir metadata reads per recall() call. Keeps dispatch
# O(recent activity), not O(lifetime run count) — 478 dirs and growing.
_METADATA_SCAN_CAP = 200

# Ancestry walk depth limit (a chain longer than this is itself a runaway
# signal; the walk is for identity, not archaeology).
_CHAIN_DEPTH_CAP = 5

_NEAR_MATCH_THRESHOLD = 0.9

# Captain's-log event types worth surfacing to the planner at loop start
# (the K3 "read bridge"): skill/evolver/rule changes, not per-run noise.
_LOOP_ACTIONABLE_EVENTS = (
    "SKILL_PROMOTED", "SKILL_DEMOTED", "SKILL_CIRCUIT_OPEN",
    "SKILL_REWRITE", "EVOLVER_APPLIED", "DIAGNOSIS",
    "HYPOTHESIS_PROMOTED", "STANDING_RULE_CONTRADICTED",
    "RULE_GRADUATED",
)


def recent_learning_activity(
    *,
    event_types=_LOOP_ACTIONABLE_EVENTS,
    scan_limit: int = 30,
    max_items: int = 5,
    header: str = "## Recent Learning System Activity",
) -> str:
    """The captain's-log read bridge: recent learning-system actions as one
    injectable block ("skill X was just demoted — account for it"). Shared by
    the loop slice and the evolver's analysis prompt; each caller keeps its
    own event-type set. Empty string when nothing actionable or on any error.
    """
    try:
        from captains_log import load_log
        wanted = set(event_types)
        actionable = [
            e for e in load_log(limit=scan_limit)
            if e.get("event_type") in wanted
        ]
        if not actionable:
            return ""
        lines = [
            f"- [{e.get('event_type', '?')}] {e.get('summary', '')[:100]}"
            for e in actionable[-max_items:]
        ]
        return header + "\n" + "\n".join(lines)
    except Exception:
        return ""


@dataclass
class PriorAttempt:
    """A recent run whose goal matches the incoming one."""
    goal: str
    handle_id: str
    status: str          # done | stuck | error | unknown (never finalized)
    when: str            # started_at, ISO-8601
    match: str           # "exact" | "near"
    # Judged goal verdict from run metadata (SF-2): True/False when a verdict
    # exists, None = unjudged — done ≠ achieved.
    goal_achieved: Optional[bool] = None


@dataclass
class ThreadIdentity:
    """Where this goal came from.

    Resolved from run-metadata origin (handle_id chain) when the caller has
    one; otherwise from the project's ancestry.json via ancestry.py — the same
    source loop_init's prompt injection reads, so the two lineage strings in
    the loop prompt can't disagree (BACKLOG: ancestry double-injection).
    """
    parent_goal: str
    parent_handle_id: str
    chain: List[str]     # immediate parent first; handle_ids (origin walk) or
                         # project slugs (ancestry.json fallback, source="ancestry")
    source: str          # task_store | agent_loop | director | direct | ancestry | ...


@dataclass
class RecallResult:
    thread: Optional[ThreadIdentity]
    prior_attempts: List[PriorAttempt]
    lessons: str = ""
    standing_rules: str = ""
    decisions: str = ""
    knowledge: str = ""
    graveyard: str = ""
    failure_notes: str = ""
    learning_activity: str = ""
    playbook: str = ""
    # Decision-prior briefs for prior attempts at THIS goal (run_curation
    # miner #3): what each tried, why it ended, its lessons, resume pointer.
    # Populated by recall() from the matched runs' run_card.json so a retry
    # arrives warm; empty when no prior attempt has a curated card (the common
    # case, and the reason a fresh goal costs nothing here).
    prior_decisions: str = ""
    sources: Dict[str, Any] = field(default_factory=dict)

    def dispatch_signals(self, *, window_minutes: float = 60.0) -> Dict[str, Any]:
        """Repeat-pressure signals for the dispatch guard.

        repeat_count counts attempts inside the window; all_failing is True
        only when every one of them failed. Verdict-preferred (SF-2): a
        judged goal_achieved=False attempt counts as failing even when its
        status is "done" (done ≠ achieved); a judged True attempt never
        does; unjudged attempts fall back to status (a non-failing attempt
        anywhere in the window disarms the guard — the goal CAN succeed,
        repeats may be legitimate).
        """
        def _failing(a: PriorAttempt) -> bool:
            if a.goal_achieved is False:
                return True
            if a.goal_achieved is True:
                return False
            # Unjudged: absence means "not judged", not "failed" — fall back
            # to process status.
            return a.status != "done"

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        in_window: List[PriorAttempt] = []
        for a in self.prior_attempts:
            try:
                when = datetime.fromisoformat(a.when)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if when >= cutoff:
                in_window.append(a)
        return {
            "repeat_count": len(in_window),
            "all_failing": bool(in_window) and all(
                _failing(a) for a in in_window
            ),
            "window_minutes": window_minutes,
        }

    def as_context_block(self, *, max_chars: int = 1200) -> str:
        """One injectable string for ancestry context. Empty when nothing known."""
        parts: List[str] = []
        if self.thread and self.thread.parent_goal:
            parts.append(
                f"This goal descends from: {self.thread.parent_goal!r} "
                f"(handle {self.thread.parent_handle_id or '?'}, "
                f"via {self.thread.source})."
            )
        if self.prior_attempts:
            by_status: Dict[str, int] = {}
            for a in self.prior_attempts:
                by_status[a.status] = by_status.get(a.status, 0) + 1
            breakdown = ", ".join(f"{n} {s}" for s, n in sorted(by_status.items()))
            # Surface judged goal verdicts when any exist (done ≠ achieved).
            _n_true = sum(1 for a in self.prior_attempts if a.goal_achieved is True)
            _n_false = sum(1 for a in self.prior_attempts if a.goal_achieved is False)
            if _n_true or _n_false:
                breakdown += (
                    f"; goal verdicts: {_n_true} achieved, "
                    f"{_n_false} NOT achieved, rest unjudged"
                )
            parts.append(
                f"Prior attempts at this goal (recent window): "
                f"{len(self.prior_attempts)} runs — {breakdown}. "
                f"Newest: {self.prior_attempts[0].when} "
                f"({self.prior_attempts[0].status}). "
                f"Do not repeat an approach that already failed; if every "
                f"prior attempt failed the same way, change the approach or "
                f"surface the blocker instead of retrying."
            )
        # The detail behind that summary: what each prior attempt actually
        # tried, why it ended, its lessons (run_curation miner #3). This is the
        # "old task context available" Jeremy asked for on a retry.
        if self.prior_decisions:
            parts.append(self.prior_decisions)
        for block in (self.lessons, self.standing_rules, self.decisions, self.knowledge):
            if block:
                parts.append(block)
        if not parts:
            return ""
        text = "== Recall (what the system already knows) ==\n" + "\n\n".join(parts)
        return text[:max_chars]

    def as_loop_block(self) -> str:
        """The loop-start memory context, assembled in `_build_loop_context`'s
        historical order: standing rules lead (top tier, unconditional), then
        ranked lessons, decisions, resurrected graveyard, failure patterns,
        learning-system activity, playbook, knowledge nodes. Unlike
        as_context_block, no banner and no truncation — each substrate already
        caps itself, and the loop prompt budget is the planner's concern."""
        ctx = self.lessons
        if self.standing_rules:
            ctx = self.standing_rules + ("\n\n" + ctx if ctx else "")
        for block in (self.decisions, self.graveyard, self.failure_notes,
                      self.learning_activity, self.playbook, self.knowledge):
            if block:
                ctx = (ctx + "\n\n" + block) if ctx else block
        # Prior-attempt decision briefs lead the loop's memory context — a
        # re-attempt must see "approach X already failed here" before it plans.
        if self.prior_decisions:
            ctx = self.prior_decisions + ("\n\n" + ctx if ctx else "")
        return ctx


def _read_run_metadata(rd) -> Optional[dict]:
    try:
        return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resolve_thread(origin: Optional[dict]) -> Optional[ThreadIdentity]:
    """Walk origin ancestry through run metadata, immediate parent first."""
    if not origin:
        return None
    parent_handle = str(origin.get("parent_handle_id") or "")
    parent_goal = str(origin.get("parent_goal") or "")
    source = str(origin.get("source") or "direct")
    if not parent_handle and not parent_goal:
        return None

    from runs import run_dir
    chain: List[str] = []
    cursor = parent_handle
    while cursor and len(chain) < _CHAIN_DEPTH_CAP:
        chain.append(cursor)
        meta = _read_run_metadata(run_dir(cursor))
        if not meta:
            break
        cursor = str((meta.get("origin") or {}).get("parent_handle_id") or "")
        if cursor in chain:  # cycle guard
            break
    return ThreadIdentity(
        parent_goal=parent_goal,
        parent_handle_id=parent_handle,
        chain=chain,
        source=source,
    )


def _thread_from_project_ancestry(project: str) -> Optional[ThreadIdentity]:
    """Lineage from the project's ancestry.json (ancestry.py).

    The unification half of the BACKLOG ancestry-double-injection item: when
    run-metadata origin gives recall nothing, consult the same chain
    loop_init's `build_ancestry_prompt` injects instead of staying silent —
    one source of truth for both lineage strings in the loop prompt.
    """
    if not project:
        return None
    from orch_items import project_dir
    from ancestry import get_project_ancestry
    pa = get_project_ancestry(project_dir(project))
    if not pa or not pa.ancestry:
        return None
    nodes = pa.ancestry  # top-level mission first, immediate parent last
    return ThreadIdentity(
        parent_goal=nodes[-1].title,
        parent_handle_id="",
        chain=[n.id for n in reversed(nodes)],  # immediate parent first
        source="ancestry",
    )


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _strip_for_match(text: str) -> str:
    """Best-effort magic-prefix strip so goal-similarity matching isn't
    polluted by prefixes like `persona:builder:` or `garrytan:`.

    Run-dir metadata's `prompt` field is deliberately the RAW input (handle.py
    persists it pre-strip for input-visibility reasons), so a prior run tried
    as `persona:builder: deploy widget` and a retry of plain `deploy widget`
    would otherwise diverge enough on word-overlap to miss the 0.9 near-match
    threshold — silently defeating decision-prior retrieval for exactly the
    retry case it exists to help (adversarial-review finding, 2026-07-13).
    Stripping both sides here, at the matching boundary, fixes it regardless
    of whether a given caller's `goal` argument happens to already be
    stripped. Falls back to the raw text if handle's prefix parser is
    unavailable — matching only degrades, it never breaks.
    """
    try:
        from handle import _apply_prefixes
        return _apply_prefixes(text).message
    except Exception:
        return text


def _find_prior_attempts(goal: str, *, window_hours: float) -> List[PriorAttempt]:
    """Scan recent run dirs (mtime-ordered, capped) for goal matches."""
    from runs import runs_root
    from memory_ledger import _text_similarity

    root = runs_root()
    if not root.is_dir():
        return []
    try:
        dirs = sorted(
            (d for d in root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    goal_stripped = _strip_for_match(goal)
    goal_norm = _normalize(goal_stripped)
    attempts: List[PriorAttempt] = []
    for rd in dirs[:_METADATA_SCAN_CAP]:
        meta = _read_run_metadata(rd)
        if not meta:
            continue
        started = meta.get("started_at") or ""
        try:
            when = datetime.fromisoformat(started)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if when < cutoff:
            continue
        prompt = str(meta.get("prompt") or "")
        if not prompt:
            continue
        prompt_stripped = _strip_for_match(prompt)
        if _normalize(prompt_stripped) == goal_norm:
            match = "exact"
        elif _text_similarity(prompt_stripped, goal_stripped) >= _NEAR_MATCH_THRESHOLD:
            match = "near"
        else:
            continue
        _ga = meta.get("goal_achieved")
        attempts.append(PriorAttempt(
            goal=prompt,
            handle_id=str(meta.get("handle_id") or rd.name.split("-", 1)[0]),
            status=str(meta.get("status") or "unknown"),
            when=started,
            match=match,
            goal_achieved=_ga if isinstance(_ga, bool) else None,
        ))
    attempts.sort(key=lambda a: a.when, reverse=True)
    return attempts


def recall(
    goal: str,
    *,
    slice: str = "loop",
    origin: Optional[dict] = None,
    project: str = "",
    window_hours: float = 24.0,
) -> RecallResult:
    """The seam. Read-only; every failure degrades to "knows nothing"."""
    t0 = time.monotonic()
    sources: Dict[str, Any] = {"slice": slice}

    try:
        thread = _resolve_thread(origin)
        if thread is None:
            thread = _thread_from_project_ancestry(project)
    except Exception as exc:
        log.debug("recall: thread resolution failed: %s", exc)
        thread = None
    sources["thread_chain_len"] = len(thread.chain) if thread else 0
    sources["thread_source"] = thread.source if thread else ""

    try:
        prior = _find_prior_attempts(goal, window_hours=window_hours)
    except Exception as exc:
        log.debug("recall: prior-attempt scan failed: %s", exc)
        prior = []
    sources["prior_attempts"] = len(prior)

    result = RecallResult(thread=thread, prior_attempts=prior, sources=sources)

    # Decision-prior briefs (run_curation miner #3): for each matched prior
    # attempt, pull its curated run_card decision_prior (what it tried, why it
    # ended, lessons, resume pointer) so a retry/rephrase arrives WARM, not
    # cold. Reuses the exact+near match already computed above — no second
    # similarity pass. The current run self-excludes (its card is written at
    # goal-END, so it has none at read time); exclude_handle_id is defensive.
    # Cheap: at most k local run_card.json reads, only when priors exist.
    try:
        from run_curation import format_prior_decisions
        try:
            from runs import current_handle_id
            _exclude = current_handle_id() or ""
        except Exception:
            _exclude = ""
        result.prior_decisions = format_prior_decisions(
            prior, goal=goal, exclude_handle_id=_exclude, k=3)
        if result.prior_decisions:
            sources["prior_decisions"] = True
    except Exception as exc:
        log.debug("recall: prior-decision briefs failed: %s", exc)

    if slice in ("loop", "navigator"):
        # The eight loop-start memory substrates, relocated here from
        # agent_loop._build_loop_context (2026-06-11). Each degrades
        # independently — a broken substrate never takes the seam down.
        # (navigator slice additionally wants goal-brain + correspondence
        # walk — still future work.)

        # 1. Tiered lessons — ranked retrieval; legacy injector as fallback.
        lessons_cited: List[str] = []
        try:
            from memory import load_lessons, _MAX_LESSON_INJECT_CHARS
            _lessons = load_lessons(task_type="agenda", query=goal, limit=3)
            if not _lessons:
                _lessons = load_lessons(task_type="general", query=goal, limit=3)
            if _lessons:
                _lines = ["## Lessons from Prior Runs (apply these)"]
                for _l in _lessons:
                    _icon = "✓" if _l.outcome == "done" else "✗"
                    _lines.append(f"- {_icon} {_l.lesson}")
                    lessons_cited.append(str(_l.lesson)[:120])
                _text = "\n".join(_lines)
                if len(_text) > _MAX_LESSON_INJECT_CHARS:
                    _text = _text[:_MAX_LESSON_INJECT_CHARS].rsplit("\n", 1)[0]
                result.lessons = _text
        except Exception:
            try:
                from memory import inject_lessons_for_task
                result.lessons = inject_lessons_for_task("agenda", goal, max_lessons=3)
            except Exception:
                pass

        # 2. Standing rules (top tier — apply unconditionally), project-scoped.
        try:
            from memory import inject_standing_rules
            result.standing_rules = inject_standing_rules(domain=project)
        except Exception:
            pass

        # 3. Decision journal.
        try:
            from memory import inject_decisions
            result.decisions = inject_decisions(goal, domain=project)
        except Exception:
            pass

        # 4. Graveyard resurrection — NOTE: resurrect=True mutates lesson
        # lifecycle (un-decays matches); inherited agent_loop behavior.
        try:
            from memory import search_graveyard
            _gy = search_graveyard(goal, resurrect=True)
            if _gy:
                result.graveyard = (
                    "Previously-learned (resurrected from decay):\n"
                    + "\n".join(f"- {l.lesson}" for l in _gy[:3])
                )
                sources["graveyard_count"] = len(_gy)
        except Exception:
            pass

        # 5. Failure patterns from diagnoses (same-project diagnoses lead).
        try:
            from introspect import find_relevant_failure_notes
            _notes = find_relevant_failure_notes(goal, limit=3, project=project or "")
            if _notes:
                result.failure_notes = (
                    "Known failure patterns for similar goals:\n"
                    + "\n".join(f"- {n}" for n in _notes)
                )
        except Exception:
            pass

        # 6. Captain's-log read bridge (recent learning-system activity).
        result.learning_activity = recent_learning_activity()

        # 7. Director's playbook.
        try:
            from playbook import inject_playbook
            result.playbook = inject_playbook(max_chars=800)
        except Exception:
            pass

        # 8. Knowledge nodes (K2 imports).
        try:
            from knowledge_web import inject_knowledge_for_goal
            result.knowledge = inject_knowledge_for_goal(goal, max_chars=600)
        except Exception:
            pass

        sources["knowledge_blocks"] = sum(
            1 for b in (result.lessons, result.standing_rules,
                        result.decisions, result.graveyard,
                        result.failure_notes, result.learning_activity,
                        result.playbook, result.knowledge) if b
        )
        # The lesson-cited edge stamp (RECALL_DESIGN.md vocabulary): which
        # lessons were injected for this goal, recorded in RECALL_PERFORMED.
        # The log is the crystallization substrate — no new store.
        if lessons_cited:
            sources["lessons_cited"] = lessons_cited

    sources["elapsed_ms"] = int((time.monotonic() - t0) * 1000)

    # Instrument every call from day one (2026-05-18 decision: static now,
    # logged tuples are the crystallization substrate later).
    try:
        from captains_log import log_event, RECALL_PERFORMED
        log_event(
            RECALL_PERFORMED,
            subject="recall",
            summary=f"recall slice={slice}: {sources['prior_attempts']} prior attempts, "
                    f"thread chain {sources['thread_chain_len']}.",
            context={"goal_preview": goal[:200], **sources},
        )
    except Exception:
        pass

    return result
