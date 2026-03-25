"""Tests for Phase 17: router.py — behavior-aligned skill router."""

from __future__ import annotations

import json
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _make_skill(sid="sk001", name="test skill", description="does stuff", triggers=None):
    """Return a minimal Skill-like object."""
    from skills import Skill
    from datetime import datetime, timezone
    return Skill(
        id=sid,
        name=name,
        description=description,
        trigger_patterns=triggers or ["pattern one", "trigger two"],
        steps_template=["step one", "step two"],
        source_loop_ids=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _write_skill_stats(tmp_path: Path, entries: list) -> Path:
    """Write entries to memory/skill-stats.jsonl and return path."""
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    p = mem / "skill-stats.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return p


def _write_skills(tmp_path: Path, entries: list) -> Path:
    """Write entries to memory/skills.jsonl and return path."""
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    p = mem / "skills.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return p


def _make_stats_entries(n: int, success_rate: float = 0.8) -> list:
    """Make n skill-stats entries for distinct skills."""
    entries = []
    for i in range(n):
        entries.append({
            "skill_id": f"sk{i:03d}",
            "skill_name": f"skill {i}",
            "total_uses": 10,
            "successes": int(10 * success_rate),
            "failures": 10 - int(10 * success_rate),
            "success_rate": success_rate,
        })
    return entries


# ---------------------------------------------------------------------------
# RouterStats + RouteResult dataclass tests
# ---------------------------------------------------------------------------

def test_router_stats_fields():
    """RouterStats has all expected fields."""
    from router import RouterStats
    s = RouterStats(
        training_samples=42,
        last_trained="2026-01-01T00:00:00+00:00",
        holdout_accuracy=0.85,
        model_path="/tmp/model.pkl",
        feature_method="tfidf",
        min_samples_reached=True,
    )
    assert s.training_samples == 42
    assert s.last_trained is not None
    assert 0.0 <= s.holdout_accuracy <= 1.0
    assert s.model_path
    assert s.feature_method in ("tfidf", "embeddings")
    assert s.min_samples_reached is True


def test_route_result_fields():
    """RouteResult has skill_id, score, method."""
    from router import RouteResult
    r = RouteResult(skill_id="sk001", skill_name="my skill", score=0.75, method="router")
    assert r.skill_id == "sk001"
    assert r.skill_name == "my skill"
    assert 0.0 <= r.score <= 1.0
    assert r.method == "router"


def test_router_stats_json_roundtrip(monkeypatch, tmp_path):
    """RouterStats survives to_dict / from_dict roundtrip."""
    from router import RouterStats
    s = RouterStats(
        training_samples=100,
        last_trained="2026-03-01T12:00:00+00:00",
        holdout_accuracy=0.92,
        model_path="/mem/router-model.pkl",
        feature_method="tfidf",
        min_samples_reached=True,
    )
    d = s.to_dict()
    s2 = RouterStats.from_dict(d)
    assert s2.training_samples == 100
    assert s2.holdout_accuracy == pytest.approx(0.92)
    assert s2.min_samples_reached is True
    assert s2.feature_method == "tfidf"


# ---------------------------------------------------------------------------
# extract_features
# ---------------------------------------------------------------------------

def test_extract_features_tfidf_fallback(monkeypatch):
    """Returns non-empty list without sentence-transformers."""
    import router
    monkeypatch.setattr(router, "_ST_AVAILABLE", False)
    features = router.extract_features("research polymarket strategies")
    assert isinstance(features, list)
    assert len(features) > 0


def test_extract_features_consistent_length(monkeypatch):
    """Same text always returns same-length vector."""
    import router
    monkeypatch.setattr(router, "_ST_AVAILABLE", False)
    f1 = router.extract_features("hello world test", vectorizer=None)
    f2 = router.extract_features("hello world test", vectorizer=None)
    assert len(f1) == len(f2)


def test_extract_features_no_sklearn_no_st(monkeypatch):
    """Falls back to char-level features when no ML available."""
    import router
    monkeypatch.setattr(router, "_ST_AVAILABLE", False)
    monkeypatch.setattr(router, "_SKLEARN_AVAILABLE", False)
    features = router.extract_features("some text here")
    assert isinstance(features, list)
    assert len(features) > 0


# ---------------------------------------------------------------------------
# build_training_data
# ---------------------------------------------------------------------------

def test_build_training_data_empty(monkeypatch, tmp_path):
    """No skill-stats → ([], [], [])."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    # Point to non-existent file
    missing = tmp_path / "memory" / "skill-stats.jsonl"
    X, y, ids = router.build_training_data(skill_stats_path=missing)
    assert X == []
    assert y == []
    assert ids == []


def test_build_training_data_with_stats(monkeypatch, tmp_path):
    """Stats + skills → parallel lists with correct labels."""
    _setup_workspace(monkeypatch, tmp_path)
    import router

    stats_entries = [
        {"skill_id": "sk001", "skill_name": "research", "total_uses": 10, "successes": 9, "failures": 1, "success_rate": 0.9},
        {"skill_id": "sk002", "skill_name": "deploy", "total_uses": 5, "successes": 1, "failures": 4, "success_rate": 0.2},
        {"skill_id": "sk003", "skill_name": "ambiguous", "total_uses": 8, "successes": 4, "failures": 4, "success_rate": 0.5},
        {"skill_id": "sk004", "skill_name": "zero uses", "total_uses": 0, "successes": 0, "failures": 0, "success_rate": 1.0},
    ]
    skill_entries = [
        {"id": "sk001", "name": "research", "description": "research anything", "trigger_patterns": ["research", "find"]},
        {"id": "sk002", "name": "deploy", "description": "deploy services", "trigger_patterns": ["deploy", "ship"]},
    ]
    sp = _write_skill_stats(tmp_path, stats_entries)
    _write_skills(tmp_path, skill_entries)

    # Override skills path
    with patch.object(router, "_skills_path", return_value=tmp_path / "memory" / "skills.jsonl"):
        X, y, ids = router.build_training_data(skill_stats_path=sp)

    # Only sk001 (positive) and sk002 (negative) should be included
    # sk003 is ambiguous (0.5), sk004 has 0 uses
    assert len(X) == 2
    assert len(y) == 2
    assert len(ids) == 2
    assert "sk001" in ids
    assert "sk002" in ids
    assert y[ids.index("sk001")] == 1.0
    assert y[ids.index("sk002")] == 0.0


# ---------------------------------------------------------------------------
# train_router
# ---------------------------------------------------------------------------

def test_train_router_insufficient_data(monkeypatch, tmp_path):
    """< MIN_TRAINING_SAMPLES → RouterStats(min_samples_reached=False)."""
    _setup_workspace(monkeypatch, tmp_path)
    sklearn = pytest.importorskip("sklearn")
    import router

    sp = _write_skill_stats(tmp_path, _make_stats_entries(5, 0.9))
    with patch.object(router, "_skills_path", return_value=tmp_path / "memory" / "skills.jsonl"):
        stats = router.train_router(
            skill_stats_path=sp,
            model_path=tmp_path / "memory" / "router-model.pkl",
        )
    assert stats.min_samples_reached is False
    assert not (tmp_path / "memory" / "router-model.pkl").exists()


def test_train_router_saves_model(monkeypatch, tmp_path):
    """Enough data → model file written."""
    _setup_workspace(monkeypatch, tmp_path)
    sklearn = pytest.importorskip("sklearn")
    import router

    # 30 positive + 30 negative = 60 total, above threshold
    entries = _make_stats_entries(30, 0.9) + _make_stats_entries(30, 0.1)
    for i, e in enumerate(entries):
        e["skill_id"] = f"sk{i:03d}"
    skill_entries = [
        {"id": e["skill_id"], "name": e["skill_name"], "description": f"desc for {e['skill_name']}", "trigger_patterns": [e["skill_name"]]}
        for e in entries
    ]
    sp = _write_skill_stats(tmp_path, entries)
    _write_skills(tmp_path, skill_entries)
    mp = tmp_path / "memory" / "router-model.pkl"

    with patch.object(router, "_skills_path", return_value=tmp_path / "memory" / "skills.jsonl"):
        stats = router.train_router(skill_stats_path=sp, model_path=mp)

    assert stats.min_samples_reached is True
    assert mp.exists()


def test_train_router_holdout_accuracy(monkeypatch, tmp_path):
    """Holdout accuracy is between 0.0 and 1.0."""
    _setup_workspace(monkeypatch, tmp_path)
    sklearn = pytest.importorskip("sklearn")
    import router

    entries = _make_stats_entries(30, 0.9) + _make_stats_entries(30, 0.1)
    for i, e in enumerate(entries):
        e["skill_id"] = f"sk{i:03d}"
    skill_entries = [
        {"id": e["skill_id"], "name": e["skill_name"], "description": f"desc {e['skill_name']}", "trigger_patterns": [e["skill_name"]]}
        for e in entries
    ]
    sp = _write_skill_stats(tmp_path, entries)
    _write_skills(tmp_path, skill_entries)
    mp = tmp_path / "memory" / "router-model.pkl"

    with patch.object(router, "_skills_path", return_value=tmp_path / "memory" / "skills.jsonl"):
        stats = router.train_router(skill_stats_path=sp, model_path=mp)

    assert 0.0 <= stats.holdout_accuracy <= 1.0


def test_train_router_no_sklearn(monkeypatch, tmp_path):
    """Without sklearn, returns RouterStats(min_samples_reached=False)."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    monkeypatch.setattr(router, "_SKLEARN_AVAILABLE", False)

    entries = _make_stats_entries(60, 0.9)
    sp = _write_skill_stats(tmp_path, entries)
    stats = router.train_router(skill_stats_path=sp, model_path=tmp_path / "memory" / "model.pkl")
    assert stats.min_samples_reached is False


# ---------------------------------------------------------------------------
# load_router
# ---------------------------------------------------------------------------

def test_load_router_no_file(monkeypatch, tmp_path):
    """(None, None) when model file is missing."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    with patch.object(router, "_model_path", return_value=tmp_path / "missing-model.pkl"):
        model, vec = router.load_router()
    assert model is None
    assert vec is None


def test_load_router_never_raises(monkeypatch, tmp_path):
    """Corrupted pickle → (None, None), never raises."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    corrupt = tmp_path / "bad.pkl"
    corrupt.write_bytes(b"not valid pickle data!!")
    with patch.object(router, "_model_path", return_value=corrupt):
        model, vec = router.load_router()
    assert model is None
    assert vec is None


def test_load_router_with_valid_model(monkeypatch, tmp_path):
    """Valid pkl → returns (model, vectorizer)."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    # Use plain dicts as stand-ins (picklable, non-None)
    pkl_path = tmp_path / "router-model.pkl"
    with pkl_path.open("wb") as f:
        pickle.dump({"model": {"type": "fake_model"}, "vectorizer": {"type": "fake_vec"}}, f)
    with patch.object(router, "_model_path", return_value=pkl_path):
        m, v = router.load_router()
    assert m is not None
    assert v is not None


# ---------------------------------------------------------------------------
# route_skills
# ---------------------------------------------------------------------------

def test_route_skills_empty_candidates(monkeypatch, tmp_path):
    """Empty candidates → []."""
    _setup_workspace(monkeypatch, tmp_path)
    from router import route_skills
    result = route_skills("research polymarket", [], top_k=3)
    assert result == []


def test_route_skills_no_model(monkeypatch, tmp_path):
    """No trained model → method='keyword', score=0.5."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    with patch.object(router, "load_router", return_value=(None, None)):
        skills = [_make_skill("sk001", "research skill", "researches stuff")]
        results = router.route_skills("research polymarket", skills, top_k=3)
    assert len(results) == 1
    assert results[0].method == "keyword"
    assert results[0].score == pytest.approx(0.5)


def test_route_skills_with_model(monkeypatch, tmp_path):
    """Mock model → method='router', scores present."""
    _setup_workspace(monkeypatch, tmp_path)
    sklearn = pytest.importorskip("sklearn")
    import router

    mock_model = MagicMock()
    mock_model.classes_ = [0.0, 1.0]
    mock_model.predict_proba.return_value = [[0.2, 0.8]]
    mock_vec = MagicMock()
    mock_vec.transform.return_value = MagicMock()

    with patch.object(router, "load_router", return_value=(mock_model, mock_vec)):
        skills = [_make_skill("sk001", "research skill", "researches stuff")]
        results = router.route_skills("research task", skills, top_k=3)

    assert len(results) == 1
    assert results[0].method == "router"
    assert results[0].score == pytest.approx(0.8)


def test_route_skills_top_k_respected(monkeypatch, tmp_path):
    """Returns at most top_k results."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    with patch.object(router, "load_router", return_value=(None, None)):
        skills = [_make_skill(f"sk{i:03d}", f"skill {i}", f"desc {i}") for i in range(10)]
        results = router.route_skills("goal text", skills, top_k=3)
    assert len(results) <= 3


def test_route_skills_sorted_by_score(monkeypatch, tmp_path):
    """Results are sorted highest score first."""
    _setup_workspace(monkeypatch, tmp_path)
    sklearn = pytest.importorskip("sklearn")
    import router

    scores = [0.3, 0.9, 0.6]
    call_count = [0]

    def mock_proba(vec):
        s = scores[call_count[0] % len(scores)]
        call_count[0] += 1
        return [[1 - s, s]]

    mock_model = MagicMock()
    mock_model.classes_ = [0.0, 1.0]
    mock_model.predict_proba.side_effect = mock_proba
    mock_vec = MagicMock()
    mock_vec.transform.return_value = MagicMock()

    skills = [_make_skill(f"sk{i:03d}", f"skill {i}", f"desc {i}") for i in range(3)]
    with patch.object(router, "load_router", return_value=(mock_model, mock_vec)):
        results = router.route_skills("any goal", skills, top_k=3)

    assert len(results) == 3
    for i in range(len(results) - 1):
        assert results[i].score >= results[i + 1].score


# ---------------------------------------------------------------------------
# maybe_retrain
# ---------------------------------------------------------------------------

def test_maybe_retrain_below_threshold(monkeypatch, tmp_path):
    """No retrain when not enough new data."""
    _setup_workspace(monkeypatch, tmp_path)
    import router

    # Simulate last trained with 40 samples, current count is 45 (<50 more)
    existing_stats = router.RouterStats(
        training_samples=40, last_trained="2026-01-01T00:00:00+00:00",
        holdout_accuracy=0.8, model_path="x", feature_method="tfidf",
        min_samples_reached=True,
    )
    sp = _write_skill_stats(tmp_path, _make_stats_entries(45))

    with patch.object(router, "get_router_stats", return_value=existing_stats):
        with patch.object(router, "_count_skill_stats", return_value=45):
            result = router.maybe_retrain(force=False)
    assert result is None


def test_maybe_retrain_force(monkeypatch, tmp_path):
    """force=True → always retrains."""
    _setup_workspace(monkeypatch, tmp_path)
    import router

    retrained = [False]

    def fake_train(**kwargs):
        retrained[0] = True
        return router.RouterStats(
            training_samples=10, last_trained="now", holdout_accuracy=0.5,
            model_path="x", feature_method="tfidf", min_samples_reached=False,
        )

    with patch.object(router, "train_router", side_effect=fake_train):
        result = router.maybe_retrain(force=True)

    assert retrained[0] is True
    assert result is not None


def test_maybe_retrain_triggers_at_threshold(monkeypatch, tmp_path):
    """Retrains when new data delta >= RETRAIN_EVERY_N."""
    _setup_workspace(monkeypatch, tmp_path)
    import router

    existing_stats = router.RouterStats(
        training_samples=10, last_trained="old", holdout_accuracy=0.0,
        model_path="x", feature_method="tfidf", min_samples_reached=False,
    )
    retrained = [False]

    def fake_train(**kwargs):
        retrained[0] = True
        return router.RouterStats(
            training_samples=65, last_trained="now", holdout_accuracy=0.7,
            model_path="x", feature_method="tfidf", min_samples_reached=True,
        )

    with patch.object(router, "get_router_stats", return_value=existing_stats):
        with patch.object(router, "_count_skill_stats", return_value=65):
            with patch.object(router, "train_router", side_effect=fake_train):
                result = router.maybe_retrain(force=False)

    assert retrained[0] is True
    assert result is not None


# ---------------------------------------------------------------------------
# get_router_stats
# ---------------------------------------------------------------------------

def test_get_router_stats_no_file(monkeypatch, tmp_path):
    """Returns default RouterStats when file doesn't exist."""
    _setup_workspace(monkeypatch, tmp_path)
    import router
    missing = tmp_path / "no-stats.json"
    with patch.object(router, "_stats_path", return_value=missing):
        stats = router.get_router_stats()
    assert stats.training_samples == 0
    assert stats.last_trained is None
    assert stats.min_samples_reached is False


def test_get_router_stats_from_file(monkeypatch, tmp_path):
    """Loads stats from JSON file when it exists."""
    _setup_workspace(monkeypatch, tmp_path)
    import router

    data = {
        "training_samples": 75,
        "last_trained": "2026-03-01T00:00:00+00:00",
        "holdout_accuracy": 0.88,
        "model_path": "/tmp/model.pkl",
        "feature_method": "tfidf",
        "min_samples_reached": True,
    }
    sp = tmp_path / "router-stats.json"
    sp.write_text(json.dumps(data), encoding="utf-8")
    with patch.object(router, "_stats_path", return_value=sp):
        stats = router.get_router_stats()
    assert stats.training_samples == 75
    assert stats.holdout_accuracy == pytest.approx(0.88)
    assert stats.min_samples_reached is True


# ---------------------------------------------------------------------------
# skills.py integration
# ---------------------------------------------------------------------------

def test_find_matching_skills_uses_router(monkeypatch, tmp_path):
    """use_router=True calls route_skills."""
    _setup_workspace(monkeypatch, tmp_path)
    from skills import find_matching_skills, save_skill
    import router

    skill = _make_skill("sk001", "research skill", "research anything", ["research", "find"])
    # Save it so load_skills() can find it
    save_skill(skill)

    route_called = [False]

    def fake_route(goal, candidates, top_k=3):
        route_called[0] = True
        from router import RouteResult
        return [RouteResult(skill_id=skill.id, skill_name=skill.name, score=0.9, method="router")]

    monkeypatch.setattr(router, "route_skills", fake_route)
    with patch.dict("sys.modules", {"router": router}):
        result = find_matching_skills("research polymarket", use_router=True)

    assert route_called[0] is True
    assert len(result) >= 1


def test_find_matching_skills_router_fallback(monkeypatch, tmp_path):
    """Router raising exception falls back to keyword matching."""
    _setup_workspace(monkeypatch, tmp_path)
    from skills import find_matching_skills, save_skill

    skill = _make_skill("sk001", "research skill", "research anything", ["research", "find"])
    save_skill(skill)

    def bad_route(*a, **kw):
        raise RuntimeError("router is broken")

    with patch("skills.route_skills", side_effect=bad_route, create=True):
        # Even if route_skills import fails, find_matching_skills must not raise
        try:
            result = find_matching_skills("research task", use_router=True)
            # keyword fallback should work
            assert isinstance(result, list)
        except Exception:
            pytest.fail("find_matching_skills raised with router error")


def test_find_matching_skills_use_router_false(monkeypatch, tmp_path):
    """use_router=False skips router entirely, uses keyword matching."""
    _setup_workspace(monkeypatch, tmp_path)
    from skills import find_matching_skills, save_skill
    import router

    skill = _make_skill("sk001", "research skill", "Does research", ["research", "polymarket"])
    save_skill(skill)

    route_called = [False]

    def spy_route(*a, **kw):
        route_called[0] = True
        return []

    monkeypatch.setattr(router, "route_skills", spy_route)
    result = find_matching_skills("research polymarket", use_router=False)

    # route_skills should NOT have been called
    assert route_called[0] is False
    # keyword matching should still find the skill
    assert any(s.id == skill.id for s in result)


# ---------------------------------------------------------------------------
# evolver integration
# ---------------------------------------------------------------------------

def test_evolver_calls_maybe_retrain(monkeypatch, tmp_path):
    """maybe_retrain is called after run_evolver completes."""
    _setup_workspace(monkeypatch, tmp_path)

    retrain_called = [False]

    def fake_maybe_retrain(force=False):
        retrain_called[0] = True
        return None

    import evolver
    import router as _router_mod

    monkeypatch.setattr(_router_mod, "maybe_retrain", fake_maybe_retrain)

    # Patch load_outcomes to return empty list to skip LLM analysis
    with patch.object(evolver, "load_outcomes", return_value=[]):
        evolver.run_evolver(min_outcomes=0, dry_run=True, verbose=False)

    assert retrain_called[0] is True
