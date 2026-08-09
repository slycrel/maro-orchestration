"""Replay-Δ measurer — effect-based lesson scoring (Δ-gate slice 1, step 1).

DELTA_GATE_BUILD_BRIEF §2/§3.1: score a lesson by whether its presence in a
decision-bearing prompt measurably moves the sampled decision toward the
known-good action (LeAct arXiv 2607.21856, App D.2.1 action-match — the
no-logprobs reward). This module is the standalone INSTRUMENT: it measures;
it does not promote. Promotion wiring (brief §3.3) comes only after the
instrument separates known-effective from known-inert lessons on the LT-1
arms against pre-registered predictions — if it can't, it's noise and must
not gate anything (brief §4, falsifier 1).

Shape stolen from the seed-reader A/B harness (frozen inputs, per-cell raw
records, deterministic scoring, no store writes): the replay never touches
the tiered-lesson store, never records costs against a run, and never sets
a run-dir contextvar — arms are pure string surgery on RECORDED prompts
from `<run>/build/calls/*.json`.

Decision-bearing calls: purposes with a crisp, enumerable action field in a
JSON response (navigator decision → "move", adaptive supervision →
"action"). Oracle discipline: a recorded action is known-good ONLY when its
run was judged goal_achieved=True — calls from unjudged or failed runs are
replayable but carry no oracle, and score_lesson skips them.

Arms per call (both directions of the same measure):
  - lesson PRESENT in the recorded prompt  → with = as recorded,
    without = prompt with the lesson's injected line removed (ablation).
  - lesson ABSENT from the recorded prompt → without = as recorded,
    with = lesson appended in the production injection format (injection).

Δ for the lesson = mean(action-match, with-arm) − mean(action-match,
without-arm) over M samples per arm per call. Positive Δ: the lesson moves
decisions toward known-good. Rule-stratum lessons ("run tests before
claiming done" — the lesson IS its own action) have Δ≈0 by construction
(LeAct §6 self-referential collapse); classify_stratum tags them so the
aggregate is read per-stratum, never pooled (brief §3.2).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("delta_replay")


# ---------------------------------------------------------------------------
# Decision-call registry: purpose → (action_key, action extractor)
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> Optional[dict]:
    """Recorded decision responses are JSON, often fenced. Deterministic
    parse with the same tolerance the live consumers apply: strip code
    fences, then take the first {...} block."""
    if not text:
        return None
    t = str(text).strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _keyed_action(key: str) -> Callable[[str], Optional[str]]:
    def _extract(text: str) -> Optional[str]:
        obj = _parse_json_response(text)
        if not isinstance(obj, dict):
            return None
        val = obj.get(key)
        return str(val).strip().lower() if isinstance(val, str) and val.strip() else None
    return _extract


# Purposes whose response carries an enumerable decision. Extend by adding a
# row — score_lesson and find_decision_calls key on this registry only.
DECISION_PURPOSES: Dict[str, Callable[[str], Optional[str]]] = {
    "navigator decision": _keyed_action("move"),
    "adaptive supervision": _keyed_action("action"),
}


@dataclass
class DecisionCall:
    """One replayable decision-bearing call, loaded from a call record."""
    run_id: str
    call_path: str
    purpose: str
    prompt: str
    recorded_action: str
    oracle: bool                 # run judged goal_achieved=True → action is known-good
    model: str = ""


def find_decision_calls(run_dir: Path,
                        goal_achieved: Optional[bool] = None) -> List[DecisionCall]:
    """Decision-bearing calls from one run dir.

    `goal_achieved` is the run's judged verdict (caller reads it from
    run_card.json / the outcome ledger — this module doesn't guess).
    Calls whose recorded response doesn't yield a parseable action are
    dropped: they can't be scored in either arm.
    """
    calls_dir = Path(run_dir) / "build" / "calls"
    if not calls_dir.is_dir():
        return []
    out: List[DecisionCall] = []
    for p in sorted(calls_dir.glob("call-*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        purpose = str(rec.get("purpose") or "")
        extractor = DECISION_PURPOSES.get(purpose)
        if extractor is None:
            continue
        action = extractor(str(rec.get("response") or ""))
        if not action:
            continue
        out.append(DecisionCall(
            run_id=Path(run_dir).name,
            call_path=str(p),
            purpose=purpose,
            prompt=str(rec.get("prompt") or ""),
            recorded_action=action,
            oracle=goal_achieved is True,
            model=str(rec.get("model") or ""),
        ))
    return out


# ---------------------------------------------------------------------------
# Arm construction — pure string surgery on the recorded prompt
# ---------------------------------------------------------------------------

# Production injection line shape (knowledge_web.inject_tiered_lessons):
#   "- ✓ <lesson text>" under "### Long-Term Lessons (always apply)".
_INJECT_HEADER = "## Tiered Lessons\n\n### Long-Term Lessons (always apply)"


def lesson_in_prompt(prompt: str, lesson_text: str) -> bool:
    """Is this lesson's text present in the recorded prompt? Whitespace-
    normalized substring — injection renders the lesson verbatim on one
    line, but recorded prompts may re-wrap."""
    norm = " ".join(str(lesson_text).split())
    return bool(norm) and norm in " ".join(str(prompt).split())


def prompt_with_lesson(prompt: str, lesson_text: str) -> str:
    """The with-arm prompt. Already present → unchanged (the recorded
    prompt IS the with-arm). Absent → append a production-format lessons
    block; appended (not spliced) so the surgery is position-independent
    and reversible."""
    if lesson_in_prompt(prompt, lesson_text):
        return prompt
    icon_line = f"- ✓ {lesson_text}"
    return f"{prompt}\n\n{_INJECT_HEADER}\n{icon_line}"


def prompt_without_lesson(prompt: str, lesson_text: str) -> str:
    """The without-arm prompt. Absent → unchanged. Present → remove the
    LINE(S) carrying the lesson text, not just the substring — a dangling
    "- ✓ " bullet would leak the injection scaffold into the ablated arm.
    Multi-line renderings fall back to substring excision."""
    if not lesson_in_prompt(prompt, lesson_text):
        return prompt
    lines = prompt.splitlines()
    norm = " ".join(str(lesson_text).split())
    kept = [ln for ln in lines if norm not in " ".join(ln.split())]
    if len(kept) < len(lines):
        return "\n".join(_drop_orphan_lesson_headers(kept))
    # Lesson spans multiple lines: excise the normalized region by rebuilding
    # from words. Rare; honest fallback rather than silent no-op.
    joined = " ".join(prompt.split())
    return joined.replace(norm, " ").strip()


_LESSON_HEADERS = (
    "## Tiered Lessons",
    "### Long-Term Lessons (always apply)",
    "### Medium-Term Lessons (apply if relevant)",
    "### Session Context",
)


def _drop_orphan_lesson_headers(lines: List[str]) -> List[str]:
    """After bullet excision, drop lesson-block headers left with no bullets
    under them — a dangling scaffold would leak the injection structure into
    the ablated arm. A header keeps its place if any bullet survives in its
    section (### scans to the next header line; ## Tiered Lessons scans to
    the next ## section, so a populated sub-section preserves it)."""
    out: List[str] = []
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped in _LESSON_HEADERS:
            top_level = stripped == "## Tiered Lessons"
            has_bullet = False
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s.startswith("- "):
                    has_bullet = True
                    break
                if not s:
                    continue
                if top_level and s.startswith("## ") and not s.startswith("### "):
                    break
                if not top_level and s.startswith("#"):
                    break
                if not top_level:
                    break
            if not has_bullet:
                continue
        out.append(ln)
    return out


# ---------------------------------------------------------------------------
# Stratification (brief §3.2): rule vs reason
# ---------------------------------------------------------------------------

# A RULE lesson is its own action — imperative-led prescription whose
# injection trivially reproduces itself (Δ≈0 by construction, LeAct §6).
# A REASON lesson records an observation/mechanism the decision must apply.
# Heuristic shape stolen from the seed-reader harness's what_not_how.
_IMPERATIVE = re.compile(
    r"^\s*(use|distill|keep|run|always|never|prefer|add|avoid|ensure|"
    r"budget|verify|check|include|set|split|batch|cache|limit|treat|record|"
    r"extract|capture|validate|confirm|apply|start|stop|make|write|read|"
    r"fetch|store|log|prioritize|maintain|implement|require|test|break|"
    r"do|don't|dont)\b", re.I)

_OBSERVATION = re.compile(
    r"\b(assumed|expected|found|turned out|differed|was not|were not|"
    r"wasn't|weren't|did not|didn't|failed to|proved|revealed|mismatch|"
    r"surprise|unexpectedly|because|caused|caches?d?|instead of|"
    r"contrary to|judge[ds]? .{0,20}better)\b", re.I)

STRATUM_RULE = "rule"
STRATUM_REASON = "reason"


def classify_stratum(lesson_text: str) -> str:
    """Deterministic, honestly rough — the corpus split it produces is a
    finding to examine (brief §3.2), not ground truth."""
    body = re.sub(r"^\s*\[[^\]]*\]\s*", "", str(lesson_text))       # [tag]
    body = re.sub(r"^[\w /-]{1,40}:\s*", "", body)                  # topic:
    if _OBSERVATION.search(lesson_text):
        return STRATUM_REASON
    return STRATUM_RULE if _IMPERATIVE.match(body) else STRATUM_REASON


# ---------------------------------------------------------------------------
# Replay + scoring
# ---------------------------------------------------------------------------

_ROLE_MARKER = re.compile(r"^\[(system|user|assistant)\]$", re.MULTILINE)


def recorded_prompt_to_messages(prompt: str) -> List[Any]:
    """Recorded call prompts flatten the message list with [system]/[user]
    role-marker lines (llm.py capture format). Rebuild LLMMessages so the
    replay presents the same role structure the original call did; a prompt
    without markers replays as a single user message."""
    from llm import LLMMessage
    parts = _ROLE_MARKER.split(prompt)
    # split yields [pre, role1, body1, role2, body2, ...]; pre is "" when
    # the prompt starts with a marker.
    if len(parts) < 3:
        return [LLMMessage(role="user", content=prompt)]
    msgs: List[Any] = []
    if parts[0].strip():
        msgs.append(LLMMessage(role="user", content=parts[0].strip()))
    for role, body in zip(parts[1::2], parts[2::2]):
        msgs.append(LLMMessage(role=role, content=body.strip()))
    return msgs

@dataclass
class CallDelta:
    """Per-call replay result."""
    run_id: str
    call_path: str
    purpose: str
    recorded_action: str
    lesson_was_present: bool
    with_actions: List[Optional[str]] = field(default_factory=list)
    without_actions: List[Optional[str]] = field(default_factory=list)

    @property
    def with_match(self) -> float:
        return _match_rate(self.with_actions, self.recorded_action)

    @property
    def without_match(self) -> float:
        return _match_rate(self.without_actions, self.recorded_action)

    @property
    def delta(self) -> float:
        return self.with_match - self.without_match


def _match_rate(actions: List[Optional[str]], target: str) -> float:
    if not actions:
        return 0.0
    return sum(1 for a in actions if a == target) / len(actions)


@dataclass
class LessonDelta:
    """The lesson's Δ over its call set — the number the gate will read."""
    lesson_text: str
    stratum: str
    calls: List[CallDelta] = field(default_factory=list)
    skipped_no_oracle: int = 0
    replay_errors: int = 0

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def delta(self) -> Optional[float]:
        if not self.calls:
            return None
        return sum(c.delta for c in self.calls) / len(self.calls)

    def jackknife(self) -> Optional[float]:
        """Leave-one-call-out spread of the mean Δ (harness precedent):
        max |Δ_mean − Δ_mean_without_call_i|. One call can't quietly own
        the verdict without this saying so."""
        if len(self.calls) < 2:
            return None
        full = self.delta
        spread = 0.0
        for i in range(len(self.calls)):
            rest = [c.delta for j, c in enumerate(self.calls) if j != i]
            spread = max(spread, abs(full - sum(rest) / len(rest)))
        return spread

    def as_dict(self) -> Dict[str, Any]:
        return {
            "lesson_text": self.lesson_text,
            "stratum": self.stratum,
            "n_calls": self.n_calls,
            "delta": self.delta,
            "jackknife_spread": self.jackknife(),
            "skipped_no_oracle": self.skipped_no_oracle,
            "replay_errors": self.replay_errors,
            "calls": [
                {
                    "run_id": c.run_id,
                    "call": Path(c.call_path).name,
                    "purpose": c.purpose,
                    "recorded_action": c.recorded_action,
                    "lesson_was_present": c.lesson_was_present,
                    "with_actions": c.with_actions,
                    "without_actions": c.without_actions,
                    "with_match": c.with_match,
                    "without_match": c.without_match,
                    "delta": c.delta,
                }
                for c in self.calls
            ],
        }


def score_lesson(
    lesson_text: str,
    calls: List[DecisionCall],
    adapter: Any,
    *,
    samples: int = 3,
    max_calls: int = 12,
) -> LessonDelta:
    """Measure one lesson's Δ over a set of decision calls.

    `adapter` is an llm adapter (a complete-method object returning a
    response with `.content`) — callers build it (hosted-free cheap rung
    is the intended tier; the instrument doesn't choose spend). Non-oracle calls
    are counted and skipped, never silently dropped (no-silent-caps).
    Replay failures score as no-match (None action) — a lesson can't earn
    Δ from calls the replay couldn't complete, and the error count is on
    the readout.
    """
    result = LessonDelta(lesson_text=lesson_text,
                         stratum=classify_stratum(lesson_text))
    usable = []
    for c in calls:
        if not c.oracle:
            result.skipped_no_oracle += 1
            continue
        usable.append(c)
    if len(usable) > max_calls:
        log.info("delta_replay: capping %d oracle calls to %d",
                 len(usable), max_calls)
        usable = usable[:max_calls]

    for call in usable:
        extractor = DECISION_PURPOSES[call.purpose]
        cd = CallDelta(
            run_id=call.run_id,
            call_path=call.call_path,
            purpose=call.purpose,
            recorded_action=call.recorded_action,
            lesson_was_present=lesson_in_prompt(call.prompt, lesson_text),
        )
        arms = (
            (prompt_with_lesson(call.prompt, lesson_text), cd.with_actions),
            (prompt_without_lesson(call.prompt, lesson_text), cd.without_actions),
        )
        for prompt, bucket in arms:
            msgs = recorded_prompt_to_messages(prompt)
            for _ in range(samples):
                try:
                    resp = adapter.complete(
                        msgs, no_tools=True, purpose="delta replay")
                    bucket.append(extractor(str(
                        getattr(resp, "content", "") or "")))
                except Exception as exc:
                    result.replay_errors += 1
                    bucket.append(None)
                    log.debug("delta_replay: sample failed for %s: %s",
                              call.call_path, exc)
        result.calls.append(cd)
    return result


# ---------------------------------------------------------------------------
# Effect route orchestration (brief §3.3/§3.4) — CLI-driven, never ambient
# ---------------------------------------------------------------------------

def gather_oracle_decision_calls(runs_root: Optional[Path] = None) -> List[DecisionCall]:
    """Every decision-bearing call across runs judged goal_achieved=True —
    the corpus a lesson's Δ is measured over. run_card.json is the verdict
    source (curation stamps it; absent/unjudged runs contribute nothing)."""
    if runs_root is None:
        try:
            from config import workspace_root
            runs_root = Path(workspace_root()) / "runs"
        except Exception:
            runs_root = Path.home() / ".maro" / "workspace" / "runs"
    out: List[DecisionCall] = []
    if not Path(runs_root).is_dir():
        return out
    for rd in sorted(Path(runs_root).iterdir()):
        card_path = rd / "run_card.json"
        if not card_path.exists():
            continue
        try:
            card = json.loads(card_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if card.get("goal_achieved") is not True:
            continue
        out.extend(find_decision_calls(rd, goal_achieved=True))
    return out


def run_effect_route(
    adapter: Any,
    *,
    promote: bool = False,
    demote: bool = False,
    samples: int = 3,
    limit: int = 5,
    lesson_ids: Optional[List[str]] = None,
    remint_pending: bool = False,
) -> Dict[str, Any]:
    """Measure Δ for candidate MEDIUM lessons and (optionally) act on the
    ones that clear either effect bar — the routes-census readout (brief
    §3.4) is the return value either way: per candidate, Δ + both routes'
    verdicts + overlap, so "what tenure alone would have missed" is
    readable directly. promote=False demote=False is the census-only dry
    run; demote=True stamps measured-negative rows out of decision
    injection (knowledge_web.demote_lesson_by_effect, 2026-08-08).

    Spend honesty: measurement is limit × 2 arms × samples × n_calls
    replays on the caller's adapter — deliberate CLI-driven spend, which is
    why nothing in the loop or maintenance cycles calls this.
    """
    from knowledge_web import (
        MemoryTier, PROMOTE_MIN_SCORE, PROMOTE_MIN_SESSIONS,
        REMINT_PATTERN_STRIKES,
        _is_contested, _is_quarantined, load_tiered_lessons,
        demote_lesson_by_effect, promote_lesson_by_effect,
        resolve_remint_watch,
    )
    calls = gather_oracle_decision_calls()
    rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0,
                               limit=None)
    if remint_pending:
        # The strike-3 lane (decision dcf8eab8): watched re-mints whose
        # pattern earned a forced re-measurement. Selection is derived from
        # the stamps — there is no separate pending file to drift. A watch
        # row can tenure-promote to LONG before the operator runs this
        # (only effect-demote blocks tenure), so the selector covers both
        # tiers — a MEDIUM-only scan would strand the queued event
        # (2026-08-08 adversarial review).
        rows = rows + load_tiered_lessons(tier=MemoryTier.LONG,
                                          min_score=0.0, limit=None)
        rows = [t for t in rows
                if (t.delta_evidence or {}).get("route") == "remint-watch"
                and int((t.delta_evidence or {}).get("strikes") or 0)
                >= REMINT_PATTERN_STRIKES]
    elif lesson_ids:
        # Explicit naming measures ANY row — including provisional/
        # quarantined/contested ones, which are excluded only from the
        # unnamed census sweep below. Measurement is evidence-gathering,
        # not an act; the act routes keep their own guards. (Review Part 1
        # finding 6: the old order filtered before selection, so the
        # documented "stamp is a true measurement either way" eligibility
        # was unreachable even by naming the lesson.)
        wanted = set(lesson_ids)
        rows = [t for t in rows if t.lesson_id in wanted]
    else:
        rows = [t for t in rows
                if not (t.provisional or _is_quarantined(t)
                        or _is_contested(t))
                and classify_stratum(t.lesson) == STRATUM_REASON]
        rows.sort(key=lambda t: t.score, reverse=True)
    dropped = max(0, len(rows) - limit)
    rows = rows[:limit]

    census: List[Dict[str, Any]] = []
    for t in rows:
        res = score_lesson(t.lesson, calls, adapter, samples=samples,
                           max_calls=len(calls) or 1)
        ev = res.as_dict()
        ev.pop("calls", None)  # census row stays readable; raw detail is per-call
        tenure_ok = (t.score >= PROMOTE_MIN_SCORE
                     and t.sessions_validated >= PROMOTE_MIN_SESSIONS)
        # The strike-3 lane acts by definition (decision dcf8eab8: the
        # forced re-measurement "sets the stamp whichever way it lands") —
        # without this, --remint-pending alone would measure a decisively
        # negative Δ and still clear probation below (2026-08-08 review).
        # Both routes carry their own Δ-bar guards, so applying them is
        # only ever "stamp what the measurement supports".
        _is_watch = (t.delta_evidence or {}).get("route") == "remint-watch"
        promoted = False
        if promote or (remint_pending and _is_watch):
            promoted = promote_lesson_by_effect(t.lesson_id, ev)
        demoted = False
        if (demote or (remint_pending and _is_watch)) and not promoted:
            demoted = demote_lesson_by_effect(t.lesson_id, ev)
        watch_cleared = False
        if ((remint_pending or promote or demote)
                and (t.delta_evidence or {}).get("route") == "remint-watch"
                and not promoted and not demoted):
            # Acting runs only — the census-only mode stays a true dry run.
            # Measurement replaces measurement: a clean full-set result that
            # cleared neither bar still ends the probation (route
            # "measured", which also resets the archive lineage clock).
            # resolve_remint_watch refuses errored or under-floor runs.
            watch_cleared = resolve_remint_watch(t.lesson_id, ev)
        census.append({
            "lesson_id": t.lesson_id,
            "lesson": t.lesson[:160],
            "score": t.score,
            "sessions_validated": t.sessions_validated,
            **ev,
            "tenure_eligible": tenure_ok,
            "promoted_by_effect": promoted,
            "demoted_by_effect": demoted,
            "remint_watch_cleared": watch_cleared,
        })
    if dropped:
        log.info("run_effect_route: measured top %d of %d candidates "
                 "(%d not measured this pass)", len(rows),
                 len(rows) + dropped, dropped)
    return {
        "n_oracle_calls": len(calls),
        "samples": samples,
        "promote": promote,
        "demote": demote,
        "candidates_not_measured": dropped,
        "census": census,
    }


def _main(argv: List[str]) -> int:
    """CLI: `python3 -m delta_replay [--promote] [--demote] [--limit N]
    [--samples N] [--lesson-id ID ...]` — census-only by default; --promote
    applies the effect route to rows that clear the bar, --demote stamps
    measured-negative rows out of decision injection. Hosted-free rung only
    (the ~$0 replay backend); refuses to run without it rather than
    silently spending on a paid tier."""
    import argparse
    ap = argparse.ArgumentParser(prog="delta_replay")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--demote", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--lesson-id", action="append", default=None)
    ap.add_argument("--remint-pending", action="store_true",
                    help="measure the strike-3 remint-watch rows (forced "
                         "re-measure lane, decision dcf8eab8); combine with "
                         "--demote/--promote to act, alone it still clears "
                         "probation on a clean null result")
    ap.add_argument("--pace-s", type=float, default=6.5,
                    help="min seconds between replays (free-tier RPM; "
                         "the validation runs died unpaced)")
    args = ap.parse_args(argv)

    import time as _time
    import hosted_free as _hf
    from hosted_free import build_hosted_free_adapter
    # Batch-replay posture (validation run lessons, see the 2026-08-06
    # history record): the per-process latency breaker is the wrong guard
    # here, and bursts trip free-tier RPM/TPM — disable the cap, pace, and
    # clear breaker state + retry once on a trip.
    _hf.max_latency_ms = lambda: 0
    inner = build_hosted_free_adapter()
    if inner is None:
        print("hosted-free rung unavailable — not falling back to a paid "
              "tier; configure GROQ/GEMINI keys or pass an adapter in code")
        return 2

    class _Paced:
        def __init__(self):
            self._last = 0.0

        def complete(self, messages, **kw):
            wait = self._last + args.pace_s - _time.time()
            if wait > 0:
                _time.sleep(wait)
            call_kw = {**kw, "no_tools": True,
                       "purpose": kw.get("purpose") or "delta replay"}
            try:
                return inner.complete(messages, **call_kw)
            except Exception:
                _time.sleep(65.0)
                _hf.reset_cache()
                return inner.complete(messages, **call_kw)
            finally:
                self._last = _time.time()

    out = run_effect_route(_Paced(), promote=args.promote, demote=args.demote,
                           limit=args.limit, samples=args.samples,
                           lesson_ids=args.lesson_id,
                           remint_pending=args.remint_pending)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_main(_sys.argv[1:]))
