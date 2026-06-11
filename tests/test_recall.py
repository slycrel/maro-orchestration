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


class TestLoopSlice:
    """The loop slice composes the eight memory substrates relocated from
    agent_loop._build_loop_context (2026-06-11)."""

    def test_loop_slice_populates_all_substrate_fields(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import memory
        import introspect
        import playbook as playbook_mod
        import knowledge_web

        class _L:
            outcome = "done"
            lesson = "prefer rg over grep on this box"

        monkeypatch.setattr(memory, "load_lessons",
                            lambda **kw: [_L()] if kw.get("task_type") == "agenda" else [])
        monkeypatch.setattr(memory, "inject_standing_rules",
                            lambda domain="": "## Standing Rules\n- always push")
        monkeypatch.setattr(memory, "inject_decisions",
                            lambda goal, domain="": "## Decisions\n- chose sqlite")
        monkeypatch.setattr(memory, "search_graveyard",
                            lambda goal, resurrect=False: [_L()])
        monkeypatch.setattr(introspect, "find_relevant_failure_notes",
                            lambda goal, limit=3, project="": ["decompose_too_broad"])
        monkeypatch.setattr(playbook_mod, "inject_playbook",
                            lambda max_chars=800: "## Playbook\n- batch the reads")
        monkeypatch.setattr(knowledge_web, "inject_knowledge_for_goal",
                            lambda goal, max_chars=600: "## Knowledge\n- K2 node")

        r = recall("research thinkpads", slice="loop", project="proj")

        assert "prefer rg over grep" in r.lessons
        assert "always push" in r.standing_rules
        assert "chose sqlite" in r.decisions
        assert "resurrected from decay" in r.graveyard
        assert "decompose_too_broad" in r.failure_notes
        assert "batch the reads" in r.playbook
        assert "K2 node" in r.knowledge
        assert r.sources["knowledge_blocks"] >= 7

    def test_as_loop_block_order_rules_lead_knowledge_trails(self):
        r = RecallResult(
            thread=None, prior_attempts=[],
            lessons="LESSONS", standing_rules="RULES", decisions="DECISIONS",
            graveyard="GRAVEYARD", failure_notes="FAILURES",
            learning_activity="ACTIVITY", playbook="PLAYBOOK",
            knowledge="KNOWLEDGE",
        )
        block = r.as_loop_block()
        order = ["RULES", "LESSONS", "DECISIONS", "GRAVEYARD", "FAILURES",
                 "ACTIVITY", "PLAYBOOK", "KNOWLEDGE"]
        positions = [block.index(s) for s in order]
        assert positions == sorted(positions)

    def test_as_loop_block_empty_when_nothing_known(self):
        assert RecallResult(thread=None, prior_attempts=[]).as_loop_block() == ""

    def test_lessons_cited_stamp_in_event(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import memory

        class _L:
            outcome = "done"
            lesson = "the cited lesson text"

        monkeypatch.setattr(memory, "load_lessons", lambda **kw: [_L()])
        events = []
        with patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            recall("any goal", slice="loop")
        performed = [kw for et, kw in events if et == "RECALL_PERFORMED"]
        assert len(performed) == 1
        cited = performed[0]["context"]["lessons_cited"]
        assert cited == ["the cited lesson text"]

    def test_broken_substrate_never_takes_seam_down(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import memory

        def _boom(**kw):
            raise RuntimeError("substrate down")

        monkeypatch.setattr(memory, "load_lessons", _boom)
        monkeypatch.setattr(memory, "inject_lessons_for_task",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(memory, "inject_standing_rules",
                            lambda domain="": "## Standing Rules\n- survives")
        r = recall("any goal", slice="loop")
        assert r.lessons == ""
        assert "survives" in r.standing_rules


class TestRecentLearningActivity:
    def test_filters_and_formats(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import captains_log as cl
        entries = [
            {"event_type": "LESSON_REINFORCED", "summary": "noise"},
            {"event_type": "SKILL_DEMOTED", "summary": "skill X demoted"},
            {"event_type": "EVOLVER_APPLIED", "summary": "applied Y"},
        ]
        monkeypatch.setattr(cl, "load_log", lambda limit=30: entries)
        from recall import recent_learning_activity
        block = recent_learning_activity()
        assert "skill X demoted" in block
        assert "applied Y" in block
        assert "noise" not in block
        assert block.startswith("## Recent Learning System Activity")

    def test_custom_event_set_and_header(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import captains_log as cl
        entries = [
            {"event_type": "EVOLVER_SKIPPED", "summary": "skipped Z"},
            {"event_type": "DIAGNOSIS", "summary": "diag"},
        ]
        monkeypatch.setattr(cl, "load_log", lambda limit=30: entries)
        from recall import recent_learning_activity
        block = recent_learning_activity(
            event_types=("EVOLVER_SKIPPED",),
            header="\n\nRecent learning system activity:")
        assert "skipped Z" in block
        assert "diag" not in block
        assert block.startswith("\n\nRecent learning system activity:")

    def test_empty_when_nothing_actionable(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import captains_log as cl
        monkeypatch.setattr(cl, "load_log", lambda limit=30: [])
        from recall import recent_learning_activity
        assert recent_learning_activity() == ""
