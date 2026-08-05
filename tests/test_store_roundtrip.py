"""The writer→reader round-trip probe (2026-08-04).

Built after the dynamic-guardrail lane turned out to have never loaded a
row: writer stamped `added_at` ISO, reader compared it to epoch seconds,
`str < float` raised, and the per-row `except Exception: continue` one
frame up discarded the entry whole. Both sides had passing unit tests.
Nothing counted rows-on-disk against rows-the-loader-returns, so the store
looked healthy to every check we had — including the writer/reader
*existence* census BACKLOG item (a) specifies.

What's pinned here is the probe's own judgement — a probe that can't tell
a silent drop from a declared one is worse than no probe — and the fact
that it states its coverage gap instead of implying completeness. The
guardrail seam itself is pinned at the seam, in
`test_evolver.py::test_written_guardrail_is_actually_loadable`; this file
deliberately doesn't restate it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_spec = importlib.util.spec_from_file_location(
    "store_roundtrip", Path(__file__).parent.parent / "scripts" / "store_roundtrip.py")
store_roundtrip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store_roundtrip)

classify = store_roundtrip.classify


def _row(**kw):
    base = {"name": "s", "path": "/x", "drops": "", "lines": 10, "parsed": 10,
            "unparseable": 0, "loaded": 10, "error": ""}
    base.update(kw)
    return base


class TestVerdicts:
    def test_lossless_round_trip_is_not_a_finding(self):
        assert classify(_row()) == ("ok", False)

    def test_an_undeclared_drop_is_a_finding(self):
        verdict, finding = classify(_row(loaded=7))
        assert finding
        assert "-3" in verdict and "lossless" in verdict

    def test_a_declared_drop_is_not_a_finding_but_still_shows_its_reason(self):
        verdict, finding = classify(_row(loaded=7, drops="contested rows"))
        assert not finding
        assert "contested rows" in verdict, (
            "an 'expected' drop must print what makes it expected — otherwise "
            "the exemption is unfalsifiable")

    def test_a_raising_loader_is_a_finding(self):
        """The guardrail shape: the loader itself blew up, not the data."""
        verdict, finding = classify(
            _row(loaded=None, error="TypeError: '<' not supported"))
        assert finding
        assert "RAISED" in verdict

    def test_a_raising_loader_is_a_finding_even_with_a_drops_allowance(self):
        """`drops` excuses filtering, never an exception."""
        _, finding = classify(_row(loaded=None, error="TypeError: boom",
                                   drops="expired rows"))
        assert finding

    def test_an_empty_store_is_not_a_finding(self):
        assert classify(_row(lines=0, parsed=0, loaded=0)) == ("empty store",
                                                               False)

    def test_unparseable_lines_are_reported_alongside_the_verdict(self):
        verdict, _ = classify(_row(lines=10, parsed=9, loaded=9, unparseable=1))
        assert "unparseable" in verdict

    def test_a_loader_returning_more_than_disk_is_not_a_drop(self):
        """Some loaders synthesize rows; that isn't this probe's business."""
        assert classify(_row(loaded=12))[1] is False


class TestCoverageIsStatedNotImplied:
    def test_uncovered_stores_are_listed(self, monkeypatch, tmp_path):
        """The probe table is hand-written. A hand-written list that hides
        what it doesn't cover is the rot list BACKLOG (a) warns about."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        (tmp_path / "memory").mkdir(parents=True)
        (tmp_path / "memory" / "something_new.jsonl").write_text('{"a": 1}\n')

        unc = store_roundtrip.uncovered_stores([])
        assert "memory/something_new.jsonl" in unc

    def test_run_and_project_stores_are_out_of_scope(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        for sub in ("runs/abc", "projects/xyz"):
            d = tmp_path / sub
            d.mkdir(parents=True)
            (d / "trace.jsonl").write_text('{"a": 1}\n')

        assert store_roundtrip.uncovered_stores([]) == []
