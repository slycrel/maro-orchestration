import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bootstrap_task import handle_placeholder
from orch import ensure_project
from orch_items import parse_next


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_define_success_criteria_creates_file_and_result(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    ensure_project("demo", "Fetch and summarize an article")
    result_path = tmp_path / "session-result.json"
    monkeypatch.setenv("ORCH_SESSION_RESULT_PATH", str(result_path))

    rc = handle_placeholder("demo", "Define success criteria")

    assert rc == 0
    criteria_path = tmp_path / "projects" / "demo" / "SUCCESS_CRITERIA.md"
    assert criteria_path.exists()
    payload = json.loads(_read(result_path))
    assert payload["status"] == "done"
    assert "success criteria" in payload["note"]


def test_create_first_pass_plan_adds_concrete_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    ensure_project("demo", "Fetch and summarize an article")
    result_path = tmp_path / "session-result.json"
    monkeypatch.setenv("ORCH_SESSION_RESULT_PATH", str(result_path))

    rc = handle_placeholder("demo", "Create first-pass plan")

    assert rc == 0
    _lines, items = parse_next("demo")
    texts = [item.text for item in items]
    assert "Gather inputs for: Fetch and summarize an article" in texts
    assert "Produce first artifact for: Fetch and summarize an article" in texts
    assert json.loads(_read(result_path))["status"] == "done"


def test_execute_next_leaf_task_unblocks_when_concrete_step_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    ensure_project("demo", "Fetch and summarize an article")
    result_path = tmp_path / "session-result.json"
    monkeypatch.setenv("ORCH_SESSION_RESULT_PATH", str(result_path))
    handle_placeholder("demo", "Create first-pass plan")

    rc = handle_placeholder("demo", "Execute next leaf task")

    assert rc == 0
    payload = json.loads(_read(result_path))
    assert payload["status"] == "done"
    assert "queued next leaf task" in payload["note"]


def test_gather_inputs_collects_urls_and_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    ensure_project("tweet-demo", "Fetch and summarize https://x.com/example/status/1234567890 — get the key takeaway")
    result_path = tmp_path / "session-result.json"
    monkeypatch.setenv("ORCH_SESSION_RESULT_PATH", str(result_path))
    handle_placeholder("tweet-demo", "Create first-pass plan")

    rc = handle_placeholder("tweet-demo", "Gather inputs for: Fetch and summarize https://x.com/example/status/1234567890")

    assert rc == 0
    inputs_path = tmp_path / "projects" / "tweet-demo" / "INPUTS.md"
    text = _read(inputs_path)
    assert "https://x.com/example/status/1234567890" in text
    assert "Gather inputs for:" in text


def test_bootstrap_artifact_and_review_steps_write_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    ensure_project("research-demo", "Research and write docs/research/productive_persistence.md using the format in docs/research/README.md")
    result_path = tmp_path / "session-result.json"
    monkeypatch.setenv("ORCH_SESSION_RESULT_PATH", str(result_path))

    assert handle_placeholder("research-demo", "Produce first artifact for: Research and write docs/research/productive_persistence.md") == 0
    assert (tmp_path / "projects" / "research-demo" / "FIRST_ARTIFACT.md").exists()

    assert handle_placeholder("research-demo", "Review results and capture next decisions for: Research and write docs/research/productive_persistence.md") == 0
    assert (tmp_path / "projects" / "research-demo" / "REVIEW.md").exists()
