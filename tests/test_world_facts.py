"""World-fact ledger (WORLD_FACTS_DESIGN slice 1, decisions 2026-08-06).

"We need to add non-action types to the planner. I think 2 types of world
facts: anecdotal/accidentally found and hypothesis type findings."

These pin the ledger's behavior, the §7.1 hypothesis quarantine, the
declaration validator's cap discipline, and the checkpoint carry that lets
a resume see the facts, not just the surviving steps.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from world_facts import (  # noqa: E402
    WorldFactLedger, clean_declared,
    KIND_ANECDOTAL, KIND_HYPOTHESIS,
    MAX_FACTS_PER_STEP, MAX_RENDERED_FACTS, MAX_FACT_CHARS,
)


class TestLedger:
    def test_observe_records_and_returns_new(self):
        led = WorldFactLedger()
        assert led.observe(KIND_ANECDOTAL, "archive X is blocked", "403 on fetch", 2)
        f = next(iter(led.facts.values()))
        assert f.fact == "archive X is blocked"
        assert f.evidence == "403 on fetch"
        assert f.first_step == 2

    def test_restated_fact_bumps_hits_not_rows(self):
        led = WorldFactLedger()
        led.observe(KIND_ANECDOTAL, "The dataset has  a second sheet", "", 1)
        # Same fact, different whitespace/case, later step: corroboration.
        assert not led.observe(KIND_ANECDOTAL, "the dataset has a second sheet", "seen again", 3)
        assert len(led.facts) == 1
        f = next(iter(led.facts.values()))
        assert f.hits == 2
        assert f.steps == [1, 3]
        # First evidence wins (terrain's first-reason-wins)
        assert f.evidence == ""

    def test_same_text_different_kind_is_a_different_row(self):
        led = WorldFactLedger()
        led.observe(KIND_ANECDOTAL, "failures cluster by transport", "", 1)
        led.observe(KIND_HYPOTHESIS, "failures cluster by transport", "", 1)
        assert len(led.facts) == 2

    def test_unknown_kind_and_empty_fact_are_dropped(self):
        led = WorldFactLedger()
        assert not led.observe("opinion", "x", "", 1)
        assert not led.observe(KIND_ANECDOTAL, "   ", "", 1)
        assert led.facts == {}


class TestQuarantine:
    """§7.1 (decided 2026-08-06): hypotheses are ledgered, never rendered."""

    def test_render_is_anecdotal_only(self):
        led = WorldFactLedger()
        led.observe(KIND_ANECDOTAL, "source A is paywalled", "hit paywall", 1)
        led.observe(KIND_HYPOTHESIS, "all gov sites will block us", "hunch", 1)
        block = led.render()
        assert "source A is paywalled" in block
        assert "all gov sites" not in block

    def test_hypotheses_only_renders_empty(self):
        """Empty render is load-bearing — zero contributions must leave
        prompts byte-identical."""
        led = WorldFactLedger()
        led.observe(KIND_HYPOTHESIS, "pattern guess", "", 1)
        assert led.render() == ""

    def test_hypotheses_accessor_is_the_slice2_seam(self):
        led = WorldFactLedger()
        led.observe(KIND_HYPOTHESIS, "pattern guess", "", 1)
        assert [f.fact for f in led.hypotheses()] == ["pattern guess"]
        assert led.anecdotal() == []


class TestRender:
    def test_empty_renders_empty(self):
        assert WorldFactLedger().render() == ""

    def test_render_carries_evidence_and_hits(self):
        led = WorldFactLedger()
        led.observe(KIND_ANECDOTAL, "fact one", "the receipt", 1)
        led.observe(KIND_ANECDOTAL, "fact one", "", 4)
        block = led.render()
        assert "the receipt" in block
        assert "2×" in block

    def test_render_is_capped(self):
        led = WorldFactLedger()
        for i in range(MAX_RENDERED_FACTS + 5):
            led.observe(KIND_ANECDOTAL, f"fact number {i}", "", 1)
        block = led.render()
        assert "…and 5 more." in block


class TestCleanDeclared:
    def test_valid_entries_pass(self):
        out = clean_declared([
            {"kind": "anecdotal", "fact": "f1", "evidence": "e1"},
            {"kind": "Hypothesis", "fact": "f2"},
        ])
        assert out == [
            {"kind": "anecdotal", "fact": "f1", "evidence": "e1"},
            {"kind": "hypothesis", "fact": "f2", "evidence": ""},
        ]

    def test_malformed_entries_do_not_consume_the_cap(self):
        """Chunk-3 decisions-directive rule, inherited: cap AFTER validation."""
        raw = ["junk", {"kind": "opinion", "fact": "x"}, {"kind": "anecdotal"}]
        raw += [{"kind": "anecdotal", "fact": f"real {i}"}
                for i in range(MAX_FACTS_PER_STEP)]
        out = clean_declared(raw)
        assert len(out) == MAX_FACTS_PER_STEP
        assert all(e["fact"].startswith("real") for e in out)

    def test_cap_applies(self):
        raw = [{"kind": "anecdotal", "fact": f"f{i}"} for i in range(10)]
        assert len(clean_declared(raw)) == MAX_FACTS_PER_STEP

    def test_non_list_input_is_empty(self):
        assert clean_declared(None) == []
        assert clean_declared({"kind": "anecdotal", "fact": "x"}) == []

    def test_char_caps(self):
        out = clean_declared([{"kind": "anecdotal", "fact": "x" * 1000}])
        assert len(out[0]["fact"]) == MAX_FACT_CHARS


class TestCheckpointCarry:
    def test_ledger_round_trips_through_lists(self):
        led = WorldFactLedger()
        led.observe(KIND_ANECDOTAL, "fact A", "evidence A", 1)
        led.observe(KIND_HYPOTHESIS, "guess B", "", 2)
        led.observe(KIND_ANECDOTAL, "fact A", "", 3)
        back = WorldFactLedger.from_list(led.to_list())
        assert back.facts.keys() == led.facts.keys()
        f = next(f for f in back.facts.values() if f.kind == KIND_ANECDOTAL)
        assert f.hits == 2 and f.steps == [1, 3] and f.evidence == "evidence A"

    def test_from_list_drops_malformed_rows(self):
        """A corrupt checkpoint must not block a resume."""
        back = WorldFactLedger.from_list([
            "junk", {"kind": "opinion", "fact": "x"}, {"kind": "anecdotal"},
            {"kind": "anecdotal", "fact": "good", "steps": ["bad", 2]},
            None,
        ])
        assert len(back.facts) == 1
        assert next(iter(back.facts.values())).steps == [2]

    def test_checkpoint_persists_and_restores_world_facts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from checkpoint import write_checkpoint, load_checkpoint
        led = WorldFactLedger()
        led.observe(KIND_ANECDOTAL, "archive X is blocked", "403", 1)
        write_checkpoint("wfloop01", "goal", "", ["step 1", "step 2"], [],
                         world_facts=led.to_list())
        ckpt = load_checkpoint("wfloop01")
        assert ckpt is not None
        restored = WorldFactLedger.from_list(ckpt.world_facts)
        assert "archive X is blocked" in restored.render()

    def test_checkpoint_without_facts_omits_the_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from checkpoint import write_checkpoint, load_checkpoint
        write_checkpoint("wfloop02", "goal", "", ["step 1"], [])
        ckpt = load_checkpoint("wfloop02")
        assert ckpt.world_facts is None


class TestLoopWiring:
    def test_context_carries_a_ledger(self):
        from loop_types import LoopContext
        ctx = LoopContext()
        assert isinstance(ctx.world_facts, WorldFactLedger)
        assert ctx.world_facts.render() == ""

    def test_each_context_gets_its_own(self):
        from loop_types import LoopContext
        a, b = LoopContext(), LoopContext()
        a.world_facts.observe(KIND_ANECDOTAL, "leak probe", "", 1)
        assert b.world_facts.facts == {}

    def test_record_step_world_facts_feeds_the_ledger(self):
        from loop_types import LoopContext
        from loop_post_step import record_step_world_facts
        ctx = LoopContext()
        outcome = {"world_facts": [
            {"kind": "anecdotal", "fact": "fact A", "evidence": "e"},
            {"kind": "hypothesis", "fact": "guess B", "evidence": ""},
            "junk",
        ]}
        record_step_world_facts(ctx, "3", outcome)
        assert len(ctx.world_facts.facts) == 2
        assert next(iter(ctx.world_facts.anecdotal())).first_step == 3

    def test_record_step_world_facts_never_raises(self):
        from loop_post_step import record_step_world_facts

        class _NoLedger:
            world_facts = None
        record_step_world_facts(_NoLedger(), "x", {"world_facts": [{"kind": "anecdotal", "fact": "f"}]})

    def test_contribution_is_replaced_not_stacked(self):
        """Same drop/re-render discipline as terrain — a re-arm must not
        replay a stale snapshot."""
        from loop_types import ContributionLedger
        led = ContributionLedger()
        m = WorldFactLedger()
        m.observe(KIND_ANECDOTAL, "fact A", "", 1)
        for _ in range(3):
            led.drop_source("world_facts")
            led.append("world_facts", "context", m.render())
        assert len([r for r in led.drain() if r.source == "world_facts"]) == 1
