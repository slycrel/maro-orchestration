"""Tests for jsonl_utils.py — the shared JSONL-tail reader (Tier 2 consolidation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonl_utils
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


def test_negative_limit_falls_back_to_full_scan(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n')
    assert read_jsonl_tail(path, limit=-1) == [{"n": 1}, {"n": 2}]


def test_unreadable_file_returns_empty(tmp_path):
    # A directory "exists" but can't be opened as a file — exercises the
    # OSError path for both the full-scan and bounded-tail branches.
    path = tmp_path / "not_a_file"
    path.mkdir()
    assert read_jsonl_tail(path) == []
    assert read_jsonl_tail(path, limit=5) == []


# --- Bounded backwards tail read (BACKLOG.md R6-D1) ---------------------
#
# _TAIL_CHUNK_BYTES is monkeypatched down to a few bytes in these tests so
# that small, readable fixtures still force many backwards chunk reads and
# exercise the boundary-crossing logic in _iter_lines_reverse.


def test_tail_read_across_many_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 32)
    path = tmp_path / "log.jsonl"
    path.write_text("\n".join(f'{{"n": {i}}}' for i in range(500)) + "\n")
    result = read_jsonl_tail(path, limit=7)
    assert result == [{"n": i} for i in range(493, 500)]


def test_tail_read_handles_line_longer_than_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 16)
    path = tmp_path / "log.jsonl"
    long_value = "x" * 200  # far longer than the 16-byte chunk size
    path.write_text(
        '{"n": 1}\n'
        f'{{"n": 2, "big": "{long_value}"}}\n'
        '{"n": 3}\n'
    )
    result = read_jsonl_tail(path, limit=3)
    assert result == [
        {"n": 1},
        {"n": 2, "big": long_value},
        {"n": 3},
    ]


def test_tail_read_no_trailing_newline(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 8)
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n{"n": 3}')  # no trailing newline
    assert read_jsonl_tail(path, limit=2) == [{"n": 2}, {"n": 3}]
    assert read_jsonl_tail(path, limit=1) == [{"n": 3}]
    assert read_jsonl_tail(path) == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_tail_read_skips_bad_lines_near_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 16)
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"n": 1}\n'
        '{"n": 2}\n'
        'not json\n'
        '\n'
        '[1, 2, 3]\n'
        '{"n": 3}\n'
        '   \n'
        '{"n": 4}\n'
    )
    result = read_jsonl_tail(path, limit=3)
    assert result == [{"n": 2}, {"n": 3}, {"n": 4}]


def test_tail_read_limit_larger_than_file_with_tiny_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 10)
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n{"n": 3}\n')
    assert read_jsonl_tail(path, limit=100) == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_tail_read_multibyte_utf8_survives_tiny_chunk_boundaries(tmp_path, monkeypatch):
    # Chunk size of 3 bytes guarantees several chunk boundaries fall inside
    # the multi-byte UTF-8 sequences below (e.g. the 4-byte emoji). Splitting
    # only ever happens on b"\n", so a line's bytes are always reassembled
    # whole before decoding — this proves that holds.
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 3)
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"n": 1, "s": "héllo"}\n'
        '{"n": 2, "s": "wörld 🎉"}\n'
        '{"n": 3, "s": "café ünïcödé"}\n',
        encoding="utf-8",
    )
    result = read_jsonl_tail(path, limit=3)
    assert result == [
        {"n": 1, "s": "héllo"},
        {"n": 2, "s": "wörld 🎉"},
        {"n": 3, "s": "café ünïcödé"},
    ]


def test_undecodable_line_skipped_in_full_scan(tmp_path):
    # limit=None: a bad-bytes line (crash-torn append) is skipped
    # individually — it must never black out the rest of the ledger. The
    # old implementation let UnicodeDecodeError from read_text() escape
    # uncaught; an intermediate version of this change returned [] for the
    # whole file (adversarial review 2026-07-15: one torn multi-byte
    # append would silently empty every full-scan reader forever).
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"n": 1}\n\xff\xfe not utf8\n{"n": 2}\n')
    assert read_jsonl_tail(path) == [{"n": 1}, {"n": 2}]


def test_undecodable_line_skipped_in_tail_scan(tmp_path, monkeypatch):
    # limit=N: same per-line decode contract as the full scan.
    monkeypatch.setattr(jsonl_utils, "_TAIL_CHUNK_BYTES", 8)
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"n": 1}\n\xff\xfe garbage\n{"n": 2}\n')
    assert read_jsonl_tail(path, limit=5) == [{"n": 1}, {"n": 2}]
