"""Tests for SlowUpdateScheduler state machine."""

import time
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from slow_update_scheduler import SlowUpdateScheduler, State


def make_sched(cooldown: float = 0.05) -> SlowUpdateScheduler:
    return SlowUpdateScheduler(idle_cooldown=cooldown)


class TestIdleWaitToWindowOpen:
    def test_stays_idle_while_busy(self):
        s = make_sched()
        s.tick(is_busy=True)
        assert s.state == State.IDLE_WAIT

    def test_stays_idle_before_cooldown(self):
        s = make_sched(cooldown=10.0)
        s.tick(is_busy=False)
        assert s.state == State.IDLE_WAIT

    def test_opens_after_cooldown(self):
        s = make_sched(cooldown=0.01)
        s.tick(is_busy=False)
        time.sleep(0.02)
        s.tick(is_busy=False)
        assert s.state == State.WINDOW_OPEN

    def test_busy_resets_cooldown_timer(self):
        s = make_sched(cooldown=0.02)
        s.tick(is_busy=False)
        time.sleep(0.01)
        s.tick(is_busy=True)   # reset
        time.sleep(0.015)
        s.tick(is_busy=False)  # cooldown restarts from here
        assert s.state == State.IDLE_WAIT  # not enough time yet


class TestWindowOpenTransitions:
    def _open(self) -> SlowUpdateScheduler:
        s = make_sched(cooldown=0.01)
        s.tick(is_busy=False)
        time.sleep(0.02)
        s.tick(is_busy=False)
        assert s.state == State.WINDOW_OPEN
        return s

    def test_busy_closes_window(self):
        s = self._open()
        s.tick(is_busy=True)
        assert s.state == State.IDLE_WAIT

    def test_start_work_moves_to_updating(self):
        s = self._open()
        s.start_work()
        assert s.state == State.UPDATING

    def test_should_run_returns_true_when_open(self):
        s = self._open()
        assert s.should_run(is_busy=False) is True

    def test_should_run_returns_false_when_busy(self):
        s = make_sched(cooldown=0.01)
        assert s.should_run(is_busy=True) is False


class TestUpdatingTransitions:
    def _updating(self) -> SlowUpdateScheduler:
        s = make_sched(cooldown=0.01)
        s.tick(is_busy=False)
        time.sleep(0.02)
        s.tick(is_busy=False)
        s.start_work()
        assert s.state == State.UPDATING
        return s

    def test_busy_moves_to_pausing(self):
        s = self._updating()
        s.tick(is_busy=True)
        assert s.state == State.PAUSING

    def test_finish_work_returns_to_window_open(self):
        s = self._updating()
        s.finish_work(is_busy=False)
        assert s.state == State.WINDOW_OPEN

    def test_finish_work_busy_moves_to_idle_wait(self):
        s = self._updating()
        s.finish_work(is_busy=True)
        assert s.state == State.IDLE_WAIT

    def test_multiple_workers_tracked(self):
        s = self._updating()
        s.start_work()  # second worker
        assert s._active_workers == 2
        s.finish_work(is_busy=False)
        assert s.state == State.UPDATING  # one still running
        s.finish_work(is_busy=False)
        assert s.state == State.WINDOW_OPEN


class TestPausingTransitions:
    def _pausing(self) -> SlowUpdateScheduler:
        s = make_sched(cooldown=0.01)
        s.tick(is_busy=False)
        time.sleep(0.02)
        s.tick(is_busy=False)
        s.start_work()
        s.tick(is_busy=True)
        assert s.state == State.PAUSING
        return s

    def test_finish_work_returns_to_idle_wait(self):
        s = self._pausing()
        s.finish_work(is_busy=True)
        assert s.state == State.IDLE_WAIT

    def test_tick_does_not_change_state_while_worker_active(self):
        s = self._pausing()
        s.tick(is_busy=False)
        assert s.state == State.PAUSING  # worker still running


class TestStatus:
    def test_status_keys(self):
        s = make_sched()
        info = s.status()
        assert set(info.keys()) == {"state", "active_workers", "idle_since", "idle_cooldown"}

    def test_repr(self):
        s = make_sched()
        assert "IDLE_WAIT" in repr(s)
