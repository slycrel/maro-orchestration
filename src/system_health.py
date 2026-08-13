"""System self-health — liveness monitoring for dynamic processes.

Jeremy decree (2026-07-29): "we need a way to ensure the system itself is
active and working, especially if we're going to allow it to modify itself."
Not ops dashboards — the system verifying its own dynamic machinery is
alive. The defect class this exists to catch (found four times in one week
of live-fire probing): writers that fire, consumers that don't, joins that
silently miss — subsystems wired end-to-end with green tests that never
execute in production. Suites structurally cannot see this (they patch the
joins); only probes over live workspace data can.

Design:
- DECLARED_PROCESSES is the liveness registry: each dynamic process
  declares what "alive" looks like and ships a cheap deterministic probe
  (no LLM calls, never raises) that reads live workspace data and answers
  OK / SILENT / UNKNOWN with evidence.
- Probes ride goal-run finalization (loop_finalize, beside
  run_skill_maintenance) — the same no-cron/no-scheduler cadence decision
  made for skill maintenance. No daemon.
- State lives in a store, transitions in the log: the snapshot
  (memory/system_health.json) is the load-bearing record — and the seed of
  the maro-level systemic-metadata home, which has never had one — while
  the captain's log gets only transition events (SUBSYSTEM_SILENT /
  SUBSYSTEM_RECOVERED, user-surfaced), honoring the log's narrated-
  changelog contract.
- SILENT is a finding, not an error: "wired but not observably executing."
  Report-only; nothing here changes runtime behavior.

House rule (HOUSE_STYLE.md): shipping a new dynamic process (a writer
whose consumer runs later, a queue, a cross-store join, a maintenance
cadence) includes declaring it here with a probe.

CLI:
    python3 -m system_health            # render current snapshot
    python3 -m system_health --probe    # run a probe cycle now, then render
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

OK = "OK"
SILENT = "SILENT"
UNKNOWN = "UNKNOWN"

# Cross-cycle observations kept per process (ring buffer in the snapshot).
HISTORY_KEEP = 8
# Consecutive observations a conditional expectation must hold before a
# cross-cycle probe may call SILENT — one bad cycle is noise, a streak is a
# finding.
STREAK_FOR_SILENT = 3
# Grace before an undrained contradiction candidate counts as starvation
# (the queue drains at run finalization, so an idle box legitimately holds
# pending candidates — age, not existence, is the signal).
CANDIDATE_STARVATION_HOURS = 48
# Grace before a frozen variant-creation count under a non-empty frontier
# counts as stopped-firing (evolver mints variants at its own cadence —
# several goal runs can finalize between mints; days, not cycles, is the
# right unit for "the generator broke").
VARIANT_STALE_DAYS = 7


# ---------------------------------------------------------------------------
# Snapshot store
# ---------------------------------------------------------------------------

def _snapshot_path() -> Path:
    from config import memory_dir
    return memory_dir() / "system_health.json"


def load_snapshot() -> Dict[str, Any]:
    path = _snapshot_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_snapshot(snapshot: Dict[str, Any]) -> None:
    from file_lock import atomic_write
    path = _snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(snapshot, indent=1, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Shared probe helpers
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    from config import memory_dir
    return memory_dir()


def _recent_outcomes(limit: int = 50) -> List[dict]:
    """Newest-last slice of outcomes.jsonl rows that carry a loop_id."""
    path = _memory_dir() / "outcomes.jsonl"
    if not path.exists():
        return []
    rows: List[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("loop_id"):
                rows.append(d)
    except Exception:
        return []
    return rows[-limit:]


def _run_source(loop_id: str) -> Optional[Path]:
    import runs
    rd = runs.resolve_run_dir(loop_id)
    return Path(rd) / "source" if rd else None


def _history_of(prior: Dict[str, Any]) -> List[dict]:
    hist = prior.get("history")
    return [h for h in hist if isinstance(h, dict)] if isinstance(hist, list) else []


# ---------------------------------------------------------------------------
# Probes — each returns (status, evidence, observation)
# ---------------------------------------------------------------------------

def _probe_run_ref_index(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """Recent loop_ids must resolve to run dirs through the run-ref index.

    The join every verdict-seam consumer depends on. Was dead for every
    live run until 2026-07-29 (index read singular loop_id; the loops-
    ledger era stamps plural keys) — the original wired-but-silent find.
    """
    recent = _recent_outcomes(limit=50)[-5:]
    if not recent:
        return UNKNOWN, "no outcomes rows with loop_id yet", {}
    import runs
    misses = [r["loop_id"] for r in recent
              if runs.resolve_run_dir(str(r["loop_id"])) is None]
    obs = {"checked": len(recent), "misses": len(misses)}
    if misses:
        return SILENT, (
            f"{len(misses)}/{len(recent)} recent loop_ids do not resolve "
            f"to a run dir (first miss: {misses[0][:8]}) — verdict-seam "
            "consumers are no-oping again"), obs
    return OK, f"{len(recent)}/{len(recent)} recent loop_ids resolve", obs


def _probe_skill_attribution(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """FULL-trust verdicted runs with a skills manifest must carry the
    attribution marker (the honest injected_runs counters' write receipt)."""
    from memory_ledger import verdict_trust, VERDICT_TRUST_FULL
    qualifying = 0
    missing: List[str] = []
    for row in reversed(_recent_outcomes(limit=50)):
        if qualifying >= 5:
            break
        if not isinstance(row.get("goal_achieved"), bool):
            continue
        try:
            if verdict_trust(row) != VERDICT_TRUST_FULL:
                continue
        except Exception:
            continue
        src = _run_source(str(row["loop_id"]))
        if src is None:
            continue  # the index probe owns unresolvable loop_ids
        manifest = src / "skills_manifest.jsonl"
        if not manifest.exists():
            continue  # no skills injected — nothing owed
        qualifying += 1
        if not (src / "skill_attribution.json").exists():
            missing.append(str(row["loop_id"])[:8])
    obs = {"qualifying": qualifying, "missing": len(missing)}
    if missing:
        return SILENT, (
            f"{len(missing)}/{qualifying} recent FULL-trust runs with a "
            f"skills manifest lack the attribution marker "
            f"(e.g. {missing[0]}) — injected counters starving"), obs
    if qualifying == 0:
        return UNKNOWN, "no recent FULL-trust verdicted runs with a skills manifest", obs
    return OK, f"{qualifying}/{qualifying} qualifying runs carry the marker", obs


def _probe_contradiction_lifecycle(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """Pending CONTRADICTION_CANDIDATE events must drain at maintenance
    cadence — old pending candidates mean the adjudicator stopped firing."""
    from captains_log import query_log, CONTRADICTION_CANDIDATE, CONTRADICTION_ADJUDICATED
    candidates = query_log(event_type=CONTRADICTION_CANDIDATE, limit=0)
    if not candidates:
        return UNKNOWN, "no contradiction candidates ever emitted", {}
    adjudicated_ids = {
        e.get("context", {}).get("loop_id") or e.get("subject", "")
        for e in query_log(event_type=CONTRADICTION_ADJUDICATED, limit=0)}
    pending = [
        c for c in candidates
        if (c.get("context", {}).get("loop_id") or c.get("subject", ""))
        not in adjudicated_ids]
    obs = {"candidates": len(candidates), "pending": len(pending)}
    if not pending:
        return OK, f"queue drained ({len(candidates)} candidates, 0 pending)", obs
    oldest = min(c.get("timestamp", "") for c in pending)
    try:
        oldest_dt = datetime.fromisoformat(oldest)
        age_h = (datetime.now(timezone.utc) - oldest_dt).total_seconds() / 3600
    except Exception:
        age_h = 0.0
    obs["oldest_pending_hours"] = round(age_h, 1)
    if age_h > CANDIDATE_STARVATION_HOURS:
        return SILENT, (
            f"{len(pending)} pending candidate(s), oldest {age_h:.0f}h old "
            f"(> {CANDIDATE_STARVATION_HOURS}h) — adjudication is not "
            "draining the queue"), obs
    return OK, (
        f"{len(pending)} pending candidate(s) within the "
        f"{CANDIDATE_STARVATION_HOURS}h drain window"), obs


def _probe_variant_ab(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """A persistently non-empty frontier must keep producing A/B variants
    — the subsystem that sat starved from birth until 2026-07-29.

    Tracks the cumulative SKILL_VARIANT_CREATED event count, not current
    variant skills: lifetime existence of one old variant must not mask a
    generator that broke later (2026-07-30 review), and retirement culling
    variant skills must not re-arm a false alarm. SILENT needs the count
    frozen across the streak AND the last creation older than
    VARIANT_STALE_DAYS (evolver-cadence grace); a never-fired subsystem
    has no last creation, so the streak alone decides it."""
    from skills import load_skills, frontier_skills
    from captains_log import query_log, SKILL_VARIANT_CREATED
    all_skills = load_skills()
    frontier = len(frontier_skills(all_skills))
    events = query_log(event_type=SKILL_VARIANT_CREATED, limit=0)
    created = len(events)
    last_created = max((e.get("timestamp", "") for e in events), default="")
    obs = {"frontier": frontier, "variant_events": created}
    if frontier == 0:
        return OK, "frontier empty — no variants owed", obs
    window = _history_of(prior)[-(STREAK_FOR_SILENT - 1):] + [obs]
    if len(window) < STREAK_FOR_SILENT or any(
            "variant_events" not in h for h in window):
        return UNKNOWN, (
            f"frontier {frontier}, {created} variant(s) ever created — "
            f"watching ({len(window)}/{STREAK_FOR_SILENT} cycles observed)"), obs
    frozen = len({h.get("variant_events") for h in window}) == 1
    frontier_held = all(h.get("frontier", 0) > 0 for h in window)
    stale = True
    if last_created:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last_created)
            stale = age > timedelta(days=VARIANT_STALE_DAYS)
        except Exception:
            stale = True
    if frozen and frontier_held and stale:
        detail = ("0 variants ever created" if not created else
                  f"no new variant since {last_created[:10]}")
        return SILENT, (
            f"frontier non-empty ({frontier} skill(s)) across "
            f"{len(window)} consecutive probe cycles, {detail} — the "
            "frontier→variant path is not firing (check evolver frontier "
            "logs; strategy pre-score PASS skips are one legitimate cause "
            "worth ruling out)"), obs
    if frozen and frontier_held:
        return OK, (
            f"last variant {last_created[:10]} within the "
            f"{VARIANT_STALE_DAYS}d grace window (frontier {frontier})"), obs
    return OK, f"{created} variant(s) created (frontier {frontier})", obs


def _probe_lesson_receipts(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """times_applied must grow while runs cite rendered lessons — receipts
    were bumped on in-memory copies (never persisted) until 2026-07-29."""
    total = 0
    from knowledge_web import MemoryTier
    for tier in (MemoryTier.SHORT, MemoryTier.MEDIUM, MemoryTier.LONG):
        path = _memory_dir() / tier / "lessons.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                total += int(json.loads(line).get("times_applied", 0) or 0)
            except Exception:
                continue
    nodes = _memory_dir() / "knowledge_nodes.jsonl"
    if nodes.exists():
        for line in nodes.read_text(encoding="utf-8").splitlines():
            try:
                total += int(json.loads(line).get("times_applied", 0) or 0)
            except Exception:
                continue
    # Newest run that actually cited lessons — receipts are only owed when
    # NEW citations happen. A single old cited run sitting in the recency
    # window must not turn "nothing newly owed" into a false SILENT
    # (2026-07-30 review), so the streak condition is "citing run CHANGED
    # while the sum stayed frozen", not "any cited run exists".
    last_cited_loop = None
    for row in reversed(_recent_outcomes(limit=20)):
        src = _run_source(str(row["loop_id"]))
        if src is None:
            continue
        cit = src / "recall_citations.json"
        if not cit.exists():
            continue
        try:
            if json.loads(cit.read_text(encoding="utf-8")).get("lesson_ids"):
                last_cited_loop = str(row["loop_id"])
                break
        except Exception:
            continue
    obs = {"receipt_sum": total, "last_cited_loop": last_cited_loop}
    window = _history_of(prior)[-(STREAK_FOR_SILENT - 1):] + [obs]
    if len(window) < STREAK_FOR_SILENT or any(
            "last_cited_loop" not in h for h in window):
        return UNKNOWN, (
            f"receipt sum {total} — watching "
            f"({len(window)}/{STREAK_FOR_SILENT} cycles observed)"), obs
    sums = {h.get("receipt_sum") for h in window}
    cited_loops = [h.get("last_cited_loop") for h in window]
    non_null = [c for c in cited_loops if c]
    # Citations advanced during the window: the citing run changed, or a
    # first citation appeared after a no-citation observation.
    advanced = (len(set(non_null)) >= 2
                or (bool(non_null) and cited_loops[0] is None))
    if advanced and len(sums) == 1:
        return SILENT, (
            f"new runs cited lessons across {len(window)} probe cycles but "
            f"times_applied sum stayed frozen at {total} — receipts are "
            "not reaching disk"), obs
    return OK, f"receipt sum {total} ({'moving' if len(sums) > 1 else 'no new citations owed'})", obs


def _probe_closure_verdicts(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """Done runs must get closure-verdicted — the census (2026-07-29) found
    5/51 loop-era done rows where closure silently never ran."""
    done_rows = [r for r in _recent_outcomes(limit=50)
                 if r.get("status") == "done"
                 and r.get("task_type") == "agenda"]
    if not done_rows:
        return UNKNOWN, "no recent done agenda rows", {}
    grace = datetime.now(timezone.utc) - timedelta(hours=1)
    unverdicted = []
    for r in done_rows:
        if isinstance(r.get("goal_achieved"), bool) or r.get("goal_verdict_source"):
            continue
        try:
            if datetime.fromisoformat(str(r.get("recorded_at", ""))) > grace:
                continue  # closure may still be in flight
        except Exception:
            pass
        unverdicted.append(str(r.get("loop_id", ""))[:8])
    obs = {"done": len(done_rows), "unverdicted": len(unverdicted),
           "unverdicted_ids": unverdicted}
    prior_hist = _history_of(prior)
    # Identity-based growth (2026-07-30 review): count comparison misses a
    # window slide where an old unverdicted run scrolls out of the recency
    # window just as a new one appears — same count, brand-new silent
    # failure. Track the ids; new id = growth. History entries predating
    # id tracking get baseline treatment rather than a false alarm.
    known_ids: set = set()
    ids_tracked = False
    for h in prior_hist:
        if isinstance(h.get("unverdicted_ids"), list):
            ids_tracked = True
            known_ids.update(h["unverdicted_ids"])
    if unverdicted and not ids_tracked:
        # First observation (or first since id tracking shipped): a
        # pre-existing backlog is a baseline, not a new finding — SILENT
        # here would flip to a spurious "recovered" next cycle.
        return OK, (
            f"baseline: {len(unverdicted)}/{len(done_rows)} known "
            f"done-without-closure row(s) (e.g. {unverdicted[0]}) — "
            "watching for new ids"), obs
    new_ids = [i for i in unverdicted if i not in known_ids]
    obs["new_ids"] = new_ids
    # Hold the alarm while growth is recent instead of flipping back to OK
    # the cycle after an accretion is acknowledged — otherwise an ongoing
    # breakage narrates SILENT/RECOVERED ping-pong. RECOVERED here means
    # "no new unverdicted runs for STREAK_FOR_SILENT cycles".
    recent_growth = any(
        h.get("new_ids") for h in prior_hist[-(STREAK_FOR_SILENT - 1):])
    if new_ids:
        return SILENT, (
            f"{len(new_ids)} new done run(s) with no closure verdict in "
            f"the outcomes store (e.g. {new_ids[0]}) — done-without-closure "
            "is accreting"), obs
    if recent_growth:
        return SILENT, (
            f"done-without-closure accreted within the last "
            f"{STREAK_FOR_SILENT} probe cycles — holding until quiet"), obs
    if unverdicted:
        return OK, (
            f"{len(unverdicted)} known done-without-closure row(s), "
            f"no new ids in {STREAK_FOR_SILENT} cycles"), obs
    return OK, f"{len(done_rows)}/{len(done_rows)} recent done runs verdicted", obs


def _probe_container_auth(prior: Dict[str, Any]) -> Tuple[str, str, dict]:
    """A configured container lane must hold a live OAuth session — the
    08-12 outage shape: docker green, auth volume dead, every dispatch step
    failing until a human noticed. The reactive breaker (container_exec)
    trips on the first auth-failed step; this probe surfaces the tripped
    state each cycle so a degraded lane can't fade into background noise.
    Reads the breaker's state file only — no docker, no LLM (probe contract).
    """
    from container_exec import container_mode, auth_breaker_state
    mode = container_mode()
    state, breaker_status = auth_breaker_state()
    obs = {"mode": mode, "breaker_tripped": state is not None,
           "breaker_status": breaker_status}
    if mode == "off":
        return OK, "executor.container=off — container lane not in play", obs
    if breaker_status == "unreadable":
        return SILENT, (
            "container auth breaker marker is UNREADABLE — lane state "
            "unknown (resolve fails open; a dead session re-trips). "
            "Inspect memory/container_auth_breaker.json"), obs
    if state is None:
        # Honest claim only: clear means NO auth failure has been observed
        # since the last trip/clear — it does not prove the session is live
        # (zero-token probe contract; review 2026-08-13).
        return OK, (f"container lane armed (mode {mode}) — no auth failure "
                    "observed (reactive breaker clear)"), obs
    tripped_at = state.get("tripped_at")
    try:
        when = datetime.fromtimestamp(
            float(tripped_at), tz=timezone.utc).isoformat()[:16]
    except (TypeError, ValueError):
        when = "unknown"
    consequence = ("executor steps REFUSE" if mode == "require"
                   else "executor steps degrade to host/fence-only")
    return SILENT, (
        f"container auth breaker tripped since {when} — {consequence}; "
        f"re-seed the maro-claude-auth volume (maro-bootstrap "
        f"container-setup step 2); reason: "
        f"{str(state.get('reason', ''))[:120]}"), obs


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class ProcessDeclaration:
    name: str
    description: str          # what the dynamic process is
    expectation: str          # what "alive" looks like, in prose
    probe: Callable[[Dict[str, Any]], Tuple[str, str, dict]]


DECLARED_PROCESSES: List[ProcessDeclaration] = [
    ProcessDeclaration(
        name="run_ref_index",
        description="loop_id → run-dir join (runs.resolve_run_dir)",
        expectation="recent loop_ids resolve to run dirs",
        probe=_probe_run_ref_index,
    ),
    ProcessDeclaration(
        name="skill_attribution",
        description="verdict-seam injected-skill counters (skill_attribution.json markers)",
        expectation="FULL-trust verdicted runs with a skills manifest carry the marker",
        probe=_probe_skill_attribution,
    ),
    ProcessDeclaration(
        name="contradiction_lifecycle",
        description="candidate → adjudicate → contest/refight pipeline",
        expectation=(
            f"pending candidates drain within {CANDIDATE_STARVATION_HOURS}h"),
        probe=_probe_contradiction_lifecycle,
    ),
    ProcessDeclaration(
        name="variant_ab",
        description="frontier rewrite → A/B challenger variants",
        expectation="a persistently non-empty frontier produces variants",
        probe=_probe_variant_ab,
    ),
    ProcessDeclaration(
        name="lesson_receipts",
        description="times_applied write-back for rendered lessons/nodes",
        expectation="receipt sums grow while runs cite lessons",
        probe=_probe_lesson_receipts,
    ),
    ProcessDeclaration(
        name="closure_verdicts",
        description="closure verdict stamping on done runs",
        expectation="done agenda runs get goal-verdicted",
        probe=_probe_closure_verdicts,
    ),
    ProcessDeclaration(
        name="container_auth",
        description="containerized executor auth session (maro-claude-auth volume)",
        expectation=("no unresolved auth failure on the container lane "
                     "(reactive breaker clear; liveness is not probed)"),
        probe=_probe_container_auth,
    ),
]


# ---------------------------------------------------------------------------
# Probe cycle
# ---------------------------------------------------------------------------

def run_health_probes(*, verbose: bool = False) -> Dict[str, Any]:
    """Run all declared probes, persist the snapshot, narrate transitions.

    Rides loop_finalize beside run_skill_maintenance. Never raises; each
    probe is individually shielded (a broken probe reports UNKNOWN, it
    doesn't take the cycle down). Narration is edge-triggered on what the
    user has last been told (the ``narrated`` field), not on raw
    status-pair equality: SUBSYSTEM_SILENT fires when a process reaches
    SILENT and the user hasn't been told (covers UNKNOWN→SILENT — how the
    streak probes arrive — and first-observation SILENT); SUBSYSTEM_RECOVERED
    fires when a told-silent process reaches OK (covers SILENT→UNKNOWN→OK).
    A held state never repeats into the log.
    """
    summary: Dict[str, Any] = {"ran": 0, "silent": [], "transitions": 0}
    try:
        from config import get as _cfg_get
        if not bool(_cfg_get("health.probes_enabled", True)):
            summary["skipped"] = "health.probes_enabled is off"
            return summary

        # The whole cycle is one read-modify-write of the snapshot; hold
        # its lock throughout so concurrent finalizers serialize instead
        # of both reading narrated=None and double-narrating / last-writer-
        # winning the history (atomic_write alone does not lock). Probes
        # only READ other stores, so no lock-ordering cycle is possible.
        from file_lock import locked_write
        pending_narrations: List[Tuple[ProcessDeclaration, str, str]] = []
        with locked_write(_snapshot_path()):
            snapshot = load_snapshot()
            processes = snapshot.get("processes")
            if not isinstance(processes, dict):
                processes = {}
                snapshot["processes"] = processes

            for decl in DECLARED_PROCESSES:
                prior = processes.get(decl.name)
                prior = prior if isinstance(prior, dict) else {}
                try:
                    status, evidence, obs = decl.probe(prior)
                except Exception as exc:
                    status, evidence, obs = (
                        UNKNOWN, f"probe failed: {str(exc)[:120]}", {})
                summary["ran"] += 1
                if status == SILENT:
                    summary["silent"].append(decl.name)

                prev_status = prior.get("status")
                now = datetime.now(timezone.utc).isoformat()
                entry = dict(prior)  # unknown keys survive (data retention)
                history = _history_of(prior)
                if obs:
                    obs = {**obs, "at": now}
                    history = (history + [obs])[-HISTORY_KEEP:]
                entry.update({
                    "status": status,
                    "evidence": evidence,
                    "description": decl.description,
                    "expectation": decl.expectation,
                    "history": history,
                    "checked_at": now,
                })

                # Edge-trigger on what the user was last told, not on raw
                # status pairs: streak probes arrive at SILENT via UNKNOWN,
                # and a probe that breaks (SILENT→UNKNOWN→OK) must still
                # narrate the recovery.
                narrated = prior.get("narrated")
                went_silent = status == SILENT and narrated != "silent"
                recovered = status == OK and narrated == "silent"
                if went_silent or recovered:
                    entry["last_transition"] = {
                        "from": prev_status, "to": status, "at": now}
                    entry["narrated"] = "silent" if went_silent else "ok"
                    pending_narrations.append((decl, status, evidence))
                processes[decl.name] = entry
                if verbose:
                    print(f"[health] {decl.name}: {status} — {evidence}")

            snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
            snapshot["cycle"] = int(snapshot.get("cycle", 0) or 0) + 1
            _write_snapshot(snapshot)

        # Narrate only after the snapshot recording narrated= persisted:
        # a failed write must not leave the log claiming the user was told
        # while the state machine forgot (it would re-narrate forever).
        # The reverse trade — write succeeds, log append fails, the line
        # is lost — is accepted: the snapshot still shows SILENT.
        summary["transitions"] = len(pending_narrations)
        for decl, status, evidence in pending_narrations:
            _narrate_transition(decl, status, evidence)
    except Exception as exc:
        logger.debug("health probe cycle failed (non-fatal): %s", exc)
        summary["error"] = str(exc)[:200]
    return summary


def _narrate_transition(decl: ProcessDeclaration, status: str, evidence: str) -> None:
    try:
        from captains_log import (
            log_event, loop_id_scope, SUBSYSTEM_SILENT, SUBSYSTEM_RECOVERED)
        # Health transitions are global process state, not evidence produced
        # by the run whose finalization happened to host the probe cycle —
        # shed the ambient loop_id_scope so run reports don't claim them
        # as attributed run events.
        with loop_id_scope(None):
            if status == SILENT:
                log_event(
                    SUBSYSTEM_SILENT,
                    subject=decl.name,
                    summary=(f"{decl.description} went silent: {evidence}"),
                    context={"expectation": decl.expectation, "evidence": evidence},
                )
            else:
                log_event(
                    SUBSYSTEM_RECOVERED,
                    subject=decl.name,
                    summary=(f"{decl.description} recovered: {evidence}"),
                    context={"expectation": decl.expectation, "evidence": evidence},
                )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_snapshot(snapshot: Optional[Dict[str, Any]] = None) -> str:
    snap = snapshot if snapshot is not None else load_snapshot()
    lines = ["# System health — dynamic-process liveness", ""]
    if not snap.get("processes"):
        lines.append("No snapshot yet — probes run at goal-run finalization "
                     "(or: python3 -m system_health --probe).")
        return "\n".join(lines)
    lines.append(f"Updated: {snap.get('updated_at', '?')}  "
                 f"(cycle {snap.get('cycle', '?')})")
    lines.append("")
    order = {SILENT: 0, UNKNOWN: 1, OK: 2}
    for name, p in sorted(
            snap["processes"].items(),
            key=lambda kv: (order.get(kv[1].get("status"), 3), kv[0])):
        status = p.get("status", "?")
        lines.append(f"[{status}] {name} — {p.get('description', '')}")
        lines.append(f"    expectation: {p.get('expectation', '')}")
        lines.append(f"    evidence:    {p.get('evidence', '')}")
        lt = p.get("last_transition")
        if isinstance(lt, dict):
            lines.append(
                f"    last transition: {lt.get('from')} → {lt.get('to')} "
                f"at {lt.get('at')}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="system_health",
        description="Dynamic-process liveness snapshot (report-only)")
    parser.add_argument("--probe", action="store_true",
                        help="Run a probe cycle now before rendering")
    parser.add_argument("--json", action="store_true",
                        help="Output the raw snapshot JSON")
    args = parser.parse_args()
    if args.probe:
        run_health_probes(verbose=True)
        print()
    if args.json:
        print(json.dumps(load_snapshot(), indent=1, sort_keys=True))
    else:
        print(render_snapshot())


if __name__ == "__main__":
    main()
