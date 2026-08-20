"""Tests for jsonl_utils.py — the shared JSONL-tail reader (Tier 2 consolidation)."""

import json
import logging
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonl_utils
from jsonl_utils import read_jsonl_tail, read_jsonl_tail_counted


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


# --- The read reports what it threw away (tier-2 silent-drop sweep) ------
#
# Dropping a corrupt line is right; dropping it *silently* is the defect. A
# caller that gets 40 records back must be able to tell "the store holds 40"
# from "the store holds 41 and one was unreadable". These pin that the drop
# is COUNTED (returned as data) and ANNOUNCED (logged for an operator).


class TestTheSkipReportCounts:

    def test_a_clean_read_reports_no_loss(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text('{"n": 1}\n{"n": 2}\n')
        records, report = read_jsonl_tail_counted(path)
        assert records == [{"n": 1}, {"n": 2}]
        assert report.dropped == 0
        assert not report
        assert not report.partial

    def test_each_drop_reason_is_counted_separately(self, tmp_path):
        # Separately, not as one lump: "3 malformed" and "3 undecodable"
        # mean different things to whoever has to go look at the file.
        path = tmp_path / "log.jsonl"
        path.write_bytes(
            b'{"n": 1}\n'
            b'nope\nalso nope\n'          # 2 malformed
            b'\xff\xfe torn\n'            # 1 undecodable
            b'[1, 2]\n"str"\n42\n'        # 3 non-dict
            b'{"n": 2}\n'
        )
        records, report = read_jsonl_tail_counted(path)
        assert records == [{"n": 1}, {"n": 2}]
        assert (report.malformed, report.undecodable, report.non_dict) == (2, 1, 3)
        assert report.dropped == 6
        assert report

    def test_blank_lines_are_not_counted_as_loss(self, tmp_path):
        # A trailing newline must not read as a corrupt ledger, or every
        # well-formed file on the box reports a drop.
        path = tmp_path / "log.jsonl"
        path.write_text('{"n": 1}\n\n   \n\n{"n": 2}\n')
        records, report = read_jsonl_tail_counted(path)
        assert records == [{"n": 1}, {"n": 2}]
        assert report.dropped == 0
        assert not report

    def test_a_missing_file_is_absence_not_loss(self, tmp_path):
        records, report = read_jsonl_tail_counted(tmp_path / "nope.jsonl")
        assert records == []
        assert report.missing
        assert report.dropped == 0
        assert not report, "no file yet is 'no data', not 'data lost'"

    def test_an_unreadable_file_is_loss_even_with_nothing_to_count(self, tmp_path):
        # The dangerous case: zero records and zero drops looks exactly like
        # an empty ledger unless `unreadable` makes the report truthy.
        path = tmp_path / "not_a_file"
        path.mkdir()
        records, report = read_jsonl_tail_counted(path)
        assert records == []
        assert report.unreadable
        assert report, "an unopenable store must not read as an empty one"
        assert "not an empty ledger" in report.summary()

    def test_the_tail_path_counts_what_it_scanned_and_says_so(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text(
            '{"n": 1}\nbad-early\n{"n": 2}\nbad-late\n{"n": 3}\n'
        )
        records, report = read_jsonl_tail_counted(path, limit=2)
        assert records == [{"n": 2}, {"n": 3}]
        assert report.malformed == 1, "only the drop inside the scanned tail"
        assert report.partial, "stopped at the limit — counts are a lower bound"
        assert "in the scanned tail" in report.summary()

    def test_a_tail_read_that_reaches_the_top_is_not_partial(self, tmp_path):
        # limit larger than the file: the scan saw everything, so the counts
        # are a whole-file total and must not be hedged as a lower bound.
        path = tmp_path / "log.jsonl"
        path.write_text('{"n": 1}\nbad\n{"n": 2}\n')
        _, report = read_jsonl_tail_counted(path, limit=100)
        assert report.malformed == 1
        assert not report.partial
        assert "in the scanned tail" not in report.summary()

    def test_the_summary_names_every_nonzero_reason_and_no_zero_ones(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text('{"n": 1}\nbad\n[1]\n')
        _, report = read_jsonl_tail_counted(path)
        summary = report.summary()
        assert "1 malformed" in summary
        assert "1 non-dict" in summary
        assert "undecodable" not in summary, "don't list reasons that didn't happen"
        assert "2" in summary

    def test_counted_and_plain_return_the_same_records(self, tmp_path):
        # The two entry points must never drift into different readers.
        path = tmp_path / "log.jsonl"
        path.write_bytes(b'{"n": 1}\nbad\n\xff\n[1]\n\n{"n": 2}\n{"n": 3}\n')
        for limit in (None, 0, 1, 2, 100):
            assert read_jsonl_tail(path, limit=limit) == \
                read_jsonl_tail_counted(path, limit=limit)[0], f"limit={limit}"


class TestTheDropIsAnnounced:

    def test_a_dropped_line_is_logged_with_the_path(self, tmp_path, caplog):
        path = tmp_path / "log.jsonl"
        path.write_text('{"n": 1}\nnot json\n')
        with caplog.at_level(logging.WARNING, logger="jsonl_utils"):
            assert read_jsonl_tail(path) == [{"n": 1}]
        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert str(path) in message, "an operator has to know WHICH store"
        assert "1 malformed" in message

    def test_a_clean_read_is_silent(self, tmp_path, caplog):
        # A warning on every healthy read is a warning nobody reads.
        path = tmp_path / "log.jsonl"
        path.write_text('{"n": 1}\n\n{"n": 2}\n')
        with caplog.at_level(logging.WARNING, logger="jsonl_utils"):
            read_jsonl_tail(path)
        assert caplog.records == []

    def test_a_missing_file_is_silent(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="jsonl_utils"):
            read_jsonl_tail(tmp_path / "nope.jsonl")
        assert caplog.records == []

    def test_an_unreadable_file_is_logged(self, tmp_path, caplog):
        path = tmp_path / "not_a_file"
        path.mkdir()
        with caplog.at_level(logging.WARNING, logger="jsonl_utils"):
            assert read_jsonl_tail(path) == []
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING


class TestLoadsCleanRefusesTheTwoShapesR5Found:
    """`loads_clean` refuses byte TAINT so a rewrite cannot launder it into a
    clean escape. Adversarial r5 (2026-08-20, Failure Operator, probed) found
    two more ways a line can be structurally valid JSON and still be a row we
    must not act on."""

    def test_a_surrogate_written_as_an_escape_is_taint_too(self):
        """`{"tier": "\\udcff"}` is pure ASCII on disk — valid UTF-8, so the
        raw-line scan cannot see it — and parses to exactly the string a torn
        byte produces. It reached the store's validators, and only the fields
        that get hashed were caught."""
        from jsonl_utils import loads_clean

        line = json.dumps({"tier": "x"}).replace('"x"', '"\\udcff"')
        with pytest.raises(json.JSONDecodeError):
            loads_clean(line)

    @pytest.mark.parametrize("line", [
        '{"a": "\\udcff"}',                      # value
        '{"a": ["ok", "\\udcff"]}',              # inside a list
        '{"a": {"b": "\\udcff"}}',               # nested object
        '{"\\udcff": "a"}',                      # the KEY
        '{"a": "\\uDCFF"}',                      # uppercase hex
    ])
    def test_it_finds_the_surrogate_wherever_it_hides(self, line):
        from jsonl_utils import loads_clean

        with pytest.raises(json.JSONDecodeError):
            loads_clean(line)

    def test_duplicate_names_are_a_corrupt_row_not_a_choice(self):
        """json.loads silently keeps the LAST value, so
        `{"applied": false, "applied": true}` read as already-applied and a
        STOP interrupt was swallowed with no warning. Two values and no rule
        saying which is not something this layer may decide."""
        from jsonl_utils import loads_clean

        with pytest.raises(json.JSONDecodeError):
            loads_clean('{"id": "s", "applied": false, "applied": true}')
        with pytest.raises(json.JSONDecodeError):
            loads_clean('{"a": {"z": 1, "z": 2}}')      # nested

    @pytest.mark.parametrize("line", [
        '{"a": 1, "b": ["x"], "c": {"d": null}}',
        '{"a": "caf\\u00e9 \\u2028 line sep"}',   # escapes that are NOT surrogates
        '{"a": "\\ud83d\\ude00"}',                # a PAIR: a real emoji, not taint
        '[]', 'null', '"x"', '3',
    ])
    def test_healthy_lines_still_parse(self, line):
        """The negative control. A refusal that fires on everything is an
        outage, and `\\ud83d\\ude00` is the shape that proves the check is
        about LONE surrogates: json.loads has already joined the pair."""
        from jsonl_utils import loads_clean

        loads_clean(line)


class TestTheTaintCheckCannotBeTheThingThatBreaks:
    """Adversarial r6 (2026-08-20, 2 lenses, probed) on r5's own fix. A shared
    helper with 84 call sites does not get to raise something its callers do
    not catch — every one of them strands a bad row via `except
    (json.JSONDecodeError, TypeError)`, and RecursionError is neither."""

    def test_deep_nesting_that_json_accepts_is_not_a_crash(self):
        from jsonl_utils import loads_clean

        # ~600 deep: json.loads parses it, the recursive walk blew the stack,
        # and InterruptQueue.poll() died before it could announce anything.
        line = "[" * 600 + '"\\u000a"' + "]" * 600
        json.loads(line)                      # the premise, stated out loud
        loads_clean(line)

    def test_it_still_finds_a_surrogate_buried_deep(self):
        """The must-detect half: iterating instead of recursing must not
        make the check shallower."""
        from jsonl_utils import loads_clean

        line = "[" * 400 + '"\\udcff"' + "]" * 400
        with pytest.raises(json.JSONDecodeError):
            loads_clean(line)

    @pytest.mark.parametrize("raw", ["\ud800", "\udbff", "\udc80", "\udfff"])
    def test_the_raw_scan_covers_the_whole_surrogate_block(self, raw):
        """r5 scanned only U+DC80–U+DCFF, the range surrogateescape produces.
        A lone HIGH surrogate reaching this helper from anywhere else was
        admitted and re-dumped as a clean-looking escape (r6, Architect)."""
        from jsonl_utils import loads_clean

        with pytest.raises(json.JSONDecodeError):
            loads_clean('{"x": "' + raw + '"}')
