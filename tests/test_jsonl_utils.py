"""Tests for jsonl_utils.py — the shared JSONL-tail reader (Tier 2 consolidation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jsonl_utils import read_jsonl_tail


def test_missing_file_returns_empty(tmp_path):
    assert read_jsonl_tail(tmp_path / "nope.jsonl") == []


def test_reads_all_records_in_order_when_no_limit(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n{"n": 3}\n')
    assert read_jsonl_tail(path) == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_limit_returns_last_n_in_chronological_order(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("\n".join(f'{{"n": {i}}}' for i in range(10)) + "\n")
    result = read_jsonl_tail(path, limit=3)
    assert result == [{"n": 7}, {"n": 8}, {"n": 9}]


def test_skips_malformed_lines_without_truncating(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\nnot json\n{"n": 3}\n')
    assert read_jsonl_tail(path) == [{"n": 1}, {"n": 3}]


def test_malformed_line_in_tail_scan_does_not_stop_early(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\nnot json\n{"n": 2}\n{"n": 3}\n')
    assert read_jsonl_tail(path, limit=2) == [{"n": 2}, {"n": 3}]


def test_skips_blank_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n\n  \n{"n": 2}\n')
    assert read_jsonl_tail(path) == [{"n": 1}, {"n": 2}]


def test_skips_non_dict_json_values(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n[1, 2, 3]\n"a string"\n42\n{"n": 2}\n')
    assert read_jsonl_tail(path) == [{"n": 1}, {"n": 2}]


def test_limit_larger_than_file_returns_everything(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n')
    assert read_jsonl_tail(path, limit=100) == [{"n": 1}, {"n": 2}]


def test_zero_limit_falls_back_to_full_scan(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n')
    assert read_jsonl_tail(path, limit=0) == [{"n": 1}, {"n": 2}]
