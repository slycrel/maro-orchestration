"""Tests for src/conversation.py — ConversationChannel / ThreadChannel."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_conversation():
    """Import conversation module with memory_dir patched to tmp."""
    import importlib
    import sys
    # Ensure fresh import picks up any patches
    if "conversation" in sys.modules:
        del sys.modules["conversation"]
    import conversation
    return conversation


def _make_channel(tmp_path: Path, handle_id: str = "test001", goal: str = "test goal"):
    """Create a ThreadChannel with memory_dir patched to tmp_path."""
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        import conversation
        ch = conversation.ThreadChannel(handle_id, goal)
    return ch


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_create_and_get_channel(tmp_path):
    """create_channel registers the channel; get_channel retrieves it."""
    import conversation
    # Clear registry for isolation
    with conversation._registry_lock:
        conversation._registry.clear()

    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.create_channel("abc123", "research polymarket")

    assert ch is not None
    assert ch.handle_id == "abc123"
    assert ch.goal == "research polymarket"

    found = conversation.get_channel("abc123")
    assert found is ch


def test_get_channel_missing_returns_none(tmp_path):
    """get_channel returns None for unknown handle_id."""
    import conversation
    result = conversation.get_channel("nonexistent_handle_xyz")
    assert result is None


def test_list_channels_returns_summaries(tmp_path):
    """list_channels returns a list of dicts with expected keys."""
    import conversation
    with conversation._registry_lock:
        conversation._registry.clear()

    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        conversation.create_channel("id1", "goal one")
        conversation.create_channel("id2", "goal two")

    summaries = conversation.list_channels()
    handle_ids = [s["handle_id"] for s in summaries]
    assert "id1" in handle_ids
    assert "id2" in handle_ids
    for s in summaries:
        assert "goal" in s
        assert "status" in s
        assert "waiting" in s
        assert "created_at" in s
        assert "event_count" in s


def test_list_channels_empty_when_no_channels(tmp_path):
    """list_channels returns [] when registry is empty."""
    import conversation
    with conversation._registry_lock:
        conversation._registry.clear()

    result = conversation.list_channels()
    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# ThreadChannel.emit tests
# ---------------------------------------------------------------------------

def test_emit_appends_to_events(tmp_path):
    """emit appends an event dict to self.events."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("e001", "emit test")

    initial_count = len(ch._events)  # has user_goal from __init__
    ch.emit("step", text="doing work", step_num=1)
    assert len(ch._events) == initial_count + 1

    last = ch._events[-1]
    assert last["type"] == "step"
    assert last["text"] == "doing work"
    assert last["step_num"] == 1
    assert "ts" in last


def test_emit_complete_sets_status(tmp_path):
    """emit('complete') sets channel status to 'complete'."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("e002", "status test")

    assert ch.status == "running"
    ch.emit("complete", text="all done")
    assert ch.status == "complete"


def test_emit_error_sets_status(tmp_path):
    """emit('error') sets channel status to 'error'."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("e003", "error test")

    ch.emit("error", text="something broke")
    assert ch.status == "error"


def test_emit_persists_to_jsonl(tmp_path):
    """emit writes the event to the JSONL file."""
    import json
    import conversation
    threads_dir = tmp_path / "threads"
    with patch("conversation._threads_dir", return_value=threads_dir):
        ch = conversation.ThreadChannel("e004", "persist test")
        ch.emit("step", text="checkpoint")

    jsonl_path = threads_dir / "e004.jsonl"
    assert jsonl_path.exists()
    lines = [l for l in jsonl_path.read_text().splitlines() if l.strip()]
    events = [json.loads(l) for l in lines]
    types = [e["type"] for e in events]
    assert "user_goal" in types
    assert "step" in types


# ---------------------------------------------------------------------------
# ThreadChannel.ask / reply tests
# ---------------------------------------------------------------------------

def test_ask_returns_reply_from_another_thread(tmp_path):
    """ask() blocks until receive_reply() is called from another thread."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("q001", "ask test")

    replies = []

    def _asker():
        reply = ch.ask("What dataset?", timeout=5)
        replies.append(reply)

    t = threading.Thread(target=_asker)
    t.start()
    time.sleep(0.1)  # give asker time to block
    ch.receive_reply("use the polymarket dataset")
    t.join(timeout=5)
    assert replies == ["use the polymarket dataset"]


def test_ask_emits_question_event(tmp_path):
    """ask() emits a 'question' event before blocking."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("q002", "ask event test")

    def _answer():
        time.sleep(0.05)
        ch.receive_reply("my answer")

    threading.Thread(target=_answer, daemon=True).start()
    ch.ask("Any question?", timeout=5)

    types = [e["type"] for e in ch._events]
    assert "question" in types


def test_ask_emits_user_reply_event(tmp_path):
    """ask() emits a 'user_reply' event when reply arrives."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("q003", "user_reply test")

    def _answer():
        time.sleep(0.05)
        ch.receive_reply("my reply text")

    threading.Thread(target=_answer, daemon=True).start()
    ch.ask("question?", timeout=5)

    types = [e["type"] for e in ch._events]
    assert "user_reply" in types


def test_ask_returns_none_on_timeout(tmp_path):
    """ask() returns None and emits 'question_timeout' when no reply arrives."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("q004", "timeout test")

    result = ch.ask("No one will answer", timeout=0)  # instant timeout

    assert result is None
    types = [e["type"] for e in ch._events]
    assert "question_timeout" in types


def test_ask_clears_waiting_flag_after_reply(tmp_path):
    """waiting_for_reply is True during ask and False after reply arrives."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("q005", "waiting flag test")

    waiting_states = []

    def _asker():
        # Small delay then check waiting
        def _check():
            time.sleep(0.05)
            waiting_states.append(ch.waiting_for_reply)
            ch.receive_reply("done")
        threading.Thread(target=_check, daemon=True).start()
        ch.ask("question?", timeout=5)
        waiting_states.append(ch.waiting_for_reply)

    _asker()
    assert waiting_states[0] is True   # was waiting during ask
    assert waiting_states[1] is False  # cleared after reply


def test_ask_clears_waiting_flag_on_timeout(tmp_path):
    """waiting_for_reply is cleared after a timeout."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("q006", "timeout flag test")

    ch.ask("question?", timeout=0)
    assert ch.waiting_for_reply is False


# ---------------------------------------------------------------------------
# events_since tests
# ---------------------------------------------------------------------------

def test_events_since_returns_correct_slice(tmp_path):
    """events_since(n) returns events starting from index n."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("s001", "slice test")

    ch.emit("step", text="step1")
    ch.emit("step", text="step2")
    ch.emit("step", text="step3")

    total = len(ch._events)
    since_0 = ch.events_since(0)
    assert len(since_0) == total

    since_last = ch.events_since(total - 1)
    assert len(since_last) == 1
    assert since_last[0]["text"] == "step3"

    beyond = ch.events_since(total + 100)
    assert beyond == []


# ---------------------------------------------------------------------------
# notify_low_confidence and complete
# ---------------------------------------------------------------------------

def test_notify_low_confidence_emits_correct_event(tmp_path):
    """notify_low_confidence emits a 'low_confidence' event with expected fields."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("lc001", "confidence test")

    ch.notify_low_confidence(
        decision="continue: proceed with risky action",
        confidence=0.6,
        reasoning="not enough data to be sure",
    )

    lc_events = [e for e in ch._events if e["type"] == "low_confidence"]
    assert len(lc_events) == 1
    ev = lc_events[0]
    assert ev["text"] == "continue: proceed with risky action"
    assert ev["confidence"] == 0.6
    assert "not enough data" in ev["reasoning"]


def test_complete_sets_status_and_emits_event(tmp_path):
    """complete() emits 'complete' event and sets status='complete'."""
    import conversation
    with patch("conversation._threads_dir", return_value=tmp_path / "threads"):
        ch = conversation.ThreadChannel("c001", "complete test")

    ch.complete("Final result text here")
    assert ch.status == "complete"
    complete_events = [e for e in ch._events if e["type"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["text"] == "Final result text here"
