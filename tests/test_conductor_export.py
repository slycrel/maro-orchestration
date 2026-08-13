"""Tests for maro-export / maro-import workspace backup."""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import from the script
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from maro_export import export_workspace, import_workspace, _should_exclude


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    """Create a minimal workspace for testing."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MARO_WORKSPACE", str(ws))

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

    # 2026-08-12 cleanup pins: exclusion reach is class-specific.
    # Secret-shaped patterns match anywhere (over-exclusion errs safe);
    # bulk/transient patterns anchor to the workspace root (retention
    # wins — the old anywhere-match ate .git/logs/* reflogs inside
    # archived project repos on the box).

    def test_nested_git_reflogs_are_included(self):
        assert _should_exclude(
            "projects/_archive/x/repo/.git/logs/HEAD") is False
        assert _should_exclude(
            "projects/ledger-kata-fix/ledger-kata/.git/logs/refs/heads/main"
        ) is False

    def test_root_logs_still_excluded(self):
        assert _should_exclude("logs/heartbeat.log") is True

    def test_nested_prototypes_and_offset_are_included(self):
        assert _should_exclude("runs/abc/artifact/prototypes/p.py") is False
        assert _should_exclude("runs/abc/telegram_offset.txt") is False
        assert _should_exclude("prototypes/old_stuff") is True

    def test_nested_secrets_still_excluded(self):
        assert _should_exclude("runs/abc/secrets/leaked.txt") is True
        assert _should_exclude("projects/x/deploy.key") is True

    def test_export_shape_workspace_prefix_normalized(self):
        # Export passes tar arcnames with a leading "workspace/" component;
        # root-anchored patterns must mean the same thing there.
        assert _should_exclude("workspace/logs/x.log") is True
        assert _should_exclude("workspace/memory/outcomes.jsonl") is False
        assert _should_exclude("workspace/runs/x/.git/logs/HEAD") is False


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
        old_env = os.environ.get("MARO_WORKSPACE")
        os.environ["MARO_WORKSPACE"] = str(new_ws)
        try:
            import_workspace(archive)
            restored = (new_ws / "memory" / "test.jsonl").read_text()
            assert '{"key":"value"}' in restored
        finally:
            if old_env:
                os.environ["MARO_WORKSPACE"] = old_env


class TestSqliteSnapshot:
    """Hot-export quiesce (2026-08-12): .db files go into the archive via
    the sqlite backup API — a raw byte copy of a live database can be
    torn. Non-sqlite *.db falls back to raw bytes, never dropped."""

    def _make_db(self, path):
        import sqlite3
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE t (k TEXT)")
        con.execute("INSERT INTO t VALUES ('v1')")
        con.commit()
        con.close()

    def test_db_snapshotted_and_sidecars_folded(self, workspace, tmp_path):
        self._make_db(workspace / "corr.db")
        # Stale sidecars next to a snapshottable db must NOT ship — a
        # fresh snapshot beside an old wal corrupts on open.
        (workspace / "corr.db-wal").write_bytes(b"stale-wal")
        (workspace / "corr.db-shm").write_bytes(b"stale-shm")

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        with tarfile.open(archive, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert "workspace/corr.db" in names
        assert not any(n.endswith((".db-wal", ".db-shm")) for n in names)

        # The archived copy is a valid database with the row intact.
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        import os
        old_env = os.environ.get("MARO_WORKSPACE")
        os.environ["MARO_WORKSPACE"] = str(out_dir)
        try:
            import_workspace(archive)
        finally:
            if old_env:
                os.environ["MARO_WORKSPACE"] = old_env
        import sqlite3
        con = sqlite3.connect(str(out_dir / "corr.db"))
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT k FROM t").fetchone()[0] == "v1"
        con.close()

    def test_non_sqlite_db_falls_back_raw_with_sidecars(
            self, workspace, tmp_path):
        (workspace / "garbage.db").write_bytes(b"not a database")
        (workspace / "garbage.db-wal").write_bytes(b"raw-wal")

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        with tarfile.open(archive, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            raw = tar.extractfile("workspace/garbage.db").read()
        assert raw == b"not a database"
        assert "workspace/garbage.db-wal" in names

    def test_export_counts_files_not_directories(
            self, workspace, tmp_path, capsys):
        # Fixture: 5 includable files (config, playbook, 2× memory,
        # 1× skills); secrets + telegram_offset excluded. Dirs must not
        # inflate the file count (the old counter reported 29,123 for a
        # 23,555-file box workspace).
        export_workspace(output_path=tmp_path / "export.tar.gz")
        err = capsys.readouterr().err
        assert "Done: 5 files" in err


class TestImportCleanAndMerge:
    """Import is a MERGE by default (2026-08-12: now says so out loud);
    --clean moves the existing workspace aside — never deletes."""

    def _import_into(self, ws, archive, monkeypatch, **kw):
        monkeypatch.setenv("MARO_WORKSPACE", str(ws))
        return import_workspace(archive, **kw)

    def test_merge_warns_and_keeps_existing(
            self, workspace, tmp_path, monkeypatch, capsys):
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "stale.txt").write_text("pre-existing")
        self._import_into(dest, archive, monkeypatch)
        err = capsys.readouterr().err
        assert "MERGES" in err
        assert (dest / "stale.txt").exists()
        assert (dest / "playbook.md").exists()

    def test_clean_moves_existing_aside_never_deletes(
            self, workspace, tmp_path, monkeypatch):
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "stale.txt").write_text("pre-existing")
        self._import_into(dest, archive, monkeypatch, clean=True)
        assert not (dest / "stale.txt").exists()
        assert (dest / "playbook.md").exists()
        aside = list(tmp_path.glob("dest.pre-import-*"))
        assert len(aside) == 1
        assert (aside[0] / "stale.txt").read_text() == "pre-existing"

    def test_clean_on_empty_dest_is_plain_import(
            self, workspace, tmp_path, monkeypatch):
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        dest = tmp_path / "dest-empty"
        dest.mkdir()
        self._import_into(dest, archive, monkeypatch, clean=True)
        assert (dest / "playbook.md").exists()
        assert list(tmp_path.glob("dest-empty.pre-import-*")) == []

    def test_clean_twice_in_same_second_gets_unique_asides(
            self, workspace, tmp_path, monkeypatch):
        """Second-resolution aside names collide on back-to-back clean
        imports — rename onto an existing dir raises (review of 707a541,
        reproduced). Aside names must be uniqued."""
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "gen1.txt").write_text("1")
        self._import_into(dest, archive, monkeypatch, clean=True)
        (dest / "gen2.txt").write_text("2")
        self._import_into(dest, archive, monkeypatch, clean=True)
        asides = sorted(tmp_path.glob("dest.pre-import-*"))
        assert len(asides) == 2
        contents = {p.name for a in asides for p in a.iterdir()}
        assert "gen1.txt" in contents and "gen2.txt" in contents


class TestReviewHardening:
    """Pins for the 3-lens review of 707a541 — every defect below was
    reproduced by a reviewer (or by the lead) before being fixed."""

    def test_traversal_sibling_prefix_rejected(self, tmp_path, monkeypatch):
        """str.startswith containment passed '<ws>-evil' as inside '<ws>';
        with the pre-filter extract fallback that was an out-of-workspace
        write (consensus HIGH, reproduced 3×). relative_to must reject."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(ws))

        evil = tmp_path / "evil.tar.gz"
        payload = tmp_path / "payload.txt"
        payload.write_text("pwn")
        with tarfile.open(evil, "w:gz") as tar:
            tar.add(str(payload),
                    arcname="workspace/../workspace-evil/pwn.txt")
        import_workspace(evil)
        assert not (tmp_path / "workspace-evil").exists()

    def test_snapshot_survives_uri_metacharacters(self, tmp_path):
        """f-string file: URIs let '?'/'#' in a filename truncate the path,
        silently 'succeeding' with an EMPTY snapshot (HIGH, reproduced).
        as_uri percent-encoding + page-count parity close the class."""
        import sqlite3
        from maro_export import _snapshot_sqlite
        src = tmp_path / "valid?.db"
        con = sqlite3.connect(str(src))
        con.execute("CREATE TABLE t (k TEXT)")
        con.execute("INSERT INTO t VALUES ('v1')")
        con.commit()
        con.close()
        snap = tmp_path / "snap.db"
        assert _snapshot_sqlite(src, snap) is True
        c2 = sqlite3.connect(str(snap))
        assert c2.execute("SELECT k FROM t").fetchone()[0] == "v1"
        c2.close()

    def test_snapshot_preserves_source_mode(self, workspace, tmp_path):
        """tar.add on the temp snapshot stamped 0644/now over a 0600
        source db — restore would broaden access (reproduced). The
        snapshot's bytes must carry the SOURCE's metadata."""
        import sqlite3
        src = workspace / "private.db"
        con = sqlite3.connect(str(src))
        con.execute("CREATE TABLE t (k TEXT)")
        con.commit()
        con.close()
        src.chmod(0o600)

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            ti = tar.getmember("workspace/private.db")
        assert ti.mode & 0o777 == 0o600

    def test_import_counts_files_apart_from_dirs(
            self, workspace, tmp_path, monkeypatch, capsys):
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        dest = tmp_path / "count-dest"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        n = import_workspace(archive)
        err = capsys.readouterr().err
        # Fixture exports 5 real files (memory/skills dirs ride apart).
        assert n == 5
        assert "Done: 5 files" in err
