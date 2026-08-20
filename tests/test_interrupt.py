"""Tests for interrupt.py — source-agnostic interrupt queue."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue_path(tmp_path):
    return tmp_path / "interrupts.jsonl"


@pytest.fixture
def queue(queue_path):
    from interrupt import InterruptQueue
    return InterruptQueue(queue_path=queue_path)


# ---------------------------------------------------------------------------
# InterruptQueue.post / poll / peek / clear
# ---------------------------------------------------------------------------

class TestInterruptQueue:
    def test_post_creates_file(self, queue, queue_path):
        queue.post("also check rate limiting", source="cli", intent="additive")
        assert queue_path.exists()

    def test_post_returns_interrupt(self, queue):
        from interrupt import Interrupt
        intr = queue.post("stop", source="telegram", intent="stop")
        assert isinstance(intr, Interrupt)
        assert intr.intent == "stop"
        assert intr.source == "telegram"
        assert intr.id  # non-empty

    def test_poll_returns_pending(self, queue):
        queue.post("also do X", source="cli", intent="additive")
        queue.post("stop", source="cli", intent="stop")
        pending = queue.poll()
        assert len(pending) == 2

    def test_poll_marks_applied(self, queue, queue_path):
        queue.post("also do X", source="cli", intent="additive")
        queue.poll()
        # Second poll should be empty
        pending2 = queue.poll()
        assert len(pending2) == 0

    def test_peek_does_not_consume(self, queue):
        queue.post("also do X", source="cli", intent="additive")
        p1 = queue.peek()
        p2 = queue.peek()
        assert len(p1) == 1
        assert len(p2) == 1

    def test_poll_empty_queue(self, queue):
        assert queue.poll() == []

    def test_is_empty(self, queue):
        assert queue.is_empty()
        queue.post("stop", source="cli", intent="stop")
        assert not queue.is_empty()

    def test_clear_removes_pending(self, queue):
        queue.post("do X", source="cli", intent="additive")
        queue.post("do Y", source="cli", intent="additive")
        n = queue.clear()
        assert n == 2
        assert queue.is_empty()

    def test_clear_already_applied_not_counted(self, queue):
        queue.post("do X", source="cli", intent="additive")
        queue.poll()  # mark applied
        n = queue.clear()
        assert n == 0

    def test_poll_order_preserved(self, queue):
        queue.post("first", source="cli", intent="additive")
        queue.post("second", source="cli", intent="priority")
        pending = queue.poll()
        assert pending[0].message == "first"
        assert pending[1].message == "second"

    def test_to_dict_round_trip(self, queue):
        from interrupt import Interrupt
        intr = queue.post("do X", source="cli", intent="additive")
        d = intr.to_dict()
        recovered = Interrupt.from_dict(d)
        assert recovered.id == intr.id
        assert recovered.message == intr.message
        assert recovered.intent == intr.intent


# ---------------------------------------------------------------------------
# Intent classification (heuristic)
# ---------------------------------------------------------------------------

class TestClassifyIntentHeuristic:
    def test_stop_keywords(self):
        from interrupt import _classify_intent
        intent, steps, goal = _classify_intent("stop", adapter=None)
        assert intent == "stop"

    def test_stop_keyword_halt(self):
        from interrupt import _classify_intent
        intent, _, _ = _classify_intent("halt everything", adapter=None)
        assert intent == "stop"

    def test_additive_default(self):
        from interrupt import _classify_intent
        intent, steps, _ = _classify_intent("also research competitor pricing", adapter=None)
        assert intent == "additive"
        assert len(steps) >= 1

    def test_corrective_keyword_instead(self):
        from interrupt import _classify_intent
        intent, _, _ = _classify_intent("focus on security instead", adapter=None)
        assert intent == "corrective"

    def test_priority_keyword_first(self):
        from interrupt import _classify_intent
        intent, steps, _ = _classify_intent("first check the API rate limits", adapter=None)
        assert intent == "priority"

    def test_stop_exclamation(self):
        from interrupt import _classify_intent
        intent, _, _ = _classify_intent("Stop!", adapter=None)
        assert intent == "stop"


class TestClassifyIntentLLM:
    def test_llm_additive(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "additive",
            "new_steps": ["check rate limits", "verify API keys"],
            "replacement_goal": None,
        })
        mock_adapter.complete.return_value = mock_resp

        intent, steps, goal = _classify_intent("also check rate limits", adapter=mock_adapter)
        assert intent == "additive"
        assert "check rate limits" in steps

    def test_llm_stop(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "stop",
            "new_steps": [],
            "replacement_goal": None,
        })
        mock_adapter.complete.return_value = mock_resp
        intent, _, _ = _classify_intent("cancel everything", adapter=mock_adapter)
        assert intent == "stop"

    def test_llm_note_coerced_to_additive(self):
        """note is explicit-only (--intent note): a free-text message the LLM
        labels "note" is a plan-change request that would be silently
        downgraded to context-only — coerce to additive instead
        (adversarial review 2026-07-15)."""
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "note",
            "new_steps": ["also check rate limits"],
            "replacement_goal": None,
        })
        mock_adapter.complete.return_value = mock_resp
        intent, steps, _ = _classify_intent(
            "also check rate limits", adapter=mock_adapter)
        assert intent == "additive"
        assert "also check rate limits" in steps

    def test_llm_corrective(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "corrective",
            "new_steps": ["focus on security"],
            "replacement_goal": "Analyze security vulnerabilities instead",
        })
        mock_adapter.complete.return_value = mock_resp
        intent, steps, replacement = _classify_intent("actually focus on security", adapter=mock_adapter)
        assert intent == "corrective"
        assert replacement == "Analyze security vulnerabilities instead"

    def test_llm_falls_back_on_bad_json(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "this is not json"
        mock_adapter.complete.return_value = mock_resp
        # Should not raise, falls back to heuristic
        intent, steps, _ = _classify_intent("also check rate limits", adapter=mock_adapter)
        assert intent in {"additive", "corrective", "priority", "stop"}

    def test_llm_invalid_intent_fallback(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"intent": "unknown_thing", "new_steps": [], "replacement_goal": None})
        mock_adapter.complete.return_value = mock_resp
        intent, _, _ = _classify_intent("do something", adapter=mock_adapter)
        assert intent == "additive"  # default for invalid


# ---------------------------------------------------------------------------
# apply_interrupt_to_steps
# ---------------------------------------------------------------------------

class TestApplyInterruptToSteps:
    def _make_interrupt(self, intent, new_steps=None, replacement_goal=None):
        from interrupt import Interrupt
        return Interrupt(
            id="test01",
            message="test",
            source="cli",
            intent=intent,
            new_steps=new_steps or [],
            replacement_goal=replacement_goal,
        )

    def test_stop_clears_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("stop")
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1", "step2"], "my goal")
        assert should_stop is True
        assert remaining == []

    def test_additive_appends(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("additive", new_steps=["new step A"])
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1", "step2"], "my goal")
        assert should_stop is False
        assert remaining == ["step1", "step2", "new step A"]

    def test_priority_prepends(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("priority", new_steps=["urgent step"])
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1", "step2"], "my goal")
        assert should_stop is False
        assert remaining == ["urgent step", "step1", "step2"]

    def test_corrective_replaces_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("corrective", new_steps=["new plan A", "new plan B"], replacement_goal="New goal")
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["old step"], "my goal")
        assert should_stop is False
        assert remaining == ["new plan A", "new plan B"]
        assert goal == "New goal"

    def test_corrective_keeps_remaining_if_no_new_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("corrective", new_steps=[], replacement_goal="New goal")
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1"], "my goal")
        assert remaining == ["step1"]
        assert goal == "New goal"

    def test_additive_empty_new_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("additive", new_steps=[])
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1"], "goal")
        assert remaining == ["step1"]
        assert not should_stop


# ---------------------------------------------------------------------------
# Loop lock
# ---------------------------------------------------------------------------

class TestLoopLock:
    def test_set_and_get(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, get_running_loop, clear_loop_running, is_loop_running

        assert not is_loop_running()
        set_loop_running("abc123", "test goal")
        info = get_running_loop()
        assert info is not None
        assert info["loop_id"] == "abc123"
        assert is_loop_running()

        clear_loop_running()
        assert not is_loop_running()

    def test_stale_lock_cleared(self, tmp_path, monkeypatch):
        """Lock with dead PID should be treated as not running."""
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        # Write a lock with PID 99999999 (almost certainly doesn't exist)
        lock_path.write_text(json.dumps({"loop_id": "old", "pid": 99999999, "goal": "x"}))
        from interrupt import get_running_loop
        result = get_running_loop()
        assert result is None
        assert not lock_path.exists()


# ---------------------------------------------------------------------------
# Integration: run_agent_loop with interrupt
# ---------------------------------------------------------------------------

class TestAgentLoopInterrupt:
    def test_stop_interrupt_halts_loop(self, tmp_path, monkeypatch):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        # Patch orch so no real filesystem needed
        mock_orch = MagicMock()
        mock_orch.project_dir.return_value = tmp_path / "proj"
        mock_orch.orch_root.return_value = tmp_path
        mock_orch.STATE_DONE = "done"
        mock_orch.STATE_BLOCKED = "blocked"
        mock_orch.append_next_items.return_value = [1, 2, 3]
        mock_orch.append_decision.return_value = None
        mock_orch.mark_item.return_value = None
        mock_orch.write_operator_status.return_value = None

        monkeypatch.setattr("agent_loop._orch", lambda: mock_orch)

        from interrupt import InterruptQueue, INTENT_STOP

        q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
        # Pre-load a stop interrupt so it fires after step 1
        q.post("stop", source="test", intent="stop")

        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "do three things",
            dry_run=True,
            project="test-interrupt",
            interrupt_queue=q,
        )
        assert result.status == "interrupted"
        assert result.interrupts_applied == 1

    def test_additive_interrupt_extends_steps(self, tmp_path, monkeypatch):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        mock_orch = MagicMock()
        mock_orch.project_dir.return_value = tmp_path / "proj"
        mock_orch.orch_root.return_value = tmp_path
        mock_orch.STATE_DONE = "done"
        mock_orch.STATE_BLOCKED = "blocked"
        mock_orch.append_next_items.return_value = [1, 2, 3, 4]
        mock_orch.append_decision.return_value = None
        mock_orch.mark_item.return_value = None
        mock_orch.write_operator_status.return_value = None

        monkeypatch.setattr("agent_loop._orch", lambda: mock_orch)

        from interrupt import InterruptQueue

        q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
        # Post additive interrupt — should complete without stopping
        q.post("also verify the results", source="test", intent="additive")

        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "research topic",
            dry_run=True,
            project="test-additive",
            interrupt_queue=q,
        )
        # Loop finishes (dry-run always completes steps)
        assert result.status == "done"
        assert result.interrupts_applied == 1


# ---------------------------------------------------------------------------
# Project isolation: per-project lockfile
# ---------------------------------------------------------------------------

class TestProjectLock:
    def test_set_project_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, get_running_project_loop, is_project_running, clear_loop_running

        set_loop_running("abc123", "test goal", project="polymarket")
        # Global lock exists
        assert lock_path.exists()
        # Per-project lock exists
        proj_lock = tmp_path / "loop-polymarket.lock"
        assert proj_lock.exists()
        # is_project_running returns True
        assert is_project_running("polymarket")
        # Different project is not running
        assert not is_project_running("nootropics")

    def test_clear_loop_removes_project_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, clear_loop_running, is_project_running

        set_loop_running("abc123", "goal", project="research")
        assert is_project_running("research")
        clear_loop_running()
        assert not is_project_running("research")
        assert not lock_path.exists()

    def test_no_project_no_project_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, is_project_running, clear_loop_running

        set_loop_running("abc123", "no-project goal")  # no project kwarg
        # No per-project lock files should be created
        proj_locks = list(tmp_path.glob("loop-*.lock"))
        assert proj_locks == []
        assert not is_project_running("anything")
        clear_loop_running()

    def test_stale_project_lock_cleared(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import get_running_project_loop

        # Write a per-project lock with dead PID
        proj_lock = tmp_path / "loop-recipes.lock"
        proj_lock.write_text(json.dumps({"loop_id": "old", "pid": 99999999, "project": "recipes"}))
        result = get_running_project_loop("recipes")
        assert result is None
        assert not proj_lock.exists()

    def test_project_lock_payload_includes_project(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, get_running_loop

        set_loop_running("loop1", "my goal", project="polymarket-edges")
        info = get_running_loop()
        assert info is not None
        assert info["project"] == "polymarket-edges"


class TestTheInterruptChannelSurvivesATornByte:
    """One torn byte used to kill the operator's whole control channel.

    _read_lines strict-decoded the queue, and it feeds peek(), which GATES
    poll()/clear()/is_empty() — so a single crash-torn append raised
    UnicodeDecodeError out of every consumer. Stop/pivot messages, and the
    kill switch's STOP interrupt, stopped reaching a running loop until
    someone repaired the file by hand (loop_post_step logs an ERROR per
    step, so it was loud — but the channel stayed dead). Probed live
    2026-08-20 against the pre-fix code with a correctly-shaped row.
    """

    def _row(self, iid: str, message: str) -> dict:
        return {"id": iid, "message": message, "source": "cli",
                "intent": "additive", "new_steps": [], "replacement_goal": None,
                "timestamp": "2026-08-19T00:00:00+00:00", "applied": False}

    def test_a_torn_row_costs_one_interrupt_not_the_channel(self, tmp_path):
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        p.write_bytes(json.dumps(self._row("i1", "stop the loop")).encode()
                      + b"\n" + b'{"id": "i2", "torn\xff": 1}\n')

        pending = InterruptQueue(queue_path=p).poll()  # used to raise

        assert [i.id for i in pending] == ["i1"]

    def test_the_torn_row_rides_the_rewrite_verbatim(self, tmp_path):
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        torn = b'{"id": "i2", "torn\xff": 1}'
        p.write_bytes(json.dumps(self._row("i1", "stop")).encode() + b"\n"
                      + torn + b"\n")

        InterruptQueue(queue_path=p).poll()

        after = p.read_bytes()
        assert torn in after, "poll's rewrite must not delete what it cannot read"
        assert b'"applied": true' in after, "the healthy row was still marked"

    def test_a_tainted_twin_is_never_laundered(self, tmp_path):
        """It parses as JSON; re-dumping it would erase the corruption signal."""
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        tainted = json.dumps(self._row("i2", "tainted")).encode().replace(
            b"tainted", b"tain\xffed")
        p.write_bytes(json.dumps(self._row("i1", "stop")).encode() + b"\n"
                      + tainted + b"\n")

        InterruptQueue(queue_path=p).poll()

        after = p.read_bytes()
        assert tainted in after
        assert b"udcff" not in after.lower()

    def test_clear_also_preserves_what_it_cannot_read(self, tmp_path):
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        torn = b'{"id": "i2", "torn\xff": 1}'
        p.write_bytes(json.dumps(self._row("i1", "stop")).encode() + b"\n"
                      + torn + b"\n")

        assert InterruptQueue(queue_path=p).clear() == 1

        assert torn in p.read_bytes()

    def test_an_undeliverable_interrupt_is_announced(self, tmp_path, caplog):
        """An interrupt the operator posted and the loop never saw is exactly
        the event that must not pass in silence."""
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        p.write_bytes(json.dumps(self._row("i1", "stop")).encode() + b"\n"
                      + b'{"id": "i2", "torn\xff": 1}\n')

        with caplog.at_level("WARNING"):
            InterruptQueue(queue_path=p).peek()

        assert any("cannot be delivered" in r.message for r in caplog.records), caplog.text


class TestTheInterruptChannelSurvivesAJsonValueThatIsNotARow:
    """Adversarial round 2026-08-20, three lenses convergent and verified.

    `loads_clean` refuses byte taint, not wrong SHAPE. `[]`, `null` and
    `"x"` are all valid, taint-free JSON, and every one of them reached
    `.get()` and raised AttributeError — which peek's handler does not catch,
    so the control channel went down exactly as it did on a torn byte. The
    byte-safety fix had closed one door and left the one beside it open.
    """

    def _row(self, iid: str, message: str, **over) -> dict:
        d = {"id": iid, "message": message, "source": "cli", "intent": "additive",
             "new_steps": [], "replacement_goal": None,
             "timestamp": "2026-08-19T00:00:00+00:00", "applied": False}
        d.update(over)
        return d

    def test_a_non_object_row_costs_one_interrupt_not_the_channel(self, tmp_path):
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        p.write_bytes((json.dumps(self._row("i1", "stop the loop")) + "\n"
                       + "[]\nnull\n\"x\"\n").encode())

        pending = InterruptQueue(queue_path=p).poll()  # used to raise

        assert [i.id for i in pending] == ["i1"]
        after = p.read_text()
        assert "[]" in after and "null" in after, "carried, not dropped"

    def test_a_string_applied_flag_does_not_swallow_a_stop(self, tmp_path):
        """`"applied": "false"` is legal JSON and truthy. Reading it as
        applied meant a STOP was silently never delivered, with no warning."""
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        p.write_bytes((json.dumps(
            self._row("i9", "STOP", intent="stop", applied="false")) + "\n").encode())

        assert [i.id for i in InterruptQueue(queue_path=p).peek()] == ["i9"]

    def test_an_already_applied_row_is_still_not_redelivered(self, tmp_path):
        """The strict-True check must not turn every row back into pending."""
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        p.write_bytes((json.dumps(self._row("done", "old", applied=True)) + "\n").encode())

        assert InterruptQueue(queue_path=p).peek() == []
        assert InterruptQueue(queue_path=p).poll() == []

    def test_a_row_carrying_a_unicode_line_separator_is_not_split(self, tmp_path):
        """JSONL frames on LF. splitlines() also breaks on U+2028/U+2029,
        which are legal inside a JSON string, so a rewrite would turn one
        valid row into two invalid fragments."""
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        row = json.dumps(self._row("i1", "line break"), ensure_ascii=False)
        p.write_bytes((row + "\n").encode())

        pending = InterruptQueue(queue_path=p).poll()

        assert [i.id for i in pending] == ["i1"]
        assert len([l for l in p.read_text().split("\n") if l.strip()]) == 1


class TestTheAppliedFlagIsReadNotGuessed:
    """Adversarial r2 (2026-08-20, 3/3 HIGH): the strict `is True` fix that
    closed r1's "false"-swallows-a-STOP bug flipped every OTHER non-boolean
    the other way. A legacy `"true"` or `1` had counted as applied for its
    whole life; suddenly it was pending, so poll() handed a CORRECTIVE
    interrupt back to the loop to be applied a second time.

    Both directions now have to hold at once, and a flag with no unambiguous
    reading makes the row unreadable rather than silently picking a failure.
    """

    def _row(self, iid: str, **over) -> dict:
        d = {"id": iid, "message": "corrective: do X instead", "source": "cli",
             "intent": "corrective", "new_steps": [], "replacement_goal": "X",
             "timestamp": "2026-08-19T00:00:00+00:00", "applied": False}
        d.update(over)
        return d

    def _peek(self, tmp_path, flag):
        from interrupt import InterruptQueue
        p = tmp_path / f"i-{abs(hash(repr(flag)))}.jsonl"
        p.write_text(json.dumps(self._row("legacy", applied=flag)) + "\n")
        return [i.id for i in InterruptQueue(queue_path=p).peek()], p

    @pytest.mark.parametrize("flag", [True, "true", "True", 1])
    def test_a_legacy_truthy_row_is_not_re_delivered(self, tmp_path, flag):
        delivered, _ = self._peek(tmp_path, flag)
        assert delivered == [], f"applied={flag!r} was re-delivered"

    @pytest.mark.parametrize("flag", [False, "false", "False", 0, None])
    def test_a_legacy_falsey_row_is_still_delivered(self, tmp_path, flag):
        delivered, _ = self._peek(tmp_path, flag)
        assert delivered == ["legacy"], f"applied={flag!r} swallowed the interrupt"

    def test_an_unreadable_flag_is_neither_delivered_nor_swallowed(self, tmp_path, caplog):
        """Choosing a default for garbage picks a failure — double-apply or a
        lost STOP — on the operator's behalf, in silence."""
        with caplog.at_level("WARNING"):
            delivered, p = self._peek(tmp_path, "maybe")

        assert delivered == []
        assert "maybe" in p.read_text(), "the row stays on disk"
        assert any("cannot be delivered" in r.message for r in caplog.records), caplog.text

    def test_an_unreadable_flag_survives_poll_verbatim(self, tmp_path):
        from interrupt import InterruptQueue
        p = tmp_path / "i.jsonl"
        row = json.dumps(self._row("weird", applied="maybe"))
        p.write_text(json.dumps(self._row("ok")) + "\n" + row + "\n")

        assert [i.id for i in InterruptQueue(queue_path=p).poll()] == ["ok"]

        assert row in p.read_text(), "the unreadable row was rewritten or dropped"


class TestARowThatBecomesUnreadableUnderTheLockIsAnnounced:
    """Adversarial r3 (2026-08-20, 3 lenses, probed): poll() and clear()
    preflight with an UNLOCKED peek() and then re-read under the lock. A row
    that becomes unreadable in that window is withheld from delivery and
    carried by the locked rewrite — both correct — but nobody was told.
    peek() is the only path that announced, and if the interrupt that WAS
    delivered stops the loop, no later peek() ever runs. An operator's
    message parked on disk in silence is the exact event this subsystem
    exists to make impossible."""

    @staticmethod
    def _row(**over):
        row = {"id": "ok", "source": "operator", "message": "stop",
               "intent": "stop", "applied": False,
               "created_at": "2026-01-01T00:00:00+00:00"}
        row.update(over)
        return row

    def _race(self, monkeypatch, tmp_path):
        """Make one foreign unreadable row land between peek() and the lock."""
        import jsonl_utils
        p = tmp_path / "interrupts.jsonl"
        p.write_text(json.dumps(self._row()) + "\n", encoding="utf-8")
        real = jsonl_utils.store_text
        fired = {}

        def racing(path):
            text = real(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(self._row(id="foreign", applied="maybe")) + "\n")
            fired["yes"] = True
            monkeypatch.setattr(jsonl_utils, "store_text", real)  # race once
            return text

        monkeypatch.setattr(jsonl_utils, "store_text", racing)
        return p, fired

    def test_poll_announces_it(self, monkeypatch, tmp_path, caplog):
        p, fired = self._race(monkeypatch, tmp_path)
        with caplog.at_level("WARNING"):
            from interrupt import InterruptQueue
            delivered = InterruptQueue(queue_path=p).poll()

        assert fired, "the racing hook never ran — test is vacuous"
        assert [i.id for i in delivered] == ["ok"]
        assert "foreign" in p.read_text(), "the unreadable row was destroyed"
        assert any("cannot be delivered" in r.msg for r in caplog.records), (
            "a row withheld under the lock was never announced")

    def test_clear_announces_it(self, monkeypatch, tmp_path, caplog):
        p, fired = self._race(monkeypatch, tmp_path)
        with caplog.at_level("WARNING"):
            from interrupt import InterruptQueue
            cleared = InterruptQueue(queue_path=p).clear()

        assert fired, "the racing hook never ran — test is vacuous"
        assert cleared == 1
        assert "foreign" in p.read_text(), "the unreadable row was destroyed"
        assert any("cannot be delivered" in r.msg for r in caplog.records)

    def test_a_clean_queue_stays_quiet(self, tmp_path, caplog):
        """The negative control: no warning when there is nothing to warn about."""
        p = tmp_path / "interrupts.jsonl"
        p.write_text(json.dumps(self._row()) + "\n", encoding="utf-8")
        from interrupt import InterruptQueue
        with caplog.at_level("WARNING"):
            InterruptQueue(queue_path=p).poll()
        assert not [r for r in caplog.records if "cannot be delivered" in r.msg]


class TestTheQueueSpeaksOncePerPass:
    """Adversarial r4 (3 lenses, probed): with peek() announcing AND the
    locked pass announcing, one pre-existing unreadable row produced TWO
    identical warnings per poll — each reading like a separate lost control
    action — and a row that was unreadable at preflight but repaired before
    the lock was announced as "cannot be delivered" in the same call that
    delivered it. The locked classification is the authoritative one."""

    @staticmethod
    def _row(**over):
        row = {"id": "ok", "source": "operator", "message": "stop",
               "intent": "stop", "applied": False,
               "created_at": "2026-01-01T00:00:00+00:00"}
        row.update(over)
        return row

    def _seed(self, tmp_path):
        p = tmp_path / "interrupts.jsonl"
        p.write_text(json.dumps(self._row()) + "\n"
                     + json.dumps(self._row(id="broken", applied="maybe")) + "\n",
                     encoding="utf-8")
        return p

    def test_a_stable_unreadable_row_is_announced_exactly_once(self, tmp_path, caplog):
        from interrupt import InterruptQueue

        p = self._seed(tmp_path)
        with caplog.at_level("WARNING"):
            delivered = InterruptQueue(queue_path=p).poll()

        assert [i.id for i in delivered] == ["ok"]
        warnings = [r for r in caplog.records if "cannot be delivered" in r.msg]
        assert len(warnings) == 1, (
            f"{len(warnings)} warnings for one unreadable row — each reads "
            "like a separate lost control action")

    def test_clear_announces_exactly_once_too(self, tmp_path, caplog):
        from interrupt import InterruptQueue

        p = self._seed(tmp_path)
        with caplog.at_level("WARNING"):
            InterruptQueue(queue_path=p).clear()

        assert len([r for r in caplog.records if "cannot be delivered" in r.msg]) == 1

    def test_a_row_repaired_before_the_lock_is_not_called_undeliverable(
            self, monkeypatch, tmp_path, caplog):
        """The false-warning direction: the preflight saw it broken, the
        locked pass delivered it, and the operator was told it could not be
        delivered."""
        import jsonl_utils
        from interrupt import InterruptQueue

        p = self._seed(tmp_path)
        real = jsonl_utils.store_text
        fired = {}

        def racing(path):
            text = real(path)
            healthy = json.dumps(self._row(id="broken"))
            path.write_text(json.dumps(self._row()) + "\n" + healthy + "\n",
                            encoding="utf-8")
            fired["yes"] = True
            monkeypatch.setattr(jsonl_utils, "store_text", real)
            return text

        monkeypatch.setattr(jsonl_utils, "store_text", racing)
        with caplog.at_level("WARNING"):
            delivered = InterruptQueue(queue_path=p).poll()

        assert fired, "the racing hook never ran — test is vacuous"
        assert sorted(i.id for i in delivered) == ["broken", "ok"]
        assert not [r for r in caplog.records if "cannot be delivered" in r.msg], (
            "an interrupt that WAS delivered was announced as undeliverable")


class TestAFailedCommitIsNotAnEmptyQueue:
    """Adversarial r4 (2 lenses, probed): poll() caught OSError from the
    locked rewrite and returned [] — which the loop reads as "no
    interrupts". A full disk or a failed replace turned a posted STOP into
    silence, and the only difference from a quiet queue was that nothing
    said so."""

    def test_poll_says_so(self, monkeypatch, tmp_path, caplog):
        import file_lock as _fl
        from interrupt import InterruptQueue

        p = tmp_path / "interrupts.jsonl"
        p.write_text(json.dumps({"id": "ok", "source": "operator",
                                 "message": "stop", "intent": "stop",
                                 "applied": False,
                                 "created_at": "2026-01-01T00:00:00+00:00"}) + "\n",
                     encoding="utf-8")

        def boom(path, fn, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_fl, "locked_rmw", boom)
        with caplog.at_level("ERROR"):
            assert InterruptQueue(queue_path=p).poll() == []

        assert any("NOT delivered" in r.msg for r in caplog.records), (
            "a failed commit was indistinguishable from an empty queue")

    def test_clear_says_so(self, monkeypatch, tmp_path, caplog):
        import file_lock as _fl
        from interrupt import InterruptQueue

        p = tmp_path / "interrupts.jsonl"
        p.write_text(json.dumps({"id": "ok", "source": "operator",
                                 "message": "stop", "intent": "stop",
                                 "applied": False,
                                 "created_at": "2026-01-01T00:00:00+00:00"}) + "\n",
                     encoding="utf-8")

        def boom(path, fn, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_fl, "locked_rmw", boom)
        with caplog.at_level("ERROR"):
            assert InterruptQueue(queue_path=p).clear() == 0
        assert any("nothing was cleared" in r.msg for r in caplog.records)


class TestAQueueOfNothingButUnreadableRowsStillSpeaks:
    """Adversarial r5 (2026-08-20, 4 lenses, probed) on r4's own fix. r4
    silenced the preflight peek() so the locked pass could own the single
    announcement — correct, except that poll()/clear() return BEFORE the
    locked pass when the preflight finds nothing deliverable. A queue holding
    only a corrupt STOP therefore behaved exactly like an empty queue: no
    delivery, no warning, no trace. The rule is one announcement per pass from
    whichever branch actually runs — not from the branch that usually runs."""

    BAD = json.dumps({"id": "stop", "source": "operator", "message": "STOP",
                      "intent": "stop", "applied": "maybe"})
    GOOD = json.dumps({"id": "ok", "source": "operator", "message": "go",
                       "intent": "stop", "applied": False,
                       "created_at": "2026-01-01T00:00:00+00:00"})

    def _q(self, tmp_path, content):
        from interrupt import InterruptQueue
        p = tmp_path / "interrupts.jsonl"
        p.write_text(content, encoding="utf-8")
        return InterruptQueue(queue_path=p), p

    @staticmethod
    def _warnings(caplog):
        return [r for r in caplog.records if "cannot be delivered" in r.message]

    def test_poll_announces_the_stranded_row(self, tmp_path, caplog):
        q, p = self._q(tmp_path, self.BAD + "\n")
        with caplog.at_level("WARNING"):
            assert q.poll() == []
        assert len(self._warnings(caplog)) == 1, caplog.text
        assert self.BAD in p.read_text(), "the raw line must stay on disk"

    def test_clear_announces_the_stranded_row(self, tmp_path, caplog):
        q, p = self._q(tmp_path, self.BAD + "\n")
        with caplog.at_level("WARNING"):
            assert q.clear() == 0
        assert len(self._warnings(caplog)) == 1, caplog.text
        assert self.BAD in p.read_text()

    def test_a_mixed_queue_still_announces_exactly_once(self, tmp_path, caplog):
        """The r4 defect in the other direction: two warnings per pass, each
        reading like a separate lost control action."""
        q, p = self._q(tmp_path, self.GOOD + "\n" + self.BAD + "\n")
        with caplog.at_level("WARNING"):
            assert [i.id for i in q.poll()] == ["ok"]
        assert len(self._warnings(caplog)) == 1, caplog.text

    def test_a_healthy_queue_says_nothing(self, tmp_path, caplog):
        """The negative control: a warning that always fires is noise."""
        q, _ = self._q(tmp_path, self.GOOD + "\n")
        with caplog.at_level("WARNING"):
            assert [i.id for i in q.poll()] == ["ok"]
        assert self._warnings(caplog) == []

    def test_an_empty_queue_says_nothing(self, tmp_path, caplog):
        q, _ = self._q(tmp_path, "")
        with caplog.at_level("WARNING"):
            assert q.poll() == []
            assert q.clear() == 0
        assert self._warnings(caplog) == []
