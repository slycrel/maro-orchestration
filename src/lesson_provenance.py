"""Lesson-mint provenance gate (2026-07-29).

Origin incident: a dispatch prompt authored by Poe (the Hermes concierge on
mini2) carried anti-escalation scaffolding ("Do NOT escalate or stop merely
because a linked page cannot be accessed..."). The lesson extractor
generalized the SCAFFOLDING itself into lesson db37d525 ("When a prompt
explicitly says 'do not escalate/stop...' treat that as a hard
constraint...") and recall injected it verbatim into the next, unrelated
run. Instruction text had rewritten persistent state.

The gate applies least-privilege to dispatch prompts — they are untrusted
input authored by another agent. A lesson whose subject is *what the prompt
says* (rather than *what happened*) is stamped ``minted_from="prompt"`` at
the storage choke points (knowledge_web.record_tiered_lesson,
memory_ledger._store_lesson) and quarantined from every cross-goal
injection surface, mirroring the provisional-lesson exclusion pattern.
Quarantined rows stay on disk and visible in readouts (quarantine, not
deletion); decay disposes of them. An outcome-derived confirming re-record
of the same text clears the flag — the knowledge independently earned
citizenship.

Deliberately Tier-0 deterministic: a handful of documented regexes, pinned
in tests/test_lesson_provenance.py against the four real lessons from the
incident. The bias runs toward quarantine — a false positive sits visible
but uninjected and decays away; a false negative contaminates future runs.
"""

import re

MINTED_FROM_PROMPT = "prompt"
MINTED_FROM_OUTCOME = "outcome"

# A lesson that generalizes what a prompt/instruction SAYS is instruction-
# derived: its evidence is the directive text, not an observed outcome.
# "a task specifies an exact output contract" (bae0851f, kept clean)
# deliberately does NOT match — prescribing a verification method for task
# contracts is outcome-shaped advice. The alternation is prompt/
# instruction(s)/directive(s) followed by a says/instructs-class verb.
_PROMPT_AUTHORITY_RE = re.compile(
    r"\b(?:the\s+|a\s+|your\s+)?(?:prompt|instructions?|directives?)\s+"
    r"(?:explicitly\s+|clearly\s+)?"
    r"(?:says?|said|states?|stated|instructs?|tells?|told|demands?|"
    r"forbids?|prohibits?)\b",
    re.IGNORECASE,
)

# Obedience generalization: elevating directive text to binding law. The
# treat-as-hard-constraint form requires a pronoun object so that domain
# facts ("rate limits are a hard constraint on parallelism") stay clean.
_OBEDIENCE_RE = re.compile(
    r"\btreat\s+(?:that|this|it|them)\s+as\s+(?:a\s+)?hard\s+constraints?\b"
    r"|\bmust\s+be\s+obeyed\b"
    r"|\bas\s+non-negotiable\b"
    r"|\bfollow\s+(?:it|them)\s+(?:exactly|to\s+the\s+letter)\b",
    re.IGNORECASE,
)

# Anti-escalation scaffolding markers. Checked against BOTH texts: a goal
# carrying these was authored to steer agent behavior, and a lesson echoing
# them back learned the steering, not the work.
_SCAFFOLDING_RE = re.compile(
    r"\bdo\s+not\s+(?:escalate|stop|abandon|refuse|give\s+up)\b"
    r"|\bcannot\s+use\b[^.]{0,60}\bas\s+an\s+excuse\b"
    r"|\bas\s+an\s+excuse\s+to\s+(?:stop|escalate)\b",
    re.IGNORECASE,
)


def provenance_gate_enabled() -> bool:
    """Killswitch for the provenance gate (default ON). config.get returns
    raw YAML nodes — a quoted "false" is a truthy string, so normalize the
    same way the other knowledge killswitches do."""
    try:
        from config import get as _cfg_get
        val = _cfg_get("knowledge.provenance_gate_enabled", True)
        if isinstance(val, str):
            return val.strip().lower() not in ("false", "0", "no", "off")
        return bool(val)
    except Exception:
        return True


def classify_lesson_provenance(lesson_text: str, goal_text: str = "") -> str:
    """Classify a minted lesson as outcome-derived or prompt-derived.

    Returns MINTED_FROM_PROMPT when the lesson generalizes instruction text
    (prompt-authority phrasing, obedience generalization, or an echo of
    anti-escalation scaffolding that the source goal also carries);
    MINTED_FROM_OUTCOME otherwise. ``goal_text`` is optional — the
    scaffolding-echo signal needs it, the other two fire on the lesson text
    alone. Deterministic, no I/O, never raises.
    """
    text = lesson_text or ""
    if _PROMPT_AUTHORITY_RE.search(text):
        return MINTED_FROM_PROMPT
    if _OBEDIENCE_RE.search(text):
        return MINTED_FROM_PROMPT
    if goal_text and _SCAFFOLDING_RE.search(goal_text) and _SCAFFOLDING_RE.search(text):
        return MINTED_FROM_PROMPT
    return MINTED_FROM_OUTCOME
