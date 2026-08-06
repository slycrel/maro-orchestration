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
