import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tool_cost_report as tcr


def test_percentile_nearest_rank_empty():
    assert tcr.percentile_nearest_rank([], 95) == 0


def test_percentile_nearest_rank_basic():
    assert tcr.percentile_nearest_rank([10, 20, 30, 40], 95) == 40
    assert tcr.percentile_nearest_rank([10, 20, 30, 40], 50) == 20


def test_infer_task_class_x_link():
    event = {"step_text_preview": "Research https://x.com/foo/status/1 and quoted thread", "goal_preview": "x post", "step_type": "research"}
    assert tcr.infer_task_class(event) == "x-link-research"


def test_infer_task_class_document_summary():
    event = {"step_text_preview": "Summarize the memo into four bullets", "goal_preview": "brief", "step_type": "summarize"}
    assert tcr.infer_task_class(event) == "document-summary"


def test_summarize_by_groups_and_medians():
    events = [
        {"step_type": "research", "status": "done", "model": "mid", "elapsed_ms": 2000, "total_tokens": 900, "cost_usd": 0.01, "step_text_preview": "Research X thread"},
        {"step_type": "research", "status": "done", "model": "mid", "elapsed_ms": 3000, "total_tokens": 1100, "cost_usd": 0.02, "step_text_preview": "Research X thread"},
        {"step_type": "verify", "status": "stuck", "model": "cheap", "elapsed_ms": 500, "total_tokens": 100, "cost_usd": 0.001, "step_text_preview": "Verify output"},
    ]
    rows = tcr.summarize_by(events, "step_type")
    assert rows[0].key == "research"
    assert rows[0].samples == 2
    assert rows[0].median_ms == 2500
    assert rows[0].p95_ms == 3000
    assert rows[0].total_cost_usd == 0.03


def test_run_fixture_benchmark_writes_metrics(tmp_path):
    fixtures_path = tmp_path / "fixtures.json"
    fixtures_path.write_text(json.dumps([
        {
            "task_class": "document-summary",
            "step_text": "Summarize document",
            "goal": "fixture summary",
            "model": "mid",
            "tokens_in": 400,
            "tokens_out": 100,
            "elapsed_ms": 1200,
            "status": "done",
        }
    ]))
    metrics_path, manifest = tcr.run_fixture_benchmark(fixtures_path, tmp_path / "out", repeats=2)
    assert metrics_path.exists()
    lines = [ln for ln in metrics_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert manifest["samples"] == 2


def test_main_writes_report_and_json(tmp_path):
    metrics_path = tmp_path / "step-costs.jsonl"
    metrics_path.write_text(
        json.dumps({
            "step_type": "research",
            "status": "done",
            "model": "mid",
            "elapsed_ms": 2000,
            "total_tokens": 900,
            "cost_usd": 0.01,
            "step_text_preview": "Research X thread",
            "goal_preview": "x goal",
        }) + "\n"
    )
    report_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"
    rc = tcr.main([
        "--metrics", str(metrics_path),
        "--write-report", str(report_path),
        "--write-json", str(json_path),
    ])
    assert rc == 0
    assert report_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["samples"] == 1
    assert "task" in payload["groups"]
