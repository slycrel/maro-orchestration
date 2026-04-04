"""skill_loader.py — Phase 41 step 3: SKILL.md format + progressive skill disclosure.

Skills as first-class markdown files with YAML frontmatter:

    skills/<name>.md
    ---
    name: web_research
    description: "Research using web sources and synthesize findings"
    roles_allowed: [worker, short]
    triggers: [research, investigate, find information, web search]
    ---
    Full skill body — injected only when the skill is requested.

Progressive disclosure model:
  1. Summaries (name + description + triggers) injected into decompose prompt for all roles.
  2. Full body loaded on demand when the step executor resolves a skill match.

This supplements (does not replace) the JSONL-based skills.py system — skills.py handles
auto-promoted runtime skills; skill_loader handles hand-authored / curated skills.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("poe.skill_loader")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = _REPO_ROOT / "skills"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SkillSummary:
    """Lightweight descriptor — what appears in the decompose prompt."""
    name: str
    description: str
    roles_allowed: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    file_path: Optional[Path] = None

    def matches_role(self, role: str) -> bool:
        """Empty roles_allowed means visible to all roles."""
        if not self.roles_allowed:
            return True
        return role in self.roles_allowed

    def matches_goal(self, goal: str) -> bool:
        """True if any trigger matches (substring or glob) the goal text."""
        goal_lower = goal.lower()
        for trigger in self.triggers:
            t = trigger.lower()
            if t in goal_lower or fnmatch.fnmatch(goal_lower, t):
                return True
        return False


# ---------------------------------------------------------------------------
# SKILL.md parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML-ish frontmatter from body. Returns (meta_dict, body_text).

    Handles the subset of YAML we use:
      key: value
      key: [item1, item2]
    No full YAML parser dependency needed for this narrow format.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw_meta = m.group(1)
    body = text[m.end():]
    meta: dict = {}

    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            # Inline list: [a, b, c]
            inner = value[1:-1]
            items = [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
            meta[key] = items
        else:
            meta[key] = value.strip("\"'")

    return meta, body


def load_skill_file(path: Path) -> Optional[SkillSummary]:
    """Parse a single SKILL.md file into a SkillSummary. Returns None on error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("skill_loader: cannot read %s: %s", path, exc)
        return None

    meta, body = _parse_frontmatter(text)

    name = meta.get("name") or path.stem
    description = meta.get("description", "")
    roles_raw = meta.get("roles_allowed", [])
    triggers_raw = meta.get("triggers", [])

    # Normalise — may come in as str if single-item list was stripped of brackets
    roles = roles_raw if isinstance(roles_raw, list) else [roles_raw]
    triggers = triggers_raw if isinstance(triggers_raw, list) else [triggers_raw]

    return SkillSummary(
        name=name,
        description=description,
        roles_allowed=roles,
        triggers=triggers,
        file_path=path,
    )


# ---------------------------------------------------------------------------
# SkillLoader
# ---------------------------------------------------------------------------

class SkillLoader:
    """Loads SKILL.md files from the skills/ directory.

    Usage:
        loader = SkillLoader()
        summaries = loader.load_summaries(role="worker")
        block = loader.get_summaries_block(role="worker", goal="research X")
        full_text = loader.load_full("web_research")
    """

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self._dir = skills_dir or SKILLS_DIR
        self._cache: Optional[Dict[str, SkillSummary]] = None

    def _all_summaries(self) -> Dict[str, SkillSummary]:
        """Load and cache all skills from disk. Keyed by name."""
        if self._cache is not None:
            return self._cache
        result: Dict[str, SkillSummary] = {}
        if not self._dir.exists():
            log.debug("skill_loader: skills dir %s not found", self._dir)
            self._cache = result
            return result
        for path in sorted(self._dir.glob("*.md")):
            skill = load_skill_file(path)
            if skill is not None:
                result[skill.name] = skill
                log.debug("skill_loader: loaded skill %r from %s", skill.name, path.name)
        self._cache = result
        return result

    def invalidate(self) -> None:
        """Clear the cache (useful after writing new skill files)."""
        self._cache = None

    def load_summaries(self, role: Optional[str] = None) -> List[SkillSummary]:
        """Return all skills visible to role (empty role list = all roles)."""
        all_skills = list(self._all_summaries().values())
        if role is None:
            return all_skills
        return [s for s in all_skills if s.matches_role(role)]

    def find_matching(self, goal: str, role: Optional[str] = None) -> List[SkillSummary]:
        """Return skills that match the goal text and are visible to role."""
        candidates = self.load_summaries(role=role)
        return [s for s in candidates if s.matches_goal(goal)]

    def load_full(self, name: str) -> Optional[str]:
        """Return the full markdown body of a skill by name.

        The body is everything after the frontmatter delimiter. Returns None
        if the skill or file doesn't exist.
        """
        skills = self._all_summaries()
        summary = skills.get(name)
        if summary is None or summary.file_path is None:
            return None
        try:
            text = summary.file_path.read_text(encoding="utf-8")
        except OSError:
            return None
        _, body = _parse_frontmatter(text)
        return body.strip() or None

    def get_summaries_block(
        self,
        role: Optional[str] = None,
        goal: Optional[str] = None,
    ) -> str:
        """Return a formatted block for injection into the decompose prompt.

        If goal is provided, only matching skills are included. Otherwise all
        visible skills are listed. Each entry shows name + description + triggers
        (not the full body — that's progressive disclosure).

        Returns empty string if no skills match.
        """
        if goal is not None:
            skills = self.find_matching(goal, role=role)
        else:
            skills = self.load_summaries(role=role)

        if not skills:
            return ""

        lines = ["Curated skills available for this goal:"]
        for s in skills:
            trigger_str = ", ".join(s.triggers[:4])  # show up to 4 triggers
            lines.append(f"\n• {s.name}: {s.description}")
            if trigger_str:
                lines.append(f"  Triggers: {trigger_str}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto-extraction: export promoted runtime Skill → SKILL.md
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert a skill name to a safe filename slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "_", slug)
    slug = slug.strip("_-")
    return slug or "unnamed_skill"


def export_skill_as_markdown(
    skill,  # Skill dataclass from skills.py (no import to avoid circular dep)
    *,
    skills_dir: Optional[Path] = None,
    overwrite: bool = False,
) -> Optional[Path]:
    """Write a runtime Skill (from skills.jsonl) as a SKILL.md curated file.

    Called automatically when a runtime skill is promoted to 'established' tier.
    Produces a SKILL.md with YAML frontmatter populated from the Skill's fields:
      - name, description, trigger_patterns → triggers
      - steps_template → ## Steps section
      - success_rate, use_count → ## Stats section

    Args:
        skill:       Skill dataclass instance from skills.py.
        skills_dir:  Target directory (default: SKILLS_DIR).
        overwrite:   If False (default), skips if the file already exists.

    Returns:
        Path of the written file, or None if skipped/failed.
    """
    target_dir = skills_dir or SKILLS_DIR
    slug = _slugify(getattr(skill, "name", "unnamed"))
    dest = target_dir / f"{slug}.md"

    if dest.exists() and not overwrite:
        log.debug("export_skill_as_markdown: %s already exists, skipping", dest.name)
        return None

    name = getattr(skill, "name", slug)
    description = getattr(skill, "description", "")
    triggers = getattr(skill, "trigger_patterns", [])
    steps = getattr(skill, "steps_template", [])
    success_rate = getattr(skill, "success_rate", 1.0)
    use_count = getattr(skill, "use_count", 0)
    tier = getattr(skill, "tier", "provisional")

    # Format triggers for YAML inline list
    trigger_str = "[" + ", ".join(repr(t) for t in triggers[:8]) + "]"

    # Build SKILL.md content
    lines = [
        "---",
        f"name: {slug}",
        f'description: "{description}"',
        "roles_allowed: [worker]",
        f"triggers: {trigger_str}",
        "---",
        "",
        f"# {name}",
        "",
        f"> Auto-extracted from runtime skill (tier: {tier}, "
        f"use_count: {use_count}, success_rate: {success_rate:.0%})",
        "",
    ]

    if description:
        lines += [f"{description}", ""]

    if steps:
        lines.append("## Steps")
        lines.append("")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    lines.append("## Stats")
    lines.append(f"- Uses: {use_count}")
    lines.append(f"- Success rate: {success_rate:.0%}")
    lines.append(f"- Tier: {tier}")
    lines.append("")

    content = "\n".join(lines)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        log.info("export_skill_as_markdown: wrote %s", dest)
        # Invalidate the module-level singleton's cache
        skill_loader.invalidate()
        return dest
    except OSError as exc:
        log.warning("export_skill_as_markdown: failed to write %s: %s", dest, exc)
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

skill_loader: SkillLoader = SkillLoader()
