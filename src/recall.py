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
from pathlib import Path
from typing import Any, Dict, List, Optional

from ancestry import Origin
from context_budget import clip

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
    "RULE_GRADUATED", "KNOWLEDGE_NODE_PROMOTED",
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
    match: str           # "exact" | "near" | "project"
    # Judged goal verdict from run metadata (SF-2): True/False when a verdict
    # exists, None = unjudged — done ≠ achieved.
    goal_achieved: Optional[bool] = None
    # Typed stop verdict from run metadata (§13b): why the run ended.
    # "external-interrupt" = process-level ending, not goal evidence.
    stop_verdict: str = ""


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
    # Bounded filename inventory from the persistent project directory.  The
    # planner gets paths to inspect/reuse, never file contents (which may be
    # large or untrusted).
    project_artifacts: str = ""
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
            # to process status. External interrupts (kill switch, stranded
            # owner, busy refusal, awaiting input) carry no goal evidence and
            # must not arm the repeat-guard (§13b): the goal wasn't disproven,
            # the process was cut down around it. The event lives in TWO
            # channels (decree 2026-07-27): the verdict field when no map
            # observation preceded the cut, else the interrupt STATUS beside
            # a supported verdict — honor both, or an operator-stopped run
            # that had already stamped out-of-budget arms the guard.
            if a.stop_verdict == "external-interrupt":
                return False
            from stop_verdicts import INTERRUPT_STATUSES
            if a.status in INTERRUPT_STATUSES:
                return False
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

    def as_context_block(self, *, max_chars: int = 4000) -> str:
        """One injectable string for ancestry context. Empty when nothing known.

        The default was 1200 with a bare tail slice until 2026-08-13
        (adversarial review of the STORE widening): the prior-decisions
        block alone can legitimately run past that, so dispatch and the
        navigator received briefs severed mid-instruction with no marker
        while the loop lane got the full text. 4000 fits the widened
        briefs plus the memory blocks in the common case; the bound is
        honest now (clip marker), though the number itself is a judgment
        call, not a measured distribution — noted in the BACKLOG audit
        entry rather than dressed up as data.
        """
        parts: List[str] = []
        if self.thread and self.thread.parent_goal:
            parts.append(
                f"This goal descends from: {self.thread.parent_goal!r} "
                f"(handle {self.thread.parent_handle_id or '?'}, "
                f"via {self.thread.source})."
            )
        if self.project_artifacts:
            parts.append(self.project_artifacts)
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
            from stop_verdicts import INTERRUPT_STATUSES as _ISTAT
            _n_int = sum(
                1 for a in self.prior_attempts
                if a.stop_verdict == "external-interrupt" or a.status in _ISTAT
            )
            if _n_int:
                breakdown += (
                    f"; {_n_int} externally interrupted (not goal evidence)"
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
        if len(text) <= max_chars:
            return text
        if max_chars <= 128:
            # Degenerate budget: no room for an announced cut — the bound
            # wins (fixpoint review 2026-08-14: max_chars - 64 went
            # negative and produced a nonsense marker).
            return text[:max(0, max_chars)]
        # Reserve room for clip's marker so the return honors max_chars.
        return clip(text, max_chars - 64)

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
        if self.project_artifacts:
            ctx = self.project_artifacts + ("\n\n" + ctx if ctx else "")
        return ctx


def _read_run_metadata(rd) -> Optional[dict]:
    try:
        return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resolve_thread(origin: Optional[Origin]) -> Optional[ThreadIdentity]:
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
    stripped. Uses the neutral prefixes.py module (shared with handle.py, not
    a reach into its private internals — adversarial-review R1 batch-1
    finding #2) and falls back to the raw text on any error — matching only
    degrades, it never breaks.
    """
    try:
        from prefixes import strip_prefixes
        return strip_prefixes(text)
    except Exception:
        return text


def find_prior_attempts(
    goal: str,
    *,
    window_hours: float,
    project: str = "",
    exclude_handle_id: str = "",
) -> List[PriorAttempt]:
    """Scan recent run dirs (mtime-ordered, capped) for goal matches.

    Public (renamed from `_find_prior_attempts`, adversarial-review R1
    batch-1 finding #2): run_curation.prior_decision_context() calls this as
    a legitimate cross-module read, not a reach into recall's private
    internals — the old private name made that reach look worse than it is.
    """
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
        handle_id = str(meta.get("handle_id") or rd.name.split("-", 1)[0])
        if exclude_handle_id and handle_id == exclude_handle_id:
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
        elif project and str(meta.get("project") or "") == project:
            # A caller-selected persistent project is a stronger, cheaper
            # family key than guessing semantic similarity from a rephrase.
            match = "project"
        else:
            continue
        _ga = meta.get("goal_achieved")
        _sv = str(meta.get("stop_verdict") or "")
        _status = str(meta.get("status") or "unknown")
        if not _sv:
            # Status-derived fallback (mirrors run_curation.classify_outcome)
            # for runs that predate break-site stamping.
            from stop_verdicts import INTERRUPT_STATUSES as _ISTAT
            if _status in _ISTAT:
                _sv = "external-interrupt"
        attempts.append(PriorAttempt(
            goal=prompt,
            handle_id=handle_id,
            status=_status,
            when=started,
            match=match,
            goal_achieved=_ga if isinstance(_ga, bool) else None,
            stop_verdict=_sv,
        ))
    attempts.sort(key=lambda a: a.when, reverse=True)
    return attempts


def _project_artifact_context(
    project: str,
    *,
    max_files: int = 12,
    max_chars: int = 600,
    max_scan_entries: int = 200,
) -> str:
    """Return a bounded path inventory for a persistent project.

    Runtime bookkeeping is omitted.  File contents are deliberately not read:
    recall tells the planner what prior work exists, and normal tool access is
    then responsible for inspecting only the files relevant to the new goal.
    """
    if not project:
        return ""
    try:
        from itertools import islice
        from orch_items import projects_root

        root = projects_root().resolve()
        pd = (root / project).resolve()
        if pd == root or root not in pd.parents or not pd.is_dir():
            return ""

        ignored = {
            "NEXT.md", "DONE.md", "ancestry.json", ".loop.lock",
            ".admission.lock", "project.json",
        }
        candidates = []
        scanned = 0

        def _consider(p) -> None:
            nonlocal scanned
            scanned += 1
            if (p.is_file() and p.name not in ignored
                    and not p.name.startswith(".")):
                try:
                    candidates.append((p.stat().st_mtime, p.relative_to(pd).as_posix()))
                except OSError:
                    pass

        # Project-root ledgers/reports plus the conventional artifact tree are
        # the durable products. Avoid recursively walking scratch/build dirs.
        # The scan itself, not merely the rendered output, is hard-bounded.
        root_entries = sorted(
            islice(pd.iterdir(), max_scan_entries),
            key=lambda item: item.name.lower(),
        )
        for p in root_entries:
            if scanned >= max_scan_entries:
                break
            _consider(p)
        artifacts = pd / "artifacts"
        pending = [artifacts] if artifacts.is_dir() else []
        while pending and scanned < max_scan_entries:
            directory = pending.pop()
            try:
                entries = sorted(
                    islice(directory.iterdir(), max_scan_entries - scanned),
                    key=lambda item: item.name.lower(),
                    reverse=True,
                )
            except OSError:
                continue
            for p in entries:
                if scanned >= max_scan_entries:
                    break
                if p.is_dir() and not p.is_symlink() and not p.name.startswith("."):
                    scanned += 1
                    pending.append(p)
                else:
                    _consider(p)
        # Fresh pivots are more useful than alphabetically early stale ones;
        # path is a stable tie-breaker for equal/coarse mtimes.
        candidates.sort(key=lambda item: (-item[0], item[1].lower()))
        paths = [path for _, path in candidates[:max_files]]
        if not paths:
            return ""
        # Filenames are user-controlled on Unix.  Keep them inert in the
        # prompt: strip control characters/Markdown delimiters and cap each.
        import re
        safe_project = re.sub(r"[\x00-\x1f\x7f`]", "_", project)[:100]
        safe_paths = [
            re.sub(r"[\x00-\x1f\x7f`]", "_", path)[:100]
            for path in paths
        ]
        prefix = (
            "## Existing project artifacts — inspect/reuse before creating replacements\n"
            f"Project `{safe_project}` already contains: "
        )
        suffix = ". These are path hints, not verified contents."
        shown = []
        for path in safe_paths:
            candidate = ", ".join([*shown, f"`{path}`"])
            if len(prefix) + len(candidate) + len(suffix) > max_chars:
                break
            shown.append(f"`{path}`")
        if not shown:
            return ""
        return prefix + ", ".join(shown) + suffix
    except Exception:
        return ""


def recall(
    goal: str,
    *,
    slice: str = "loop",
    origin: Optional[Origin] = None,
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
        from runs import current_handle_id
        _exclude = current_handle_id() or ""
    except Exception:
        _exclude = ""

    try:
        prior = find_prior_attempts(
            goal,
            window_hours=window_hours,
            project=project,
            exclude_handle_id=_exclude,
        )
    except Exception as exc:
        log.debug("recall: prior-attempt scan failed: %s", exc)
        prior = []
    sources["prior_attempts"] = len(prior)

    result = RecallResult(thread=thread, prior_attempts=prior, sources=sources)
    result.project_artifacts = _project_artifact_context(project)
    if result.project_artifacts:
        sources["project_artifacts"] = True

    # Decision-prior briefs (run_curation miner #3): for each matched prior
    # attempt, pull its curated run_card decision_prior (what it tried, why it
    # ended, lessons, resume pointer) so a retry/rephrase arrives WARM, not
    # cold. Reuses the exact+near match already computed above — no second
    # similarity pass. The current run self-excludes (its card is written at
    # goal-END, so it has none at read time); exclude_handle_id is defensive.
    # Cheap: at most k local run_card.json reads, only when priors exist.
    # format_prior_decisions lives in the neutral decision_prior.py, not
    # run_curation.py (adversarial-review R1 batch-1 finding #2 — this used
    # to reach into the write-side curation module for read-side formatting).
    try:
        from decision_prior import format_prior_decisions
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
        # Chunk 6 rewire: this comment always declared tiered-first, but the
        # read was flat-store-only — so tiered-only writers (M3 recovery
        # lessons, verify-learn, novelty-scored records) never reached the
        # main-loop prompt. Now the tiered store (ranked, decay-scored) leads
        # and the legacy flat store tops up with lessons never dual-written.
        lessons_cited: List[str] = []
        lesson_ids_cited: List[str] = []
        rules_cited: List[str] = []
        _applied_pairs: List[tuple] = []  # (lesson_id, tier) — rendered only
        _cam_candidates: Dict[str, list] = {}  # source -> [(lesson, score|None)]
        _port_adj: List[dict] = []  # §14a portability re-weights, for the frame
        _cam_degraded = ""  # set when ranked selection fell back to legacy
        try:
            from memory import load_lessons, _MAX_LESSON_INJECT_CHARS
            from knowledge_web import query_lessons_scored
            from age_stamp import age_stamps_enabled, age_suffix
            from portability import apply_portability, load_cache
            # Chunk A camera frames: fetch a WIDER scored window (10) than
            # the selection window (3) so the frame records the road not
            # taken. _cam_candidates keeps RAW ranker scores (frame data
            # contract F4); §14a slice 2 selects over portability-adjusted
            # scores — foreign-context lessons with >=3 verdicted foreign
            # citations are re-weighted by 2*beta_mean (earned globality,
            # decision e2b83703). No cache / no qualifying evidence →
            # apply_portability is an identity and selection stays
            # byte-identical to the pre-slice-2 first-3 windows. The
            # adjustments ride the frame extra so the readout can see both
            # rankings.
            _scored_agenda = query_lessons_scored(
                goal, n=10, task_type="agenda")
            _cam_candidates["agenda"] = _scored_agenda
            # One cache snapshot for both apply calls — a concurrent
            # finalize refresh landing between them must not show the
            # agenda and untyped passes different evidence (r1 skeptic).
            _port_cache = load_cache()
            _sel_agenda, _port_adj = apply_portability(
                _scored_agenda, goal, project, cache=_port_cache)
            _lessons = [_l for _l, _ in _sel_agenda[:3]]
            if len(_lessons) < 3:
                # Untyped/other-type tiered writers (evolver, verify-learn,
                # prereq) TOP UP — an existing agenda match must not mask
                # them (chunk-6 review: they are tiered-only, so the flat
                # top-up below can never recover them). Dedup by lesson_id
                # — the untyped query is a superset of the agenda one.
                _have_ids = {getattr(_l, "lesson_id", "") for _l in _lessons}
                _scored_untyped = query_lessons_scored(goal, n=10)
                _cam_candidates["untyped"] = _scored_untyped
                _sel_untyped, _adj_untyped = apply_portability(
                    _scored_untyped, goal, project, cache=_port_cache)
                _port_adj = _port_adj + [
                    a for a in _adj_untyped
                    if a["lesson_id"] not in {p["lesson_id"]
                                              for p in _port_adj}]
                for _t, _ in _sel_untyped[:3]:
                    if len(_lessons) >= 3:
                        break
                    if _t.lesson_id in _have_ids:
                        continue
                    _lessons.append(_t)
                    _have_ids.add(_t.lesson_id)
            if len(_lessons) < 3:
                # Chain BOTH flat sources (chunk-6 review: `agenda or
                # general` meant an agenda result of already-selected twins
                # masked general flat-only lessons while slots stayed open).
                _flat = ((load_lessons(task_type="agenda", query=goal, limit=3) or [])
                         + (load_lessons(task_type="general", query=goal, limit=3) or []))
                _cam_candidates["flat"] = [(_f, None) for _f in _flat]
                _seen = {str(_l.lesson).strip().lower() for _l in _lessons}
                for _f in _flat:
                    if len(_lessons) >= 3:
                        break
                    # Extraction dual-writes both stores — skip the flat twin
                    # of a tiered lesson already selected.
                    _norm = str(_f.lesson).strip().lower()
                    if _norm in _seen:
                        continue
                    _lessons.append(_f)
                    _seen.add(_norm)
            if _lessons:
                # Time-blindness hook (a): age-stamp injected lessons from
                # their stored timestamp (memory.age_stamps; flag off or
                # timestamp absent renders byte-identically).
                _stamp_ages = age_stamps_enabled()
                _age_stamped_any = False
                _lines = ["## Lessons from Prior Runs (weigh by their receipts)"]
                _budget = len(_lines[0])
                for _l in _lessons:
                    # Verdict-preferred (SF-2): a lesson from a run judged
                    # goal-not-achieved is a failure lesson even though the
                    # run's process status was "done" (same rule as
                    # memory.inject_lessons_for_task).
                    _icon = ("✗" if getattr(_l, "goal_achieved", None) is False
                             else ("✓" if _l.outcome == "done" else "✗"))
                    # TieredLesson rows may predate recorded_at; last_reinforced
                    # is the decay anchor and always present — use it as the
                    # age anchor of record when recorded_at is absent.
                    _suffix = (age_suffix(getattr(_l, "recorded_at", "")
                                          or getattr(_l, "last_reinforced", "") or "")
                               if _stamp_ages else "")
                    # Certainty receipt (Jeremy decree 2026-07-29): entries
                    # cite the evidence behind them or read as what they are —
                    # a single observation. Fields ride the TieredLesson row;
                    # flat-store rows have none and genuinely ARE one
                    # observation, so the honest fallback needs no plumbing.
                    _rein = int(getattr(_l, "times_reinforced", 0) or 0)
                    _sess = int(getattr(_l, "sessions_validated", 0) or 0)
                    _appl = int(getattr(_l, "times_applied", 0) or 0)
                    _rparts = ([f"reinforced {_rein}x"] if _rein else []) \
                        + ([f"{_sess} sessions"] if _sess else []) \
                        + ([f"applied {_appl}x"] if _appl else [])
                    _receipt = (" (" + ", ".join(_rparts) + ")") if _rparts \
                        else " (observed once)"
                    _line = f"- {_icon} {_l.lesson}{_receipt}{_suffix}"
                    # Budget-aware selection (chunk-6 review): a lesson is
                    # cited ONLY if its line is actually rendered. The old
                    # truncate-after-the-fact could drop trailing lines while
                    # their IDs stayed cited — and the contradiction join
                    # would then contest lessons the run never saw.
                    if _budget + 1 + len(_line) > _MAX_LESSON_INJECT_CHARS:
                        break
                    _lines.append(_line)
                    _budget += 1 + len(_line)
                    if _suffix:
                        _age_stamped_any = True
                    lessons_cited.append(str(_l.lesson)[:120])
                    _lid = getattr(_l, "lesson_id", "") or ""
                    if _lid:
                        lesson_ids_cited.append(_lid)
                        _tier = getattr(_l, "tier", "") or ""
                        if _tier:
                            _applied_pairs.append((_lid, _tier))
                if len(_lines) > 1:
                    result.lessons = "\n".join(_lines)
                    if _age_stamped_any:
                        # Rides into RECALL_PERFORMED via **sources below.
                        sources["age_stamped"] = True
                    # Receipt write-back (2026-07-29): this render is THE
                    # live main-loop lesson surface, and it bypassed the
                    # times_applied writer — every "applied Nx" receipt the
                    # lines above promise rendered as "(observed once)"
                    # forever (0/338 live rows had times_applied > 0).
                    # Same law as citations: a lesson is "applied" ONLY if
                    # its line was actually rendered. Flat-store rows carry
                    # no tier and genuinely ARE one observation — skipped
                    # by construction above.
                    if _applied_pairs:
                        try:
                            from knowledge_web import _increment_times_applied
                            _increment_times_applied(
                                _applied_pairs, task_type="agenda")
                        except Exception:
                            pass
        except Exception:
            # Legacy fallback renders lessons WITHOUT citations or scored
            # candidates — the camera frame below must say so, or it records
            # a false "nothing chosen" against a run that did render lessons
            # (adversarial-review 2026-07-31 F5).
            _cam_degraded = "legacy_fallback"
            try:
                from memory import inject_lessons_for_task
                result.lessons = inject_lessons_for_task("agenda", goal, max_lessons=3)
            except Exception:
                pass

        # 2. Standing rules (top tier — apply unconditionally), project-scoped.
        try:
            from memory import standing_rules_with_ids
            result.standing_rules, rules_cited = standing_rules_with_ids(
                domain=project)
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
        # Chunk-4 citation join: durable IDs (not 120-char previews) so a
        # later failure verdict can name the exact rules/lessons the run was
        # injected with. Stamped in the log AND written run-keyed below —
        # stamp_outcome_verdict reads the run dir, not the log.
        if lesson_ids_cited:
            sources["lesson_ids_cited"] = lesson_ids_cited
        if rules_cited:
            sources["rules_cited"] = rules_cited
        # Written on EVERY recall that has a run-dir, including when nothing
        # was cited. The `if lesson_ids_cited or rules_cited` guard this
        # replaces made absence ambiguous — "this run was injected with no
        # lessons" and "the citation writer never ran" produced the identical
        # empty state. That is fatal for the cold/warm attribution rail: the
        # cold arm of a paired run cites nothing BY DESIGN, and that zero is
        # the measurement, not a gap. Present-with-empty-lists now means
        # "recall ran, cited nothing"; absent means recall never got here.
        try:
            import runs as _runs
            _rd = _runs.current_run_dir()
            if _rd is not None:
                _src = Path(_rd) / "source"
                _src.mkdir(parents=True, exist_ok=True)
                # Overwrite-per-recall is correct: a restarted run's
                # verdict should join against the citations of the recall
                # that actually fed it (the verdict stamp reads before
                # any later run's recall overwrites).
                (_src / "recall_citations.json").write_text(json.dumps({
                    "rule_ids": rules_cited,
                    "lesson_ids": lesson_ids_cited,
                    "goal_preview": goal[:200],
                    "project": project or "",
                }, indent=2))
        except Exception as exc:
            log.debug("recall: citation file write failed: %s", exc)

        # Camera frame (Chunk A): log the lesson-selection fork FORWARD —
        # the candidate sets the selector saw (raw scores + shares), what
        # actually rendered (durable IDs), and the substrate sizes around
        # it. Append-per-recall is correct (each recall = one frame); the
        # readout joins frames to run_card verdicts by run dir.
        try:
            from camera_log import log_fork_frame
            from knowledge_web import ranker_name
            try:
                from memory import _MAX_LESSON_INJECT_CHARS as _cam_budget
            except Exception:
                _cam_budget = None
            _cands_payload = {
                _src_name: [{
                    "lesson_id": getattr(_c, "lesson_id", "") or "",
                    "text": str(getattr(_c, "lesson", _c))[:160],
                    # Raw, unrounded — the frame's data contract (F4).
                    "score": (_s if isinstance(_s, (int, float)) else None),
                } for _c, _s in _pairs]
                for _src_name, _pairs in _cam_candidates.items()
            }
            _cam_extra = {
                "ranker": ranker_name(),
                "render_budget_chars": _cam_budget,
                "selection_window": 3,
                "fetch_window": 10,
            }
            if _port_adj:
                # §14a slice 2: candidates above keep raw ranker scores;
                # this records every fetched candidate whose score was
                # re-weighted — NOT only those that changed the chosen
                # set (r1 skeptic: rank effects need the join — diff
                # chosen against the raw candidate order to see them).
                _cam_extra["portability_adjusted"] = _port_adj
            if _cam_degraded:
                # Ranked selection died mid-flight; candidates show what it
                # saw before dying, chosen is empty because the legacy path
                # records no citations. The frame is honest, not blind.
                _cam_extra["degraded"] = _cam_degraded
            if log_fork_frame(
                "recall.lesson_selection",
                query=goal,
                axes={
                    "slice": slice,
                    "project": project or "",
                    "thread_source": sources.get("thread_source", ""),
                    "substrate_chars": {
                        "lessons": len(result.lessons or ""),
                        "standing_rules": len(result.standing_rules or ""),
                        "decisions": len(result.decisions or ""),
                        "graveyard": len(result.graveyard or ""),
                        "failure_notes": len(result.failure_notes or ""),
                        "learning_activity": len(result.learning_activity or ""),
                        "playbook": len(result.playbook or ""),
                        "knowledge": len(result.knowledge or ""),
                    },
                },
                candidates=_cands_payload,
                chosen={
                    "lesson_ids": lesson_ids_cited,
                    "previews": lessons_cited,
                    "rule_ids": rules_cited,
                },
                extra=_cam_extra,
            ):
                sources["camera_frame"] = True
        except Exception as exc:
            log.debug("recall: camera frame failed: %s", exc)

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
