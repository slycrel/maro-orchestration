# @lat: [[poe-identity]]
"""Poe Self-Identity — persistent 'who I am' block injected into every session.

Addresses research GAP 1: agents with an always-in-context identity block maintain
better goal coherence and session continuity than those without one.

The identity block is separate from memory (lessons/outcomes) — it's stable self-model,
not episodic knowledge. It gets prepended to the decompose system prompt so every plan
is made by a Poe who knows who she is.

Source priority:
  1. user/POE_IDENTITY.md (user-editable, durable)
  2. Built-in fallback (always available, minimal)

Usage:
    from poe_self import load_poe_identity, with_poe_identity
    system_prompt = with_poe_identity(base_system_prompt)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("poe.self")

_IDENTITY_CACHE: Optional[str] = None
_IDENTITY_FALLBACK = """\
You are Poe — autonomous AI partner. Act, don't ask. Own outcomes. Show reasoning.
Name failures plainly with next steps. Bound uncertainty explicitly. Never silent-fail.
Direct, concise, occasionally sardonic. No preamble. Lead with the answer.
"""


def _identity_path() -> Path:
    """Resolve user/POE_IDENTITY.md relative to this file's location."""
    here = Path(__file__).parent
    # Try: src/../user/POE_IDENTITY.md
    candidate = here.parent / "user" / "POE_IDENTITY.md"
    if candidate.exists():
        return candidate
    # Try orch_root() / user/POE_IDENTITY.md
    try:
        from orch_items import orch_root
        return orch_root() / "user" / "POE_IDENTITY.md"
    except Exception:
        return candidate


def load_poe_identity(*, use_cache: bool = True, max_chars: int = 2000) -> str:
    """Load Poe's persistent identity block.

    Args:
        use_cache: Cache the identity string in-process (default True)
        max_chars: Truncate to this length to bound token cost (default 2000)

    Returns:
        Identity string to prepend to system prompts. Never empty — falls back
        to a minimal built-in if the file is missing.
    """
    global _IDENTITY_CACHE
    if use_cache and _IDENTITY_CACHE is not None:
        return _IDENTITY_CACHE

    text = _FALLBACK = _IDENTITY_FALLBACK
    path = _identity_path()
    try:
        raw = path.read_text(encoding="utf-8")
        # Strip markdown heading lines that start with "#" at top
        # Keep the content readable but strip the file-level header comment block
        lines = raw.splitlines()
        # Skip the top "# Poe — Self Identity Block\n\nThis file is..." preamble
        # Find first content-bearing line after the initial comment block
        content_lines = []
        skip_header = True
        for line in lines:
            if skip_header and (line.startswith("#") or line.startswith("This file") or not line.strip()):
                if not line.strip():
                    continue
                if line.startswith("This file"):
                    continue
                if line.startswith("# Poe"):
                    continue
                if line.startswith("---"):
                    skip_header = False
                    continue
            else:
                skip_header = False
                content_lines.append(line)
        text = "\n".join(content_lines).strip()
        if not text:
            text = _FALLBACK
        elif len(text) > max_chars:
            text = text[:max_chars] + "\n[identity truncated]"
    except OSError:
        log.debug("poe_self: POE_IDENTITY.md not found at %s, using fallback", path)
        text = _FALLBACK

    if use_cache:
        _IDENTITY_CACHE = text
    return text


def clear_cache() -> None:
    """Clear the identity cache (mainly for testing)."""
    global _IDENTITY_CACHE
    _IDENTITY_CACHE = None


def with_poe_identity(system_prompt: str, *, separator: str = "\n\n---\n\n") -> str:
    """Prepend Poe's identity block to a system prompt.

    Args:
        system_prompt: The base system prompt to augment
        separator: String between identity block and existing prompt

    Returns:
        Identity block + separator + original prompt
    """
    identity = load_poe_identity()
    if not identity or not identity.strip():
        return system_prompt
    return f"## Who I Am\n\n{identity}{separator}{system_prompt}"
