"""Tests for the Director's Playbook — evolving operational wisdom."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playbook import (
    load_playbook,
    seed_playbook,
    inject_playbook,
    append_to_playbook,
)


@pytest.fixture(autouse=True)
def _isolate_workspace(monkeypatch, tmp_path):
    """Point workspace to temp dir."""
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


class TestPlaybookSeed:

    def test_seed_creates_file(self, tmp_path):
        seed_playbook()
        path = tmp_path / "playbook.md"
        assert path.exists()
        content = path.read_text()
        assert "Director's Playbook" in content
        assert "## Decomposition" in content

    def test_seed_is_idempotent(self, tmp_path):
        seed_playbook()
        path = tmp_path / "playbook.md"
        original = path.read_text()
        seed_playbook()  # Second call should not overwrite
        assert path.read_text() == original

    def test_load_creates_seed_if_missing(self, tmp_path):
        text = load_playbook()
        assert "Director's Playbook" in text
        assert (tmp_path / "playbook.md").exists()


class TestPlaybookInjection:

    def test_inject_returns_operational_content(self, tmp_path):
        seed_playbook()
        block = inject_playbook()
        assert "## Operational Playbook" in block
        assert "Decomposition" in block

    def test_inject_respects_max_chars(self, tmp_path):
        seed_playbook()
        block = inject_playbook(max_chars=200)
        assert len(block) <= 300  # Some overhead for header

    def test_inject_empty_when_no_content(self, tmp_path):
        # Write an empty playbook (no ## headers)
        (tmp_path / "playbook.md").write_text("Just a title\n")
        block = inject_playbook()
        assert block == ""


class TestPlaybookAppend:

    def test_append_to_existing_section(self, tmp_path):
        seed_playbook()
        append_to_playbook(
            "Always check token counts before decompose.",
            section="Decomposition",
        )
        text = load_playbook()
        assert "Always check token counts before decompose." in text

    def test_append_creates_new_section(self, tmp_path):
        seed_playbook()
        append_to_playbook(
            "New insight about debugging.",
            section="Debugging",
        )
        text = load_playbook()
        assert "## Debugging" in text
        assert "New insight about debugging." in text

    def test_append_includes_source(self, tmp_path):
        seed_playbook()
        append_to_playbook(
            "Token budgets need attention.",
            section="Cost",
            source="evolver:sug-001",
        )
        text = load_playbook()
        assert "evolver:sug-001" in text

    def test_append_auto_adds_dash_prefix(self, tmp_path):
        seed_playbook()
        append_to_playbook("no dash", section="Learned")
        text = load_playbook()
        assert "- no dash" in text

    def test_append_updates_timestamp(self, tmp_path):
        seed_playbook()
        append_to_playbook("test", section="Learned")
        text = load_playbook()
        assert "*Last updated:" in text


class TestWorkspaceSkillResolution:
    """Test that skill_loader scans workspace before repo."""

    def test_workspace_skills_dir_exists(self, tmp_path):
        from config import skills_dir
        sd = skills_dir()
        assert sd.exists()
        assert str(tmp_path) in str(sd)

    def test_workspace_personas_dir_exists(self, tmp_path):
        from config import personas_dir
        pd = personas_dir()
        assert pd.exists()
        assert str(tmp_path) in str(pd)

    def test_skill_loader_scans_workspace(self, tmp_path):
        """Workspace skill should be found by SkillLoader."""
        from config import skills_dir
        ws_skills = skills_dir()

        # Write a skill to workspace
        skill_md = ws_skills / "ws_test_skill.md"
        skill_md.write_text(
            "---\n"
            "name: ws-test-skill\n"
            "description: A workspace skill\n"
            "roles_allowed: [worker]\n"
            "triggers: [test workspace]\n"
            "---\n"
            "Body of the workspace skill.\n"
        )

        from skill_loader import SkillLoader
        loader = SkillLoader()
        loader.invalidate()
        summaries = loader.load_summaries()
        names = [s.name for s in summaries]
        assert "ws-test-skill" in names

    def test_workspace_skill_overrides_repo(self, tmp_path):
        """Workspace version should override repo version with same name."""
        from config import skills_dir
        ws_skills = skills_dir()

        # Write a skill that matches a repo skill name
        # (web_research exists in repo)
        skill_md = ws_skills / "web_research.md"
        skill_md.write_text(
            "---\n"
            "name: web_research\n"
            "description: EVOLVED version of web research\n"
            "roles_allowed: [worker]\n"
            "triggers: [research]\n"
            "---\n"
            "Evolved body.\n"
        )

        from skill_loader import SkillLoader
        loader = SkillLoader()
        loader.invalidate()
        summaries = {s.name: s for s in loader.load_summaries()}
        assert "web_research" in summaries
        assert "EVOLVED" in summaries["web_research"].description
