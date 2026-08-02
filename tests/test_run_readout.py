"""Pins for scripts/run_readout.py — the post-hoc cost + terrain readout.

This script exists because both numbers it prints were got wrong by hand.
Its own first hand-rolled version was wrong too (a narrower host extractor
than terrain's, which reported 0 avoidable retries on a run that had 23), so
the pins here are mostly about the readout agreeing with the system of
record rather than with a second derivation.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_SPEC = importlib.util.spec_from_file_location(
    "run_readout", Path(__file__).parent.parent / "scripts" / "run_readout.py")
run_readout = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_readout)


def _make_run(root, handle="abcd1234", steps=None, calls=None, card_extra=None):
    run_dir = root / f"{handle}-test-run"
    (run_dir / "build" / "calls").mkdir(parents=True)
    card = {"handle_id": handle, "total_cost_usd": 5.05, "goal_achieved": True}
    card.update(card_extra or {})
    (run_dir / "run_card.json").write_text(json.dumps(card))
    (run_dir / "build" / "loop-x-log.json").write_text(
        json.dumps({"steps": steps if steps is not None else []}))
    for i, call in enumerate(calls or [], start=1):
        (run_dir / "build" / "calls" / f"call-{i:05d}.json").write_text(json.dumps(call))
    return run_dir


def _fetch(url, output):
    return {"name": "WebFetch", "input": {"url": url}, "output": output}


class TestCost:
    def test_provider_cost_is_summed_from_steps(self, tmp_path):
        run = _make_run(tmp_path, steps=[{"provider_cost_usd": 1.5},
                                         {"provider_cost_usd": 2.25}])
        assert run_readout.read_run(run)["provider_cost_usd"] == 3.75

    def test_card_cost_is_read_not_recomputed(self, tmp_path):
        """The whole retraction: read the number the run recorded."""
        run = _make_run(tmp_path, card_extra={"total_cost_usd": 7.83})
        assert run_readout.read_run(run)["card_cost_usd"] == 7.83

    def test_missing_provider_cost_reports_none_not_zero(self, tmp_path):
        """A silent 0.00 would read as 'this run was free'."""
        run = _make_run(tmp_path, steps=[{"status": "ok"}])
        assert run_readout.read_run(run)["provider_cost_usd"] is None

    def test_naive_column_reproduces_the_retracted_method(self, tmp_path):
        """Kept deliberately: the gap is only visible if both are printed."""
        run = _make_run(tmp_path, calls=[{"tokens_in": 1_000_000, "tokens_out": 0}])
        row = run_readout.read_run(run)
        assert row["naive_cost_usd_RETRACTED"] == pytest.approx(3.0)
        assert row["naive_cost_usd_RETRACTED"] > (row["card_cost_usd"] or 0) / 2

    def test_not_a_run_dir_is_skipped(self, tmp_path):
        (tmp_path / "junk").mkdir()
        assert run_readout.read_run(tmp_path / "junk") is None


class TestTerrain:
    def test_agrees_with_the_terrain_module_on_hosts(self, tmp_path):
        """The readout must not re-derive block rules — it imports them."""
        run = _make_run(tmp_path, calls=[
            {"tool_events": [_fetch("https://a.example/1", "HTTP 403 Forbidden")]}])
        row = run_readout.read_run(run)
        assert row["blocked_hosts"]["a.example"]["reason"] == "403 forbidden"

    def test_avoidable_retry_counted_only_across_steps(self, tmp_path):
        """Terrain renders into LATER steps, so a same-step repeat was never
        avoidable — counting it would inflate the value of §5b."""
        run = _make_run(tmp_path, calls=[
            {"tool_events": [_fetch("https://a.example/1", "403"),
                             _fetch("https://a.example/2", "403")]},
            {"tool_events": [_fetch("https://a.example/3", "403")]}])
        row = run_readout.read_run(run)
        assert row["avoidable_retries"] == 1
        assert row["avoidable_detail"] == ["s2:a.example"]

    def test_host_extraction_matches_terrains_own(self, tmp_path):
        """The bug this file was written after: a narrower extractor here
        silently reported zero waste. Non-url-keyed inputs must still count."""
        run = _make_run(tmp_path, calls=[
            {"tool_events": [{"name": "Bash",
                              "input": {"command": "curl https://a.example/x"},
                              "output": "403 Forbidden"}]},
            {"tool_events": [{"name": "Bash",
                              "input": {"command": "curl -s https://a.example/y"},
                              "output": "403 Forbidden"}]}])
        row = run_readout.read_run(run)
        assert "a.example" in row["blocked_hosts"]
        assert row["avoidable_retries"] == 1

    def test_clean_run_reports_no_waste(self, tmp_path):
        run = _make_run(tmp_path, calls=[
            {"tool_events": [_fetch("https://ok.example/1", "HTTP 200 OK")]}])
        row = run_readout.read_run(run)
        assert row["blocked_hosts"] == {} and row["avoidable_retries"] == 0

    def test_promotable_needs_two_distinct_steps(self, tmp_path):
        run = _make_run(tmp_path, calls=[
            {"tool_events": [_fetch("https://a.example/1", "403")]},
            {"tool_events": [_fetch("https://a.example/2", "403")]}])
        assert run_readout.read_run(run)["promotable_hosts"] == ["a.example"]


class TestCLI:
    def test_handle_filter_and_json(self, tmp_path, capsys):
        _make_run(tmp_path, handle="aaaa1111")
        _make_run(tmp_path, handle="bbbb2222")
        rc = run_readout.main(["--runs-dir", str(tmp_path), "--json", "aaaa"])
        assert rc == 0
        rows = json.loads(capsys.readouterr().out)
        assert [r["handle_id"] for r in rows] == ["aaaa1111"]

    def test_missing_runs_dir_is_an_error_not_an_empty_table(self, tmp_path):
        assert run_readout.main(["--runs-dir", str(tmp_path / "nope")]) == 1
