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

log = logging.getLogger("maro.handle")
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Magic prefix registry
# ---------------------------------------------------------------------------
# Each prefix mutates execution without changing the goal text.
# The registry defines all prefixes centrally — adding a new prefix requires
# one entry here, not scattered startswith() chains scattered through handle().

@dataclass
class _PrefixRule:
    prefix: str           # exact lowercase prefix string (including trailing : or space)
    flag: str             # attribute name on _PrefixResult to set True
    model_tier: str = ""  # if non-empty, override model to this tier (cheap/mid/power)
    max_steps: int = 0    # if > 0, override max_steps to this value
    persona: str = ""     # if non-empty, force this persona name (overrides persona_for_goal)


@dataclass
class _PrefixResult:
    """Result of applying the prefix registry to a message."""
    message: str          # cleaned message with all prefixes stripped
    model_tier: str = ""  # model tier override (empty = no override)
    max_steps: int = 0    # max_steps override (0 = no override)
    thin_mode: bool = False
    btw_mode: bool = False
    ultraplan_mode: bool = False
    direct_mode: bool = False
    ralph_mode: bool = False
    pipeline_mode: bool = False
    strict_mode: bool = False
    team_mode: bool = False
    forced_persona: str = ""   # if non-empty, override persona_for_goal selection


_PREFIX_REGISTRY: List[_PrefixRule] = [
    # effort: overrides model tier; exclusive per level (first match wins)
    _PrefixRule("effort:low",   flag="",           model_tier="cheap"),
    _PrefixRule("effort:mid",   flag="",           model_tier="mid"),
    _PrefixRule("effort:high",  flag="",           model_tier="power"),
    # execution mode modifiers
    _PrefixRule("mode:thin",    flag="thin_mode"),
    _PrefixRule("btw:",         flag="btw_mode"),
    _PrefixRule("ultraplan:",   flag="ultraplan_mode", model_tier="power", max_steps=12),
    _PrefixRule("direct:",      flag="direct_mode"),
    # quality / behavior modifiers (non-exclusive — can stack)
    _PrefixRule("ralph:",       flag="ralph_mode"),
    _PrefixRule("verify:",      flag="ralph_mode"),   # alias for ralph:
    _PrefixRule("pipeline:",    flag="pipeline_mode"),
    _PrefixRule("strict:",      flag="strict_mode"),
    _PrefixRule("team:",        flag="team_mode",  model_tier="mid"),
    # forced persona shortcuts
    _PrefixRule("garrytan:",    flag="",           model_tier="power", persona="garrytan"),
]


def _apply_prefixes(message: str) -> _PrefixResult:
    """Strip all recognized magic prefixes from `message` and return a _PrefixResult.

    Prefixes are matched case-insensitively and stripped in registry order.
    Multiple prefixes can stack (e.g. "strict: pipeline: do the thing").
    The effort: group is exclusive (first level wins); all others accumulate.

    This replaces nine separate startswith() blocks scattered through handle().
    """
    result = _PrefixResult(message=message)
    changed = True
    while changed:
        changed = False
        lower = result.message.lower()
        for rule in _PREFIX_REGISTRY:
            if lower.startswith(rule.prefix):
                result.message = result.message[len(rule.prefix):].lstrip()
                if rule.flag:
                    setattr(result, rule.flag, True)
                if rule.model_tier:
                    if result.model_tier and result.model_tier != rule.model_tier:
                        import logging as _logging
                        _logging.getLogger("maro.handle").warning(
                            "conflicting model tiers: %r already set, ignoring %r (from prefix %r)",
                            result.model_tier, rule.model_tier, rule.prefix,
                        )
                    elif not result.model_tier:
                        result.model_tier = rule.model_tier
                if rule.max_steps:
                    result.max_steps = rule.max_steps
                if rule.persona and not result.forced_persona:
                    result.forced_persona = rule.persona
                changed = True
                lower = result.message.lower()  # re-check after strip
                break  # restart registry scan after each match
    return result


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

_BTW_SYSTEM = """You are an autonomous agent surfacing a non-blocking observation.
Note what you observe, briefly and specifically. Do not attempt to fix or solve anything.
Keep it to 1–3 sentences max. Format: one sentence per observation, plain text.
This is a side-note, not a task result.
"""


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
    """
    import re
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

    return False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _run_now(
    message: str,
    handle_id: str,
    adapter,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Execute a NOW-lane task: single LLM call, returns result dict."""
    from llm import LLMMessage

    if verbose:
        print(f"[maro:{handle_id}] NOW lane — executing...", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _NOW_SYSTEM),
                LLMMessage("user", message),
            ],
            max_tokens=2048,
            temperature=0.4,
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
    '{"fulfilled": true} or {"fulfilled": false}. '
    "fulfilled=false when the response states the task could not be done, is "
    "incomplete or impossible, or only explains why it failed. "
    "fulfilled=true when the response delivers what was asked."
)


# ---------------------------------------------------------------------------
# Provenance guard (deterministic done != achieved check)
# ---------------------------------------------------------------------------
# A text-only verdict (the LLM judge below, or the local validator) can't see
# whether a claimed input was actually read or a claimed output actually landed.
# Live find (shadow-eval n=42, 2026-06-24): "save the listing to
# artifacts/skills-listing.txt" saved to a DIFFERENT path and narrated success —
# local PASSed at conf 1.00, paid FAILed. Three deterministic checks:
#   * OUTPUT, dir-qualified  ("save … to artifacts/X")  → STRICT: must be at that
#     exact path. The user said *where*; honor it.
#   * OUTPUT, bare filename   ("save … to report.md")    → LENIENT: the basename
#     must exist *somewhere* reasonable (location ambiguous → don't punish it).
#   * INPUT, dir-qualified    ("read /nonexistent/X")     → STRICT: the input must
#     exist (you can't read a file that isn't there). Remote (URLs) and transient
#     (/tmp, scratchpad) inputs are skipped — can't/shouldn't verify them.
# Same provenance-blindness root as the fabricated-input recovery guard; this is
# the verdict-layer net for fabrication that reaches "done" without blocking.

_OUTPUT_CLAIM_RE = re.compile(
    r"\b(?:sav\w*|writ\w*|creat\w*|output\w*|stor\w*|export\w*|generat\w*|dump\w*)\b"
    r"[^.\n]*?\b(?:to|into|at|as)\s+[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
_INPUT_CLAIM_RE = re.compile(
    r"\b(?:read|load|open|pars\w*|fetch|import|ingest)\w*\s+"
    r"(?:the\s+|a\s+|file\s+|from\s+|contents?\s+of\s+|in\s+)*"
    r"[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_REMOTE_PREFIXES = ("http://", "https://", "ftp://", "s3://", "gs://", "git@", "ssh://")
_TRANSIENT_SEGMENTS = ("/tmp/", "scratchpad", "/dev/", "/proc/", "/var/tmp/")


def _clean_path_token(tok: str) -> str:
    return tok.strip().strip("`'\"()").rstrip(".,;:")


def _claimed_output_paths(goal: str) -> List[str]:
    """Dir-qualified output paths the goal asks to be written (user said *where*)."""
    out: List[str] = []
    for m in _OUTPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        if "/" in tok and tok not in ("/", "./", "../") and not tok.endswith("/"):
            out.append(tok)
    return out


def _claimed_output_bare(goal: str) -> List[str]:
    """Bare output filenames (no dir, has an extension) — user said only *what*."""
    out: List[str] = []
    for m in _OUTPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        if "/" not in tok and _EXT_RE.search(tok):
            out.append(tok)
    return out


def _claimed_input_paths(goal: str) -> List[str]:
    """Dir-qualified, local, non-transient input paths the goal asks to read."""
    out: List[str] = []
    for m in _INPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        low = tok.lower()
        if low.startswith(_REMOTE_PREFIXES):
            continue                      # remote — can't cheaply verify
        if "/" not in tok:
            continue                      # bare name — ambiguous
        if any(seg in low for seg in _TRANSIENT_SEGMENTS):
            continue                      # may be gone by verdict time
        out.append(tok)
    return out


def _output_provenance_bases() -> List[Path]:
    """Candidate base dirs a relative path could legitimately resolve under.
    Generous on purpose — a false demotion is worse than a missed one."""
    bases: List[Path] = []
    for fn in (
        lambda: Path.cwd(),
        lambda: Path(__file__).resolve().parent.parent,
    ):
        try:
            bases.append(fn())
        except Exception:
            pass
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd:
            bases.append(Path(rd))
    except Exception:
        pass
    try:
        from config import workspace_root
        ws = Path(workspace_root())
        bases.extend([ws, ws / "output"])
    except Exception:
        pass
    return bases


def _exists_at_exact(rel: str, bases: List[Path]) -> bool:
    """True if a (possibly relative) path resolves to an existing file. For
    relative paths, also checks one level under workspace/projects/<slug>/."""
    p = Path(rel)
    if p.is_absolute():
        return p.exists()
    if any((b / rel).exists() for b in bases):
        return True
    try:
        from config import workspace_root
        ws_projects = Path(workspace_root()) / "projects"
        if ws_projects.is_dir():
            return any((d / rel).exists() for d in ws_projects.glob("*") if d.is_dir())
    except Exception:
        pass
    return False


def _bare_search_dirs() -> List[Path]:
    """Small, bounded landing spots to scan for a bare output basename."""
    dirs: List[Path] = []
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd:
            dirs.append(Path(rd))
    except Exception:
        pass
    try:
        from config import workspace_root
        ws = Path(workspace_root())
        dirs.extend([ws / "output", ws / "projects"])
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


def _exists_bare_anywhere(name: str, bases: List[Path]) -> bool:
    """True if a bare basename exists under any base (direct) or any landing
    spot (one or two levels deep — where run/project/output files land)."""
    if any((b / name).exists() for b in bases):
        return True
    for d in _bare_search_dirs():
        try:
            if (d / name).exists():
                return True
            if any(d.glob(f"*/{name}")) or any(d.glob(f"*/*/{name}")):
                return True
        except Exception:
            pass
    return False


def _missing_claimed_outputs(goal: str) -> List[str]:
    """Dir-qualified output paths named in the goal that don't exist at that
    exact location. Empty = nothing claimed, or everything landed. Fails open."""
    claimed = _claimed_output_paths(goal)
    if not claimed:
        return []
    bases = _output_provenance_bases()
    return [rel for rel in claimed if not _exists_at_exact(rel, bases)]


def _missing_output_bare(goal: str) -> List[str]:
    """Bare output filenames whose basename exists nowhere reasonable (the
    output was never produced). Lenient: location is not part of the contract."""
    bare = _claimed_output_bare(goal)
    if not bare:
        return []
    bases = _output_provenance_bases()
    return [name for name in bare if not _exists_bare_anywhere(name, bases)]


def _missing_claimed_inputs(goal: str) -> List[str]:
    """Dir-qualified local input paths the goal asks to read that don't exist —
    you can't legitimately read a file that isn't there. Fails open."""
    claimed = _claimed_input_paths(goal)
    if not claimed:
        return []
    bases = _output_provenance_bases()
    return [rel for rel in claimed if not _exists_at_exact(rel, bases)]


# --- Tool-evidence layer ----------------------------------------------------
# The three checks above scan the GOAL text. This one scans the RESULT text for
# paths the run CLAIMS it wrote ("saved to X", "wrote report.md") and demotes
# unless that path exists AND was modified during this run's wall-clock window.
# The mtime gate is the actual evidence of a side effect: a pre-existing stale
# file with the right name does NOT prove the run wrote it. This is what catches
# fabrication when the GOAL named no path (the *claim* names it) and the n=42
# "narrated success, saved elsewhere/nowhere" case the text-only judge missed.
# Window is intentionally generous (buffer) — a missed fabrication is cheaper
# than a false demotion (fail-open).
# Residual it CANNOT catch (no execution transcript is available from `claude -p
# --output-format json` — only the final text): a run that fabricates a result
# with no file claim at all ("ran the tests: 142 passed" writing nothing). That
# needs tool-call evidence the backend doesn't expose. Documented, not solved.
_WINDOW_BUFFER_SECS = 120.0


def _run_window_start(elapsed_ms) -> Optional[float]:
    """Wall-clock instant before which a file mtime can't be evidence of THIS
    run: now - elapsed - buffer. None (skip the gate) when elapsed is unknown."""
    try:
        ems = float(elapsed_ms or 0)
        if ems <= 0:
            return None
        return time.time() - ems / 1000.0 - _WINDOW_BUFFER_SECS
    except Exception:
        return None


def _resolve_exact(rel: str, bases: List[Path]) -> Optional[Path]:
    """Like _exists_at_exact but returns the resolved existing Path (or None)."""
    p = Path(rel)
    if p.is_absolute():
        return p if p.exists() else None
    for b in bases:
        if (b / rel).exists():
            return b / rel
    try:
        from config import workspace_root
        ws_projects = Path(workspace_root()) / "projects"
        if ws_projects.is_dir():
            for d in ws_projects.glob("*"):
                if d.is_dir() and (d / rel).exists():
                    return d / rel
    except Exception:
        pass
    return None


def _resolve_bare(name: str, bases: List[Path]) -> Optional[Path]:
    """Like _exists_bare_anywhere but returns the resolved existing Path (or None)."""
    for b in bases:
        if (b / name).exists():
            return b / name
    for d in _bare_search_dirs():
        try:
            if (d / name).exists():
                return d / name
            for hit in list(d.glob(f"*/{name}")) + list(d.glob(f"*/*/{name}")):
                return hit
        except Exception:
            pass
    return None


def _is_fresh(path: Path, window_start: float) -> bool:
    """True if the file was modified at/after the run window start. Can't stat →
    True (fail open — never punish on an inability to check)."""
    try:
        return path.stat().st_mtime >= window_start
    except Exception:
        return True


def _result_claimed_outputs(text: str) -> List[str]:
    """Output paths a result narration claims to have written — dir-qualified
    and bare — minus remote/transient (can't have been written locally now)."""
    out: List[str] = []
    for rel in _claimed_output_paths(text) + _claimed_output_bare(text):
        low = rel.lower()
        if low.startswith(_REMOTE_PREFIXES):
            continue
        if any(seg in low for seg in _TRANSIENT_SEGMENTS):
            continue
        out.append(rel)
    return out


def _missing_or_stale_result_outputs(result_text: str, window_start: float) -> List[str]:
    """Output paths the RESULT claims to have written that either don't exist or
    predate the run window (so the run did not actually write them). Fails open."""
    claimed = _result_claimed_outputs(result_text or "")
    if not claimed:
        return []
    bases = _output_provenance_bases()
    flagged: List[str] = []
    for rel in claimed:
        if "/" in rel and not rel.endswith("/"):
            found = _resolve_exact(rel, bases)
        else:
            found = _resolve_bare(rel, bases)
        if found is None:
            flagged.append(f"{rel} (claimed written, not found)")
        elif not _is_fresh(found, window_start):
            flagged.append(f"{rel} (claimed written, but predates this run)")
    return flagged


def _provenance_missing(goal: str, *, result_text: Optional[str] = None,
                        window_start: Optional[float] = None) -> List[str]:
    """Aggregate deterministic provenance failures, honoring config flags. Scans
    the GOAL (output/input claims) and, when result_text + window_start are
    given, the RESULT (tool-evidence: claimed-written paths must exist and be
    fresh). Empty = nothing to flag. Never raises (fails open)."""
    missing: List[str] = []
    try:
        from config import get as _cfg_get
        if _cfg_get("validate.output_provenance", True):
            missing.extend(_missing_claimed_outputs(goal))
            missing.extend(_missing_output_bare(goal))
        if _cfg_get("validate.input_provenance", True):
            missing.extend(_missing_claimed_inputs(goal))
        if (result_text and window_start is not None
                and _cfg_get("validate.result_provenance", True)):
            missing.extend(_missing_or_stale_result_outputs(result_text, window_start))
    except Exception as exc:
        log.debug("provenance check skipped: %s", exc)
    return list(dict.fromkeys(missing))  # dedup, preserve order


def _verify_now_outcome(message: str, outcome: Dict[str, Any], adapter) -> Dict[str, Any]:
    """Demote an autonomous NOW 'done' to 'incomplete' when the response itself
    reports failure. Fails open — any error keeps the original status."""
    # Deterministic provenance guard, ahead of the text judge: if the goal named
    # an input that isn't on disk or an output that never landed, the goal is not
    # achieved regardless of how the response narrates it. Catches what the
    # text-only validator can't see; also saves the judge LLM call when it fires.
    _missing = _provenance_missing(
        message,
        result_text=str(outcome.get("result", "")),
        window_start=_run_window_start(outcome.get("elapsed_ms")),
    )
    if _missing:
        out = dict(outcome)
        out["status"] = "incomplete"
        out["goal_achieved"] = False
        out["provenance_missing"] = _missing
        log.info(
            "provenance: claimed input/output(s) not found %s — demoted to incomplete",
            _missing,
        )
        return out
    try:
        from llm import LLMMessage
        from llm_parse import extract_json
        resp = adapter.complete(
            [
                LLMMessage("system", _NOW_VERIFY_SYSTEM),
                LLMMessage(
                    "user",
                    f"Request:\n{message[:2000]}\n\n"
                    f"Response:\n{str(outcome.get('result', ''))[:2000]}",
                ),
            ],
            max_tokens=64,
            temperature=0.0,
        )
        verdict = extract_json(resp.content, dict, log_tag="now_verify")
        if verdict.get("fulfilled") is False:
            out = dict(outcome)
            out["status"] = "incomplete"
            out["goal_achieved"] = False
            out["tokens_in"] = outcome.get("tokens_in", 0) + getattr(resp, "input_tokens", 0)
            out["tokens_out"] = outcome.get("tokens_out", 0) + getattr(resp, "output_tokens", 0)
            log.info("now-verify: response reports non-fulfillment — status demoted to incomplete")
            return out
        if verdict.get("fulfilled") is True:
            out = dict(outcome)
            out["goal_achieved"] = True
            return out
    except Exception as exc:
        log.debug("now-verify failed open (keeping done): %s", exc)
    # Failed open or no clear verdict: goal achievement stays unverified
    # (no goal_achieved key) — absence means "not judged", not "failed".
    return outcome


# ---------------------------------------------------------------------------
# User config loader
# ---------------------------------------------------------------------------

def _load_user_config() -> dict:
    """Parse user/CONFIG.md into a key→value dict. Non-fatal — returns {} on any error."""
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "user" / "CONFIG.md"
        if not cfg_path.exists():
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
    origin: Optional[dict] = None,
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
            origin=origin,
        )
        return result
    finally:
        # Finalize the per-run metadata for EVERY caller, not just the CLI.
        # Before 2026-06-11 only cli main() finalized, so task-path runs
        # (drain_task_store -> handle_task -> handle) were left status=None
        # -> recall read them as "unknown" -> all_failing counted a
        # *succeeding* repeat goal as failing and could trip the dispatch
        # guard on it. On an exception the run is closed as "error" via the
        # pinned run context. The CLI keeps only the context clear.
        try:
            from runs import finalize_run as _finalize_run
            from runs import slice_log_for_run as _slice_log
            from runs import snapshot_repo_bundle as _snapshot_repo
            from runs import current_handle_id as _current_hid
            if result is not None:
                _hid = result.handle_id
            else:
                # Exception path: only trust the pinned run context if THIS
                # call pinned it — a long-lived process (drain loop) may
                # still carry the previous task's pin if we raised before
                # create_run_dir ran.
                _hid = _current_hid()
                if _hid == _pre_hid:
                    _hid = None
            if _hid:
                _slice_log(_hid)
                _snapshot_repo(_hid)
                _status = result.status if result is not None else "error"
                _finalize_run(_hid, status=_status)
                # Post-goal curation: classify the now-finalized run and park
                # the paid-for capture for later mining (skills/scripts/decision
                # priors/re-attempts). Reads the metadata finalize just wrote, so
                # it runs AFTER _finalize_run. Best-effort, never affects outcome.
                _card = None
                try:
                    from run_curation import curate_run as _curate_run
                    _card = _curate_run(_hid, status=_status)
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
    origin: Optional[dict] = None,
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

    Returns:
        HandleResult with routing info and substantive result.
    """
    from intent import classify
    from llm import build_adapter, MODEL_CHEAP
    from agent_loop import run_agent_loop, _DryRunAdapter

    handle_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()

    if verbose:
        print(f"[maro:{handle_id}] handle: {message!r}", file=sys.stderr, flush=True)

    # Persist raw input before any prefix stripping — visibility hole fix.
    # Writes to memory/handle_inputs.jsonl so every goal + its prefixes are recoverable.
    _raw_input = message
    try:
        _inputs_path = Path(__file__).resolve().parent.parent / "memory" / "handle_inputs.jsonl"
        _inputs_path.parent.mkdir(parents=True, exist_ok=True)
        with _inputs_path.open("a", encoding="utf-8") as _fh:
            _input_rec = {
                "handle_id": handle_id,
                "raw_input": _raw_input,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            if origin:
                _input_rec["origin"] = origin
            _fh.write(json.dumps(_input_rec) + "\n")
    except Exception:
        pass  # never block on logging

    # Per-run isolation: create the run-dir at start and pin it as the
    # current-run context so artifact writers downstream land directly
    # in `~/.maro/workspace/runs/<id>-<nick>/` rather than scattered
    # across project_workspace/. See src/runs.py.
    # Never block the run on a runs/ failure.
    try:
        from runs import create_run_dir as _create_run_dir
        from runs import set_current_run_dir as _set_current_run_dir
        from runs import record_log_offset as _record_log_offset
        from runs import record_repo_base as _record_repo_base
        _rd = _create_run_dir(
            handle_id,
            prompt=_raw_input,
            model=model,
            extra_metadata={"origin": origin} if origin else None,
        )
        _set_current_run_dir(_rd)
        _record_log_offset(handle_id)
        if repo_path:
            _record_repo_base(handle_id, repo_path)
    except Exception as _run_dir_exc:
        log.debug("runs: create_run_dir failed: %s", _run_dir_exc)

    # Apply user/CONFIG.md defaults (non-fatal — bad config never blocks a run)
    _cfg = _load_user_config()
    if model is None:
        _tier = _cfg.get("default_model_tier", "").strip().lower()
        if _tier in ("cheap", "mid", "power"):
            model = _tier

    # Apply magic prefix registry — strips all recognized prefixes in one pass.
    _pfx = _apply_prefixes(message)
    message = _pfx.message
    if _pfx.model_tier and model is None:
        model = _pfx.model_tier

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

    # Scope-based model floor: wide/deep goals shouldn't start on cheap.
    # The pre-flight scope estimate is free (<1ms, zero LLM) and already exists.
    # If no explicit model was requested (no prefix, no config), lift to mid.
    if model is None or model == MODEL_CHEAP:
        try:
            from planner import estimate_goal_scope
            _scope = estimate_goal_scope(message)
            if _scope in ("wide", "deep"):
                model = "mid"
                log.info("handle: scope=%s → lifting model floor to mid (was %s)",
                         _scope, model or "cheap")
        except Exception:
            pass

    # Build adapter
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or MODEL_CHEAP)
    elif dry_run:
        adapter = _DryRunAdapter()

    # Classify intent
    if force_lane:
        lane = force_lane
        confidence = 1.0
        reason = f"forced to {force_lane}"
    else:
        lane, confidence, reason = classify(message, adapter=adapter if not dry_run else None, dry_run=dry_run)

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
                    _write_meta_esc(
                        _rd_esc, handle_id=handle_id, prompt=_raw_input,
                        lane="agenda", model=model,
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
            outcome = _verify_now_outcome(message, outcome, adapter)
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
                    _wm_now(
                        _rd_now, handle_id=handle_id, prompt=_raw_input,
                        extra={
                            "goal_achieved": bool(outcome["goal_achieved"]),
                            "goal_verdict_source": "now_self_verdict",
                        },
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
                _record_outcome(
                    goal=message,
                    status=outcome["status"],
                    summary=str(outcome.get("result", ""))[:500],
                    task_type="now",
                    tokens_in=outcome["tokens_in"],
                    tokens_out=outcome["tokens_out"],
                    elapsed_ms=elapsed,
                    model=model or "",
                )
            except Exception:
                pass  # outcome recording must never block the NOW response

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

    else:  # agenda
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

        # Clarification milestone — check goal clarity before starting (skipped if yolo=true)
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
                        # No channel — return clarification_needed (CLI path)
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

        if verbose:
            print(f"[maro:{handle_id}] AGENDA lane — starting loop...", file=sys.stderr, flush=True)

        # mode:thin — use factory_thin loop (faster, lower cost) instead of full Mode 2
        if _use_thin_mode and not dry_run:
            try:
                from factory_thin import run_factory_thin
                _thin_result = run_factory_thin(
                    message,
                    model=model or "cheap",
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
                    project=project,
                    model=model,
                    adapter=adapter,
                    dry_run=dry_run,
                    verbose=verbose,
                    preset_steps=_pipe_steps,
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
                project=project,
                model=model,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
                parallel_fan_out=4,
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
                project=project,
                model=model,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
            )
            return _loop_result_to_handle(
                _direct_result, handle_id=handle_id, message=message,
                confidence=confidence, reason=reason, started_at=started_at,
                project=project, reason_suffix=" [direct]",
            )

        _ralph_from_cfg = _cfg.get("ralph_verify", "").strip().lower() == "true"
        _loop_kwargs: dict = dict(
            project=project,
            repo_path=repo_path,
            model=model,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            ralph_verify=_ralph_from_cfg or _ralph_prefix,
        )
        if _ultraplan_max_steps is not None:
            _loop_kwargs["max_steps"] = _ultraplan_max_steps

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
        # forced_persona (from garrytan:, etc.) overrides auto-selection.
        _persona_ctx = ""
        try:
            from persona import persona_for_goal, PersonaRegistry, build_persona_system_prompt, record_persona_dispatch, _DEFAULT_PERSONA
            _preg = PersonaRegistry()
            _pconf = 1.0
            if _pfx.forced_persona:
                _pname = _pfx.forced_persona
            else:
                _pname, _pconf = persona_for_goal(message, registry=_preg, confidence_threshold=0.75)
            # Track dispatch for persona gap detection (evolver uses this)
            try:
                _is_fallback = not _pfx.forced_persona and (
                    _pconf < 0.75 or _pname == _DEFAULT_PERSONA
                )
                record_persona_dispatch(message, _pname, _pconf, is_fallback=_is_fallback)
            except Exception:
                pass
            _pspec = _preg.load(_pname)
            if _pspec:
                _persona_ctx = build_persona_system_prompt(_pspec, goal=message)
                log.info("handle: persona=%s conf=%.2f forced=%s", _pname, _pconf, bool(_pfx.forced_persona))
        except Exception:
            pass
        _extra_ctx_parts = []
        if prior_context:
            _extra_ctx_parts.append(
                f"== Prior run context (for continuation) ==\n{prior_context}\n"
                f"== End prior context — continue from here =="
            )
        if _persona_ctx:
            _extra_ctx_parts.append(_persona_ctx)
        # Completion standard — injected for every AGENDA run
        try:
            _std_path = Path(__file__).parent.parent / "user" / "COMPLETION_STANDARD.md"
            if _std_path.exists():
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
                ).as_context_block()
                if _recall_block:
                    _extra_ctx_parts.append(_recall_block)
            except Exception as _recall_exc:
                log.debug("handle: dispatch recall skipped: %s", _recall_exc)

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
                )
                # Keep _scope as the scope-view for back-compat with the
                # existing artifact-write / captain's-log / ab-skip branches
                # below — they all operate on the ScopeSet shape.
                _scope = _resolved_intent.scope if _resolved_intent else None
                # Resolve the project artifacts dir once; used for both
                # successful scope.md persistence and raw-dump on parse failure.
                try:
                    from agent_loop import _goal_to_slug
                    _scope_project = project or _goal_to_slug(message)
                    _proj_dir = Path.home() / ".maro" / "workspace" / "projects" / _scope_project / "artifacts"
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
                    if _proj_dir is not None and _raw:
                        try:
                            (_proj_dir / "scope-raw-FAILED.txt").write_text(
                                _raw + "\n", encoding="utf-8"
                            )
                            log.info("scope: parse failed, raw response at %s/scope-raw-FAILED.txt", _proj_dir)
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

        loop_result = run_agent_loop(message, **_loop_kwargs)
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Every loop that ran for this handle (restarts add more) — the join
        # key from a run to its step-costs entries. Written to metadata after
        # the restart blocks settle.
        _run_loop_ids = [loop_result.loop_id] if getattr(loop_result, "loop_id", "") else []

        # Director restart: loop broke with restart status — re-run with restart context.
        # continuation_depth increment prevents infinite restart loops.
        if (loop_result.status == "restart"
                and not dry_run
                and _loop_kwargs.get("continuation_depth", 0) < 3):
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
                from director import verify_goal_completion
                _closure = verify_goal_completion(
                    message,
                    loop_result.steps,
                    adapter,
                    workspace_path=repo_path or "",
                    channel=channel,
                    scope=_scope,
                    resolved_intent=_resolved_intent,
                    diagnosis=_closure_diag,
                    loop_id=getattr(loop_result, "loop_id", "") or "",
                )
            except Exception:
                _closure = None

            try:
                from config import get as _config_get
                _closure_restart = bool(_config_get("closure_restart", True))
            except Exception:
                _closure_restart = True

            _depth = _loop_kwargs.get("continuation_depth", 0)
            if (
                _closure_restart
                and _closure is not None
                and not _closure.complete
                and _closure.confidence >= 0.6
                and _closure.checks_run > 0
                and getattr(_closure, "inconclusive_count", 0) == 0
                and _depth < 3
                and loop_result.status == "done"  # only escalate from "done" — stuck/partial already know they're incomplete
            ):
                _gap_lines = "\n".join(f"- {g}" for g in _closure.gaps) or "(none specified)"
                _closure_ctx = (
                    f"The previous run declared done, but closure verification found gaps.\n"
                    f"Summary: {_closure.summary}\n"
                    f"Gaps:\n{_gap_lines}\n"
                    f"Verification: {_closure.checks_passed}/{_closure.checks_run} checks passed.\n"
                    f"Address the gaps before declaring done again."
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
                try:
                    loop_result = run_agent_loop(message, **_closure_kwargs)
                    elapsed = int((time.monotonic() - started_at) * 1000)
                    if getattr(loop_result, "loop_id", ""):
                        _run_loop_ids.append(loop_result.loop_id)
                    # Re-verify the restarted loop — its declared status is
                    # exactly as unverified as the first loop's was. Without
                    # this, a restart that re-declares done sticks regardless
                    # of whether the gaps were addressed.
                    try:
                        _closure = verify_goal_completion(
                            message,
                            loop_result.steps,
                            adapter,
                            workspace_path=repo_path or "",
                            channel=channel,
                            scope=_scope,
                            resolved_intent=_resolved_intent,
                            diagnosis=None,
                            loop_id=getattr(loop_result, "loop_id", "") or "",
                        )
                    except Exception:
                        _closure = None  # fail open: no re-verdict, no demotion
                except Exception as _cr_exc:
                    log.warning("handle: closure restart re-run failed: %s", _cr_exc)

            # Deterministic provenance guard (agenda twin of the NOW guard): an
            # input the goal asked to read that isn't on disk, or an output that
            # never landed, means not-achieved — regardless of closure/narrative.
            # Works even when closure is None. Catches the false_pass a text-only
            # verdict can't see (shadow-eval n=42, 2026-06-24).
            if loop_result.status == "done":
                _done_results = "\n\n".join(
                    s.result for s in loop_result.steps
                    if s.status == "done" and s.result
                )
                _prov_missing = _provenance_missing(
                    _raw_input,
                    result_text=_done_results,
                    window_start=_run_window_start(loop_result.elapsed_ms),
                )
                if _prov_missing:
                    log.info(
                        "provenance (agenda): claimed input/output(s) not found %s — demoted to incomplete",
                        _prov_missing,
                    )
                    loop_result.status = "incomplete"
                    if loop_result.stuck_reason is None:
                        loop_result.stuck_reason = (
                            f"provenance: claimed input/output(s) not found: {_prov_missing}"
                        )
                    try:
                        from runs import write_metadata as _wm_prov
                        from runs import current_run_dir as _crd_prov
                        _rd_p = _crd_prov()
                        if _rd_p is not None:
                            _wm_prov(
                                _rd_p, handle_id=handle_id, prompt=_raw_input,
                                extra={
                                    "goal_achieved": False,
                                    "goal_verdict_source": "provenance",
                                    "goal_verdict_summary":
                                        f"claimed input/output(s) not found: {_prov_missing}",
                                },
                            )
                    except Exception:
                        pass

            # Status honesty (agenda twin of _verify_now_outcome): when the
            # director's own verifier contradicts a declared "done" at high
            # confidence, the run is recorded as incomplete. Live find
            # 2026-06-11: an unsatisfiable goal ran the loop, every step
            # result said "goal is incomplete", closure agreed at 0.95–0.99 —
            # and the run still finalized done, poisoning recall, the
            # dispatch guard, and the navigator. Verified-done beats
            # reported-done.
            if (
                _closure is not None
                and not _closure.complete
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

            # Record the goal verdict as its own metadata dimension — process
            # status ("did the run finish") and goal achievement ("did it
            # deliver what was asked") are different facts; status alone
            # conflated them until 2026-06-11.
            # Only when closure actually ran checks. The fail-open null
            # verdict (complete=True, "Verification skipped.", checks_run=0)
            # exists so closure errors never block execution — recording it
            # would bless unverified work as achieved (burn-in batch 4,
            # 2026-07-02: a rate-limit-stuck run got goal_achieved=True from
            # a skipped verification). No checks → no verdict → unverified.
            if _closure is not None and _closure.checks_run > 0:
                try:
                    from runs import write_metadata as _wm_verdict
                    from runs import current_run_dir as _crd_verdict
                    _rd_v = _crd_verdict()
                    if _rd_v is not None:
                        _wm_verdict(
                            _rd_v, handle_id=handle_id, prompt=_raw_input,
                            extra={
                                "goal_achieved": bool(_closure.complete),
                                "goal_verdict_confidence": float(_closure.confidence),
                                "goal_verdict_source": "closure",
                                "goal_verdict_summary": str(_closure.summary)[:300],
                            },
                        )
                except Exception:
                    pass

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
                with default_subprocess_cwd(_qg_cwd):
                    _gate_verdict = run_quality_gate(
                        message, loop_result.steps, adapter,
                        run_council=_strict_prefix,
                        run_cross_ref=_strict_prefix,
                        loop_id=getattr(loop_result, "loop_id", None),
                    )
                _contested_claims = _gate_verdict.contested_claims or []
                if _gate_verdict.escalate and loop_result.status == "done":
                    _next_tier = next_model_tier(model or "cheap")
                    _action = _cfg.get("quality_gate_action", "escalate").strip().lower()
                    _gate_note = f"\n\n⚠️ Quality gate: ESCALATE — {_gate_verdict.reason}"
                    if verbose:
                        print(f"[maro:{handle_id}] quality gate: ESCALATE → {_next_tier} ({_gate_verdict.reason})",
                              file=sys.stderr, flush=True)
                    if _action == "escalate" and _next_tier:
                        if verbose:
                            print(f"[maro:{handle_id}] re-running with model={_next_tier}",
                                  file=sys.stderr, flush=True)
                        _escalated_adapter = build_adapter(model=_next_tier)
                        loop_result = run_agent_loop(
                            message,
                            project=(project or loop_result.project or "") + "-escalated",
                            model=_next_tier,
                            adapter=_escalated_adapter,
                            dry_run=False,
                            verbose=verbose,
                            loop_reason="quality_gate_escalate",
                            parent_loop_id=getattr(loop_result, "loop_id", None),
                        )
                        elapsed = int((time.monotonic() - started_at) * 1000)
                        if getattr(loop_result, "loop_id", ""):
                            _run_loop_ids.append(loop_result.loop_id)
                        _gate_note = f"\n\n✅ Quality gate escalated to {_next_tier} — re-run complete."
                        _contested_claims = []  # fresh run — don't append stale claims

                        # Re-run closure on the escalated loop. Without this, only the
                        # initial loop's closure verdict shows up in the captain's log
                        # — the escalated re-run (which is the version we ship) would
                        # have no closure record at all (2026-04-26 audit finding).
                        if not dry_run:
                            try:
                                from director import verify_goal_completion as _verify_post_escalate
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
                                _post_closure = _verify_post_escalate(
                                    message,
                                    loop_result.steps,
                                    _escalated_adapter,
                                    workspace_path=repo_path or "",
                                    channel=channel,
                                    scope=_scope,
                                    resolved_intent=_resolved_intent,
                                    diagnosis=_post_diag,
                                    loop_id=getattr(loop_result, "loop_id", "") or "",
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
) -> Optional[str]:
    """Write the NOW-lane result into the run dir's artifact/ subtree."""
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
        fname = f"now-{handle_id}.json"
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
    """
    if decision is None:
        return None
    try:
        try:
            from config import get as _cfg_get
            if not bool(_cfg_get("navigator.act_dispatch", False)):
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
                _notify_emit("escalation", {
                    "handle_id": "",
                    "goal": goal,
                    "status": status,
                    "summary": result,
                    "reason": reasoning,
                    "job_id": job_id,
                    "source": source,
                    "point": "dispatch",
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


def handle_task(
    task: dict,
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
):
    """Route a task_store task to the appropriate handler based on its source.

    - loop_escalation → director.handle_escalation() (judgment call: continue/narrow/close/surface)
    - loop_continuation → run_agent_loop() directly with continuation_depth (already classified AGENDA)
    - all others → handle(reason) (standard text-based routing)

    This is the closure mechanism: escalation tasks don't sit silently in the queue,
    they route to the director for a reasoned decision.
    """
    source = task.get("source", "")
    reason = task.get("reason", "")
    try:
        depth = int(task.get("continuation_depth", 0))
    except (TypeError, ValueError):
        depth = 0
    job_id = task.get("job_id", "unknown")

    if source == "loop_escalation":
        from director import handle_escalation
        log.info("handle_task routing escalation job_id=%s depth=%d", job_id, depth)
        _esc = handle_escalation(task, adapter=adapter, dry_run=dry_run, verbose=verbose)
        # "surface" means "for operator review" — that review only happens if
        # the operator is told. continue/narrow/close are internal dispositions.
        if getattr(_esc, "action", "") == "surface" and not dry_run:
            try:
                from notify import emit as _notify_emit
                _notify_emit("escalation", {
                    "handle_id": "",
                    "goal": reason[:500],
                    "status": "surfaced",
                    "summary": getattr(_esc, "summary_for_user", ""),
                    "reason": getattr(_esc, "reasoning", ""),
                    "job_id": job_id,
                    "source": source,
                    "point": "director_escalation",
                })
            except Exception:
                pass
        return _esc

    elif source == "loop_continuation":
        # Continuations are already classified AGENDA — skip intent classification overhead.
        # Extract the original goal cleanly; pass accomplished/remaining context as ancestry
        # so the planner gets focused decomposition ("this is pass N, remaining work is X")
        # rather than treating the full blob as a new goal to plan from scratch.
        log.info("handle_task routing continuation job_id=%s depth=%d", job_id, depth)
        _cont_goal, _cont_ctx = _parse_continuation_reason(reason)
        from agent_loop import run_agent_loop
        if adapter is None and not dry_run:
            from llm import build_adapter, MODEL_CHEAP
            adapter = build_adapter(model=MODEL_CHEAP)
        _filtered_ctx = _context_firewall(_cont_ctx, depth=depth) if _cont_ctx else ""
        return run_agent_loop(
            _cont_goal,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            continuation_depth=depth,
            ancestry_context_extra=_filtered_ctx,
        )

    else:
        log.info("handle_task routing %s job_id=%s via handle()", source or "unknown", job_id)
        # Carry ancestry across the requeue boundary: the task's origin (if its
        # creator recorded one) plus queue-level identity. Without this, a
        # requeued goal arrives at handle() indistinguishable from fresh user
        # input (goal-brain pressure test, 2026-06-10, finding 1).
        _origin = dict(task.get("origin") or {})
        _origin.setdefault("source", source or "task_store")
        _origin.setdefault("job_id", job_id)
        if task.get("parent_job_id"):
            _origin.setdefault("parent_job_id", task["parent_job_id"])
        # Dispatch guard (goal-brain step 3, docs/RECALL_DESIGN.md): refuse to
        # re-run a goal whose recent attempts ALL failed. Applies only to this
        # autonomous requeue path — a human calling handle() directly is never
        # blocked. Basis: 2026-05-17, the same goal ran ~25x in 35 minutes
        # with nothing consulting prior outcomes. Skipped on dry_run (preview
        # burns nothing, so there is no waste to guard against).
        if not dry_run:
            try:
                from config import get as _cfg_get
                _guard_on = bool(_cfg_get("recall.dispatch_guard", True))
                _guard_attempts = int(_cfg_get("recall.guard_attempts", 3))
                _guard_window = float(_cfg_get("recall.guard_window_minutes", 60))
            except Exception:
                _guard_on, _guard_attempts, _guard_window = True, 3, 60.0
            # One recall serves both the guard and the live navigator shadow.
            _rr = None
            if _guard_on:
                try:
                    from recall import recall as _recall_fn
                    _rr = _recall_fn(reason, slice="dispatch", origin=_origin)
                except Exception as _guard_exc:
                    log.debug("handle_task recall guard skipped: %s", _guard_exc)
            _sig = None
            if _rr is not None:
                try:
                    _sig = _rr.dispatch_signals(window_minutes=_guard_window)
                except Exception:
                    _sig = None
            _guard_tripped = bool(
                _sig and _sig["repeat_count"] >= _guard_attempts and _sig["all_failing"]
            )
            # Live navigator shadow (goal-brain step 5, docs/NAVIGATOR_SCHEMA.md):
            # decide-only beside the pipeline; NAVIGATOR_DECIDED records the
            # navigator's move next to what dispatch actually did. Config-gated
            # (navigator.shadow_dispatch, default off) and failure-isolated —
            # it can never change dispatch behavior.
            _nav_decision = None
            try:
                from navigator_shadow import shadow_dispatch_live
                _nav_decision = shadow_dispatch_live(
                    reason,
                    origin=_origin,
                    recall_result=_rr,
                    pipeline_move="guard_refused" if _guard_tripped else "execute",
                    extra={"job_id": job_id, "source": source or "task_store"},
                )
            except Exception as _shadow_exc:
                log.debug("handle_task navigator shadow skipped: %s", _shadow_exc)
            if _guard_tripped:
                _msg = (
                    f"recall guard: {_sig['repeat_count']} attempts at this goal "
                    f"in the last {int(_guard_window)}m, all failed — refusing to "
                    f"re-run without a change of approach (docs/RECALL_DESIGN.md)"
                )
                log.warning("handle_task %s job_id=%s", _msg, job_id)
                try:
                    from captains_log import log_event, RECALL_GUARD_TRIPPED
                    log_event(
                        RECALL_GUARD_TRIPPED,
                        subject="recall_guard",
                        summary=_msg,
                        context={"goal_preview": reason[:200], "job_id": job_id, **_sig},
                    )
                except Exception:
                    pass
                return HandleResult(
                    handle_id="",
                    lane="agenda",
                    lane_confidence=1.0,
                    classification_reason="recall_guard",
                    message=reason,
                    status="error",
                    result=_msg,
                )
            # Dispatch-class cutover (navigator.act_dispatch, default OFF):
            # the navigator's dispatch decision acts instead of being
            # shadow-only. Earned by shadow agreement data (NAVIGATOR_SCHEMA
            # cutover rule); the recall guard above stays as the deterministic
            # backstop and always gets the first word. Conservative by
            # construction: only escalate/close act, only at or above the
            # confidence floor, only on this autonomous requeue path —
            # everything else falls through to execute.
            _nav_acted = _navigator_act_dispatch(
                _nav_decision, reason, job_id=job_id,
                source=source or "task_store",
            )
            if _nav_acted is not None:
                return _nav_acted
        return handle(reason, adapter=adapter, dry_run=dry_run, verbose=verbose, origin=_origin)


def drain_task_store(
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
    max_tasks: int = 3,
    sources: tuple = ("loop_continuation", "loop_escalation", "user_goal"),
    job_ids: Optional[set] = None,
) -> int:
    """Claim and process queued task_store tasks with known sources.

    Called from the heartbeat or scheduler to consume continuation,
    escalation, and user-enqueued goals. Returns the number processed.

    Args:
        max_tasks: Max tasks to process per call (avoids monopolizing the heartbeat).
        sources: Which task sources to drain. Includes user_goal for
                 ad-hoc goals enqueued via ``maro-enqueue``.
        job_ids: If set, drain ONLY these tasks. This is the substrate
                 dispatch contract (enqueue --drain): a dispatch must run
                 exactly what it enqueued — never an older queued task, whose
                 notify event the substrate would misattribute and whose
                 tokens nobody consented to spend right now.
    """
    try:
        from task_store import list_tasks, claim, complete, fail as task_fail
    except ImportError:
        log.warning("drain_task_store: task_store not available")
        return 0

    queued = [
        t for t in list_tasks(status_filter="queued")
        if t.get("source") in sources
        and (job_ids is None or t.get("job_id") in job_ids)
    ]
    if not queued:
        return 0

    log.info("drain_task_store: %d queued task(s) to process", len(queued))
    processed = 0

    for task in queued[:max_tasks]:
        job_id = task.get("job_id", "unknown")
        try:
            claim(job_id)
        except Exception as exc:
            log.warning("drain_task_store: failed to claim %s: %s", job_id, exc)
            continue

        try:
            handle_task(task, adapter=adapter, dry_run=dry_run, verbose=verbose)
            try:
                complete(job_id)
            except Exception as _ce:
                log.warning("drain_task_store: failed to mark %s complete: %s", job_id, _ce)
            processed += 1
            log.info("drain_task_store: completed %s", job_id)
            # Emit observable event so the dashboard shows continuation/escalation activity
            try:
                from observe import write_event as _write_event
                _write_event(
                    "task_drained",
                    goal=task.get("reason", "")[:80],
                    project=task.get("parent_job_id", ""),
                    loop_id=job_id,
                    status=task.get("source", ""),
                    detail=f"depth={task.get('continuation_depth', 0)}",
                )
            except Exception:
                pass
        except Exception as exc:
            log.warning("drain_task_store: task %s failed: %s", job_id, exc)
            try:
                task_fail(job_id, str(exc))
            except Exception:
                pass

    return processed


# ---------------------------------------------------------------------------
# Goal queue — user-facing mission enqueue
# ---------------------------------------------------------------------------

def enqueue_goal(
    goal: str,
    *,
    reason: str = "",
    blocked_by: Optional[List[str]] = None,
) -> str:
    """Enqueue a user goal for the director to process sequentially.

    Returns the job_id. The goal will be picked up by ``drain_task_store``
    on the next heartbeat tick (or can be drained manually).

    This is the user-facing "drop goals here" API. Each goal runs through
    ``handle()`` in order — the director gets full discretion over how to
    decompose and execute each one.
    """
    from task_store import enqueue
    task = enqueue(
        lane="agenda",
        source="user_goal",
        reason=reason or goal,
        blocked_by=blocked_by,
    )
    job_id = task["job_id"]
    log.info("enqueue_goal: queued %s — %s", job_id, goal[:80])
    return job_id


def enqueue_goals(goals: List[str], *, sequential: bool = True) -> List[str]:
    """Enqueue multiple goals. If sequential=True, each goal is blocked_by the previous."""
    job_ids = []
    for goal in goals:
        blocked = [job_ids[-1]] if sequential and job_ids else None
        jid = enqueue_goal(goal, blocked_by=blocked)
        job_ids.append(jid)
    return job_ids


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
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument("--format", choices=["text", "json"], default="text")

    args = parser.parse_args(argv)
    msg = " ".join(args.message)

    result = handle(
        msg,
        project=args.project,
        repo_path=args.repo or "",
        model=args.model,
        force_lane=args.lane,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

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
