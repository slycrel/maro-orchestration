"""Tests for maro-export / maro-import workspace backup."""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import from the script
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from maro_export import (export_workspace, import_workspace, inspect_archive,
                         _should_exclude)


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


class TestMetaArea:
    """Archive format v2 (Jeremy 2026-08-13: 'the behavior IS data') —
    user-tier config + experiments ride the archive under meta/, staged
    non-destructively at import; provenance carries a custody chain."""

    def _user_dir(self, monkeypatch, tmp_path):
        d = tmp_path / "maro-user"
        d.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(d))
        return d

    def _staged_dir(self, dest):
        dirs = sorted((dest / ".import-meta").glob("import-*"))
        assert dirs, "no per-import staging dir created"
        return dirs[-1]

    def test_meta_carried_and_staged_not_applied(
            self, workspace, tmp_path, monkeypatch, capsys):
        user = self._user_dir(monkeypatch, tmp_path)
        (user / "config.yml").write_text(
            "model: haiku\nscope_generation: true\n")
        exp = user / "experiments"
        (exp / "lt4").mkdir(parents=True)
        (exp / "lt4" / "results.md").write_text("# results\n")

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert "meta/user-config.yml" in names
        assert "meta/experiments/lt4/results.md" in names
        assert "meta/provenance.json" in names

        dest = tmp_path / "dest"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(archive)
        staging = self._staged_dir(dest)
        assert (staging / "user-config.yml").read_text().startswith("model:")
        assert (staging / "experiments" / "lt4" / "results.md").exists()
        # Behavior NOT applied without the flag: the importing machine's
        # user tier is untouched.
        err = capsys.readouterr().err
        assert "behavior not applied" in err
        assert not (user / "config.yml").read_text().startswith("REDACTED")

    def test_apply_meta_places_config_with_backup(
            self, workspace, tmp_path, monkeypatch):
        user = self._user_dir(monkeypatch, tmp_path)
        (user / "config.yml").write_text("model: opus\n")
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        # Simulate the importing machine: same user dir, different config.
        (user / "config.yml").write_text("model: haiku  # local\n")
        dest = tmp_path / "dest"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(archive, apply_meta=True)
        # 0-redaction config ships raw, so it round-trips byte-for-byte.
        assert (user / "config.yml").read_text() == "model: opus\n"
        backups = list(user.glob("config.yml.pre-import-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "model: haiku  # local\n"

    def test_credential_shaped_config_values_redacted(
            self, workspace, tmp_path, monkeypatch):
        user = self._user_dir(monkeypatch, tmp_path)
        (user / "config.yml").write_text(
            "model: haiku\ntelegram:\n  bot_token: 12345:AAbbCC\n"
            "  chat_id: 42\nmax_tokens: 4096\n")
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            text = tar.extractfile("meta/user-config.yml").read().decode()
        assert "12345:AAbbCC" not in text
        assert "REDACTED-BY-EXPORT" in text
        assert "42" in text          # chat_id — not credential-shaped
        assert "4096" in text        # max_tokens — key matches but value int

    def test_provenance_digest_and_custody_chain(
            self, workspace, tmp_path, monkeypatch, capsys):
        self._user_dir(monkeypatch, tmp_path)
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        dest = tmp_path / "dest"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(archive)
        err = capsys.readouterr().err
        assert "PROVENANCE (self-attested, UNSIGNED):" in err
        assert "workspace shape digest: OK" in err
        prov = json.loads(
            (self._staged_dir(dest) / "provenance.json").read_text())
        events = [e["event"] for e in prov["custody"]]
        assert events == ["export", "import"]
        assert prov["custody"][1]["shape_verified"] is True

    def test_digest_mismatch_warns(
            self, workspace, tmp_path, monkeypatch, capsys):
        self._user_dir(monkeypatch, tmp_path)
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        # Tamper: append a workspace file the shape digest never saw.
        with tarfile.open(tmp_path / "tampered.tar.gz", "w:gz") as out, \
                tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                f = tar.extractfile(m) if m.isfile() else None
                out.addfile(m, f)
            import io as _io
            ti = tarfile.TarInfo("workspace/injected.txt")
            payload = b"surprise"
            ti.size = len(payload)
            out.addfile(ti, _io.BytesIO(payload))
        dest = tmp_path / "dest"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(tmp_path / "tampered.tar.gz")
        err = capsys.readouterr().err
        assert "MISMATCH" in err
        prov = json.loads(
            (self._staged_dir(dest) / "provenance.json").read_text())
        assert prov["custody"][-1]["shape_verified"] is False

    def test_v1_archive_without_meta_imports_clean(
            self, workspace, tmp_path, monkeypatch, capsys):
        # Hand-build a v1-shaped archive: workspace members only.
        archive = tmp_path / "v1.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(str(workspace / "playbook.md"),
                    arcname="workspace/playbook.md")
        dest = tmp_path / "dest"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        n = import_workspace(archive)
        err = capsys.readouterr().err
        assert n == 1
        assert "v1 archive (no meta/)" in err
        assert not (dest / ".import-meta").exists()

    def test_reexport_of_imported_workspace_skips_staging_dir(
            self, workspace, tmp_path, monkeypatch):
        staging = workspace / ".import-meta"
        staging.mkdir()
        (staging / "provenance.json").write_text("{}")
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert not any(".import-meta" in n for n in names)


class TestV2ReviewHardening:
    """Pins for the 3-lens review of c257a48 — every defect below was
    reproduced by a reviewer (or the lead) before being fixed. Archives
    are treated as UNTRUSTED input on import."""

    def _user_dir(self, monkeypatch, tmp_path):
        d = tmp_path / "maro-user"
        d.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(d))
        return d

    def _staged(self, dest):
        dirs = sorted((dest / ".import-meta").glob("import-*"))
        return dirs[-1] if dirs else None

    # --- redactor: structural, all leak shapes, no benign clobber -------

    def test_redactor_catches_nested_block_and_inline(self, monkeypatch,
                                                      tmp_path):
        from maro_export import _redact_config_text
        text = ("api_key:\n  primary: NESTED-SECRET\n"
                "bot_token: |\n  BLOCK-SECRET\n"
                "auth: {api_key: INLINE-SECRET}\n"
                "max_tokens: 4096\n"
                "token_budget: 2000\n")
        out, n, ok = _redact_config_text(text)
        assert ok and n == 3
        for leaked in ("NESTED-SECRET", "BLOCK-SECRET", "INLINE-SECRET"):
            assert leaked not in out
        assert "4096" in out and "2000" in out  # int values under cred keys

    def test_redactor_catches_yaml_alias_copy(self):
        # Whole-changeset review 2026-08-13 (Architect): an anchor aliases
        # a secret out from under its credential key into a benign one.
        from maro_export import _redact_config_text
        out, n, ok = _redact_config_text(
            "api_key: &s supersecret\nlabel: *s\n")
        assert ok and "supersecret" not in out

    def test_redactor_catches_binary_scalar_under_cred_key(self):
        # !!binary secret is bytes, which the str-only check skipped.
        from maro_export import _redact_config_text
        out, n, ok = _redact_config_text(
            "api_key: !!binary c3VwZXJzZWNyZXQ=\n")
        assert ok and "c3VwZXJzZWNyZXQ" not in out

    def test_redactor_fails_closed_on_unparseable(self):
        from maro_export import _redact_config_text
        out, n, ok = _redact_config_text("model: [unclosed\n")
        assert ok is False and out == ""

    def test_unparseable_config_not_exported(self, workspace, tmp_path,
                                             monkeypatch, capsys):
        user = self._user_dir(monkeypatch, tmp_path)
        (user / "config.yml").write_text("model: [unterminated\n")
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert "meta/user-config.yml" not in names
        assert "fail closed" in capsys.readouterr().err

    # --- import member-type / link-target allowlist --------------------

    def test_hostile_absolute_symlink_member_rejected(
            self, tmp_path, monkeypatch):
        arc = tmp_path / "evil.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            ti = tarfile.TarInfo("workspace/leak")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            tar.addfile(ti)
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc)
        assert not (dest / "leak").exists()
        assert not (dest / "leak").is_symlink()

    def test_hostile_fifo_member_rejected(self, tmp_path, monkeypatch):
        arc = tmp_path / "evil.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            ti = tarfile.TarInfo("workspace/pipe")
            ti.type = tarfile.FIFOTYPE
            tar.addfile(ti)
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc)
        assert not (dest / "pipe").exists()

    def test_internal_relative_symlink_still_imports(
            self, tmp_path, monkeypatch):
        arc = tmp_path / "ok.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            d = tarfile.TarInfo("workspace/venv/lib")
            d.type = tarfile.DIRTYPE
            tar.addfile(d)
            ti = tarfile.TarInfo("workspace/venv/lib64")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "lib"
            tar.addfile(ti)
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc)
        assert (dest / "venv" / "lib64").is_symlink()

    # --- format gate ---------------------------------------------------

    def test_future_format_refused_without_mutation(
            self, tmp_path, monkeypatch):
        import io as _io
        arc = tmp_path / "future.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            prov = json.dumps({"format": 99, "custody": []}).encode()
            ti = tarfile.TarInfo("meta/provenance.json")
            ti.size = len(prov)
            tar.addfile(ti, _io.BytesIO(prov))
            body = b"from the future"
            ti2 = tarfile.TarInfo("workspace/future.txt")
            ti2.size = len(body)
            tar.addfile(ti2, _io.BytesIO(body))
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        with pytest.raises(SystemExit):
            import_workspace(arc)
        assert not (dest / "future.txt").exists()

    # --- malformed provenance must not crash after extraction ----------

    def test_malformed_provenance_no_crash_after_extract(
            self, tmp_path, monkeypatch):
        import io as _io
        arc = tmp_path / "bad.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            body = b"real"
            ti = tarfile.TarInfo("workspace/keep.txt")
            ti.size = len(body)
            tar.addfile(ti, _io.BytesIO(body))
            prov = json.dumps(
                {"format": 2, "custody": "not-a-list",
                 "meta": "not-a-dict", "source": 7}).encode()
            ti2 = tarfile.TarInfo("meta/provenance.json")
            ti2.size = len(prov)
            tar.addfile(ti2, _io.BytesIO(prov))
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        n = import_workspace(arc)  # must not raise
        assert n == 1
        assert (dest / "keep.txt").exists()

    # --- terminal injection via archive-authored provenance ------------

    def test_provenance_strings_sanitized_before_print(
            self, tmp_path, monkeypatch, capsys):
        import io as _io
        arc = tmp_path / "inject.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            prov = json.dumps({
                "format": 2,
                "exporter": "evil\r\n  workspace shape digest: OK SPOOF",
                "created_at": "x", "source": {}, "meta": {},
                "custody": [],
            }).encode()
            ti = tarfile.TarInfo("meta/provenance.json")
            ti.size = len(prov)
            tar.addfile(ti, _io.BytesIO(prov))
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc)
        err = capsys.readouterr().err
        # The exporter line must not contain a raw newline/CR that could
        # forge a second provenance line.
        exporter_lines = [ln for ln in err.splitlines()
                          if "exported by" in ln]
        assert exporter_lines and "\r" not in err.split("exported by")[1][:80]
        assert "evil?" in err  # control chars replaced with '?'

    # --- --apply-meta gating + symlink-dest refusal --------------------

    def test_apply_meta_not_applied_when_archive_lacks_config(
            self, workspace, tmp_path, monkeypatch, capsys):
        # Archive A has a config; archive B does not. Importing B with
        # --apply-meta into a workspace that earlier staged A must NOT
        # apply A's stale config.
        user = self._user_dir(monkeypatch, tmp_path)
        (user / "config.yml").write_text("model: from-A\n")
        arc_a = tmp_path / "a.tar.gz"
        export_workspace(output_path=arc_a)

        # Build B: workspace-only, no meta/user-config.
        arc_b = tmp_path / "b.tar.gz"
        with tarfile.open(arc_b, "w:gz") as tar:
            tar.add(str(workspace / "playbook.md"),
                    arcname="workspace/playbook.md")

        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc_a)               # stages A's config
        (user / "config.yml").write_text("model: local\n")
        import_workspace(arc_b, apply_meta=True)  # B has no config
        assert (user / "config.yml").read_text() == "model: local\n"

    def test_apply_meta_refuses_symlinked_destination(
            self, workspace, tmp_path, monkeypatch, capsys):
        user = self._user_dir(monkeypatch, tmp_path)
        (user / "config.yml").write_text("model: opus\n")
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)

        # Importing machine: config.yml is a symlink to an external file.
        external = tmp_path / "external-target.yml"
        external.write_text("model: victim\n")
        (user / "config.yml").unlink()
        (user / "config.yml").symlink_to(external)
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(archive, apply_meta=True)
        assert "refusing" in capsys.readouterr().err
        assert external.read_text() == "model: victim\n"  # not clobbered

    # --- custody survives a hop (export→import→re-export) ---------------

    def test_custody_chain_survives_reexport(
            self, workspace, tmp_path, monkeypatch):
        self._user_dir(monkeypatch, tmp_path)
        arc1 = tmp_path / "a.tar.gz"
        export_workspace(output_path=arc1)
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc1)
        # Re-export the imported workspace: lineage must carry forward.
        arc2 = tmp_path / "b.tar.gz"
        export_workspace(output_path=arc2)
        with tarfile.open(arc2, "r:gz") as tar:
            prov = json.loads(
                tar.extractfile("meta/provenance.json").read().decode())
        events = [e["event"] for e in prov["custody"]]
        assert events == ["export", "import", "export"]

    # --- meta-side secret screening on import --------------------------

    def test_secret_shaped_meta_screened_on_import(
            self, tmp_path, monkeypatch, capsys):
        import io as _io
        arc = tmp_path / "sneaky.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            prov = json.dumps({"format": 2, "meta": {}, "custody": []}).encode()
            ti = tarfile.TarInfo("meta/provenance.json")
            ti.size = len(prov)
            tar.addfile(ti, _io.BytesIO(prov))
            body = b"sk-leaked"
            ti2 = tarfile.TarInfo("meta/experiments/secrets/key.txt")
            ti2.size = len(body)
            tar.addfile(ti2, _io.BytesIO(body))
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        import_workspace(arc)
        staged = self._staged(dest)
        assert not (staged / "experiments" / "secrets" / "key.txt").exists()


class TestSymlinkPortability:
    """External symlinks travel as data (meta/symlinks.json), not links —
    a link to /usr/bin/python3 resolves to the WRONG binary on another
    OS (Jeremy 2026-08-13: portable, or at least non-destructive)."""

    def test_external_absolute_symlink_recorded_not_shipped(
            self, workspace, tmp_path, monkeypatch):
        self_dir = tmp_path / "maro-user"
        self_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(self_dir))
        scratch = workspace / "runs" / "r1" / "scratch"
        scratch.mkdir(parents=True)
        (scratch / "pybin").symlink_to("/usr/bin/python3")
        (scratch / "loglink").symlink_to("/tmp/nonexistent-target.out")

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            links = [m.name for m in tar.getmembers() if m.issym()]
            rec = json.loads(
                tar.extractfile("meta/symlinks.json").read().decode())
        assert links == []
        recorded = {l["path"]: l["target"] for l in rec["links"]}
        assert recorded["runs/r1/scratch/pybin"] == "/usr/bin/python3"
        assert recorded["runs/r1/scratch/loglink"] == \
            "/tmp/nonexistent-target.out"

    def test_internal_relative_symlink_still_ships_as_link(
            self, workspace, tmp_path, monkeypatch):
        self_dir = tmp_path / "maro-user"
        self_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(self_dir))
        venv = workspace / "runs" / "r1" / "venv"
        venv.mkdir(parents=True)
        (venv / "lib").mkdir()
        (venv / "lib64").symlink_to("lib")

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            syms = {m.name: m.linkname
                    for m in tar.getmembers() if m.issym()}
        assert syms == {"workspace/runs/r1/venv/lib64": "lib"}

    def test_escaping_relative_symlink_recorded_not_shipped(
            self, workspace, tmp_path, monkeypatch):
        self_dir = tmp_path / "maro-user"
        self_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(self_dir))
        (workspace / "sneaky").symlink_to("../outside-the-tree")

        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        with tarfile.open(archive, "r:gz") as tar:
            links = [m.name for m in tar.getmembers() if m.issym()]
            rec = json.loads(
                tar.extractfile("meta/symlinks.json").read().decode())
        assert links == []
        assert rec["links"][0]["path"] == "sneaky"

    def test_cyclic_symlink_does_not_abort_export(
            self, workspace, tmp_path, monkeypatch):
        self_dir = tmp_path / "maro-user"
        self_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(self_dir))
        loop = workspace / "runs" / "r1"
        loop.mkdir(parents=True)
        (loop / "a").symlink_to("b")
        (loop / "b").symlink_to("a")

        archive = tmp_path / "export.tar.gz"
        # Must not raise (cyclic resolve was an unhandled RuntimeError).
        export_workspace(output_path=archive)
        assert archive.exists()



class TestInspect:
    """`inspect` is look-before-you-import (2026-08-13 overnight): show
    provenance + custody, verify the shape digest, preview import skips —
    all read-only, nothing extracted or mutated."""

    def _user_dir(self, monkeypatch, tmp_path):
        d = tmp_path / "maro-user"
        d.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(d))
        return d

    def test_inspect_clean_archive_reports_and_exits_zero(
            self, workspace, tmp_path, monkeypatch, capsys):
        self._user_dir(monkeypatch, tmp_path)
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        rc = inspect_archive(archive)
        out = capsys.readouterr().out
        assert rc == 0
        assert "workspace shape digest: OK" in out
        assert "exported by" in out
        assert "custody:" in out

    def test_inspect_never_extracts(self, workspace, tmp_path, monkeypatch):
        self._user_dir(monkeypatch, tmp_path)
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        dest = tmp_path / "should-stay-empty"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        inspect_archive(archive)
        assert list(dest.iterdir()) == []  # inspect must not write anything

    def test_inspect_flags_digest_mismatch_nonzero(
            self, workspace, tmp_path, monkeypatch, capsys):
        self._user_dir(monkeypatch, tmp_path)
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        tampered = tmp_path / "tampered.tar.gz"
        import io as _io
        with tarfile.open(tampered, "w:gz") as out, \
                tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                out.addfile(m, tar.extractfile(m) if m.isfile() else None)
            ti = tarfile.TarInfo("workspace/injected.txt")
            ti.size = 3
            out.addfile(ti, _io.BytesIO(b"xxx"))
        rc = inspect_archive(tampered)
        assert rc == 2
        assert "MISMATCH" in capsys.readouterr().out

    def test_inspect_previews_unsafe_members(
            self, tmp_path, monkeypatch, capsys):
        import io as _io
        arc = tmp_path / "mixed.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            body = b"ok"
            ti = tarfile.TarInfo("workspace/good.txt")
            ti.size = len(body)
            tar.addfile(ti, _io.BytesIO(body))
            sym = tarfile.TarInfo("workspace/leak")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "/etc/passwd"
            tar.addfile(sym)
            fifo = tarfile.TarInfo("workspace/pipe")
            fifo.type = tarfile.FIFOTYPE
            tar.addfile(fifo)
        rc = inspect_archive(arc)
        out = capsys.readouterr().out
        assert "import would SKIP 2 unsafe member" in out
        assert "external symlink" in out
        assert "special file" in out
        # Review 2026-08-13: unsafe members previously still exited 0 —
        # "clean" as a scripting signal was a lie. rc 4 = unsafe present.
        assert rc == 4

    def test_inspect_future_format_flagged_nonzero(self, tmp_path, capsys):
        import io as _io
        arc = tmp_path / "future.tar.gz"
        with tarfile.open(arc, "w:gz") as tar:
            prov = json.dumps({
                "format": 99, "custody": [],
                "contents": {"workspace_shape_sha256": ""},
            }).encode()
            ti = tarfile.TarInfo("meta/provenance.json")
            ti.size = len(prov)
            tar.addfile(ti, _io.BytesIO(prov))
        rc = inspect_archive(arc)
        out = capsys.readouterr().out
        assert rc == 3
        assert "NEWER than this tool" in out


class TestInspectReviewHardening:
    """2026-08-13 cross-model review of 977f58f (REJECT — verdict in
    docs/history/2026-08-13-maro-export-inspect-adversarial-review.md):
    the exit-code gate failed open, inspect re-implemented import policy
    and the two disagreed, caps applied after the full scan, and
    archive-authored strings reached the terminal raw."""

    def _arc(self, tmp_path, name="probe.tar.gz"):
        return tmp_path / name

    def _add(self, tar, name, body=b"", type=None, linkname=""):
        import io as _io
        ti = tarfile.TarInfo(name)
        if type is not None:
            ti.type = type
        if linkname:
            ti.linkname = linkname
        ti.size = len(body)
        tar.addfile(ti, _io.BytesIO(body) if body else None)

    def test_non_int_format_is_malformed_not_v1(self, tmp_path, capsys):
        # "99" (str) and 3.0 (float) previously ducked the newer-format
        # gate AND read as clean v1 — fail closed instead.
        for bad_fmt in ("99", 3.0, True):
            arc = self._arc(tmp_path, f"fmt-{bad_fmt}.tar.gz")
            with tarfile.open(arc, "w:gz") as tar:
                prov = json.dumps({"format": bad_fmt, "custody": [],
                                   "contents": {}}).encode()
                self._add(tar, "meta/provenance.json", prov)
            rc = inspect_archive(arc)
            out = capsys.readouterr().out
            assert rc == 5, f"format={bad_fmt!r} must flag malformed"
            assert "MALFORMED" in out
            assert "v1 (no meta/provenance)" not in out

    def test_oversized_first_provenance_flags_and_no_duplicate_rescue(
            self, tmp_path, capsys):
        # Import stops at the FIRST provenance member; inspect previously
        # skipped an oversized first and accepted a later duplicate — the
        # two lanes disagreed about the same archive.
        from maro_export import _MAX_PROVENANCE_BYTES
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            self._add(tar, "meta/provenance.json",
                      b"0" * (_MAX_PROVENANCE_BYTES + 1))
            good = json.dumps({"format": 2, "custody": [],
                               "exporter": "later-dupe", "contents": {},
                               "source": {}, "meta": {}}).encode()
            self._add(tar, "meta/provenance.json", good)
        rc = inspect_archive(arc)
        out = capsys.readouterr().out
        assert rc == 5
        assert "oversized" in out
        assert "later-dupe" not in out  # duplicate must not rescue it

    def test_import_refuses_malformed_provenance(self, tmp_path, monkeypatch):
        # A provenance file that EXISTS but cannot be read is a crafted or
        # corrupt archive — refuse before mutating anything.
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            self._add(tar, "workspace/keep.txt", b"x")
            self._add(tar, "meta/provenance.json", b"{not json")
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        with pytest.raises(SystemExit):
            import_workspace(arc)
        assert not (dest / "keep.txt").exists()

    def test_meta_screens_are_previewed(self, tmp_path, capsys):
        # inspect previously counted only secret-shaped regular files in
        # meta — traversal, links, and specials staged nothing at import
        # but previewed as zero findings.
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            self._add(tar, "meta/../escaped.txt", b"x")
            self._add(tar, "meta/link", type=tarfile.SYMTYPE,
                      linkname="/etc/passwd")
            self._add(tar, "meta/pipe", type=tarfile.FIFOTYPE)
            self._add(tar, "meta/secrets/api.key", b"k")
        rc = inspect_archive(arc)
        out = capsys.readouterr().out
        assert rc == 4
        assert "meta traversal" in out
        assert "meta non-regular member" in out
        assert "secret-shaped meta" in out

    def test_policy_excluded_files_are_reported(self, tmp_path, capsys):
        # workspace/.env is silently skipped by import; the preview must
        # say so instead of omitting it from every count.
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            self._add(tar, "workspace/.env", b"SECRET=1")
            self._add(tar, "workspace/ok.txt", b"x")
        inspect_archive(arc)
        out = capsys.readouterr().out
        assert "exclude 1 policy-excluded file(s)" in out

    def test_terminal_injection_via_linkname_and_counters(self, tmp_path, capsys):
        # Reviewer reproduced ANSI clearing + forged lines through the
        # symlink-target reason and raw meta counters.
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            self._add(tar, "workspace/evil", type=tarfile.SYMTYPE,
                      linkname="/tmp/\x1b[2J\nforged: line")
            prov = json.dumps({
                "format": 2, "custody": [], "contents": {}, "source": {},
                "meta": {"user_config_redactions": "\x1b[31mRED\x1b[0m",
                         "experiments_files": "12\nforged", }
            }).encode()
            self._add(tar, "meta/provenance.json", prov)
        inspect_archive(arc)
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "\nforged" not in out

    def test_member_cap_enforced_during_scan(self, tmp_path, monkeypatch, capsys):
        # The cap must reject AS MEMBERS STREAM — previously the whole
        # member list was materialized first, so the cap cost more than it
        # protected.
        import maro_export as mx
        monkeypatch.setattr(mx, "_MAX_MEMBERS", 3)
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            for i in range(6):
                self._add(tar, f"workspace/f{i}.txt", b"x")
        rc = inspect_archive(arc)
        assert rc == 1
        assert "member cap" in capsys.readouterr().err
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        with pytest.raises(SystemExit):
            import_workspace(arc)
        assert list(dest.iterdir()) == []

    def test_inspect_prints_archive_sha256(self, workspace, tmp_path,
                                           monkeypatch, capsys):
        d = tmp_path / "maro-user"
        d.mkdir(exist_ok=True)
        monkeypatch.setenv("MARO_USER_DIR", str(d))
        archive = tmp_path / "export.tar.gz"
        export_workspace(output_path=archive)
        inspect_archive(archive)
        out = capsys.readouterr().out
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        assert f"sha256: {digest}" in out

    def test_import_expect_sha256_binds_the_bytes(self, tmp_path, monkeypatch):
        arc = self._arc(tmp_path)
        with tarfile.open(arc, "w:gz") as tar:
            self._add(tar, "workspace/keep.txt", b"x")
        dest = tmp_path / "ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        digest = hashlib.sha256(arc.read_bytes()).hexdigest()
        # Wrong digest → refuse, nothing changed.
        with pytest.raises(SystemExit):
            import_workspace(arc, expect_sha256="0" * 64)
        assert not (dest / "keep.txt").exists()
        # Too-short prefix → refuse (accidental 8-char paste is not a bind).
        with pytest.raises(SystemExit):
            import_workspace(arc, expect_sha256=digest[:8])
        # Matching prefix (>=16 chars, as inspect prints) → proceeds.
        n = import_workspace(arc, expect_sha256=digest[:16])
        assert n == 1 and (dest / "keep.txt").exists()
