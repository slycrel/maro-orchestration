"""Tests for dev_status — the project-state readout.

Concentrated on the parsing, because that is where this instrument can
lie. Its first version counted bare words out of CAPABILITIES.md,
including the paragraph that DEFINES the three marks, and reported 42
verified where the row-level truth was 19 — turning a flat trend into an
apparent 3x convergence. Every test below that looks pedantic is pinning
that failure shut.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import dev_status as ds


GROUNDING_PARAGRAPH = """
**Grounding rule (house discipline: claimed != probed):** every example marks
whether it has actually been run. `verified` = ran end-to-end with the
expected UX; `target` = we believe current machinery covers it, unproven;
`aspirational` = needs capability that doesn't exist yet.
"""


class TestCapabilityMarks:

    def test_the_paragraph_that_defines_the_marks_counts_as_nothing(self):
        # The original bug, pinned: this text contains all three words in
        # backticks and describes zero capabilities.
        assert ds.capability_marks(GROUNDING_PARAGRAPH) == {
            "verified": 0, "target": 0, "aspirational": 0}

    def test_prose_mentions_do_not_count(self):
        text = ("The canonical case is honestly `verified` end-to-end, and "
                "the pair proves: capability `verified`, delivery `target`.")
        assert ds.capability_marks(text) == {
            "verified": 0, "target": 0, "aspirational": 0}

    def test_table_rows_count(self):
        text = (
            "| goal | notes | status |\n"
            "|---|---|---|\n"
            "| find gas | multi-step | `target` |\n"
            "| summarize | one shot | `verified` |\n"
        )
        got = ds.capability_marks(text)
        assert (got["target"], got["verified"]) == (1, 1)

    def test_a_table_cell_with_trailing_commentary_still_counts_once(self):
        text = ("| a | b |\n|---|---|\n"
                "| x | `target` — content verified by hand, delivery not |\n")
        got = ds.capability_marks(text)
        assert got["target"] == 1
        assert got["verified"] == 0, "the word in the commentary is not a mark"

    def test_status_annotations_count(self):
        text = "*(Jeremy, from the car, 2026-07-10 — status: `target`)*"
        assert ds.capability_marks(text)["target"] == 1

    def test_one_mark_per_row_leftmost_wins(self):
        # A row naming two marks is one capability, not two.
        text = "| a | b |\n|---|---|\n| `verified` | `target` |\n"
        got = ds.capability_marks(text)
        assert (got["verified"], got["target"]) == (1, 0)

    def test_bold_wrapped_marks_count(self):
        text = "| a | b |\n|---|---|\n| **`verified`** | note |\n"
        assert ds.capability_marks(text)["verified"] == 1

    def test_the_real_doc_parses_far_below_its_word_count(self):
        # Regression on the live file: the bare-word count is roughly
        # double the row count, and the readout must report the row count.
        doc = (Path(__file__).parent.parent / "docs" / "CAPABILITIES.md")
        text = doc.read_text()
        marks = ds.capability_marks(text)
        assert marks["verified"] < text.lower().count("verified")
        assert 0 < marks["verified"] < 100


class TestBacklogState:

    SAMPLE = """
## Actionable Stack

### Live thing with open work

- [ ] do the thing
- [x] did half

### A record, no boxes at all

Prose describing something that shipped.

### Gated thing (evidence-gated)

- [ ] wait for the counter to move

## Vision / Deferred

### Not counted, wrong section

- [ ] someday
"""

    def test_counts_only_the_named_section(self):
        st = ds.backlog_state(self.SAMPLE)
        assert st.open_boxes == 2      # the Vision box is excluded
        assert st.checked_boxes == 1

    def test_splits_entries_by_kind(self):
        st = ds.backlog_state(self.SAMPLE)
        assert st.entries_live == 2
        assert st.entries_record == 1
        assert st.entries_gated == 1

    def test_a_live_entry_without_a_stopping_rule_is_named(self):
        st = ds.backlog_state(self.SAMPLE)
        assert st.entries_no_stop_rule == ["Live thing with open work"]

    @pytest.mark.parametrize("marker", [
        "kill criterion", "falsifier", "evidence-gated", "watch-item",
        "revisit trigger",
    ])
    def test_every_declared_stopping_rule_shape_is_recognized(self, marker):
        doc = f"## Actionable Stack\n\n### T\n\nSome prose with a {marker}.\n\n- [ ] x\n"
        st = ds.backlog_state(doc)
        assert st.entries_gated == 1
        assert st.entries_no_stop_rule == []

    def test_a_fully_checked_entry_is_neither_live_nor_a_record(self):
        doc = "## Actionable Stack\n\n### Shipped\n\n- [x] done\n"
        st = ds.backlog_state(doc)
        assert (st.entries_live, st.entries_record) == (0, 0)


class TestTrend:

    def _git(self, table):
        return lambda args: table.get(" ".join(args), "")

    def test_unreadable_history_degrades_to_unknown_not_zero(self):
        # A silent zero would render as "no change", which is a lie.
        tr = ds.gather_trend(self._git({}), today="2026-08-16")
        assert tr.open_boxes_30d_ago is None
        assert tr.verified_30d_ago is None
        assert tr.capability_ledger_age_days is None

    def test_ledger_age_is_computed_from_the_last_commit_touching_it(self):
        g = self._git({
            "log -1 --format=%cd --date=format:%Y-%m-%d -- docs/CAPABILITIES.md":
                "2026-08-02"})
        tr = ds.gather_trend(g, today="2026-08-16")
        assert tr.capability_ledger_age_days == 14

    def test_commit_counts_survive_garbage_output(self):
        g = self._git({"rev-list --count --since=7 days ago HEAD": "not-a-number"})
        assert ds.gather_trend(g, today="2026-08-16").commits_7d == 0


class TestRender:

    CAPS = {"verified": 19, "target": 21, "aspirational": 5}

    def _render(self, **tr_kw):
        tr = ds.Trend(**tr_kw)
        return ds.render(self.CAPS, ds.BacklogState(open_boxes=60), tr,
                         today="2026-08-16")

    def test_a_stale_ledger_is_called_out_loudly(self):
        # The whole point: a flat count from an unmaintained doc must not
        # render like a flat count from stalled work.
        out = self._render(capability_ledger_age_days=30)
        assert "30 days stale" in out
        assert "bookkeeping lag, not a plateau" in out

    def test_a_fresh_ledger_says_the_count_is_readable(self):
        out = self._render(capability_ledger_age_days=1)
        assert "stale" not in out
        assert "current enough to read as real" in out

    def test_unknown_freshness_is_not_silently_fresh(self):
        out = self._render()
        assert "freshness unknown" in out

    def test_unknown_history_prints_unknown_rather_than_a_delta(self):
        assert "(30d: unknown)" in self._render()

    def test_the_fan_out_rate_carries_its_own_caveat(self):
        out = self._render(open_boxes_30d_ago=40, commits_30d=200)
        assert "+1.00" in out
        assert "never as a score" in out

    def test_the_readout_names_the_section_it_counted(self):
        # "backlog open boxes" was ambiguous — it is one section, and the
        # total across the file is roughly double.
        assert "Actionable Stack** open boxes" in self._render()


class TestOwnsItsOwnDoc:
    """The readout writes a FILE, not a region inside a narrative one.

    It began as a marker-delimited block spliced into docs/DEV_LOG.md,
    which put generated content in the same file as hand-written session
    entries: every regeneration collided with anyone's narrative edit,
    three commits existed only to refresh it, and tree-triage reported it
    as REAL uncommitted work forever, because generated content matches no
    ancestor by construction.
    """

    def test_the_render_is_a_standalone_document(self):
        out = ds.render({"verified": 1, "target": 2, "aspirational": 0},
                        ds.BacklogState(open_boxes=3), ds.Trend(),
                        today="2026-08-17")
        assert out.startswith("---\nstatus: living\n---")
        assert "## Dev status — 2026-08-17" in out, (
            "the Dev log page supplies its own h1 and its minimal "
            "renderer drops an H1 — the heading must be ##")
        assert "regenerate" in out, "it must say it is generated"

    def test_it_carries_no_splice_markers(self):
        # No markers means no shared-file region, which is the whole point.
        out = ds.render({"verified": 1, "target": 1, "aspirational": 0},
                        ds.BacklogState(), ds.Trend(), today="2026-08-17")
        assert "<!--" not in out

    def test_the_doc_path_is_not_the_narrative_log(self):
        assert ds.DOC.name == "DEV_STATUS.md"
        assert "DEV_LOG" not in str(ds.DOC)


class TestMarksMustBeTheCellsStatus:
    """Built to EVADE the parser (mutation sweep, 2026-08-16): a backticked
    mark that is not the cell's status must not count. Without this, a
    relaxed `re.search` silently re-admits the prose-counting bug."""

    def test_a_mark_mentioned_mid_cell_is_not_a_status(self):
        text = ("| goal | notes |\n|---|---|\n"
                "| find gas | see the `verified` runs below for detail |\n")
        assert ds.capability_marks(text)["verified"] == 0

    def test_a_cell_that_only_references_another_row_counts_nothing(self):
        text = ("| a | b |\n|---|---|\n"
                "| x | superseded by the `target` row above |\n"
                "| y | blocked until the `aspirational` work lands |\n")
        assert ds.capability_marks(text) == {
            "verified": 0, "target": 0, "aspirational": 0}

    def test_and_a_real_status_in_the_same_table_still_counts(self):
        # Negative control: the screen above must not be so tight that it
        # stops finding real marks.
        text = ("| a | b |\n|---|---|\n"
                "| x | see the `verified` runs |\n"
                "| y | `verified` |\n")
        assert ds.capability_marks(text)["verified"] == 1


class TestOneClaimPerRow:
    """Three lenses, 2026-08-16: the `status:` scan ran unconditionally, so
    a table row whose notes cell QUOTED a status counted twice — the same
    over-count-from-prose class this parser exists to prevent, one level in."""

    def test_a_row_quoting_a_status_still_counts_once(self):
        text = ("| a | b | c |\n|---|---|---|\n"
                "| x | `target` | quoting: status: `verified` was the old "
                "call, now target |\n")
        assert ds.capability_marks(text) == {
            "verified": 0, "target": 1, "aspirational": 0}

    def test_a_prose_status_annotation_still_counts(self):
        # Negative control: the prose shape must keep working, or the fix
        # has just deleted a real counting path.
        assert ds.capability_marks(
            "> *(from the car — status: `target`)*")["target"] == 1


class TestFencedExamplesAreNotClaims:
    """Minimalist lens, 2026-08-16: a table shown INSIDE a ``` fence is
    documentation of the format, not a capability claim — the same
    count-the-explanation failure as the grounding paragraph."""

    def test_a_fenced_example_row_does_not_count(self):
        text = ("Format:\n\n```\n| goal | status |\n|---|---|\n"
                "| example | `verified` |\n```\n\n"
                "| real | status |\n|---|---|\n| actual | `target` |\n")
        assert ds.capability_marks(text) == {
            "verified": 0, "target": 1, "aspirational": 0}

    def test_an_unclosed_fence_does_not_swallow_the_rest_of_the_doc(self):
        # Degenerate input: a stray fence must not zero the whole ledger
        # silently. It will undercount from that point — assert the shape
        # so the behavior is known rather than discovered.
        text = "```\n| a | `verified` |\n"
        assert ds.capability_marks(text)["verified"] == 0
