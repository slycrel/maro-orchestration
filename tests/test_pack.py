"""Tests for pack.py — maro-pack export/seal/import/adopt (PORTABLE_LEARNING_DESIGN.md §7 chunks 3+4)."""

from __future__ import annotations

import io
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
    adopt,
    build_manifest,
    default_denylist,
    export_pack,
    import_pack,
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
# Import / adopt fixtures
# ---------------------------------------------------------------------------

def _rewrite_pack(pack_path: Path, *, manifest_updates: dict = None, review_text: str = None) -> None:
    with tarfile.open(pack_path, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("pack.json").read().decode("utf-8"))
        cur_review = tar.extractfile("REVIEW.md").read().decode("utf-8")
        member_names = [n for n in tar.getnames() if n not in ("pack.json", "REVIEW.md")]
        artifact_bytes = {n: tar.extractfile(n).read() for n in member_names}
    if manifest_updates:
        manifest.update(manifest_updates)
    new_review = review_text if review_text is not None else cur_review
    with tarfile.open(pack_path, "w:gz") as tar:
        pack_module._add_tar_text(tar, "pack.json", json.dumps(manifest, indent=2) + "\n")
        pack_module._add_tar_text(tar, "REVIEW.md", new_review)
        for n, data in artifact_bytes.items():
            info = tarfile.TarInfo(name=n)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))


def _add_artifact(pack_path: Path, *, cls: str, relpath: str, content: str) -> None:
    """Inject an extra artifact row + tar member without touching REVIEW.md —
    lets tests exercise the unknown-class quarantine path on an already-sealed pack."""
    with tarfile.open(pack_path, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("pack.json").read().decode("utf-8"))
        review_text = tar.extractfile("REVIEW.md").read().decode("utf-8")
        member_names = [n for n in tar.getnames() if n not in ("pack.json", "REVIEW.md")]
        artifact_bytes = {n: tar.extractfile(n).read() for n in member_names}
    path = f"artifacts/{relpath}"
    manifest["artifacts"].append({"class": cls, "path": path, "sha256": pack_module._sha256_text(content)})
    artifact_bytes[path] = content.encode("utf-8")
    with tarfile.open(pack_path, "w:gz") as tar:
        pack_module._add_tar_text(tar, "pack.json", json.dumps(manifest, indent=2) + "\n")
        pack_module._add_tar_text(tar, "REVIEW.md", review_text)
        for n, data in artifact_bytes.items():
            info = tarfile.TarInfo(name=n)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))


def _export_and_seal(src_ws: Path, tmp_path: Path, *, name="src-pack", **export_kwargs) -> Path:
    report = export_pack(name=name, label="src", workspace=src_ws, out_dir=tmp_path / "out",
                          denylist=[], **export_kwargs)
    pack_path = Path(report["pack_path"])
    seal_pack(pack_path, confirmed=True)
    return pack_path


@pytest.fixture
def target_ws(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path / "dst")
    monkeypatch.setenv("MARO_MEMORY_DIR", str(ws / "memory"))
    return ws


# ---------------------------------------------------------------------------
# import_pack
# ---------------------------------------------------------------------------

class TestImportPack:
    def test_refuses_unsealed_pack_by_default(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        report = export_pack(name="p", label="src", workspace=src_ws, out_dir=tmp_path / "out", denylist=[])
        with pytest.raises(SystemExit):
            import_pack(Path(report["pack_path"]), label="l", target=target_ws)

    def test_allow_unreviewed_permits_unsealed_import(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "skills" / "s.md").write_text("hi", encoding="utf-8")
        report = export_pack(name="p", label="src", workspace=src_ws, out_dir=tmp_path / "out", denylist=[])
        result = import_pack(Path(report["pack_path"]), label="l", target=target_ws, allow_unreviewed=True)
        assert result["skills_md"][0]["outcome"] == "quarantined"

    def test_refuses_newer_pack_format(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        pack_path = _export_and_seal(src_ws, tmp_path)
        _rewrite_pack(pack_path, manifest_updates={"pack_format": PACK_FORMAT + 1})
        with pytest.raises(SystemExit):
            import_pack(pack_path, label="l", target=target_ws)

    def test_refuses_on_tampered_review_md(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        pack_path = _export_and_seal(src_ws, tmp_path)
        _rewrite_pack(pack_path, review_text="tampered content, hash won't match")
        with pytest.raises(SystemExit):
            import_pack(pack_path, label="l", target=target_ws)

    def test_standing_rule_demoted_to_hypothesis(self, tmp_path, target_ws):
        from knowledge_lens import load_hypotheses, load_standing_rules
        src_ws = _make_workspace(tmp_path / "src")
        _write_jsonl(src_ws / "memory" / "standing_rules.jsonl",
                     [{"rule_id": "r1", "rule": "always check twice", "domain": "ops",
                       "confirmations": 5, "contradictions": 0}])
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="hermes-trial", target=target_ws)
        assert result["rules_demoted_to_hypotheses"][0]["outcome"] == "demoted_to_hypothesis"
        assert load_standing_rules() == []
        hyps = load_hypotheses()
        assert len(hyps) == 1
        h = hyps[0]
        assert h.lesson == "always check twice"
        assert h.confirmations == 0 and h.contradictions == 0
        assert h.source_lesson_ids == ["imported:src-pack/r1"]
        assert h.imported["imported_from"] == "hermes-trial"
        assert h.imported["original_id"] == "r1"
        assert h.imported["pack"].startswith("src-pack@")

    def test_content_identical_rule_skipped(self, tmp_path, target_ws):
        from knowledge_lens import Hypothesis, load_hypotheses, _hypotheses_path
        from file_lock import locked_append
        # target already knows this exact lesson text locally
        locked_append(_hypotheses_path(), json.dumps(Hypothesis(
            hyp_id="local1", lesson="always check twice", domain="ops",
            confirmations=1, contradictions=0, source_lesson_ids=[],
            first_seen="2026-01-01", last_seen="2026-01-01",
        ).to_dict()))
        src_ws = _make_workspace(tmp_path / "src")
        _write_jsonl(src_ws / "memory" / "standing_rules.jsonl",
                     [{"rule_id": "r1", "rule": "always check twice", "domain": "ops"}])
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["rules_demoted_to_hypotheses"][0]["outcome"] == "skipped_identical"
        assert len(load_hypotheses()) == 1  # no duplicate added

    def test_lesson_enters_medium_tier_with_capped_score(self, tmp_path, target_ws):
        from knowledge_web import load_tiered_lessons, MemoryTier
        src_ws = _make_workspace(tmp_path / "src")
        _write_jsonl(src_ws / "memory" / "long" / "lessons.jsonl",
                     [{"lesson_id": "l1", "lesson": "batch writes", "task_type": "ops",
                       "outcome": "success", "source_goal": "g1", "confidence": 0.9,
                       "tier": "long", "score": 1.0, "last_reinforced": "2020-01-01",
                       "sessions_validated": 5}])
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["lessons_imported"][0]["outcome"] == "imported_medium"
        medium = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        assert len(medium) == 1
        tl = medium[0]
        assert tl.tier == MemoryTier.MEDIUM
        assert tl.score <= 0.5
        assert tl.sessions_validated == 0
        assert tl.last_reinforced != "2020-01-01"  # transaction time, not origin's event time
        assert tl.imported["original_trust"] == 1.0
        assert load_tiered_lessons(tier=MemoryTier.LONG, limit=None) == []

    def test_skill_record_stats_moved_to_claimed(self, tmp_path, target_ws):
        from skills import load_skills
        src_ws = _make_workspace(tmp_path / "src")
        _write_jsonl(src_ws / "memory" / "skills.jsonl",
                     [{"id": "s1", "name": "digest", "description": "d", "trigger_patterns": [],
                       "steps_template": [], "source_loop_ids": [], "created_at": "2020-01-01",
                       "use_count": 50, "success_rate": 0.9}])
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["skill_records_imported"][0]["outcome"] == "imported"
        skills = load_skills()
        assert len(skills) == 1
        sk = skills[0]
        assert sk.use_count == 0
        assert sk.success_rate == 1.0
        assert sk.imported["claimed_use_count"] == 50
        assert sk.imported["claimed_success_rate"] == 0.9

    def test_skill_md_quarantined_not_live(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "skills" / "foo.md").write_text("---\nname: foo\n---\nbody", encoding="utf-8")
        pack_path = _export_and_seal(src_ws, tmp_path)
        import_pack(pack_path, label="l", target=target_ws)
        assert not (target_ws / "skills" / "foo.md").exists()
        assert (target_ws / "imports" / "l" / "skills" / "foo.md").exists()

    def test_persona_md_quarantined_not_live(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "personas" / "researcher.md").write_text("---\nname: researcher\n---\nbody", encoding="utf-8")
        pack_path = _export_and_seal(src_ws, tmp_path)
        import_pack(pack_path, label="l", target=target_ws)
        assert not (target_ws / "personas" / "researcher.md").exists()
        assert (target_ws / "imports" / "l" / "personas" / "researcher.md").exists()

    def test_collision_same_name_different_content_quarantines_with_conflicts_note(self, tmp_path, target_ws):
        (target_ws / "skills" / "foo.md").write_text("local version", encoding="utf-8")
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "skills" / "foo.md").write_text("imported version", encoding="utf-8")
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["skills_md"][0]["outcome"] == "conflict_quarantined"
        assert (target_ws / "skills" / "foo.md").read_text(encoding="utf-8") == "local version"
        assert (target_ws / "imports" / "l" / "skills" / "foo.md").read_text(encoding="utf-8") == "imported version"
        conflicts = (target_ws / "imports" / "l" / "CONFLICTS.md").read_text(encoding="utf-8")
        assert "foo.md" in conflicts

    def test_collision_same_name_identical_content_skipped(self, tmp_path, target_ws):
        (target_ws / "skills" / "foo.md").write_text("same everywhere", encoding="utf-8")
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "skills" / "foo.md").write_text("same everywhere", encoding="utf-8")
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["skills_md"][0]["outcome"] == "skipped_identical"
        assert not (target_ws / "imports" / "l" / "CONFLICTS.md").exists()

    def test_unknown_class_quarantined_never_fails_import(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        pack_path = _export_and_seal(src_ws, tmp_path)
        _add_artifact(pack_path, cls="future_class_v5", relpath="memory/weird.jsonl", content='{"x":1}\n')
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["quarantined_unknown"][0]["class"] == "future_class_v5"
        assert (target_ws / "imports" / "l" / "unknown" / "memory" / "weird.jsonl").exists()

    def test_known_quarantine_only_class_preserves_relpath(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "playbook.md").write_text("wisdom", encoding="utf-8")
        pack_path = _export_and_seal(src_ws, tmp_path, include_playbook=True)
        result = import_pack(pack_path, label="l", target=target_ws)
        assert result["quarantined"][0] == {"class": "playbook", "path": "playbook.md", "outcome": "quarantined"}
        assert (target_ws / "imports" / "l" / "playbook.md").read_text(encoding="utf-8") == "wisdom"

    def test_dry_run_writes_nothing(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        _write_jsonl(src_ws / "memory" / "standing_rules.jsonl", [{"rule_id": "r1", "rule": "x"}])
        (src_ws / "skills" / "foo.md").write_text("body", encoding="utf-8")
        pack_path = _export_and_seal(src_ws, tmp_path)
        result = import_pack(pack_path, label="l", target=target_ws, dry_run=True)
        assert result["rules_demoted_to_hypotheses"][0]["outcome"] == "demoted_to_hypothesis"
        assert result["skills_md"][0]["outcome"] == "quarantined"
        assert not (target_ws / "imports").exists()
        assert not (target_ws / "memory" / "hypotheses.jsonl").exists()
        assert not (target_ws / "memory" / "imports.jsonl").exists()

    def test_import_appends_audit_row_to_imports_jsonl(self, tmp_path, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        pack_path = _export_and_seal(src_ws, tmp_path)
        import_pack(pack_path, label="l", target=target_ws)
        rows = (target_ws / "memory" / "imports.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(rows) == 1
        row = json.loads(rows[0])
        assert row["action"] == "pack_import"
        assert row["label"] == "l"


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------

class TestAdopt:
    def _quarantine(self, ws, label, kind, name, content="body"):
        d = ws / "imports" / label / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content, encoding="utf-8")

    def test_adopt_named_skill_copies_with_provenance_header(self, target_ws):
        self._quarantine(target_ws, "hermes-trial", "skills", "foo.md", "---\nname: foo\n---\nbody")
        report = adopt("hermes-trial", items=["foo.md"], target=target_ws)
        assert report["adopted"] == [{"kind": "skills", "name": "foo.md"}]
        adopted = (target_ws / "skills" / "foo.md").read_text(encoding="utf-8")
        assert "imported_from: hermes-trial" in adopted
        assert "adopted_at:" in adopted
        assert "body" in adopted

    def test_adopt_by_stem_without_extension(self, target_ws):
        self._quarantine(target_ws, "l", "skills", "foo.md")
        report = adopt("l", items=["foo"], target=target_ws)
        assert report["adopted"] == [{"kind": "skills", "name": "foo.md"}]

    def test_adopt_all_flag(self, target_ws):
        self._quarantine(target_ws, "l", "skills", "a.md")
        self._quarantine(target_ws, "l", "personas", "b.md")
        report = adopt("l", all_items=True, target=target_ws)
        assert {"kind": "skills", "name": "a.md"} in report["adopted"]
        assert {"kind": "personas", "name": "b.md"} in report["adopted"]
        assert (target_ws / "skills" / "a.md").exists()
        assert (target_ws / "personas" / "b.md").exists()

    def test_adopt_never_overwrites_existing_live_file(self, target_ws):
        (target_ws / "skills" / "foo.md").write_text("local content", encoding="utf-8")
        self._quarantine(target_ws, "l", "skills", "foo.md", "imported content")
        report = adopt("l", items=["foo.md"], target=target_ws)
        assert report["adopted"] == []
        assert report["skipped"][0]["reason"] == "already exists locally"
        assert (target_ws / "skills" / "foo.md").read_text(encoding="utf-8") == "local content"

    def test_adopt_missing_label_raises(self, target_ws):
        with pytest.raises(SystemExit):
            adopt("no-such-label", all_items=True, target=target_ws)

    def test_adopt_no_items_and_no_all_raises(self, target_ws):
        self._quarantine(target_ws, "l", "skills", "foo.md")
        with pytest.raises(SystemExit):
            adopt("l", target=target_ws)

    def test_adopt_unknown_item_name_raises(self, target_ws):
        self._quarantine(target_ws, "l", "skills", "foo.md")
        with pytest.raises(SystemExit):
            adopt("l", items=["does-not-exist.md"], target=target_ws)

    def test_adopt_records_audit_row(self, target_ws):
        self._quarantine(target_ws, "l", "skills", "foo.md")
        adopt("l", items=["foo.md"], target=target_ws)
        rows = (target_ws / "memory" / "imports.jsonl").read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[0])
        assert row["action"] == "adopt"
        assert row["label"] == "l"

    def test_adopt_dry_run_writes_nothing(self, target_ws):
        self._quarantine(target_ws, "l", "skills", "foo.md")
        report = adopt("l", items=["foo.md"], target=target_ws, dry_run=True)
        assert report["adopted"] == [{"kind": "skills", "name": "foo.md"}]
        assert not (target_ws / "skills" / "foo.md").exists()
        assert not (target_ws / "memory" / "imports.jsonl").exists()


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

    def test_export_seal_import_adopt_round_trip_via_main(self, tmp_path, monkeypatch, capsys, target_ws):
        src_ws = _make_workspace(tmp_path / "src")
        (src_ws / "skills" / "s.md").write_text("hello", encoding="utf-8")
        monkeypatch.setattr(pack_module, "default_denylist", lambda: [])
        rc = pack_module.main([
            "export", "cli-pack5", "--label", "test", "--seal", "--yes",
            "--workspace", str(src_ws), "--out-dir", str(tmp_path / "out"),
        ])
        assert rc == 0
        pack_path = tmp_path / "out" / "cli-pack5.maropack.tar.gz"

        capsys.readouterr()
        rc = pack_module.main([
            "import", str(pack_path), "--label", "cli-trial", "--target", str(target_ws),
        ])
        assert rc == 0
        import_report = json.loads(capsys.readouterr().out)
        assert import_report["skills_md"][0]["outcome"] == "quarantined"
        assert (target_ws / "imports" / "cli-trial" / "skills" / "s.md").exists()
        assert not (target_ws / "skills" / "s.md").exists()

        rc = pack_module.main(["adopt", "cli-trial", "--all", "--target", str(target_ws)])
        assert rc == 0
        adopt_report = json.loads(capsys.readouterr().out)
        assert adopt_report["adopted"] == [{"kind": "skills", "name": "s.md"}]
        assert (target_ws / "skills" / "s.md").exists()
        assert "imported_from: cli-trial" in (target_ws / "skills" / "s.md").read_text(encoding="utf-8")
