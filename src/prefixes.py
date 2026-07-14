"""Neutral magic-prefix parsing — the single prefix-parsing abstraction shared
by handle.py (dispatch: strips prefixes off an incoming goal and mutates
execution) and recall.py (best-effort strip so goal-similarity matching isn't
polluted by a prefix a retry/rephrase happens to drop or add).

Extracted 2026-07-13 (adversarial-review R1 batch-1, findings #1 + #2):
previously handle.py held two parallel mechanisms for "the same kind of
thing" — `_PREFIX_REGISTRY` (fixed-string entries) and a separate hardcoded
`_PERSONA_PREFIX_RE` regex branch for `persona:<name>:` — and recall.py
lazily imported handle's private `_apply_prefixes` to reuse it, a read-side
module reaching into a dispatch-side module's internals. This module is ONE
registry (literal-prefix rules and the one capture-group rule run through the
same scan loop) with no owner — handle.py and recall.py both import it
directly, instead of one reaching into the other's private names.

This is a refactor, not a behavior change: every existing prefix (`effort:`,
`mode:thin`, `btw:`, `ultraplan:`, `direct:`, `ralph:`/`verify:`, `pipeline:`,
`strict:`, `team:`, `garrytan:`, `persona:<name>:`) parses identically to
before the extraction. See handle.py's `_resolve_forced_persona` for the
(separate) existence-validation step that runs on the `forced_persona` this
module produces — that needs PersonaRegistry, so it stays in handle.py
rather than this dependency-free module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Pattern

# Kept as "maro.handle" (not "maro.prefixes") even though the code moved
# here — these warnings are part of handle's dispatch contract (existing
# tests assert on them via caplog scoped to this logger name) and recall's
# best-effort strip is a rare-edge-case caller of the same warning path.
log = logging.getLogger("maro.handle")


@dataclass
class PrefixRule:
    """One recognized magic prefix.

    Two shapes share this one rule type — the unification this module exists
    for:
      - literal (`prefix` set, `pattern` empty): an exact lowercase string,
        matched via startswith — effort:, ralph:, garrytan:, etc.
      - pattern (`pattern` set, `prefix` empty): a compiled regex anchored at
        the start of the message with exactly one capture group, whose match
        becomes `forced_persona` — currently only `persona:<name>:`, but the
        shape is general so a future capture-group prefix reuses this path
        instead of growing a second hardcoded branch the way persona: used to.
    """
    prefix: str = ""
    pattern: Optional[Pattern] = None
    flag: str = ""             # attribute name on PrefixResult to set True
    model_tier: str = ""       # if non-empty, override model to this tier (cheap/mid/power)
    max_steps: int = 0         # if > 0, override max_steps to this value
    persona: str = ""          # if non-empty, force this persona name (literal rules only —
    # the pattern rule's persona name comes from its capture group instead)


@dataclass
class PrefixResult:
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
    persona_bundled_tier: str = ""  # model_tier that arrived bundled with forced_persona
    # (e.g. garrytan:'s power tier) — tracked separately from model_tier so an
    # explicit persona= override that replaces the persona can also drop the
    # tier bump it brought along, instead of silently keeping it.


# Generalized "run this AS <persona>" pattern (BACKLOG hist-r2-02). The
# persona name is a variable captured out of the message, so it's a pattern
# rule (below) rather than a literal one — matched last, only when no literal
# rule matched the start of the message. Only identity is forced this way (no
# model_tier bump); garrytan: stays the dedicated shortcut for "identity +
# power tier" bundled together, since that tier bump is a persona-specific
# tuning choice, not something every persona should carry.
_PERSONA_CAPTURE_RE = re.compile(r"^persona:([a-z0-9][a-z0-9_+-]*):\s*", re.IGNORECASE)


PREFIX_REGISTRY: List[PrefixRule] = [
    # effort: overrides model tier; exclusive per level (first match wins)
    PrefixRule(prefix="effort:low",   model_tier="cheap"),
    PrefixRule(prefix="effort:mid",   model_tier="mid"),
    PrefixRule(prefix="effort:high",  model_tier="power"),
    # execution mode modifiers
    PrefixRule(prefix="mode:thin",    flag="thin_mode"),
    PrefixRule(prefix="btw:",         flag="btw_mode"),
    PrefixRule(prefix="ultraplan:",   flag="ultraplan_mode", model_tier="power", max_steps=12),
    PrefixRule(prefix="direct:",      flag="direct_mode"),
    # quality / behavior modifiers (non-exclusive — can stack)
    PrefixRule(prefix="ralph:",       flag="ralph_mode"),
    PrefixRule(prefix="verify:",      flag="ralph_mode"),   # alias for ralph:
    PrefixRule(prefix="pipeline:",    flag="pipeline_mode"),
    PrefixRule(prefix="strict:",      flag="strict_mode"),
    PrefixRule(prefix="team:",        flag="team_mode",  model_tier="mid"),
    # forced persona shortcuts
    PrefixRule(prefix="garrytan:",    model_tier="power", persona="garrytan"),
    # Pattern rule — tried last (see docstring above); must stay last in this
    # list so every literal rule gets a chance to match first, same ordering
    # the old two-mechanism version enforced by construction (regex only
    # checked when the literal for-loop found nothing).
    PrefixRule(pattern=_PERSONA_CAPTURE_RE),
]


def apply_prefixes(message: str, *, warn_conflicts: bool = True) -> PrefixResult:
    """Strip all recognized magic prefixes from `message` and return a PrefixResult.

    Prefixes are matched case-insensitively and stripped in registry order.
    Multiple prefixes can stack (e.g. "strict: pipeline: do the thing").
    The effort: group is exclusive (first level wins); all others accumulate.
    """
    result = PrefixResult(message=message)
    changed = True
    while changed:
        changed = False
        lower = result.message.lower()
        for rule in PREFIX_REGISTRY:
            if rule.pattern is not None:
                m = rule.pattern.match(result.message)
                if not m:
                    continue
                requested = m.group(1).lower()
                if result.forced_persona and result.forced_persona != requested:
                    if warn_conflicts:
                        log.warning(
                            "conflicting forced personas: %r already set, ignoring %r "
                            "(from prefix 'persona:%s:')",
                            result.forced_persona, requested, requested,
                        )
                elif not result.forced_persona:
                    result.forced_persona = requested
                result.message = result.message[m.end():].lstrip()
                changed = True
                break
            if not lower.startswith(rule.prefix):
                continue
            result.message = result.message[len(rule.prefix):].lstrip()
            if rule.flag:
                setattr(result, rule.flag, True)
            rule_set_model_tier = False
            if rule.model_tier:
                if result.model_tier and result.model_tier != rule.model_tier:
                    if warn_conflicts:
                        log.warning(
                            "conflicting model tiers: %r already set, ignoring %r (from prefix %r)",
                            result.model_tier, rule.model_tier, rule.prefix,
                        )
                elif not result.model_tier:
                    result.model_tier = rule.model_tier
                    rule_set_model_tier = True
            if rule.max_steps:
                result.max_steps = rule.max_steps
            if rule.persona:
                if result.forced_persona and result.forced_persona != rule.persona:
                    if warn_conflicts:
                        log.warning(
                            "conflicting forced personas: %r already set, ignoring %r (from prefix %r)",
                            result.forced_persona, rule.persona, rule.prefix,
                        )
                elif not result.forced_persona:
                    result.forced_persona = rule.persona
                    if rule_set_model_tier:
                        result.persona_bundled_tier = rule.model_tier
            changed = True
            break  # restart registry scan after each match
    return result


def strip_prefixes(text: str) -> str:
    """Convenience: just the cleaned message, prefixes stripped.

    The shape recall.py wants at the matching boundary (it only cares about
    the text, not the parsed flags) — see recall._strip_for_match.
    """
    return apply_prefixes(text, warn_conflicts=False).message
