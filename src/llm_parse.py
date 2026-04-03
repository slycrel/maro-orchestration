"""llm_parse.py — Robust LLM output parsing utilities.

All functions in this module are pure (no LLM calls, no I/O) and safe to
call on any string that came back from an LLM adapter.  They never raise —
failures are logged and a safe default is returned.

Primary entry point: ``extract_json(content, expected_type)``

  from llm_parse import extract_json, safe_float, safe_str

  data = extract_json(resp.content, dict)    # {} on failure
  lessons = extract_json(resp.content, list) # [] on failure
  conf = safe_float(data.get("confidence"), default=0.5)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Union

log = logging.getLogger("poe.llm_parse")

# ---------------------------------------------------------------------------
# Markdown fence stripping
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?(.*?)\n?```$", re.DOTALL)


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` (or bare ``` ```) wrappers if present."""
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip()
    return stripped


# ---------------------------------------------------------------------------
# JSON bracket extraction
# ---------------------------------------------------------------------------

def _find_json_bounds(text: str, open_char: str, close_char: str) -> tuple[int, int]:
    """Return (start, end) indices of the first balanced JSON object/array.

    Uses a depth counter so nested braces don't confuse the bounds.
    Returns (-1, -1) if no valid bounds found.
    """
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == open_char:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0 and start >= 0:
                return start, i + 1
    return -1, -1


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_json(
    content: Optional[str],
    expected_type: type = dict,
    *,
    default: Any = None,
    log_tag: str = "",
) -> Any:
    """Extract and parse JSON from LLM response content.

    Handles:
    - None / empty content → returns default
    - Markdown fence wrapping (```json ... ```)
    - Leading/trailing prose around the JSON object/array
    - Type mismatch between parsed result and expected_type
    - Any JSONDecodeError or ValueError

    Args:
        content: Raw LLM response string (may be None).
        expected_type: ``dict`` or ``list`` — what you expect the parsed value to be.
        default: Value to return on any failure. Defaults to ``{}`` for dict,
                 ``[]`` for list, or ``None`` if specified explicitly.
        log_tag: Short label included in debug log messages (e.g. module name).

    Returns:
        Parsed value of expected_type, or default on any failure.
    """
    if default is None:
        default = {} if expected_type is dict else [] if expected_type is list else None

    tag = f"[{log_tag}] " if log_tag else ""

    if not content:
        log.debug("%sextract_json: empty/None content", tag)
        return default

    text = strip_markdown_fences(content)

    open_char = "{" if expected_type is dict else "["
    close_char = "}" if expected_type is dict else "]"

    start, end = _find_json_bounds(text, open_char, close_char)
    if start < 0:
        # Also try the other bracket type in case LLM switched object vs array
        alt_open = "[" if open_char == "{" else "{"
        alt_close = "]" if close_char == "}" else "}"
        alt_start, alt_end = _find_json_bounds(text, alt_open, alt_close)
        if alt_start >= 0:
            log.debug(
                "%sextract_json: expected %s but found %s — attempting parse",
                tag, open_char, alt_open,
            )
            start, end = alt_start, alt_end
        else:
            log.debug("%sextract_json: no JSON brackets found in content", tag)
            return default

    raw = text[start:end]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log.debug("%sextract_json: JSONDecodeError: %s (raw=%r)", tag, exc, raw[:120])
        return default

    if not isinstance(parsed, expected_type):
        log.debug(
            "%sextract_json: type mismatch — expected %s, got %s",
            tag, expected_type.__name__, type(parsed).__name__,
        )
        # If we got a dict and expected a list, check common wrapping patterns
        if expected_type is list and isinstance(parsed, dict):
            for key in ("items", "results", "steps", "lessons", "signals", "patterns", "suggestions"):
                if key in parsed and isinstance(parsed[key], list):
                    log.debug("%sextract_json: unwrapped list from dict key %r", tag, key)
                    return parsed[key]
        return default

    return parsed


# ---------------------------------------------------------------------------
# safe_float — convert LLM-provided value to float without crashing
# ---------------------------------------------------------------------------

def safe_float(
    value: Any,
    *,
    default: float = 0.0,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Convert value to float, returning default on any error.

    Handles: None, empty string, non-numeric strings ("high", "0.8 approx"),
    "NaN", "Infinity", and numeric types.
    """
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    import math
    if math.isnan(result) or math.isinf(result):
        return default

    if min_val is not None:
        result = max(min_val, result)
    if max_val is not None:
        result = min(max_val, result)
    return result


# ---------------------------------------------------------------------------
# safe_str — coerce LLM-provided value to string safely
# ---------------------------------------------------------------------------

def safe_str(value: Any, *, default: str = "", max_len: Optional[int] = None) -> str:
    """Convert value to string, returning default if value is None/falsy."""
    if value is None:
        return default
    result = str(value).strip()
    if max_len is not None:
        result = result[:max_len]
    return result


# ---------------------------------------------------------------------------
# safe_list — ensure a value is a list of a given element type
# ---------------------------------------------------------------------------

def safe_list(value: Any, *, element_type: type = str, max_items: Optional[int] = None) -> list:
    """Return value if it's a list of element_type items, else [].

    Filters out items that don't match element_type rather than rejecting
    the whole list.
    """
    if not isinstance(value, list):
        return []
    result = [v for v in value if isinstance(v, element_type)]
    if max_items is not None:
        result = result[:max_items]
    return result


# ---------------------------------------------------------------------------
# content_or_empty — None-safe content accessor
# ---------------------------------------------------------------------------

def content_or_empty(resp: Any) -> str:
    """Return resp.content as a stripped string, or '' if None/missing."""
    content = getattr(resp, "content", None)
    if not content:
        return ""
    return str(content).strip()
