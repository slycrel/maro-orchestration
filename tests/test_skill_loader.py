"""Tests for Phase 41 step 3: skill_loader.py — SKILL.md format + progressive disclosure."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skill_loader import (
    SkillLoader,
    SkillSummary,
    _parse_frontmatter,
    _slugify,
    export_skill_as_markdown,
    load_skill_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


BASIC_SKILL = """\
    ---
    name: web_research
    description: "Research using web sources"
    roles_allowed: [worker, short]
    triggers: [research, investigate, web search]
    ---

    ## Steps
    1. Search
    2. Read
    3. Synthesize
    """

NO_FRONTMATTER_SKILL = """\
    ## Just a body
    No frontmatter here.
    """

EMPTY_ROLES_SKILL = """\
    ---
    name: universal_skill
    description: "Available to all roles"
    roles_allowed: []
    triggers: [always, universal]
    ---
    Body text.
    """


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_basic_string_fields(self):
        text = "---\nname: my_skill\ndescription: Does stuff\n---\nBody text."
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "my_skill"
        assert meta["description"] == "Does stuff"
        assert body.strip() == "Body text."

    def test_list_field(self):
        text = "---\ntriggers: [research, investigate]\n---\nBody."
        meta, _ = _parse_frontmatter(text)
        assert meta["triggers"] == ["research", "investigate"]

    def test_quoted_description(self):
        text = '---\ndescription: "Research using web"\n---\nBody.'
        meta, _ = _parse_frontmatter(text)
        assert meta["description"] == "Research using web"

    def test_no_frontmatter(self):
        text = "Just a plain body without any frontmatter."
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_list(self):
        text = "---\nroles_allowed: []\n---\nBody."
        meta, _ = _parse_frontmatter(text)
        assert meta["roles_allowed"] == []

    def test_single_item_list(self):
        text = "---\ntriggers: [research]\n---\nBody."
        meta, _ = _parse_frontmatter(text)
        assert meta["triggers"] == ["research"]

    def test_comments_skipped(self):
        text = "---\n# This is a comment\nname: my_skill\n---\nBody."
        meta, _ = _parse_frontmatter(text)
        assert meta["name"] == "my_skill"
        assert "#" not in meta

    def test_body_preserved_exactly(self):
        body_text = "## Steps\n1. Do A\n2. Do B\n"
        text = f"---\nname: x\n---\n{body_text}"
        _, body = _parse_frontmatter(text)
        assert body == body_text


# ---------------------------------------------------------------------------
# load_skill_file
# ---------------------------------------------------------------------------

class TestLoadSkillFile:
    def test_basic_load(self, tmp_path):
        p = _write_skill(tmp_path, "web_research.md", BASIC_SKILL)
        skill = load_skill_file(p)
        assert skill is not None
        assert skill.name == "web_research"
        assert skill.description == "Research using web sources"
        assert "worker" in skill.roles_allowed
        assert "short" in skill.roles_allowed
        assert "research" in skill.triggers

    def test_no_frontmatter_uses_stem(self, tmp_path):
        p = _write_skill(tmp_path, "my_custom.md", NO_FRONTMATTER_SKILL)
        skill = load_skill_file(p)
        assert skill is not None
        assert skill.name == "my_custom"

    def test_file_path_stored(self, tmp_path):
        p = _write_skill(tmp_path, "web_research.md", BASIC_SKILL)
        skill = load_skill_file(p)
        assert skill.file_path == p

    def test_missing_file_returns_none(self, tmp_path):
        result = load_skill_file(tmp_path / "nonexistent.md")
        assert result is None

    def test_empty_roles_allowed(self, tmp_path):
        p = _write_skill(tmp_path, "universal.md", EMPTY_ROLES_SKILL)
        skill = load_skill_file(p)
        assert skill.roles_allowed == []


# ---------------------------------------------------------------------------
# SkillSummary
# ---------------------------------------------------------------------------

class TestSkillSummary:
    def _make(self, roles=None, triggers=None):
        return SkillSummary(
            name="test_skill",
            description="A test skill",
            roles_allowed=roles or [],
            triggers=triggers or ["research", "investigate"],
        )

    def test_matches_role_empty_allows_all(self):
        s = self._make(roles=[])
        for role in ["worker", "short", "inspector", "director", "verifier"]:
            assert s.matches_role(role), f"should allow {role}"

    def test_matches_role_specific(self):
        s = self._make(roles=["worker", "short"])
        assert s.matches_role("worker")
        assert s.matches_role("short")
        assert not s.matches_role("inspector")
        assert not s.matches_role("director")

    def test_matches_goal_substring(self):
        s = self._make(triggers=["research", "investigate"])
        assert s.matches_goal("I need to research market trends")
        assert s.matches_goal("please investigate the failure")
        assert not s.matches_goal("just implement it")

    def test_matches_goal_case_insensitive(self):
        s = self._make(triggers=["research"])
        assert s.matches_goal("RESEARCH the topic")
        assert s.matches_goal("Research this")

    def test_matches_goal_glob_pattern(self):
        s = self._make(triggers=["debug*"])
        assert s.matches_goal("debug the failing test")
        assert s.matches_goal("debugging session")

    def test_matches_goal_no_match(self):
        s = self._make(triggers=["research"])
        assert not s.matches_goal("build a module")
        assert not s.matches_goal("fix the bug")


# ---------------------------------------------------------------------------
# SkillLoader
# ---------------------------------------------------------------------------

class TestSkillLoader:
    def _loader(self, tmp_path: Path, *skill_contents: tuple[str, str]) -> SkillLoader:
        """Create a loader pointing to tmp_path with named skill files."""
        for filename, content in skill_contents:
            _write_skill(tmp_path, filename, content)
        return SkillLoader(skills_dir=tmp_path)

    def test_load_summaries_basic(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        summaries = loader.load_summaries()
        assert len(summaries) == 1
        assert summaries[0].name == "web_research"

    def test_load_summaries_role_filter(self, tmp_path):
        loader = self._loader(
            tmp_path,
            ("web.md", BASIC_SKILL),        # roles: worker, short
            ("universal.md", EMPTY_ROLES_SKILL),  # roles: []
        )
        worker = loader.load_summaries(role="worker")
        names = {s.name for s in worker}
        assert "web_research" in names
        assert "universal_skill" in names  # empty roles = all

        inspector = loader.load_summaries(role="inspector")
        names = {s.name for s in inspector}
        assert "web_research" not in names  # inspector not in [worker, short]
        assert "universal_skill" in names   # empty roles = all

    def test_load_summaries_empty_dir(self, tmp_path):
        loader = SkillLoader(skills_dir=tmp_path)
        assert loader.load_summaries() == []

    def test_load_summaries_missing_dir(self, tmp_path):
        loader = SkillLoader(skills_dir=tmp_path / "nonexistent")
        assert loader.load_summaries() == []

    def test_find_matching_by_goal(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        matches = loader.find_matching("I need to research X")
        assert len(matches) == 1
        assert matches[0].name == "web_research"

    def test_find_matching_no_match(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        matches = loader.find_matching("implement a new feature")
        assert matches == []

    def test_find_matching_role_filtered(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        # inspector cannot use web_research (roles: worker, short)
        matches = loader.find_matching("research something", role="inspector")
        assert matches == []

    def test_load_full_returns_body(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        body = loader.load_full("web_research")
        assert body is not None
        assert "## Steps" in body
        assert "---" not in body  # frontmatter stripped

    def test_load_full_missing_skill(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        assert loader.load_full("nonexistent") is None

    def test_get_summaries_block_empty(self, tmp_path):
        loader = SkillLoader(skills_dir=tmp_path)
        assert loader.get_summaries_block() == ""

    def test_get_summaries_block_with_skills(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        block = loader.get_summaries_block()
        assert "web_research" in block
        assert "Research using web sources" in block

    def test_get_summaries_block_goal_filter(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        block_match = loader.get_summaries_block(goal="research market trends")
        block_nomatch = loader.get_summaries_block(goal="implement a module")
        assert "web_research" in block_match
        assert block_nomatch == ""

    def test_get_summaries_block_includes_triggers(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        block = loader.get_summaries_block()
        assert "Triggers:" in block

    def test_invalidate_clears_cache(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        _ = loader.load_summaries()  # populate cache
        assert loader._cache is not None

        # Add a new file
        _write_skill(tmp_path, "new_skill.md", EMPTY_ROLES_SKILL)
        loader.invalidate()

        summaries = loader.load_summaries()
        names = {s.name for s in summaries}
        assert "universal_skill" in names  # picked up after invalidate

    def test_cache_populated_after_first_load(self, tmp_path):
        loader = self._loader(tmp_path, ("web.md", BASIC_SKILL))
        assert loader._cache is None
        loader.load_summaries()
        assert loader._cache is not None  # cache populated after first call

    def test_multiple_skills_loaded(self, tmp_path):
        loader = self._loader(
            tmp_path,
            ("web.md", BASIC_SKILL),
            ("universal.md", EMPTY_ROLES_SKILL),
        )
        summaries = loader.load_summaries()
        assert len(summaries) == 2


# ---------------------------------------------------------------------------
# Integration: default skills dir (real files)
# ---------------------------------------------------------------------------

class TestDefaultSkillsDir:
    """Load the actual skills/ directory that ships with the repo."""

    def _real_loader(self) -> SkillLoader:
        return SkillLoader()  # uses SKILLS_DIR = repo/skills/

    def test_skills_dir_has_files(self):
        from skill_loader import SKILLS_DIR
        assert SKILLS_DIR.exists(), f"skills/ dir missing at {SKILLS_DIR}"
        md_files = list(SKILLS_DIR.glob("*.md"))
        assert len(md_files) >= 1, "at least one SKILL.md must exist"

    def test_all_real_skills_parse(self):
        loader = self._real_loader()
        summaries = loader.load_summaries()
        for s in summaries:
            assert s.name, f"skill from {s.file_path} has no name"
            assert s.description, f"skill {s.name} has no description"

    def test_real_skills_have_full_body(self):
        loader = self._real_loader()
        for s in loader.load_summaries():
            body = loader.load_full(s.name)
            assert body is not None, f"skill {s.name} has no body"
            assert len(body) > 10, f"skill {s.name} body too short"

    def test_web_research_skill_exists(self):
        loader = self._real_loader()
        skill = loader._all_summaries().get("web_research")
        assert skill is not None

    def test_code_implement_skill_exists(self):
        loader = self._real_loader()
        skill = loader._all_summaries().get("code_implement")
        assert skill is not None

    def test_worker_sees_curated_skills(self):
        loader = self._real_loader()
        worker_skills = loader.load_summaries(role="worker")
        assert len(worker_skills) >= 1

    def test_summaries_block_nonempty_for_research_goal(self):
        loader = self._real_loader()
        block = loader.get_summaries_block(role="worker", goal="research web sources")
        assert block != ""
        assert "web_research" in block


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic_name(self):
        assert _slugify("web_research") == "web_research"

    def test_spaces_to_underscores(self):
        assert _slugify("web research") == "web_research"

    def test_lowercase(self):
        assert _slugify("WebResearch") == "webresearch"

    def test_special_chars_stripped(self):
        result = _slugify("research (v2)!")
        assert "(" not in result
        assert "!" not in result

    def test_empty_returns_fallback(self):
        assert _slugify("") == "unnamed_skill"

    def test_dashes_preserved(self):
        result = _slugify("web-research")
        assert "web" in result
        assert "research" in result


# ---------------------------------------------------------------------------
# export_skill_as_markdown
# ---------------------------------------------------------------------------

class TestExportSkillAsMarkdown:
    def _make_mock_skill(self, **kwargs):
        """Create a simple namespace object mimicking a Skill dataclass."""
        from types import SimpleNamespace
        defaults = {
            "name": "test_research",
            "description": "Test research skill",
            "trigger_patterns": ["research", "investigate"],
            "steps_template": ["Step 1: Do X", "Step 2: Do Y"],
            "success_rate": 0.85,
            "use_count": 7,
            "tier": "established",
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_creates_file(self, tmp_path):
        skill = self._make_mock_skill()
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        assert result is not None
        assert result.exists()

    def test_filename_is_slug(self, tmp_path):
        skill = self._make_mock_skill(name="web research")
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        assert result.name == "web_research.md"

    def test_frontmatter_name(self, tmp_path):
        skill = self._make_mock_skill(name="my_skill")
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        content = result.read_text()
        assert "name: my_skill" in content

    def test_frontmatter_description(self, tmp_path):
        skill = self._make_mock_skill(description="Does cool stuff")
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        content = result.read_text()
        assert "Does cool stuff" in content

    def test_frontmatter_triggers(self, tmp_path):
        skill = self._make_mock_skill(trigger_patterns=["alpha", "beta"])
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        content = result.read_text()
        assert "alpha" in content
        assert "beta" in content

    def test_steps_in_body(self, tmp_path):
        skill = self._make_mock_skill(steps_template=["Do A", "Do B", "Do C"])
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        content = result.read_text()
        assert "Do A" in content
        assert "Do B" in content

    def test_stats_section(self, tmp_path):
        skill = self._make_mock_skill(use_count=12, success_rate=0.9)
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        content = result.read_text()
        assert "## Stats" in content
        assert "12" in content

    def test_no_overwrite_by_default(self, tmp_path):
        skill = self._make_mock_skill(description="version 1")
        export_skill_as_markdown(skill, skills_dir=tmp_path)
        skill2 = self._make_mock_skill(description="version 2")
        result2 = export_skill_as_markdown(skill2, skills_dir=tmp_path)
        assert result2 is None  # skipped — file already exists

    def test_overwrite_flag(self, tmp_path):
        skill = self._make_mock_skill(description="version 1")
        export_skill_as_markdown(skill, skills_dir=tmp_path)
        skill2 = self._make_mock_skill(description="version 2")
        result2 = export_skill_as_markdown(skill2, skills_dir=tmp_path, overwrite=True)
        assert result2 is not None
        assert "version 2" in result2.read_text()

    def test_creates_dir_if_needed(self, tmp_path):
        new_dir = tmp_path / "new_skills"
        assert not new_dir.exists()
        skill = self._make_mock_skill()
        result = export_skill_as_markdown(skill, skills_dir=new_dir)
        assert result is not None
        assert new_dir.exists()

    def test_valid_frontmatter_parseable(self, tmp_path):
        skill = self._make_mock_skill()
        result = export_skill_as_markdown(skill, skills_dir=tmp_path)
        content = result.read_text()
        meta, body = _parse_frontmatter(content)
        assert meta.get("name") is not None
        assert meta.get("description") is not None

    def test_exported_file_loadable(self, tmp_path):
        skill = self._make_mock_skill(name="loadable_skill")
        export_skill_as_markdown(skill, skills_dir=tmp_path)
        loader = SkillLoader(skills_dir=tmp_path)
        summaries = loader.load_summaries()
        names = {s.name for s in summaries}
        assert "loadable_skill" in names
