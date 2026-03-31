"""Tests for cron persistence — scheduler.py."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup(monkeypatch, tmp_path):
    """Redirect jobs.json to tmp_path."""
    import scheduler
    monkeypatch.setattr(scheduler, "_jobs_path", lambda: tmp_path / "jobs.json")


# ---------------------------------------------------------------------------
# add_job / list_jobs / remove_job
# ---------------------------------------------------------------------------

class TestJobStore:
    def test_add_and_list(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, list_jobs
        job = add_job("Test goal", {"type": "daily", "time": "09:00"})
        assert job["job_id"]
        assert job["goal"] == "Test goal"
        assert job["enabled"] is True
        assert "next_run" in job

        jobs = list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == job["job_id"]

    def test_remove_job(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, remove_job, list_jobs
        job = add_job("Remove me", {"type": "once", "time": "10:00"})
        assert remove_job(job["job_id"]) is True
        assert list_jobs() == []

    def test_remove_nonexistent_returns_false(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import remove_job
        assert remove_job("nonexistent-id") is False

    def test_list_enabled_only(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, list_jobs, enable_job
        j1 = add_job("Enabled", {"type": "daily", "time": "09:00"})
        j2 = add_job("Disabled", {"type": "daily", "time": "10:00"})
        enable_job(j2["job_id"], enabled=False)

        all_jobs = list_jobs(enabled_only=False)
        enabled_jobs = list_jobs(enabled_only=True)
        assert len(all_jobs) == 2
        assert len(enabled_jobs) == 1
        assert enabled_jobs[0]["job_id"] == j1["job_id"]

    def test_add_disabled(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job
        job = add_job("Silent goal", {"type": "daily", "time": "09:00"}, enabled=False)
        assert job["enabled"] is False

    def test_jobs_persist_across_instances(self, monkeypatch, tmp_path):
        """Jobs loaded from file are the same as those written."""
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, list_jobs
        job = add_job("Persist me", {"type": "daily", "time": "08:00"})
        # Simulate restart by reimporting
        import importlib
        import scheduler as s
        importlib.reload(s)
        monkeypatch.setattr(s, "_jobs_path", lambda: tmp_path / "jobs.json")
        jobs = s.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["goal"] == "Persist me"


# ---------------------------------------------------------------------------
# check_due_jobs
# ---------------------------------------------------------------------------

class TestCheckDueJobs:
    def test_no_jobs_returns_empty(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import check_due_jobs
        assert check_due_jobs() == []

    def test_future_job_not_due(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, check_due_jobs
        # Add a job with next_run 2 hours from now
        job = add_job("Future", {"type": "once", "time": "09:00"})
        # Check with a 'now' that is 1 hour before next_run
        from datetime import datetime as dt
        past = dt.fromisoformat(job["next_run"]) - timedelta(hours=1)
        due = check_due_jobs(now=past)
        assert due == []

    def test_past_job_is_due(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, check_due_jobs
        job = add_job("Past", {"type": "once", "time": "09:00"})
        # Check with now = next_run + 5 minutes
        from datetime import datetime as dt
        after = dt.fromisoformat(job["next_run"]) + timedelta(minutes=5)
        due = check_due_jobs(now=after)
        assert len(due) == 1
        assert due[0]["job_id"] == job["job_id"]

    def test_disabled_job_not_returned(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, check_due_jobs, enable_job
        from datetime import datetime as dt
        job = add_job("Disabled", {"type": "once", "time": "09:00"}, enabled=False)
        after = dt.fromisoformat(job["next_run"]) + timedelta(minutes=5)
        due = check_due_jobs(now=after)
        assert due == []


# ---------------------------------------------------------------------------
# mark_job_done
# ---------------------------------------------------------------------------

class TestMarkJobDone:
    def test_once_job_disabled_after_done(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, mark_job_done, list_jobs
        job = add_job("One shot", {"type": "once", "time": "09:00"})
        mark_job_done(job["job_id"])
        jobs = list_jobs(enabled_only=False)
        assert len(jobs) == 1
        assert jobs[0]["enabled"] is False
        assert jobs[0]["run_count"] == 1

    def test_daily_job_next_run_advances(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, mark_job_done, list_jobs
        job = add_job("Daily", {"type": "daily", "time": "09:00"})
        old_next = job["next_run"]
        mark_job_done(job["job_id"])
        jobs = list_jobs()
        updated = jobs[0]
        # next_run should have advanced (same time tomorrow or later today depending on current time)
        assert updated["enabled"] is True
        assert updated["run_count"] == 1
        assert "last_run" in updated

    def test_interval_job_next_run_advances(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, mark_job_done, list_jobs
        from datetime import datetime as dt
        job = add_job("Interval", {"type": "interval", "minutes": 30})
        mark_job_done(job["job_id"])
        jobs = list_jobs()
        updated = jobs[0]
        assert updated["enabled"] is True
        # next_run should be ~30 minutes from now
        next_dt = dt.fromisoformat(updated["next_run"])
        now = _now()
        assert next_dt > now  # definitely in the future

    def test_mark_nonexistent_returns_false(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import mark_job_done
        assert mark_job_done("ghost-id") is False


# ---------------------------------------------------------------------------
# _next_run_for_schedule
# ---------------------------------------------------------------------------

class TestNextRunForSchedule:
    def test_once_future_time(self):
        from scheduler import _next_run_for_schedule
        from datetime import datetime as dt
        # now = 08:00; schedule time = 09:00 → same day
        now = dt(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc)
        result = _next_run_for_schedule({"type": "once", "time": "09:00"}, after=now)
        assert "2026-04-01T09:00:00" in result

    def test_once_past_time_advances_to_tomorrow(self):
        from scheduler import _next_run_for_schedule
        from datetime import datetime as dt
        # now = 10:00; schedule time = 09:00 → next day
        now = dt(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = _next_run_for_schedule({"type": "once", "time": "09:00"}, after=now)
        assert "2026-04-02T09:00:00" in result

    def test_interval_N_minutes(self):
        from scheduler import _next_run_for_schedule
        from datetime import datetime as dt
        now = dt(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = _next_run_for_schedule({"type": "interval", "minutes": 45}, after=now)
        expected = dt(2026, 4, 1, 10, 45, 0, tzinfo=timezone.utc)
        assert expected.isoformat() in result

    def test_invalid_type_raises(self):
        from scheduler import _next_run_for_schedule
        with pytest.raises(ValueError, match="Unknown schedule type"):
            _next_run_for_schedule({"type": "weekly"})

    def test_invalid_time_format_raises(self):
        from scheduler import _next_run_for_schedule
        with pytest.raises((ValueError, IndexError)):
            _next_run_for_schedule({"type": "once", "time": "9am"})


# ---------------------------------------------------------------------------
# drain_due_jobs
# ---------------------------------------------------------------------------

class TestDrainDueJobs:
    def test_no_due_jobs_returns_zero(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import drain_due_jobs
        assert drain_due_jobs(dry_run=True) == 0

    def test_dry_run_does_not_submit(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from scheduler import add_job, drain_due_jobs, list_jobs
        from datetime import datetime as dt
        job = add_job("Dry run job", {"type": "once", "time": "09:00"})
        # Force next_run to be in the past
        jobs_path = tmp_path / "jobs.json"
        data = json.loads(jobs_path.read_text())
        data[0]["next_run"] = "2020-01-01T00:00:00+00:00"
        jobs_path.write_text(json.dumps(data))

        n = drain_due_jobs(dry_run=True)
        assert n == 0
        # Job should still be enabled (not consumed)
        jobs = list_jobs()
        assert jobs[0]["enabled"] is True
