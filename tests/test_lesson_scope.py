"""§14a slice 3 — mint-time method/world scope stamp.

Pins the provenance contract (decision e2b83703): the stamp is categorical,
written at mint from the extractor's typed JSON, filled once and never
flipped, and consumed by the slice-1 census only — never by ranking, which
stays on earned globality (test_portability.py).

The cross-slot recovery tests exist because of a measured failure, not a
hypothetical: on the naive schema wording a scope value landed in the "type"
slot on 2 of 18 live-corpus extractions, and the pre-existing
not-a-known-type fallback would have silently rewritten those to "execution".
The shipped prompt drove that to 0/31 across two model lanes; the parser
recovers anyway.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import memory  # noqa: E402
from memory import TypedLesson, as_typed_lesson  # noqa: E402


def _parse(raw):
    """Drive the module's real parser through its public entry point.

    _parse_typed is a closure inside extract_lessons_via_llm, so the honest
    way to reach it is the adapter seam the production path uses.
    """
    class _Adapter:
        def complete(self, messages, **kw):
            self.system = messages[0].content
            return type("R", (), {"content": json.dumps(raw),
                                  "input_tokens": 0, "output_tokens": 0})()

    return memory.extract_lessons_via_llm(
        goal="g", status="done", result_summary="s", task_type="research",
        adapter=_Adapter(), return_typed=True)


class TestParseScope:
    def test_scope_parsed_from_typed_json(self):
        out = _parse([{"lesson": "L1", "type": "planning", "scope": "world"}])
        assert out == [TypedLesson("L1", "planning", "world")]

    def test_missing_scope_is_unstamped(self):
        # The pre-slice-3 wire shape. Must still parse — an old cached
        # response or a model that ignores the new key cannot cost us lessons.
        out = _parse([{"lesson": "L1", "type": "planning"}])
        assert out == [TypedLesson("L1", "planning", "")]

    def test_unknown_scope_value_is_dropped_not_invented(self):
        out = _parse([{"lesson": "L1", "type": "planning", "scope": "global"}])
        assert out[0].scope == ""

    def test_legacy_bare_string_still_parses(self):
        out = _parse(["just a string"])
        assert out == [TypedLesson("just a string", "execution", "")]

    def test_scope_value_in_type_slot_is_recovered(self):
        # The measured 2/18 failure. Pre-fix this yielded ("L1","execution",""):
        # a wrong lesson_type AND a lost stamp.
        out = _parse([{"lesson": "L1", "type": "world"}])
        assert out[0].scope == "world"
        assert out[0].lesson_type == "execution"  # no type was actually given

    def test_scope_value_in_type_slot_does_not_clobber_a_real_scope(self):
        out = _parse([{"lesson": "L1", "type": "method", "scope": "world"}])
        assert out[0].scope == "world"

    def test_type_value_in_scope_slot_is_recovered(self):
        out = _parse([{"lesson": "L1", "type": "", "scope": "recovery"}])
        assert out[0].lesson_type == "recovery"
        assert out[0].scope == ""

    def test_both_slots_crossed_recovers_both(self):
        """Full swap: each slot holds the other's value.

        r1 review: the sequential recovery form fixed lesson_type and then
        zeroed the scope it had just rescued — destroying the value this
        block exists to preserve.
        """
        out = _parse([{"lesson": "L1", "type": "world", "scope": "planning"}])
        assert out[0].lesson_type == "planning"
        assert out[0].scope == "world"

    def test_scope_in_type_slot_survives_junk_in_the_scope_slot(self):
        """r2 fix-audit: the (scope-word-in-type, junk-in-scope) cell.

        Only-if-empty recovery dropped the one real scope answer in the row
        whenever the model also filled the scope slot with something outside
        the vocabulary — the row landed ("execution", ""), indistinguishable
        from a lesson that was never stamped at all.
        """
        out = _parse([{"lesson": "L1", "type": "world", "scope": "global"}])
        assert out[0].scope == "world"
        assert out[0].lesson_type == "execution"

    def test_cross_type_cap_still_applies_with_scopes(self):
        out = _parse([
            {"lesson": "A", "type": "planning", "scope": "method"},
            {"lesson": "B", "type": "planning", "scope": "world"},
            {"lesson": "C", "type": "recovery", "scope": "world"},
        ])
        assert [t.lesson for t in out] == ["A", "C"]

    def test_dry_run_lessons_are_unstamped(self):
        out = memory.extract_lessons_via_llm(
            goal="g", status="done", result_summary="s", task_type="research",
            dry_run=True, return_typed=True)
        assert out[0].scope == ""

    def test_untyped_return_is_still_plain_strings(self):
        class _Adapter:
            def complete(self, messages, **kw):
                return type("R", (), {
                    "content": json.dumps(
                        [{"lesson": "L1", "type": "planning", "scope": "world"}]),
                    "input_tokens": 0, "output_tokens": 0})()

        assert memory.extract_lessons_via_llm(
            goal="g", status="done", result_summary="s", task_type="research",
            adapter=_Adapter()) == ["L1"]


class TestPromptContract:
    """The stamp only arrives if the prompt keeps asking for it correctly."""

    def test_prompt_requests_both_keys_as_separate_axes(self):
        p = memory._REFLECT_SYSTEM
        assert '"scope"' in p and '"type"' in p
        assert "method" in p and "world" in p

    def test_prompt_forbids_the_measured_cross_slot_failure(self):
        assert "Never put a scope value" in memory._REFLECT_SYSTEM

    def test_examples_carry_both_keys(self):
        # The S2 seed-reader removal (2026-08-06) is the precedent: exemplars
        # anchor extraction. Every example must show both keys, or the model
        # learns the stamp is optional. An earlier draft of this prompt also
        # SWAPPED an exemplar's lesson_type and measurably shifted the type
        # distribution — hence the second assertion.
        import re
        examples = memory._REFLECT_SYSTEM.split("Example:")[1]
        assert examples.count('"scope"') == examples.count('"type"')
        # Pin the exact multiset, not mere presence: the S2 mechanism is
        # distributional, so swapping ONE of two same-typed exemplars is a
        # real anchor change that a presence check sails straight past.
        assert sorted(re.findall(r'"type": "(\w+)"', examples)) == [
            "planning", "recovery", "recovery"]
        assert sorted(re.findall(r'"scope": "(\w+)"', examples)) == [
            "method", "method", "world"], (
            "exemplars must show both scope values, or the model learns one")


class TestAsTypedLesson:
    def test_legacy_pair_normalizes_to_unstamped_triple(self):
        # Test doubles across the suite return two-element pairs.
        assert as_typed_lesson(("text", "planning")) == TypedLesson(
            "text", "planning", "")

    def test_triple_passes_through(self):
        assert as_typed_lesson(("t", "cost", "world")).scope == "world"

    def test_bad_scope_normalizes_to_unstamped(self):
        assert as_typed_lesson(("t", "cost", "GLOBAL")).scope == ""

    def test_case_is_normalized(self):
        assert as_typed_lesson(("t", "cost", "World")).scope == "world"

    def test_bare_string_is_not_shredded_into_characters(self):
        # r1 review: tuple("a lesson") yields its CHARACTERS, so the
        # normalizer silently returned lesson='a'. A bare string is a shape
        # this module hands out (return_typed=False), so a fake copied from
        # there lands here.
        out = as_typed_lesson("a bare lesson string")
        assert out.lesson == "a bare lesson string"
        assert out.lesson_type == "execution"

    def test_lesson_dict_is_not_shredded_into_keys(self):
        # tuple(dict) yields KEYS — this returned lesson='lesson'. The dict
        # is the exact shape the LLM wire format uses, so a double mimicking
        # the real response hits it.
        out = as_typed_lesson({"lesson": "Real text", "type": "planning",
                               "scope": "world"})
        assert out == TypedLesson("Real text", "planning", "world")

    def test_uninterpretable_item_degrades_instead_of_raising(self):
        assert as_typed_lesson(object()) == TypedLesson("", "execution", "")

    def test_an_empty_item_says_so_in_the_log(self, caplog):
        """The sibling branch, which shipped with no test at all (r3).

        Both degrade to the same empty TypedLesson, so the log line is the
        ONLY thing that distinguishes "a lesson arrived with no text" from
        "nothing arrived" — replacing it with `pass` survived every related
        test file.
        """
        with caplog.at_level(logging.WARNING, logger="memory"):
            assert as_typed_lesson([]) == TypedLesson("", "execution", "")
        assert any("no lesson text to keep" in r.getMessage()
                   for r in caplog.records), [
                       (r.name, r.getMessage()) for r in caplog.records]


class TestScopeIsNotARankingInput:
    """The decree, with a tripwire under it (r3 design lens).

    e2b83703 settled that the categorical stamp feeds the CENSUS ONLY and
    ranking stays on earned globality. The design held on inspection, but
    nothing in the suite failed if a future session wired `scope` into
    ranking — and this file's own docstring pointed at test_portability.py,
    which never mentions scope.
    """

    def test_the_vocabularies_do_not_drift(self):
        # knowledge_web's copy carries a prose "Mirrors memory._LESSON_SCOPES"
        # claim and nothing checked it.
        import knowledge_web
        assert memory._LESSON_SCOPES == knowledge_web._LESSON_SCOPES

    def test_no_ranking_module_reads_the_stamp(self):
        """Grep tripwire: the seam must stay empty.

        r4 audit: the first version required "scope" AND "lesson" on the SAME
        line, so `_bonus = 1.2 if getattr(tl, 'scope', '') == 'method'` sailed
        straight through. Match the attribute access itself.
        """
        root = Path(__file__).parent.parent / "src"
        offenders = []
        for name in ("portability.py", "knowledge_lens.py", "recall.py"):
            f = root / name
            if not f.exists():
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                bare = line.split("#")[0]
                if ".scope" in bare or '"scope"' in bare or "'scope'" in bare:
                    offenders.append(f"{name}:{i}: {line.strip()}")
        assert not offenders, (
            "a ranking-path module now reads the lesson scope stamp — that is "
            "the e2b83703 decree, not a style preference. If this is "
            "deliberate, the decree changed and GOAL_BRAIN says so first:\n"
            + "\n".join(offenders))

    def test_the_stamp_does_not_move_a_lesson_in_the_real_ranker(self, tmp_path,
                                                                 monkeypatch):
        """Behavioral pin, because a grep only covers the modules it lists.

        Two lessons identical but for the stamp must rank identically through
        the real query path. This is the decree stated as behavior rather than
        as a file list (r4 skeptic).
        """
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
        from knowledge_web import record_tiered_lesson, query_lessons_scored
        texts = ["Timeouts on a bot-challenged endpoint never recover on retry",
                 "Timeouts on a rate-limited endpoint never recover on retry"]
        record_tiered_lesson(texts[0], "research", "done", "g1", scope="method")
        record_tiered_lesson(texts[1], "research", "done", "g2", scope="world")
        stamped = {t: sc for t, sc in
                   zip(texts, ("method", "world"))}
        scored = query_lessons_scored("endpoint timeouts on retry",
                                      task_type="research", n=10)
        by_text = {tl.lesson: score for tl, score in scored}
        assert set(by_text) == set(texts), by_text
        # Same evidence, same shape, different stamp -> the stamp contributed
        # nothing. (Scores differ only if the TEXT differs, which it does
        # slightly; compare each against its own unstamped twin instead.)
        for t in texts:
            row = next(tl for tl, _ in scored if tl.lesson == t)
            assert row.scope == stamped[t], "fixture wrong, not the ranker"
        import portability
        pairs = [(tl, sc) for tl, sc in scored]
        out, adj = portability.apply_portability(pairs, "a foreign goal", "proj")
        assert out is pairs and adj == [], (
            "the ranking hook reacted to lessons whose only distinguishing "
            "feature is the scope stamp")


class TestStorePersistence:
    @pytest.fixture(autouse=True)
    def _ws(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
        self.ws = tmp_path

    def _rows(self, tier="medium"):
        p = self.ws / "memory" / tier / "lessons.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_scope_round_trips_to_disk_and_back(self):
        from knowledge_web import load_tiered_lessons, record_tiered_lesson
        record_tiered_lesson("a durable lesson about fetching", "research",
                             "done", "some goal", scope="world")
        assert self._rows()[0]["scope"] == "world"
        assert load_tiered_lessons("medium", min_score=0.0)[0].scope == "world"

    def test_unknown_scope_stored_as_unstamped(self):
        from knowledge_web import record_tiered_lesson
        record_tiered_lesson("another lesson", "research", "done", "g",
                             scope="somewhere-else")
        assert self._rows()[0]["scope"] == ""

    def test_legacy_row_without_scope_loads_as_unstamped(self):
        from knowledge_web import load_tiered_lessons
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lessons.jsonl").write_text(json.dumps({
            "lesson_id": "old1", "task_type": "research", "outcome": "done",
            "lesson": "a lesson from before the stamp existed",
            "source_goal": "g", "confidence": 0.5, "tier": "medium",
            "score": 1.0, "last_reinforced": "2026-08-01"}) + "\n",
            encoding="utf-8")
        assert load_tiered_lessons("medium", min_score=0.0)[0].scope == ""

    def test_reinforce_fills_an_empty_stamp(self):
        from knowledge_web import load_tiered_lessons, record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        record_tiered_lesson(text, "research", "done", "goal one")
        record_tiered_lesson(text, "research", "done", "goal two", scope="world")
        rows = load_tiered_lessons("medium", min_score=0.0)
        assert len(rows) == 1, "expected dedup-reinforce, not a second row"
        assert rows[0].scope == "world"
        assert rows[0].times_reinforced == 1

    def test_reinforce_never_flips_an_existing_stamp(self):
        # The never-flip invariant: the category is a fact about origin.
        from knowledge_web import load_tiered_lessons, record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        record_tiered_lesson(text, "research", "done", "goal one", scope="world")
        record_tiered_lesson(text, "research", "done", "goal two", scope="method")
        rows = load_tiered_lessons("medium", min_score=0.0)
        assert len(rows) == 1
        assert rows[0].scope == "world"

    def test_reinforce_heals_an_off_vocabulary_stamp(self, caplog):
        """Fill-if-empty must mean fill-if-INVALID, not fill-if-falsy.

        r2 Expert QA: a row carrying `"scope": "banana"` is not stamped — it
        is corrupt — but it is truthy, so a truthiness test froze the garbage
        in place permanently and no honest mint could ever displace it.
        """
        from knowledge_web import load_tiered_lessons, record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lessons.jsonl").write_text(json.dumps({
            "lesson_id": "forged1", "task_type": "research", "outcome": "done",
            "lesson": text, "source_goal": "goal one", "confidence": 0.5,
            "tier": "medium", "score": 1.0, "last_reinforced": "2026-08-01",
            "scope": "banana"}) + "\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="knowledge_web"):
            record_tiered_lesson(text, "research", "done", "goal two",
                                 scope="world")
        rows = load_tiered_lessons("medium", min_score=0.0)
        assert len(rows) == 1, "expected dedup-reinforce, not a second row"
        assert rows[0].scope == "world"
        # Healing silently would erase the only trace that a corrupt row ever
        # existed — the heal is the one moment anyone can see it (broad
        # mutation sweep, r4: suppressing this warning survived the suite).
        assert "carried an off-vocabulary scope" in caplog.text

    def test_reinforce_rejects_an_off_vocabulary_incoming_stamp(self):
        """`incoming_scope` is NOT filtered by the caller.

        `record_tiered_lesson` screens the vocabulary only in the new-row
        constructor; on the reinforce path it forwards `scope` raw. The one
        thing between `scope="banana"` and a permanently corrupt store row is
        the `incoming_scope in _LESSON_SCOPES` conjunct, and nothing tested it
        — dropping it wrote "banana" to disk and survived the whole related
        suite (r3 test-audit).
        """
        from knowledge_web import record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        record_tiered_lesson(text, "research", "done", "goal one")
        record_tiered_lesson(text, "research", "done", "goal two",
                             scope="banana")
        rows = self._rows()
        assert len(rows) == 1, "expected dedup-reinforce, not a second row"
        assert rows[0]["scope"] == "", f"garbage reached disk: {rows[0]['scope']!r}"

    @pytest.mark.parametrize("bad", [["world"], {"s": 1}, {"world"}])
    def test_reinforce_survives_an_unhashable_incoming_stamp(self, bad):
        """The type axis on the INCOMING side, which r3 left bare.

        r3 screened the stored scope and left `incoming_scope in
        _LESSON_SCOPES` eight lines under a comment saying the type check has
        to come first "everywhere", and the suite above only ever handed the
        reinforce path a hashable "banana". `record_tiered_lesson` is
        re-exported public API, both mint call sites swallow the TypeError as
        a bare counter bump, and the lost mint leaves no log line (r4, two
        lenses).
        """
        from knowledge_web import record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        record_tiered_lesson(text, "research", "done", "goal one")
        record_tiered_lesson(text, "research", "done", "goal two", scope=bad)
        rows = self._rows()
        assert len(rows) == 1, "expected dedup-reinforce, not a second row"
        assert rows[0]["times_reinforced"] == 1, "the mint was lost, not reinforced"
        assert rows[0]["scope"] == ""

    def test_an_off_vocabulary_mint_is_reported_not_absorbed(self, caplog):
        """A silent "" makes a caller bug indistinguishable from an honest
        unstamped mint — and the reinforce heal warns loudly on the identical
        condition, so the write path was inconsistent with itself (r4).
        """
        from knowledge_web import record_tiered_lesson
        with caplog.at_level(logging.WARNING, logger="knowledge_web"):
            record_tiered_lesson("Bot challenges never clear on retry",
                                 "research", "done", "g", scope="banana")
        assert "minted with an off-vocabulary scope" in caplog.text
        assert self._rows()[0]["scope"] == ""

    @pytest.mark.parametrize("bad", ["banana", "World", "  method  ", 7, True,
                                     ["world"], {"s": 1}, 0.5])
    def test_reinforce_heals_every_corrupt_shape_without_crashing(self, bad):
        """The type axis, applied to the WRITER this time.

        r2 parametrized forged scopes over the census READER and used a single
        string on the reinforce side — and that is exactly where the r2 fix
        put a bare `row.scope not in _LESSON_SCOPES`, which RAISES on an
        unhashable stored value. Both mint call sites swallow the exception as
        a bare counter bump, so one corrupt row silently blackholed every
        future mint matching its text, permanently: the heal that would clear
        the value was the thing that crashed (r3, three lenses).
        """
        from knowledge_web import record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lessons.jsonl").write_text(json.dumps({
            "lesson_id": "forged1", "task_type": "research", "outcome": "done",
            "lesson": text, "source_goal": "goal one", "confidence": 0.5,
            "tier": "medium", "score": 1.0, "last_reinforced": "2026-08-01",
            "scope": bad}) + "\n", encoding="utf-8")
        record_tiered_lesson(text, "research", "done", "goal two", scope="world")
        rows = self._rows()
        assert len(rows) == 1, "the mint was lost, not reinforced"
        assert rows[0]["scope"] == "world"
        assert rows[0]["times_reinforced"] == 1

    def test_a_non_numeric_score_does_not_wedge_every_write(self):
        """One bad row must not take the tier's whole write path down.

        `load_tiered_lessons` sorts by score OUTSIDE its per-row guard, so a
        row with `"score": "high"` raised TypeError on every raw load — and
        every read-modify-write (reinforce, promote, GC, tombstone, canon,
        pack variant-union) loads raw. The ordinary read path hid the row, so
        nothing pointed at it: the reader concealed it and the writer died on
        it (r3 Expert QA). Pre-existing, found through this slice.
        """
        from knowledge_web import load_tiered_lessons, record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        base = {"task_type": "research", "outcome": "done", "source_goal": "g",
                "confidence": 0.5, "tier": "medium",
                "last_reinforced": "2026-08-01"}
        (d / "lessons.jsonl").write_text("\n".join(json.dumps(r) for r in [
            dict(base, lesson_id="L-good", lesson=text, score=1.0),
            dict(base, lesson_id="L-bad", lesson="other", score="high"),
        ]) + "\n", encoding="utf-8")
        loaded = load_tiered_lessons("medium", min_score=0.0, limit=None,
                                     raw=True)
        assert [t.lesson_id for t in loaded] == ["L-good"]
        record_tiered_lesson(text, "research", "done", "goal two")
        assert any(r["lesson_id"] == "L-good" and r["times_reinforced"] == 1
                   for r in self._rows()), "the reinforce was lost"

    def _seed_drift(self, text):
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lessons.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"lesson_id": "L-good", "task_type": "research", "outcome": "done",
             "lesson": text, "source_goal": "g", "confidence": 0.5,
             "tier": "medium", "score": 1.0, "last_reinforced": "2026-08-01"},
            {"lesson_id": "L-drift", "lesson": "from a future schema"},
        ]) + "\n", encoding="utf-8")
        return d

    def _sidecar(self):
        p = self.ws / "memory" / "medium" / "lessons.jsonl.unparseable"
        return ([json.loads(l) for l in p.read_text().splitlines() if l.strip()]
                if p.exists() else [])

    def test_unparseable_rows_are_quarantined_not_destroyed(self, caplog):
        """Data retention: a rewrite must never destroy the only copy.

        Rewrites rebuild the file from the PARSED list and the loader silently
        skips what it cannot parse, so before r3 the next reinforcement
        permanently deleted every unparseable row — unarchived, uncounted,
        against `_archive_lessons`' own "decay trust, never data" decree.
        (The flat store has preserved them for ages; the tiered store did not,
        and that inconsistency is the actual finding.)
        """
        from knowledge_web import record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        self._seed_drift(text)
        with caplog.at_level(logging.WARNING, logger="knowledge_web"):
            record_tiered_lesson(text, "research", "done", "goal two")
        assert [r.get("lesson_id") for r in self._rows()] == ["L-good"]
        assert [r.get("lesson_id") for r in self._sidecar()] == ["L-drift"]
        # A quarantined row is invisible to every reader; silent quarantine is
        # how the pre-r3 silent deletion went unnoticed for months.
        assert "unparseable lesson row(s)" in caplog.text

    def test_quarantine_is_idempotent(self):
        """Append-only sidecar + shrunken live file = no duplication.

        The r3 shape carried rows back into the live file, so this had to be
        re-checked under the r4 shape: a second rewrite must find nothing left
        to move rather than re-appending what it moved last time.
        """
        from knowledge_web import record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        self._seed_drift(text)
        for goal in ("goal two", "goal three", "goal four"):
            record_tiered_lesson(text, "research", "done", goal)
        assert [r.get("lesson_id") for r in self._sidecar()] == ["L-drift"]

    def test_a_quarantined_row_cannot_resurrect_a_forgotten_lesson(self):
        """Why the sidecar, and not r3's carry-it-in-the-live-file.

        r3 preserved unparseable rows by appending them back, which made them
        immortal: no code path could remove one — not forget_lesson, not GC,
        not even a `lambda L: []` mutate. Worse, a pre-schema shadow row
        sharing a lesson_id with a forgotten lesson would come back to life
        the moment a future version could parse it, inverting forget_lesson's
        "forgetting is final" (r4 Expert QA, reproduced).
        """
        from knowledge_web import forget_lesson
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lessons.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"lesson_id": "L1", "task_type": "research", "outcome": "done",
             "lesson": "the current text", "source_goal": "g",
             "confidence": 0.5, "tier": "medium", "score": 1.0,
             "last_reinforced": "2026-08-01"},
            {"lesson_id": "L1", "lesson": "FORGOTTEN 2024 text",
             "confidence": 0.5},
        ]) + "\n", encoding="utf-8")
        assert forget_lesson("L1") is True
        assert self._rows() == [], "the live store must hold no L1 row"
        assert [r["lesson"] for r in self._sidecar()] == ["FORGOTTEN 2024 text"]

    def test_the_sidecar_accumulates_and_the_live_file_shrinks(self):
        """Idempotence is a property of BOTH halves of the move.

        Neither half is observable through `record_tiered_lesson`, because the
        rewrite that follows quarantine rebuilds the live file from the parsed
        list anyway — so the mint-level tests above passed with the shrink
        removed and with the sidecar opened "w" (r4 must-detect pass). Skip
        the shrink and the next quarantine re-appends what it already moved,
        duplicating the rows the sidecar exists to preserve exactly once;
        overwrite instead of append and a second drift event silently erases
        the first, which is the deletion this whole mechanism was built to
        stop. Drive the helper directly, where its own contract lives.
        """
        from knowledge_web import _quarantine_unparseable, _tiered_lessons_path
        text = "Retries against a bot-challenged endpoint never recover"
        self._seed_drift(text)
        p = _tiered_lessons_path("medium")
        assert _quarantine_unparseable(p) == 1
        assert [r.get("lesson_id") for r in self._rows()] == ["L-good"], \
            "the live file still carries the quarantined row"
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"lesson_id": "L-drift2",
                                 "lesson": "a later drift"}) + "\n")
        assert _quarantine_unparseable(p) == 1
        assert [r.get("lesson_id") for r in self._sidecar()] == \
            ["L-drift", "L-drift2"]

    def test_the_sidecar_count_is_reportable(self):
        # Invisible-and-unreported is how the pre-r3 silent deletion went
        # unnoticed; the census puts this number in front of an operator.
        from knowledge_web import record_tiered_lesson, unparseable_sidecar_count
        text = "Retries against a bot-challenged endpoint never recover"
        self._seed_drift(text)
        assert unparseable_sidecar_count("medium") == 0
        record_tiered_lesson(text, "research", "done", "goal two")
        assert unparseable_sidecar_count("medium") == 1

    def test_a_string_int_field_does_not_wedge_the_decay_cycle(self):
        """The field-generic version of the score wedge (r4 design lens).

        `sessions_validated` is compared against PROMOTE_MIN_SESSIONS inside
        run_decay_cycle's promotion loop with no enclosing try, so one row
        with `"sessions_validated": "3"` killed promotion AND GC for the tier
        on every cycle, forever — and the row parses cleanly, so quarantine
        would never have caught it either.
        """
        from knowledge_web import run_decay_cycle
        d = self.ws / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        base = {"task_type": "research", "outcome": "done", "source_goal": "g",
                "confidence": 0.5, "tier": "medium", "score": 9.9,
                "last_reinforced": "2026-08-16"}
        (d / "lessons.jsonl").write_text("\n".join(json.dumps(r) for r in [
            dict(base, lesson_id="L-good", lesson="fine", sessions_validated=5),
            dict(base, lesson_id="L-sv", lesson="string", sessions_validated="3"),
        ]) + "\n", encoding="utf-8")
        out = run_decay_cycle(tier="medium", dry_run=True)
        assert out["promoted"] == 2, out

    def test_reinforce_without_a_stamp_leaves_the_row_alone(self):
        from knowledge_web import load_tiered_lessons, record_tiered_lesson
        text = "Retries against a bot-challenged endpoint never recover"
        record_tiered_lesson(text, "research", "done", "goal one", scope="world")
        record_tiered_lesson(text, "research", "done", "goal two")
        assert load_tiered_lessons("medium", min_score=0.0)[0].scope == "world"


class TestCensusRollup:
    """The stamp's only consumer: the §14a slice-1 census cross-tab."""

    def _rollup(self, rows):
        from camera_readout import _scope_rollup
        return _scope_rollup(rows)

    def _row(self, lid, scope, s=0, f=0, cites=1, foreign=0):
        return {"lesson_id": lid, "scope": scope, "cites": cites,
                "foreign": foreign, "foreign_s": s, "foreign_f": f}

    def test_buckets_by_stamp(self):
        out = self._rollup([self._row("a", "method"), self._row("b", "world"),
                            self._row("c", "method")])
        assert out["method"]["lessons"] == 2
        assert out["world"]["lessons"] == 1

    def test_unstamped_rows_get_their_own_bucket(self):
        out = self._rollup([self._row("a", "")])
        assert out["unstamped"]["lessons"] == 1
        assert "method" not in out

    def test_pooled_is_pooled_not_mean_of_means(self):
        # One lesson at 5/0 and one at 0/1 pool to beta(5,1)=0.75, whereas
        # averaging per-lesson portabilities would give (0.857+0.333)/2=0.595.
        # Pooling is the point: evidence weight must scale with citations.
        out = self._rollup([self._row("a", "method", s=5),
                            self._row("b", "method", f=1)])
        assert out["method"]["pooled"] == pytest.approx(6 / 8)

    def test_bucket_without_verdicts_reports_insufficient(self):
        assert self._rollup([self._row("a", "world")])["world"]["pooled"] is None

    def test_evidenced_counts_only_rows_at_the_rank_time_bar(self):
        from portability import MIN_FOREIGN_EVIDENCE
        out = self._rollup([
            self._row("a", "method", s=MIN_FOREIGN_EVIDENCE),
            self._row("b", "method", s=MIN_FOREIGN_EVIDENCE - 1)])
        assert out["method"]["evidenced"] == 1

    def test_archived_lessons_keep_their_stamp(self, tmp_path, monkeypatch):
        """Archive rows must carry scope too, not just live-store rows.

        Not hypothetical: measured 2026-08-15, 23 of the 79 resolvable cited
        lesson ids resolved only through the archive fallback, so dropping the
        stamp there would silently unstamp ~29% of the evidence base. (An
        earlier draft of this docstring said "23 of 64" — 64 is the
        portability CACHE entry count, a different denominator. Point-in-time
        figures on a live corpus: treat as measured-then, not evergreen.)
        """
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = tmp_path / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        (mem / "lessons_archive.jsonl").write_text(json.dumps({
            "lesson_id": "arch1", "source_goal": "an old goal",
            "task_type": "research", "scope": "world",
            "lesson": "an archived lesson"}) + "\n", encoding="utf-8")
        import camera_readout
        origins = camera_readout._lesson_origins(warn=lambda *a: None)
        assert origins["arch1"]["scope"] == "world"

    def test_origins_carry_the_imported_flag_from_both_readers(
            self, tmp_path, monkeypatch):
        """The `imported` plumbing, driven for real rather than monkeypatched.

        Every test of the cross-labeller refusal stubs `_lesson_origins`, so
        the two lines that actually READ `imported` off a store row and an
        archive row were uncovered — and the refusal is the only thing
        stopping the readout from pooling two labellers' stamps (broad
        mutation sweep, r4: both survived). Same flag, two readers, one of
        which resolves ~29% of the live evidence base.
        """
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = tmp_path / "memory"
        (mem / "medium").mkdir(parents=True, exist_ok=True)
        (mem / "medium" / "lessons.jsonl").write_text(json.dumps({
            "lesson_id": "live1", "task_type": "research", "outcome": "done",
            "lesson": "a foreign-minted lesson", "source_goal": "g",
            "confidence": 0.5, "tier": "medium", "score": 1.0,
            "last_reinforced": "2026-08-01", "scope": "world",
            "imported": {"imported_from": "mini2"}}) + "\n", encoding="utf-8")
        (mem / "lessons_archive.jsonl").write_text(json.dumps({
            "lesson_id": "arch1", "source_goal": "an old goal",
            "task_type": "research", "scope": "world",
            "lesson": "an archived foreign lesson",
            "imported": {"imported_from": "mini2"}}) + "\n", encoding="utf-8")
        import camera_readout
        origins = camera_readout._lesson_origins(warn=lambda *a: None)
        assert origins["live1"]["imported"] is True
        assert origins["arch1"]["imported"] is True

    def test_evidence_bar_follows_the_rank_time_constant(self, monkeypatch):
        """`evidenced` must track ranking's bar, not a value frozen at import.

        r1 review: the bar was aliased with `from portability import CONST`,
        which snapshots. A census that claims to mirror ranking has to keep
        mirroring it when ranking moves.
        """
        import portability
        from camera_readout import _scope_rollup
        row = self._row("a", "method", s=4)
        assert _scope_rollup([row])["method"]["evidenced"] == 1
        monkeypatch.setattr(portability, "MIN_FOREIGN_EVIDENCE", 10)
        assert _scope_rollup([row])["method"]["evidenced"] == 0

    def test_census_row_carries_the_stamp(self, tmp_path, monkeypatch):
        """End of the pipe: a stamped store row surfaces in census output."""
        import camera_readout
        monkeypatch.setattr(camera_readout, "_lesson_origins", lambda warn=None: {
            "L1": {"source_goal": "origin goal", "task_type": "research",
                   "scope": "world", "preview": "p"}})
        run = tmp_path / "run1"
        run.mkdir()
        (run / "metadata.json").write_text(json.dumps(
            {"project": "other-project", "prompt": "a different goal",
             "goal_achieved": True}), encoding="utf-8")
        c = camera_readout.portability_census(
            [{"dir": run, "frames": [{"chosen": {"lesson_ids": ["L1"]}}]}],
            warn=lambda *a: None)
        assert c["rows"][0]["scope"] == "world"
        assert c["by_scope"]["world"]["foreign_s"] == 1


class TestReadoutHonesty:
    """What the printed census SAYS about its own evidence (§14a slice 3 r1).

    The rollup can be arithmetically perfect and still mislead, so the
    messages are pinned like behavior: each state must claim only what it
    actually knows.
    """

    def _run(self, capsys, monkeypatch, tmp_path, cited):
        """Drive the real print path over a synthetic corpus.

        `cited` is a list of (lesson_id, scope, achieved, n_citations); each
        lesson is cited from `n_citations` distinct foreign runs, all carrying
        verdict `achieved`. Citation COUNT is what clears the evidence bar, so
        the helper has to model it — one citation per lesson never evidences
        anything.
        """
        import camera_readout
        origins = {c[0]: {"source_goal": f"origin of {c[0]}",
                          "task_type": "research", "scope": c[1],
                          "preview": f"preview {c[0]}",
                          # 5th element, optional: minted on another machine.
                          "imported": ({"imported_from": "mini2"}
                                       if len(c) > 4 and c[4] else {})}
                   for c in cited}
        monkeypatch.setattr(camera_readout, "_lesson_origins",
                            lambda warn=None: origins)
        per_run = []
        for lid, _scope, achieved, n in ((c[0], c[1], c[2], c[3]) for c in cited):
            for i in range(n):
                d = tmp_path / f"run-{lid}-{i}"
                d.mkdir(exist_ok=True)
                (d / "metadata.json").write_text(json.dumps(
                    {"project": "a-different-project",
                     "prompt": f"foreign goal {lid}-{i}",
                     "goal_achieved": achieved}), encoding="utf-8")
                per_run.append({"dir": d,
                                "frames": [{"chosen": {"lesson_ids": [lid]}}]})
        camera_readout._print_portability(per_run)
        return capsys.readouterr().out

    def test_unresolvable_corpus_does_not_blame_the_stamp(
            self, capsys, monkeypatch, tmp_path):
        # Zero resolvable rows also yields zero stamped rows, so the
        # "predates the stamp" line used to print for what may be a
        # resolution bug.
        import camera_readout
        monkeypatch.setattr(camera_readout, "_lesson_origins", lambda warn=None: {})
        d = tmp_path / "r"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps(
            {"project": "p", "prompt": "g", "goal_achieved": True}),
            encoding="utf-8")
        camera_readout._print_portability(
            [{"dir": d, "frames": [{"chosen": {"lesson_ids": ["ghost"]}}]}])
        out = capsys.readouterr().out
        assert "predates the stamp" not in out
        assert "no resolvable cited lessons at all" in out

    def test_all_legacy_rows_says_by_construction(
            self, capsys, monkeypatch, tmp_path):
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("L1", "", True, 4), ("L2", "", False, 4)])
        assert "predates the stamp" in out

    def test_one_sided_evidence_is_not_called_a_comparison(
            self, capsys, monkeypatch, tmp_path):
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, 4), ("W1", "world", True, 1)])
        assert "not a comparison" in out

    def test_thin_two_sided_evidence_is_labelled_a_hint(
            self, capsys, monkeypatch, tmp_path):
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, 4), ("W1", "world", True, 4)])
        assert "read as a hint, not a result" in out

    def test_evidence_above_the_floor_is_called_readable(
            self, capsys, monkeypatch, tmp_path):
        from camera_readout import _SCOPE_COMPARISON_FLOOR as FLOOR
        cited = ([(f"M{i}", "method", True, 4) for i in range(FLOOR)]
                 + [(f"W{i}", "world", True, 4) for i in range(FLOOR)])
        out = self._run(capsys, monkeypatch, tmp_path, cited)
        assert "comparison is readable" in out

    def test_exactly_at_the_floor_is_readable(
            self, capsys, monkeypatch, tmp_path):
        """The stated floor is inclusive — pin the boundary, not just the ends.

        r3 test-audit: the suite exercised thinnest=4 (hint) and thinnest=40
        (readable) and never the boundary, so `<` -> `<=` survived the whole
        related suite while flipping the readout's own headline honesty claim
        at exactly the number it prints.
        """
        from camera_readout import _SCOPE_COMPARISON_FLOOR as FLOOR
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, FLOOR * 2),
                         ("W1", "world", True, FLOOR)])
        assert "comparison is readable" in out
        assert "read as a hint" not in out

    def test_one_under_the_floor_is_only_a_hint(
            self, capsys, monkeypatch, tmp_path):
        from camera_readout import _SCOPE_COMPARISON_FLOOR as FLOOR
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, FLOOR * 2),
                         ("W1", "world", True, FLOOR - 1)])
        assert "read as a hint, not a result" in out
        assert "comparison is readable" not in out

    def test_imported_stamps_block_the_readable_verdict(
            self, capsys, monkeypatch, tmp_path):
        """Stamps are comparable WITHIN a labeller, not across them.

        pack.py claimed the census could separate foreign-minted stamps
        "by construction" because the rows carry `imported`. Nothing read it
        (r3, two lenses) — so a cross-machine mixture would eventually have
        printed "this comparison is readable" over exactly the pooling the
        stamp's own contract forbids.
        """
        from camera_readout import _SCOPE_COMPARISON_FLOOR as FLOOR
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, FLOOR * 2, False),
                         ("W1", "world", True, FLOOR * 2, True)])
        assert "comparable within a labeller" in out
        assert "comparison is readable" not in out

    def test_an_uncited_imported_row_does_not_void_the_verdict(
            self, capsys, monkeypatch, tmp_path):
        """Membership is not contamination.

        r3 suppressed the readable verdict on the mere PRESENCE of an
        imported row in a compared bucket. An imported row with no verdicted
        citations contributes nothing to the pooled figure, so one uncited
        pack row would have voided the §14a headline permanently — the one
        line the whole slice exists to eventually print (r4, two lenses).

        Two shapes of "no verdicted citations", because only the second one
        actually reaches the rollup: I1 is never cited at all, so the census
        builds no row for it and the gate is never consulted (the r4
        must-detect pass caught this test passing vacuously). I2 IS cited,
        four times, from foreign runs that carry no verdict — a row in the
        bucket, counted as imported, contributing zero to the pooled figure.
        That is the case the `imported_fv` gate exists for.
        """
        from camera_readout import _SCOPE_COMPARISON_FLOOR as FLOOR
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, FLOOR * 2, False),
                         ("W1", "world", True, FLOOR * 2, False),
                         ("I1", "method", True, 0, True),
                         ("I2", "method", None, 4, True)])
        assert "comparison is readable" in out
        assert "comparable within a labeller" not in out

    def test_all_local_stamps_still_read_as_a_comparison(
            self, capsys, monkeypatch, tmp_path):
        # The above must gate on `imported`, not merely suppress the verdict.
        from camera_readout import _SCOPE_COMPARISON_FLOOR as FLOOR
        out = self._run(capsys, monkeypatch, tmp_path,
                        [("M1", "method", True, FLOOR * 2, False),
                         ("W1", "world", True, FLOOR * 2, False)])
        assert "comparison is readable" in out
        assert "comparable within a labeller" not in out


class TestStampCoverage:
    """Is the stamp landing at all? (r3 Expert QA.)

    Every other scope number in the readout is citation-gated, and a fresh
    mint needs days-to-weeks of foreign verdicted citations. So a totally
    dead write path printed the same reassuring "predates the stamp … by
    construction" line as a healthy one. This reads the store directly.
    """

    @pytest.fixture(autouse=True)
    def _ws(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        self.d = tmp_path / "memory" / "medium"
        self.d.mkdir(parents=True)

    def _write(self, *scopes):
        base = {"task_type": "research", "outcome": "done", "source_goal": "g",
                "confidence": 0.5, "tier": "medium", "score": 1.0,
                "last_reinforced": "2026-08-01"}
        rows = []
        for i, sc in enumerate(scopes):
            r = dict(base, lesson_id=f"L{i}", lesson=f"lesson {i}",
                     recorded_at=f"2026-08-1{i}T00:00:00+00:00")
            if sc is not None:
                r["scope"] = sc
            rows.append(r)
        (self.d / "lessons.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_counts_the_whole_store_not_the_citation_join(self):
        from camera_readout import _stamp_coverage
        self._write("method", "method", "world", None, "banana")
        cov = _stamp_coverage()
        assert (cov["total"], cov["method"], cov["world"], cov["malformed"]) \
            == (5, 2, 1, 1)

    def test_the_denominator_is_the_file_not_the_loader(self):
        """"Whole store" has to mean the bytes on disk.

        The loader skips unparseable rows, so counting its output let a store
        that is mostly unreadable print "100% stamped" (r4 Expert QA).
        """
        from camera_readout import _stamp_coverage
        self._write("method")
        with (self.d / "lessons.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"lesson_id": "drift", "lesson": "x"}) + "\n")
            fh.write(json.dumps({"lesson_id": "drift2", "lesson": "y"}) + "\n")
        cov = _stamp_coverage()
        assert cov["total"] == 3, cov
        assert cov["readable"] == 1, cov

    def test_an_unreadable_store_reports_an_error(self):
        """The UNREADABLE branch was dead code before this.

        load_tiered_lessons swallows every read failure and returns [], so a
        chmod-000 store was indistinguishable from a fresh empty workspace —
        in the one instrument built to tell working from dead (r4 skeptic).
        """
        import os
        from camera_readout import _stamp_coverage
        self._write("method", "world")
        p = self.d / "lessons.jsonl"
        os.chmod(p, 0o000)
        try:
            cov = _stamp_coverage()
        finally:
            os.chmod(p, 0o644)
        assert cov.get("error"), cov

    def test_a_loader_that_returns_nothing_for_a_full_store_is_an_error(
            self, monkeypatch):
        """The chmod test above is caught by the OUTER guard, not this branch.

        A store the process cannot read at all raises in the line-count read
        and lands in `error` either way, so it never exercised the
        loader-disagrees check (r4 must-detect pass). The branch exists for
        the harder case: bytes on disk that read fine, and a loader that
        swallowed something internally and returned []. That is exactly how
        the pre-r4 instrument printed "0 rows" over a full store.
        """
        import knowledge_web
        from camera_readout import _stamp_coverage
        self._write("method", "world")
        monkeypatch.setattr(knowledge_web, "load_tiered_lessons",
                            lambda *a, **k: [])
        cov = _stamp_coverage()
        assert "loader returned none" in cov.get("error", ""), cov

    def test_imported_stamps_are_not_counted_as_a_live_local_writer(self):
        """BACKLOG reads this counter as "did the PRODUCTION lane stamp?".

        An imported row proves another machine's writer fired, not ours, so
        pooling them would silence the dead-writer alarm on a pack import
        (r4 design lens).
        """
        from camera_readout import _stamp_coverage
        base = {"task_type": "research", "outcome": "done", "source_goal": "g",
                "confidence": 0.5, "tier": "medium", "score": 1.0,
                "last_reinforced": "2026-08-01"}
        (self.d / "lessons.jsonl").write_text(json.dumps(
            dict(base, lesson_id="i1", lesson="foreign", scope="method",
                 imported={"imported_from": "mini2"})) + "\n",
            encoding="utf-8")
        cov = _stamp_coverage()
        assert cov["method"] == 1 and cov["imported"] == 1, cov

    def test_a_pack_import_does_not_silence_the_dead_writer_alarm(self, capsys):
        import camera_readout
        base = {"task_type": "research", "outcome": "done", "source_goal": "g",
                "confidence": 0.5, "tier": "medium", "score": 1.0,
                "last_reinforced": "2026-08-01"}
        (self.d / "lessons.jsonl").write_text(json.dumps(
            dict(base, lesson_id="i1", lesson="foreign", scope="method",
                 imported={"imported_from": "mini2"})) + "\n",
            encoding="utf-8")
        camera_readout._print_portability([])
        out = capsys.readouterr().out
        assert "ZERO locally-minted stamps store-wide" in out
        assert "IMPORTED" in out

    def test_a_third_scope_value_does_not_break_the_instrument(self,
                                                               monkeypatch):
        """The counter is vocabulary-driven, not two hard-coded keys.

        The mirror tripwire permits growing _LESSON_SCOPES; hard-coded
        out["method"]/out["world"] turned that into a KeyError swallowed as
        UNREADABLE, discarding the counts it had already made (r4 Expert QA).
        """
        import camera_readout, knowledge_web
        monkeypatch.setattr(knowledge_web, "_LESSON_SCOPES",
                            frozenset({"method", "world", "tool"}))
        monkeypatch.setattr(camera_readout, "_LESSON_SCOPES",
                            frozenset({"method", "world", "tool"}))
        self._write("method", "tool")
        cov = camera_readout._stamp_coverage()
        assert not cov.get("error"), cov
        assert cov["method"] == 1 and cov.get("tool") == 1, cov

    def test_newest_stamped_mint_is_reported(self):
        # The NEWEST STAMPED row, not the newest row — the unstamped one is
        # deliberately the most recent here, because the figure's whole job is
        # to answer "when did the writer last fire?" (broad sweep, r4: the
        # earlier fixture ordering let "newest over all rows" survive).
        from camera_readout import _stamp_coverage
        self._write("method", "world", None)
        assert _stamp_coverage()["newest"].startswith("2026-08-11")

    def test_the_long_tier_is_counted_too(self):
        """"Whole store" means both tiers.

        Every coverage fixture writes to medium, so scanning medium only
        survived the suite — and LONG is where promoted lessons live, i.e.
        exactly the rows an operator would ask about first (broad sweep, r4).
        """
        from camera_readout import _stamp_coverage
        long_d = self.d.parent / "long"
        long_d.mkdir(parents=True, exist_ok=True)
        (long_d / "lessons.jsonl").write_text(json.dumps({
            "lesson_id": "Lg", "task_type": "research", "outcome": "done",
            "lesson": "a promoted lesson", "source_goal": "g",
            "confidence": 0.5, "tier": "long", "score": 1.0,
            "last_reinforced": "2026-08-01", "scope": "world"}) + "\n",
            encoding="utf-8")
        self._write("method")
        cov = _stamp_coverage()
        assert (cov["total"], cov["method"], cov["world"]) == (2, 1, 1), cov

    def test_the_coverage_read_is_not_paged(self):
        """`load_tiered_lessons` defaults to limit=50.

        Drop the explicit `limit=None` and the store-wide census silently
        becomes a census of the top 50 rows by score — reporting a number
        smaller than the truth in exactly the direction that looks like a
        broken writer (broad sweep, r4). The live store is already 195 rows.
        """
        from camera_readout import _stamp_coverage
        self._write(*["method"] * 60)
        cov = _stamp_coverage()
        assert (cov["total"], cov["readable"], cov["method"]) == (60, 60, 60), cov

    def test_a_world_only_store_is_not_called_a_dead_writer(self, capsys):
        # `stamped` sums the VOCABULARY; counting method alone would raise the
        # alarm over a store the writer had stamped perfectly (broad sweep, r4).
        import camera_readout
        self._write("world", "world")
        camera_readout._print_portability([])
        out = capsys.readouterr().out
        assert "2/2 rows stamped" in out
        assert "ZERO locally-minted stamps" not in out

    def test_a_dead_write_path_says_so_in_words(self, capsys):
        """The whole point: this must NOT read like the healthy-but-early case."""
        import camera_readout
        self._write(None, None, None)
        camera_readout._print_portability([])
        out = capsys.readouterr().out
        assert "0/3 rows stamped" in out
        assert "ZERO locally-minted stamps store-wide" in out

    def test_a_working_write_path_does_not_raise_the_alarm(self, capsys):
        import camera_readout
        self._write("method", None, None)
        camera_readout._print_portability([])
        out = capsys.readouterr().out
        assert "1/3 rows stamped" in out
        assert "ZERO locally-minted stamps" not in out

    def test_an_empty_store_is_not_an_alarm(self, capsys):
        # A fresh install has nothing to stamp; that is not a broken writer.
        import camera_readout
        self._write()
        camera_readout._print_portability([])
        assert "ZERO stamped rows" not in capsys.readouterr().out

    def test_an_unreadable_store_is_named_not_swallowed(self, capsys,
                                                        monkeypatch):
        import camera_readout, knowledge_web

        def boom(*a, **k):
            raise RuntimeError("tier read exploded")

        monkeypatch.setattr(knowledge_web, "load_tiered_lessons", boom)
        camera_readout._print_portability([])
        out = capsys.readouterr().out
        assert "stamp coverage: UNREADABLE" in out
        assert "tier read exploded" in out


class TestForgedStoreRows:
    """Sad paths from r2 Expert QA: the store is JSON and nothing validates it
    on READ, so a hand-edited or corrupted row can carry any decodable type.

    Each of these crashed a different consumer before the fix — and one of
    them wedged the rank-time portability cache workspace-wide, silently,
    because `_scope_rollup` sits inside `refresh_cache`'s return path and the
    failure was swallowed below the default log threshold.
    """

    @pytest.mark.parametrize("bad", ["weird", "  method  ", "World",
                                     "мethod", 123, True, ["world"],
                                     {"s": 1}])
    def test_rollup_survives_any_forged_scope(self, bad):
        from camera_readout import _scope_rollup
        out = _scope_rollup([{"lesson_id": "x", "scope": bad, "cites": 1,
                              "foreign": 1, "foreign_s": 1, "foreign_f": 0}])
        assert set(out) == {"unstamped"}, f"{bad!r} minted its own bucket"
        assert out["unstamped"]["malformed"] == 1

    def test_malformed_flag_beside_a_valid_scope_does_not_crash(self):
        """The row shape the 8-case parametrize could not reach.

        `scope_malformed` is read for direct callers, and a caller that sets
        it next to a VALID scope creates no "unstamped" bucket to report it
        on — so the report line indexed a key that wasn't there. Same
        instrument-must-never-crash class as the `both[0]` guard.
        """
        from camera_readout import _scope_rollup
        out = _scope_rollup([{"lesson_id": "x", "scope": "method",
                              "scope_malformed": True, "cites": 1,
                              "foreign": 1, "foreign_s": 1, "foreign_f": 0}])
        assert out["method"]["lessons"] == 1
        assert out["unstamped"]["malformed"] == 1

    def test_falsy_corruption_is_reported_through_the_live_store(
            self, tmp_path, monkeypatch, capsys):
        """`or ""` upstream made the malformed detector blind to falsy junk.

        The r2 tests injected past `_lesson_origins`, so they never crossed
        the `getattr(tl, "scope", "") or ""` that flattened False/0/[]/{} to
        "" before the screen ran. Those shapes reported as honestly-unstamped
        and the WARNING could not fire — which made r2's "0 malformed" live
        claim really "0 among the truthy shapes" (r3 skeptic).
        """
        import camera_readout
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = tmp_path / "memory" / "medium"
        mem.mkdir(parents=True)
        mem.joinpath("lessons.jsonl").write_text(json.dumps({
            "lesson_id": "L1", "task_type": "research", "outcome": "done",
            "lesson": "a lesson", "source_goal": "origin goal",
            "confidence": 0.5, "tier": "medium", "score": 1.0,
            "last_reinforced": "2026-08-01", "scope": []}) + "\n",
            encoding="utf-8")
        per_run = []
        for i in range(4):
            d = tmp_path / f"r{i}"
            d.mkdir()
            (d / "metadata.json").write_text(json.dumps(
                {"project": "other", "prompt": f"foreign {i}",
                 "goal_achieved": True}), encoding="utf-8")
            per_run.append({"dir": d,
                            "frames": [{"chosen": {"lesson_ids": ["L1"]}}]})
        camera_readout._print_portability(per_run)
        out = capsys.readouterr().out
        assert "outside {method, world}" in out

    def test_null_scope_is_unstamped_not_malformed(self):
        from camera_readout import _scope_rollup
        out = _scope_rollup([{"lesson_id": "x", "scope": None, "cites": 1,
                              "foreign": 0, "foreign_s": 0, "foreign_f": 0}])
        assert out["unstamped"]["malformed"] == 0

    @pytest.mark.parametrize("bad", ["weird", 123, ["world"], {"s": 1}])
    def test_readout_does_not_crash_on_a_forged_scope(self, bad, capsys,
                                                      monkeypatch, tmp_path):
        """The whole --portability CLI died on these before the fix.

        Two distinct crashes, which is why the row is sanitized at build time
        AND the rollup validates again: a non-str scope killed the per-row
        print's `:7s` format before the rollup ever ran, and an unhashable one
        killed the rollup's bucket key.
        """
        import camera_readout
        monkeypatch.setattr(camera_readout, "_lesson_origins", lambda warn=None: {
            "L1": {"source_goal": "origin goal", "task_type": "research",
                   "scope": bad, "preview": "p"}})
        runs = []
        for i in range(4):
            d = tmp_path / f"r{i}"
            d.mkdir()
            (d / "metadata.json").write_text(json.dumps(
                {"project": "other", "prompt": f"foreign {i}",
                 "goal_achieved": True}), encoding="utf-8")
            runs.append({"dir": d, "frames": [{"chosen": {"lesson_ids": ["L1"]}}]})
        camera_readout._print_portability(runs)
        out = capsys.readouterr().out
        assert "WARNING" in out and "outside {method, world}" in out

    def test_readout_names_evidence_under_an_unrecognized_scope(
            self, capsys, monkeypatch):
        """The belt to the read-site validation's suspenders.

        `stamped` counts every non-unstamped bucket, so a scope category the
        print branches don't know about makes `both` empty — and the old
        `both[0]` index took the whole instrument down with an IndexError.
        Unreachable through today's write paths by construction; pinned
        anyway, because "the instrument crashed" is the one failure mode an
        instrument may never have.
        """
        import camera_readout
        # One resolvable row so the print gets past the no-rows branch and
        # reaches the evidence branches, where the rogue bucket lives.
        monkeypatch.setattr(camera_readout, "portability_census",
                            lambda per_run, warn=None: {
                                "rows": [{"lesson_id": "L1", "preview": "p",
                                          "scope": "", "cites": 4, "runs": 4,
                                          "home": 0, "foreign": 4,
                                          "foreign_s": 4, "foreign_f": 0,
                                          "foreign_unjudged": 0,
                                          "unattributable": 0,
                                          "portability": 0.83}],
                                "unresolved": {}, "runs_no_meta": 0,
                                "by_scope": {"banana": {
                                    "lessons": 1, "cites": 4, "foreign": 4,
                                    "foreign_s": 4, "foreign_f": 0,
                                    "evidenced": 1, "pooled": 0.83,
                                    "malformed": 0}}})
        camera_readout._print_portability([])
        out = capsys.readouterr().out
        assert "no recognized scope" in out

    def test_refresh_failure_is_logged_at_warning(self, monkeypatch, caplog):
        """A refresh that fails every finalize must not be silent.

        It rides loop finalize and feeds RANK-TIME weighting, so at debug
        level the operator's only symptom was weighting that quietly stopped
        updating — no error, no output, indefinitely.
        """
        import portability, camera_readout

        def boom(*a, **k):
            raise RuntimeError("forged store row")

        monkeypatch.setattr(camera_readout, "portability_census", boom)
        with caplog.at_level(logging.WARNING, logger="portability"):
            assert portability.refresh_cache() == -1
        # Name AND message: caplog captures the whole root tree, so a bare
        # "something warned" assertion passed with the fix reverted as soon as
        # any unrelated logger warned on the same path (r3 test-audit).
        assert any(r.name == "portability"
                   and "cache refresh failed" in r.getMessage()
                   and r.levelno >= logging.WARNING
                   for r in caplog.records), [
                       (r.name, r.levelname, r.getMessage())
                       for r in caplog.records]

    def test_refresh_cache_survives_a_forged_row(self, tmp_path, monkeypatch):
        """The HIGH one: a single bad row stopped the RANKING cache refreshing.

        `_scope_rollup` runs inside portability_census's return expression, so
        its crash discarded the good rows too and the cache never wrote again.
        """
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = tmp_path / "memory" / "medium"
        mem.mkdir(parents=True)
        rows = [{"lesson_id": "good", "task_type": "research", "outcome": "done",
                 "lesson": "a clean lesson", "source_goal": "origin goal",
                 "confidence": 0.5, "tier": "medium", "score": 1.0,
                 "last_reinforced": "2026-08-01", "scope": "method"},
                {"lesson_id": "bad", "task_type": "research", "outcome": "done",
                 "lesson": "a corrupted lesson", "source_goal": "origin goal",
                 "confidence": 0.5, "tier": "medium", "score": 1.0,
                 "last_reinforced": "2026-08-01", "scope": ["world"]}]
        (mem / "lessons.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        runs_root = tmp_path / "runs"
        for i, lid in enumerate(("good", "bad", "good")):
            d = runs_root / f"run{i}" / "source"
            d.mkdir(parents=True)
            (d.parent / "metadata.json").write_text(json.dumps(
                {"project": "other", "prompt": f"foreign goal {i}",
                 "goal_achieved": True}), encoding="utf-8")
            (d / "camera_frames.jsonl").write_text(json.dumps(
                {"chosen": {"lesson_ids": [lid]}}) + "\n", encoding="utf-8")
        import portability
        # refresh_cache takes no args — it resolves runs_root() and
        # memory_dir() itself, both off MARO_WORKSPACE (set above).
        assert runs_root.exists()
        n = portability.refresh_cache()
        assert n >= 0, "refresh returned the -1 failure sentinel"
        assert portability.cache_path().exists(), "cache never written"
        # Read the CONTENT. `n >= 0` plus "a file exists" cannot tell a correct
        # refresh apart from one that swallowed the census crash and wrote an
        # empty cache — and both look identical to the operator, whose only
        # symptom either way is weighting that stops updating (r3 test-audit
        # demonstrated the empty-cache alternative fix passing this test).
        cache = portability.load_cache()
        assert cache.get("good") == {"foreign_s": 2, "foreign_f": 0}, cache
        assert n == 2, f"expected both rows counted, got {n}: {cache}"
