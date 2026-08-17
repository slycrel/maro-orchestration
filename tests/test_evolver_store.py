"""Byte-safety tests for evolver_store.py — the suggestions ledger.

Silent-drop arc, 2026-08-17 (evolver_store chunk). Probed live before the
fix: one crash-torn byte in suggestions.jsonl made load_suggestions return
[] and get_suggestion return None (family A — the V2 auto-revert authority
guard reads that None), made suggestion_is_applied / apply_suggestion /
revert_suggestion RAISE UnicodeDecodeError into callers (family B), and
blinded _save_suggestions' dedup scan so a re-derived identical suggestion
duplicated — the 81-duplicate calibration bug resurrected by one byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import evolver_store as ev

_TORN = b'{"torn": "\xff'


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))


def _row(sid="S1", **over):
    d = {"suggestion_id": sid, "category": "observation", "target": "all",
         "suggestion": f"suggestion body for {sid}", "confidence": 0.5,
         "failure_pattern": "fp", "outcomes_analyzed": 3}
    d.update(over)
    return d


def _seed(*rows, torn=True):
    p = ev._suggestions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
    if torn:
        body += _TORN + b"\n"
    p.write_bytes(body)
    return p


class TestTheSuggestionsLedgerSurvivesATornByte:
    def test_load_returns_healthy_rows_and_warns(self, caplog):
        import logging
        _seed(_row("S1"), _row("S2"))
        with caplog.at_level(logging.WARNING):
            got = ev.load_suggestions()
        assert {s.suggestion_id for s in got} == {"S1", "S2"}
        assert any("load_suggestions" in r.message and "undecodable" in r.message
                   for r in caplog.records)

    def test_get_finds_the_row_past_the_torn_line(self):
        _seed(_row("S1"))
        got = ev.get_suggestion("S1")
        assert got is not None and got.suggestion_id == "S1"

    def test_is_applied_reads_instead_of_raising(self):
        _seed(_row("S1", applied=True))
        assert ev.suggestion_is_applied("S1") is True

    def test_the_dedup_scan_still_sees_existing_content(self):
        # One torn byte used to empty `seen`, so an identical re-derived
        # suggestion re-appended — the 81-duplicate bug back in one byte.
        p = _seed(_row("S1"))
        ev._save_suggestions([ev.Suggestion.from_dict(_row("S1"))])
        n = sum(1 for l in p.read_bytes().splitlines()
                if b"suggestion body for S1" in l)
        assert n == 1

    def test_apply_works_and_preserves_the_torn_bytes(self):
        p = _seed(_row("S1"))
        assert ev.apply_suggestion("S1") is True
        after = p.read_bytes()
        assert _TORN in after
        assert ev.suggestion_is_applied("S1") is True

    def test_revert_works_and_never_launders(self):
        from orch_items import memory_dir
        # Three-row store: clean target + tainted-but-VALID bystander + torn
        # tail. The bystander is the launder probe — json.loads parses it
        # (surrogates ride through), and _mark_reverted re-dumps every row it
        # parses, which would rewrite the raw byte as a clean \udcff escape.
        p = ev._suggestions_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tainted = json.dumps(_row("S2")).encode().replace(b"fp", b"\xff")
        p.write_bytes(json.dumps(_row("S1", applied=True)).encode() + b"\n"
                      + tainted + b"\n" + _TORN + b"\n")
        cl = memory_dir() / "change_log.jsonl"
        cl.parent.mkdir(parents=True, exist_ok=True)
        cl.write_bytes(json.dumps({"suggestion_id": "S1",
                                   "category": "prompt_tweak"}).encode()
                       + b"\n" + _TORN + b"\n")
        r = ev.revert_suggestion("S1")
        assert r.get("reverted") is not False
        after = p.read_bytes()
        assert _TORN in after            # _mark_reverted preserved the torn tail
        assert tainted in after          # ...and never laundered the valid twin
        with pytest.raises(UnicodeDecodeError):
            after.decode("utf-8")        # corruption signal intact


class TestSuggestionRewritesPreserveWhatTheyCannotParse:
    """Keyed-merge rewrites re-emit unmatched/unparseable lines verbatim
    and a byte-tainted-but-parseable line never matches the key.

    Covers all FIVE keyed rewrites in the module: apply's _merge and
    revert's _drop_constraint (the two REVIEWED census entries — the AST
    census can see those sites), plus dismiss_suggestion._merge,
    stamp_verification._merge, and revert's _mark_reverted, whose except
    bodies re-emit the line and are therefore invisible to the census —
    these tests are their only tripwire (2026-08-17 review r1: the first
    two shipped unconverted; all five lenses flagged it)."""

    def test_the_apply_merge_never_launders_a_tainted_twin(self):
        # A tainted row carrying the SAME suggestion_id must not id-match:
        # matching would replace it (and its corruption signal) wholesale.
        p = ev._suggestions_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tainted = json.dumps(_row("S1")).encode().replace(b"fp", b"\xff")
        p.write_bytes(json.dumps(_row("S1")).encode() + b"\n" + tainted + b"\n")
        assert ev.apply_suggestion("S1") is True
        assert tainted in p.read_bytes()

    def test_the_dismiss_merge_never_launders_a_tainted_twin(self):
        # Same shape as apply: a tainted, not-yet-applied twin of the target
        # id must be refused by the parse, not dismissed-and-re-dumped.
        p = ev._suggestions_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tainted = json.dumps(_row("S1")).encode().replace(b"fp", b"\xff")
        p.write_bytes(json.dumps(_row("S1")).encode() + b"\n"
                      + tainted + b"\n" + _TORN + b"\n")
        assert ev.dismiss_suggestion("S1", reason="test") is True
        after = p.read_bytes()
        assert tainted in after          # tainted twin re-emitted verbatim
        assert _TORN in after            # torn tail preserved
        assert b'"dismissed"' in after   # ...while the clean row WAS stamped

    def test_the_verify_stamp_never_launders_a_tainted_twin(self):
        # stamp_verification fires unattended from the V2 cadence pass, on
        # exactly the rows being re-examined — the most exposed of the five.
        p = ev._suggestions_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tainted = json.dumps(_row("S1", applied=True)).encode().replace(
            b"fp", b"\xff")
        p.write_bytes(json.dumps(_row("S1", applied=True)).encode() + b"\n"
                      + tainted + b"\n" + _TORN + b"\n")
        assert ev.stamp_verification(
            "S1", verdict="confirmed", verified_at="2026-08-17T00:00:00Z"
        ) is True
        after = p.read_bytes()
        assert tainted in after          # tainted twin re-emitted verbatim
        assert _TORN in after            # torn tail preserved
        assert b'"confirmed"' in after   # ...while the clean row WAS stamped

    def test_the_constraint_drop_never_matches_a_tainted_row(self):
        from file_lock import locked_rmw
        dc = ev._dynamic_constraints_path()
        dc.parent.mkdir(parents=True, exist_ok=True)
        tainted = (b'{"source": "evolver:S1", "pattern": "\xff"}')
        clean = json.dumps({"source": "evolver:S1", "pattern": "x"}).encode()
        dc.write_bytes(clean + b"\n" + tainted + b"\n")
        from orch_items import memory_dir
        cl = memory_dir() / "change_log.jsonl"
        cl.write_bytes(json.dumps({
            "suggestion_id": "S1", "category": "new_guardrail",
            "suggestion_text": "never match this"}).encode() + b"\n")
        _seed(_row("S1", applied=True), torn=False)
        ev.revert_suggestion("S1")
        after = dc.read_bytes()
        assert tainted in after      # tainted twin preserved verbatim
        assert clean not in after    # the clean match WAS dropped
