"""Tests for recall.py — the unified memory read seam (goal-brain step 3).

Dispatch slice: thread identity from origin ancestry, prior-attempt matching
over run metadata, guard signals. See docs/RECALL_DESIGN.md.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from recall import recall, RecallResult, PriorAttempt  # noqa: E402


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


def _make_run(goal, *, status="stuck", started_ago_minutes=5, origin=None,
              handle_id=None):
    """Create a run dir with finalized metadata, started N minutes ago."""
    import runs
    import uuid
    handle_id = handle_id or uuid.uuid4().hex[:12]
    rd = runs.create_run_dir(
        handle_id,
        prompt=goal,
        extra_metadata={"origin": origin} if origin else None,
    )
    meta_path = rd / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = status
    meta["started_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=started_ago_minutes)
    ).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return handle_id


class TestDispatchSlice:
    def test_no_history_knows_nothing(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        r = recall("build a websocket server", slice="dispatch")
        assert r.thread is None
        assert r.prior_attempts == []
        assert r.as_context_block() == ""

    def test_prior_attempts_exact_match(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        goal = "verify the polymarket rate limit handling end to end"
        for _ in range(3):
            _make_run(goal, status="stuck")
        _make_run("a completely different goal about gardening", status="done")

        r = recall(goal, slice="dispatch")
        assert len(r.prior_attempts) == 3
        assert all(a.match == "exact" for a in r.prior_attempts)
        assert all(a.status == "stuck" for a in r.prior_attempts)

    def test_near_match_by_word_overlap(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        # Same word set, different order: exact normalized compare fails,
        # jaccard similarity is 1.0 -> "near".
        _make_run("alpha beta gamma delta epsilon zeta eta theta", status="error")
        r = recall("beta alpha gamma delta epsilon zeta theta eta", slice="dispatch")
        assert len(r.prior_attempts) == 1
        assert r.prior_attempts[0].match == "near"

    def test_out_of_window_excluded(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        goal = "rebuild the index"
        _make_run(goal, status="stuck", started_ago_minutes=60 * 48)
        r = recall(goal, slice="dispatch", window_hours=24.0)
        assert r.prior_attempts == []

    def test_thread_identity_walks_ancestry(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        grandparent = _make_run("the original mission", status="done")
        parent = _make_run(
            "a continuation step", status="stuck",
            origin={"parent_handle_id": grandparent, "parent_goal": "the original mission"},
        )
        r = recall(
            "the next fragment",
            slice="dispatch",
            origin={
                "parent_handle_id": parent,
                "parent_goal": "a continuation step",
                "source": "task_store",
            },
        )
        assert r.thread is not None
        assert r.thread.parent_goal == "a continuation step"
        assert r.thread.chain == [parent, grandparent]
        assert r.thread.source == "task_store"

    def test_recall_performed_event_emitted(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        events = []
        with patch("captains_log.log_event",
                   side_effect=lambda et, *a, **k: events.append(et)):
            recall("anything at all", slice="dispatch")
        assert "RECALL_PERFORMED" in events


class TestDispatchSignals:
    def _result(self, attempts):
        return RecallResult(thread=None, prior_attempts=attempts)

    def _attempt(self, status, minutes_ago):
        when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        return PriorAttempt(goal="g", handle_id="h", status=status,
                            when=when, match="exact")

    def test_all_failing_true(self):
        r = self._result([self._attempt("stuck", 5),
                          self._attempt("error", 15),
                          self._attempt("stuck", 30)])
        sig = r.dispatch_signals(window_minutes=60)
        assert sig["repeat_count"] == 3
        assert sig["all_failing"] is True

    def test_done_disarms(self):
        r = self._result([self._attempt("stuck", 5),
                          self._attempt("done", 15),
                          self._attempt("stuck", 30)])
        sig = r.dispatch_signals(window_minutes=60)
        assert sig["repeat_count"] == 3
        assert sig["all_failing"] is False

    def test_window_filters_old_attempts(self):
        r = self._result([self._attempt("stuck", 5),
                          self._attempt("stuck", 120)])
        sig = r.dispatch_signals(window_minutes=60)
        assert sig["repeat_count"] == 1

    def test_empty_is_not_all_failing(self):
        sig = self._result([]).dispatch_signals(window_minutes=60)
        assert sig["repeat_count"] == 0
        assert sig["all_failing"] is False


class TestContextBlock:
    def test_block_summarizes_history_and_thread(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        goal = "fix the flaky scheduler lease test"
        parent = _make_run("parent mission", status="done")
        for _ in range(2):
            _make_run(goal, status="stuck")
        r = recall(goal, slice="dispatch",
                   origin={"parent_handle_id": parent,
                           "parent_goal": "parent mission",
                           "source": "agent_loop"})
        block = r.as_context_block()
        assert "parent mission" in block
        assert "2 runs" in block
        assert "2 stuck" in block

    def test_block_size_capped(self):
        attempts = [PriorAttempt(goal="g" * 500, handle_id="h", status="stuck",
                                 when="2026-06-10T00:00:00+00:00", match="exact")]
        r = RecallResult(thread=None, prior_attempts=attempts,
                         lessons="L" * 5000)
        assert len(r.as_context_block(max_chars=1200)) <= 1200
