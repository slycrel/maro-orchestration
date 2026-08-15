"""§14a slice 2 — portability weighting at the recall injection fork.

Pins the consumption contract (decision e2b83703): thresholds only at
ranking time, home/unattributable/evidence-poor candidates untouched,
no-cache is a strict identity, contradiction bleeds weight through the
Beta posterior. Cache refresh reuses the census (single source of truth
with camera_readout --portability).
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from portability import (  # noqa: E402
    MIN_FOREIGN_EVIDENCE, apply_portability, beta_mean, cache_path,
    is_home, load_cache, refresh_cache)


def _lesson(lid, source_goal="some origin goal"):
    return SimpleNamespace(lesson_id=lid, source_goal=source_goal,
                           lesson=f"text of {lid}")


def _ws(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_cache(ws, lessons):
    (ws / "memory" / "portability_cache.json").write_text(
        json.dumps({"computed_at": "2026-08-15T00:00:00+00:00",
                    "runs_scanned": 1, "lessons": lessons}),
        encoding="utf-8")


class TestIsHome:
    def test_exact_match_under_mint_cap(self):
        goal = "x" * 200
        assert is_home(goal[:120], goal, "unrelated-project")

    def test_different_goal_different_project_is_foreign(self):
        assert not is_home("origin goal", "other goal", "")


class TestApplyPortability:
    def test_no_cache_is_strict_identity(self, tmp_path, monkeypatch):
        _ws(tmp_path, monkeypatch)
        scored = [(_lesson("a"), 1.0), (_lesson("b"), 0.5)]
        out, adj = apply_portability(scored, "goal", "proj")
        assert out is scored  # same object — cheap no-op detection
        assert adj == []

    def test_foreign_success_evidence_boosts_rank(
            self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        _write_cache(ws, {"b": {"foreign_s": 5, "foreign_f": 0}})
        scored = [(_lesson("a"), 1.0), (_lesson("b"), 0.9)]
        out, adj = apply_portability(scored, "current goal", "proj")
        assert [getattr(t, "lesson_id", "") for t, _ in out] == ["b", "a"]
        (a,) = adj
        assert a["lesson_id"] == "b" and a["raw"] == 0.9
        assert abs(a["weight"] - 2 * beta_mean(5, 0)) < 1e-3
        assert abs(out[0][1] - 0.9 * 2 * beta_mean(5, 0)) < 1e-9

    def test_foreign_failure_evidence_demotes(self, tmp_path, monkeypatch):
        """Contradiction bleeds weight: a lesson whose foreign citations
        fail drops below a lower-raw-scored neutral sibling."""
        ws = _ws(tmp_path, monkeypatch)
        _write_cache(ws, {"a": {"foreign_s": 0, "foreign_f": 4}})
        scored = [(_lesson("a"), 1.0), (_lesson("b"), 0.5)]
        out, _ = apply_portability(scored, "current goal", "proj")
        assert [getattr(t, "lesson_id", "") for t, _ in out] == ["b", "a"]

    def test_home_lesson_never_weighted(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        _write_cache(ws, {"a": {"foreign_s": 0, "foreign_f": 9}})
        scored = [(_lesson("a", source_goal="the goal"), 1.0)]
        out, adj = apply_portability(scored, "the goal", "proj")
        assert out is scored and adj == []

    def test_below_min_evidence_untouched(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        _write_cache(ws, {"a": {"foreign_s": MIN_FOREIGN_EVIDENCE - 1,
                                "foreign_f": 0}})
        scored = [(_lesson("a"), 1.0)]
        out, adj = apply_portability(scored, "current goal", "proj")
        assert out is scored and adj == []

    def test_sentinel_source_untouched(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        _write_cache(ws, {"a": {"foreign_s": 9, "foreign_f": 0}})
        scored = [(_lesson("a", source_goal="evolver-abc"), 1.0)]
        out, adj = apply_portability(scored, "current goal", "proj")
        assert out is scored and adj == []

    def test_killswitch_off_is_identity(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        _write_cache(ws, {"a": {"foreign_s": 9, "foreign_f": 0}})
        monkeypatch.setattr("portability.weighting_enabled", lambda: False)
        scored = [(_lesson("a"), 1.0)]
        out, adj = apply_portability(scored, "current goal", "proj")
        assert out is scored and adj == []


class TestCacheRefresh:
    def test_refresh_counts_verdicted_foreign_only(
            self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        # A lesson cited by a verdicted foreign run, and one cited by an
        # unjudged run (must NOT enter the cache).
        (ws / "memory" / "medium").mkdir(parents=True, exist_ok=True)
        (ws / "memory" / "long").mkdir(parents=True, exist_ok=True)
        for lid, sg in (("lesV", "origin goal"), ("lesU", "another origin")):
            row = {"lesson_id": lid, "task_type": "agenda",
                   "outcome": "success", "lesson": "t", "source_goal": sg,
                   "confidence": 0.8, "tier": "medium", "score": 1.0,
                   "last_reinforced": "2026-08-01"}
            with (ws / "memory" / "medium" / "lessons.jsonl").open(
                    "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        for name, lid, achieved in (("aaa-r1", "lesV", True),
                                    ("bbb-r2", "lesU", None)):
            rd = ws / "runs" / name
            (rd / "source").mkdir(parents=True, exist_ok=True)
            frame = {"fork": "lesson-selection",
                     "chosen": {"lesson_ids": [lid]}}
            (rd / "source" / "camera_frames.jsonl").write_text(
                json.dumps(frame) + "\n", encoding="utf-8")
            meta = {"project": "p", "prompt": "g", "status": "done"}
            if achieved is not None:
                meta["goal_achieved"] = achieved
            (rd / "metadata.json").write_text(json.dumps(meta),
                                              encoding="utf-8")
        n = refresh_cache()
        assert n == 1
        cache = load_cache()
        assert cache == {"lesV": {"foreign_s": 1, "foreign_f": 0}}
        assert cache_path().exists()

    def test_load_cache_corrupt_is_empty(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path, monkeypatch)
        (ws / "memory" / "portability_cache.json").write_text(
            "{not json", encoding="utf-8")
        assert load_cache() == {}
