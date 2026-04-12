"""Tests for poe-export / poe-import workspace backup."""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import from the script
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from poe_export import export_workspace, import_workspace, _should_exclude


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    """Create a minimal workspace for testing."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("POE_WORKSPACE", str(ws))

    # Create some files
    (ws / "config.yml").write_text("model: haiku\n")
    (ws / "playbook.md").write_text("# Playbook\n")

    mem = ws / "memory"
    mem.mkdir()
    (mem / "outcomes.jsonl").write_text('{"status":"done"}\n')
    (mem / "knowledge_nodes.jsonl").write_text('{"node_id":"n1"}\n')

    skills = ws / "skills"
    skills.mkdir()
    (skills / "evolved_skill.md").write_text("---\nname: test\n---\n")

    # Files that should be excluded
    secrets = ws / "secrets"
    secrets.mkdir()
    (secrets / "api_key.txt").write_text("sk-secret-123")
    (ws / "telegram_offset.txt").write_text("12345")

    return ws


class TestShouldExclude:

    def test_excludes_secrets(self):
        assert _should_exclude("secrets/api_key.txt") is True
        assert _should_exclude("secrets") is True

    def test_excludes_telegram_offset(self):
        assert _should_exclude("telegram_offset.txt") is True

    def test_excludes_prototypes(self):
        assert _should_exclude("prototypes/old_stuff") is True

    def test_includes_memory(self):
        assert _should_exclude("memory/outcomes.jsonl") is False

    def test_includes_config(self):
        assert _should_exclude("config.yml") is False

    def test_includes_playbook(self):
        assert _should_exclude("playbook.md") is False

    def test_includes_skills(self):
        assert _should_exclude("skills/my_skill.md") is False


class TestExport:

    def test_export_creates_archive(self, workspace, tmp_path):
        out = tmp_path / "export.tar.gz"
        result = export_workspace(output_path=out)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_export_excludes_secrets(self, workspace, tmp_path):
        out = tmp_path / "export.tar.gz"
        export_workspace(output_path=out)

        with tarfile.open(out, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert not any("secrets" in n for n in names)
        assert not any("telegram_offset" in n for n in names)

    def test_export_includes_learning_data(self, workspace, tmp_path):
        out = tmp_path / "export.tar.gz"
        export_workspace(output_path=out)

        with tarfile.open(out, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert any("outcomes.jsonl" in n for n in names)
        assert any("playbook.md" in n for n in names)
        assert any("config.yml" in n for n in names)


class TestImport:

    def test_import_restores_files(self, workspace, tmp_path):
        # Export
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        # Delete a file
        (workspace / "playbook.md").unlink()
        assert not (workspace / "playbook.md").exists()

        # Import
        count = import_workspace(archive)
        assert count > 0
        assert (workspace / "playbook.md").exists()

    def test_import_dry_run(self, workspace, tmp_path):
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        count = import_workspace(archive, dry_run=True)
        assert count == 0  # dry run doesn't extract

    def test_roundtrip_preserves_content(self, workspace, tmp_path):
        # Write specific content
        (workspace / "memory" / "test.jsonl").write_text('{"key":"value"}\n')

        # Export
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        # Clear and reimport to a new workspace
        new_ws = tmp_path / "new_workspace"
        new_ws.mkdir()

        import os
        old_env = os.environ.get("POE_WORKSPACE")
        os.environ["POE_WORKSPACE"] = str(new_ws)
        try:
            import_workspace(archive)
            restored = (new_ws / "memory" / "test.jsonl").read_text()
            assert '{"key":"value"}' in restored
        finally:
            if old_env:
                os.environ["POE_WORKSPACE"] = old_env
