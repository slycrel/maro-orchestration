"""Tests for workspace_import (maro-import) — merge semantics + idempotency."""

import json
from pathlib import Path

import pytest

from workspace_import import run_import


def _mk_workspace(root: Path, *, runs=(), ledger_rows=None, daily=None,
                  curated=False) -> Path:
    (root / "memory").mkdir(parents=True)
    (root / "runs").mkdir()
    for run_id in runs:
        d = root / "runs" / run_id
        (d / "source").mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps({"run_id": run_id}))
        (d / "source" / "prompt.txt").write_text("goal text")
        (d / "metadata.json.lock").write_text("")
    for rel, rows in (ledger_rows or {}).items():
        f = root / "memory" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    for name, content in (daily or {}).items():
        (root / "memory" / name).write_text(content)
    if curated:
        (root / "MEMORY.md").write_text("# curated memory\n")
        (root / "playbook.md").write_text("# playbook\n")
        (root / "skills").mkdir()
        (root / "skills" / "evolved.md").write_text("skill body")
    return root


def test_runs_copied_if_absent_with_provenance(tmp_path):
    src = _mk_workspace(tmp_path / "src", runs=["aaaa1111-new-run"])
    dst = _mk_workspace(tmp_path / "dst", runs=["bbbb2222-existing"])

    report = run_import(src, dst, "trial-x")

    assert report["runs"]["copied"] == ["aaaa1111-new-run"]
    copied = dst / "runs" / "aaaa1111-new-run"
    assert (copied / "metadata.json").exists()
    marker = json.loads((copied / "imported_from.json").read_text())
    assert marker["imported_from"] == "trial-x"
    # lock files never travel
    assert not (copied / "metadata.json.lock").exists()


def test_existing_runs_never_overwritten(tmp_path):
    src = _mk_workspace(tmp_path / "src", runs=["cccc3333-shared"])
    dst = _mk_workspace(tmp_path / "dst", runs=["cccc3333-shared"])
    (dst / "runs" / "cccc3333-shared" / "metadata.json").write_text(
        json.dumps({"run_id": "target-version"})
    )

    report = run_import(src, dst, "trial-x")

    assert report["runs"]["copied"] == []
    assert report["runs"]["skipped_existing"] == ["cccc3333-shared"]
    kept = json.loads(
        (dst / "runs" / "cccc3333-shared" / "metadata.json").read_text()
    )
    assert kept["run_id"] == "target-version"


def test_ledger_rows_appended_and_deduped(tmp_path):
    shared = {"ts": "2026-07-09T01:00:00", "event": "shared"}
    fresh = {"ts": "2026-07-09T02:00:00", "event": "fresh"}
    src = _mk_workspace(
        tmp_path / "src",
        ledger_rows={"events.jsonl": [shared, fresh],
                     "medium/change_log.jsonl": [fresh]},
    )
    dst = _mk_workspace(
        tmp_path / "dst", ledger_rows={"events.jsonl": [shared]}
    )

    report = run_import(src, dst, "trial-x")

    assert report["ledgers"]["events.jsonl"] == {"appended": 1, "duplicates": 1}
    lines = (dst / "memory" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    # nested ledger created at the right relative path
    nested = dst / "memory" / "medium" / "change_log.jsonl"
    assert json.loads(nested.read_text().splitlines()[0])["event"] == "fresh"


def test_import_is_idempotent(tmp_path):
    src = _mk_workspace(
        tmp_path / "src",
        runs=["dddd4444-run"],
        ledger_rows={"events.jsonl": [{"e": 1}, {"e": 2}]},
        daily={"2026-07-09.md": "did things\n"},
    )
    dst = _mk_workspace(tmp_path / "dst",
                        daily={"2026-07-09.md": "target day\n"})

    first = run_import(src, dst, "trial-x")
    second = run_import(src, dst, "trial-x")

    assert first["ledgers"]["events.jsonl"]["appended"] == 2
    assert second["ledgers"]["events.jsonl"]["appended"] == 0
    assert second["runs"]["copied"] == []
    assert second["daily_logs"]["already_imported"] == ["2026-07-09.md"]
    # daily content landed exactly once
    day = (dst / "memory" / "2026-07-09.md").read_text()
    assert day.count("did things") == 1
    assert day.startswith("target day")


def test_curated_files_quarantined_not_merged(tmp_path):
    src = _mk_workspace(tmp_path / "src", curated=True)
    dst = _mk_workspace(tmp_path / "dst")
    (dst / "MEMORY.md").write_text("# target curated\n")

    report = run_import(src, dst, "trial-x", include_curated=True)

    assert set(report["curated_quarantined"]) == {"MEMORY.md", "playbook.md", "skills"}
    # live curated file untouched
    assert (dst / "MEMORY.md").read_text() == "# target curated\n"
    q = dst / "imports" / "trial-x"
    assert (q / "MEMORY.md").exists()
    assert (q / "skills" / "evolved.md").exists()


def test_curated_skipped_by_default(tmp_path):
    src = _mk_workspace(tmp_path / "src", curated=True)
    dst = _mk_workspace(tmp_path / "dst")

    report = run_import(src, dst, "trial-x")

    assert "curated_quarantined" not in report
    assert not (dst / "imports").exists()


def test_audit_row_written(tmp_path):
    src = _mk_workspace(tmp_path / "src", runs=["eeee5555-run"])
    dst = _mk_workspace(tmp_path / "dst")

    run_import(src, dst, "trial-x")

    rows = [
        json.loads(ln)
        for ln in (dst / "memory" / "imports.jsonl").read_text().splitlines()
    ]
    assert rows[0]["label"] == "trial-x"
    assert rows[0]["runs"]["copied"] == ["eeee5555-run"]


def test_dry_run_writes_nothing(tmp_path):
    src = _mk_workspace(
        tmp_path / "src",
        runs=["ffff6666-run"],
        ledger_rows={"events.jsonl": [{"e": 1}]},
    )
    dst = _mk_workspace(tmp_path / "dst")

    report = run_import(src, dst, "trial-x", dry_run=True)

    assert report["runs"]["copied"] == ["ffff6666-run"]
    assert not (dst / "runs" / "ffff6666-run").exists()
    assert not (dst / "memory" / "events.jsonl").exists()
    assert not (dst / "memory" / "imports.jsonl").exists()


def test_same_workspace_rejected(tmp_path):
    ws = _mk_workspace(tmp_path / "ws")
    with pytest.raises(SystemExit):
        run_import(ws, ws, "self")


def test_non_workspace_rejected(tmp_path):
    src = _mk_workspace(tmp_path / "src")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        run_import(src, empty, "x")
