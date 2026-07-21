"""Tests for the typed finding-code vocabulary (swarm-review chunk 1)."""

import pytest

from finding_codes import (
    FINDING_CODES,
    parse_finding_codes,
    parse_unknown_codes,
    stamp,
)


class TestFindingCodes:
    def test_seed_vocabulary_present(self):
        for code in ("CITATION_INVERSION", "PHANTOM_SYMBOL",
                     "THEORY_MECHANISM", "GAP_UNDERSTATED"):
            assert code in FINDING_CODES
            definition, hint = FINDING_CODES[code]
            assert definition and hint

    def test_stamp_round_trip(self):
        line = stamp("PHANTOM_SYMBOL", "`reframe_intent` — zero src/ hits")
        assert line.startswith("FINDING[PHANTOM_SYMBOL] ")
        assert parse_finding_codes(line) == ["PHANTOM_SYMBOL"]

    def test_stamp_unknown_code_raises(self):
        with pytest.raises(ValueError):
            stamp("MADE_UP_CODE")

    def test_parse_multiple_in_order(self):
        text = (
            "FINDING[CITATION_INVERSION] paper cited backwards\n"
            "prose in between\n"
            "FINDING[GAP_UNDERSTATED] two more dead callers\n"
        )
        assert parse_finding_codes(text) == [
            "CITATION_INVERSION", "GAP_UNDERSTATED"]

    def test_parse_ignores_unknown_but_surfaces_via_helper(self):
        text = "FINDING[TYPO_CODE] oops\nFINDING[PHANTOM_SYMBOL] real one"
        assert parse_finding_codes(text) == ["PHANTOM_SYMBOL"]
        assert parse_unknown_codes(text) == ["TYPO_CODE"]

    def test_stamp_without_detail(self):
        assert stamp("THEORY_MECHANISM") == "FINDING[THEORY_MECHANISM]"
