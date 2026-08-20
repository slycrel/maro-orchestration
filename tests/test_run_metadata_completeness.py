"""Run metadata must answer the questions people actually ask of a run dir.

Each test here pins a field whose ABSENCE previously forced a guess. Measured
before the 2026-08-18 completeness pass: 85/788 runs recorded an entry point
(and all 85 said `user_goal`, the queue lane, not the CLI); no step recorded
when it started, which model ran it, or whether it ran containerized; and only
60% of captain's-log rows could be tied to a run at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# --------------------------------------------------------------- captain's log

def test_log_rows_carry_the_run_they_belong_to():
    """The log is one global JSONL sliced per run by byte offset, so without an
    id on the row a slice cannot be filtered -- only guessed at by timestamp."""
    import runs
    from captains_log import log_event
    runs.open_run("aa112233", prompt="p")
    row = log_event("SCOPE_GENERATED", subject="s", summary="m", context={})
    assert row["handle_id"] == "aa112233"
    assert row["timestamp"]


def test_log_row_outside_a_run_is_not_falsely_attributed():
    from captains_log import log_event
    row = log_event("MEMORY_CONSOLIDATED", subject="s", summary="m")
    assert "handle_id" not in row


# ------------------------------------------------------------------- venue

def test_executor_call_records_where_it_actually_ran():
    """Config records container INTENT; mode 'on' still degrades to the host
    when docker is down. Only the per-call outcome answers 'was this isolated'."""
    import container_exec as ce
    ce.reset_venue()
    ce.resolve_container_run(False, True)
    assert ce.last_venue() in ("host", "refused") or ce.last_venue().startswith("container:")


def test_non_executor_call_leaves_no_venue():
    """Maro's own reasoning calls are never containerized; claiming a venue for
    them would inflate any containerization count."""
    import container_exec as ce
    ce.reset_venue()
    ce.resolve_container_run(False, False)
    assert ce.last_venue() == ""


def test_venue_is_reset_between_steps():
    """A step that makes no executor call must not inherit the previous step's
    venue -- that would silently attribute isolation it never had."""
    import container_exec as ce
    ce.reset_venue()
    ce.resolve_container_run(False, True)
    assert ce.last_venue()
    ce.reset_venue()
    assert ce.last_venue() == ""


# ------------------------------------------------------------- step records

def test_step_outcome_carries_the_origin_story_fields():
    from loop_types import step_from_decompose
    s = step_from_decompose(
        "do a thing", 3, status="done", started_ts="2026-08-18T00:00:00+00:00",
        model="sonnet", model_tier="mid", tier_escalated_from="cheap",
        venue="container:maro-exec-abc")
    assert s.started_ts and s.ended_ts          # both ends of the interval
    assert s.model == "sonnet" and s.model_tier == "mid"
    assert s.tier_escalated_from == "cheap"     # the retry ladder is measurable
    assert s.venue.startswith("container:")


def test_loop_log_persists_the_new_step_fields():
    """A field on the dataclass that never reaches the run dir is not evidence."""
    import inspect
    import loop_artifacts
    src = inspect.getsource(loop_artifacts._write_loop_log)
    for field in ("started_ts", "venue", "model", "model_tier",
                  "tier_escalated_from"):
        assert f'"{field}"' in src, f"{field} never reaches the loop log"
    for total in ("steps_containerized", "steps_on_host", "steps_tier_escalated"):
        assert f'"{total}"' in src, f"{total} missing from totals"


def test_a_real_run_records_both_ends_of_every_step():
    import runs
    from agent_loop import run_agent_loop
    runs.open_run("bb223344", prompt="simple goal")
    run_agent_loop("simple goal", project="meta-test", dry_run=True,
                   verbose=False, handle_id="bb223344")
    logs = sorted((runs.run_dir("bb223344") / "build").glob("loop-*-log.json"))
    assert logs, "no loop log written"
    steps = json.loads(logs[0].read_text())["steps"]
    assert steps
    for s in steps:
        assert s["started_ts"], "step has no start -- timeline degrades to an estimate"
        assert s["ended_ts"]
        assert s["started_ts"] <= s["ended_ts"]


# ------------------------------------------------------------------ provenance

@pytest.mark.parametrize("module,attr,expected", [
    ("telegram_listener", "handle", "telegram"),
    ("slack_listener", "handle", "slack"),
])
def test_listener_lanes_name_themselves(module, attr, expected):
    """These lanes passed no origin at all, so a dispatched run looked
    hand-typed forever after."""
    import importlib, inspect
    src = inspect.getsource(importlib.import_module(module))
    assert f'"source": "{expected}"' in src


def test_scheduler_names_itself_and_its_job():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler)
    assert '"source": "scheduler"' in src
    assert '"job_id"' in src


def test_atlas_entry_map_does_not_default_unknown_sources_to_cli():
    """The old fallback rendered every origin-carrying run as a CLI invocation
    when all of them were queue-drained `user_goal` runs."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "scripts" / "run_atlas" / "extract_paths.py").read_text()
    assert '"user_goal": "intake.queue"' in src
    assert '.get(osrc, "intake.cli")' not in src
