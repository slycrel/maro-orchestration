"""Chunk-4 (2026-07-21): contradiction wiring — the writer chain that makes
the contested→refight lifecycle reachable.

Emitter: memory_ledger.stamp_outcome_verdict → CONTRADICTION_CANDIDATE when a
FULL-trust goal_achieved=False verdict lands on a run whose recall wrote
citations (source/recall_citations.json). Adjudicator: knowledge_lens.
adjudicate_contradiction_candidates renders a capped tri-state verdict; only
an exact "yes" mutates via contradict_pattern.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runs as runs_module
from memory_ledger import (
    _memory_dir,
    record_outcome,
    stamp_outcome_verdict,
)
from knowledge_lens import (
    adjudicate_contradiction_candidates,
    contested_rules,
    load_standing_rules,
    observe_pattern,
    standing_rules_with_ids,
    inject_standing_rules,
)
from captains_log import (
    log_event,
    CONTRADICTION_CANDIDATE,
    CONTRADICTION_ADJUDICATED,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _events(event_type: str):
    path = _memory_dir() / "captains_log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event_type") == event_type
    ]


def _seed_run_citations(monkeypatch, tmp_path, *, rule_ids=None,
                        lesson_ids=None, write_file=True):
    """Durable loop_id→run-dir join (review F6): the emitter resolves the
    STAMPED loop's dir via runs.resolve_run_dir, never the ambient
    ContextVar — so these tests patch the resolver."""
    run_dir = tmp_path / "runs" / "test-run"
    (run_dir / "source").mkdir(parents=True, exist_ok=True)
    if write_file:
        (run_dir / "source" / "recall_citations.json").write_text(json.dumps({
            "rule_ids": rule_ids or [],
            "lesson_ids": lesson_ids or [],
            "goal_preview": "test goal",
            "project": "test-project",
        }))
    monkeypatch.setattr(runs_module, "resolve_run_dir", lambda ref: run_dir)
    return run_dir


# ---------------------------------------------------------------------------
# Emitter: stamp_outcome_verdict → CONTRADICTION_CANDIDATE
# ---------------------------------------------------------------------------


class TestCandidateEmitter:
    def test_full_trust_failure_with_citations_emits_candidate(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _seed_run_citations(monkeypatch, tmp_path,
                            rule_ids=["r-1"], lesson_ids=["l-9"])
        record_outcome("goal", "done", "it failed", loop_id="lp-c1")
        assert stamp_outcome_verdict(
            "lp-c1", goal_achieved=False, goal_verdict_source="closure",
            goal_verdict_confidence=0.9,
        ).status == "updated"
        events = _events(CONTRADICTION_CANDIDATE)
        assert len(events) == 1
        ev = events[0]
        assert ev["subject"] == "lp-c1"
        assert ev["context"]["rule_ids"] == ["r-1"]
        assert ev["context"]["lesson_ids"] == ["l-9"]
        assert "rule:r-1" in ev.get("related_ids", [])

    def test_directional_failure_never_emits(self, monkeypatch, tmp_path):
        """Era-10 law pin: verdicts are consumed only through verdict_trust.
        A low-confidence False is directional — it may flavor, never gate —
        so it must not seed a contradiction against a standing rule."""
        _setup(monkeypatch, tmp_path)
        _seed_run_citations(monkeypatch, tmp_path, rule_ids=["r-1"])
        record_outcome("goal", "done", "s", loop_id="lp-c2")
        stamp_outcome_verdict(
            "lp-c2", goal_achieved=False, goal_verdict_source="closure",
            goal_verdict_confidence=0.4,
        )
        assert _events(CONTRADICTION_CANDIDATE) == []

    def test_achieved_run_never_emits(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _seed_run_citations(monkeypatch, tmp_path, rule_ids=["r-1"])
        record_outcome("goal", "done", "s", loop_id="lp-c3")
        stamp_outcome_verdict(
            "lp-c3", goal_achieved=True, goal_verdict_source="closure",
            goal_verdict_confidence=0.9,
        )
        assert _events(CONTRADICTION_CANDIDATE) == []

    def test_no_citations_file_no_event(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _seed_run_citations(monkeypatch, tmp_path, write_file=False)
        record_outcome("goal", "done", "s", loop_id="lp-c4")
        stamp_outcome_verdict(
            "lp-c4", goal_achieved=False, goal_verdict_source="closure",
            goal_verdict_confidence=0.9,
        )
        assert _events(CONTRADICTION_CANDIDATE) == []

    def test_empty_citations_no_event(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _seed_run_citations(monkeypatch, tmp_path, rule_ids=[], lesson_ids=[])
        record_outcome("goal", "done", "s", loop_id="lp-c5")
        stamp_outcome_verdict(
            "lp-c5", goal_achieved=False, goal_verdict_source="closure",
            goal_verdict_confidence=0.9,
        )
        assert _events(CONTRADICTION_CANDIDATE) == []

    def test_no_run_dir_degrades_gracefully(self, monkeypatch, tmp_path):
        """A loop the run index can't resolve — the stamp must still land
        and no event fires."""
        _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(runs_module, "resolve_run_dir", lambda ref: None)
        record_outcome("goal", "done", "s", loop_id="lp-c6")
        assert stamp_outcome_verdict(
            "lp-c6", goal_achieved=False, goal_verdict_source="closure",
            goal_verdict_confidence=0.9,
        ).status == "updated"
        assert _events(CONTRADICTION_CANDIDATE) == []

    def test_deterministic_no_confidence_verdict_emits(
            self, monkeypatch, tmp_path):
        """A judged verdict with no confidence (deterministic provenance
        guard) is authoritative — verdict_trust says FULL, so it emits."""
        _setup(monkeypatch, tmp_path)
        _seed_run_citations(monkeypatch, tmp_path, rule_ids=["r-1"])
        record_outcome("goal", "done", "s", loop_id="lp-c7")
        stamp_outcome_verdict(
            "lp-c7", goal_achieved=False, goal_verdict_source="provenance",
        )
        assert len(_events(CONTRADICTION_CANDIDATE)) == 1


# ---------------------------------------------------------------------------
# Adjudicator: tri-state verdict, cap, dedup, retriable parse failures
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Scripted adapter — returns each payload once, in order."""

    def __init__(self, *payloads: str):
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        content = self.payloads.pop(0) if self.payloads else ""
        return types.SimpleNamespace(content=content)


def _promote_rule(text="Always fetch via Jina."):
    observe_pattern(text, "agenda")
    rule = observe_pattern(text, "agenda")
    assert rule is not None
    return rule


def _seed_candidate(loop_id, *, rule_ids=None, lesson_ids=None):
    log_event(
        CONTRADICTION_CANDIDATE,
        subject=loop_id,
        summary="test candidate",
        context={
            "loop_id": loop_id,
            "rule_ids": rule_ids or [],
            "lesson_ids": lesson_ids or [],
            "failure_summary": "the run failed",
            "goal_preview": "test goal",
        },
    )


class TestAdjudicator:
    def test_yes_verdict_contests_rule_and_reaches_refight(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-a1", rule_ids=[rule.rule_id])
        adapter = _FakeAdapter(
            f'{{"contradicted": "yes", "contradicted_ids": ["{rule.rule_id}"],'
            f' "reasoning": "rule steered it wrong"}}')
        counts = adjudicate_contradiction_candidates(adapter)
        assert counts["examined"] == 1 and counts["contradicted"] == 1
        stored = load_standing_rules()[0]
        assert stored.contradictions == 1
        # The whole point of chunk 4: refight is now reachable.
        assert [r.rule_id for r in contested_rules()] == [rule.rule_id]
        # And injection demotes it to the verify-before-relying tier.
        assert "verify before relying" in inject_standing_rules()
        adjudicated = _events(CONTRADICTION_ADJUDICATED)
        assert len(adjudicated) == 1
        assert adjudicated[0]["context"]["verdict"] == "yes"
        # Honest mutation record (review F5): the rule actually took the hit.
        assert adjudicated[0]["context"]["applied"] == [rule.rule_id]

    def test_yes_contests_only_named_artifacts(self, monkeypatch, tmp_path):
        """Review F4 (Skeptic+Architect consensus): a run cites its whole
        injected bundle — a yes must name the guilty artifact, and innocent
        bystanders stay uncontested."""
        _setup(monkeypatch, tmp_path)
        guilty = _promote_rule("Always fetch via Jina.")
        innocent = _promote_rule("Always run the linter.")
        _seed_candidate("lp-a7", rule_ids=[guilty.rule_id, innocent.rule_id])
        adjudicate_contradiction_candidates(_FakeAdapter(
            f'{{"contradicted": "yes", "contradicted_ids": '
            f'["{guilty.rule_id}"], "reasoning": "only the fetch rule"}}'))
        by_id = {r.rule_id: r for r in load_standing_rules()}
        assert by_id[guilty.rule_id].contradictions == 1
        assert by_id[innocent.rule_id].contradictions == 0

    def test_yes_naming_nothing_is_unparsable_and_retried(
            self, monkeypatch, tmp_path):
        """A yes with no valid attribution must never fan out across the
        bundle — it is treated as unparsable and retried."""
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-a8", rule_ids=[rule.rule_id])
        counts = adjudicate_contradiction_candidates(_FakeAdapter(
            '{"contradicted": "yes", "contradicted_ids": [],'
            ' "reasoning": "vibes"}'))
        assert counts["unparsable"] == 1
        assert load_standing_rules()[0].contradictions == 0
        assert _events(CONTRADICTION_ADJUDICATED) == []
        # Naming an id that was never cited is equally invalid.
        counts2 = adjudicate_contradiction_candidates(_FakeAdapter(
            '{"contradicted": "yes", "contradicted_ids": ["not-cited"],'
            ' "reasoning": "?"}'))
        assert counts2["unparsable"] == 1
        assert load_standing_rules()[0].contradictions == 0

    def test_no_verdict_records_without_mutation(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-a2", rule_ids=[rule.rule_id])
        counts = adjudicate_contradiction_candidates(
            _FakeAdapter('{"contradicted": "no", "reasoning": "unrelated"}'))
        assert counts["cleared"] == 1
        assert load_standing_rules()[0].contradictions == 0
        assert _events(CONTRADICTION_ADJUDICATED)[0]["context"]["verdict"] == "no"

    def test_undecided_is_unjudged_never_contested(self, monkeypatch, tmp_path):
        """Checkpoint law (iv): a cheap judge cannot demote a rule by
        shrugging — undecided records terminally but mutates nothing."""
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-a3", rule_ids=[rule.rule_id])
        counts = adjudicate_contradiction_candidates(
            _FakeAdapter('{"contradicted": "undecided", "reasoning": "?"}'))
        assert counts["undecided"] == 1
        assert load_standing_rules()[0].contradictions == 0
        assert contested_rules() == []
        # Terminal: a rerun does not re-examine it.
        counts2 = adjudicate_contradiction_candidates(_FakeAdapter())
        assert counts2["examined"] == 0

    def test_unparsable_output_stays_pending(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-a4", rule_ids=[rule.rule_id])
        counts = adjudicate_contradiction_candidates(
            _FakeAdapter("total garbage, no json"))
        assert counts["unparsable"] == 1
        assert _events(CONTRADICTION_ADJUDICATED) == []
        # Retriable: the next cycle picks the same candidate up.
        counts2 = adjudicate_contradiction_candidates(
            _FakeAdapter('{"contradicted": "no", "reasoning": "ok now"}'))
        assert counts2["cleared"] == 1

    def test_cap_bounds_spend_per_cycle(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        for i in range(5):
            _seed_candidate(f"lp-b{i}", rule_ids=[rule.rule_id])
        adapter = _FakeAdapter(
            *['{"contradicted": "no", "reasoning": "x"}'] * 5)
        counts = adjudicate_contradiction_candidates(adapter)
        assert counts["examined"] == 3          # default cap
        assert adapter.calls == 3

    def test_fifo_oldest_candidate_first(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        for i in range(4):
            _seed_candidate(f"lp-f{i}", rule_ids=[rule.rule_id])
        adjudicate_contradiction_candidates(
            _FakeAdapter(*['{"contradicted": "no", "reasoning": "x"}'] * 3))
        judged = {e["context"]["loop_id"]
                  for e in _events(CONTRADICTION_ADJUDICATED)}
        assert judged == {"lp-f0", "lp-f1", "lp-f2"}

    def test_moot_candidate_clears_without_adapter(self, monkeypatch, tmp_path):
        """All cited artifacts gone from the stores → deterministic 'no',
        no LLM needed — prevents infinite re-examination."""
        _setup(monkeypatch, tmp_path)
        _seed_candidate("lp-a5", rule_ids=["gone-rule"])
        counts = adjudicate_contradiction_candidates(None)
        assert counts["cleared"] == 1
        assert _events(CONTRADICTION_ADJUDICATED)[0]["context"]["verdict"] == "no"

    def test_dry_run_judges_but_never_mutates(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-a6", rule_ids=[rule.rule_id])
        counts = adjudicate_contradiction_candidates(
            _FakeAdapter(
                f'{{"contradicted": "yes", "contradicted_ids": '
                f'["{rule.rule_id}"], "reasoning": "would hit"}}'),
            dry_run=True)
        assert counts["contradicted"] == 1
        assert load_standing_rules()[0].contradictions == 0
        assert _events(CONTRADICTION_ADJUDICATED) == []

    def test_duplicate_candidates_same_loop_judged_once(
            self, monkeypatch, tmp_path):
        """Review F2/Architect-4: re-stamped verdicts can emit duplicate
        candidates for one loop — one verdict covers them all, and the
        contradiction count moves by exactly one."""
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-dup", rule_ids=[rule.rule_id])
        _seed_candidate("lp-dup", rule_ids=[rule.rule_id])
        adapter = _FakeAdapter(
            f'{{"contradicted": "yes", "contradicted_ids": '
            f'["{rule.rule_id}"], "reasoning": "x"}}',
            f'{{"contradicted": "yes", "contradicted_ids": '
            f'["{rule.rule_id}"], "reasoning": "x"}}')
        counts = adjudicate_contradiction_candidates(adapter)
        assert counts["examined"] == 1 and adapter.calls == 1
        assert load_standing_rules()[0].contradictions == 1
        # And the adjudicated marker blocks the duplicate on later cycles.
        counts2 = adjudicate_contradiction_candidates(_FakeAdapter())
        assert counts2["examined"] == 0

    def test_no_bounded_window_starvation(self, monkeypatch, tmp_path):
        """Review F1 (all three lenses): the candidate read must be
        unlimited — with >100 pending, a limit-100 newest-first window made
        the oldest candidates permanently invisible."""
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        for i in range(105):
            _seed_candidate(f"lp-s{i:03d}", rule_ids=[rule.rule_id])
        adjudicate_contradiction_candidates(
            _FakeAdapter(*['{"contradicted": "no", "reasoning": "x"}'] * 3))
        judged = {e["context"]["loop_id"]
                  for e in _events(CONTRADICTION_ADJUDICATED)}
        # True FIFO: the three OLDEST — exactly the ones a bounded newest-100
        # window would never have returned.
        assert judged == {"lp-s000", "lp-s001", "lp-s002"}

    def test_cycle_lock_skips_when_held(self, monkeypatch, tmp_path):
        """Review F2: maintenance runs at every loop finalize; a concurrent
        holder means this cycle skips rather than double-judging."""
        import fcntl
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-lock", rule_ids=[rule.rule_id])
        from memory_ledger import _memory_dir as md
        lock_path = md() / "contradiction_adjudication.lock"
        holder = open(lock_path, "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            adapter = _FakeAdapter('{"contradicted": "no", "reasoning": "x"}')
            counts = adjudicate_contradiction_candidates(adapter)
            assert counts["examined"] == 0 and adapter.calls == 0
        finally:
            holder.close()
        # Lock released → next cycle proceeds normally.
        counts2 = adjudicate_contradiction_candidates(
            _FakeAdapter('{"contradicted": "no", "reasoning": "x"}'))
        assert counts2["examined"] == 1

    def test_lesson_only_yes_records_honest_noop(self, monkeypatch, tmp_path):
        """Review F5: a cited lesson that is not also a rule/hypothesis has
        no contested tier — the yes records, but applied stays empty."""
        _setup(monkeypatch, tmp_path)
        from memory_ledger import _lessons_path
        _lessons_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_lessons_path(), "a") as f:
            f.write(json.dumps({"lesson_id": "l-solo",
                                "lesson": "a lesson matching no rule"}) + "\n")
        _seed_candidate("lp-a9", lesson_ids=["l-solo"])
        counts = adjudicate_contradiction_candidates(_FakeAdapter(
            '{"contradicted": "yes", "contradicted_ids": ["l-solo"],'
            ' "reasoning": "the lesson misled it"}'))
        assert counts["contradicted"] == 1
        ev = _events(CONTRADICTION_ADJUDICATED)[0]["context"]
        assert ev["contradicted_ids"] == ["l-solo"]
        assert ev["applied"] == []

    def test_refight_evidence_includes_adjudication_reasoning(
            self, monkeypatch, tmp_path):
        """Review F3: refight must see the actual collision (which run
        failed, judge's attribution), not just a contradiction tally."""
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-ev", rule_ids=[rule.rule_id])
        adjudicate_contradiction_candidates(_FakeAdapter(
            f'{{"contradicted": "yes", "contradicted_ids": '
            f'["{rule.rule_id}"], "reasoning": "Jina endpoint retired"}}'))
        from knowledge_lens import _rule_contradiction_evidence
        evidence = "\n".join(_rule_contradiction_evidence(rule.rule_id))
        assert "Jina endpoint retired" in evidence
        assert "lp-ev" in evidence

    def test_retire_keeps_full_source_lesson_ids(self, monkeypatch, tmp_path):
        """Review M3: demotion back to hypothesis is exactly the lifecycle
        provenance exists for — every contributor survives retirement."""
        _setup(monkeypatch, tmp_path)
        observe_pattern("Always verify.", "agenda", source_lesson_id="l1")
        rule = observe_pattern("Always verify.", "agenda",
                               source_lesson_id="l2")
        from knowledge_lens import refight_rule, load_hypotheses
        from knowledge_lens import contradict_pattern
        contradict_pattern("Always verify.", "")
        rule = load_standing_rules()[0]
        action = refight_rule(rule, _FakeAdapter(
            '{"action": "retire", "reasoning": "world moved"}'))
        assert action == "retire"
        hyp = load_hypotheses()[0]
        assert hyp.source_lesson_ids == ["l1", "l2"]


# ---------------------------------------------------------------------------
# End-to-end: the plan's chunk-4 verification, LLM canned, every seam real
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_failing_run_citing_rule_reaches_refight_in_one_pass(
            self, monkeypatch, tmp_path):
        """Seed a failing run citing a rule; confirm candidate event →
        adjudication → contested injection block → RULE_REFOUGHT — with the
        adjudicator running BEFORE the refight scan, the whole lifecycle
        completes inside a single run_skill_maintenance call."""
        _setup(monkeypatch, tmp_path)
        from recall import recall
        rule = _promote_rule()

        # 1. A real recall writes the run-keyed citations file. Recall runs
        # inside the run (ambient dir is correct there); the emitter later
        # joins the same dir by loop_id through the run index.
        run_dir = tmp_path / "runs" / "e2e"
        run_dir.mkdir(parents=True)
        monkeypatch.setattr(runs_module, "current_run_dir", lambda: run_dir)
        monkeypatch.setattr(runs_module, "resolve_run_dir",
                            lambda ref: run_dir if ref == "lp-e2e" else None)
        r = recall("do the thing", slice="loop", project="proj-x")
        assert rule.rule in r.standing_rules
        assert json.loads((run_dir / "source" / "recall_citations.json")
                          .read_text())["rule_ids"] == [rule.rule_id]

        # 2. A full-trust failure verdict emits the candidate.
        record_outcome("do the thing", "done", "claimed done, artifact absent",
                       loop_id="lp-e2e")
        stamp_outcome_verdict(
            "lp-e2e", goal_achieved=False, goal_verdict_source="closure",
            goal_verdict_confidence=0.9)
        assert len(_events(CONTRADICTION_CANDIDATE)) == 1

        # 3. One maintenance pass: adjudicate (yes) then refight (keep).
        from skill_lifecycle import run_skill_maintenance
        adapter = _FakeAdapter(
            f'{{"contradicted": "yes", "contradicted_ids": '
            f'["{rule.rule_id}"], "reasoning": "rule steered the failure"}}',
            '{"action": "keep", "reasoning": "noise — rule survives"}')
        result = run_skill_maintenance(adapter=adapter)
        assert result["contradictions_adjudicated"]["contradicted"] == 1
        assert result["rules_refought"] == [f"{rule.rule_id}:keep"]
        refought = _events("RULE_REFOUGHT")
        assert len(refought) == 1 and refought[0]["context"]["action"] == "keep"
        # Battle re-fought and won: trust restored, verified today.
        stored = load_standing_rules()[0]
        assert stored.contradictions == 0 and stored.last_verified

    def test_maintenance_gate_off_skips_adjudication(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rule = _promote_rule()
        _seed_candidate("lp-g1", rule_ids=[rule.rule_id])
        import skill_lifecycle as sl
        import config as config_module
        real_get = config_module.get

        def _get(key, default=None):
            if key == "knowledge.contradiction_adjudication_enabled":
                return False
            return real_get(key, default)

        monkeypatch.setattr(config_module, "get", _get)
        result = sl.run_skill_maintenance(
            adapter=_FakeAdapter('{"contradicted": "yes", "reasoning": "x"}'))
        assert result["contradictions_adjudicated"] == {}
        assert load_standing_rules()[0].contradictions == 0


# ---------------------------------------------------------------------------
# Citation read-side plumbing
# ---------------------------------------------------------------------------


class TestStandingRulesWithIds:
    def test_parity_with_inject_and_ids_cover_all_tiers(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        fresh = _promote_rule("Always run the linter.")
        contested = _promote_rule("Always fetch via Jina.")
        from knowledge_lens import contradict_pattern
        contradict_pattern("Always fetch via Jina.", "")
        text, ids = standing_rules_with_ids()
        assert text == inject_standing_rules()
        # Contested rules are still relied-on for citation purposes.
        assert set(ids) == {fresh.rule_id, contested.rule_id}

    def test_empty_store(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        assert standing_rules_with_ids() == ("", [])
