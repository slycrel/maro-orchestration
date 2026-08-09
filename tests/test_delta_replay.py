"""delta_replay — the Δ-gate's standalone instrument (brief §3.1).

Pins: action extraction from real recorded-response shapes, arm surgery in
both directions (ablation and injection), oracle discipline, stratum
classification, and Δ arithmetic against a scripted adapter. No LLM.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from delta_replay import (
    DecisionCall,
    recorded_prompt_to_messages,
    STRATUM_REASON,
    STRATUM_RULE,
    classify_stratum,
    find_decision_calls,
    lesson_in_prompt,
    prompt_with_lesson,
    prompt_without_lesson,
    score_lesson,
    DECISION_PURPOSES,
)


# ---------------------------------------------------------------------------
# Action extraction
# ---------------------------------------------------------------------------

class TestActionExtraction:
    def test_navigator_fenced_json(self):
        # Shape of a real recorded navigator response (2738d9c0 call-00011)
        resp = '```json\n{\n  "move": "extend",\n  "reasoning": "..."\n}\n```'
        assert DECISION_PURPOSES["navigator decision"](resp) == "extend"

    def test_supervision_bare_json(self):
        resp = '{\n  "action": "adjust",\n  "reasoning": "...",\n  "revised_steps": []\n}'
        assert DECISION_PURPOSES["adaptive supervision"](resp) == "adjust"

    def test_truncated_response_yields_none(self):
        # Recorded responses can be clipped mid-JSON; that call is unscoreable.
        resp = '```json\n{\n  "move": "extend",\n  "reasoning": "cut off her'
        assert DECISION_PURPOSES["navigator decision"](resp) is None

    def test_action_normalized_lowercase(self):
        assert DECISION_PURPOSES["navigator decision"]('{"move": "Extend "}') == "extend"

    def test_missing_key_yields_none(self):
        assert DECISION_PURPOSES["navigator decision"]('{"action": "adjust"}') is None


# ---------------------------------------------------------------------------
# Arm surgery
# ---------------------------------------------------------------------------

LESSON = "when a judge found 'extend' better 3x, prefer 'extend' for blocked_step"


class TestArmSurgery:
    def test_injection_direction(self):
        prompt = "Decide the next move.\n\nSituation: blocked_step."
        with_arm = prompt_with_lesson(prompt, LESSON)
        assert lesson_in_prompt(with_arm, LESSON)
        assert "Long-Term Lessons" in with_arm
        # without-arm on an absent lesson is the recorded prompt untouched
        assert prompt_without_lesson(prompt, LESSON) == prompt

    def test_ablation_direction(self):
        prompt = (
            "Decide the next move.\n\n## Tiered Lessons\n\n"
            "### Long-Term Lessons (always apply)\n"
            f"- ✓ {LESSON}\n\nSituation: blocked_step."
        )
        without = prompt_without_lesson(prompt, LESSON)
        assert not lesson_in_prompt(without, LESSON)
        # the whole bullet line goes, no dangling "- ✓" scaffold
        assert "- ✓" not in without
        # with-arm on a present lesson is the recorded prompt untouched
        assert prompt_with_lesson(prompt, LESSON) == prompt

    def test_whitespace_rewrap_still_detected(self):
        wrapped = "context\n- ✓ when a judge found 'extend' better 3x,\n  prefer 'extend' for blocked_step\nmore"
        assert lesson_in_prompt(wrapped, LESSON)

    def test_surgery_is_inverse_pair(self):
        prompt = "Bare decision prompt."
        assert prompt_without_lesson(
            prompt_with_lesson(prompt, LESSON), LESSON).strip() \
            == prompt.strip()


# ---------------------------------------------------------------------------
# find_decision_calls
# ---------------------------------------------------------------------------

def _write_call(calls_dir: Path, seq: int, purpose: str, response: str):
    calls_dir.mkdir(parents=True, exist_ok=True)
    (calls_dir / f"call-{seq:05d}.json").write_text(json.dumps({
        "seq": seq, "purpose": purpose, "prompt": f"prompt {seq}",
        "response": response, "model": "claude-haiku-4-5-20251001",
    }))


class TestFindDecisionCalls:
    def test_only_registry_purposes_with_parseable_actions(self, tmp_path):
        rd = tmp_path / "run-x"
        calls = rd / "build" / "calls"
        _write_call(calls, 1, "navigator decision", '{"move": "extend"}')
        _write_call(calls, 2, "step-execute", "free text")
        _write_call(calls, 3, "adaptive supervision", '{"action": "continue"}')
        _write_call(calls, 4, "navigator decision", "not json at all")
        found = find_decision_calls(rd, goal_achieved=True)
        assert [(c.purpose, c.recorded_action) for c in found] == [
            ("navigator decision", "extend"),
            ("adaptive supervision", "continue"),
        ]
        assert all(c.oracle for c in found)

    def test_unjudged_run_is_not_oracle(self, tmp_path):
        rd = tmp_path / "run-y"
        _write_call(rd / "build" / "calls", 1, "navigator decision",
                    '{"move": "execute"}')
        found = find_decision_calls(rd, goal_achieved=None)
        assert found and not found[0].oracle

    def test_missing_calls_dir_returns_empty(self, tmp_path):
        assert find_decision_calls(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Recorded-prompt role reconstruction
# ---------------------------------------------------------------------------

class TestPromptToMessages:
    def test_role_markers_rebuild_message_list(self):
        msgs = recorded_prompt_to_messages(
            "[system]\nYou are the NAVIGATOR.\n[user]\nDecide the move.")
        assert [(m.role, m.content) for m in msgs] == [
            ("system", "You are the NAVIGATOR."),
            ("user", "Decide the move."),
        ]

    def test_markerless_prompt_is_single_user_message(self):
        msgs = recorded_prompt_to_messages("just a bare prompt")
        assert [(m.role, m.content) for m in msgs] == [
            ("user", "just a bare prompt")]

    def test_inline_bracket_words_are_not_markers(self):
        # [system] must be alone on its line to count — prose mentioning
        # "[user] data" mid-line must not split the prompt.
        msgs = recorded_prompt_to_messages(
            "[system]\nCare about [user] data handling.\n[user]\nGo.")
        assert len(msgs) == 2
        assert "[user] data" in msgs[0].content


# ---------------------------------------------------------------------------
# Stratification
# ---------------------------------------------------------------------------

class TestStratum:
    def test_imperative_prescription_is_rule(self):
        assert classify_stratum(
            "Always run tests before claiming done") == STRATUM_RULE

    def test_observation_is_reason(self):
        assert classify_stratum(
            "The deliverable path differed from the plan because the worker "
            "assumed artifacts/ existed") == STRATUM_REASON

    def test_judge_evidence_is_reason(self):
        assert classify_stratum(
            "a judge found the pipeline's 'extend' call better 3x for "
            "blocked_step shapes") == STRATUM_REASON

    def test_bracket_tag_and_topic_header_stripped(self):
        assert classify_stratum(
            "[testing] pytest: run the full suite before landing") == STRATUM_RULE


# ---------------------------------------------------------------------------
# score_lesson against a scripted adapter
# ---------------------------------------------------------------------------

class ScriptedAdapter:
    """Answers 'extend' when the lesson is in the prompt, 'execute' when
    not — a maximally lesson-sensitive decision-maker."""
    def __init__(self, lesson: str):
        self.lesson = lesson
        self.n_calls = 0

    def complete(self, messages, **kw):
        self.n_calls += 1
        prompt = " ".join(m.content for m in messages)
        move = "extend" if lesson_in_prompt(prompt, self.lesson) else "execute"
        return SimpleNamespace(content=json.dumps({"move": move}))


def _decision_call(prompt: str, recorded: str, oracle: bool = True) -> DecisionCall:
    return DecisionCall(
        run_id="run-z", call_path="/x/call-00001.json",
        purpose="navigator decision", prompt=prompt,
        recorded_action=recorded, oracle=oracle,
    )


class TestScoreLesson:
    def test_effective_lesson_positive_delta(self):
        adapter = ScriptedAdapter(LESSON)
        calls = [_decision_call("bare prompt, decide", "extend")]
        res = score_lesson(LESSON, calls, adapter, samples=2)
        assert res.n_calls == 1
        assert res.calls[0].with_match == 1.0
        assert res.calls[0].without_match == 0.0
        assert res.delta == 1.0
        assert adapter.n_calls == 4  # 2 samples x 2 arms

    def test_inert_lesson_zero_delta(self):
        # Adapter keyed to a DIFFERENT lesson — ours changes nothing.
        adapter = ScriptedAdapter("some other lesson entirely")
        calls = [_decision_call("bare prompt, decide", "execute")]
        res = score_lesson(LESSON, calls, adapter, samples=2)
        assert res.delta == 0.0

    def test_non_oracle_calls_skipped_and_counted(self):
        adapter = ScriptedAdapter(LESSON)
        calls = [
            _decision_call("p", "extend", oracle=True),
            _decision_call("p", "extend", oracle=False),
        ]
        res = score_lesson(LESSON, calls, adapter, samples=1)
        assert res.n_calls == 1
        assert res.skipped_no_oracle == 1

    def test_replay_failure_scores_no_match_and_counts(self):
        class ExplodingAdapter:
            def complete(self, messages, **kw):
                raise RuntimeError("backend down")
        calls = [_decision_call("p", "extend")]
        res = score_lesson(LESSON, calls, ExplodingAdapter(), samples=2)
        assert res.replay_errors == 4
        assert res.delta == 0.0  # no arm earned anything

    def test_ablation_direction_measures_present_lesson(self):
        adapter = ScriptedAdapter(LESSON)
        recorded_prompt = prompt_with_lesson("decide", LESSON)
        calls = [_decision_call(recorded_prompt, "extend")]
        res = score_lesson(LESSON, calls, adapter, samples=1)
        assert res.calls[0].lesson_was_present is True
        assert res.delta == 1.0

    def test_jackknife_flags_single_call_dominance(self):
        adapter = ScriptedAdapter(LESSON)
        calls = [
            _decision_call("a", "extend"),   # delta 1.0
            _decision_call("b", "execute"),  # with=0, without=1 → delta -1.0
        ]
        res = score_lesson(LESSON, calls, adapter, samples=1)
        assert res.delta == 0.0
        assert res.jackknife() == 1.0

    def test_as_dict_roundtrips(self):
        adapter = ScriptedAdapter(LESSON)
        res = score_lesson(LESSON, [_decision_call("p", "extend")],
                           adapter, samples=1)
        d = res.as_dict()
        assert d["delta"] == 1.0
        assert d["stratum"] == res.stratum
        assert d["calls"][0]["recorded_action"] == "extend"
        json.dumps(d)  # serializable as-is


# ---------------------------------------------------------------------------
# Effect promotion route (knowledge_web.promote_lesson_by_effect)
# ---------------------------------------------------------------------------

GOOD_EVIDENCE = {"delta": 0.59, "jackknife_spread": 0.09, "n_calls": 18,
                 "stratum": "reason"}


def _seed_medium_lesson(monkeypatch, tmp_path, **overrides):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    from memory import record_tiered_lesson
    tl = record_tiered_lesson(
        overrides.pop("lesson_text",
                      "the deliverable path differed because the worker "
                      "assumed artifacts/ existed"),
        "agenda", "done", "goal", **overrides)
    return tl


class TestEffectPromotion:
    def test_clears_bar_promotes_and_stamps_evidence(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        assert kw.promote_lesson_by_effect(tl.lesson_id, GOOD_EVIDENCE) is True
        longs = kw.load_tiered_lessons(tier=kw.MemoryTier.LONG, min_score=0.0)
        row = next(l for l in longs if l.lesson_id == tl.lesson_id)
        assert row.delta_evidence["route"] == "effect"
        assert row.delta_evidence["delta"] == 0.59
        # popped from MEDIUM, not duplicated
        mediums = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        assert all(l.lesson_id != tl.lesson_id for l in mediums)

    def test_tenure_not_required(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        # Fresh row: sessions_validated=0, far below PROMOTE_MIN_SESSIONS —
        # the whole point of the route (brief §1: tenure excludes
        # blind-spot lessons).
        assert tl.sessions_validated < kw.PROMOTE_MIN_SESSIONS
        assert kw.promote_lesson_by_effect(tl.lesson_id, GOOD_EVIDENCE) is True

    def test_below_delta_threshold_refused(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(GOOD_EVIDENCE, delta=0.1)
        assert kw.promote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_dominated_verdict_refused(self, monkeypatch, tmp_path):
        # jackknife spread >= delta: one call owns the verdict
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(GOOD_EVIDENCE, delta=0.4, jackknife_spread=0.5)
        assert kw.promote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_rule_stratum_refused(self, monkeypatch, tmp_path):
        # The validation's rule specimen measured NEGATIVE; a positive Δ on
        # a rule lesson is noise by construction (LeAct §6).
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(GOOD_EVIDENCE, stratum="rule")
        assert kw.promote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_too_few_calls_refused(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(GOOD_EVIDENCE, n_calls=3)
        assert kw.promote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_non_finite_evidence_refused(self, monkeypatch, tmp_path):
        # Round-4 review (3/3 lenses): NaN fails BOTH bar comparisons, so a
        # malformed measurement sailed through `< min_delta` and
        # `spread >= delta` and mutated a tier — and --remint-pending applies
        # the routes BEFORE the hardened watch resolver, so the routes are
        # the gate that matters.
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        nan, inf = float("nan"), float("inf")
        assert kw.promote_lesson_by_effect(
            tl.lesson_id, dict(GOOD_EVIDENCE, delta=nan)) is False
        assert kw.promote_lesson_by_effect(
            tl.lesson_id, dict(GOOD_EVIDENCE, jackknife_spread=nan)) is False
        assert kw.promote_lesson_by_effect(
            tl.lesson_id, dict(GOOD_EVIDENCE, jackknife_spread=-1.0)) is False
        assert kw.promote_lesson_by_effect(
            tl.lesson_id, dict(GOOD_EVIDENCE, delta=inf)) is False
        neg = {"delta": -1.0, "jackknife_spread": 0.0, "n_calls": 18,
               "stratum": "reason"}
        assert kw.demote_lesson_by_effect(
            tl.lesson_id, dict(neg, delta=nan)) is False
        assert kw.demote_lesson_by_effect(
            tl.lesson_id, dict(neg, jackknife_spread=nan)) is False
        assert kw.demote_lesson_by_effect(
            tl.lesson_id, dict(neg, jackknife_spread=-1.0)) is False
        # the well-formed originals still act
        assert kw.demote_lesson_by_effect(tl.lesson_id, neg) is True

    def test_killswitch_off_refuses(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        monkeypatch.setattr(kw, "effect_promotion_enabled", lambda: False)
        assert kw.promote_lesson_by_effect(tl.lesson_id, GOOD_EVIDENCE) is False

    def test_provisional_row_never_reaches_long(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path, provisional=True)
        import knowledge_web as kw
        assert kw.promote_lesson_by_effect(tl.lesson_id, GOOD_EVIDENCE) is False


# ---------------------------------------------------------------------------
# Effect demotion route (knowledge_web.demote_lesson_by_effect, 2026-08-08)
# ---------------------------------------------------------------------------

NEG_EVIDENCE = {"delta": -0.137, "jackknife_spread": 0.04, "n_calls": 51,
                "stratum": "reason"}


class TestEffectDemotion:
    def test_clears_bar_stamps_and_stays_medium(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True
        mediums = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        row = next(l for l in mediums if l.lesson_id == tl.lesson_id)
        # demotion is a stamp, not a tier move or deletion
        assert row.delta_evidence["route"] == "effect-demote"
        assert row.delta_evidence["delta"] == -0.137

    def test_stamped_row_leaves_injection_surface(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        # present before demotion — the exclusion assertion below isn't vacuous
        assert tl.lesson in kw.inject_tiered_lessons("agenda")
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True
        assert tl.lesson not in kw.inject_tiered_lessons("agenda")

    def test_flat_query_surface_untouched(self, monkeypatch, tmp_path):
        # Surface-scoping decree: decision-replay Δ demotes from decision
        # injection only — query_lessons still serves the row.
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True
        hits = kw.query_lessons("deliverable path artifacts worker", n=5)
        assert any(tl.lesson_id == h.lesson_id for h in hits)

    def test_tenure_promotion_blocked(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True

        def _force_eligible(lessons):
            for l in lessons:
                if l.lesson_id == tl.lesson_id:
                    l.score = 1.0
                    l.sessions_validated = kw.PROMOTE_MIN_SESSIONS
            return lessons

        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _force_eligible)
        assert kw.promote_lesson(tl.lesson_id) is False
        mediums = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        assert any(l.lesson_id == tl.lesson_id for l in mediums)

    def test_new_positive_measurement_replaces_stamp(self, monkeypatch, tmp_path):
        # Measurement replaces measurement: a later replay clearing the
        # promote bar overwrites the demote stamp wholesale.
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True
        assert kw.promote_lesson_by_effect(tl.lesson_id, GOOD_EVIDENCE) is True
        longs = kw.load_tiered_lessons(tier=kw.MemoryTier.LONG, min_score=0.0)
        row = next(l for l in longs if l.lesson_id == tl.lesson_id)
        assert row.delta_evidence["route"] == "effect"

    def test_weak_negative_refused(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(NEG_EVIDENCE, delta=-0.02, jackknife_spread=0.01)
        assert kw.demote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_positive_delta_refused(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(NEG_EVIDENCE, delta=0.2)
        assert kw.demote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_dominated_verdict_refused(self, monkeypatch, tmp_path):
        # jackknife spread >= |delta|: one call owns the verdict — this is
        # exactly what stops the known-inert specimen (−0.06, spread 0.09)
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(NEG_EVIDENCE, delta=-0.06, jackknife_spread=0.09)
        assert kw.demote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_rule_stratum_refused(self, monkeypatch, tmp_path):
        # Census round 2 measured the rule stratum MIXED (−0.067/+0.067) —
        # rule-negative doesn't generalize; rules are already excluded from
        # the effect surface by construction.
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(NEG_EVIDENCE, stratum="rule")
        assert kw.demote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_too_few_calls_refused(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        ev = dict(NEG_EVIDENCE, n_calls=3)
        assert kw.demote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_killswitch_off_refuses(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        import knowledge_web as kw
        monkeypatch.setattr(kw, "effect_demotion_enabled", lambda: False)
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is False

    def test_census_demote_applies_stamp(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        # 6 oracle calls whose recorded action the adapter only produces
        # WITHOUT the lesson → per-call Δ = −1.0, spread 0
        import runs
        rd = runs.create_run_dir("hdem", prompt="census", lane="agenda")
        for i in range(1, 7):
            _write_call(rd / "build" / "calls", i, "navigator decision",
                        '{"move": "execute"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))
        from delta_replay import run_effect_route
        adapter = ScriptedAdapter(tl.lesson)
        out = run_effect_route(adapter, demote=True, samples=1)
        row = next(r for r in out["census"] if r["lesson_id"] == tl.lesson_id)
        assert row["delta"] == -1.0
        assert row["demoted_by_effect"] is True
        assert row["promoted_by_effect"] is False
        import knowledge_web as kw
        mediums = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        stamped = next(l for l in mediums if l.lesson_id == tl.lesson_id)
        assert stamped.delta_evidence["route"] == "effect-demote"


class TestEffectRouteCensus:
    def test_census_reports_both_routes_without_promoting(self, monkeypatch, tmp_path):
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        # oracle corpus: one judged-True run with one navigator call
        import runs
        rd = runs.create_run_dir("hcen", prompt="census", lane="agenda")
        _write_call(rd / "build" / "calls", 1, "navigator decision",
                    '{"move": "extend"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))
        from delta_replay import run_effect_route
        adapter = ScriptedAdapter(tl.lesson)
        out = run_effect_route(adapter, promote=False, samples=1)
        assert out["n_oracle_calls"] == 1
        row = next(r for r in out["census"] if r["lesson_id"] == tl.lesson_id)
        assert row["delta"] == 1.0
        assert row["tenure_eligible"] is False
        assert row["promoted_by_effect"] is False
        # census-only run must not move tiers
        import knowledge_web as kw
        longs = kw.load_tiered_lessons(tier=kw.MemoryTier.LONG, min_score=0.0)
        assert all(l.lesson_id != tl.lesson_id for l in longs)


# ---------------------------------------------------------------------------
# Adversarial-review hardening (2026-08-08): slot-eating, TOCTOU, replay
# errors, audit event, named-lesson measurement
# ---------------------------------------------------------------------------

def _cl_events(tmp_path, event_type):
    path = tmp_path / "memory" / "captains_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)
            for line in path.read_text().splitlines()
            if line.strip() and json.loads(line).get("event_type") == event_type]


class TestReviewHardening:
    def test_demoted_rows_do_not_eat_injection_slots(self, monkeypatch, tmp_path):
        # Review Part 1 finding 1 (measured live): demoted rows keep their
        # score rank, so a filter placed after the load limit spends the
        # pool on rows it then drops. Healthy lessons must fill the slots.
        import knowledge_web as kw
        texts = [
            "the run diverged because the dependency resolver preferred the "
            "wrong registry mirror",
            "the summary step failed because the artifact index was read "
            "before the writer flushed",
            "the fetch retried forever because the proxy stripped the "
            "content-length header",
            "the plan stalled because the validator held the workspace lock "
            "across the whole batch",
        ]
        seeded = []
        for text in texts:
            seeded.append(_seed_medium_lesson(
                monkeypatch, tmp_path, lesson_text=text))
        top_two = [seeded[0].lesson_id, seeded[1].lesson_id]

        def _rank(lessons):
            order = {tl.lesson_id: n for n, tl in enumerate(seeded)}
            for l in lessons:
                if l.lesson_id in order:
                    l.score = 2.0 - 0.1 * order[l.lesson_id]
            return lessons

        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _rank)
        for lid in top_two:
            assert kw.demote_lesson_by_effect(lid, NEG_EVIDENCE) is True
        block = kw.inject_tiered_lessons("agenda", track_applied=False)
        assert seeded[2].lesson in block and seeded[3].lesson in block
        assert seeded[0].lesson not in block and seeded[1].lesson not in block

    def test_promote_revalidates_guards_under_lock(self, monkeypatch, tmp_path):
        # Review Part 1 finding 2 (TOCTOU): promote_lesson pre-checks an
        # unlocked snapshot. Simulate a demote stamp landing in the window
        # by serving promote_lesson a stale (unstamped) snapshot while the
        # on-disk row carries the stamp — the in-lock re-check must refuse.
        import knowledge_web as kw
        tl = _seed_medium_lesson(monkeypatch, tmp_path)

        def _eligible(lessons):
            for l in lessons:
                if l.lesson_id == tl.lesson_id:
                    l.score = 1.0
                    l.sessions_validated = kw.PROMOTE_MIN_SESSIONS
            return lessons

        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _eligible)
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True

        real_load = kw.load_tiered_lessons
        first = {"done": False}

        def stale_first_read(*args, **kwargs):
            # Only promote_lesson's PRE-CHECK read (the first) is stale —
            # the locked read inside _mutate_tiered_lessons sees the disk
            # truth, which is exactly the TOCTOU shape under test.
            rows = real_load(*args, **kwargs)
            if not first["done"]:
                first["done"] = True
                for r in rows:
                    if r.lesson_id == tl.lesson_id:
                        r.delta_evidence = {}  # the pre-stamp snapshot
            return rows

        monkeypatch.setattr(kw, "load_tiered_lessons", stale_first_read)
        assert kw.promote_lesson(tl.lesson_id) is False
        monkeypatch.setattr(kw, "load_tiered_lessons", real_load)
        mediums = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        assert any(l.lesson_id == tl.lesson_id for l in mediums)
        longs = kw.load_tiered_lessons(tier=kw.MemoryTier.LONG, min_score=0.0)
        assert all(l.lesson_id != tl.lesson_id for l in longs)

    def test_errored_measurement_never_demotes(self, monkeypatch, tmp_path):
        # Review Part 1 finding 3: a with-arm outage manufactures delta=-1
        # with jackknife 0. Errored measurements are evidence for a re-run,
        # not an act.
        import knowledge_web as kw
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        ev = dict(NEG_EVIDENCE, delta=-1.0, jackknife_spread=0.0,
                  replay_errors=51)
        assert kw.demote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_errored_measurement_never_promotes(self, monkeypatch, tmp_path):
        import knowledge_web as kw
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        ev = dict(GOOD_EVIDENCE, replay_errors=3)
        assert kw.promote_lesson_by_effect(tl.lesson_id, ev) is False

    def test_stamp_persists_replay_errors_for_audit(self, monkeypatch, tmp_path):
        import knowledge_web as kw
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        assert kw.demote_lesson_by_effect(
            tl.lesson_id, dict(NEG_EVIDENCE, replay_errors=0)) is True
        mediums = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        row = next(l for l in mediums if l.lesson_id == tl.lesson_id)
        assert row.delta_evidence["replay_errors"] == 0

    def test_demotion_emits_captains_log_event(self, monkeypatch, tmp_path):
        # Review Part 1 finding 5: quarantine and contest both leave an
        # operator-readable audit row; demotion must too.
        import knowledge_web as kw
        from captains_log import LESSON_DELTA_DEMOTED
        tl = _seed_medium_lesson(monkeypatch, tmp_path)
        assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True
        events = _cl_events(tmp_path, LESSON_DELTA_DEMOTED)
        assert len(events) == 1
        assert events[0]["subject"] == tl.lesson_id
        assert events[0]["context"]["delta"] == NEG_EVIDENCE["delta"]
        assert events[0]["context"]["replay_errors"] == 0

    def test_named_lesson_ids_measure_excluded_rows(self, monkeypatch, tmp_path):
        # Review Part 1 finding 6: measurement is evidence-gathering, not an
        # act — naming a provisional row explicitly must measure it (the act
        # routes keep their own guards).
        tl = _seed_medium_lesson(monkeypatch, tmp_path, provisional=True)
        import runs
        rd = runs.create_run_dir("hnam", prompt="census", lane="agenda")
        _write_call(rd / "build" / "calls", 1, "navigator decision",
                    '{"move": "extend"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))
        from delta_replay import run_effect_route
        adapter = ScriptedAdapter(tl.lesson)
        out = run_effect_route(adapter, samples=1, lesson_ids=[tl.lesson_id])
        row = next((r for r in out["census"] if r["lesson_id"] == tl.lesson_id),
                   None)
        assert row is not None
        assert row["delta"] == 1.0

# ---------------------------------------------------------------------------
# Re-mint tombstones (2026-08-08, decision dcf8eab8): the archive is the
# tombstone store — a fresh mint matching a GC'd Δ-demoted lineage gets a
# remint-watch stamp (circulates normally), strike 3 queues re-measurement.
# ---------------------------------------------------------------------------

REMINT_TEXT = ("the verification step misread the ledger because the writer "
               "flushed after the reader's snapshot")


def _gc_lesson(kw, lesson_id):
    """Force a live MEDIUM row through the real GC path (archive-then-drop)."""
    def _sink(lessons):
        for l in lessons:
            if l.lesson_id == lesson_id:
                l.score = 0.05
                l.last_reinforced = "2020-01-01"
        return lessons
    kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _sink)
    stats = kw.run_decay_cycle(kw.MemoryTier.MEDIUM)
    assert stats["gc"] >= 1


def _demote_and_gc(monkeypatch, tmp_path, **mint_kw):
    """Mint → Δ-demote → GC. Returns (kw module, original lesson_id)."""
    tl = _seed_medium_lesson(monkeypatch, tmp_path,
                             lesson_text=mint_kw.pop("lesson_text", REMINT_TEXT),
                             **mint_kw)
    import knowledge_web as kw
    assert kw.demote_lesson_by_effect(tl.lesson_id, NEG_EVIDENCE) is True
    _gc_lesson(kw, tl.lesson_id)
    return kw, tl.lesson_id


class TestRemintTombstones:
    def test_remint_of_demoted_lesson_gets_watch_stamp(self, monkeypatch, tmp_path):
        kw, orig_id = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        assert re1.lesson_id != orig_id  # fresh row, not a resurrection
        assert re1.delta_evidence["route"] == "remint-watch"
        assert re1.delta_evidence["strikes"] == 1
        assert re1.delta_evidence["prior_lesson_id"] == orig_id
        assert re1.delta_evidence["prior_evidence"]["delta"] == -0.137
        # Gentle policy: the watch stamp is NOT a demotion — the row
        # circulates (no injection exclusion, no tenure block).
        assert kw._is_delta_demoted(re1) is False

    def test_watch_row_stays_on_injection_surface(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        block = kw.inject_tiered_lessons(task_type="agenda")
        assert REMINT_TEXT[:40] in block

    def test_remint_different_task_type_unstamped(self, monkeypatch, tmp_path):
        _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "research", "done", "goal")
        assert (re1.delta_evidence or {}).get("route") != "remint-watch"

    def test_remint_dissimilar_text_unstamped(self, monkeypatch, tmp_path):
        _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(
            "prefer the staging mirror when the registry rate-limits",
            "agenda", "done", "goal")
        assert (re1.delta_evidence or {}).get("route") != "remint-watch"

    def test_remint_killswitch_off_unstamped(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        monkeypatch.setattr(kw, "effect_demotion_enabled", lambda: False)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        assert (re1.delta_evidence or {}).get("route") != "remint-watch"

    def test_strikes_accumulate_and_pattern_event_at_three(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        from captains_log import LESSON_REMINT_PATTERN
        for expected in (1, 2):
            row = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
            assert row.delta_evidence["strikes"] == expected
            assert _cl_events(tmp_path, LESSON_REMINT_PATTERN) == []
            _gc_lesson(kw, row.lesson_id)
        row = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        assert row.delta_evidence["strikes"] == 3
        events = _cl_events(tmp_path, LESSON_REMINT_PATTERN)
        assert len(events) == 1
        assert events[0]["subject"] == row.lesson_id
        assert events[0]["context"]["strikes"] == 3

    def test_resolve_clears_watch_and_resets_lineage(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        null_ev = {"delta": 0.01, "jackknife_spread": 0.02, "n_calls": 51,
                   "stratum": "reason", "replay_errors": 0}
        assert kw.resolve_remint_watch(re1.lesson_id, null_ev) is True
        rows = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        row = next(l for l in rows if l.lesson_id == re1.lesson_id)
        assert row.delta_evidence["route"] == "measured"
        # The measured record resets the archive lineage: GC it, re-mint,
        # and the pattern clock has restarted — no watch stamp without a
        # fresh demotion.
        _gc_lesson(kw, re1.lesson_id)
        re2 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        assert (re2.delta_evidence or {}).get("route") != "remint-watch"

    def test_resolve_refuses_errored_thin_or_nonwatch(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        base = {"delta": 0.01, "jackknife_spread": 0.02, "n_calls": 51,
                "stratum": "reason", "replay_errors": 0}
        assert kw.resolve_remint_watch(re1.lesson_id, dict(base, replay_errors=2)) is False
        assert kw.resolve_remint_watch(re1.lesson_id, dict(base, n_calls=3)) is False
        # Decisive measurements are the routes' to act on — never a clear
        # (round-2 review: a disabled route must not launder them).
        assert kw.resolve_remint_watch(re1.lesson_id, dict(base, delta=-0.2)) is False
        assert kw.resolve_remint_watch(re1.lesson_id, dict(base, delta=0.5)) is False
        assert kw.resolve_remint_watch(re1.lesson_id, dict(base, delta=None)) is False
        # A measurement the routes would refuse as unreliable must not end a
        # probation either (round-3 review: NaN, spread straddling a bar, and
        # a non-reason stratum all cleared the watch).
        assert kw.resolve_remint_watch(
            re1.lesson_id, dict(base, delta=float("nan"))) is False
        assert kw.resolve_remint_watch(
            re1.lesson_id, dict(base, jackknife_spread=1.0)) is False
        assert kw.resolve_remint_watch(
            re1.lesson_id, dict(base, jackknife_spread=None)) is False
        assert kw.resolve_remint_watch(
            re1.lesson_id, dict(base, stratum="rule")) is False
        # A row that isn't under watch can't be "cleared"
        other = record_tiered_lesson("the cache key omitted the model tier",
                                     "agenda", "done", "goal")
        assert kw.resolve_remint_watch(other.lesson_id, base) is False
        # And the watch row itself is still watched (refusals mutated nothing)
        rows = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        row = next(l for l in rows if l.lesson_id == re1.lesson_id)
        assert row.delta_evidence["route"] == "remint-watch"

    def test_run_effect_route_remint_pending_selects_and_clears(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")

        # Fast-forward to strike 3 without two more GC cycles: the selector
        # reads the stamp, not the archive.
        def _bump(lessons):
            for l in lessons:
                if l.lesson_id == re1.lesson_id:
                    l.delta_evidence = dict(l.delta_evidence, strikes=3)
            return lessons
        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _bump)

        import runs
        rd = runs.create_run_dir("hrem", prompt="census", lane="agenda")
        for i in range(1, 7):  # >= shared min-calls floor (6)
            _write_call(rd / "build" / "calls", i, "navigator decision",
                        '{"move": "extend"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))

        class AlwaysExtend:
            def complete(self, messages, **kw_):
                return SimpleNamespace(content=json.dumps({"move": "extend"}))

        from delta_replay import run_effect_route
        out = run_effect_route(AlwaysExtend(), samples=1, remint_pending=True)
        row = next((r for r in out["census"] if r["lesson_id"] == re1.lesson_id),
                   None)
        assert row is not None
        assert row["delta"] == 0.0
        assert row["remint_watch_cleared"] is True
        rows = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        live = next(l for l in rows if l.lesson_id == re1.lesson_id)
        assert live.delta_evidence["route"] == "measured"

    def test_remint_pending_negative_delta_demotes_not_clears(self, monkeypatch, tmp_path):
        """The strike-3 lane acts by definition (2026-08-08 review): a
        decisively negative re-measurement must stamp effect-demote, not
        quietly end the probation."""
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")

        def _bump(lessons):
            for l in lessons:
                if l.lesson_id == re1.lesson_id:
                    l.delta_evidence = dict(l.delta_evidence, strikes=3)
            return lessons
        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _bump)

        import runs
        rd = runs.create_run_dir("hneg", prompt="census", lane="agenda")
        for i in range(1, 7):
            _write_call(rd / "build" / "calls", i, "navigator decision",
                        '{"move": "execute"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))

        from delta_replay import run_effect_route
        # ScriptedAdapter answers "extend" WITH the lesson, "execute"
        # without → with-arm always misses the execute oracle → Δ = −1.0.
        out = run_effect_route(ScriptedAdapter(REMINT_TEXT), samples=1,
                               remint_pending=True)
        row = next(r for r in out["census"] if r["lesson_id"] == re1.lesson_id)
        assert row["delta"] == -1.0
        assert row["demoted_by_effect"] is True
        assert row["remint_watch_cleared"] is False
        rows = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        live = next(l for l in rows if l.lesson_id == re1.lesson_id)
        assert live.delta_evidence["route"] == "effect-demote"

    def test_remint_pending_reaches_long_tier_watch_row(self, monkeypatch, tmp_path):
        """A watch row that tenure-promoted to LONG must stay selectable
        and clearable (2026-08-08 review: MEDIUM-only scan stranded it)."""
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")

        moved = {}

        def _pop(lessons):
            for l in lessons:
                if l.lesson_id == re1.lesson_id:
                    l.delta_evidence = dict(l.delta_evidence, strikes=3)
                    moved["row"] = l
            return [l for l in lessons if l.lesson_id != re1.lesson_id]
        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _pop)
        moved["row"].tier = kw.MemoryTier.LONG

        def _push(lessons):
            return lessons + [moved["row"]]
        kw._mutate_tiered_lessons(kw.MemoryTier.LONG, _push)

        import runs
        rd = runs.create_run_dir("hlng", prompt="census", lane="agenda")
        for i in range(1, 7):
            _write_call(rd / "build" / "calls", i, "navigator decision",
                        '{"move": "extend"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))

        class AlwaysExtend:
            def complete(self, messages, **kw_):
                return SimpleNamespace(content=json.dumps({"move": "extend"}))

        from delta_replay import run_effect_route
        out = run_effect_route(AlwaysExtend(), samples=1, remint_pending=True)
        row = next((r for r in out["census"]
                    if r["lesson_id"] == re1.lesson_id), None)
        assert row is not None, "LONG-tier watch row must be selectable"
        assert row["remint_watch_cleared"] is True
        longs = kw.load_tiered_lessons(tier=kw.MemoryTier.LONG, min_score=0.0)
        live = next(l for l in longs if l.lesson_id == re1.lesson_id)
        assert live.delta_evidence["route"] == "measured"

    def test_remint_pending_disabled_route_never_launders_decisive_delta(self, monkeypatch, tmp_path):
        """Round-2 review: with the demotion killswitch OFF, the demote
        route returns False for CONFIG reasons — a decisively negative
        re-measurement must leave the watch in place, not clear it to
        route 'measured'."""
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")

        def _bump(lessons):
            for l in lessons:
                if l.lesson_id == re1.lesson_id:
                    l.delta_evidence = dict(l.delta_evidence, strikes=3)
            return lessons
        kw._mutate_tiered_lessons(kw.MemoryTier.MEDIUM, _bump)

        import runs
        rd = runs.create_run_dir("hoff", prompt="census", lane="agenda")
        for i in range(1, 7):
            _write_call(rd / "build" / "calls", i, "navigator decision",
                        '{"move": "execute"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))

        # Flip the killswitch off AFTER the watch stamp exists
        monkeypatch.setattr(kw, "effect_demotion_enabled", lambda: False)

        from delta_replay import run_effect_route
        out = run_effect_route(ScriptedAdapter(REMINT_TEXT), samples=1,
                               remint_pending=True)
        row = next(r for r in out["census"] if r["lesson_id"] == re1.lesson_id)
        assert row["delta"] == -1.0
        assert row["demoted_by_effect"] is False  # killswitch held it
        assert row["remint_watch_cleared"] is False  # decisive ≠ neutral
        rows = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        live = next(l for l in rows if l.lesson_id == re1.lesson_id)
        assert live.delta_evidence["route"] == "remint-watch"

    def test_census_dry_run_never_clears_probation(self, monkeypatch, tmp_path):
        kw, _ = _demote_and_gc(monkeypatch, tmp_path)
        from memory import record_tiered_lesson
        re1 = record_tiered_lesson(REMINT_TEXT, "agenda", "done", "goal")
        import runs
        rd = runs.create_run_dir("hdry", prompt="census", lane="agenda")
        for i in range(1, 7):
            _write_call(rd / "build" / "calls", i, "navigator decision",
                        '{"move": "extend"}')
        (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))

        class AlwaysExtend:
            def complete(self, messages, **kw_):
                return SimpleNamespace(content=json.dumps({"move": "extend"}))

        from delta_replay import run_effect_route
        run_effect_route(AlwaysExtend(), samples=1,
                         lesson_ids=[re1.lesson_id])  # census-only naming
        rows = kw.load_tiered_lessons(tier=kw.MemoryTier.MEDIUM, min_score=0.0)
        live = next(l for l in rows if l.lesson_id == re1.lesson_id)
        assert live.delta_evidence["route"] == "remint-watch"
