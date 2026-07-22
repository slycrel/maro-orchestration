"""Typed finding codes — the shared review-error vocabulary.

Swarm-review chunk 1 (2026-07-21). Review passes (adversarial review,
evidence-path lenses, dev review sessions) stamp findings with a typed
code so downstream tooling can grep/count error classes instead of
LLM-classifying prose. The chunk-7 discretion readout consumes these;
the chunk-5 lenses and the dev adversarial-review workflow emit them.

Seeded from the 2026-05-12 adversarial-verification taxonomy
(docs/history/2026-05-12-adversarial-verification-brief.md — the review
that measured our own reviewer-error rates). The greppable-code shape
follows era 01's ERROR[E_*] precedent (machine-checkable codes beat
prose classification); runtime error codes remain a separate BACKLOG
idea — this vocabulary is for *findings about the tree/docs/design*,
not runtime failures.

Convention:
- Stamp format is ``FINDING[CODE]`` on the finding's first line, e.g.
  ``FINDING[PHANTOM_SYMBOL] recall.py cites `reframe_intent` — zero src/ hits``.
- Codes come from FINDING_CODES only. Extending the vocabulary = add a
  row here with a one-line definition and a detection hint (zero-overlap
  rule: if a new code overlaps an existing one, sharpen the existing
  definition instead of adding a near-duplicate).
- A finding that fits no code goes unstamped — an honest blank beats a
  forced classification.
- Read boundary is strict by default: ``parse_finding_codes`` raises on
  an unknown code inside a FINDING[...] stamp, so typos force repair
  instead of silently vanishing from counts. Tolerant readers opt in
  with ``strict=False`` and must surface ``parse_unknown_codes``.
"""

from __future__ import annotations

import re
from typing import Dict, List

# code -> (definition, detection hint)
FINDING_CODES: Dict[str, tuple] = {
    "CITATION_INVERSION": (
        "A real source (paper, doc, commit) is cited with the direction of "
        "evidence backwards — the source actually supports the opposite.",
        "Verify the primary source's abstract/text, not the citing prose; "
        "check doc-vs-code timestamps for causation direction.",
    ),
    "PHANTOM_SYMBOL": (
        "A cited code symbol, file, command, or config key does not exist "
        "in the tree.",
        "grep — zero occurrences settles it.",
    ),
    "THEORY_MECHANISM": (
        "A behavioral analogy is asserted as a causal mechanism — 'X works "
        "because <theory>' where only behavioral equivalence is plausible.",
        "Ask what evidence ties the mechanism (not just the behavior) to "
        "the theory; treat human-cognition pillars as inspiration only.",
    ),
    "GAP_UNDERSTATED": (
        "A confirmed gap is larger than the finding states — verification "
        "adds scope (adjacent dead paths, more callers affected).",
        "When confirming a finding, sweep its neighbors before scoping the "
        "fix.",
    ),
}

_STAMP_RE = re.compile(r"FINDING\[([A-Z_]+)\]")


def stamp(code: str, detail: str = "") -> str:
    """Return a stamped finding line; raises ValueError on unknown code."""
    if code not in FINDING_CODES:
        raise ValueError(
            f"unknown finding code {code!r} — add it to "
            f"finding_codes.FINDING_CODES first (known: {sorted(FINDING_CODES)})"
        )
    return f"FINDING[{code}] {detail}".rstrip()


def parse_finding_codes(text: str, *, strict: bool = True) -> List[str]:
    """Extract all finding codes stamped in ``text``, in order.

    strict (default): an unknown code inside FINDING[...] raises — a
    typo'd stamp silently vanishing from a readout is exactly the
    failure this vocabulary exists to prevent (2026-07-21 chunk-1
    adversarial review, all three lenses). A reader that must survive
    malformed historical text passes ``strict=False`` and owns surfacing
    ``parse_unknown_codes`` in its output.
    """
    found = _STAMP_RE.findall(text)
    if strict:
        unknown = [c for c in found if c not in FINDING_CODES]
        if unknown:
            raise ValueError(
                f"unknown finding code(s) stamped: {sorted(set(unknown))} — "
                f"fix the stamp or extend finding_codes.FINDING_CODES "
                f"(known: {sorted(FINDING_CODES)}); tolerant readers use "
                f"strict=False + parse_unknown_codes()"
            )
    return [c for c in found if c in FINDING_CODES]


def parse_unknown_codes(text: str) -> List[str]:
    """Extract FINDING[...] stamps whose code is NOT in the registry."""
    return [c for c in _STAMP_RE.findall(text) if c not in FINDING_CODES]
