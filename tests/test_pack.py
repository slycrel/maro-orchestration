"""Tests for pack.py — maro-pack export/seal (PORTABLE_LEARNING_DESIGN.md §7 chunk 3)."""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pack as pack_module
from pack import (
    ARCHIVE_SUFFIX,
    PACK_FORMAT,
    build_manifest,
    default_denylist,
    export_pack,
    read_pack_manifest,
    seal_pack,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "memory" / "long").mkdir(parents=True)
    (ws / "memory" / "medium").mkdir(parents=True)
    (ws / "skills").mkdir(parents=True)
    (ws / "personas").mkdir(parents=True)
    return ws


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------

class TestBuildManifest:
    def test_shape(self):
        m = build_manifest(name="n", label="l", artifacts=[])
        assert m["pack_format"] == PACK_FORMAT == 1
        assert m["name"] == "n"
        assert m["origin"]["label"] == "l"
        assert m["origin"]["scrubber_version"] == 1
        assert m["review"] == {"human_reviewed": False, "reviewed_at": None, "review_manifest_sha256": None}
        assert m["trust_policy"] == "demote-to-hypothesis"


# ---------------------------------------------------------------------------
# export_pack
# ---------------------------------------------------------------------------

class TestExportPack:
    def test_empty_workspace_produces_pack_with_no_artifacts(self, tmp_path):
        ws = _make_workspace(tmp_path)
        report = export_pack(name="empty-pack", label="test", workspace=ws, out_dir=tmp_path / "out",
                              denylist=[])
        assert Path(report["pack_path"]).exists()
        manifest = read_pack_manifest(Path(report["pack_path"]))
        assert manifest["artifacts"] == []
        assert manifest["review"]["human_reviewed"] is False

    def test_skills_and_personas_included_by_default(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "edge-scan.md").write_text("# Edge Scan\ndo the thing", encoding="utf-8")
        (ws / "personas" / "researcher.md").write_text("# Researcher persona", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        manifest = read_pack_manifest(Path(report["pack_path"]))
        classes = {a["class"] for a in manifest["artifacts"]}
        assert "skill_md" in classes
        assert "persona_md" in classes
        paths = {a["path"] for a in manifest["artifacts"]}
        assert "artifacts/skills/edge-scan.md" in paths
        assert "artifacts/personas/researcher.md" in paths

    def test_standing_rules_hypotheses_and_long_lessons_included_by_default(self, tmp_path):
        ws = _make_workspace(tmp_path)
        _write_jsonl(ws / "memory" / "standing_rules.jsonl", [{"rule_id": "r1", "rule": "always x"}])
        _write_jsonl(ws / "memory" / "hypotheses.jsonl", [{"hyp_id": "h1", "lesson": "maybe y"}])
        _write_jsonl(ws / "memory" / "long" / "lessons.jsonl", [{"lesson_id": "l1", "lesson": "z"}])
        _write_jsonl(ws / "memory" / "medium" / "lessons.jsonl", [{"lesson_id": "m1", "lesson": "medium one"}])
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        manifest = read_pack_manifest(Path(report["pack_path"]))
        classes = {a["class"] for a in manifest["artifacts"]}
        assert {"rules", "hypotheses", "lessons"} <= classes
        assert "lessons_medium" not in classes  # opt-in only

    def test_include_medium_flag(self, tmp_path):
        ws = _make_workspace(tmp_path)
        _write_jsonl(ws / "memory" / "medium" / "lessons.jsonl", [{"lesson_id": "m1", "lesson": "medium one"}])
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out",
                              include_medium=True, denylist=[])
        manifest = read_pack_manifest(Path(report["pack_path"]))
        assert any(a["class"] == "lessons_medium" for a in manifest["artifacts"])

    def test_include_knowledge_flag(self, tmp_path):
        ws = _make_workspace(tmp_path)
        _write_jsonl(ws / "memory" / "knowledge_nodes.jsonl", [{"id": "n1"}])
        _write_jsonl(ws / "memory" / "knowledge_edges.jsonl", [{"id": "e1"}])
        report_off = export_pack(name="p1", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        manifest_off = read_pack_manifest(Path(report_off["pack_path"]))
        assert not any(a["class"].startswith("knowledge_") for a in manifest_off["artifacts"])

        report_on = export_pack(name="p2", label="test", workspace=ws, out_dir=tmp_path / "out",
                                 include_knowledge=True, denylist=[])
        manifest_on = read_pack_manifest(Path(report_on["pack_path"]))
        classes = {a["class"] for a in manifest_on["artifacts"]}
        assert {"knowledge_nodes", "knowledge_edges"} <= classes

    def test_include_playbook_flag(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "playbook.md").write_text("# Playbook\nlessons learned", encoding="utf-8")
        report_off = export_pack(name="p1", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        assert not any(a["class"] == "playbook" for a in read_pack_manifest(Path(report_off["pack_path"]))["artifacts"])

        report_on = export_pack(name="p2", label="test", workspace=ws, out_dir=tmp_path / "out",
                                 include_playbook=True, denylist=[])
        assert any(a["class"] == "playbook" for a in read_pack_manifest(Path(report_on["pack_path"]))["artifacts"])

    def test_include_runs_flag(self, tmp_path):
        ws = _make_workspace(tmp_path)
        run_dir = ws / "runs" / "abc123-run"
        run_dir.mkdir(parents=True)
        (run_dir / "RESULT.md").write_text("done", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out",
                              include_runs=["abc123-run"], denylist=[])
        manifest = read_pack_manifest(Path(report["pack_path"]))
        paths = {a["path"] for a in manifest["artifacts"]}
        assert "artifacts/runs/abc123-run/RESULT.md" in paths

    def test_missing_run_id_raises(self, tmp_path):
        ws = _make_workspace(tmp_path)
        with pytest.raises(SystemExit):
            export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out",
                        include_runs=["does-not-exist"], denylist=[])

    def test_secret_shaped_string_scrubbed(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "leaky.md").write_text("key=sk-ant-abcdefghijklmnopqrstuvwx", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        with tarfile.open(report["pack_path"], "r:gz") as tar:
            content = tar.extractfile("artifacts/skills/leaky.md").read().decode("utf-8")
        assert "sk-ant-" not in content
        assert "[REDACTED]" in content

    def test_identifiers_scrubbed_with_stable_tokens(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("cd /home/jeremy/workspace && ask jeremy", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out",
                              home="/home/jeremy", hostname="mac-mini", denylist=[])
        with tarfile.open(report["pack_path"], "r:gz") as tar:
            content = tar.extractfile("artifacts/skills/s.md").read().decode("utf-8")
        assert "/home/jeremy" not in content
        assert "[HOME]" in content
        assert "[USER]" in content

    def test_denylist_redacts_configured_email(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("contact slycrel@gmail.com", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out",
                              denylist=["slycrel@gmail.com"])
        with tarfile.open(report["pack_path"], "r:gz") as tar:
            content = tar.extractfile("artifacts/skills/s.md").read().decode("utf-8")
        assert "slycrel@gmail.com" not in content
        assert "[REDACTED]" in content

    def test_review_md_written_as_loose_companion(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        review_path = Path(report["review_path"])
        assert review_path.exists()
        assert review_path.name == "p.REVIEW.md"
        assert "hello" in review_path.read_text(encoding="utf-8")

    def test_review_md_embedded_in_archive_matches_companion(self, tmp_path):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        with tarfile.open(report["pack_path"], "r:gz") as tar:
            archived = tar.extractfile("REVIEW.md").read().decode("utf-8")
        assert archived == Path(report["review_path"]).read_text(encoding="utf-8")

    def test_empty_jsonl_artifact_skipped(self, tmp_path):
        ws = _make_workspace(tmp_path)
        # standing_rules.jsonl not created at all — must not appear as an artifact
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        manifest = read_pack_manifest(Path(report["pack_path"]))
        assert manifest["artifacts"] == []

    def test_sha256_matches_content(self, tmp_path):
        ws = _make_workspace(tmp_path)
        _write_jsonl(ws / "memory" / "standing_rules.jsonl", [{"rule_id": "r1"}])
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        manifest = read_pack_manifest(Path(report["pack_path"]))
        entry = next(a for a in manifest["artifacts"] if a["class"] == "rules")
        with tarfile.open(report["pack_path"], "r:gz") as tar:
            content = tar.extractfile(entry["path"]).read().decode("utf-8")
        assert pack_module._sha256_text(content) == entry["sha256"]

    def test_out_dir_defaults_under_workspace_output(self, tmp_path):
        ws = _make_workspace(tmp_path)
        report = export_pack(name="p", label="test", workspace=ws, denylist=[])
        assert Path(report["pack_path"]).parent == ws / "output" / "packs"


# ---------------------------------------------------------------------------
# seal_pack
# ---------------------------------------------------------------------------

class TestSealPack:
    def _export(self, tmp_path, content="hello world"):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text(content, encoding="utf-8")
        report = export_pack(name="p", label="test", workspace=ws, out_dir=tmp_path / "out", denylist=[])
        return Path(report["pack_path"])

    def test_refuses_without_confirmation(self, tmp_path):
        pack_path = self._export(tmp_path)
        with pytest.raises(SystemExit):
            seal_pack(pack_path, confirmed=False)
        manifest = read_pack_manifest(pack_path)
        assert manifest["review"]["human_reviewed"] is False

    def test_seals_with_confirmation(self, tmp_path):
        pack_path = self._export(tmp_path)
        manifest = seal_pack(pack_path, confirmed=True)
        assert manifest["review"]["human_reviewed"] is True
        assert manifest["review"]["reviewed_at"]
        reloaded = read_pack_manifest(pack_path)
        assert reloaded["review"]["human_reviewed"] is True

    def test_review_hash_matches_review_content(self, tmp_path):
        pack_path = self._export(tmp_path)
        manifest = seal_pack(pack_path, confirmed=True)
        with tarfile.open(pack_path, "r:gz") as tar:
            review_text = tar.extractfile("REVIEW.md").read().decode("utf-8")
        assert manifest["review"]["review_manifest_sha256"] == pack_module._sha256_text(review_text)

    def test_artifacts_survive_seal_rewrite(self, tmp_path):
        pack_path = self._export(tmp_path, content="artifact content")
        seal_pack(pack_path, confirmed=True)
        with tarfile.open(pack_path, "r:gz") as tar:
            content = tar.extractfile("artifacts/skills/s.md").read().decode("utf-8")
        assert content == "artifact content"

    def test_missing_pack_raises(self, tmp_path):
        with pytest.raises(SystemExit):
            seal_pack(tmp_path / "does-not-exist.maropack.tar.gz", confirmed=True)

    def test_companion_edit_before_seal_is_what_gets_hashed(self, tmp_path):
        pack_path = self._export(tmp_path)
        companion = pack_module._review_companion_path(pack_path)
        edited = companion.read_text(encoding="utf-8") + "\n<!-- reviewer note -->\n"
        companion.write_text(edited, encoding="utf-8")
        manifest = seal_pack(pack_path, confirmed=True)
        assert manifest["review"]["review_manifest_sha256"] == pack_module._sha256_text(edited)
        with tarfile.open(pack_path, "r:gz") as tar:
            archived = tar.extractfile("REVIEW.md").read().decode("utf-8")
        assert archived == edited


# ---------------------------------------------------------------------------
# default_denylist
# ---------------------------------------------------------------------------

class TestDefaultDenylist:
    def test_returns_list(self, monkeypatch):
        monkeypatch.delenv("EMAIL", raising=False)
        monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
        monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)
        result = default_denylist()
        assert isinstance(result, list)

    def test_picks_up_env_email(self, monkeypatch):
        monkeypatch.setenv("EMAIL", "someone@example.com")
        result = default_denylist()
        assert "someone@example.com" in result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_export_then_seal_via_main(self, tmp_path, monkeypatch, capsys):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        monkeypatch.setattr(pack_module, "default_denylist", lambda: [])
        rc = pack_module.main([
            "export", "cli-pack", "--label", "test",
            "--workspace", str(ws), "--out-dir", str(tmp_path / "out"),
        ])
        assert rc == 0
        pack_path = tmp_path / "out" / "cli-pack.maropack.tar.gz"
        assert pack_path.exists()
        manifest = read_pack_manifest(pack_path)
        assert manifest["review"]["human_reviewed"] is False

        rc = pack_module.main(["seal", str(pack_path), "--yes"])
        assert rc == 0
        manifest = read_pack_manifest(pack_path)
        assert manifest["review"]["human_reviewed"] is True

    def test_export_seal_flag_with_yes(self, tmp_path, monkeypatch):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        monkeypatch.setattr(pack_module, "default_denylist", lambda: [])
        rc = pack_module.main([
            "export", "cli-pack2", "--label", "test",
            "--workspace", str(ws), "--out-dir", str(tmp_path / "out"),
            "--seal", "--yes",
        ])
        assert rc == 0
        manifest = read_pack_manifest(tmp_path / "out" / "cli-pack2.maropack.tar.gz")
        assert manifest["review"]["human_reviewed"] is True

    def test_seal_without_yes_refuses_on_eof(self, tmp_path, monkeypatch):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        monkeypatch.setattr(pack_module, "default_denylist", lambda: [])
        pack_module.main([
            "export", "cli-pack3", "--label", "test",
            "--workspace", str(ws), "--out-dir", str(tmp_path / "out"),
        ])
        pack_path = tmp_path / "out" / "cli-pack3.maropack.tar.gz"
        # input() raises EOFError with no stdin attached under pytest -> refuse
        with pytest.raises(SystemExit):
            pack_module.main(["seal", str(pack_path)])

    def test_inspect_prints_manifest(self, tmp_path, monkeypatch, capsys):
        ws = _make_workspace(tmp_path)
        (ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        monkeypatch.setattr(pack_module, "default_denylist", lambda: [])
        pack_module.main([
            "export", "cli-pack4", "--label", "test",
            "--workspace", str(ws), "--out-dir", str(tmp_path / "out"),
        ])
        pack_path = tmp_path / "out" / "cli-pack4.maropack.tar.gz"
        capsys.readouterr()  # discard the export command's stdout
        rc = pack_module.main(["inspect", str(pack_path)])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["name"] == "cli-pack4"
