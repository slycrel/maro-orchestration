import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import backend_benchmark as bb


def test_run_memory_backend_slice_returns_both_backends(tmp_path):
    payload = bb.run_memory_backend_slice(tmp_path, iterations=2, records=25)
    assert payload["slice"] == "memory-backend-lessons"
    assert payload["iterations"] == 2
    assert payload["records"] == 25
    ops = {row["operation"] for row in payload["summary"]}
    assert ops == {"append_lessons", "read_all_lessons"}
    assert len(payload["samples"]) == 8
    assert {sample["backend"] for sample in payload["samples"]} == {"jsonl", "sqlite"}


def test_run_filtered_lookup_slice_returns_query_metadata(tmp_path):
    payload = bb.run_memory_backend_filtered_lookup_slice(tmp_path, iterations=2, records=250, limit=5, min_confidence=0.8)
    assert payload["slice"] == "memory-backend-filtered-lookup"
    assert payload["iterations"] == 2
    assert payload["records"] == 250
    assert payload["query"]["limit"] == 5
    assert payload["query"]["expected_matches"] >= payload["query"]["expected_returned"]
    assert {row["operation"] for row in payload["summary"]} == {"filtered_lookup_lessons"}
    assert len(payload["samples"]) == 4
    assert {sample["backend"] for sample in payload["samples"]} == {"jsonl", "sqlite"}


def test_render_markdown_contains_summary_table(tmp_path):
    payload = bb.run_memory_backend_slice(tmp_path, iterations=1, records=10)
    report = bb.render_markdown(payload)
    assert "# Backend Benchmark Report" in report
    assert "`append_lessons`" in report
    assert "`read_all_lessons`" in report
    assert "Recommendation" in report


def test_render_markdown_contains_query_details_for_filtered_slice(tmp_path):
    payload = bb.run_memory_backend_filtered_lookup_slice(tmp_path, iterations=1, records=100, limit=5, min_confidence=0.8)
    report = bb.render_markdown(payload)
    assert "category=target" in report
    assert "retrieval-hot" in report
    assert "Matching records in seeded dataset" in report


def test_run_append_contention_slice_reports_integrity(tmp_path):
    payload = bb.run_memory_backend_append_contention_slice(tmp_path, workers=(2, 4), writes_per_worker=20)
    assert payload["slice"] == "memory-backend-append-contention"
    assert payload["workers"] == [2, 4]
    assert payload["writes_per_worker"] == 20
    assert {row["operation"] for row in payload["summary"]} == {"concurrent_append_contention"}
    assert len(payload["samples"]) == 4
    assert {sample["backend"] for sample in payload["samples"]} == {"jsonl", "sqlite"}
    assert len(payload["contention"]["jsonl"]) == 2
    assert len(payload["contention"]["sqlite"]) == 2
    for backend_name in ("jsonl", "sqlite"):
        for case in payload["contention"][backend_name]:
            assert case["attempted_writes"] == case["workers"] * case["writes_per_worker"]
            assert case["observed_successful_writes"] >= 0
            assert case["failed_writes"] >= 0


def test_render_markdown_contains_contention_table(tmp_path):
    payload = bb.run_memory_backend_append_contention_slice(tmp_path, workers=(2,), writes_per_worker=10)
    report = bb.render_markdown(payload)
    assert "Contention details" in report
    assert "corrupt rows" in report
    assert "`jsonl`" in report
    assert "`sqlite`" in report


def test_main_writes_default_artifacts(tmp_path):
    rc = bb.main(["--output-dir", str(tmp_path), "--iterations", "1", "--records", "10"])
    assert rc == 0
    report_path = tmp_path / "backend-benchmark-memory.md"
    json_path = tmp_path / "backend-benchmark-memory.json"
    assert report_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["slice"] == "memory-backend-lessons"


def test_main_writes_filtered_lookup_artifacts(tmp_path):
    rc = bb.main(
        [
            "--slice",
            "memory-backend-filtered-lookup",
            "--output-dir",
            str(tmp_path),
            "--iterations",
            "1",
            "--records",
            "100",
            "--limit",
            "5",
        ]
    )
    assert rc == 0
    report_path = tmp_path / "backend-benchmark-memory-filtered-lookup.md"
    json_path = tmp_path / "backend-benchmark-memory-filtered-lookup.json"
    assert report_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["slice"] == "memory-backend-filtered-lookup"


def test_main_writes_append_contention_artifacts(tmp_path):
    rc = bb.main(
        [
            "--slice",
            "memory-backend-append-contention",
            "--output-dir",
            str(tmp_path),
            "--workers",
            "2",
            "4",
            "--writes-per-worker",
            "10",
        ]
    )
    assert rc == 0
    report_path = tmp_path / "backend-benchmark-memory-append-contention.md"
    json_path = tmp_path / "backend-benchmark-memory-append-contention.json"
    assert report_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["slice"] == "memory-backend-append-contention"
