"""Tests for checkpoint.py — session checkpointing and resume (GAP 3)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import checkpoint as ckpt_module
from checkpoint import (
    Checkpoint,
    CompletedStep,
    delete_checkpoint,
    list_checkpoints,
    load_checkpoint,
    resume_from,
    write_checkpoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeStepOutcome:
    """Minimal stand-in for StepOutcome (avoids importing agent_loop)."""
    index: int
    text: str
    status: str
    result: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    iteration: int = 1
    confidence: str = ""


def _make_checkpoint(tmp_path: Path, loop_id: str = "abc12345", n_completed: int = 2) -> Checkpoint:
    steps = [f"step {i}" for i in range(1, 5)]
    completed = [CompletedStep(index=i, text=f"step {i}", status="done") for i in range(1, n_completed + 1)]
    c = Checkpoint(loop_id=loop_id, goal="test goal", project="proj", steps=steps, completed=completed)
    path = tmp_path / f"ckpt_{loop_id}.json"
    path.write_text(json.dumps(c.to_dict()), encoding="utf-8")
    return c


@pytest.fixture(autouse=True)
def patch_checkpoint_dir(tmp_path):
    """Redirect checkpoint storage to a temp directory."""
    with patch.object(ckpt_module, "_checkpoint_dir", return_value=tmp_path):
        yield tmp_path


# ---------------------------------------------------------------------------
# CompletedStep
# ---------------------------------------------------------------------------


class TestCompletedStep:
    def test_basic_fields(self):
        cs = CompletedStep(index=1, text="do thing", status="done")
        assert cs.index == 1
        assert cs.text == "do thing"
        assert cs.status == "done"

    def test_defaults(self):
        cs = CompletedStep(index=1, text="x", status="done")
        assert cs.tokens_in == 0
        assert cs.result == ""
        assert cs.elapsed_ms == 0


# ---------------------------------------------------------------------------
# Checkpoint dataclass
# ---------------------------------------------------------------------------


class TestCheckpoint:
    def test_next_step_index_no_completed(self):
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=["a", "b"], completed=[])
        assert c.next_step_index == 0

    def test_next_step_index_with_completed(self):
        completed = [CompletedStep(index=1, text="a", status="done")]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=["a", "b"], completed=completed)
        assert c.next_step_index == 1

    def test_remaining_steps(self):
        steps = ["a", "b", "c"]
        completed = [CompletedStep(index=1, text="a", status="done")]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=steps, completed=completed)
        assert c.remaining_steps == ["b", "c"]

    def test_remaining_steps_all_done(self):
        steps = ["a", "b"]
        completed = [
            CompletedStep(index=1, text="a", status="done"),
            CompletedStep(index=2, text="b", status="done"),
        ]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=steps, completed=completed)
        assert c.remaining_steps == []

    def test_is_complete_false(self):
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=["a", "b"],
                       completed=[CompletedStep(index=1, text="a", status="done")])
        assert not c.is_complete()

    def test_is_complete_true(self):
        steps = ["a", "b"]
        completed = [
            CompletedStep(index=1, text="a", status="done"),
            CompletedStep(index=2, text="b", status="done"),
        ]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=steps, completed=completed)
        assert c.is_complete()

    def test_timestamp_auto_set(self):
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=[], completed=[])
        assert c.timestamp  # non-empty

    def test_to_dict_roundtrip(self):
        completed = [CompletedStep(index=1, text="a", status="done", result="ok")]
        c = Checkpoint(loop_id="abc", goal="goal text", project="proj", steps=["a", "b"], completed=completed)
        d = c.to_dict()
        c2 = Checkpoint.from_dict(d)
        assert c2.loop_id == "abc"
        assert c2.goal == "goal text"
        assert c2.steps == ["a", "b"]
        assert len(c2.completed) == 1
        assert c2.completed[0].text == "a"


# ---------------------------------------------------------------------------
# write_checkpoint / load_checkpoint
# ---------------------------------------------------------------------------


class TestWriteAndLoad:
    def test_write_creates_file(self, tmp_path):
        outcomes = [_FakeStepOutcome(index=1, text="step 1", status="done")]
        write_checkpoint("loop1", "test goal", "proj", ["step 1", "step 2"], outcomes)
        assert (tmp_path / "ckpt_loop1.json").exists()

    def test_load_returns_checkpoint(self, tmp_path):
        outcomes = [_FakeStepOutcome(index=1, text="step 1", status="done", result="res")]
        write_checkpoint("loop2", "goal", "proj", ["step 1", "step 2"], outcomes)
        c = load_checkpoint("loop2")
        assert c is not None
        assert c.loop_id == "loop2"
        assert c.goal == "goal"
        assert len(c.completed) == 1

    def test_load_missing_returns_none(self, tmp_path):
        assert load_checkpoint("nonexistent") is None

    def test_write_overwrites_previous(self, tmp_path):
        outcomes1 = [_FakeStepOutcome(index=1, text="step 1", status="done")]
        write_checkpoint("loopX", "goal", "proj", ["step 1", "step 2"], outcomes1)

        outcomes2 = [
            _FakeStepOutcome(index=1, text="step 1", status="done"),
            _FakeStepOutcome(index=2, text="step 2", status="done"),
        ]
        write_checkpoint("loopX", "goal", "proj", ["step 1", "step 2"], outcomes2)

        c = load_checkpoint("loopX")
        assert len(c.completed) == 2

    def test_write_swallows_errors(self):
        # Should not raise even with bad inputs
        write_checkpoint("lid", "g", "p", ["s1"], [object()])  # non-StepOutcome object

    def test_load_corrupt_returns_none(self, tmp_path):
        (tmp_path / "ckpt_bad.json").write_text("not json", encoding="utf-8")
        assert load_checkpoint("bad") is None

    def test_completed_fields_preserved(self, tmp_path):
        outcomes = [_FakeStepOutcome(index=1, text="t", status="done", result="r", tokens_in=5, elapsed_ms=100)]
        write_checkpoint("lid", "g", "p", ["t", "u"], outcomes)
        c = load_checkpoint("lid")
        cs = c.completed[0]
        assert cs.result == "r"
        assert cs.tokens_in == 5
        assert cs.elapsed_ms == 100


# ---------------------------------------------------------------------------
# delete_checkpoint / list_checkpoints
# ---------------------------------------------------------------------------


class TestDeleteAndList:
    def test_delete_removes_file(self, tmp_path):
        outcomes = [_FakeStepOutcome(index=1, text="s", status="done")]
        write_checkpoint("del1", "g", "p", ["s"], outcomes)
        assert (tmp_path / "ckpt_del1.json").exists()
        delete_checkpoint("del1")
        assert not (tmp_path / "ckpt_del1.json").exists()

    def test_delete_nonexistent_is_safe(self):
        delete_checkpoint("does-not-exist")  # should not raise

    def test_list_returns_checkpoints(self, tmp_path):
        for lid in ["l1", "l2"]:
            write_checkpoint(lid, "g", "p", ["s"], [_FakeStepOutcome(index=1, text="s", status="done")])
        ckpts = list_checkpoints()
        ids = {c.loop_id for c in ckpts}
        assert "l1" in ids
        assert "l2" in ids

    def test_list_empty_dir(self, tmp_path):
        assert list_checkpoints() == []


# ---------------------------------------------------------------------------
# resume_from
# ---------------------------------------------------------------------------


class TestResumeFrom:
    def test_remaining_and_completed(self, tmp_path):
        steps = ["a", "b", "c", "d"]
        completed = [
            CompletedStep(index=1, text="a", status="done"),
            CompletedStep(index=2, text="b", status="done"),
        ]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=steps, completed=completed)
        remaining, done = resume_from(c)
        assert remaining == ["c", "d"]
        assert len(done) == 2
        assert done[0].text == "a"

    def test_all_complete_no_remaining(self):
        steps = ["a", "b"]
        completed = [
            CompletedStep(index=1, text="a", status="done"),
            CompletedStep(index=2, text="b", status="done"),
        ]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=steps, completed=completed)
        remaining, done = resume_from(c)
        assert remaining == []
        assert len(done) == 2

    def test_none_complete_all_remaining(self):
        steps = ["a", "b", "c"]
        c = Checkpoint(loop_id="x", goal="g", project="p", steps=steps, completed=[])
        remaining, done = resume_from(c)
        assert remaining == ["a", "b", "c"]
        assert done == []
