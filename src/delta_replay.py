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
