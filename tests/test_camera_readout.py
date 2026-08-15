"""Portability census tests (§14a slice 1 — camera_readout --portability).

The census is an INSTRUMENT: pure read over camera frames × run
metadata × lesson stores. These tests pin the claims from the chunk's
fact-ledger contract: home/foreign split, Beta math, unjudged/unresolved
counted-not-hidden, archive fallback, and read-purity.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from camera_readout import (  # noqa: E402
    _beta_mean, _lesson_origins, portability_census)


def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "medium").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "long").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_lesson(ws, lesson_id, *, source_goal, tier="medium",
                  archive=False, lesson="lesson text"):
    row = {
        "lesson_id": lesson_id, "task_type": "agenda", "outcome": "success",
        "lesson": lesson, "source_goal": source_goal, "confidence": 0.8,
        "tier": tier, "score": 1.0, "last_reinforced": "2026-08-01",
    }
    if archive:
        row["archived_at"] = "2026-08-10"
        row["archived_reason"] = "decay_gc"
        path = ws / "memory" / "lessons_archive.jsonl"
    else:
        path = ws / "memory" / tier / "lessons.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _write_cited_run(ws, name, *, lesson_ids, project="proj-a",
                     goal="do the thing", goal_achieved=None,
                     write_meta=True):
    rd = ws / "runs" / name
    (rd / "source").mkdir(parents=True, exist_ok=True)
    frame = {"fork": "lesson-selection", "ts": "2026-08-15T00:00:00+00:00",
             "axes": {}, "candidates": {}, "query_preview": "",
             "chosen": {"lesson_ids": list(lesson_ids)}, "extra": {}}
    (rd / "source" / "camera_frames.jsonl").write_text(
        json.dumps(frame) + "\n", encoding="utf-8")
    if write_meta:
        meta = {"handle_id": name.split("-")[0], "project": project,
                "prompt": goal, "status": "done"}
        if goal_achieved is not None:
            meta["goal_achieved"] = goal_achieved
        (rd / "metadata.json").write_text(json.dumps(meta),
                                          encoding="utf-8")
    return rd


def _per_run(ws):
    out = []
    for rd in sorted((ws / "runs").iterdir()):
        frames = [json.loads(l) for l in
                  (rd / "source" / "camera_frames.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        out.append({"dir": rd, "frames": frames, "card": None})
    return out


class TestBetaMean:
    def test_values(self):
        assert _beta_mean(0, 0) == 0.5
        assert _beta_mean(2, 1) == 0.6
        assert abs(_beta_mean(5, 0) - 6 / 7) < 1e-9


class TestCensus:
    def test_foreign_verdicts_fold_into_portability(
            self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "les1", source_goal="origin goal about X")
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"],
                         project="other-proj", goal="different goal",
                         goal_achieved=True)
        _write_cited_run(ws, "bbb-r2", lesson_ids=["les1"],
                         project="third-proj", goal="another goal",
                         goal_achieved=False)
        c = portability_census(_per_run(ws))
        (row,) = c["rows"]
        assert row["foreign_s"] == 1 and row["foreign_f"] == 1
        assert row["portability"] == _beta_mean(1, 1) == 0.5

    def test_exact_goal_match_is_home(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "les1", source_goal="the very same goal")
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"],
                         project="whatever", goal="the very same goal",
                         goal_achieved=True)
        c = portability_census(_per_run(ws))
        (row,) = c["rows"]
        assert row["home"] == 1 and row["foreign"] == 0
        assert row["portability"] is None  # home evidence never counts

    def test_unjudged_counted_not_folded(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "les1", source_goal="origin goal")
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"],
                         project="p", goal="g")  # no goal_achieved
        c = portability_census(_per_run(ws))
        (row,) = c["rows"]
        assert row["foreign_unjudged"] == 1
        assert row["foreign_s"] == 0 and row["foreign_f"] == 0
        assert row["portability"] is None

    def test_archived_lesson_still_resolves(self, tmp_path, monkeypatch):
        """A lesson cited while live but archived since keeps its
        citations as evidence (live census: 23/79 cited ids were
        archive-only at read time)."""
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "lesA", source_goal="origin", archive=True)
        _write_cited_run(ws, "aaa-r1", lesson_ids=["lesA"],
                         project="p", goal="g", goal_achieved=True)
        c = portability_census(_per_run(ws))
        assert len(c["rows"]) == 1
        assert not c["unresolved"]

    def test_live_store_wins_over_archive_on_dup(
            self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "lesD", source_goal="live origin")
        _write_lesson(ws, "lesD", source_goal="stale archived origin",
                      archive=True)
        origins = _lesson_origins()
        assert origins["lesD"]["source_goal"] == "live origin"

    def test_unresolved_id_counted_not_hidden(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_cited_run(ws, "aaa-r1", lesson_ids=["ghost"],
                         project="p", goal="g", goal_achieved=True)
        c = portability_census(_per_run(ws))
        assert c["rows"] == []
        assert c["unresolved"] == {"ghost": 1}

    def test_unreadable_metadata_counts_and_goes_unjudged(
            self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "les1", source_goal="origin goal")
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"],
                         write_meta=False)
        c = portability_census(_per_run(ws))
        assert c["runs_no_meta"] == 1
        (row,) = c["rows"]
        assert row["foreign_unjudged"] == 1 and row["portability"] is None

    def test_long_goal_home_survives_mint_truncation(
            self, tmp_path, monkeypatch):
        """source_goal is stored truncated to 120 chars at mint
        (knowledge_web record_tiered_lesson); the home compare must
        mirror that cap or the exact-match leg is dead for realistic
        goals — 121/123 verdicted citations in the live corpus carried
        a truncated source_goal (r1 review, architect HIGH)."""
        ws = _workspace(tmp_path, monkeypatch)
        goal = ("investigate the seventeen distinct failure modes of the "
                "widget assembly line and produce a prioritized remediation "
                "plan with cost estimates for each")
        assert len(goal) > 120
        _write_lesson(ws, "les1", source_goal=goal[:120])
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"],
                         project="unrelated-project", goal=goal,
                         goal_achieved=True)
        c = portability_census(_per_run(ws))
        (row,) = c["rows"]
        assert row["home"] == 1 and row["foreign"] == 0

    def test_same_project_different_goal_is_home_via_slug(
            self, tmp_path, monkeypatch):
        """The slug leg: a lesson minted from a different goal in the
        SAME project is home (r1 review, skeptic HIGH — this leg was
        untested)."""
        ws = _workspace(tmp_path, monkeypatch)
        src = "audit the payment gateway for timeout errors"
        from loop_artifacts import resolve_project_slug
        slug = resolve_project_slug(src)
        _write_lesson(ws, "les1", source_goal=src)
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"], project=slug,
                         goal="a totally different follow-up goal",
                         goal_achieved=False)
        c = portability_census(_per_run(ws))
        (row,) = c["rows"]
        assert row["home"] == 1 and row["foreign"] == 0
        assert row["portability"] is None

    def test_sentinel_source_goals_bucket_as_unattributable(
            self, tmp_path, monkeypatch):
        """manual/evolver-/prereq: mints carry sentinel source_goals
        (three live callers); their citations can't be home/foreign
        classified and must be counted, not folded into foreign (r1
        review, architect MEDIUM-HIGH; live corpus today: 0 such
        citations — this pin keeps the bucket visible if that changes)."""
        ws = _workspace(tmp_path, monkeypatch)
        for lid, sg in (("lesM", "manual"), ("lesE", "evolver-abc123"),
                        ("lesP", "prereq:some topic"), ("lesX", "")):
            _write_lesson(ws, lid, source_goal=sg)
        _write_cited_run(ws, "aaa-r1",
                         lesson_ids=["lesM", "lesE", "lesP", "lesX"],
                         project="p", goal="g", goal_achieved=True)
        c = portability_census(_per_run(ws))
        assert len(c["rows"]) == 4
        for row in c["rows"]:
            assert row["unattributable"] == 1
            assert row["foreign"] == 0 and row["home"] == 0
            assert row["portability"] is None

    def test_census_is_pure_read(self, tmp_path, monkeypatch):
        """Instrument charter: the census writes nothing anywhere in the
        workspace tree."""
        ws = _workspace(tmp_path, monkeypatch)
        _write_lesson(ws, "les1", source_goal="origin goal")
        _write_cited_run(ws, "aaa-r1", lesson_ids=["les1"],
                         project="p", goal="g", goal_achieved=True)

        def _tree_hash():
            h = hashlib.sha256()
            for p in sorted(ws.rglob("*")):
                if p.is_file():
                    h.update(str(p).encode())
                    h.update(p.read_bytes())
            return h.hexdigest()

        before = _tree_hash()
        portability_census(_per_run(ws))
        assert _tree_hash() == before
