"""Per-thread goal-brain artifact (thread architecture; queue item 2026-06-11).

GOAL_BRAIN.md at the repo root is instance #1 of the goal-brain artifact —
the project steering itself. Runtime threads had no equivalent, so
NavigatorInput.goal_brain was fed a stand-in (resolved_intent.md + scope.md,
see navigator_shadow._goal_brain_standin). This module gives every thread a
real one: `<run_dir>/source/goal_brain.md`, created when the run dir is
created, closed when the run finalizes.

Same section grammar as the repo artifact, scaled down:

  Intent          — the goal verbatim + origin ancestry. Never paraphrased
                    (paraphrase is how telephone flaws start).
  Compiled truth  — verified claims only, accreted as the thread runs.
  Decisions       — append-only, dated. Open and close are the v0 writers;
                    append_decision is the seam for everything between.
  Threads         — children; nothing leaves the list silently (fan-out
                    defense, same rule as the navigator close validator).
  Open questions  — what blocks downstream moves.

v0 scope: artifact + lifecycle (open/close) + append seams. Per-turn
maintenance by the navigator is future work — the navigator is shadow-only
today, so nothing live would write turns yet anyway.

All writers are never-raise: a thread-brain failure must not block a run
(same posture as the rest of the runs.py plumbing).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("thread_brain")

_FILENAME = "goal_brain.md"

# Injected whole into NavigatorInput — keep it cappable. 4000 chars is ~1k
# tokens; past that the artifact has stopped being "short enough to inject
# whole" (the goodness criterion from the artifact definition) and the tail
# (newest decisions) matters more than the middle.
_MAX_INJECT_CHARS = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def brain_path(run_dir: Path) -> Path:
    return Path(run_dir) / "source" / _FILENAME


def create_thread_brain(
    run_dir: Path,
    *,
    goal: str,
    origin: Optional[dict] = None,
) -> Optional[Path]:
    """Seed the thread's goal-brain. First call wins (same rule as
    prompt.txt) — re-creating a run dir must not clobber accreted state.
    Returns the path, or None on failure / already-exists-no-op.
    """
    try:
        path = brain_path(run_dir)
        if path.exists():
            return None
        path.parent.mkdir(parents=True, exist_ok=True)

        origin = origin or {}
        intent_lines = [goal.strip() or "(empty goal)"]
        parent_goal = str(origin.get("parent_goal") or "").strip()
        parent_id = str(origin.get("parent_handle_id") or "").strip()
        source = str(origin.get("source") or "").strip()
        if parent_goal or parent_id:
            origin_bits = []
            if parent_id:
                origin_bits.append(f"parent thread `{parent_id}`")
            if parent_goal:
                origin_bits.append(f'parent goal: "{parent_goal}"')
            if source:
                origin_bits.append(f"via {source}")
            intent_lines.append("")
            intent_lines.append(f"Origin: {', '.join(origin_bits)}")

        text = (
            f"# Goal-Brain: {Path(run_dir).name}\n"
            "\n"
            "## Intent (human-steerable — goal verbatim, never paraphrased)\n"
            "\n"
            + "\n".join(intent_lines) + "\n"
            "\n"
            "## Compiled truth (system-maintained; verified claims only)\n"
            "\n"
            "- (none yet)\n"
            "\n"
            "## Decisions (system-maintained, append-only)\n"
            "\n"
            f"- {_now()}: thread opened\n"
            "\n"
            "## Threads (children — nothing leaves this list silently)\n"
            "\n"
            "- (none)\n"
            "\n"
            "## Open questions\n"
            "\n"
            "- (none)\n"
        )
        path.write_text(text, encoding="utf-8")
        return path
    except Exception as exc:
        log.debug("create_thread_brain failed for %s: %s", run_dir, exc)
        return None


def append_decision(run_dir: Path, text: str) -> bool:
    """Append one dated line to the Decisions section. Never raises."""
    return _append_under(run_dir, "## Decisions", f"- {_now()}: {text.strip()}")


def append_compiled_truth(run_dir: Path, text: str) -> bool:
    """Append one verified claim to Compiled truth. The caller owns the
    'verified' part — this seam doesn't police it. Never raises."""
    return _append_under(run_dir, "## Compiled truth", f"- {text.strip()}")


def record_child(run_dir: Path, child_handle_id: str, child_goal: str) -> bool:
    """Record a forked child under Threads. Never raises."""
    return _append_under(
        run_dir, "## Threads",
        f"- `{child_handle_id}` — {child_goal.strip()[:120]} (open, {_now()})",
    )


def record_close(run_dir: Path, *, status: str, note: str = "") -> bool:
    """Append the closing decision. Never raises."""
    line = f"thread closed: {status}"
    if note.strip():
        line += f" — {note.strip()[:200]}"
    return append_decision(run_dir, line)


def _append_under(run_dir: Path, section_prefix: str, line: str) -> bool:
    """Append `line` at the end of the section whose header starts with
    `section_prefix`, removing that section's '(none...)' placeholder if
    present. Markdown-append, no real parsing — the artifact stays a plain
    file a human can edit without breaking the writers."""
    try:
        path = brain_path(run_dir)
        if not path.exists():
            return False
        lines = path.read_text(encoding="utf-8").splitlines()

        start = next((i for i, l in enumerate(lines)
                      if l.startswith(section_prefix)), None)
        if start is None:
            return False
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))

        section = [l for l in lines[start:end]
                   if not l.strip().startswith("- (none")]
        # Drop trailing blanks so the new line lands flush with the list,
        # then restore one blank before the next section header.
        while section and not section[-1].strip():
            section.pop()
        section.append(line)
        section.append("")

        lines[start:end] = section
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception as exc:
        log.debug("thread_brain append failed for %s: %s", run_dir, exc)
        return False


def load_thread_brain(run_dir: Path, *, max_chars: int = _MAX_INJECT_CHARS) -> str:
    """The artifact text for NavigatorInput.goal_brain ('' if absent).

    Over the cap, keeps the head (Intent + Compiled truth lead) and the tail
    (newest decisions) and drops the middle — both ends carry more steering
    signal than the middle of a long decision list.
    """
    try:
        text = brain_path(run_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    except Exception as exc:
        log.debug("load_thread_brain failed for %s: %s", run_dir, exc)
        return ""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-(max_chars // 2 - 40):]
    return head + "\n\n[... middle elided for injection ...]\n\n" + tail
