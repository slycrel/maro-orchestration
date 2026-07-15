"""Age rendering for injected memory — time-blindness first slice.

BACKLOG vehicle "Time blindness — LLMs don't experience ideas over time"
(2026-07-11, Jeremy; first slice hooks (d)+(a)): a lesson from February
reads identically to one from yesterday — staleness is invisible to the
model unless the prompt says so. This module renders the age suffixes the
injection seams attach (memory_bridge worker slice, recall() loop slice,
memory.inject_lessons_for_task) and the elapsed-time wording the step-gap
contributor in loop_execute uses.

Everything is gated on `memory.age_stamps` (hardcoded default False —
fresh installs keep byte-identical prompts per the no-silent-change
decree; docs/DEFAULTS.md). Items without a parseable stored timestamp
never get a suffix, so absent timestamps also render byte-identically.

Deliberately dependency-free (no dateutil/humanize): coarse honest
buckets — minutes, hours, days, months (≈30 days). The exact stored date
rides the suffix, so the coarse age never has to fake precision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_MONTH = 30 * _DAY  # coarse by design; the exact date rides the suffix


def age_stamps_enabled() -> bool:
    """memory.age_stamps — default False (no-silent-change; DEFAULTS.md)."""
    try:
        from config import get as _get
        return bool(_get("memory.age_stamps", False))
    except Exception:  # config unavailable == flag off; never break a seam
        return False


def parse_stored_ts(ts: str) -> Optional[datetime]:
    """Parse a stored ISO-8601 timestamp (or bare YYYY-MM-DD); None when
    absent or unparsable. Naive values are read as UTC — every writer in
    this codebase stamps UTC."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_elapsed(seconds: float) -> str:
    """Coarse honest wording for an elapsed span: "42 minutes", "3 hours",
    "5 days", "5 months". Sub-minute spans render "under a minute"."""
    seconds = max(0.0, seconds)
    if seconds < _MINUTE:
        return "under a minute"
    if seconds < _HOUR:
        count, unit = int(seconds // _MINUTE), "minute"
    elif seconds < _DAY:
        count, unit = int(seconds // _HOUR), "hour"
    elif seconds < _MONTH:
        count, unit = int(seconds // _DAY), "day"
    else:
        count, unit = int(seconds // _MONTH), "month"
    return f"{count} {unit}" + ("s" if count != 1 else "")


def age_suffix(ts: str, *, verb: str = "learned",
               now: Optional[datetime] = None) -> str:
    """Render " (learned 2026-02-14 — 5 months ago)" from a stored
    timestamp. Empty string when the timestamp is absent or unparsable —
    callers append the result unconditionally, so items without a
    timestamp render byte-identically to the unstamped path."""
    recorded = parse_stored_ts(ts)
    if recorded is None:
        return ""
    reference = now or datetime.now(timezone.utc)
    elapsed = (reference - recorded).total_seconds()
    if elapsed < 0:
        # Future stamp (clock skew, hand-edited or imported row): an "ago"
        # claim would be fabricated — render the date without an age.
        return f" ({verb} {recorded.date().isoformat()})"
    return (f" ({verb} {recorded.date().isoformat()} — "
            f"{format_elapsed(elapsed)} ago)")
