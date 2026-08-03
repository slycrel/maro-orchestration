"""Memory-write provenance guard (live find, 2026-08-02).

A run was handed a standing convention by a mid-run corrective interrupt,
applied it correctly for the rest of that run, and then closed a step with
*"durable feedback memory already persisted and complete — no write required"*.
Nothing had been written. The next run started blank.

That is the same fabrication shape as a run claiming "saved to
artifacts/foo.md" with no file on disk — a confident completion claim about a
write that never happened — except aimed at MEMORY, where nothing was
checking. It is also the most expensive place to fabricate: the cost is
invisible, paid later by a future run that silently never receives the
knowledge.

These pin three things, in descending order of how load-bearing they are:

  1. the guard finds the fabrication (a named claim with no matching row);
  2. it does NOT invent fabrications (the conservatism block) — the file guard
     produced two false demotions the same day (`9d88acf2`, `ea4ebe4a`), both
     from the *claim* side, both against runs that merely DISCUSSED paths;
  3. it stays advisory (the wiring block) — two runs were silently flipped to
     failed that day by a verdict layer that admits no overrule.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from provenance import (  # noqa: E402
    _memory_claim_subjects,
    _memory_store_rows,
    memory_provenance_unverified,
)


def _store_lesson(text: str) -> None:
    """Write one row the way the system writes it — through the real ledger,
    so a change to the row format fails this file instead of silently making
    the guard blind to every stored lesson."""
    import memory_ledger
    memory_ledger._store_lesson(
        task_type="general", outcome="done", lesson=text,
        source_goal="pin: memory-provenance guard")


# The founding incident's convention, as a run would narrate persisting it.
CLAIM = ('Persisted the convention to durable memory: always cite the '
         'originating run id in step summaries.')
CONVENTION = "Always cite the originating run id in step summaries."


class TestClaimWithMatchingRow:
    """The clean case. A run that says it persisted something, and did, must
    come out of this guard completely unremarked — an advisory that fires on
    honest work is noise, and noise is how a guard gets turned off."""

    def test_matching_lesson_row_is_clean(self):
        _store_lesson(CONVENTION)
        assert memory_provenance_unverified(CLAIM) == []

    def test_row_that_reworded_the_connective_tissue_still_counts(self):
        """The lookup is deliberately generous: a stored row carrying the
        claim's content words, in among others, IS the write landing. Only the
        forgiving direction is safe here — the strict one accuses honest runs."""
        _store_lesson("Rule: always cite the originating run id in every step "
                      "summary, including partial ones.")
        assert memory_provenance_unverified(CLAIM) == []

    def test_a_tiered_store_row_counts(self):
        """Lessons live in medium/long tiers as well as the flat ledger; a
        guard that only knew one store would accuse runs that used the other."""
        import knowledge_web
        knowledge_web.record_tiered_lesson(
            CONVENTION, "general", "done", "pin",
            tier=knowledge_web.MemoryTier.LONG)
        assert memory_provenance_unverified(CLAIM) == []

    def test_a_decision_journal_row_counts(self):
        import knowledge_lens
        knowledge_lens.record_decision(CONVENTION, rationale="operator decree")
        assert memory_provenance_unverified(CLAIM) == []

    def test_a_knowledge_node_counts(self):
        import knowledge_web
        knowledge_web.append_knowledge_node(knowledge_web.KnowledgeNode(
            node_id="n1", node_type="principle", title="run id citation",
            description=CONVENTION))
        assert memory_provenance_unverified(CLAIM) == []

    def test_no_freshness_gate_on_the_row(self):
        """Unlike the file layer, this one has no run-window check on purpose,
        and the pin is structural: "already persisted" is a legitimate claim
        shape, and the ledgers reinforce a duplicate rather than appending it —
        so a mtime/window gate would flag every correct no-op re-record."""
        import inspect
        import provenance
        src = inspect.getsource(provenance.memory_provenance_unverified)
        assert "window_start" not in src and "_is_fresh" not in src
        _store_lesson(CONVENTION)
        assert memory_provenance_unverified(CLAIM) == []


class TestClaimWithNoMatchingRow:
    """The incident itself: the claim is made, the store does not have it."""

    def test_unbacked_claim_is_flagged(self):
        _store_lesson("Unrelated: prefer WebFetch over curl for HTML pages.")
        found = memory_provenance_unverified(CLAIM)
        assert len(found) == 1
        assert "originating run id" in found[0]
        assert "no matching row" in found[0]

    def test_a_near_miss_is_still_a_miss(self):
        """The lesson the system minted about the incident names this exactly:
        the run closed without verifying the existing memory *matched the newly
        stated convention's exact wording*. A row about a different rule is not
        that rule."""
        _store_lesson("Always cite the originating file path in step summaries.")
        assert memory_provenance_unverified(CLAIM) != []

    def test_quoted_and_that_clause_claims_are_checked_too(self):
        _store_lesson("Unrelated content about http retries.")
        quoted = ('Saved the rule "artifacts always land under artifacts/ in '
                  'the run dir" to durable memory.')
        that = ('Recorded the standing convention that step summaries must '
                'cite the originating run id to the memory store.')
        assert memory_provenance_unverified(quoted) != []
        assert memory_provenance_unverified(that) != []

    def test_findings_are_capped(self):
        """Evidence, not a transcript — a run narrating twenty claims must not
        put twenty strings into run metadata."""
        _store_lesson("Unrelated.")
        text = "\n".join(
            f"Persisted the convention to durable memory: rule number {i} "
            f"says workers must always tag artifacts {i}." for i in range(20))
        assert len(memory_provenance_unverified(text)) <= 5


class TestConservatism:
    """The expensive error is the false accusation, not the miss.

    The file guard's two live false demotions (2026-08-02) both came from the
    claim side reading prose that DISCUSSED a path as prose that CLAIMED it.
    Memory prose is worse that way: forensic runs, self-inspection runs, and
    the lesson pipeline itself all talk about memory constantly. Every case
    here must produce nothing even though a durable-memory noun is present and
    no matching row exists.
    """

    @pytest.mark.parametrize("text", [
        # Plain discussion of the store.
        "The memory store currently holds 386 lesson rows across two tiers.",
        "Reviewed durable memory for existing conventions about run ids.",
        # Intent, not completion — the run is telling you what it will do.
        "I will persist the convention to durable memory in the next step.",
        "We should have recorded that convention to durable memory.",
        "The plan is to save the convention to the memory store after review.",
        # Reporting on someone else's claim — this is the ea4ebe4a class, and
        # it is the exact sentence the incident's own lesson is written in.
        "The run claimed it had persisted the rule to durable memory when it "
        "had not.",
        # Explicit negation: the run is being honest about NOT writing.
        "The convention was not recorded to durable memory because the step "
        "ran out of budget.",
        "This step did not save anything to durable memory.",
        # A question is not a claim.
        "Did we record the convention that summaries cite run ids to memory?",
        # RAM, not durability — "in memory" is the engineering idiom.
        "Loaded the lessons file into memory and ranked it by TF-IDF.",
        "Cached the ranked lessons in memory for the rest of the loop.",
        # A file write that happens to mention future runs.
        "Persisted the report to artifacts/report.md for the operator.",
    ])
    def test_prose_about_memory_is_not_a_claim(self, text):
        _store_lesson("Unrelated stored lesson about http retries.")
        assert memory_provenance_unverified(text) == [], text

    def test_a_claim_that_names_nothing_is_skipped_not_accused(self):
        """The founding incident's own wording ("durable feedback memory
        already persisted and complete — no write required") names no content,
        so there is nothing to look up. Documented residual, deliberately
        chosen: verifying it would mean fuzzy-matching operator prose against
        stored wording, which is the imprecise claim-side machinery that
        produced the file guard's false demotions."""
        _store_lesson("Unrelated stored lesson.")
        assert memory_provenance_unverified(
            "Durable feedback memory already persisted and complete - no "
            "write required.") == []

    def test_a_two_word_subject_is_too_generic_to_verify(self):
        """"the rule" matches everything or nothing, arbitrarily. A subject
        that thin is not evidence of anything."""
        _store_lesson("Unrelated stored lesson.")
        assert _memory_claim_subjects(
            "Persisted the rule to durable memory: use tabs.") == []

    def test_an_unreachable_store_never_accuses(self):
        """No store file at all cannot be distinguished from "memory lives
        somewhere else" — and an accusation on that footing is precisely the
        unverified confidence this guard exists to catch."""
        assert memory_provenance_unverified(CLAIM) == []


class TestFailsOpen:
    """Any exception must leave the run untouched — same discipline as
    `_provenance_missing`. An advisory annotation that can break a run is a
    worse bug than the one it reports."""

    @pytest.mark.parametrize("bad", [None, "", 12345, [], {"a": 1}, b"bytes",
                                     "\x00" * 32, "no verbs here at all"])
    def test_malformed_input_never_raises(self, bad):
        assert memory_provenance_unverified(bad) == []

    def test_a_corrupt_store_line_is_skipped_not_fatal(self):
        import orch_items
        _store_lesson(CONVENTION)
        path = orch_items.memory_dir() / "lessons.jsonl"
        path.write_text("{not json\n\n[]\n" + path.read_text(), encoding="utf-8")
        assert memory_provenance_unverified(CLAIM) == []

    def test_a_store_that_raises_on_read_is_survivable(self, monkeypatch):
        import provenance
        monkeypatch.setattr(provenance, "_memory_store_paths",
                            lambda: (_ for _ in ()).throw(OSError("boom")))
        assert memory_provenance_unverified(CLAIM) == []


class TestConfigFlag:
    """`validate.memory_provenance`, documented in docs/DEFAULTS.md beside its
    siblings. Off must mean OFF — no scan, no finding."""

    def _flag(self, monkeypatch, value):
        import config as config_mod
        real = config_mod.get
        monkeypatch.setattr(
            config_mod, "get",
            lambda key, default=None: (value if key == "validate.memory_provenance"
                                       else real(key, default)))

    def test_flag_off_produces_no_findings(self, monkeypatch):
        _store_lesson("Unrelated stored lesson.")
        assert memory_provenance_unverified(CLAIM) != []
        self._flag(monkeypatch, False)
        assert memory_provenance_unverified(CLAIM) == []

    def test_a_quoted_false_still_kills_it(self, monkeypatch):
        """config.get hands back raw YAML nodes, so `enabled: "false"` arrives
        as a truthy string — the chunk-5a lesson that a killswitch which can't
        kill is worse than none."""
        _store_lesson("Unrelated stored lesson.")
        self._flag(monkeypatch, "false")
        assert memory_provenance_unverified(CLAIM) == []

    def test_default_is_on(self):
        _store_lesson("Unrelated stored lesson.")
        assert memory_provenance_unverified(CLAIM) != []


class TestAdvisoryWiring:
    """Structural pin on the posture, not the plumbing.

    The standing decree (Jeremy, 2026-08-02) is "it's less about being correct
    up front and more about how well you recover when you're wrong" — a verdict
    layer that admits no overrule has to earn that standing, and this guard's
    claim half is a regex over prose. So the wiring may MARK and must not
    demote. If someone later wires it into a verdict, this fails and they have
    to say so out loud.
    """

    def _mark_source(self) -> str:
        import inspect
        import handle
        return inspect.getsource(handle._mark_memory_provenance)

    def test_the_mark_never_touches_the_verdict(self):
        src = self._mark_source()
        assert "goal_achieved" not in src
        assert "goal_verdict_source" not in src
        assert "status" not in src

    def test_the_finding_is_recorded_where_a_human_can_find_it(self):
        """Silent is the failure mode being fixed. The finding rides in run
        metadata and in the log, or it may as well not exist."""
        src = self._mark_source()
        assert "stamp_run_metadata" in src
        assert "memory_provenance_unverified" in src
        assert "log.warning" in src

    def test_both_lanes_run_it(self):
        """NOW and agenda both close steps with narrated results; a guard wired
        into one lane only is a guard with a hole in it."""
        import inspect
        import handle
        src = inspect.getsource(handle)
        assert src.count("_mark_memory_provenance(") >= 3  # def + NOW + agenda
        assert "_mark_memory_provenance(_done_results)" in src

    def test_the_now_lane_marks_before_it_can_return_early(self):
        """The file guard returns early on a demotion. A mark computed after
        that point would be dropped exactly when the run is most suspect."""
        import inspect
        import handle
        src = inspect.getsource(handle._verify_now_outcome)
        assert (src.index("_mark_memory_provenance(")
                < src.index("_provenance_missing("))


class TestStoreLookupIsExact:
    """The half of this guard that earns trust: no LLM, no ranking, no
    similarity score — a reproducible predicate over rows that are on disk."""

    def test_no_llm_or_ranker_is_involved(self):
        import inspect
        import provenance
        src = inspect.getsource(provenance._memory_row_exists)
        for banned in ("adapter", "complete(", "tfidf", "hybrid_rank",
                       "embed", "similarity"):
            assert banned not in src.lower()

    def test_rows_are_read_from_the_real_store_files(self):
        import orch_items
        _store_lesson(CONVENTION)
        rows, saw_store = _memory_store_rows()
        assert saw_store is True
        assert any("originating run id" in norm for norm, _ in rows)
        raw = (orch_items.memory_dir() / "lessons.jsonl").read_text()
        assert json.loads(raw.splitlines()[0])["lesson"] == CONVENTION


class TestUnverifiableSufficiencyClaims:
    """The founding incident's EXACT wording was not caught by the accusing
    guard, and deliberately still isn't.

    Run 9c8d0a43 closed a step with "durable feedback memory already persisted
    and complete — no write required" and the rule it was meant to persist was
    never stored. That sentence names no content, so there is nothing to look
    up. Deciding whether "already complete" is true would mean fuzzy-matching
    operator prose against stored wording — the imprecise claim-side machinery
    that produced two FULL-trust false demotions on 2026-08-02.

    So the claim is recorded as UNVERIFIABLE rather than false: a weaker
    category that asserts nothing about whether the run lied, and exists only
    so the shape stops being invisible. Same discipline as the run-readout
    residue counter — an unrecognized failure mode is otherwise
    indistinguishable from success.
    """

    def _u(self, text):
        from provenance import memory_claims_unverifiable
        return memory_claims_unverifiable(text)

    def test_founding_incident_verbatim_is_recorded(self):
        got = self._u("Durable feedback memory already persisted and "
                      "complete - no write required.")
        assert len(got) == 1
        assert "names nothing checkable" in got[0]

    def test_it_is_not_an_accusation(self):
        """Wording is load-bearing: this must never read as 'the run lied'."""
        got = self._u("The standing memory is already up to date; no write required.")
        assert got and "no matching row" not in got[0]

    def test_forensic_register_is_skipped(self):
        """Same false-positive class the file guard fell into on ea4ebe4a."""
        assert self._u("The run claimed durable memory was already complete, "
                       "but no row was ever written.") == []

    def test_ram_idiom_is_not_a_memory_claim(self):
        assert self._u("The parsed table was already present in memory.") == []

    def test_unrelated_sufficiency_is_skipped(self):
        assert self._u("No update required to the README.") == []

    def test_checkable_claims_belong_to_the_other_guard_not_both(self):
        """Never double-reported: a sufficiency claim that DOES name content
        is checkable, so it must route to the accusing guard alone."""
        text = ("Already saved the standing convention to durable memory for "
                "future runs: always end a cost report with a COST-METHOD "
                "line naming the accounting method used.")
        from provenance import _memory_claim_subjects
        assert _memory_claim_subjects(text), "fixture must name content"
        assert self._u(text) == []

    def test_question_is_not_a_claim(self):
        assert self._u("Is the durable memory already complete, or is a "
                       "write required?") == []

    def test_never_raises(self):
        assert self._u(None) == [] and self._u("") == []

    def test_findings_are_capped(self):
        text = " ".join(f"Durable memory {i} already complete - no write required."
                        for i in range(30))
        assert len(self._u(text)) <= 5
