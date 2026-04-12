"""Director's Playbook — evolving operational wisdom.

The playbook is a living markdown document at ~/.poe/workspace/playbook.md
that captures what the system has learned about doing its job well. Unlike
personas (identity) or skills (procedures), this is meta-level operational
knowledge: when to use which approach, what failure patterns to watch for,
and how to make better decisions.

Three sources feed the playbook:
  1. Standing rules — promoted from lessons (knowledge_lens.py)
  2. Evolver suggestions — when applied, the insight is captured here
  3. Manual edits — operator can directly edit the playbook

The playbook is injected into director and decompose context alongside
POE_IDENTITY. It's meant to be short, opinionated, and actionable.

Usage:
    from playbook import load_playbook, inject_playbook, append_to_playbook
    wisdom = load_playbook()              # Full text
    block = inject_playbook(max_chars=800) # Formatted for injection
    append_to_playbook("Research tasks need gather→synthesize→verify steps.")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _playbook_path() -> Path:
    from config import playbook_path
    return playbook_path()


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

_SEED_CONTENT = """\
# Director's Playbook

Operational wisdom accumulated by the orchestration system. This document
is maintained automatically (evolver, standing rules) and can be edited
manually by the operator. It's injected into director and decompose context.

---

## Decomposition

- Research goals benefit from a gather → synthesize → verify structure.
- Narrow goals (≤15 words) should get 1-4 steps, not more.
- Wide/deep goals should use staged-pass decomposition.
- More atomic steps > fewer broad steps. One file or one command per step.

## Execution

- If a step fails 3 times, the problem is usually the decomposition, not the execution.
- Token budgets for build tasks should be ~2x research tasks.
- Always verify outputs before recording as done.

## Cost

- Haiku for execution, Sonnet for decomposition, Opus only at decision points.
- Enable extended thinking for decompose (high) and advisory calls (mid).
- Narrow goals should skip multi-plan (saves 3 LLM calls).

## Quality

- The verification loop is the highest-leverage investment.
- Inspector friction signals should be acted on, not just logged.
- Standing rules are zero-cost — promote aggressively when validated.

---

*Last updated: {date}*
"""


def load_playbook() -> str:
    """Load the full playbook text. Creates seed file if missing."""
    path = _playbook_path()
    if not path.exists():
        seed_playbook()
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def seed_playbook() -> None:
    """Create the initial playbook with seed content."""
    path = _playbook_path()
    if path.exists():
        return  # Don't overwrite
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _SEED_CONTENT.format(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path.write_text(content, encoding="utf-8")
    log.info("playbook: seeded at %s", path)


def inject_playbook(*, max_chars: int = 800) -> str:
    """Load playbook and format for context injection.

    Returns a truncated version suitable for prepending to decompose/director
    prompts. Skips the header, extracts the operational rules.
    """
    text = load_playbook()
    if not text:
        return ""

    # Skip the header (everything before first ##)
    lines = text.split("\n")
    body_lines = []
    in_body = False
    chars = 0
    for line in lines:
        if line.startswith("## "):
            in_body = True
        if in_body:
            if chars + len(line) > max_chars:
                break
            body_lines.append(line)
            chars += len(line) + 1

    if not body_lines:
        return ""
    return "## Operational Playbook\n" + "\n".join(body_lines)


def append_to_playbook(
    entry: str,
    *,
    section: str = "Learned",
    source: str = "",
) -> None:
    """Append an operational insight to the playbook.

    Called by the evolver when a suggestion is applied, or by graduation
    when a standing rule is promoted. The entry is added under the specified
    section header.

    Args:
        entry: The insight text (one line, starts with "- ").
        section: Which section to append under (created if missing).
        source: Where this insight came from (e.g., "evolver:suggestion-id").
    """
    # Validate entry — reject empty or whitespace-only entries
    entry = (entry or "").strip()
    if not entry:
        log.warning("playbook: rejected empty entry (section=%s, source=%s)", section, source)
        return
    if len(entry) > 500:
        entry = entry[:500] + "…"

    path = _playbook_path()
    if not path.exists():
        seed_playbook()

    text = path.read_text(encoding="utf-8")
    entry_line = entry if entry.startswith("- ") else f"- {entry}"

    # Add source attribution if provided
    if source:
        entry_line += f" *(from {source})*"

    section_header = f"## {section}"

    if section_header in text:
        # Append after the section header
        parts = text.split(section_header, 1)
        # Find the end of this section (next ## or end of file)
        remainder = parts[1]
        next_section = remainder.find("\n## ")
        if next_section >= 0:
            insert_point = next_section
        else:
            # Before the "Last updated" line if it exists
            last_updated = remainder.find("\n*Last updated:")
            insert_point = last_updated if last_updated >= 0 else len(remainder)

        updated = (
            parts[0] + section_header +
            remainder[:insert_point].rstrip() + "\n" + entry_line + "\n" +
            remainder[insert_point:]
        )
    else:
        # Create new section before "Last updated" or at end
        last_updated = text.find("\n*Last updated:")
        if last_updated >= 0:
            updated = (
                text[:last_updated].rstrip() + "\n\n" +
                section_header + "\n\n" + entry_line + "\n" +
                text[last_updated:]
            )
        else:
            updated = text.rstrip() + f"\n\n{section_header}\n\n{entry_line}\n"

    # Update timestamp
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "*Last updated:" in updated:
        import re
        updated = re.sub(
            r"\*Last updated:.*\*",
            f"*Last updated: {now}*",
            updated,
        )

    path.write_text(updated, encoding="utf-8")
    log.info("playbook: appended to [%s]: %s", section, entry_line[:80])

    # Captain's log
    try:
        from captains_log import log_event
        log_event(
            event_type="PLAYBOOK_UPDATED",
            subject=section,
            summary=entry_line[:200],
            context={"source": source, "section": section},
        )
    except Exception:
        pass
