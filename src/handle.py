#!/usr/bin/env python3
"""Maro's Handle — unified entry point for all incoming requests.

Routes to NOW lane (1-shot) or AGENDA lane (multi-step loop) based on
intent classification. This is the interface Jeremy sends messages through.

Response timing contract:
    - Immediate ack printed within the call (before execution starts)
    - Status updates printed as execution progresses (--verbose)
    - Substantive result in HandleResult.result

Usage:
    from handle import handle
    result = handle("research winning polymarket strategies")
    print(result.format())

CLI:
    python -m handle "your request here" [--project SLUG] [--dry-run]
    orch maro-handle "your request here"
"""

from __future__ import annotations

import json
import logging
import os
import re

import sys
import time
import uuid

from typing import List, TYPE_CHECKING
if TYPE_CHECKING:
    from conversation import ConversationChannel
    from persona import PersonaRegistry

log = logging.getLogger("maro.handle")
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ancestry import Origin


# ---------------------------------------------------------------------------
# Magic prefix registry
# ---------------------------------------------------------------------------
# Each prefix mutates execution without changing the goal text. The registry
# (one mechanism for both literal-string prefixes and the persona:<name>:
# capture-group prefix — adversarial-review R1 batch-1 finding #1) lives in
# prefixes.py, a neutral module recall.py also imports directly instead of
# reaching into handle's private internals (finding #2). Re-exported here
# under their historical private names so every existing call site and test
# in this file (and the handful of tests that import them from `handle`)
# keeps working unchanged.
#
# _PREFIX_REGISTRY is the real prefixes.PREFIX_REGISTRY object, not a copy —
# mutate it in place (`.append(...)`) to add a rule. Never reassign this
# name (`handle._PREFIX_REGISTRY = [...]`); apply_prefixes() closes over
# prefixes.py's own module-level name, so a reassignment here would silently
# no-op instead of erroring (adversarial-review batch-1, Architect finding
# #7, 2026-07-13). Add new prefixes via prefixes.PREFIX_REGISTRY directly.
from prefixes import (
    PrefixRule as _PrefixRule,
    PrefixResult as _PrefixResult,
    PREFIX_REGISTRY as _PREFIX_REGISTRY,
    apply_prefixes as _apply_prefixes,
)


def _resolve_forced_persona(requested: str, registry: "PersonaRegistry") -> "tuple[str, bool]":
    """Validate a forced-persona request (from a prefix or the `persona=` kwarg).

    Returns (name, honored). When `requested` is empty or doesn't match a real,
    registered persona, honored=False and a warning is logged listing what IS
    available — callers fall back to persona_for_goal() auto-selection rather
    than silently producing no persona context (BACKLOG hist-r2-02: an unknown
    forced persona must degrade gracefully, not crash or no-op quietly).
    """
    if not requested:
        return "", False
    if registry.load(requested) is not None:
        return requested, True
    log.warning(
        "handle: forced persona %r not found — available: %s — falling back to auto-selection",
        requested, ", ".join(registry.list()) or "(none)",
    )
    return "", False


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class HandleResult:
    handle_id: str
    lane: str                   # "now" | "agenda"
    lane_confidence: float
    classification_reason: str
    message: str
    status: str                 # "done" | "stuck" | "error"
    result: str                 # The substantive response / work product
    project: Optional[str] = None
    loop_result: Any = None     # LoopResult if AGENDA
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    artifact_path: Optional[str] = None

    def format(self, mode: str = "text") -> str:
        if mode == "json":
            return json.dumps({
                "handle_id": self.handle_id,
                "lane": self.lane,
                "classification_reason": self.classification_reason,
                "status": self.status,
                "result": self.result,
                "project": self.project,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "elapsed_ms": self.elapsed_ms,
                "artifact_path": self.artifact_path,
            }, indent=2)
        lines = [
            f"handle_id={self.handle_id}",
            f"lane={self.lane} (confidence={self.lane_confidence:.2f})",
            f"status={self.status}",
            f"tokens={self.tokens_in}in+{self.tokens_out}out elapsed={self.elapsed_ms}ms",
        ]
        if self.project:
            lines.append(f"project={self.project}")
        if self.artifact_path:
            lines.append(f"artifact={self.artifact_path}")
        lines.append("")
        lines.append(self.result)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NOW lane executor
# ---------------------------------------------------------------------------

_NOW_SYSTEM = """You are an autonomous AI assistant.
Answer the user's request directly and completely. Be thorough but concise.
If the request is a question, answer it. If it's a task, complete it.
Do not hedge or defer — just do the work.
"""

# Appended to _NOW_SYSTEM only when link content was pre-fetched. The shape
# comes from the conversational-compute decree (Jeremy 2026-07-17, GOAL_BRAIN):
# an opinionated ~2-min read, and honest "can't see it" over fake certainty
# or reflecting the ask back ("that's a thing but you should figure it out").
_NOW_LINK_READ = """
Fetched content for the link(s) in the request is provided above it. Answer
from that content — never tell the user to go look for themselves. If the
ask is link triage ("is this worth my time?"-shaped), give a quick
opinionated read: verdict first (worth your time / probably not /
interesting, but wait), what it actually is, one or two reasons it does or
doesn't fit the user's setup, and the catch or the one thing to verify
next. If the fetched content is empty or missing something you need, say
plainly what you couldn't see ("can't see it from here — send the direct
repo link or a screenshot") rather than guessing.
"""

_BTW_SYSTEM = """You are an autonomous agent surfacing a non-blocking observation.
Note what you observe, briefly and specifically. Do not attempt to fix or solve anything.
Keep it to 1–3 sentences max. Format: one sentence per observation, plain text.
This is a side-note, not a task result.
"""


# Answer-first delivery (2026-07-17): deferred learning (lesson extraction +
# skill crystallization — 2-4 subprocess LLM calls, ~90-120s on this box) is
# bookkeeping the user never sees; running it before the run_completed notify
# was the biggest slice of calm-echo's ~285s post-loop tail. _handle_impl
# registers the work here instead of running it inline; handle()'s finalize
# block drains it AFTER the notify emit, then refreshes the run card's
# lesson-consuming fields (decision priors, classification) via the same
# contract audit repair uses. The quality-gate escalation path drains early
# instead — the escalated retry's decompose recalls lessons from the loop it
# is retrying.
_POST_NOTIFY_LEARNING: dict = {}


def _defer_learning_post_notify(handle_id: str, fn) -> None:
    _POST_NOTIFY_LEARNING.setdefault(handle_id, []).append(fn)


def _drain_deferred_learning(handle_id: str) -> int:
    """Run + clear any registered deferred learning for handle_id.

    Returns the number of callables run. Never raises."""
    fns = _POST_NOTIFY_LEARNING.pop(handle_id, [])
    for fn in fns:
        try:
            fn()
        except Exception as exc:
            log.warning("deferred learning failed for handle %s: %s",
                        handle_id, exc)
    return len(fns)


# The maintenance twin of this lane (async-tail decree, 2026-08-11) lives in
# loop_finalize (defer_maintenance_post_notify / drain_deferred_maintenance)
# — NOT here, because this module is also a `python -m handle` entry point:
# run that way it executes as __main__, and loop_finalize's `import handle`
# would load a second copy whose registry the finalize block below never
# drains (3-lens review of 707a541). The learning registry above is safe in
# this module: its registrar and drainer are both local, so they always
# agree on module identity. Drained ONLY by handle()'s finalize, after
# run_completed and after the learning drain — never by the quality-gate
# early drain: the escalated retry needs the lessons of the loop it is
# retrying, not its promotions.


def _is_complex_directive(message: str) -> bool:
    """Heuristic: does a NOW-classified message actually require Director-level planning?

    Returns True when the message shows signs of multi-step complexity that the
    single-shot NOW lane would handle poorly. Used to gate optional escalation to
    AGENDA when now_lane.escalate_to_director is enabled.

    Signals:
      - More than 25 words (classifier uses ≤15 as simple)
      - Multi-step sequencing language
      - Action verbs that imply building/researching/designing
      - Multiple sentences (compound task)
      - Two or more coordinated action-verb heads ("write X and run it and
        save Y" — a pipeline, even when each verb alone would stay NOW)
    """
    import re
    # URLs are opaque tokens — their dots/digits must not read as sentence
    # boundaries (live 2026-07-17: "is this worth my time? <x.com link>"
    # escalated NOW→agenda because the URL's dots counted as sentences).
    try:
        from web_fetch import extract_urls_from_text
        for _u in extract_urls_from_text(message):
            message = message.replace(_u, "URL")
    except Exception:
        pass
    msg_lower = message.lower().strip()
    words = msg_lower.split()

    if len(words) > 25:
        return True

    # Multi-step indicators
    _SEQUENCE_PATTERNS = [
        r'\bthen\b', r'\bfirst\b.{0,60}\bthen\b', r'\bafter(ward)?\b',
        r'\bstep\s+\d', r'\b\d+\.\s', r'\band\s+also\b', r'\badditionally\b',
    ]
    if any(re.search(p, msg_lower) for p in _SEQUENCE_PATTERNS):
        return True

    # Action verbs implying multi-step work (require 8+ words to avoid false positives
    # on short creative requests like "write a haiku" or "create a joke")
    _COMPLEX_VERBS = {
        "build", "implement", "design", "research", "analyze",
        "investigate", "develop", "plan", "architect", "refactor",
        "migrate", "integrate", "deploy", "configure",
    }
    first_words = set(words[:8])
    if len(words) >= 8 and first_words & _COMPLEX_VERBS:
        return True

    # Multiple sentences (compound task)
    sentences = [s.strip() for s in re.split(r'[.!?]', message) if s.strip() and len(s.strip()) > 10]
    if len(sentences) >= 2:
        return True

    # Coordinated action-verb heads: "write a script and run it and save the
    # outputs" is a multi-step pipeline even though "write" alone stays NOW
    # (short creative requests). A head is an action verb at the start of the
    # message or immediately after a coordinator (and/then/also/plus). Two or
    # more heads = compound directive. (BACKLOG #4 residual, 2026-07-03: the
    # long form of the run_health goal was already caught by word count; the
    # short compound-imperative form was the remaining hole.)
    _ACTION_HEADS = _COMPLEX_VERBS | {
        "write", "create", "run", "execute", "save", "generate", "test",
        "check", "fix", "update", "add", "install", "verify", "measure",
        "document", "commit", "push", "report", "summarize", "compare",
    }
    heads = 0
    if words and words[0].strip(",.;:") in _ACTION_HEADS:
        heads += 1
    heads += len(re.findall(
        r"\b(?:and|then|also|plus)\s+(?:" + "|".join(sorted(_ACTION_HEADS)) + r")\b",
        msg_lower))
    if heads >= 2:
        return True

    return False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_PROJECT_MATCH_MIN_LEN = 6


def _match_existing_project(message: str) -> str:
    """If the goal text literally names an existing project directory, return
    that project name; else "".

    A dispatched goal like "deepen one edge in the polymarket-edges ledger"
    must bind to the existing `polymarket-edges` project — minting a fresh
    slug-project fences the run (and closure verification) into an empty dir
    while the real deliverable lands in the named one, producing a
    done-not-achieved false negative (first organic batch, 2026-07-03,
    run 4a5dc90c). Longest name wins; names shorter than
    _PROJECT_MATCH_MIN_LEN chars are ignored as too generic to trust.
    """
    try:
        import orch_items as _oi
        root = _oi.projects_root()
        if not root.is_dir():
            return ""
        msg = message.lower()
        best = ""
        for d in root.iterdir():
            name = d.name
            if not d.is_dir() or len(name) < _PROJECT_MATCH_MIN_LEN:
                continue
            if len(name) <= len(best):
                continue
            if re.search(r"(?<![a-z0-9-])" + re.escape(name.lower()) + r"(?![a-z0-9-])", msg):
                best = name
        return best
    except Exception:
        return ""


def _default_project_for(message: str) -> str:
    """Project identity for a project-less goal: an existing project named in
    the goal text, else the minted goal slug. Both the loop fence and the
    scope pass must resolve through here so they can't diverge."""
    matched = _match_existing_project(message)
    if matched:
        return matched
    from loop_artifacts import resolve_project_slug
    return resolve_project_slug(message)


def _run_now(
    message: str,
    handle_id: str,
    adapter,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Execute a NOW-lane task: single LLM call, returns result dict.

    Link-bearing asks get their URLs pre-fetched (reply-aware for X, via
    web_fetch) and answered from the fetched content in the same single
    call — the conversational-compute lane (Jeremy 2026-07-17: "drop a
    link somewhere and ask 'is this worth my time?'"). No URLs, or nothing
    fetchable → identical to the plain NOW call.
    """
    from llm import LLMMessage

    if verbose:
        print(f"[maro:{handle_id}] NOW lane — executing...", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    enrichment = ""
    try:
        from web_fetch import enrich_step_with_urls
        enrichment = enrich_step_with_urls(message) or ""
    except Exception:
        log.debug("NOW-lane URL enrichment failed (answering without it)",
                  exc_info=True)
    if enrichment and verbose:
        print(f"[maro:{handle_id}] NOW lane — pre-fetched {len(enrichment)} "
              f"chars of link content", file=sys.stderr, flush=True)
    try:
        resp = adapter.complete(
            [
                LLMMessage("system",
                           _NOW_SYSTEM + (_NOW_LINK_READ if enrichment else "")),
                LLMMessage("user",
                           f"{enrichment}\n\n{message}" if enrichment else message),
            ],
            max_tokens=2048,
            temperature=0.4,
            no_tools=True,
            purpose="now",
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        content = resp.content.strip()
        if not content:
            content = "[no response]"
        return {
            "status": "done",
            "result": content,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
            "elapsed_ms": elapsed,
        }
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return {
            "status": "error",
            "result": f"NOW lane error: {exc}",
            "tokens_in": 0,
            "tokens_out": 0,
            "elapsed_ms": elapsed,
        }


_NOW_VERIFY_SYSTEM = (
    "You judge whether a response fulfilled a request. Reply with JSON only: "
    '{"fulfilled": true} or {"fulfilled": false, "why": "<one short sentence>"}. '
    "On false, `why` must name the SPECIFIC missing thing (what was claimed "
    "vs what is absent) — not a restatement of the verdict. It is the only "
    "explanation a person will see for the run. Omit `why` when fulfilled. "
    "fulfilled=false when the response states the task could not be done, is "
    "incomplete or impossible, or only explains why it failed. "
    "fulfilled=false also when the response is a NON-ANSWER: it answers a "
    "different question than asked, offers generic how-to-find-it guidance "
    "instead of the asked-for answer, or lacks the specific information "
    "requested (e.g. the request asks WHERE and the response names no "
    "place, or asks WHICH and the response picks nothing). "
    "fulfilled=true when the response delivers what was asked."
)


# ---------------------------------------------------------------------------
# Provenance guard (deterministic done != achieved check) — moved to provenance.py
# ---------------------------------------------------------------------------
from provenance import (
    memory_claims_unverifiable,
    _clean_path_token,
    _claimed_output_paths,
    _claimed_output_bare,
    _claimed_input_paths,
    _output_provenance_bases,
    _exists_at_exact,
    _bare_search_dirs,
    _exists_bare_anywhere,
    _missing_claimed_outputs,
    _missing_output_bare,
    _missing_claimed_inputs,
    _run_window_start,
    _resolve_exact,
    _resolve_bare,
    _is_fresh,
    _result_claimed_outputs,
    _missing_or_stale_result_outputs,
    _provenance_missing,
    memory_provenance_unverified,
)


def _mark_memory_provenance(result_text: str) -> List[str]:
    """Mark — never demote on — unbacked "persisted it to memory" claims.

    The memory twin of the file guard above (see provenance.py's memory-claim
    banner): a run that closes a step claiming it saved a rule/convention to
    durable memory, with no matching row in any store, has fabricated the most
    expensive kind of write there is — the bill lands on a FUTURE run that
    silently never receives the knowledge. Advisory by decree (Jeremy,
    2026-08-02): the finding rides in run metadata as evidence, the verdict is
    untouched. Never raises.

    Two categories, kept separate on purpose. `memory_provenance_unverified`
    is checkable ("you claimed X; X is in no store"). `memory_claims_
    unverifiable` is not ("you claimed nothing needed saving, and named no
    subject") — that is the founding incident's exact wording, and merging it
    into the first would launder an unverifiable claim into an accusation.
    """
    findings: List[str] = []
    try:
        findings = memory_provenance_unverified(result_text)
    except Exception:
        findings = []
    if findings:
        log.warning(
            "memory provenance: claimed persisted to durable memory, no matching "
            "row in any store — %s (advisory; verdict unchanged)", findings)
        try:
            from runs import stamp_run_metadata as _srm_mem
            _srm_mem({"memory_provenance_unverified": findings})
        except Exception:
            pass
    try:
        _unverifiable = memory_claims_unverifiable(result_text)
    except Exception:
        _unverifiable = []
    if _unverifiable:
        # Not an accusation: the run may be right. Recorded so the shape stops
        # being invisible — a step declaring memory complete, being wrong, and
        # leaving no trace is how run 9c8d0a43 lost a convention silently.
        log.info(
            "memory provenance: %d unverifiable no-write-needed claim(s) "
            "(advisory, not an accusation) — %s",
            len(_unverifiable), _unverifiable)
        try:
            from runs import stamp_run_metadata as _srm_unv
            _srm_unv({"memory_claims_unverifiable": _unverifiable})
        except Exception:
            pass
    return findings


# Hard wall-clock budget for the interactive NOW judge (review F1): the
# hosted-free ladder can serially wait out multiple provider timeouts on an
# outage (20s+ each), and keep-raw-speed is the interactive contract. Normal
# hosted-free verdicts are sub-second; 5s is generous headroom, not a tunable.
_INTERACTIVE_JUDGE_BUDGET_S = 5.0


def _interactive_now_verdict_enabled() -> bool:
    """Killswitch for the interactive NOW self-verdict (chunk B, 2026-07-31;
    default ON, inert without hosted-free CONSENT — both keys and the
    validate.hosted_free.enabled egress opt-in, which defaults false: a
    credential proves authentication, not consent). Stringly 'false'
    tolerated (chunk-5a quoted-killswitch lesson)."""
    try:
        from config import get as _cfg_get
    except Exception:
        return True
    val = _cfg_get("now_lane.interactive_self_verdict", True)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "off", "no", "0", "")
    return bool(val)


def _now_verdict_rationale(raw: str, limit: int = 400) -> str:
    """The judge's prose reason, minus the JSON verdict it leads with.

    Found by run `ea4ebe4a` diagnosing `ed7cf400` (2026-08-02): the NOW judge
    replies `{"fulfilled": false}` followed by a specific, genuinely useful
    rationale — *"No Write or Bash tool calls showing the file was created…"* —
    and only the boolean was ever read. The explanation existed in
    `build/calls/` the whole time while the run presented as failed for no
    stated reason. Keeping it is half the fix; the other half is that the
    metadata writer has to accept the field at all.
    """
    text = (raw or "").strip()
    if text.startswith("```"):                      # ```json { ... } ``` preamble
        parts = text.split("```")
        text = "".join(parts[2:]).strip() if len(parts) > 2 else ""
    elif text.startswith("{"):                      # bare JSON, then prose
        depth = 0
        for i, ch in enumerate(text):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                text = text[i + 1:].strip()
                break
    text = " ".join(text.split())
    return text[:limit]


_NOW_VERIFY_CUT = 2000


def _now_verify_payload(message: str, result: str,
                        cut: int = _NOW_VERIFY_CUT) -> str:
    """Request + response for the NOW self-verdict, truncation VISIBLE.

    The last unmarked judge window in the codebase as of 2026-08-03. The
    other three were fixed the same day after the same failure repeated:
    a judge shown `Response:` cannot tell a whole answer from its first
    2000 characters, and reports what it cannot see as not delivered
    (quality gate `f4ef704`, closure work summary `0f8409f`).

    Lower harm than those — NOW is the quick-answer lane and most replies
    are far under the cut — so this marks rather than widens. If a NOW
    reply ever routinely exceeds 2000 chars, that is a signal the lane is
    being used for agenda work, and the marker will make it visible.
    """
    def _seg(label: str, text: str) -> str:
        text = text or ""
        if len(text) > cut:
            return (f"{label} [TRUNCATED — first {cut} of {len(text)} "
                    f"characters; the rest was NOT shown to you]:\n"
                    f"{text[:cut]}")
        return f"{label}:\n{text}"

    return f"{_seg('Request', message)}\n\n{_seg('Response', result)}"


def _verify_now_outcome(
    message: str, outcome: Dict[str, Any], adapter,
    wall_start: Optional[float] = None,
) -> Dict[str, Any]:
    """Demote an autonomous NOW 'done' to 'incomplete' when the response itself
    reports failure. Fails open — any error keeps the original status.

    adapter=None runs the deterministic provenance guard ONLY (free, no
    latency class change) and skips the text judge — the interactive lane
    uses this when no hosted-free judge is available."""
    # Memory-claim mark first — it is advisory, so it must be recorded even on
    # the paths below that return early on a file-provenance failure.
    _mem_unverified = _mark_memory_provenance(str(outcome.get("result", "")))
    if _mem_unverified:
        outcome = {**outcome, "memory_provenance_unverified": _mem_unverified}
    # Deterministic provenance guard, ahead of the text judge: if the goal named
    # an input that isn't on disk or an output that never landed, the goal is not
    # achieved regardless of how the response narrates it. Catches what the
    # text-only validator can't see; also saves the judge LLM call when it fires.
    _missing = _provenance_missing(
        message,
        result_text=str(outcome.get("result", "")),
        window_start=_run_window_start(
            outcome.get("elapsed_ms"), wall_start=wall_start),
    )
    if _missing:
        out = dict(outcome)
        out["status"] = "incomplete"
        out["goal_achieved"] = False
        out["provenance_missing"] = _missing
        out["goal_verdict_summary"] = (
            f"claimed input/output(s) not found: {_missing}")
        # Typed stop verdict, same as the agenda twin (handle ~2475). The
        # §13b taxonomy names the provenance guard as a lost-the-plot source
        # in stop_verdicts.py's own docstring, and the agenda guard has
        # stamped it since the verdict shipped — the NOW guard fires on
        # identical evidence and recorded prose only, so the same demotion
        # was typed on one lane and untyped on the other (found 2026-08-02).
        # Deliberately NOT extended to the judge branch below: a one-shot
        # "I couldn't" carries no map observation, and the four verdicts are
        # observations about the map.
        out["stop_verdict"] = "lost-the-plot"
        out["stop_evidence"] = (
            f"provenance: claimed input/output(s) not found: {_missing}")[:500]
        log.info(
            "provenance: claimed input/output(s) not found %s — demoted to incomplete",
            _missing,
        )
        return out
    if adapter is None:
        return outcome  # provenance guard only — no judge available
    try:
        from llm import LLMMessage
        from llm_parse import extract_json
        resp = adapter.complete(
            [
                LLMMessage("system", _NOW_VERIFY_SYSTEM),
                LLMMessage("user", _now_verify_payload(
                    message, str(outcome.get("result", "")))),
            ],
            # 64 fit `{"fulfilled": false}` and nothing else, which is why
            # the free judge could never give a reason (found live 2026-08-02,
            # run 2113a608: the propagation fix landed inert because there was
            # no rationale to propagate). A one-sentence `why` needs headroom;
            # this is still far inside the sub-second interactive budget.
            max_tokens=160,
            temperature=0.0,
            no_tools=True,
            purpose="now-verify",
        )
        verdict = extract_json(resp.content, dict, log_tag="now_verify")
        if verdict.get("fulfilled") is False:
            out = dict(outcome)
            out["status"] = "incomplete"
            out["goal_achieved"] = False
            out["tokens_in"] = outcome.get("tokens_in", 0) + getattr(resp, "input_tokens", 0)
            out["tokens_out"] = outcome.get("tokens_out", 0) + getattr(resp, "output_tokens", 0)
            out["goal_verdict_summary"] = (
                str(verdict.get("why") or "").strip()[:400]
                or _now_verdict_rationale(resp.content))
            log.info("now-verify: response reports non-fulfillment — status demoted to incomplete (%s)",
                     out["goal_verdict_summary"][:120] or "no rationale given")
            return out
        if verdict.get("fulfilled") is True:
            out = dict(outcome)
            out["goal_achieved"] = True
            return out
    except Exception as exc:
        log.debug("now-verify failed open (keeping done): %s", exc)
        # Fail-open stays fail-open (status/verdict untouched), but the
        # attempt-that-errored is marked so the ledger can distinguish a
        # broken verdict pipe from a deliberately unjudged run (review F7:
        # a keyed provider outage used to look identical to no-keys).
        out = dict(outcome)
        out["now_verify_error"] = str(exc)[:120]
        return out
    # Failed open or no clear verdict: goal achievement stays unverified
    # (no goal_achieved key) — absence means "not judged", not "failed".
    return outcome


# ---------------------------------------------------------------------------
# User config loader
# ---------------------------------------------------------------------------

def _load_user_config() -> dict:
    """Parse user/CONFIG.md into a key→value dict. Non-fatal — returns {} on any error.

    Resolves workspace-overlay-first via config.user_file() — an operator's
    ~/.maro/workspace/user/CONFIG.md wins over the shipped template.
    """
    try:
        from config import user_file
        cfg_path = user_file("CONFIG.md")
        if cfg_path is None:
            return {}
        result = {}
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.split("#")[0].strip()  # strip inline comments
            if key and val:
                result[key] = val
        return result
    except Exception:
        return {}


def _breadcrumb_verdict_stamp_failure(loop_id: str, label: str, detail: str) -> None:
    """Best-effort compatibility breadcrumb for EXT-AUDIT-2 readers."""
    try:
        from runs import stamp_run_metadata
        stamp_run_metadata({
            "goal_verdict_stamp_failed": True,
            "goal_verdict_stamp_failed_label": label,
            "goal_verdict_stamp_failed_loop_id": loop_id,
            "goal_verdict_stamp_failed_detail": str(detail)[:300],
        })
    except Exception:
        pass


def _stamp_verdict_tracked(stamp_call, *, loop_id: str, label: str,
                           unstamped_loop_ids: set) -> None:
    """Compatibility seam for the first EXT-AUDIT-2 implementation.

    New delivered paths use :mod:`audit_policy` for owner-visible warnings and
    exact repair metadata. This helper remains for callers/tests that use the
    original learning-quarantine contract directly.
    """
    if not loop_id:
        return
    try:
        result = stamp_call()
    except Exception as exc:
        log.error(
            "handle: %s verdict stamp raised for loop %s — quarantining "
            "deferred learning: %s", label, loop_id, exc,
        )
        unstamped_loop_ids.add(loop_id)
        _breadcrumb_verdict_stamp_failure(loop_id, label, str(exc))
        return
    if result is not None and result.status == "write_failed":
        detail = result.error or "outcome verdict was not updated"
        log.error(
            "handle: %s verdict stamp failed for loop %s after %d attempt(s) "
            "— quarantining deferred learning: %s",
            label, loop_id, result.attempts, detail,
        )
        unstamped_loop_ids.add(loop_id)
        _breadcrumb_verdict_stamp_failure(loop_id, label, detail)


def _stamp_stop_on_demotion(loop_result, verdict: str, evidence: str) -> None:
    """Stamp a stop verdict when the handle demotes a "done" run (§13b).

    First-write-wins extends across layers: a verdict stamped at the loop's
    own break site (e.g. landing-synthesis out-of-budget) outranks the
    handle-level demotion — the site closest to the stop evidence wins.
    Writes all three homes the loop-level rail already uses: the result
    object, run metadata.json, and the outcome-ledger row.
    """
    if getattr(loop_result, "stop_verdict", ""):
        return
    loop_result.stop_verdict = verdict
    loop_result.stop_evidence = (evidence or "")[:500]
    try:
        from runs import stamp_run_metadata
        stamp_run_metadata({
            "stop_verdict": verdict,
            "stop_evidence": loop_result.stop_evidence,
        })
    except Exception as exc:
        log.debug("stop-verdict metadata stamp failed on demotion: %s", exc)
    try:
        from memory_ledger import stamp_outcome_stop_verdict
        _lid = getattr(loop_result, "loop_id", "") or ""
        if _lid:
            stamp_outcome_stop_verdict(_lid, verdict, loop_result.stop_evidence)
    except Exception as exc:
        log.debug("stop-verdict outcome stamp failed on demotion: %s", exc)


# ---------------------------------------------------------------------------
# Core handle function
# ---------------------------------------------------------------------------

def handle(
    message: str,
    *,
    project: Optional[str] = None,
    repo_path: str = "",
    model: Optional[str] = None,
    adapter=None,
    force_lane: Optional[str] = None,   # "now" | "agenda" | None (auto)
    dry_run: bool = False,
    verbose: bool = False,
    channel: Optional["ConversationChannel"] = None,
    prior_context: Optional[str] = None,
    operator_context: Optional[str] = None,
    origin: Optional[Origin] = None,
    persona: Optional[str] = None,
    measurement_class: Optional[str] = None,
) -> HandleResult:
    """Process an incoming request through Maro's handle.

    Thin lifecycle wrapper around :func:`_handle_impl` (see its docstring for
    argument semantics). After the request completes — success or failure —
    opportunistic memory consolidation runs (knowledge_web.maybe_consolidate):
    marker-gated to at most once per interval, in-process by design (no
    cron/daemon), and never allowed to affect the request's outcome. Skipped
    on dry_run so dry runs stay side-effect free.
    """
    result: Optional[HandleResult] = None
    _backend_err = None
    try:
        from runs import current_handle_id as _pre_hid_fn
        _pre_hid = _pre_hid_fn()
    except Exception:
        _pre_hid = None
    try:
        result = _handle_impl(
            message,
            project=project,
            repo_path=repo_path,
            model=model,
            adapter=adapter,
            force_lane=force_lane,
            dry_run=dry_run,
            verbose=verbose,
            channel=channel,
            prior_context=prior_context,
            operator_context=operator_context,
            origin=origin,
            persona=persona,
            measurement_class=measurement_class,
        )
        return result
    except Exception as _handle_exc:
        # Classify backend deaths so the finalize block below can stamp the
        # actionable context into run metadata and ping the notify channel —
        # a headless user's only view of "your auth/credits died" (design §2).
        try:
            from llm_errors import BackendError, classify_error, is_actionable
            if isinstance(_handle_exc, BackendError):
                _backend_err = _handle_exc.info
            else:
                _backend_err = classify_error(_handle_exc)
                if not is_actionable(_backend_err):
                    _backend_err = None
        except Exception:
            _backend_err = None
        raise
    finally:
        # Finalize the per-run metadata for EVERY caller, not just the CLI.
        # Before 2026-06-11 only cli main() finalized, so task-path runs
        # (drain_task_store -> handle_task -> handle) were left status=None
        # -> recall read them as "unknown" -> all_failing counted a
        # *succeeding* repeat goal as failing and could trip the dispatch
        # guard on it. On an exception the run is closed as "error" via the
        # pinned run context. The CLI keeps only the context clear.
        try:
            from runs import close_run as _close_run
            from runs import current_handle_id as _current_hid
            if result is not None:
                _hid = result.handle_id
            else:
                # Exception path: only trust the pinned run context if THIS
                # call pinned it — a long-lived process (drain loop) may
                # still carry the previous task's pin if we raised before
                # open_run ran.
                _hid = _current_hid()
                if _hid == _pre_hid:
                    _hid = None
            if _hid:
                _status = result.status if result is not None else "error"
                # Shared run-dir finalization (slice log, snapshot repo, stamp
                # status + backend_error, curate run_card, re-render reports).
                # Returns the run_card, which IS the completion payload.
                _card = _close_run(_hid, status=_status, backend_error=_backend_err)
                # Actionable backend death: ping the notify channel with the
                # fix (auth/billing/context) — distinct from run_completed so
                # substrates can render it as "act now", not "run finished".
                if _backend_err is not None:
                    try:
                        from notify import emit as _notify_emit_be
                        _notify_emit_be("backend_actionable", {
                            "handle_id": _hid,
                            "status": _status,
                            "error_class": _backend_err.error_class,
                            "backend": _backend_err.backend,
                            "user_action": _backend_err.user_action,
                            "summary": _backend_err.user_action,
                            # Run identity for the relay layer — an alert
                            # without the original ask can't be tied back to
                            # the job it interrupted (azure-finch 2026-07-17).
                            "goal": str((_card or {}).get("goal", ""))[:300],
                        })
                    except Exception:
                        pass
                # Substrate notification: the run_card IS the completion payload
                # (status, done!=achieved class, result excerpt + path).
                try:
                    from notify import emit as _notify_emit
                    from runs import run_dir as _run_dir_notify
                    _notify_emit(
                        "run_completed",
                        _card or {"handle_id": _hid, "status": _status},
                        run_dir=str(_run_dir_notify(_hid)),
                    )
                except Exception:
                    pass
                # Answer-first: deferred learning runs only now, after the
                # user has heard the outcome. Lessons feed curation's
                # decision priors and classification, so refresh those card
                # fields + re-render — the same contract audit repair uses.
                if _drain_deferred_learning(_hid):
                    try:
                        from run_curation import refresh_run_card_classification
                        from loop_report import write_reports_for_run_dir
                        from runs import run_dir as _run_dir_refresh
                        refresh_run_card_classification(_hid)
                        write_reports_for_run_dir(_run_dir_refresh(_hid))
                    except Exception:
                        pass
                # Maintenance tail drains after learning (async-tail decree):
                # promotions read the freshest skill/lesson stats — this run's
                # crystallization is now one cycle earlier in their view — and
                # nothing here feeds the run card, so no refresh follows.
                try:
                    from loop_finalize import drain_deferred_maintenance
                    drain_deferred_maintenance(_hid)
                except Exception:
                    pass
        except Exception:
            pass  # finalize must never affect the request outcome
        if not dry_run:
            try:
                from knowledge_web import maybe_consolidate
                maybe_consolidate()
            except Exception:
                pass  # consolidation must never affect the request outcome


def _handle_impl(
    message: str,
    *,
    project: Optional[str] = None,
    repo_path: str = "",
    model: Optional[str] = None,
    adapter=None,
    force_lane: Optional[str] = None,   # "now" | "agenda" | None (auto)
    dry_run: bool = False,
    verbose: bool = False,
    channel: Optional["ConversationChannel"] = None,
    prior_context: Optional[str] = None,
    operator_context: Optional[str] = None,
    origin: Optional[Origin] = None,
    persona: Optional[str] = None,
    measurement_class: Optional[str] = None,
) -> HandleResult:
    """Process an incoming request through Maro's handle.

    Args:
        message: The natural language request.
        project: Project slug to attach AGENDA work to.
        repo_path: Optional path to target source repo (auto-injects stack context).
        model: LLM model override.
        adapter: Pre-built LLMAdapter (skips build_adapter).
        force_lane: Override classification ("now" or "agenda").
        dry_run: Simulate without API calls.
        verbose: Print progress.
        channel: Optional ConversationChannel for bidirectional comms (e.g. dashboard).
            When provided, the clarity check uses channel.ask() to gather missing info
            (rather than returning clarification_needed), and step events are emitted.
        origin: Ancestry of this request when it was spawned by prior work
            (parent_handle_id, parent_loop_id, parent_goal, source, job_id).
            Stamped into the run-dir metadata so every run is traceable to the
            thread it serves. None for direct user input.
        persona: Explicit forced-persona name (CLI --persona / programmatic
            callers). Same effect as a `persona:<name>:` prefix in the message
            text, but takes precedence over it when both are given (an explicit
            argument beats freeform text — same precedence `model=` already has
            over `effort:` prefixes). Unknown names degrade to normal
            persona_for_goal() auto-selection; see _resolve_forced_persona().
        measurement_class: Prospective cohort provenance for success-rate
            measurement. Inherits an origin label when present; otherwise
            normal work defaults organic. Synthetic callers must explicitly
            choose smoke, control, or benchmark.

    Returns:
        HandleResult with routing info and substantive result.
    """
    from intent import classify
    from llm import build_adapter
    from agent_loop import run_agent_loop, _DryRunAdapter

    handle_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()
    # Wall anchor for the provenance freshness window: monotonic can't feed
    # mtime comparisons, and now-minus-elapsed drifts (see _run_window_start).
    wall_started_at = time.time()

    from ancestry import normalize_measurement_class
    measurement_class = normalize_measurement_class(
        measurement_class or (origin or {}).get("measurement_class")
    )

    # Revive the run viewer if configured (viz.autostart, default off) — a
    # goal run is the natural "someone will want to look" moment, and this
    # survives reboots without a system agent. Best-effort, never blocks.
    if not dry_run:
        from viz_server import ensure_running as _viz_ensure_running
        _viz_ensure_running()

    if verbose:
        print(f"[maro:{handle_id}] handle: {message!r}", file=sys.stderr, flush=True)

    # Persist raw input before any prefix stripping — visibility hole fix.
    # Writes to memory/handle_inputs.jsonl so every goal + its prefixes are
    # recoverable. Locked append since 2026-08-10: rerun_identity reads this
    # as a correctness source, and bare concurrent appends can interleave
    # (file_lock module doc). dry_run is stamped so the re-run detector can
    # drop previews without paying a metadata read.
    _raw_input = message
    try:
        from orch_items import memory_dir as _mem_dir
        from file_lock import locked_append as _locked_append
        _input_rec = {
            "handle_id": handle_id,
            "raw_input": _raw_input,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if dry_run:
            _input_rec["dry_run"] = True
        if origin:
            _input_rec["origin"] = origin
        _locked_append(_mem_dir() / "handle_inputs.jsonl",
                       json.dumps(_input_rec))
    except Exception:
        pass  # never block on logging

    # Per-run isolation: create the run-dir at start and pin it as the
    # current-run context so artifact writers downstream land directly
    # in `~/.maro/workspace/runs/<id>-<nick>/` rather than scattered
    # across project_workspace/. `runs.open_run` is the shared "own a run"
    # sequence — the `maro run`/`maro resume` CLI lane calls the same helper
    # (BACKLOG #18). See src/runs.py. Never block the run on a runs/ failure.
    try:
        from runs import open_run as _open_run
        _open_run(
            handle_id,
            prompt=_raw_input,
            model=model,
            repo_path=repo_path,
            origin=origin,
            measurement_class=measurement_class,
            dry_run=dry_run,
        )
    except Exception as _run_dir_exc:
        log.debug("runs: open_run failed: %s", _run_dir_exc)

    # Apply magic prefix registry — strips all recognized prefixes in one pass.
    # Runs BEFORE the user/CONFIG.md default_model_tier fallback below: a
    # prefix is an explicit, per-request override (the whole point of typing
    # `effort:high` or `garrytan:` is to deviate from whatever's configured),
    # so it must outrank a passive config default, not lose to it. The
    # previous order (config default applied first, prefix only applied "if
    # model is None") meant a configured default_model_tier silently defeated
    # every prefix's tier bump (adversarial-review finding, 2026-07-13; the
    # template stopped shipping a default_model_tier value 2026-07-21 —
    # execution floor is the MID role default unless the operator opts in).
    _pfx = _apply_prefixes(message)
    message = _pfx.message
    # Explicit persona= wins over a prefix-forced persona (full precedence
    # logic + registry validation happens later where PersonaRegistry is in
    # scope) — but the model_tier floor below is resolved now, well before
    # that. Without this check, overriding e.g. `garrytan:` via an explicit
    # persona= kwarg would swap out the persona but silently keep the
    # power-tier bump garrytan: bundled with it (adversarial-review finding,
    # 2026-07-13). Mirror the precedence check early so the tier follows the
    # persona that actually wins.
    _persona_overridden_early = bool(
        persona and persona.strip() and _pfx.forced_persona
        and persona.strip().lower() != _pfx.forced_persona
    )
    if _pfx.model_tier and model is None and not (
        _persona_overridden_early and _pfx.model_tier == _pfx.persona_bundled_tier
    ):
        model = _pfx.model_tier

    # Apply user/CONFIG.md defaults (non-fatal — bad config never blocks a run).
    # Fallback only: a prefix (above) or an explicit model= kwarg always wins.
    _cfg = _load_user_config()
    if model is None:
        _tier = _cfg.get("default_model_tier", "").strip().lower()
        if _tier in ("cheap", "mid", "power"):
            model = _tier

    # Unpack prefix flags into local names for backward compatibility
    # with the rest of this function (no other code changes needed below).
    _use_thin_mode = _pfx.thin_mode
    _btw_mode = _pfx.btw_mode
    _ultraplan_max_steps = _pfx.max_steps if _pfx.max_steps else None
    _direct_mode = _pfx.direct_mode
    _ralph_prefix = _pfx.ralph_mode
    _pipeline_prefix = _pfx.pipeline_mode
    _strict_prefix = _pfx.strict_mode
    _team_prefix = _pfx.team_mode

    # Build adapter — execution floor is MID by role (2026-07-20 decree:
    # the cheap-vs-mid execution split was a non-decision under flat-rate).
    # A caller-supplied adapter is the injection seam (tests, scripted
    # flows): every LLM call in this handle rides it, classification
    # included.
    _adapter_injected = adapter is not None
    if adapter is None and not dry_run:
        from conductor import assign_model_by_role
        adapter = build_adapter(model=model or assign_model_by_role("worker"))
    elif dry_run:
        adapter = _DryRunAdapter()

    # Classify intent
    introspects_self = False
    if force_lane:
        # Known gap (adversarial-review 2026-07-18, accepted): a forced lane
        # skips classification, so `--lane agenda "diagnose your last run"`
        # runs blind-isolated — the introspection grant requires the
        # classifier. Acceptable: force_lane exists to skip that LLM call,
        # and the operator forcing a lane can also flip the config gate.
        lane = force_lane
        confidence = 1.0
        reason = f"forced to {force_lane}"
    else:
        # Classification is a classifier-role call (CHEAP by decree) — don't
        # reuse the MID worker adapter for it. Chunk-1 adversarial review
        # (Architect finding 1): the worker default leaked backward into the
        # classifier when the shared adapter went MID. Only applies when we
        # built the adapter ourselves — an injected adapter stays the seam.
        _classify_adapter = None
        if not dry_run:
            if _adapter_injected:
                _classify_adapter = adapter
            else:
                try:
                    from conductor import assign_model_by_role as _ambr
                    _classify_adapter = build_adapter(model=_ambr("classifier"))
                except Exception:
                    _classify_adapter = adapter  # fall back to the run adapter
        _cls = classify(message, adapter=_classify_adapter, dry_run=dry_run)
        lane, confidence, reason, introspects_self = (
            _cls.lane, _cls.confidence, _cls.reason, _cls.introspects_self)

    if verbose:
        print(f"[maro:{handle_id}] classified lane={lane} confidence={confidence:.2f}: {reason}", file=sys.stderr, flush=True)

    # direct: forces AGENDA lane regardless of classifier — the whole point is to bypass
    # Director overhead (which only applies to AGENDA) and go straight to run_agent_loop.
    if _direct_mode:
        lane = "agenda"

    # Refresh run-dir metadata.json now that lane is known. Fills in
    # the lane/model fields that were null at create_run_dir time
    # (which had to run before classification to record offsets early).
    try:
        from runs import write_metadata as _write_meta
        from runs import current_run_dir as _crd
        _rd = _crd()
        if _rd is not None:
            _write_meta(
                _rd, handle_id=handle_id, prompt=_raw_input,
                lane=lane, model=model,
            )
    except Exception:
        pass

    # btw mode: quick observation, always routes to NOW regardless of classification.
    # The result is prefixed with "[Observation]" to distinguish from work products.
    if _btw_mode:
        from llm import LLMMessage
        try:
            _btw_resp = adapter.complete(
                [LLMMessage("system", _BTW_SYSTEM), LLMMessage("user", message)],
                max_tokens=256,
                temperature=0.3,
                no_tools=True,
                purpose="btw observation",
            )
            _btw_content = _btw_resp.content.strip() or "[no observation]"
        except Exception as _btw_exc:
            _btw_content = f"[observation error: {_btw_exc}]"
            _btw_resp = type("R", (), {"input_tokens": 0, "output_tokens": 0})()
        elapsed = int((time.monotonic() - started_at) * 1000)
        return HandleResult(
            handle_id=handle_id,
            lane="now",
            lane_confidence=1.0,
            classification_reason="btw: non-blocking observation",
            message=message,
            status="done",
            result=f"[Observation] {_btw_content}",
            tokens_in=getattr(_btw_resp, "input_tokens", 0),
            tokens_out=getattr(_btw_resp, "output_tokens", 0),
            elapsed_ms=elapsed,
        )

    # Route to lane
    # NOW→AGENDA verdict escalation stash (BACKLOG 22 follow-up): when the
    # NOW self-verdict says not-achieved, the escalated agenda run receives
    # the failed quick answer as ancestry context via this variable.
    _now_escalation_context = ""
    if lane == "now":
        # Escalation: if the message looks like a complex directive, reclassify
        # to agenda so the Director can plan it. Default ON since 2026-06-11 —
        # live runs showed execution-shaped goals ("run X and save the output")
        # landing in NOW, where a single completion can't do the work but the
        # run is still recorded done. Disable via now_lane.escalate_to_director.
        _now_escalate_enabled = True
        try:
            from config import get as _cfg_get
            _now_escalate_enabled = bool(_cfg_get("now_lane.escalate_to_director", True))
        except Exception:
            pass
        # An explicit force_lane="now" wins over escalation — the caller
        # chose the lane; escalation protects *classified* routing only.
        if _now_escalate_enabled and not force_lane and _is_complex_directive(message):
            lane = "agenda"
            reason = reason + " [now→agenda: complex directive escalated to Director]"
            log.info("handle: now→agenda escalation for: %s", message[:80])
            # Keep run metadata honest about the lane that actually executes —
            # it was written at classify time, before this flip.
            try:
                from runs import write_metadata as _write_meta_esc
                from runs import current_run_dir as _crd_esc
                _rd_esc = _crd_esc()
                if _rd_esc is not None:
                    # now_escalated mirrors now_verdict_escalated below: the
                    # 2026-07-28 recon greped 726 runs for firings and found
                    # zero, but nothing durable was ever written — this stamp
                    # makes absence-of-records mean absence-of-firings.
                    _write_meta_esc(
                        _rd_esc, handle_id=handle_id, prompt=_raw_input,
                        lane="agenda", model=model,
                        extra={"now_escalated": True},
                    )
            except Exception:
                pass
            # Fall through to the agenda branch below

    if lane == "now":
        outcome = _run_now(message, handle_id, adapter, verbose=verbose)

        # Status honesty for autonomous callers: NOW "done" means the
        # completion call returned, not that the goal was achieved — a
        # response honestly stating "this cannot be done" was recorded done
        # (live find 2026-06-11), which poisons recall, the dispatch guard,
        # and the navigator downstream. Task-path runs (origin present — no
        # human reading the text) get a cheap self-verdict and demote to
        # "incomplete" when the response reports non-fulfillment.
        # Interactive calls keep raw speed.
        if origin is not None and not dry_run and outcome.get("status") == "done":
            outcome = _verify_now_outcome(
                message, outcome, adapter, wall_start=wall_started_at)
        elif (origin is None and not dry_run
                and outcome.get("status") == "done"
                and _interactive_now_verdict_enabled()):
            # Interactive half of the NOW verdict pipe (chunk B, 2026-07-31 —
            # panel round 3: "close the two verdict-blind lanes"). The
            # keep-raw-speed decision stands for the PAID adapter: the text
            # judge rides hosted-free only (sub-second, $0, consent-gated —
            # build returns None without keys AND the explicit
            # validate.hosted_free.enabled egress opt-in), and without it
            # only the free deterministic provenance guard runs. Different
            # judge family than the player is a feature, not a compromise
            # (correlated-errors finding, same panel).
            _hf_adapter = None
            try:
                from hosted_free import build_hosted_free_adapter
                _hf_adapter = build_hosted_free_adapter()
            except Exception:
                _hf_adapter = None
            if _hf_adapter is None:
                # No consent/keys: deterministic provenance guard only.
                outcome = _verify_now_outcome(
                    message, outcome, None, wall_start=wall_started_at)
            else:
                # The judge runs on a daemon thread with a hard wall-clock
                # budget (review F1): a hosted-free provider outage must not
                # stall the interactive answer for the ladder's serial
                # timeouts. The thread gets a snapshot copy of the outcome
                # (no shared-dict race if abandoned); a daemon thread never
                # blocks process exit for a one-shot CLI call.
                import threading as _threading
                _judge_out: list = []

                def _run_judge(_snap=dict(outcome)):
                    try:
                        _judge_out.append(_verify_now_outcome(
                            message, _snap, _hf_adapter,
                            wall_start=wall_started_at))
                    except Exception:
                        pass  # _verify_now_outcome fails open; belt-and-braces

                _judge_th = _threading.Thread(target=_run_judge, daemon=True)
                _judge_th.start()
                _judge_th.join(_INTERACTIVE_JUDGE_BUDGET_S)
                if _judge_out:
                    outcome = _judge_out[0]
                    if ("goal_achieved" in outcome
                            and not outcome.get("provenance_missing")):
                        outcome["now_verify_family"] = "hosted_free"
                else:
                    # Budget breached: answer returns un-judged (raw speed
                    # kept), marked as an attempted-but-failed verdict so
                    # the ledger can see the broken pipe (review F7). The
                    # free deterministic provenance guard still runs.
                    outcome = dict(_verify_now_outcome(
                        message, outcome, None, wall_start=wall_started_at))
                    outcome["now_verify_error"] = "judge_timeout"
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Goal verdict as its own metadata dimension (done != successful):
        # process status says the lane finished; goal_achieved says the
        # request was actually fulfilled. Absent key = unverified.
        if not dry_run and "goal_achieved" in outcome:
            try:
                from runs import write_metadata as _wm_now
                from runs import current_run_dir as _crd_now
                _rd_now = _crd_now()
                if _rd_now is not None:
                    _now_extra = {
                        "goal_achieved": bool(outcome["goal_achieved"]),
                        "goal_verdict_source": (
                            "provenance" if outcome.get("provenance_missing")
                            else ("now_self_verdict_free"
                                  if outcome.get("now_verify_family") == "hosted_free"
                                  else "now_self_verdict")),
                    }
                    # WHY the verdict landed, not just that it did. This dict
                    # carried exactly two keys until 2026-08-02, which made it
                    # structurally impossible for a NOW run to record a reason
                    # — ed7cf400 presented as "incomplete, no explanation"
                    # while its judge's rationale sat in build/calls/ the whole
                    # time. Found by ea4ebe4a (LT-1 #8) reading live source.
                    _now_summary = str(outcome.get("goal_verdict_summary") or "")
                    if _now_summary:
                        _now_extra["goal_verdict_summary"] = _now_summary
                    # Typed stop verdict rides the same write. classify_outcome
                    # reads meta["stop_verdict"] straight onto the run card, so
                    # this is the NOW lane's equivalent of the agenda rail's
                    # stamp_run_metadata leg.
                    _now_stop = str(outcome.get("stop_verdict") or "")
                    if _now_stop:
                        _now_extra["stop_verdict"] = _now_stop
                        _now_extra["stop_evidence"] = str(
                            outcome.get("stop_evidence") or "")[:500]
                    _wm_now(
                        _rd_now, handle_id=handle_id, prompt=_raw_input,
                        extra=_now_extra,
                    )
            except Exception:
                pass

        # Write artifact
        artifact_path = _write_now_artifact(handle_id, message, outcome.get("result", ""), elapsed)

        # Slim outcome record — NOW runs feed attempt history and outcome
        # stats but skip LLM lesson extraction (a quick-answer lane must not
        # pay a reflection model call per request; lessons stay agenda-only).
        if not dry_run:
            try:
                from memory import record_outcome as _record_outcome
                # Verdict tri-state (SF-2): the NOW self-verdict (and the
                # provenance guard inside it) runs BEFORE this record, so the
                # outcome row carries the verdict directly. No verdict key on
                # the outcome dict = unjudged = absent on the row.
                _now_judged = "goal_achieved" in outcome
                _record_outcome(
                    goal=message,
                    status=outcome["status"],
                    summary=str(outcome.get("result", ""))[:500],
                    task_type="now",
                    tokens_in=outcome["tokens_in"],
                    tokens_out=outcome["tokens_out"],
                    elapsed_ms=elapsed,
                    model=model or "",
                    goal_achieved=(bool(outcome["goal_achieved"]) if _now_judged else None),
                    goal_verdict_source=(
                        ("provenance" if outcome.get("provenance_missing")
                         else ("now_self_verdict_free"
                               if outcome.get("now_verify_family") == "hosted_free"
                               else "now_self_verdict"))
                        if _now_judged
                        # Attempted-but-failed verdict (judge error/timeout):
                        # a stamp with no goal_achieved — visible in the
                        # ledger as a broken pipe, never as "unjudged by
                        # design" (review F7).
                        else ("now_self_verdict_error"
                              if outcome.get("now_verify_error") else "")
                    ),
                    measurement_class=measurement_class,
                    handle_id=handle_id,
                    # Third home for the typed verdict, matching the agenda
                    # rail's three (result object / run metadata / outcome
                    # row). record_outcome has accepted these since the
                    # verdict shipped; the NOW call site never passed them.
                    stop_verdict=str(outcome.get("stop_verdict") or ""),
                    stop_evidence=str(outcome.get("stop_evidence") or "")[:500],
                )
            except Exception:
                pass  # outcome recording must never block the NOW response

        # NOW retry rung, shallow half (BACKLOG "NOW retry rung" 2026-07-28;
        # trio-triage PARTIAL 2026-07-29). A self-verdict failure gets ONE
        # artifact-seeded second shot in the same lane before any agenda
        # escalation: the ask rides again with the failed answer + demotion
        # reason attached. Shallow failures only — a complex directive is a
        # structural failure (a single completion can't do the work) and
        # skips straight to the escalation below. Default OFF on fresh
        # installs (an extra LLM call on the strength of one cheap verdict —
        # same no-silent-spend posture as escalate_on_not_achieved); ON in
        # this box's workspace config. Attempt 1 above stays fully recorded
        # (artifact + outcome row); the retry writes its own alongside.
        _rung_fired = False
        _rung_retry_judged = False
        if (outcome.get("goal_achieved") is False and not force_lane
                and not dry_run):
            _rung_enabled = False
            try:
                from config import get as _rr_cfg_get
                _rung_enabled = bool(
                    _rr_cfg_get("now_lane.artifact_retry", False))
            except Exception:
                _rung_enabled = False
            if _rung_enabled and not _is_complex_directive(message):
                _rung_fired = True
                _fail_reason = (
                    "it claimed inputs/outputs that are not on disk: "
                    f"{outcome.get('provenance_missing')}"
                    if outcome.get("provenance_missing")
                    else "it did not deliver what the request asked for")
                _seeded = (
                    f"{message}\n\n"
                    "== Prior attempt (judged insufficient) ==\n"
                    "A first quick answer was judged NOT to fulfill this "
                    f"request — {_fail_reason}. Do not repeat its approach; "
                    "fix what it missed and answer the request itself. The "
                    "insufficient answer was:\n"
                    + str(outcome.get("result", ""))[:1500])
                log.info("handle: NOW artifact-retry rung firing for: %s",
                         message[:80])
                _retry = _run_now(_seeded, handle_id, adapter, verbose=verbose)
                if _retry.get("status") == "done":
                    # Judge against the ORIGINAL ask — the seeded wrapper is
                    # scaffolding, not the request.
                    _retry = _verify_now_outcome(
                        message, _retry, adapter, wall_start=wall_started_at)
                _recovered = _retry.get("goal_achieved") is True
                try:
                    from captains_log import log_event as _rr_log_event
                    from captains_log import NOW_ARTIFACT_RETRY as _RR_EVT
                    _ctx = {"goal_preview": message[:120],
                            "recovered": _recovered}
                    if outcome.get("provenance_missing"):
                        _ctx["provenance_missing"] = \
                            outcome["provenance_missing"]
                    _rr_log_event(
                        _RR_EVT, subject=handle_id,
                        summary=("artifact-seeded NOW retry "
                                 + ("recovered" if _recovered
                                    else "did not recover")),
                        context=_ctx, loop_id=handle_id)
                except Exception:
                    pass
                # A retry that errored out must not replace a real (if
                # insufficient) answer; anything else becomes the delivered
                # outcome — even unrecovered, it is the later, seeded attempt.
                # An errored retry was never JUDGED — the escalation context
                # below must not claim two judged failures (2026-07-29
                # adversarial review, 3/3 lens consensus).
                if _retry.get("status") != "error":
                    outcome = _retry
                    _rung_retry_judged = "goal_achieved" in _retry
                elapsed = int((time.monotonic() - started_at) * 1000)
                _write_now_artifact(
                    handle_id, message, _retry.get("result", ""),
                    _retry.get("elapsed_ms", 0), suffix="-retry")
                try:
                    from runs import write_metadata as _wm_rr
                    from runs import current_run_dir as _crd_rr
                    _rd_rr = _crd_rr()
                    if _rd_rr is not None:
                        _extra_rr = {"now_artifact_retry": (
                            "recovered" if _recovered else "unrecovered")}
                        if "goal_achieved" in outcome:
                            _extra_rr["goal_achieved"] = \
                                bool(outcome["goal_achieved"])
                            _extra_rr["goal_verdict_source"] = \
                                "now_self_verdict"
                        _wm_rr(_rd_rr, handle_id=handle_id,
                               prompt=_raw_input, extra=_extra_rr)
                except Exception:
                    pass
                if not dry_run:
                    try:
                        from memory import record_outcome as _record_retry
                        _r_judged = "goal_achieved" in _retry
                        _record_retry(
                            goal=message,
                            status=_retry["status"],
                            summary=str(_retry.get("result", ""))[:500],
                            task_type="now",
                            tokens_in=_retry.get("tokens_in", 0),
                            tokens_out=_retry.get("tokens_out", 0),
                            elapsed_ms=_retry.get("elapsed_ms", 0),
                            model=model or "",
                            goal_achieved=(bool(_retry["goal_achieved"])
                                           if _r_judged else None),
                            goal_verdict_source=(
                                ("provenance"
                                 if _retry.get("provenance_missing")
                                 else "now_self_verdict")
                                if _r_judged else ""),
                            measurement_class=measurement_class,
                            handle_id=handle_id,
                        )
                    except Exception:
                        pass

        # NOW→AGENDA verdict escalation (BACKLOG 22 follow-up, Jeremy
        # 2026-07-11 "we should just do this"): a NOW answer the self-verdict
        # judged not-achieved becomes a regular AGENDA run with the failed
        # quick answer attached as context, instead of returning a recorded
        # failure to a caller nobody is reading. The NOW attempt above stays
        # fully recorded (artifact + outcome row, status incomplete) — the
        # escalated run is honest about being a second attempt.
        # Scope: task-path only by construction (the self-verdict only runs
        # when origin is set); an explicit force_lane="now" wins, matching
        # the complex-directive escalation above. Default OFF on fresh
        # installs — this turns a quick answer into a full orchestrated run
        # (real spend) on the strength of one cheap verdict; no silent LLM
        # spend without an operator decision (same posture as
        # scope_generation). ON on this box.
        _verdict_escalate = False
        if outcome.get("goal_achieved") is False and not force_lane:
            try:
                from config import get as _ve_cfg_get
                _verdict_escalate = bool(
                    _ve_cfg_get("now_lane.escalate_on_not_achieved", False)
                )
            except Exception:
                _verdict_escalate = False
        if _verdict_escalate:
            lane = "agenda"
            reason = reason + " [now→agenda: self-verdict not-achieved, escalated with NOW context]"
            _now_escalation_context = (
                ("TWO quick single-shot (NOW lane) attempts at this request "
                 "were already made — the second seeded with the first's "
                 "failure — and both were judged NOT to have fulfilled it"
                 if _rung_retry_judged else
                 "A quick single-shot (NOW lane) attempt at this request was "
                 "already made and judged NOT to have fulfilled it"
                 + (" (an artifact-seeded retry was attempted but errored "
                    "out before it could be judged)" if _rung_fired else ""))
                + (" (claimed inputs/outputs missing on disk: "
                   f"{outcome.get('provenance_missing')})"
                   if outcome.get("provenance_missing") else "")
                + ". Do not repeat its approach; do the actual work the "
                "request asks for. The insufficient answer was:\n"
                + str(outcome.get("result", ""))[:1500]
            )
            log.info("handle: now→agenda verdict escalation for: %s", message[:80])
            try:
                from runs import write_metadata as _write_meta_ve
                from runs import current_run_dir as _crd_ve
                _rd_ve = _crd_ve()
                if _rd_ve is not None:
                    _write_meta_ve(
                        _rd_ve, handle_id=handle_id, prompt=_raw_input,
                        lane="agenda", model=model,
                        extra={"now_verdict_escalated": True},
                    )
            except Exception:
                pass
            # Fall through to the agenda branch below.
        else:
            return HandleResult(
                handle_id=handle_id,
                lane="now",
                lane_confidence=confidence,
                classification_reason=reason,
                message=message,
                status=outcome["status"],
                result=outcome["result"],
                tokens_in=outcome["tokens_in"],
                tokens_out=outcome["tokens_out"],
                elapsed_ms=elapsed,
                artifact_path=artifact_path,
            )

    if lane == "agenda":  # plain classification, or escalated from NOW above
        # Only route through the Conductor for meta-commands (status, inspect, goal-map).
        # For actual mission goals, always go direct to run_agent_loop to avoid stale
        # mission data being returned instead of a fresh run.
        _is_meta_command = False
        try:
            from conductor import _looks_like_status, _looks_like_inspect, _looks_like_goal_map
            _is_meta_command = (
                _looks_like_status(message)
                or _looks_like_inspect(message)
                or _looks_like_goal_map(message)
            )
        except ImportError:
            pass

        if not dry_run and not project and _is_meta_command:
            try:
                from conductor import conduct
                from agent_loop import _goal_to_slug
                conductor_response = conduct(
                    message,
                    adapter=adapter,
                    model=model,
                    dry_run=False,
                )
                elapsed = int((time.monotonic() - started_at) * 1000)
                conductor_project = _goal_to_slug(message)
                return HandleResult(
                    handle_id=handle_id,
                    lane="agenda",
                    lane_confidence=confidence,
                    classification_reason=reason + " [routed via Conductor]",
                    message=message,
                    status="done",
                    result=conductor_response.message,
                    project=conductor_project,
                    elapsed_ms=elapsed,
                    artifact_path=None,
                )
            except (ImportError, Exception):
                pass  # fall through to direct agenda handling
        # Clarification milestone — check goal clarity before starting (skipped if yolo=true).
        # Runs on the goal AS SUBMITTED, before the BLE rewrite: clarity judges
        # the user's goal, not the system's transform of it. When this ran
        # after the rewrite, a lossy rewrite (URL dropped) became a
        # clarification question back at the user for information they had
        # already provided (2026-07-16, cobalt-pine / hermes dispatch).
        _yolo = _env_flag(
            "MARO_YOLO",
            str(_cfg.get("yolo", "false")).strip().lower() == "true",
        )
        if not dry_run and not _yolo:
            try:
                from intent import check_goal_clarity
                _clarity = check_goal_clarity(message, adapter=adapter)
                if not _clarity.get("clear"):
                    _q = _clarity.get("question", "Could you clarify the goal?")
                    if verbose:
                        print(f"[maro:{handle_id}] clarity check: UNCLEAR — {_q}", file=sys.stderr, flush=True)
                    if channel is not None:
                        # Ask via channel and wait for reply — then continue with enriched goal
                        _reply = channel.ask(_q)
                        if _reply:
                            message = f"{message}\n\nAdditional context: {_reply}"
                        # Fall through to continue execution
                    else:
                        # No channel — return clarification_needed (CLI path).
                        # Stamp the question into run metadata: the HandleResult
                        # is ephemeral on queue/dispatch paths, and a
                        # clarification_needed record without its question is
                        # undiagnosable from the other side of the wire.
                        try:
                            from runs import stamp_run_metadata as _stamp_q
                            from stop_verdicts import PAUSE_OP_CLARIFICATION
                            _stamp_q({
                                "clarification_question": _q,
                                "pause_reason": PAUSE_OP_CLARIFICATION,
                            })
                        except Exception:
                            pass
                        elapsed = int((time.monotonic() - started_at) * 1000)
                        return HandleResult(
                            handle_id=handle_id,
                            lane="agenda",
                            lane_confidence=confidence,
                            classification_reason=reason + " [clarity check: ambiguous]",
                            message=message,
                            status="clarification_needed",
                            result=(
                                f"Before starting, I need to clarify one thing:\n\n"
                                f"{_q}\n\n"
                                f"*(Add `yolo: true` to user/CONFIG.md to skip this check.)*"
                            ),
                            elapsed_ms=elapsed,
                        )
            except Exception:
                pass  # clarity check must never block execution

        # BLE rewriter — strip prescribed execution steps, keep outcome intent (non-blocking)
        # Bitter Lesson Engineering: embed the "what", let the AI own the "how".
        if not dry_run:
            try:
                from intent import rewrite_imperative_goal
                _rewritten = rewrite_imperative_goal(message, adapter=adapter)
                if _rewritten != message:
                    if verbose:
                        print(f"[maro:{handle_id}] BLE rewrite: imperative goal → outcome goal", file=sys.stderr, flush=True)
                    message = _rewritten
            except Exception:
                pass  # rewrite failures must never block a run

        if verbose:
            print(f"[maro:{handle_id}] AGENDA lane — starting loop...", file=sys.stderr, flush=True)

        # mode:thin — use factory_thin loop (stripped scaffolding) instead of
        # full Mode 2. Kept as an operator-only escape hatch + benchmark
        # instrument per the 2026-07-21 factory adjudication ("mixed bag");
        # default tier follows the MID execution-floor decree.
        if _use_thin_mode and not dry_run:
            try:
                from factory_thin import run_factory_thin
                from conductor import assign_model_by_role
                _thin_result = run_factory_thin(
                    message,
                    model=model or assign_model_by_role("worker"),
                    verbose=verbose,
                )
                elapsed = int((time.monotonic() - started_at) * 1000)
                _thin_text = _thin_result.final_report or "[no output produced]"
                if _thin_result.status != "done":
                    _thin_text += f"\n\n⚠️ Thin loop status: {_thin_result.status}"
                return HandleResult(
                    handle_id=handle_id,
                    lane="agenda",
                    lane_confidence=confidence,
                    classification_reason=reason + " [mode:thin]",
                    message=message,
                    status=_thin_result.status,
                    result=_thin_text,
                    project=project or "",
                    tokens_in=_thin_result.total_tokens // 2,
                    tokens_out=_thin_result.total_tokens // 2,
                    elapsed_ms=elapsed,
                )
            except Exception as _thin_exc:
                log.warning("mode:thin failed, falling back to Mode 2: %s", _thin_exc)
                # Fall through to run_agent_loop below

        # Resolve persistent identity once for every full AGENDA shape.  It is
        # both the loop fence and the deterministic goal-family key used by
        # recall: a semantic rephrase explicitly routed to the same project
        # must inherit prior decisions/artifact paths without an embedding or
        # another LLM call.  Stamp it before recall so the next run can join
        # this one even though metadata was opened before lane classification.
        _agenda_project = project or _default_project_for(message)
        try:
            from runs import stamp_run_metadata as _stamp_project_metadata
            _stamp_project_metadata({"project": _agenda_project})
        except Exception:
            pass

        # pipeline: prefix — user specifies explicit steps as "step1 | step2 | step3".
        # Bypasses LLM decomposition entirely; runs the given steps in order.
        if _pipeline_prefix:
            _pipe_raw = _pfx.message
            _pipe_steps = [s.strip() for s in _pipe_raw.split("|") if s.strip()]
            if not _pipe_steps:
                _pipe_steps = [s.strip() for s in _pipe_raw.splitlines() if s.strip()]
            if _pipe_steps:
                if verbose:
                    print(f"[maro] pipeline: {len(_pipe_steps)} steps: {_pipe_steps}", file=sys.stderr, flush=True)
                _pipe_result = run_agent_loop(
                    _pipe_raw,
                    project=_agenda_project,
                    model=model,
                    adapter=adapter,
                    dry_run=dry_run,
                    verbose=verbose,
                    preset_steps=_pipe_steps,
                    measurement_class=measurement_class,
                    handle_id=handle_id,
                    introspection_access=introspects_self,
                    # Async-tail: this lane returns through handle()'s
                    # finalize, which drains post-notify — same contract as
                    # the agenda lane (review of 707a541: these lanes were
                    # missing the win). defer_learning stays off: no closure
                    # runs here, so learning must extract at finalize.
                    defer_maintenance=True,
                )
                return _loop_result_to_handle(
                    _pipe_result, handle_id=handle_id, message=message,
                    confidence=confidence, reason=reason, started_at=started_at,
                    project=project, reason_suffix=" [pipeline]",
                )

        # team: prefix — decompose into DAG and execute with dep-aware parallel pool.
        # Uses parallel_fan_out=4 so _run_steps_dag fires when [after:N] parallelism is found.
        if _team_prefix:
            if verbose:
                print("[maro] team: dag execution mode (parallel_fan_out=4)", file=sys.stderr, flush=True)
            _team_result = run_agent_loop(
                _pfx.message,
                project=_agenda_project,
                model=model,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
                parallel_fan_out=4,
                measurement_class=measurement_class,
                handle_id=handle_id,
                introspection_access=introspects_self,
                defer_maintenance=True,  # drains in handle()'s finalize
            )
            return _loop_result_to_handle(
                _team_result, handle_id=handle_id, message=message,
                confidence=confidence, reason=reason, started_at=started_at,
                project=project, reason_suffix=" [team]",
            )

        # direct: prefix — skip quality gate and escalation, route straight to run_agent_loop.
        # Bitter Lesson experiment: for simple goals, scaffolding overhead doesn't improve output.
        if _direct_mode:
            _direct_result = run_agent_loop(
                message,
                project=_agenda_project,
                model=model,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
                measurement_class=measurement_class,
                handle_id=handle_id,
                introspection_access=introspects_self,
                defer_maintenance=True,  # drains in handle()'s finalize
            )
            return _loop_result_to_handle(
                _direct_result, handle_id=handle_id, message=message,
                confidence=confidence, reason=reason, started_at=started_at,
                project=project, reason_suffix=" [direct]",
            )

        _ralph_from_cfg = _cfg.get("ralph_verify", "").strip().lower() == "true"
        # Dispatched goals arrive project-less; default the loop's project
        # identity via _default_project_for — an existing project named in the
        # goal, else the minted goal slug (same derivation the scope pass uses
        # below) — so the cwd fence, per-step cwd binds, and prompt project_dir
        # all engage instead of silently running unfenced from the launch cwd
        # (BACKLOG #1, 3rd repro), and scope + execution stop pointing at two
        # different project dirs. `project` itself stays as-given: routing
        # checks above and HandleResult report what the caller asked for.
        _loop_kwargs: dict = dict(
            project=_agenda_project,
            repo_path=repo_path,
            model=model,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            ralph_verify=_ralph_from_cfg or _ralph_prefix,
            measurement_class=measurement_class,
            handle_id=handle_id,
            introspection_access=introspects_self,
        )
        if _ultraplan_max_steps is not None:
            _loop_kwargs["max_steps"] = _ultraplan_max_steps

        # Ancestry write-side (BACKLOG ancestry unification): a dispatched
        # fork records its lineage in the child project's ancestry.json —
        # the same chain build_ancestry_prompt injects and recall falls back
        # to — so origin-walk and ancestry.json stop being two disagreeing
        # sources. First fork wins; parent identity derives from parent_goal
        # via the same _default_project_for the parent's own loop used.
        if origin:
            try:
                from ancestry import record_fork_ancestry
                from orch_items import project_dir as _anc_pdir
                _par_goal = str(origin.get("parent_goal") or "").strip()
                _par_hid = str(origin.get("parent_handle_id") or "").strip()
                _child_slug = str(_loop_kwargs.get("project") or "")
                _par_slug = _default_project_for(_par_goal) if _par_goal else ""
                if (_par_goal or _par_hid) and _child_slug and _child_slug != _par_slug:
                    record_fork_ancestry(
                        _anc_pdir(_child_slug),
                        parent_id=_par_slug or _par_hid,
                        parent_title=_par_goal or f"thread {_par_hid}",
                        parent_dir=_anc_pdir(_par_slug) if _par_slug else None,
                    )
            except Exception:
                pass

        # Wire step_callback for channel live updates (main AGENDA path only)
        if channel is not None:
            def _step_cb(step_num: int, step_text: str, summary: Optional[str], status: str) -> None:
                channel.emit(
                    "step",
                    text=f"Step {step_num}: {(summary or step_text)[:600]}",
                    step_num=step_num,
                    status=status,
                )
            _loop_kwargs["step_callback"] = _step_cb

        # Persona injection: select best persona for goal and inject as ancestry_context_extra.
        # forced_persona (from garrytan:, persona:<name>:, or the explicit persona=
        # kwarg) overrides auto-selection — but only when the name resolves to a
        # real, registered persona (_resolve_forced_persona). An unknown forced
        # name degrades to normal persona_for_goal() auto-selection with a
        # warning, rather than silently producing no persona context
        # (BACKLOG hist-r2-02).
        _persona_ctx = ""
        try:
            from persona import persona_for_goal, PersonaRegistry, build_persona_system_prompt, record_persona_dispatch, _DEFAULT_PERSONA
            _preg = PersonaRegistry()
            _pconf = 1.0
            # Explicit persona= kwarg wins over a text-embedded prefix — same
            # precedence model= already has over the effort: prefix group.
            _requested_persona = _pfx.forced_persona
            if persona and persona.strip():
                _explicit_persona = persona.strip().lower()
                if _requested_persona and _requested_persona != _explicit_persona:
                    log.warning(
                        "handle: explicit persona=%r overrides prefix-forced persona=%r",
                        _explicit_persona, _requested_persona,
                    )
                _requested_persona = _explicit_persona
            _pname, _forced_honored = _resolve_forced_persona(_requested_persona, _preg)
            if not _forced_honored:
                _pname, _pconf = persona_for_goal(message, registry=_preg, confidence_threshold=0.75)
            # Track dispatch for persona gap detection (evolver uses this)
            try:
                _is_fallback = not _forced_honored and (
                    _pconf < 0.75 or _pname == _DEFAULT_PERSONA
                )
                record_persona_dispatch(
                    message, _pname, _pconf,
                    is_fallback=_is_fallback, handle_id=handle_id,
                )
                # Run-keyed copy: the global dispatch log can't answer
                # "which persona did THIS run use" — metadata.json can.
                from runs import stamp_run_metadata as _stamp_run_metadata
                _stamp_run_metadata({
                    "persona": _pname,
                    "persona_confidence": round(_pconf, 3),
                    "persona_fallback": _is_fallback,
                    "persona_forced": _forced_honored,
                })
            except Exception:
                pass
            _pspec = _preg.load(_pname)
            if _pspec:
                _persona_ctx = build_persona_system_prompt(_pspec, goal=message)
                log.info("handle: persona=%s conf=%.2f forced=%s", _pname, _pconf, _forced_honored)
        except Exception:
            pass
        _extra_ctx_parts = []
        if prior_context:
            _extra_ctx_parts.append(
                f"== Prior run context (for continuation) ==\n{prior_context}\n"
                f"== End prior context — continue from here =="
            )
        # Dispatch-envelope operator channel (docs/DISPATCH_ENVELOPE.md):
        # advisory operator framing rides context, never the goal — lesson
        # extraction receives the goal only, so this text is structurally
        # unlearnable. Arrives pre-labeled (dispatch_envelope.operator_block).
        if operator_context:
            _extra_ctx_parts.append(operator_context)
        # NOW→AGENDA verdict escalation: the failed quick answer rides along
        # so the orchestrated run doesn't re-answer from model knowledge.
        if _now_escalation_context:
            _extra_ctx_parts.append(
                f"== Escalated from NOW lane ==\n{_now_escalation_context}\n"
                f"== End NOW-lane context =="
            )
        if _persona_ctx:
            _extra_ctx_parts.append(_persona_ctx)
        # Completion standard — injected for every AGENDA run
        # (workspace overlay wins over the shipped template)
        try:
            from config import user_file as _user_file
            _std_path = _user_file("COMPLETION_STANDARD.md")
            if _std_path is not None:
                _extra_ctx_parts.append(_std_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass

        # Dispatch recall (goal-brain step 3, docs/RECALL_DESIGN.md): the goal
        # arrives knowing its own history — thread ancestry plus recent
        # attempts at the same goal. Advisory injection; the hard guard lives
        # in handle_task (autonomous requeue path only). Read-only and local;
        # any failure degrades to "knows nothing".
        try:
            from config import get as _recall_cfg_get
            _recall_inject_on = bool(_recall_cfg_get("recall.dispatch_inject", True))
        except Exception:
            _recall_inject_on = True
        if _recall_inject_on:
            try:
                from recall import recall as _recall_fn
                _recall_block = _recall_fn(
                    message, slice="dispatch", origin=origin,
                    project=_agenda_project,
                ).as_context_block()
                if _recall_block:
                    _extra_ctx_parts.append(_recall_block)
            except Exception as _recall_exc:
                log.debug("handle: dispatch recall skipped: %s", _recall_exc)

        # Re-run identity (BACKLOG 2026-08-09, Jeremy: "a re-run should know
        # it's a re-run, with prior art"): deterministic prior-attempts brief
        # from the intake record — complete-by-construction, each verdict
        # carrying its standing — so a re-dispatched goal builds on prior
        # deliverables instead of doing project archaeology, and never reads
        # a contested or re-stamped record as its own failure. Zero LLM
        # spend; the recall block above stays the fuzzy/semantic complement.
        # Matched on _raw_input (pre-prefix-strip) — the same field the
        # intake record stores. exclude_handle_id: this handle's own row was
        # already written above.
        try:
            from rerun_identity import brief_for_goal as _rerun_brief
            _rerun_block = _rerun_brief(_raw_input, exclude_handle_id=handle_id)
            if _rerun_block:
                _extra_ctx_parts.append(_rerun_block)
        except Exception as _rerun_exc:
            log.debug("handle: rerun brief skipped: %s", _rerun_exc)

        # Phase 65 minimum viable experiment: scope generation via inversion.
        # Gated by `scope_generation` config flag (default off). `scope_ab_skip`
        # is the paired A/B flag — when true, we'd-have-generated is recorded
        # but not injected, so the same goal can be run with/without scope for
        # comparison. Uses the same config system as adaptive_execution (reads
        # from ~/.maro/config.yml, not the repo-local user/CONFIG.md).
        # See docs/PHASE_65_IMPLEMENTATION_PLAN.md.
        _scope = None
        _resolved_intent = None
        try:
            from config import get as _config_get
            _scope_on = bool(_config_get("scope_generation", False))
            _scope_ab_skip = bool(_config_get("scope_ab_skip", False))
        except Exception:
            _scope_on = False
            _scope_ab_skip = False
        if _scope_on and not dry_run:
            try:
                from scope import generate_resolved_intent
                # Hand the generator the ancestry assembled so far — it gets
                # passed to the director-proxy fallback on parse failure so the
                # proxy can commit to an interpretation informed by the same
                # context the planner would see.
                _scope_ancestry = "\n\n".join(p for p in _extra_ctx_parts if p)
                _resolved_intent = generate_resolved_intent(
                    message, adapter,
                    ancestry_context=_scope_ancestry,
                    # Scopes any proxy-interpretation decision to this project
                    # (blank domain would inject it into every project's
                    # recall — chunk-3 review finding).
                    decision_domain=project or _default_project_for(message),
                )
                # Keep _scope as the scope-view for back-compat with the
                # existing artifact-write / captain's-log / ab-skip branches
                # below — they all operate on the ScopeSet shape.
                _scope = _resolved_intent.scope if _resolved_intent else None
                # Resolve the project artifacts dir once; used for both
                # successful scope.md persistence and raw-dump on parse failure.
                try:
                    import orch_items as _oi
                    _scope_project = project or _default_project_for(message)
                    _proj_dir = _oi.projects_root() / _scope_project / "artifacts"
                    _proj_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    _proj_dir = None

                if _scope is None:
                    # Generator returned None (adapter failure swallowed inside
                    # generate_scope). Record the skip — during the May-2026
                    # rc=1 outage every run silently lost its scope and nothing
                    # in the artifacts showed scoping had been attempted.
                    try:
                        from captains_log import log_event, SCOPE_SKIPPED
                        log_event(
                            SCOPE_SKIPPED,
                            subject="scope_skipped",
                            summary="Scope generation enabled but returned nothing (adapter failure).",
                            context={"goal_preview": message[:200], "reason": "generator_returned_none"},
                        )
                    except Exception:
                        pass
                elif _scope.is_empty():
                    # Parse failed. Persist the raw LLM response so the next
                    # debug pass has evidence, and record a captain's log event
                    # so closure/scope observability runs can count parse failures.
                    _raw = (_scope.raw_text or "").strip()
                    if _raw:
                        # Debug evidence, not a work product — keep it OUT of
                        # the project artifacts dir. artifacts/ is the
                        # deliverable channel: run 75a88777 ranked this dump
                        # as a user deliverable, and the 1dac0e17 planner even
                        # planned a step around reading it. Run build dir
                        # first; project ROOT (unscanned) as fallback.
                        _dump_dir = None
                        try:
                            from runs import current_run_dir as _crd_scope
                            _rd_scope = _crd_scope()
                            if _rd_scope is not None:
                                _dump_dir = _rd_scope / "build"
                                _dump_dir.mkdir(parents=True, exist_ok=True)
                        except Exception:
                            _dump_dir = None
                        if _dump_dir is None and _proj_dir is not None:
                            _dump_dir = _proj_dir.parent
                        if _dump_dir is not None:
                            try:
                                (_dump_dir / "scope-raw-FAILED.txt").write_text(
                                    _raw + "\n", encoding="utf-8"
                                )
                                log.info("scope: parse failed, raw response at %s/scope-raw-FAILED.txt", _dump_dir)
                            except Exception as _raw_exc:
                                log.debug("scope: could not record raw response: %s", _raw_exc)
                    try:
                        from captains_log import log_event, SCOPE_PARSE_FAILED
                        log_event(
                            SCOPE_PARSE_FAILED,
                            subject="scope_parse_failed",
                            summary=f"Scope LLM response did not parse into failure_modes/in_scope/out_of_scope sections.",
                            context={
                                "goal_preview": message[:200],
                                "raw_length": len(_raw),
                                "raw_preview": _raw[:400],
                            },
                        )
                    except Exception:
                        pass
                    _scope = None  # treat as "no scope" for the rest of the pipeline
                else:
                    # Successful parse. Persist scope.md + resolved_intent.md
                    # + emit captain's log event.
                    # Per-run isolation: prefer run-dir/source when active,
                    # fall back to project_dir for older callers.
                    _scope_dir = _proj_dir
                    try:
                        from runs import source_dir as _source_dir_fn
                        _src = _source_dir_fn()
                        if _src is not None:
                            _scope_dir = _src
                    except Exception:
                        pass
                    if _scope_dir is not None:
                        try:
                            (_scope_dir / "scope.md").write_text(
                                _scope.to_markdown(), encoding="utf-8"
                            )
                            log.info("scope: recorded artifact at %s/scope.md", _scope_dir)
                        except Exception as _scope_rec_exc:
                            log.debug("scope: could not record artifact: %s", _scope_rec_exc)
                        # Resolved-intent artifact — "the thread the driver
                        # watches" per docs/DRIVER_AND_WATCHER.md #4. Scope is
                        # a section of the thread; the thread itself includes
                        # deliverables (and, later, assumed/verified/unknown
                        # and agenda-state carryover).
                        if _resolved_intent is not None and not _resolved_intent.is_empty():
                            try:
                                (_scope_dir / "resolved_intent.md").write_text(
                                    _resolved_intent.to_markdown(), encoding="utf-8"
                                )
                                log.info(
                                    "resolved_intent: recorded artifact at %s/resolved_intent.md "
                                    "(%d deliverables)",
                                    _scope_dir, len(_resolved_intent.deliverables),
                                )
                            except Exception as _ri_rec_exc:
                                log.debug("resolved_intent: could not record artifact: %s", _ri_rec_exc)
                    try:
                        from captains_log import log_event, SCOPE_GENERATED
                        _scope_ctx = {
                            "goal_preview": message[:200],
                            "failure_modes_count": len(_scope.failure_modes),
                            "in_scope_count": len(_scope.in_scope),
                            "out_of_scope_count": len(_scope.out_of_scope),
                            "deliverables_count": (
                                len(_resolved_intent.deliverables)
                                if _resolved_intent is not None else 0
                            ),
                            "ab_skip": bool(_scope_ab_skip),
                        }
                        # Surface director-proxy resolution when the scope
                        # only parsed after an ambiguity handoff. This lets
                        # post-hoc review see "goal was ambiguous, proxy
                        # committed to X, scope generated from X."
                        if _scope.proxy_resolution:
                            _scope_ctx["proxy_resolution"] = _scope.proxy_resolution
                        log_event(
                            SCOPE_GENERATED,
                            subject=("scope_generated_via_proxy"
                                     if _scope.proxy_resolution else "scope_generated"),
                            summary=(
                                f"Scope: {len(_scope.failure_modes)} failure modes, "
                                f"{len(_scope.in_scope)} in-scope, "
                                f"{len(_scope.out_of_scope)} out-of-scope"
                                + (" (proxy-resolved)" if _scope.proxy_resolution else "")
                                + "."
                            ),
                            context=_scope_ctx,
                        )
                    except Exception:
                        pass
                    # A/B skip: record but don't inject
                    if _scope_ab_skip:
                        log.info("[scope-deferred] ab-skip: scope generated "
                                 "but not injected (ab-test control arm)")
                    else:
                        # Inject the full resolved intent (scope + deliverables)
                        # when available; fall back to scope-only for back-compat.
                        if _resolved_intent is not None and not _resolved_intent.is_empty():
                            _extra_ctx_parts.append(_resolved_intent.to_markdown())
                        else:
                            _extra_ctx_parts.append(_scope.to_markdown())
                        if channel is None:
                            log.info("[scope-deferred] human-gate: no channel, "
                                     "proceeding with generated scope without review")
                        else:
                            log.info("[scope-deferred] human-gate: scope used "
                                     "without review (gate UX deferred)")
                        log.info("[scope-deferred] enforcement: scope injected "
                                 "but not checked mid-execution, violation "
                                 "detection deferred")
            except Exception as _scope_exc:
                log.warning("scope: generation failed, continuing without scope: %s", _scope_exc)
                try:
                    from captains_log import log_event, SCOPE_SKIPPED
                    log_event(
                        SCOPE_SKIPPED,
                        subject="scope_skipped",
                        summary=f"Scope generation raised; continuing without scope: {str(_scope_exc)[:120]}",
                        context={"goal_preview": message[:200], "reason": "exception",
                                 "error": str(_scope_exc)[:300]},
                    )
                except Exception:
                    pass

        if _extra_ctx_parts:
            _loop_kwargs["ancestry_context_extra"] = "\n\n".join(_extra_ctx_parts)

        if channel is not None:
            _loop_kwargs["channel"] = channel

        # data-r2-01: this lane runs closure judging below — defer lesson
        # extraction + skill crystallization past it (finalize_deferred_
        # learning) so learning sees the verdict instead of running blind
        # at loop finalize. Restart re-runs inherit via dict(_loop_kwargs).
        _loop_kwargs["defer_learning"] = True
        # Async-tail decree: this lane's finalize (the finally block below)
        # drains _POST_NOTIFY_MAINTENANCE after the run_completed notify —
        # the explicit opt-in the maintenance deferral requires. The
        # direct-CLI lanes set defer_learning without this and stay inline.
        _loop_kwargs["defer_maintenance"] = True

        loop_result = run_agent_loop(message, **_loop_kwargs)
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Every loop that ran for this handle (restarts add more) — the join
        # key from a run to its step-costs entries. Written to metadata after
        # the restart blocks settle.
        _run_loop_ids = [loop_result.loop_id] if getattr(loop_result, "loop_id", "") else []
        _audit_warnings: List[str] = []
        _audit_failed_loop_ids: set[str] = set()
        # Verdicts where the deterministic guard and closure DISAGREE. Jeremy,
        # 2026-08-02: "we ignore contested results for learning… add them as
        # anecdotes, but don't move for or against the learning either way."
        # So these ride the existing unresolved-verdict-audit lane: the
        # outcome row is written and keeps its evidence (decay trust, never
        # data), lesson extraction just doesn't run off it. Nothing is
        # blocked or deleted — the run simply doesn't get a vote.
        _contested_verdict_loop_ids: set[str] = set()

        # Director restart: loop broke with restart status — re-run with restart context.
        # continuation_depth increment prevents infinite restart loops.
        from loop_types import MAX_RESTART_DEPTH
        if (loop_result.status == "restart"
                and not dry_run
                and _loop_kwargs.get("continuation_depth", 0) < MAX_RESTART_DEPTH):
            try:
                _restart_ctx = loop_result.stuck_reason or "Director requested restart."
                _restart_ancestry = (
                    _loop_kwargs.get("ancestry_context_extra", "")
                    + f"\n\n== Director restart context ==\n{_restart_ctx}\n== End restart context =="
                ).strip()
                _restart_kwargs = dict(_loop_kwargs)
                _restart_kwargs["ancestry_context_extra"] = _restart_ancestry
                _restart_kwargs["continuation_depth"] = (
                    _loop_kwargs.get("continuation_depth", 0) + 1
                )
                _restart_kwargs["loop_reason"] = "director_restart"
                _restart_kwargs["parent_loop_id"] = getattr(loop_result, "loop_id", None)
                log.info("handle: director restart (depth %d) — %s",
                         _restart_kwargs["continuation_depth"], _restart_ctx[:80])
                if channel is not None:
                    channel.emit("restart", text=f"Director restart: {_restart_ctx[:200]}")
                loop_result = run_agent_loop(message, **_restart_kwargs)
                elapsed = int((time.monotonic() - started_at) * 1000)
                if getattr(loop_result, "loop_id", ""):
                    _run_loop_ids.append(loop_result.loop_id)
            except Exception as _rst_exc:
                log.warning("handle: restart re-run failed: %s", _rst_exc)

        # Director closure check — verify the goal was actually achieved.
        # Runs on any terminal state that produced steps (not just "done"):
        # a stuck/partial/restart loop still benefits from closure's honest
        # "what got delivered" signal, and the CLOSURE_VERDICT event makes
        # the recovery paths observable. Closure-restart escalation only
        # fires from "done" (other states already indicate work isn't
        # complete — re-running via this path would double-recover).
        _closure_eligible_statuses = ("done", "partial", "stuck", "restart")
        _ran_any_step = any(getattr(s, "status", "") == "done"
                            for s in (loop_result.steps or []))
        # Defined unconditionally: the quality gate below reads _closure for
        # escalate reconciliation, and its run conditions only happen to
        # mirror this block's — don't let that coupling become a NameError.
        _closure = None
        _closure_decision = None
        _closure_error = ""
        # Say WHY closure is being skipped, at the only place that knows.
        #
        # The finished-without-closure tripwire (runs.close_run) sees run
        # metadata, which carries no step count — so a run where no step
        # completed looked exactly like a run closure forgot to judge.
        # Measured 2026-08-06: 259 of the 345 unverdicted agenda runs on the
        # box are that shape. **75% of the tripwire's firings were false
        # positives**, which is how a tripwire teaches people to ignore it.
        #
        # Inferring it later from the loop log would work but is archaeology;
        # this is the frame that evaluated the condition. And no change to
        # the tripwire is needed — it already skips anything carrying a
        # goal_verdict_source, so naming the skip IS the fix.
        if (not dry_run
                and loop_result.status in _closure_eligible_statuses
                and not _ran_any_step):
            try:
                from stop_verdicts import VERDICT_SOURCE_NO_STEPS_COMPLETED
                from runs import stamp_run_metadata as _srm_nosteps
                _srm_nosteps(
                    {"goal_verdict_source": VERDICT_SOURCE_NO_STEPS_COMPLETED})
                _nosteps_loop = getattr(loop_result, "loop_id", "") or ""
                if _nosteps_loop:
                    # The ledger row exists for these runs (they DO reach
                    # reflect_and_record), so the denominator needs the same
                    # marker there. goal_achieved stays None: no work, no
                    # verdict, and inventing one is the failure this family
                    # of guards exists to prevent.
                    from memory_ledger import stamp_outcome_verdict as _sov_nosteps
                    _sov_nosteps(
                        _nosteps_loop,
                        goal_achieved=None,
                        goal_verdict_source=VERDICT_SOURCE_NO_STEPS_COMPLETED,
                    )
            except Exception as _nosteps_exc:
                log.debug("no-steps verdict marker failed: %s", _nosteps_exc)
        if (not dry_run
                and loop_result.status in _closure_eligible_statuses
                and _ran_any_step):
            _closure_diag = None
            try:
                from introspect import diagnose_loop as _diagnose_loop
                if getattr(loop_result, "loop_id", ""):
                    _closure_diag = _diagnose_loop(loop_result.loop_id)
            except Exception:
                _closure_diag = None
            try:
                from director import evaluate_closure
                _closure_decision = evaluate_closure(
                    message,
                    loop_result.steps,
                    adapter,
                    workspace_path=repo_path or "",
                    channel=channel,
                    scope=_scope,
                    resolved_intent=_resolved_intent,
                    diagnosis=_closure_diag,
                    loop_id=getattr(loop_result, "loop_id", "") or "",
                    project=project or getattr(loop_result, "project", "") or "",
                )
                _closure = _closure_decision.closure_verdict
            except Exception as _closure_exc:
                _closure_decision = None
                _closure = None
                # LT-4 R2w: this swallow used to be silent — "closure
                # crashed" was indistinguishable from "closure not
                # eligible", and a benchmark run recorded no verdict with
                # nothing saying why.
                _closure_error = f"{type(_closure_exc).__name__}: {_closure_exc}"
                log.warning(
                    "handle: closure evaluation raised — run records no "
                    "verdict (source=closure_error): %s", _closure_error)

            try:
                from config import get as _config_get
                _closure_restart = bool(_config_get("closure_restart", True))
            except Exception:
                _closure_restart = True

            _depth = _loop_kwargs.get("continuation_depth", 0)
            # Evidence judgment (restart-worthy gaps vs narrative-only gaps vs
            # unjudged) lives in director.evaluate_closure — the unified
            # closure trigger of the adaptive-execution seam. What stays here
            # is orchestration policy: the closure_restart config flag, the
            # restart-depth ceiling, and only-escalate-from-"done" (stuck/
            # partial already know work isn't complete — re-running via this
            # path would double-recover).
            if (
                _closure_restart
                and _closure_decision is not None
                and _closure_decision.action == "restart"
                and _closure is not None
                and _depth < MAX_RESTART_DEPTH
                and loop_result.status == "done"
            ):
                # The first attempt's closure verdict is consumed as the
                # restart trigger, then `_closure` is replaced by the second
                # attempt's verdict below. Stamp the rejected attempt before
                # crossing that boundary so a crash, successful retry, or
                # failed retry cannot leave it looking like unjudged success
                # to deferred learning and strategy scoring.
                _superseded_loop_id = getattr(loop_result, "loop_id", "") or ""
                try:
                    from memory import stamp_outcome_verdict as _stamp_superseded
                    _stamp_result = _stamp_superseded(
                        _superseded_loop_id,
                        goal_achieved=False,
                        goal_verdict_source="closure",
                        goal_verdict_confidence=float(_closure.confidence),
                        max_attempts=2,
                    )
                    # Missing means loop finalization produced no evidence to
                    # protect, so recovery may proceed. Only a write failure
                    # leaves a possibly dishonest row behind.
                    if _stamp_result.status not in ("updated", "missing"):
                        _failure_detail = (
                            _stamp_result.error
                            or "outcome verdict was not updated"
                        )
                        if _stamp_result.attempts:
                            _failure_detail += (
                                f" after {_stamp_result.attempts} attempt(s)")
                        raise RuntimeError(
                            _failure_detail)
                except Exception as _stamp_exc:
                    # Fail closed at the restart boundary. The rejected run's
                    # outcome row cannot be trusted by deferred learning until
                    # its negative verdict is durable, so do not spawn a retry
                    # or continue into learning/quality paths that could score
                    # the verdict-less `done` row as success.
                    _stamp_reason = (
                        "closure rejected the completed attempt, but its negative "
                        f"verdict could not be persisted: {_stamp_exc}"
                    )
                    log.error("handle: refusing closure restart — %s", _stamp_reason)
                    loop_result.status = "incomplete"
                    loop_result.stuck_reason = _stamp_reason
                    try:
                        from runs import stamp_run_metadata as _stamp_failed_meta
                        _meta_path = _stamp_failed_meta({
                            "goal_achieved": False,
                            "goal_verdict_source": "closure_stamp_failed",
                            "goal_verdict_confidence": float(_closure.confidence),
                            "goal_verdict_summary": _stamp_reason[:300],
                            "loop_ids": list(_run_loop_ids),
                        })
                        if _meta_path is None:
                            raise RuntimeError("active run metadata was not updated")
                    except Exception as _meta_exc:
                        log.error(
                            "handle: closure stamp failure metadata also failed: %s",
                            _meta_exc,
                        )
                    if channel is not None:
                        try:
                            channel.emit("error", text=_stamp_reason)
                        except Exception:
                            pass
                    return _loop_result_to_handle(
                        loop_result,
                        handle_id=handle_id,
                        message=message,
                        confidence=confidence,
                        reason=reason,
                        started_at=started_at,
                        project=project,
                        extra_text=(
                            "\n\n⚠️ Closure restart refused: " + _stamp_reason
                        ),
                    )
                # Gap context is built by evaluate_closure (restart_context on
                # the decision) — the same seam a §9.3 declare-blocked verdict
                # would populate.
                _closure_ctx = _closure_decision.restart_context or (
                    f"The previous run declared done, but closure verification "
                    f"found gaps.\nSummary: {_closure.summary}"
                )
                _closure_ancestry = (
                    _loop_kwargs.get("ancestry_context_extra", "")
                    + f"\n\n== Closure gap context ==\n{_closure_ctx}\n== End closure gap context =="
                ).strip()
                _closure_kwargs = dict(_loop_kwargs)
                _closure_kwargs["ancestry_context_extra"] = _closure_ancestry
                _closure_kwargs["continuation_depth"] = _depth + 1
                _closure_kwargs["loop_reason"] = "closure_restart"
                _closure_kwargs["parent_loop_id"] = getattr(loop_result, "loop_id", None)
                log.info(
                    "handle: closure restart (depth %d) — gaps=%d confidence=%.2f",
                    _closure_kwargs["continuation_depth"],
                    len(_closure.gaps),
                    _closure.confidence,
                )
                if channel is not None:
                    try:
                        channel.emit(
                            "closure_restart",
                            text=f"Closure verification found gaps — restarting.\n{_closure.summary}",
                        )
                    except Exception:
                        pass
                # The pre-restart verdict is the convergence baseline the
                # re-verify compares against (§9.3): held here because the
                # re-verify below replaces `_closure`.
                _pre_restart_closure = _closure
                try:
                    loop_result = run_agent_loop(message, **_closure_kwargs)
                    elapsed = int((time.monotonic() - started_at) * 1000)
                    if getattr(loop_result, "loop_id", ""):
                        _run_loop_ids.append(loop_result.loop_id)
                    # Re-verify the restarted loop — its declared status is
                    # exactly as unverified as the first loop's was. Without
                    # this, a restart that re-declares done sticks regardless
                    # of whether the gaps were addressed. A "restart" action
                    # is deliberately ignored here (a second restart is the
                    # depth-gated outer gate's call), but "declare-blocked"
                    # IS consumed (§9.3): identical failure fingerprint
                    # across the restart means plan-level stall — stamp the
                    # typed stop with its structural evidence. First-write-
                    # wins: stamping before the demotion machinery below
                    # lets thesis-refuted (structural, evidence-bearing)
                    # outrank the generic demotion verdict.
                    try:
                        _reverify_decision = evaluate_closure(
                            message,
                            loop_result.steps,
                            adapter,
                            workspace_path=repo_path or "",
                            channel=channel,
                            scope=_scope,
                            resolved_intent=_resolved_intent,
                            diagnosis=None,
                            loop_id=getattr(loop_result, "loop_id", "") or "",
                            project=project or getattr(loop_result, "project", "") or "",
                            prior_verdict=_pre_restart_closure,
                        )
                        _closure = _reverify_decision.closure_verdict
                        if (
                            _reverify_decision.action == "declare-blocked"
                            and _reverify_decision.stop_verdict
                        ):
                            log.info(
                                "handle: closure restart declared blocked — %s",
                                _reverify_decision.reasoning,
                            )
                            # Status honesty rides the declaration: the
                            # evidence behind declare-blocked is deterministic
                            # (the same hard-failed checks across BOTH
                            # attempts), stronger than the LLM-confidence bar
                            # the generic demotion below gates on — without
                            # this, a verdict in [0.6, 0.7) stamps
                            # thesis-refuted on a run that still reports done.
                            # Non-done statuses (stuck, failed) are already
                            # honest; only "done" gets demoted.
                            if loop_result.status == "done":
                                loop_result.status = "incomplete"
                                if loop_result.stuck_reason is None:
                                    loop_result.stuck_reason = (
                                        _reverify_decision.stop_evidence[:300]
                                    )
                            _stamp_stop_on_demotion(
                                loop_result,
                                _reverify_decision.stop_verdict,
                                _reverify_decision.stop_evidence,
                            )
                    except Exception as _rv_exc:
                        _closure = None  # fail open: no re-verdict, no demotion
                        # R2-4 (adversarial review 2026-08-06): a crashed
                        # re-verification is the same measurement gap as a
                        # crashed first closure — without this the run
                        # finalizes closure_never_stamped (or keeps a
                        # pre-restart verdict of superseded work) instead of
                        # saying the judge died.
                        _closure_error = (
                            f"reverify {type(_rv_exc).__name__}: {_rv_exc}")
                except Exception as _cr_exc:
                    log.warning("handle: closure restart re-run failed: %s", _cr_exc)

            # Deterministic provenance guard (agenda twin of the NOW guard): an
            # input the goal asked to read that isn't on disk, or an output that
            # never landed, means not-achieved — regardless of closure/narrative.
            # Works even when closure is None. Catches the false_pass a text-only
            # verdict can't see (shadow-eval n=42, 2026-06-24).
            _provenance_failed = False
            if loop_result.status == "done":
                _done_results = "\n\n".join(
                    s.result for s in loop_result.steps
                    if s.status == "done" and s.result
                )
                # Advisory memory twin (agenda lane). Runs before the file
                # guard's demotion branch so the mark lands either way — the
                # two findings are independent evidence, not a ladder.
                _mark_memory_provenance(_done_results)
                _prov_missing = _provenance_missing(
                    _raw_input,
                    result_text=_done_results,
                    window_start=_run_window_start(
                        loop_result.elapsed_ms, wall_start=wall_started_at),
                )
                if _prov_missing:
                    _provenance_failed = True
                    log.info(
                        "provenance (agenda): claimed input/output(s) not found %s — demoted to incomplete",
                        _prov_missing,
                    )
                    loop_result.status = "incomplete"
                    if loop_result.stuck_reason is None:
                        loop_result.stuck_reason = (
                            f"provenance: claimed input/output(s) not found: {_prov_missing}"
                        )
                    # The run believed it was done; deterministic provenance
                    # says the claimed inputs/outputs don't exist — the map
                    # diverged from the territory (§13b lost-the-plot).
                    _stamp_stop_on_demotion(
                        loop_result, "lost-the-plot",
                        f"provenance: claimed input/output(s) not found: {_prov_missing}",
                    )
                    try:
                        from runs import write_metadata as _wm_prov
                        from runs import current_run_dir as _crd_prov
                        _rd_p = _crd_prov()
                        if _rd_p is not None:
                            _prov_extra = {
                                "goal_achieved": False,
                                "goal_verdict_source": "provenance",
                                "goal_verdict_summary":
                                    f"claimed input/output(s) not found: {_prov_missing}",
                            }
                            # Closure disagreeing with the guard is recorded,
                            # not silently discarded. Additive only — the
                            # demotion still stands. Two false demotions on
                            # 2026-08-02 (9d88acf2 @ 0.75, ea4ebe4a @ 0.92 with
                            # 5/5 checks) were each found by hand days later;
                            # both were paths the run DISCUSSED rather than
                            # claimed. See provenance.contested_by_closure.
                            try:
                                from provenance import contested_by_closure
                                _contested = contested_by_closure(
                                    _closure, _prov_missing)
                                if _contested:
                                    _prov_extra.update(_contested)
                                    _contested_verdict_loop_ids.add(
                                        getattr(loop_result, "loop_id", "") or "")
                                    log.warning(
                                        "provenance demoted a run closure judged "
                                        "COMPLETE @ %.2f — recorded as contested: %s",
                                        _contested.get("closure_confidence", 0.0),
                                        _prov_missing,
                                    )
                            except Exception:
                                pass
                            _wm_prov(
                                _rd_p, handle_id=handle_id, prompt=_raw_input,
                                extra=_prov_extra,
                            )
                            # Compiled-truth half (MILESTONES #3a): the
                            # provenance guard is deterministic — the most
                            # trustworthy verified-claim source there is.
                            try:
                                from thread_brain import append_compiled_truth
                                append_compiled_truth(
                                    _rd_p,
                                    "provenance: claimed input/output(s) not "
                                    f"found: {str(_prov_missing)[:200]}",
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # Verdict tri-state (SF-2): the outcomes row was already
                    # written at loop finalization, verdict-less — stamp the
                    # deterministic provenance verdict onto it so learning
                    # consumers see the failure, not just run metadata.
                    from audit_policy import persist_delivered_outcome_verdict
                    _prov_audit = persist_delivered_outcome_verdict(
                        getattr(loop_result, "loop_id", "") or "",
                        goal_achieved=False,
                        goal_verdict_source="provenance",
                        loop_ids=_run_loop_ids,
                        channel=channel,
                    )
                    if not _prov_audit.learning_allowed:
                        _audit_warnings.append(_prov_audit.warning)
                        _audit_failed_loop_ids.add(
                            getattr(loop_result, "loop_id", "") or "")

            # Status honesty (agenda twin of _verify_now_outcome): when the
            # director's own verifier contradicts a declared "done" at high
            # confidence, the run is recorded as incomplete. Live find
            # 2026-06-11: an unsatisfiable goal ran the loop, every step
            # result said "goal is incomplete", closure agreed at 0.95–0.99 —
            # and the run still finalized done, poisoning recall, the
            # dispatch guard, and the navigator. Verified-done beats
            # reported-done.
            # Unjudged verdicts (negative verdict resting only on inconclusive
            # probes — verifier tooling/privilege failures, not disproof) must
            # not demote: that's the verifier's failure, not the goal's.
            if (
                _closure is not None
                and not _closure.complete
                and getattr(_closure, "judged", True)
                and _closure.confidence >= 0.7
                and loop_result.status == "done"
            ):
                log.info(
                    "handle: closure contradicts done (conf=%.2f) — status demoted to incomplete: %s",
                    _closure.confidence,
                    str(_closure.summary)[:120],
                )
                loop_result.status = "incomplete"
                if loop_result.stuck_reason is None:
                    loop_result.stuck_reason = (
                        f"closure verification: {str(_closure.summary)[:300]}"
                    )
                _stamp_stop_on_demotion(
                    loop_result, "lost-the-plot",
                    f"closure contradicts done (conf={_closure.confidence:.2f}): "
                    f"{str(_closure.summary)[:300]}",
                )

            # Record the goal verdict as its own metadata dimension — process
            # status ("did the run finish") and goal achievement ("did it
            # deliver what was asked") are different facts; status alone
            # conflated them until 2026-06-11.
            # Only when closure actually ran checks. The fail-open null
            # verdict (unjudged, "Verification did not run.", checks_run=0
            # — complete=False carries no evidence) exists so closure errors
            # never block execution — recording it would bless unverified
            # work as achieved (burn-in batch 4, 2026-07-02: a
            # rate-limit-stuck run got goal_achieved=True from a skipped
            # verification). No checks → no verdict → unverified.
            # Unjudged verdicts additionally omit goal_achieved: when every
            # non-passing check was inconclusive (verifier syntax error,
            # permission wall, missing tool), the verdict has no disproof in
            # it — recording false would blame the goal for the verifier's
            # own failures (2026-07-09 dogfood batch: 4/5 known-good runs
            # false-negatived this way). Absence means "not judged".
            if (
                _closure is not None
                and _closure.checks_run > 0
                and not _provenance_failed
            ):
                _judged = getattr(_closure, "judged", True)
                try:
                    from runs import write_metadata as _wm_verdict
                    from runs import current_run_dir as _crd_verdict
                    _rd_v = _crd_verdict()
                    if _rd_v is not None:
                        _verdict_extra = {
                            "goal_verdict_confidence": float(_closure.confidence),
                            "goal_verdict_source": (
                                "closure" if _judged else "closure_unverifiable"
                            ),
                            "goal_verdict_summary": str(_closure.summary)[:300],
                        }
                        # Gaps ride as their own field: the 300-char summary
                        # can truncate away the "why not" — merry-nettle
                        # (2026-07-16) surfaced goal_achieved=false beside a
                        # summary whose visible prefix read "Goal achieved."
                        _gaps = [
                            str(g)[:200] for g in (_closure.gaps or []) if g
                        ][:5]
                        if _gaps and not _closure.complete:
                            _verdict_extra["goal_verdict_gaps"] = _gaps
                        # Only-when-stamped: key absent means "no downgrade",
                        # never "" — same convention as the event fields.
                        _downgrade = str(
                            getattr(_closure, "downgrade_reason", "") or "")
                        if _downgrade:
                            _verdict_extra["goal_verdict_downgrade_reason"] = (
                                _downgrade[:300])
                        if _judged:
                            _verdict_extra["goal_achieved"] = bool(_closure.complete)
                        _wm_verdict(
                            _rd_v, handle_id=handle_id, prompt=_raw_input,
                            extra=_verdict_extra,
                        )
                        # Compiled-truth half (MILESTONES #3a): a closure
                        # verdict with checks actually run is a verified
                        # claim — one line per run in the thread brain.
                        try:
                            from thread_brain import append_compiled_truth
                            _verdict_word = (
                                ("achieved" if _closure.complete else "NOT achieved")
                                if _judged else "UNVERIFIABLE (probes inconclusive)"
                            )
                            append_compiled_truth(
                                _rd_v,
                                f"closure verdict: {_verdict_word}"
                                f" (conf {float(_closure.confidence):.2f}, "
                                f"{int(_closure.checks_run)} checks) — "
                                f"{str(_closure.summary)[:200]}",
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
                # Verdict tri-state (SF-2): stamp the closure verdict onto the
                # outcomes row written at loop finalization, mirroring the run
                # metadata exactly — goal_achieved only when judged; an
                # unjudged (closure_unverifiable) verdict records its source
                # but leaves goal_achieved absent (and never overwrites a
                # provenance False already stamped above).
                from audit_policy import persist_delivered_outcome_verdict
                _closure_audit = persist_delivered_outcome_verdict(
                    getattr(loop_result, "loop_id", "") or "",
                    goal_achieved=(bool(_closure.complete) if _judged else None),
                    goal_verdict_source=(
                        "closure" if _judged else "closure_unverifiable"
                    ),
                    goal_verdict_confidence=float(_closure.confidence),
                    loop_ids=_run_loop_ids,
                    channel=channel,
                )
                if not _closure_audit.learning_allowed:
                    _audit_warnings.append(_closure_audit.warning)
                    _audit_failed_loop_ids.add(
                        getattr(loop_result, "loop_id", "") or "")
                # Durable disputed marker (adversarial review 2026-08-09:
                # an in-memory skip set is routing state, not a verdict
                # state). Field-name parity with the provenance contested
                # lane so every surface that already renders contested
                # picks this up. Consumer-side neutrality (strategy
                # fitness, repeat-pressure) is a shared residual with that
                # lane — BACKLOG.
                try:
                    if (getattr(_closure, "verdict_audit", {}) or {}).get(
                            "disputed"):
                        from runs import write_metadata as _wm_vad
                        from runs import current_run_dir as _crd_vad
                        _rd_vad = _crd_vad()
                        if _rd_vad is not None:
                            _wm_vad(
                                _rd_vad, handle_id=handle_id,
                                prompt=_raw_input,
                                extra={
                                    "goal_verdict_contested": True,
                                    "goal_verdict_contested_by":
                                        "verdict_audit",
                                })
                except Exception:
                    pass
            elif _closure is None and _closure_error:
                # Absence-means-not-judged holds (no goal_achieved key), but
                # the source must say WHY there is no verdict — a crashed
                # judge is a measurement gap, not an ineligible run.
                try:
                    from runs import write_metadata as _wm_cerr
                    from runs import current_run_dir as _crd_cerr
                    _rd_ce = _crd_cerr()
                    if _rd_ce is not None:
                        _wm_cerr(
                            _rd_ce, handle_id=handle_id, prompt=_raw_input,
                            extra={
                                "goal_verdict_source": "closure_error",
                                "goal_verdict_summary": _closure_error[:300],
                            },
                        )
                except Exception:
                    pass
                # The ledger row needs the same why (adversarial review
                # 2026-08-06 R2-1): metadata alone leaves the outcomes row
                # source-less — and the presence of a metadata source
                # suppresses close_run's never-stamped fallback, so without
                # this stamp the crash case keeps the exact silent
                # denominator hole this branch exists to close.
                # goal_achieved=None: stamp_outcome_verdict leaves any
                # provenance False untouched.
                try:
                    from audit_policy import persist_delivered_outcome_verdict
                    persist_delivered_outcome_verdict(
                        getattr(loop_result, "loop_id", "") or "",
                        goal_achieved=None,
                        goal_verdict_source="closure_error",
                        goal_verdict_confidence=0.0,
                        loop_ids=_run_loop_ids,
                        channel=channel,
                    )
                except Exception:
                    pass

        # data-r2-01: learning was deferred at loop finalize (defer_learning
        # above) — run it now that the closure/provenance verdict is stamped
        # on the outcomes rows. Lessons extract verdict-aware for every loop
        # this handle ran; skills crystallize for the final loop unless it
        # was judged not-achieved. Sits OUTSIDE the closure gate on purpose:
        # when closure was skipped (dry run, no done steps), the deferred
        # lessons still extract — unjudged, same as the pre-fix behavior.
        # Answer-first: registered, not run — handle()'s finalize drains this
        # after the run_completed notify (or the escalation path drains it
        # early, see _POST_NOTIFY_LEARNING). Snapshot the mutables now:
        # loop_result is rebound and _run_loop_ids appended-to on escalation.
        try:
            from loop_finalize import finalize_deferred_learning
            _final_lid = getattr(loop_result, "loop_id", "") or ""
            _dl_result = loop_result
            _dl_project = project or getattr(loop_result, "project", "") or ""
            _dl_extra = [l for l in _run_loop_ids if l != _final_lid]
            # Verdict-audit dispute (2026-08-09): the closure verdict stood,
            # but the evidence-holding auditor disagreed and a judge retry
            # maintained it — two judges in disagreement is the same shape as
            # provenance-vs-closure, so the loop joins the same contested
            # holdout: recorded, but not voting for or against in learning.
            try:
                _va = getattr(_closure, "verdict_audit", {}) or {}
                if _va.get("disputed") and _final_lid:
                    _contested_verdict_loop_ids.add(_final_lid)
                    log.info(
                        "learning: closure verdict stands but the verdict "
                        "audit disputed it — loop %s held out as contested",
                        _final_lid)
            except Exception:
                pass
            _dl_skip = list(_audit_failed_loop_ids | _contested_verdict_loop_ids)
            if _contested_verdict_loop_ids:
                log.info(
                    "learning: %d loop(s) held out as contested — recorded, "
                    "but not voting for or against",
                    len(_contested_verdict_loop_ids))
            _defer_learning_post_notify(
                handle_id,
                lambda: finalize_deferred_learning(
                    _dl_result,
                    adapter=adapter,
                    project=_dl_project,
                    dry_run=dry_run,
                    verbose=verbose,
                    extra_loop_ids=_dl_extra,
                    skip_loop_ids=_dl_skip,
                ))
        except Exception as _dl_exc:
            log.warning("deferred learning failed for loop %s: %s",
                        getattr(loop_result, "loop_id", ""), _dl_exc)

        # Notify channel that the main loop completed
        if channel is not None:
            try:
                _result_parts = [
                    s.result for s in loop_result.steps
                    if s.status == "done" and s.result
                ]
                _result_summary = "\n\n".join(_result_parts) if _result_parts else "[no output]"
                if loop_result.status == "stuck":
                    _stuck_reason = getattr(loop_result, "stuck_reason", None) or "no further progress possible"
                    channel.emit("stuck", text=f"Loop got stuck after {len(loop_result.steps)} steps: {_stuck_reason}")
                elif loop_result.status == "restart":
                    # restart re-run failed or depth exceeded — treat as stuck
                    _rst_reason = getattr(loop_result, "stuck_reason", None) or "restart limit reached"
                    channel.emit("stuck", text=f"Director restart loop exhausted: {_rst_reason}")
                elif getattr(loop_result, "pause_reason", ""):
                    # A typed pause is a wait, not a failure — don't let it
                    # reach the user as an error line (delivery decree: plain
                    # words where they asked, no terminal language). The break
                    # site already said anything specific it had to say.
                    channel.emit(
                        "paused",
                        text=(f"Paused ({loop_result.pause_reason}) — this can "
                              f"pick up right where it left off."),
                    )
                elif loop_result.status not in ("done", "complete"):
                    channel.emit("error", text=f"Loop ended with status: {loop_result.status}")
                channel.complete(_result_summary)
            except Exception:
                pass  # channel notifications must never block

        # Quality gate — skeptic review; escalate model tier if output is below bar.
        # Runs on any terminal state that produced work so contested-claims
        # and probe events fire regardless of outcome. Only the *escalation*
        # re-run is gated on "done" — stuck/partial loops don't benefit from
        # being re-run at a higher tier (they indicate a decomposition or
        # recovery issue, not a model-tier issue).
        _gate_note = ""
        _contested_claims: list = []
        _gate_statuses = ("done", "partial", "stuck", "restart")
        _ran_any_step_for_gate = any(getattr(s, "status", "") == "done"
                                      for s in (loop_result.steps or []))
        _skip_quality_gate = os.environ.get("ORCH_SOURCE", "").strip().lower() == "build-loop"
        if (not dry_run
                and not _skip_quality_gate
                and loop_result.status in _gate_statuses
                and _ran_any_step_for_gate
                and _cfg.get("quality_gate", "true") == "true"):
            try:
                from quality_gate import run_quality_gate, next_model_tier
                from llm import default_subprocess_cwd
                from agent_loop import _project_dir_root
                # Quality gate runs agentic council/adversarial/probe calls after
                # the loop returns — scope their cwd to the just-run project dir
                # so they (and the settled_by_command probe) resolve files
                # in-workspace, not against Maro's launch cwd.
                _qg_proj = getattr(loop_result, "project", "") or ""
                _qg_cwd = str(_project_dir_root() / _qg_proj) if _qg_proj else None
                # Cross-ref lanes: strict: = paid + acting (unchanged).
                # Research-shaped goals get the hosted-free flag-only lane
                # (chunk 5b) — cross_ref's fresh-context-per-claim design is
                # the genuinely decorrelated check and had zero organic uses.
                if _strict_prefix:
                    _cross_ref_lane: object = True
                else:
                    from workers import infer_worker_type, WORKER_RESEARCH
                    _cross_ref_lane = (
                        "hosted_free"
                        if infer_worker_type(message) == WORKER_RESEARCH
                        else False
                    )
                with default_subprocess_cwd(_qg_cwd):
                    _gate_verdict = run_quality_gate(
                        message, loop_result.steps, adapter,
                        run_council=_strict_prefix,
                        run_cross_ref=_cross_ref_lane,
                        loop_id=getattr(loop_result, "loop_id", None),
                    )
                _contested_claims = _gate_verdict.contested_claims or []
                # Verdict reconciliation (2026-07-29, ead11c12-nimble-shore):
                # closure runs executed probes over the delivered artifacts;
                # the gate judges a few-KB excerpt. When both have spoken and
                # they disagree, the one that read more evidence wins — the
                # gate escalated a run whose closure had just passed 5/5 at
                # 0.88, and the forced re-run burned the cost budget. Same
                # 0.7 bar as the demotion path: closure's authority to
                # overturn a "done" and to defend one are symmetric. The
                # dissent is recorded (QUALITY_GATE_OVERRULED), never lost —
                # stack, don't substitute.
                try:
                    from config import get as _qg_config_get
                    _closure_overrule = bool(
                        _qg_config_get("quality_gate.closure_overrule", True))
                except Exception:
                    _closure_overrule = True
                _closure_defends = (
                    _closure is not None
                    and getattr(_closure, "judged", True)
                    and _closure.complete
                    and _closure.checks_run > 0
                    and _closure.confidence >= 0.7
                )
                if (_gate_verdict.escalate and loop_result.status == "done"
                        and _closure_overrule and _closure_defends):
                    _gate_note = (
                        f"\n\n⚠️ Quality gate: ESCALATE overruled by closure "
                        f"verdict ({_closure.checks_passed}/{_closure.checks_run}"
                        f" checks, conf {_closure.confidence:.2f}) — "
                        f"{_gate_verdict.reason}"
                    )
                    if verbose:
                        print(f"[maro:{handle_id}] quality gate: ESCALATE "
                              f"overruled by closure verdict "
                              f"({_closure.checks_passed}/{_closure.checks_run} "
                              f"checks, conf {_closure.confidence:.2f})",
                              file=sys.stderr, flush=True)
                    try:
                        from captains_log import log_event as _qg_log_event
                        from captains_log import QUALITY_GATE_OVERRULED
                        _qg_log_event(
                            QUALITY_GATE_OVERRULED,
                            message[:120],
                            f"gate ESCALATE vs closure "
                            f"{_closure.checks_passed}/{_closure.checks_run}"
                            f"@{_closure.confidence:.2f} — closure wins, "
                            f"no re-run",
                            context={
                                "gate_reason": str(_gate_verdict.reason)[:400],
                                "closure_checks_run": int(_closure.checks_run),
                                "closure_checks_passed": int(
                                    _closure.checks_passed),
                                "closure_confidence": float(
                                    _closure.confidence),
                                "closure_summary": str(_closure.summary)[:300],
                            },
                            loop_id=getattr(loop_result, "loop_id", None),
                        )
                    except Exception:
                        pass
                elif _gate_verdict.escalate and loop_result.status == "done":
                    _cur_tier = model or getattr(adapter, "model_key", None)
                    if not isinstance(_cur_tier, str):
                        _cur_tier = "mid"  # execution floor since 2026-07-21
                    _next_tier = next_model_tier(_cur_tier)
                    _action = _cfg.get("quality_gate_action", "escalate").strip().lower()
                    _gate_note = f"\n\n⚠️ Quality gate: ESCALATE — {_gate_verdict.reason}"
                    if verbose:
                        print(f"[maro:{handle_id}] quality gate: ESCALATE → {_next_tier} ({_gate_verdict.reason})",
                              file=sys.stderr, flush=True)
                    if _action == "escalate" and _next_tier:
                        if verbose:
                            print(f"[maro:{handle_id}] re-running with model={_next_tier}",
                                  file=sys.stderr, flush=True)
                        # Deferred learning drains early here: the retry's
                        # decompose recalls lessons from the loop it is
                        # retrying, so they must exist before it plans.
                        _drain_deferred_learning(handle_id)
                        _escalated_adapter = build_adapter(model=_next_tier)
                        _pre_escalation_loop = loop_result
                        _pre_escalation_loop_id = getattr(loop_result, "loop_id", None)
                        _escalated_project = (
                            project or getattr(loop_result, "project", "") or ""
                        ) + "-escalated"
                        # Preserve the normal run contract (measurement
                        # provenance, handle identity, deferred learning,
                        # callback/context, repo fence) while changing only
                        # the fields intrinsic to an escalation retry.
                        _escalate_kwargs = dict(_loop_kwargs)
                        _escalate_kwargs.update({
                            "project": _escalated_project,
                            "model": _next_tier,
                            "adapter": _escalated_adapter,
                            "dry_run": False,
                            "verbose": verbose,
                            "loop_reason": "quality_gate_escalate",
                            "parent_loop_id": _pre_escalation_loop_id,
                        })
                        loop_result = run_agent_loop(message, **_escalate_kwargs)
                        elapsed = int((time.monotonic() - started_at) * 1000)
                        if getattr(loop_result, "loop_id", ""):
                            _run_loop_ids.append(loop_result.loop_id)
                        _post_audit_failed = False
                        # Escalation only fires from a "done" parent, so a
                        # re-run that dies (budget breaker, stuck, empty) must
                        # not replace delivered work with its own corpse —
                        # ead11c12-nimble-shore (2026-07-29) shipped
                        # "[no output] / Stuck" to the dispatch lane while two
                        # complete memos sat on disk. The parent stays the
                        # shipped version unless the re-run finished with
                        # results of its own.
                        _rerun_shipped = (
                            getattr(loop_result, "status", "") == "done"
                            and any(
                                getattr(s, "status", "") == "done"
                                and getattr(s, "result", None)
                                for s in (loop_result.steps or [])
                            )
                        )
                        if _rerun_shipped:
                            _gate_note = f"\n\n✅ Quality gate escalated to {_next_tier} — re-run complete."
                            _contested_claims = []  # fresh run — don't append stale claims
                        else:
                            _dead_reason = (
                                getattr(loop_result, "stuck_reason", None)
                                or f"status={getattr(loop_result, 'status', '?')}"
                            )
                            _dead_loop_id = getattr(loop_result, "loop_id", "") or ""
                            _dead_rerun_loop = loop_result
                            loop_result = _pre_escalation_loop
                            # Parent ships → its contested claims still apply;
                            # its closure verdict (stamped before the gate ran)
                            # stays the run's verdict — no post-escalate
                            # re-stamp from the dead re-run's state.
                            _gate_note = (
                                f"\n\n⚠️ Quality gate escalated to {_next_tier}, "
                                f"but the re-run did not complete "
                                f"({_dead_reason}) — shipping the original "
                                f"loop's output; its closure verdict stands."
                            )
                            log.info(
                                "handle: escalated re-run %s died (%s) — "
                                "reverting to pre-escalation loop %s for delivery",
                                _dead_loop_id, _dead_reason,
                                _pre_escalation_loop_id,
                            )

                        # Re-run closure on the escalated loop. Without this, only the
                        # initial loop's closure verdict shows up in the captain's log
                        # — the escalated re-run (which is the version we ship) would
                        # have no closure record at all (2026-04-26 audit finding).
                        # Only when the re-run is actually the shipped version:
                        # on revert the parent's verdict already stands and a
                        # fresh judgment of the dead re-run would re-stamp the
                        # run from work we are not delivering.
                        if not dry_run and _rerun_shipped:
                            try:
                                from director import evaluate_closure as _evaluate_post_escalate
                                from introspect import diagnose_loop as _diag_post_escalate
                                _post_diag = None
                                try:
                                    if getattr(loop_result, "loop_id", ""):
                                        _post_diag = _diag_post_escalate(
                                            loop_result.loop_id,
                                            project=(project or loop_result.project or ""),
                                        )
                                except Exception:
                                    _post_diag = None
                                # Evidence-only read: the escalated re-run is
                                # the version we ship — its verdict feeds
                                # stamping/demotion, never another restart.
                                _post_closure = _evaluate_post_escalate(
                                    message,
                                    loop_result.steps,
                                    _escalated_adapter,
                                    workspace_path=repo_path or "",
                                    channel=channel,
                                    scope=_scope,
                                    resolved_intent=_resolved_intent,
                                    diagnosis=_post_diag,
                                    loop_id=getattr(loop_result, "loop_id", "") or "",
                                    project=project or getattr(loop_result, "project", "") or "",
                                ).closure_verdict
                                if (
                                    _post_closure is not None
                                    and _post_closure.checks_run > 0
                                ):
                                    _post_judged = getattr(_post_closure, "judged", True)
                                    _post_source = (
                                        "closure" if _post_judged
                                        else "closure_unverifiable"
                                    )
                                    _post_achieved = (
                                        bool(_post_closure.complete)
                                        if _post_judged else None
                                    )
                                    from audit_policy import persist_delivered_outcome_verdict
                                    _post_audit = persist_delivered_outcome_verdict(
                                        getattr(loop_result, "loop_id", "") or "",
                                        goal_achieved=_post_achieved,
                                        goal_verdict_source=_post_source,
                                        goal_verdict_confidence=float(
                                            _post_closure.confidence
                                        ),
                                        loop_ids=_run_loop_ids,
                                        channel=channel,
                                    )
                                    if not _post_audit.learning_allowed:
                                        _post_audit_failed = True
                                        _audit_warnings.append(_post_audit.warning)
                                        _audit_failed_loop_ids.add(
                                            getattr(loop_result, "loop_id", "") or "")
                                    try:
                                        from runs import stamp_run_verdict as _srv_post
                                        _srv_post(
                                            goal_achieved=_post_achieved,
                                            source=_post_source,
                                            confidence=float(_post_closure.confidence),
                                            summary=str(_post_closure.summary),
                                            downgrade_reason=str(getattr(
                                                _post_closure,
                                                "downgrade_reason", "") or ""),
                                        )
                                    except Exception:
                                        pass
                                    if (
                                        _post_judged
                                        and not _post_closure.complete
                                        and _post_closure.confidence >= 0.7
                                        and loop_result.status == "done"
                                    ):
                                        loop_result.status = "incomplete"
                                        if loop_result.stuck_reason is None:
                                            loop_result.stuck_reason = (
                                                "post-escalate closure verification: "
                                                f"{str(_post_closure.summary)[:300]}"
                                            )
                                        _stamp_stop_on_demotion(
                                            loop_result, "lost-the-plot",
                                            "post-escalate closure contradicts done "
                                            f"(conf={_post_closure.confidence:.2f}): "
                                            f"{str(_post_closure.summary)[:300]}",
                                        )
                                if verbose and _post_closure is not None:
                                    print(
                                        f"[maro:{handle_id}] post-escalate closure: "
                                        f"complete={_post_closure.complete} "
                                        f"confidence={_post_closure.confidence:.2f}",
                                        file=sys.stderr, flush=True,
                                    )
                            except Exception as _post_exc:
                                log.debug("post-escalate closure failed: %s", _post_exc)
                                # R2-4 (adversarial review 2026-08-06): the
                                # shipped re-run must not inherit the parent's
                                # verdict — that judged work we reverted. A
                                # crashed post-escalate judge stamps
                                # closure_error with replace-semantics
                                # (goal_achieved=None removes the stale
                                # boolean); the ledger row for the shipped
                                # loop gets the same why.
                                if _rerun_shipped:
                                    _pe_err = (f"post-escalate "
                                               f"{type(_post_exc).__name__}: "
                                               f"{_post_exc}")
                                    try:
                                        from runs import stamp_run_verdict as _srv_err
                                        _srv_err(
                                            goal_achieved=None,
                                            source="closure_error",
                                            confidence=0.0,
                                            summary=_pe_err,
                                        )
                                    except Exception:
                                        pass
                                    try:
                                        from audit_policy import (
                                            persist_delivered_outcome_verdict as _pdov_err)
                                        _pdov_err(
                                            getattr(loop_result, "loop_id", "") or "",
                                            goal_achieved=None,
                                            goal_verdict_source="closure_error",
                                            goal_verdict_confidence=0.0,
                                            loop_ids=_run_loop_ids,
                                            channel=channel,
                                        )
                                    except Exception:
                                        pass
                        # The copied loop contract intentionally keeps
                        # defer_learning=True. Complete that contract after
                        # the escalated verdict is available (or unjudged),
                        # otherwise the shipped retry never extracts lessons.
                        # On revert the contract belongs to the dead re-run
                        # (the reverted parent was already drained pre-retry)
                        # — and a died-on-budget loop is a real lesson source.
                        if not _post_audit_failed:
                            try:
                                from loop_finalize import finalize_deferred_learning as _fdl_post
                                _dl_post_result = (
                                    loop_result if _rerun_shipped
                                    else _dead_rerun_loop
                                )
                                _dl_post_adapter = _escalated_adapter
                                _dl_post_project = _escalated_project
                                # Disputed-audit holdout, escalation lane
                                # (adversarial review 2026-08-09: the
                                # normal-path skip set never reaches this
                                # callback).
                                _dl_post_skip = None
                                try:
                                    if (getattr(_post_closure,
                                                "verdict_audit", {}) or {}
                                            ).get("disputed"):
                                        _dl_post_skip = [
                                            getattr(_dl_post_result,
                                                    "loop_id", "") or ""]
                                except Exception:
                                    _dl_post_skip = None
                                _defer_learning_post_notify(
                                    handle_id,
                                    lambda: _fdl_post(
                                        _dl_post_result,
                                        adapter=_dl_post_adapter,
                                        project=_dl_post_project,
                                        dry_run=False,
                                        verbose=verbose,
                                        skip_loop_ids=_dl_post_skip,
                                    ))
                            except Exception as _post_dl_exc:
                                log.warning(
                                    "post-escalate deferred learning failed for loop %s: %s",
                                    getattr(loop_result, "loop_id", ""), _post_dl_exc,
                                )
            except Exception:
                pass  # gate never blocks delivery of results

        # Loop ids into run metadata: the join key from a run to its
        # step-costs entries (cost-per-run). Written once, after every path
        # that can spawn another loop (director restart, closure restart,
        # quality-gate escalate) has settled. Burn-in adjudication 2026-07-02:
        # cost-per-goal was unrecoverable without this.
        if _run_loop_ids:
            try:
                from runs import write_metadata as _wm_loops
                from runs import current_run_dir as _crd_loops
                _rd_loops = _crd_loops()
                if _rd_loops is not None:
                    _wm_loops(_rd_loops, handle_id=handle_id, prompt=_raw_input,
                              extra={"loop_ids": list(_run_loop_ids)})
            except Exception:
                pass

        # Build extra annotations from quality gate / pre-flight
        _extra = ""
        _pf = getattr(loop_result, "pre_flight_review", None)
        if _pf and getattr(_pf, "scope", None) == "wide":
            _extra += f"\n\n⚠️ Pre-flight: scope=wide — {_pf.scope_note}"
        if _contested_claims:
            _claims_text = "\n".join(
                f"- [{c.get('verdict', '?')}] {c.get('claim', '')} — {c.get('reason', '')}"
                for c in _contested_claims
            )
            _extra += f"\n\n---\n\n**⚠️ Adversarial review — contested claims:**\n{_claims_text}"
        if _gate_note:
            _extra += _gate_note
        if _audit_warnings:
            _extra += "\n\n---\n\n**⚠️ Audit incomplete:**\n" + "\n".join(
                f"- {warning}" for warning in dict.fromkeys(_audit_warnings)
            )

        return _loop_result_to_handle(
            loop_result, handle_id=handle_id, message=message,
            confidence=confidence, reason=reason, started_at=started_at,
            project=project, extra_text=_extra,
        )


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def _loop_result_to_handle(
    loop_result,
    *,
    handle_id: str,
    message: str,
    confidence: float,
    reason: str,
    started_at: float,
    project: Optional[str] = None,
    reason_suffix: str = "",
    extra_text: str = "",
) -> "HandleResult":
    """Convert a LoopResult into a HandleResult with formatted step text.

    Deduplicates the pipeline/team/direct/default AGENDA paths that all
    format steps identically.
    """
    elapsed = int((time.monotonic() - started_at) * 1000)
    result_parts = []
    # Number by position, not s.index — index is the NEXT.md item index and
    # is -1 for injected/parallel-batch steps (the "Step -1" of BACKLOG #2).
    for _pos, s in enumerate((s for s in loop_result.steps
                              if s.status == "done" and s.result), 1):
        result_parts.append(f"**Step {_pos}: {s.text}**\n{s.result}")
    result_text = "\n\n---\n\n".join(result_parts) if result_parts else "[no output]"
    if loop_result.status == "stuck":
        result_text += f"\n\n⚠️ Stuck: {loop_result.stuck_reason}"
    if extra_text:
        result_text += extra_text
    _class_reason = reason + reason_suffix if reason_suffix else reason
    return HandleResult(
        handle_id=handle_id,
        lane="agenda",
        lane_confidence=confidence,
        classification_reason=_class_reason,
        message=message,
        status=loop_result.status,
        result=result_text,
        project=loop_result.project or project or "",
        loop_result=loop_result,
        tokens_in=loop_result.total_tokens_in,
        tokens_out=loop_result.total_tokens_out,
        elapsed_ms=elapsed,
        artifact_path=getattr(loop_result, "log_path", None),
    )


def _write_now_artifact(
    handle_id: str,
    message: str,
    result: str,
    elapsed_ms: int,
    suffix: str = "",
) -> Optional[str]:
    """Write the NOW-lane result into the run dir's artifact/ subtree.

    `suffix` distinguishes same-handle attempts (the artifact-retry rung
    passes "-retry") — attempt 1's artifact must never be overwritten
    (data-retention rule)."""
    try:
        # The run dir is created at the top of every handle() call; its
        # artifact/ subtree is where run products belong. If the current-run
        # pointer is missing, derive the same path from the handle_id.
        from runs import current_run_dir as _crd_art
        from runs import run_dir as _run_dir_art
        _rd = _crd_art()
        if _rd is None:
            _rd = _run_dir_art(handle_id)
        artifacts_dir = _rd / "artifact"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"now-{handle_id}{suffix}.json"
        path = artifacts_dir / fname
        payload = {
            "handle_id": handle_id,
            "lane": "now",
            "message": message,
            "result": result,
            "elapsed_ms": elapsed_ms,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Task store routing — escalation and continuation consumers
# ---------------------------------------------------------------------------

def _context_firewall(reason: str, depth: int, cap: int = 600) -> str:
    """Filter a continuation/escalation reason blob for passing to a sub-loop.

    At depth ≤ 1: pass the full reason (capped) — the first continuation should
    have full context of what came before.

    At depth ≥ 2: strip accomplished steps (they're done and irrelevant to the
    sub-loop's planner). Extract only:
      - Original goal (one line)
      - Remaining steps (the work that actually needs to happen)
    This prevents context contamination and token bloat at depth 3, 4, etc.

    Always caps at `cap` characters.
    """
    if depth <= 1:
        return reason[:cap]

    # Deep continuation: extract only what matters to the next executor
    lines = reason.split("\n")
    goal_line = ""
    remaining_lines: list = []
    in_remaining = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Original goal:"):
            goal_line = stripped
        elif stripped == "Remaining:" or stripped.startswith("Remaining:"):
            in_remaining = True
            remaining_lines.append(line)
        elif in_remaining:
            if stripped.startswith("Accomplished:") or stripped.startswith("ESCALATION"):
                in_remaining = False
            else:
                remaining_lines.append(line)

    if goal_line or remaining_lines:
        filtered = "\n".join(filter(None, [goal_line] + remaining_lines)).strip()
        return filtered[:cap]

    # Fallback: just cap it
    return reason[:cap]


def _parse_continuation_reason(reason: str):
    """Extract (goal, context) from a loop_continuation or loop_escalation reason string.

    Recognized prefixes and their formats:

    "CONTINUATION of: <goal>\\n\\nPass N..."
        → goal=<goal>, context=remainder

    "NARROWED from escalation <id>:\\n\\n<revised goal>\\n\\n..."
        → goal=<revised goal> (second line block), context=full reason

    "ESCALATION — task has been through..."
        → goal extracted from "Original goal: <goal>" line, context=full reason

    Falls back to (reason, "") for unrecognized formats.
    """
    if reason.startswith("CONTINUATION of:"):
        parts = reason.split("\n", 1)
        goal = parts[0].replace("CONTINUATION of:", "").strip()
        context = parts[1].strip() if len(parts) > 1 else ""
        return goal, context

    if reason.startswith("NARROWED from escalation"):
        # Format: "NARROWED from escalation <id>:\n\n<revised goal>\n\n..."
        # The revised goal is the first non-empty line after the prefix line.
        lines = reason.split("\n")
        for line in lines[1:]:
            stripped = line.strip()
            if stripped:
                return stripped, reason
        return reason, ""

    if reason.startswith("ESCALATION —"):
        # Format includes "Original goal: <goal>" line
        for line in reason.split("\n"):
            if line.startswith("Original goal:"):
                goal = line.replace("Original goal:", "").strip()
                return goal, reason
        return reason, ""

    # Fallback: treat the whole reason as the goal
    return reason, ""


def _navigator_act_dispatch(
    decision, goal: str, *, job_id: str, source: str
):
    """Dispatch-class cutover: turn a navigator decision into a dispatch
    outcome, or None to proceed with the normal pipeline.

    Cutover is per-move, not per-class — the live data forced the split.
    `navigator.act_moves` (default ["escalate"]) is the set allowed to act:
    escalate earned it first (every adjudicated divergence navigator-right,
    and it defers to a human — it cannot assert a wrong resolution), so
    flipping `act_dispatch` on gets escalate by default. close is opt-in on
    top (add it to act_moves) because it asserts a goal is resolved WITHOUT
    running it; as of 2026-06-12 its only evidence is synthetic probes.
    extend/fork/collate have no dispatch machinery and fall through to
    execute. Acting requires confidence >= navigator.act_confidence_floor
    (default 0.9); below the floor the pipeline keeps the wheel. Never raises.

    DEFAULT ON since 2026-07-08 (Jeremy's flip; this box ran it live since
    2026-06-21 — 14/14 execute agreement, zero bad escalates). Escalate-only
    by default via act_moves; note the decide call this implies costs one
    cheap-tier model call per autonomous dispatch (see navigator_shadow).
    Set navigator.act_dispatch: false to return to shadow-only.
    """
    if decision is None:
        return None
    try:
        try:
            from config import get as _cfg_get
            if not bool(_cfg_get("navigator.act_dispatch", True)):
                return None
            _floor = float(_cfg_get("navigator.act_confidence_floor", 0.9))
            _act_moves = set(_cfg_get("navigator.act_moves", ["escalate"]) or [])
        except Exception:
            return None
        move = getattr(decision, "move", "")
        conf = float(getattr(decision, "confidence", 0.0) or 0.0)
        reasoning = str(getattr(decision, "reasoning", ""))
        payload = dict(getattr(decision, "payload", {}) or {})
        if move not in _act_moves or move not in ("escalate", "close") or conf < _floor:
            return None
        # Synthesized idunno-chain escalates never act: conf 1.0 is synthetic
        # and the chain exhausts on adapter outages too — an unreachable
        # navigator must fail open to the pipeline, not stop the line.
        if payload.get("escalated_via") == "idunno_chain":
            return None

        if move == "escalate":
            status = "stuck"
            classification = "navigator_escalate"
            result = (
                f"navigator escalated at dispatch (conf {conf:.2f}): {reasoning}"
            )
        else:  # close
            closure = str(payload.get("closure", "")) or "abandoned"
            status = "done" if closure == "delivered" else "incomplete"
            classification = "navigator_close"
            result = (
                f"navigator closed at dispatch ({closure}, conf {conf:.2f}): "
                f"{reasoning}"
            )

        log.warning("handle_task navigator %s job_id=%s: %s",
                    classification, job_id, result[:200])
        try:
            from captains_log import log_event, NAVIGATOR_ACTED
            log_event(
                NAVIGATOR_ACTED,
                subject="navigator",
                summary=f"dispatch: {move} acted (conf {conf:.2f}) — run prevented",
                context={
                    "point": "dispatch",
                    "move": move,
                    "confidence": conf,
                    "reasoning": reasoning[:500],
                    "goal_preview": goal[:200],
                    "job_id": job_id,
                    "source": source,
                    "status": status,
                },
            )
        except Exception:
            pass
        if move == "escalate":
            # Deferring to a human only works if a human finds out. No run-dir
            # exists (the run was prevented), so this is the only signal out.
            try:
                from notify import emit as _notify_emit
                # §9.6: single-chasm ask. No loop ran, so there is no
                # diagnosis to key a family-ROI line on — omitted, not faked.
                try:
                    from escalation_context import decision_line
                    _decision_ask = decision_line(
                        "dispatch", reason=reasoning or result)
                except Exception:
                    _decision_ask = ""
                _notify_emit("escalation", {
                    "handle_id": "",
                    "goal": goal,
                    "status": status,
                    "summary": result,
                    "reason": reasoning,
                    "job_id": job_id,
                    "source": source,
                    "point": "dispatch",
                    "decision": _decision_ask,
                })
            except Exception:
                pass
        return HandleResult(
            handle_id="",
            lane="agenda",
            lane_confidence=1.0,
            classification_reason=classification,
            message=goal,
            status=status,
            result=result,
        )
    except Exception as _act_exc:
        log.debug("navigator act_dispatch fell through: %s", _act_exc)
        return None


# ---------------------------------------------------------------------------
# Task-store queue consumer + goal enqueue — moved to handle_queue.py
# ---------------------------------------------------------------------------
from handle_queue import handle_task, drain_task_store, enqueue_goal, enqueue_goals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="maro-handle", description="Maro's unified request handler")
    parser.add_argument("message", nargs="+", help="The request to handle")
    parser.add_argument("--project", "-p", help="Project slug for AGENDA work")
    parser.add_argument("--repo", help="Path to target repo (auto-injects stack context into decompose)")
    parser.add_argument("--model", "-m", help="LLM model string")
    parser.add_argument("--lane", choices=["now", "agenda"], help="Force a specific lane")
    parser.add_argument("--persona", help="Force a specific persona by name (same as a 'persona:<name>:' prefix in the message; unknown names fall back to auto-selection)")
    from ancestry import MEASUREMENT_CLASSES
    parser.add_argument("--measurement-class", choices=MEASUREMENT_CLASSES, default="organic", help="Success-measurement cohort provenance (default: organic)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument(
        "--wait", type=float, default=None, metavar="SECONDS",
        help="If the project is busy, poll for the slot up to this many "
             "seconds instead of refusing immediately (interactive use)",
    )

    args = parser.parse_args(argv)
    msg = " ".join(args.message)
    if args.wait is not None:
        # Env override reaches every nested run_agent_loop without threading
        # a parameter through handle's layers.
        os.environ["MARO_ADMISSION_WAIT_S"] = str(max(0.0, args.wait))

    try:
        result = handle(
            msg,
            project=args.project,
            repo_path=args.repo or "",
            model=args.model,
            force_lane=args.lane,
            dry_run=args.dry_run,
            verbose=args.verbose,
            persona=args.persona,
            measurement_class=args.measurement_class,
        )
    except RuntimeError as e:
        # build_adapter() raises RuntimeError with an actionable, human-facing
        # message (missing API key / backend) — a raw traceback on a new
        # user's first command reads as "broken," not "needs config."
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Per-run finalize (metadata status, log slice, repo bundle) happens in
    # handle() itself for every caller as of 2026-06-11. The CLI only clears
    # the current-run context; programmatic test callers that care about
    # isolation can call set_current_run_dir(None) themselves.
    try:
        from runs import set_current_run_dir as _clear_run
        _clear_run(None)
    except Exception:
        pass

    print(result.format(mode=args.format))
    return 0 if result.status == "done" else 1


def enqueue_main(argv=None):
    """CLI entry point for ``maro-enqueue``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="maro-enqueue",
        description="Enqueue goals for the director to process sequentially.",
    )
    parser.add_argument("goals", nargs="+", help="Goal(s) to enqueue. Each arg is one goal.")
    parser.add_argument(
        "--parallel", action="store_true",
        help="Allow goals to run in parallel (default: sequential, each waits for previous)",
    )
    parser.add_argument(
        "--drain", action="store_true",
        help="After enqueueing, immediately drain the queue (run goals now).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)
    job_ids = enqueue_goals(args.goals, sequential=not args.parallel)

    for i, (goal, jid) in enumerate(zip(args.goals, job_ids)):
        print(f"  [{i+1}] {jid} — {goal[:80]}")
    print(f"\n{len(job_ids)} goal(s) queued ({'sequential' if not args.parallel else 'parallel'})")

    if args.drain:
        print("\nDraining queue...")
        n = drain_task_store(verbose=args.verbose, max_tasks=len(job_ids),
                             job_ids=set(job_ids))
        print(f"Processed {n} task(s)")

    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
