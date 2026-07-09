"""Blocked-step recovery logic for the agent loop (Tier 3 split of agent_loop.py).

Extracted verbatim from agent_loop.py — the convergence-tracking helpers
(error fingerprinting, sibling failure rate), the missing-input honesty
check, timeout-split generation, the Phase 44+45 diagnosis consult, and the
Phase 62 metacognitive decision algorithm (_handle_blocked_step) plus its
loop-state-mutating caller (_process_blocked_step).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loop_types import LoopContext, StepOutcome, _orch, step_from_decompose
from loop_planning import _is_combined_exec_analyze, _shape_steps, _split_exec_analyze
from step_exec import generate_refinement_hint as _generate_refinement_hint

log = logging.getLogger("maro.loop")


@dataclass
class BlockedStepContext:
    """Bundle of inputs + mutable state passed to `_process_blocked_step`.

    Session 20 adversarial review finding 3.12: `_process_blocked_step` had
    21 parameters — a design smell that resists testing and is rarely called
    correctly. This dataclass packages them all into one object passed by
    reference. Mutable collections (step_retries, failure_chain, etc.) still
    mutate in place because the dataclass field is just a reference; scalar
    in/out values (consecutive_max_timeouts, replan_count, next_step_*) are
    returned via the function's tuple result, so assignments to them inside
    the function don't need to write back to the dataclass.
    """
    # Per-step inputs
    step_text: str
    step_idx: int
    step_result: str
    step_elapsed: int
    outcome: dict
    item_index: int
    iteration: int
    step_adapter: Any
    # Mutable shared state (referenced by both caller and callee)
    step_retries: Dict[str, int]
    step_tier_overrides: Dict[str, str]
    failure_chain: List[str]
    step_outcomes: List[StepOutcome]
    remaining_steps: List[str]
    remaining_indices: List[int]
    manifest_steps: List[str]
    error_fingerprints: Dict[str, List[str]] = field(default_factory=dict)
    # Loop-level scalars (in via init, out via tuple return)
    next_step_injected_context: str = ""
    consecutive_max_timeouts: int = 0
    max_consecutive_timeouts: int = 3
    replan_count: int = 0


def _process_blocked_step(ctx: LoopContext, blk: BlockedStepContext) -> tuple:
    """Phase F11: Process a blocked step — retry, split, redecompose, or terminal.

    Returns (flow: str, step_idx, loop_status, stuck_reason, next_step_injected_context,
             consecutive_max_timeouts, recovery_step_count_delta, replan_count).
    flow is "continue" (retry/split/redecompose), "break" (adapter hung), or "normal" (terminal, fall through).
    Mutates blk.step_retries, blk.step_tier_overrides, blk.failure_chain,
    blk.step_outcomes, blk.remaining_steps/indices, blk.manifest_steps,
    blk.error_fingerprints in place.
    """
    from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER

    # Unpack into local names so the function body below is unchanged.
    # Session 20.5 refactor: the body still uses bare names; this preserves
    # the call-site change without rewriting 300+ lines of internals.
    step_text = blk.step_text
    step_idx = blk.step_idx
    step_result = blk.step_result
    step_elapsed = blk.step_elapsed
    outcome = blk.outcome
    item_index = blk.item_index
    iteration = blk.iteration
    step_adapter = blk.step_adapter
    step_retries = blk.step_retries
    step_tier_overrides = blk.step_tier_overrides
    failure_chain = blk.failure_chain
    step_outcomes = blk.step_outcomes
    remaining_steps = blk.remaining_steps
    remaining_indices = blk.remaining_indices
    manifest_steps = blk.manifest_steps
    next_step_injected_context = blk.next_step_injected_context
    consecutive_max_timeouts = blk.consecutive_max_timeouts
    max_consecutive_timeouts = blk.max_consecutive_timeouts
    replan_count = blk.replan_count
    error_fingerprints = blk.error_fingerprints

    o = _orch()
    _prior_retries = step_retries.get(step_text, 0)
    if error_fingerprints is None:
        error_fingerprints = {}

    # Phase 62: Track error fingerprint for convergence detection
    _fp = _error_fingerprint(outcome)
    _fps = error_fingerprints.setdefault(step_text, [])
    _fps.append(_fp)

    _decision = _handle_blocked_step(
        step_text, outcome, _prior_retries, ctx.adapter,
        error_fingerprints=_fps,
        step_outcomes=step_outcomes,
        replan_count=replan_count,
        loop_id=ctx.loop_id,
    )

    # The heuristic recovery tree's chosen action (used by both the
    # METACOGNITIVE_DECISION log and the navigator shadow below).
    _heuristic_action = "retry" if _decision.retry else (
        "redecompose" if _decision.redecompose else (
            "split" if _decision.split_into else "stuck"
        )
    )

    # Phase 62: Log metacognitive reasoning
    if _decision.metacognitive_reason:
        log.info("metacognitive decision: %s", _decision.metacognitive_reason)
        try:
            from captains_log import log_event
            log_event(
                event_type="METACOGNITIVE_DECISION",
                subject=step_text[:80],
                summary=_decision.metacognitive_reason,
                context={
                    "step_idx": step_idx,
                    "retries": _prior_retries,
                    "fingerprints": _fps[-3:],  # last 3
                    "replan_count": replan_count,
                    "action": _heuristic_action,
                },
            )
        except Exception as _exc:
            log.debug("captain's log emit for recovery decision failed: %s", _exc)

    # Dumb-loop audit priority-1 point: shadow the navigator against this
    # heuristic recovery decision (decide-only, config-gated off by default,
    # never raises, never alters recovery). The navigator judges the same
    # block from the goal-brain + the convergence/sibling signals the
    # heuristic used. Divergence is the cutover evidence. Skipped on dry_run:
    # the shadow builds its own real adapter (not ctx.adapter), so the
    # hermeticity guard belongs at the call site.
    try:
        if not ctx.dry_run:
            import navigator_shadow as _ns
            from agent_loop import _current_run_dir_safe
            _nav_decision = _ns.shadow_blocked_step_live(
                ctx.goal,
                run_dir=_current_run_dir_safe(),
                heuristic_action=_heuristic_action,
                block_reason=outcome.get("stuck_reason", "blocked"),
                signals={
                    "retries": _prior_retries,
                    "replan_count": replan_count,
                    "converging": _is_converging(_fps),
                    "sibling_fail_rate": round(_sibling_failure_rate(step_outcomes), 2)
                    if step_outcomes else 0.0,
                    "block_reason": outcome.get("stuck_reason", "blocked")[:80],
                },
                turn_index=iteration,
            )
            # Blocked-step escalate cutover (2026-07-03): the navigator may
            # override a FORWARD recovery decision with an honest stop —
            # escalate-only, confidence-floored, config-gated off in code.
            # The shadow row above still logs either way (audit trail).
            _act_override = _navigator_act_blocked_step(
                _nav_decision, _decision,
                goal=ctx.goal,
                step_text=step_text,
                step_idx=step_idx,
                loop_id=getattr(ctx, "loop_id", "") or "",
            )
            if _act_override is not None:
                _decision = _act_override
    except Exception as _nav_exc:
        log.debug("blocked-step navigator shadow skipped: %s", _nav_exc)
    _recovery_delta = 0

    if _decision.retry:
        step_retries[step_text] = _prior_retries + 1
        _recovery_delta = 1
        # Tier escalation
        _cur_tier = getattr(step_adapter, "model_key", MODEL_CHEAP)
        if _cur_tier == MODEL_CHEAP:
            step_tier_overrides[step_text] = MODEL_MID
            log.info("step %d retry tier-up: cheap → mid", step_idx)
        elif _cur_tier == MODEL_MID:
            step_tier_overrides[step_text] = MODEL_POWER
            log.info("step %d retry tier-up: mid → power", step_idx)
        _br_reason = outcome.get("stuck_reason", "blocked")
        failure_chain.append(
            f"step {step_idx} blocked ({_br_reason[:60]}); retry {_prior_retries + 1} with hint"
        )
        _retry_reminder = (
            f"RETRY REMINDER — ORIGINAL GOAL: {ctx.goal}\n"
            "Focus only on completing the step above. "
            "Use data already in context. Target <500 tokens."
        )
        _hint_with_reminder = (
            (_decision.hint + "\n\n" + _retry_reminder).strip()
            if _decision.hint
            else _retry_reminder
        )
        next_step_injected_context = (
            (next_step_injected_context + "\n\n" + _hint_with_reminder).strip()
            if next_step_injected_context
            else _hint_with_reminder
        )
        remaining_steps.insert(0, step_text)
        remaining_indices.insert(0, item_index)
        step_idx -= 1
        if ctx.verbose:
            _br = outcome.get("stuck_reason", "blocked")
            print(f"[maro] step {step_idx+1} blocked ({_br[:80]}), retrying with fallback hint", file=sys.stderr, flush=True)
        step_outcomes.append(step_from_decompose(
            step_text, item_index,
            status="blocked", result=step_result, iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
            # 2026-07-08 adversarial review round 2 (Skeptic): these blocked-
            # retry/redecompose/timeout-split outcomes carried the raw
            # `outcome` dict's tokens but dropped call_record/cache_read_tokens/
            # confidence/injected_steps even though outcome has them — the
            # report's "each executed step gets a detail link" promise was
            # silently broken for any step that hit one of these paths.
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            confidence=outcome.get("confidence", ""),
            injected_steps=list(outcome.get("inject_steps", [])),
            call_record=outcome.get("call_record", ""),
        ))
        return ("continue", step_idx, "", None, next_step_injected_context,
                consecutive_max_timeouts, _recovery_delta, replan_count)

    elif _decision.redecompose:
        # Phase 62: Mid-loop re-decomposition — the step (or plan) needs
        # to be broken down differently, not just retried.
        _recovery_delta = 1
        failure_chain.append(
            f"step {step_idx} re-decomposing: {_decision.metacognitive_reason[:80]}"
        )
        try:
            from planner import decompose
            _sub_steps = decompose(
                step_text,
                ctx.adapter,
                max_steps=5,
            )
            if _sub_steps and len(_sub_steps) >= 2:
                _sub_shaped = _shape_steps(list(_sub_steps), label="redecompose")
                for _new_step in reversed(_sub_shaped):
                    remaining_steps.insert(0, _new_step)
                    remaining_indices.insert(0, -1)
                manifest_steps.extend(_sub_shaped)
                replan_count += 1
                log.info("mid-loop re-decompose: step %d → %d sub-steps (replan #%d)",
                         step_idx, len(_sub_shaped), replan_count)
                if ctx.verbose:
                    print(
                        f"[maro] step {step_idx} re-decomposed into {len(_sub_shaped)} sub-steps "
                        f"(replan #{replan_count})",
                        file=sys.stderr, flush=True,
                    )
                step_outcomes.append(step_from_decompose(
                    step_text, item_index,
                    status="blocked", result=step_result, iteration=iteration,
                    tokens_in=outcome.get("tokens_in", 0),
                    tokens_out=outcome.get("tokens_out", 0),
                    elapsed_ms=step_elapsed,
                    # 2026-07-08 adversarial review round 2 (Skeptic): see the
                    # other two step_from_decompose call sites in this file —
                    # same fix, this one was missed by the first pass's
                    # replace_all because its extra indentation (nested one
                    # level deeper) didn't match the other two sites' text.
                    cache_read_tokens=outcome.get("cache_read_tokens", 0),
                    confidence=outcome.get("confidence", ""),
                    injected_steps=list(outcome.get("inject_steps", [])),
                    call_record=outcome.get("call_record", ""),
                ))
                return ("continue", step_idx, "", None, next_step_injected_context,
                        consecutive_max_timeouts, _recovery_delta, replan_count)
        except Exception as exc:
            log.warning("mid-loop re-decompose failed: %s — falling through to stuck", exc)

        # Re-decompose failed — fall through to terminal
        _decision = _BlockDecision(
            retry=False, hint="", loop_status="stuck",
            stuck_reason=f"re-decompose failed after {_prior_retries} retries: {outcome.get('stuck_reason', 'blocked')}",
            metacognitive_reason="re-decompose failed — terminal",
        )
        # Fall through to terminal handler below

    elif _decision.split_into:
        failure_chain.append(
            f"step {step_idx} split: combined step split into {len(_decision.split_into)} parts"
        )
        _recovery_delta = 1
        _split_reason = outcome.get("stuck_reason", "")
        if "timed out" in _split_reason.lower() or "timeout" in _split_reason.lower():
            consecutive_max_timeouts += 1
            if consecutive_max_timeouts >= max_consecutive_timeouts:
                _stuck_reason = (
                    f"Adapter appears hung: {consecutive_max_timeouts} consecutive steps all "
                    f"timed out at the {600}s ceiling across different step texts. "
                    "This is an adapter/transport failure, not a step-size issue. "
                    "Check that 'claude -p' is functional and authenticated."
                )
                log.warning("adapter-hung detection: %d consecutive max-timeouts — bailing out",
                            consecutive_max_timeouts)
                if ctx.verbose:
                    print(f"[maro] adapter appears hung ({consecutive_max_timeouts} consecutive "
                          f"ceiling timeouts) — stopping loop", file=sys.stderr, flush=True)
                return ("break", step_idx, "stuck", _stuck_reason, next_step_injected_context,
                        consecutive_max_timeouts, _recovery_delta, replan_count)
        else:
            consecutive_max_timeouts = 0
        _split_shaped = _shape_steps(list(_decision.split_into), label="replan-split")
        for _new_step in reversed(_split_shaped):
            remaining_steps.insert(0, _new_step)
            remaining_indices.insert(0, -1)
        manifest_steps.extend(_split_shaped)
        replan_count += 1
        if ctx.verbose:
            print(
                f"[maro] step {step_idx} timed out — split into {len(_decision.split_into)} steps "
                f"(step-shape replan #{replan_count})",
                file=sys.stderr, flush=True,
            )
        step_outcomes.append(step_from_decompose(
            step_text, item_index,
            status="blocked", result=step_result, iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
            # 2026-07-08 adversarial review round 2 (Skeptic): these blocked-
            # retry/redecompose/timeout-split outcomes carried the raw
            # `outcome` dict's tokens but dropped call_record/cache_read_tokens/
            # confidence/injected_steps even though outcome has them — the
            # report's "each executed step gets a detail link" promise was
            # silently broken for any step that hit one of these paths.
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            confidence=outcome.get("confidence", ""),
            injected_steps=list(outcome.get("inject_steps", [])),
            call_record=outcome.get("call_record", ""),
        ))
        return ("continue", step_idx, "", None, next_step_injected_context,
                consecutive_max_timeouts, _recovery_delta, replan_count)

    # Terminal failure — reached when no branch returned (redecompose fallthrough, or
    # explicit stuck decision from _handle_blocked_step)
    _loop_status = _decision.loop_status or "stuck"
    _stuck_reason = _decision.stuck_reason or outcome.get("stuck_reason", "blocked")
    failure_chain.append(f"step {step_idx} terminal: {_stuck_reason[:80]}")
    if item_index >= 0:
        try:
            o.mark_item(ctx.project, item_index, o.STATE_BLOCKED)
        except OSError as _mark_exc:  # FileLockTimeout: ledger contended — the run result matters more than the checkbox
            log.warning("mark_item(BLOCKED) failed for %s#%d: %s", ctx.project, item_index, _mark_exc)
    if ctx.verbose:
        print(f"[maro] step {step_idx} stuck after retry: {_stuck_reason}", file=sys.stderr, flush=True)
    try:
        from skills import attribute_failure_to_skills, find_matching_skills, record_variant_outcome, record_skill_outcome
        from metrics import estimate_cost as _est_cost
        attribute_failure_to_skills(step_text, _stuck_reason, goal=ctx.goal)
        _fail_cost = _est_cost(
            int(outcome.get("tokens_in", 0)),
            int(outcome.get("tokens_out", 0)),
            getattr(step_adapter, "model_key", None),
            cache_read_tokens=int(outcome.get("cache_read_tokens", 0)),
        )
        for _sk in find_matching_skills(step_text + " " + ctx.goal, use_router=False, project=ctx.project):
            if getattr(_sk, "variant_of", None) is not None:
                record_variant_outcome(_sk.id, success=False)
            # Phase 59: record failure telemetry per skill
            record_skill_outcome(
                _sk.id,
                success=False,
                cost_usd=_fail_cost,
                latency_ms=float(step_elapsed),
            )
    except Exception as _exc:
        # Affects the evolver's per-skill telemetry — silent loss skews learning.
        log.warning("skill outcome recording failed for stuck step %d: %s", step_idx, _exc)
    try:
        from metrics import record_step_cost
        record_step_cost(
            step_text=step_text,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            status="blocked",
            goal=ctx.goal,
            model=getattr(ctx.adapter, "model_key", ""),
            elapsed_ms=step_elapsed,
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            loop_id=getattr(ctx, "loop_id", "") or "",
        )
    except Exception as _exc:
        log.debug("metrics.record_step_cost failed for stuck step %d: %s", step_idx, _exc)
    if ctx.step_callback is not None:
        try:
            ctx.step_callback(step_idx, step_text, _stuck_reason or "blocked", "blocked")
        except Exception as _exc:
            log.debug("step_callback raised for stuck step %d: %s", step_idx, _exc)
    return ("normal", step_idx, _loop_status, _stuck_reason, next_step_injected_context,
            consecutive_max_timeouts, _recovery_delta, replan_count)


# ---------------------------------------------------------------------------
# Convergence tracking (Phase 62)
# ---------------------------------------------------------------------------

def _error_fingerprint(outcome: dict) -> str:
    """Generate a stable fingerprint for a step failure.

    Two failures with the same fingerprint indicate no convergence — the step
    is failing identically. Different fingerprints indicate the error is
    evolving, which is progress.
    """
    import hashlib
    reason = outcome.get("stuck_reason", "")
    result = outcome.get("result", "")
    # Normalize: strip timestamps, whitespace, and take first 200 chars of each
    _norm_reason = " ".join(reason.split())[:200]
    _norm_result = " ".join(result.split())[:200]
    _combined = f"{_norm_reason}|{_norm_result}"
    return hashlib.md5(_combined.encode("utf-8")).hexdigest()[:12]


def _is_converging(fingerprints: List[str]) -> bool:
    """Check if a step's retries are converging (producing different errors).

    Returns True if at least half the fingerprints are unique — the error
    landscape is changing, so retries are making progress.
    Returns False if most retries produce the same fingerprint — stuck in
    a loop.
    """
    if len(fingerprints) < 2:
        return True  # too few data points to judge
    unique = len(set(fingerprints))
    return unique / len(fingerprints) > 0.5


def _sibling_failure_rate(step_outcomes: list) -> float:
    """Fraction of completed steps that are blocked (not done).

    Used to detect whether the decomposition itself is wrong — if most
    siblings are failing, retrying individual steps won't help.
    """
    if not step_outcomes:
        return 0.0
    blocked = sum(1 for s in step_outcomes if s.status == "blocked")
    return blocked / len(step_outcomes)


# Phase 62 thresholds (from zoom-metacognition research)
_RETRY_THRESHOLD = 3         # retries before considering redecompose
_SIBLING_THRESHOLD = 0.5     # >50% sibling failure → redecompose parent
_REDECOMPOSE_THRESHOLD = 2   # max re-decompositions before flagging stuck
_NEED_INFO_PREFIX = "NEED_INFO:"  # step output prefix requesting more context

# A required external input is absent (file/url/resource does not exist). Such a
# block must NOT be retried (the resource won't appear), split, or re-decomposed
# (re-decomposition manufactures a synthetic stand-in and fakes success — see the
# fabricated-input false-success bug, dumb-loop audit round 2). It is an honest
# escalate/fail: ask for the real input, don't conjure one.
_MISSING_INPUT_SIGNALS = (
    "no such file or directory",
    "no such file",
    "no such path",
    "filenotfounderror",
    "errno 2",
    "enoent",
    "does not exist",
    "doesn't exist",
    "cannot find",
    "could not find",
    "couldn't find",
    "404",
    "not found",
)

# Verbs that mean the step *consumes* an external input (vs. produces one). We
# only short-circuit on missing-input when the step was trying to read it — a
# "create X" step legitimately may not find X yet.
_INPUT_CONSUMING_KEYWORDS = (
    "read ", "open ", "load ", "parse ", "fetch ", "download ", "import ",
    "ingest ", "contents of", "from the file", "from the url", "cat ",
)


def _looks_like_missing_input(text: str) -> bool:
    """True if text reads like a referenced external resource is absent."""
    low = (text or "").lower()
    return any(sig in low for sig in _MISSING_INPUT_SIGNALS)


def _is_input_consuming_step(step: str) -> bool:
    """True if the step's job is to consume an external input (read/fetch/load)."""
    low = (step or "").lower()
    return any(kw in low for kw in _INPUT_CONSUMING_KEYWORDS)


@dataclass
class _BlockDecision:
    """Outcome of _handle_blocked_step(): what should the main loop do next."""
    retry: bool            # True → re-queue step; False → terminate loop
    hint: str              # context to prepend on retry
    loop_status: str       # "stuck" on terminate, unchanged on retry
    stuck_reason: str      # non-empty on terminate
    split_into: List[str] = field(default_factory=list)  # non-empty → replace stuck step with these
    redecompose: bool = False  # True → re-decompose this step into sub-steps
    metacognitive_reason: str = ""  # why we chose this action (Phase 62 logging)


def _navigator_act_blocked_step(
    nav_decision, heuristic_decision, *, goal: str, step_text: str,
    step_idx: int, loop_id: str = "",
):
    """Blocked-step cutover: turn a navigator escalate into an honest stop,
    or None to keep the heuristic's recovery decision.

    Mirror of `handle._navigator_act_dispatch`, scoped to this point's
    evidence (dumb-loop audit rounds 2-4, 24 rows): escalate-ONLY — it
    defers to a human, so it cannot assert a wrong resolution; the data
    (18/19 doomed-block stops at 0.95, zero false escalates on recoverable
    blocks) earned exactly that move and no other. close asserts resolution
    without running anything and extend/fork are what the heuristic already
    does with better mechanics — all fall through. Only overrides FORWARD
    heuristic decisions (retry/split/redecompose): if the heuristic already
    stopped, its reason stands and agreement needs no act. Acting requires
    confidence >= navigator.act_confidence_floor (default 0.9 — would have
    passed 13/14 correct stops in the corpus and blocked the one 0.85
    wobble). Gated by `navigator.act_blocked_step` (default OFF). Enabled
    per Jeremy 2026-07-03 with a standing note to re-verify against actual
    usage (see GOAL_BRAIN.md decision record). Never raises.
    """
    if nav_decision is None or heuristic_decision is None:
        return None
    try:
        try:
            from config import get as _cfg_get
            if not bool(_cfg_get("navigator.act_blocked_step", False)):
                return None
            _floor = float(_cfg_get("navigator.act_confidence_floor", 0.9))
        except Exception:
            return None
        _forward = bool(
            heuristic_decision.retry
            or heuristic_decision.split_into
            or heuristic_decision.redecompose
        )
        move = getattr(nav_decision, "move", "")
        conf = float(getattr(nav_decision, "confidence", 0.0) or 0.0)
        reasoning = str(getattr(nav_decision, "reasoning", ""))
        if not _forward or move != "escalate" or conf < _floor:
            return None
        # Synthesized idunno-chain escalates never act (mirror of
        # handle._navigator_act_dispatch): the chain exhausts on adapter
        # outages too, and a dead navigator must not abort recovery mid-run.
        _payload = dict(getattr(nav_decision, "payload", {}) or {})
        if _payload.get("escalated_via") == "idunno_chain":
            return None

        stuck_reason = (
            f"NAVIGATOR_ESCALATE: recovery overridden at blocked step "
            f"(conf {conf:.2f}) — {reasoning[:300]}"
        )
        log.warning("navigator escalate override at blocked step %s: %s",
                    step_idx, stuck_reason[:200])
        try:
            from captains_log import log_event, NAVIGATOR_ACTED
            log_event(
                NAVIGATOR_ACTED,
                subject="navigator",
                summary=(
                    f"blocked_step: escalate acted (conf {conf:.2f}) — "
                    f"heuristic recovery overridden, honest stop"
                ),
                context={
                    "point": "blocked_step",
                    "move": move,
                    "confidence": conf,
                    "reasoning": reasoning[:500],
                    "goal_preview": goal[:200],
                    "step_preview": step_text[:200],
                    "step_idx": step_idx,
                    "loop_id": loop_id,
                    "heuristic_action": (
                        "retry" if heuristic_decision.retry
                        else "split" if heuristic_decision.split_into
                        else "redecompose"
                    ),
                },
            )
        except Exception:
            pass
        # Deferring to a human only works if a human finds out. The run dir
        # exists here (unlike dispatch-escalate) so finalize will also emit
        # run_completed — this is the escalation-class signal, sent now
        # because the stop reason is a deferral, not a completion.
        try:
            from notify import emit as _notify_emit
            _notify_emit("escalation", {
                "handle_id": "",
                "goal": goal,
                "status": "stuck",
                "summary": stuck_reason,
                "reason": reasoning,
                "loop_id": loop_id,
                "point": "blocked_step",
                "step": step_text[:200],
            })
        except Exception:
            pass
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="stuck",
            stuck_reason=stuck_reason,
            metacognitive_reason=(
                "navigator escalate override — doomed-block class, defer to "
                "human instead of grinding recovery"
            ),
        )
    except Exception as _act_exc:
        log.debug("navigator act_blocked_step fell through: %s", _act_exc)
        return None


def _generate_timeout_split(step_text: str, adapter) -> List[str]:
    """Ask the cheap model to split a timed-out step into smaller atomic steps.

    Uses a short 45s timeout so a struggling adapter doesn't compound the delay.
    Falls back to a simple heuristic split (one sentence per line) if the LLM
    call fails. Returns [] only if both attempts produce nothing usable.
    """
    if adapter is not None:
        try:
            from llm import LLMMessage, MODEL_CHEAP, build_adapter
            _prompt = (
                f"An autonomous agent step timed out because it was too large to complete in time.\n\n"
                f"Timed-out step: {step_text}\n\n"
                f"Rewrite this as 2-4 smaller, atomic steps that together accomplish the same goal. "
                f"Each step must be self-contained and completable independently. "
                f"Return ONLY a numbered list, one step per line, no explanation."
            )
            try:
                _split_adapter = build_adapter(model=MODEL_CHEAP)
            except Exception as _sa_exc:
                log.debug("cheap adapter build for timeout-split failed, using default: %s", _sa_exc)
                _split_adapter = adapter
            resp = _split_adapter.complete(
                [LLMMessage("user", _prompt)],
                max_tokens=300,
                temperature=0.2,
                timeout=45,
            )
            lines = [
                ln.lstrip("0123456789.-) ").strip()
                for ln in resp.content.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            steps = [ln for ln in lines if len(ln) > 10]
            if len(steps) >= 2:
                return steps
        except Exception as exc:
            log.debug("timeout split LLM call failed: %s", exc)

    # Heuristic fallback: split on sentence boundaries / conjunctions in the step text.
    import re as _re
    parts = _re.split(r"\s*;\s*|\s+and\s+then\s+|\s*\band\b\s*(?=[A-Z])", step_text)
    parts = [p.strip().rstrip(",") for p in parts if len(p.strip()) > 10]
    if len(parts) >= 2:
        log.debug("timeout split heuristic: %d parts from %r", len(parts), step_text[:60])
        return parts

    return []


_DIAGNOSIS_RETRY_THRESHOLD = 2  # retries before we consult diagnose_loop()


def _consult_diagnosis(loop_id: str) -> Optional[tuple]:
    """Mid-loop consultation of Phase 44 diagnose_loop() + Phase 45 plan_recovery().

    Returns (failure_class, recovery_plan) if diagnosis classifies anything
    actionable (non-healthy, with a recovery plan), else None.

    Safe to call mid-loop — diagnose_loop() reads whatever events.jsonl has
    been flushed so far (write_event is synchronous).
    """
    if not loop_id:
        return None
    try:
        from introspect import diagnose_loop, plan_recovery
        diag = diagnose_loop(loop_id)
        if diag.failure_class == "healthy":
            return None
        plan = plan_recovery(diag)
        if plan is None:
            return None
        return (diag.failure_class, plan)
    except Exception as exc:
        log.debug("mid-loop diagnosis consult failed: %s", exc)
        return None


def _handle_blocked_step(
    step_text: str,
    outcome: dict,
    prior_retries: int,
    adapter,
    *,
    error_fingerprints: Optional[List[str]] = None,
    step_outcomes: Optional[list] = None,
    replan_count: int = 0,
    loop_id: str = "",
) -> _BlockDecision:
    """Decide what to do when a step returns status != 'done'.

    Phase 62: Implements the zoom-metacognition decision algorithm:
    - Track error convergence (are retries producing different errors?)
    - Check sibling failure rate (is the decomposition itself wrong?)
    - Choose retry / redecompose / stuck based on evidence

    Does not mutate any loop state — returns a decision the caller applies.

    Args:
        step_text:          The step text that failed.
        outcome:            The raw outcome dict from _execute_step().
        prior_retries:      Number of times this step has already been retried.
        adapter:            LLM adapter (used for round-2 refinement hint).
        error_fingerprints: List of error fingerprints from prior retries of this step.
        step_outcomes:      All step outcomes so far (for sibling failure correlation).
        replan_count:       Number of re-decompositions already attempted.

    Returns:
        _BlockDecision — retry=True means re-queue; retry=False means terminate or redecompose.
    """
    block_reason = outcome.get("stuck_reason", "blocked")
    step_result = outcome.get("result", "")
    fingerprints = error_fingerprints or []

    # NEED_INFO: step explicitly requests more context (Phase 62 deliverable 4)
    if block_reason.startswith(_NEED_INFO_PREFIX):
        _info_needed = block_reason[len(_NEED_INFO_PREFIX):].strip()
        log.info("step NEED_INFO: %s — generating research sub-steps", _info_needed[:80])
        _research_steps = [f"Research: {_info_needed}"]
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",
            stuck_reason="",
            split_into=_research_steps + [step_text],  # research first, then retry original
            metacognitive_reason=f"step requested info: {_info_needed[:100]}",
        )

    # Missing external input: the step tried to consume a resource (file/url)
    # that does not exist. Retrying won't make it appear; splitting won't help;
    # re-decomposing FABRICATES a synthetic stand-in and fakes success (the
    # fabricated-input false-success bug). Honest escalate/fail instead — the
    # navigator wanted escalate/close 5/5 at exactly this point.
    if _is_input_consuming_step(step_text) and (
        _looks_like_missing_input(block_reason)
        or _looks_like_missing_input(step_result)
    ):
        log.info("missing external input on %r (%s) — escalating, not fabricating",
                 step_text[:60], block_reason[:60])
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="stuck",
            stuck_reason=(
                f"MISSING_INPUT: a required input appears absent — {block_reason[:120]}. "
                "A missing external input cannot be retried, split, or manufactured; "
                "escalate for the real input rather than fabricating one."
            ),
            metacognitive_reason="missing external input — honest fail, do not fabricate",
        )

    # Combined exec+analyze steps are structurally wrong — retrying identically
    # won't fix a bad step shape.  Split immediately on first block regardless
    # of the reason (timeout, LLM confusion, output overflow, etc.).
    if _is_combined_exec_analyze(step_text):
        _parts = _split_exec_analyze(step_text)
        log.info("step-shape: combined exec+analyze blocked (%s) — splitting into %d steps",
                 block_reason[:60], len(_parts))
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",      # not stuck — split recovers
            stuck_reason="",
            split_into=_parts,
            metacognitive_reason="combined exec+analyze step shape — structural split",
        )

    # Timeout failures must not be retried identically — the subprocess will
    # just time out again, burning wall-clock time with zero progress.
    # Instead: ask the cheap model to reason about how to split the step, then
    # inject the resulting steps via split_into so execution continues.
    _is_timeout = "timed out" in block_reason.lower()
    if _is_timeout:
        _split_steps = _generate_timeout_split(step_text, adapter)
        if _split_steps:
            log.info("step-shape: timeout on %r — LLM split into %d steps", step_text[:60], len(_split_steps))
            return _BlockDecision(
                retry=False,
                hint="",
                loop_status="",          # not stuck — split recovers
                stuck_reason="",
                split_into=_split_steps,
                metacognitive_reason="timeout — decomposed into smaller steps",
            )
        # Split generation itself failed — hard stop to avoid infinite spin.
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="stuck",
            stuck_reason=(
                f"TIMEOUT and split-recovery failed: {block_reason}. "
                "Consider narrowing the step scope or switching to an API adapter."
            ),
            metacognitive_reason="timeout and split-recovery failed — terminal",
        )

    # Phase 44+45 bridge: after N retries, consult the rich diagnosis
    # taxonomy (10 failure classes) before falling back to the convergence
    # heuristic. The diagnosis sees the whole loop trace — it can spot
    # retry_churn, decomposition_too_broad, etc. that per-step heuristics miss.
    if prior_retries >= _DIAGNOSIS_RETRY_THRESHOLD and loop_id:
        _diag_result = _consult_diagnosis(loop_id)
        if _diag_result is not None:
            _fc, _plan = _diag_result
            _meta = f"retries={prior_retries}, diag={_fc}, plan_action={_plan.action[:60]!r}"
            if _fc == "retry_churn":
                if replan_count < _REDECOMPOSE_THRESHOLD:
                    log.info("diagnosis (retry_churn) — re-decomposing to break churn (%s)", _meta)
                    return _BlockDecision(
                        retry=False, hint="", loop_status="", stuck_reason="",
                        redecompose=True,
                        metacognitive_reason=f"diagnose_loop: retry_churn — redecompose ({_meta})",
                    )
                log.warning("diagnosis (retry_churn) — exhausted re-decompositions (%s)", _meta)
                return _BlockDecision(
                    retry=False, hint="", loop_status="stuck",
                    stuck_reason=f"retry_churn after {replan_count} re-decompositions",
                    metacognitive_reason=f"diagnose_loop: retry_churn exhausted ({_meta})",
                )
            if _fc == "decomposition_too_broad" and replan_count < _REDECOMPOSE_THRESHOLD:
                log.info("diagnosis (decomposition_too_broad) — re-decomposing (%s)", _meta)
                return _BlockDecision(
                    retry=False, hint="", loop_status="", stuck_reason="",
                    redecompose=True,
                    metacognitive_reason=f"diagnose_loop: decomposition_too_broad ({_meta})",
                )
            if _fc == "empty_model_output" and prior_retries < _RETRY_THRESHOLD:
                _hint_txt = (
                    _plan.params.get("hint")
                    or "You MUST call complete_step or flag_stuck. Do not return bare text."
                )
                log.info("diagnosis (empty_model_output) — retry with tool-call hint (%s)", _meta)
                return _BlockDecision(
                    retry=True, hint=_hint_txt, loop_status="", stuck_reason="",
                    metacognitive_reason=f"diagnose_loop: empty_model_output — explicit tool-call hint ({_meta})",
                )
            if _fc == "constraint_false_positive" and prior_retries < _RETRY_THRESHOLD:
                log.info("diagnosis (constraint_false_positive) — retry (%s)", _meta)
                return _BlockDecision(
                    retry=True,
                    hint="[Constraint false-positive suspected; retrying with refreshed state]",
                    loop_status="", stuck_reason="",
                    metacognitive_reason=f"diagnose_loop: constraint_false_positive ({_meta})",
                )
            # Other classes (adapter_timeout, budget_exhaustion, setup_failure,
            # artifact_missing, integration_drift, token_explosion) fall through
            # to the convergence heuristic — they're either already handled by
            # earlier special cases or lack a clear mid-loop action.
            log.debug("diagnosis (%s) — no targeted mid-loop action; using heuristic (%s)", _fc, _meta)

    # Phase 62: Convergence-aware decision algorithm
    # (from zoom-metacognition research: Argyris double-loop / Boyd OODA)
    converging = _is_converging(fingerprints)
    sibling_rate = _sibling_failure_rate(step_outcomes) if step_outcomes else 0.0

    # Log the metacognitive state for every decision
    _meta_ctx = (
        f"retries={prior_retries}, converging={converging}, "
        f"sibling_fail_rate={sibling_rate:.0%}, replan_count={replan_count}"
    )

    # Check sibling failure correlation first (zoom-metacognition §3.3)
    # If most siblings are failing, the decomposition is wrong — redecompose
    if (sibling_rate > _SIBLING_THRESHOLD
            and len(step_outcomes or []) >= 3
            and replan_count < _REDECOMPOSE_THRESHOLD):
        log.info("sibling failure rate %.0f%% > %.0f%% — triggering re-decomposition "
                 "(%s)", sibling_rate * 100, _SIBLING_THRESHOLD * 100, _meta_ctx)
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",
            stuck_reason="",
            redecompose=True,
            metacognitive_reason=(
                f"sibling failure rate {sibling_rate:.0%} exceeds {_SIBLING_THRESHOLD:.0%} "
                f"threshold — decomposition is likely wrong ({_meta_ctx})"
            ),
        )

    # Standard retry path: retry if under threshold AND converging
    if prior_retries < _RETRY_THRESHOLD and converging:
        if prior_retries == 0:
            # Round 1: generic fallback hint
            hint = (
                f"[Previous attempt blocked: {block_reason[:120]}] "
                "Try an alternative approach: use a different tool, rephrase the request, "
                "work around the obstacle, or summarize what you know so far and mark complete. "
                "If you lack required information, say NEED_INFO: [what's missing] instead of guessing."
            )
        else:
            # Round 2+: LLM-assisted targeted refinement hint
            hint = _generate_refinement_hint(
                step_text=step_text,
                block_reason=block_reason,
                partial_result=step_result,
                adapter=adapter,
            )
        log.info("retry (converging): %s", _meta_ctx)
        return _BlockDecision(
            retry=True, hint=hint, loop_status="", stuck_reason="",
            metacognitive_reason=f"retry — errors converging, under threshold ({_meta_ctx})",
        )

    # Not converging or threshold exceeded — try re-decomposition
    if replan_count < _REDECOMPOSE_THRESHOLD:
        log.info("not converging or threshold exceeded — re-decomposing step (%s)", _meta_ctx)
        return _BlockDecision(
            retry=False,
            hint="",
            loop_status="",
            stuck_reason="",
            redecompose=True,
            metacognitive_reason=(
                f"not converging after {prior_retries} retries — "
                f"re-decomposing step ({_meta_ctx})"
            ),
        )

    # Exhausted all options — terminal failure
    log.warning("terminal: exhausted retries and re-decompositions (%s)", _meta_ctx)
    return _BlockDecision(
        retry=False,
        hint="",
        loop_status="stuck",
        stuck_reason=block_reason,
        metacognitive_reason=(
            f"exhausted: {prior_retries} retries, {replan_count} re-decompositions, "
            f"converging={converging}, sibling_rate={sibling_rate:.0%}"
        ),
    )
