"""Tests for the navigator decision schema (goal-brain step 4).

Schema layer only — parsing and validation. There is no decision logic to
test until the prompt (step 5) exists. See docs/NAVIGATOR_SCHEMA.md.
"""
import json

import pytest

from navigator import (
    CHILD_DISPOSITIONS,
    DEFAULT_PLANNING_DEPTH,
    FORK_CHILD_CAP,
    MOVES,
    PLANNING_DEPTHS,
    ChildSummary,
    DecisionParseError,
    NavigatorDecision,
    NavigatorInput,
    WorkReport,
    parse_decision,
    validate_decision,
)


def _decision(move="execute", reasoning="because", confidence=0.8, **payload):
    return NavigatorDecision(
        move=move, reasoning=reasoning, confidence=confidence, payload=payload,
    )


def _child(handle_id="c1", state="open", goal="check reddit for X"):
    return ChildSummary(handle_id=handle_id, goal=goal, state=state)


class TestParseDecision:
    def test_raw_json(self):
        d = parse_decision(json.dumps({
            "move": "execute",
            "reasoning": "next step is concrete",
            "confidence": 0.7,
            "payload": {"instruction": "run the fetch"},
        }))
        assert d.move == "execute"
        assert d.confidence == 0.7
        assert d.payload["instruction"] == "run the fetch"

    def test_fenced_json_block(self):
        text = (
            "Here is my decision:\n```json\n"
            '{"move": "close", "reasoning": "done", "confidence": 1.0,\n'
            ' "payload": {"closure": "delivered", "verdict": "shipped"}}\n'
            "```\nThanks!"
        )
        d = parse_decision(text)
        assert d.move == "close"
        assert d.payload["closure"] == "delivered"

    def test_json_embedded_in_prose(self):
        text = (
            'Thinking... the answer is {"move": "idunno", "reasoning": "unclear", '
            '"confidence": 0.2, "payload": {"confusion": "goal is ambiguous"}} done'
        )
        d = parse_decision(text)
        assert d.move == "idunno"
        assert d.payload["confusion"] == "goal is ambiguous"

    def test_move_normalized_to_lowercase(self):
        d = parse_decision('{"move": "Execute", "reasoning": "r", "confidence": 0.5}')
        assert d.move == "execute"

    def test_missing_payload_defaults_empty(self):
        d = parse_decision('{"move": "execute", "reasoning": "r", "confidence": 0.5}')
        assert d.payload == {}

    def test_no_json_raises(self):
        with pytest.raises(DecisionParseError):
            parse_decision("I think we should probably execute the next step.")

    def test_empty_raises(self):
        with pytest.raises(DecisionParseError):
            parse_decision("")

    def test_non_dict_payload_raises(self):
        with pytest.raises(DecisionParseError):
            parse_decision(
                '{"move": "execute", "reasoning": "r", "confidence": 0.5, '
                '"payload": ["not", "a", "dict"]}')

    def test_non_numeric_confidence_raises(self):
        with pytest.raises(DecisionParseError):
            parse_decision(
                '{"move": "execute", "reasoning": "r", "confidence": "high"}')


class TestPlanningDepthParsing:
    """Thread-arch #5 (MILESTONES 1.5): planning_depth rides the same
    envelope as move. It is advisory shadow data, not core decision
    mechanics — absent or malformed values fail closed to "plan" rather
    than raising, so a bad/missing depth judgment never blocks or retries
    the underlying move decision (parse_decision's docstring)."""

    @pytest.mark.parametrize("depth", sorted(PLANNING_DEPTHS - {"plan"}))
    def test_non_default_depth_round_trips(self, depth):
        """Every non-default PLANNING_DEPTHS member parses through
        untouched — includes spawn-sub-goal (2026-07-09 recursion decree: a
        legal shape, not an enum afterthought), collapsed from 3 near-
        duplicate single-value tests (Minimalist finding #4, adversarial-
        review batch-1, 2026-07-13)."""
        d = parse_decision(json.dumps({
            "move": "execute", "reasoning": "r", "confidence": 0.6,
            "planning_depth": depth,
        }))
        assert d.planning_depth == depth

    def test_case_normalized(self):
        d = parse_decision(json.dumps({
            "move": "execute", "reasoning": "r", "confidence": 0.7,
            "planning_depth": "One-Shot",
        }))
        assert d.planning_depth == "one-shot"

    def test_absent_field_defaults_to_plan(self):
        d = parse_decision(json.dumps({
            "move": "execute", "reasoning": "r", "confidence": 0.5,
        }))
        assert d.planning_depth == DEFAULT_PLANNING_DEPTH == "plan"

    def test_malformed_value_fails_closed_to_plan(self):
        """An unrecognized string must not raise DecisionParseError — the
        move/confidence/payload core must still parse successfully."""
        d = parse_decision(json.dumps({
            "move": "execute", "reasoning": "r", "confidence": 0.5,
            "planning_depth": "full-blown-mega-plan",
        }))
        assert d.planning_depth == "plan"
        assert d.move == "execute"  # the core decision is unaffected

    def test_non_string_value_fails_closed_to_plan(self):
        d = parse_decision(json.dumps({
            "move": "execute", "reasoning": "r", "confidence": 0.5,
            "planning_depth": 7,
        }))
        assert d.planning_depth == "plan"

    def test_default_on_direct_construction(self):
        d = NavigatorDecision(move="execute", reasoning="r", confidence=0.5)
        assert d.planning_depth == "plan"


class TestPlanningDepthAddendumSync:
    """Architect finding #5 (adversarial-review batch-1, 2026-07-13):
    PLANNING_DEPTHS (navigator.py) and PLANNING_DEPTH_ADDENDUM
    (navigator_prompt.py) are two hand-maintained encodings of the same
    vocabulary with nothing generating one from the other — parse_decision
    silently coerces any value the addendum forgets to mention back to
    "plan", so a drift here would be indistinguishable from "the model
    chose plan". This pins that every PLANNING_DEPTHS member is still named
    in the addendum the model actually reads."""

    def test_addendum_names_every_planning_depth(self):
        from navigator_prompt import PLANNING_DEPTH_ADDENDUM
        for depth in PLANNING_DEPTHS:
            assert f'"{depth}"' in PLANNING_DEPTH_ADDENDUM, (
                f"{depth!r} is a legal PLANNING_DEPTHS value but isn't "
                "named in PLANNING_DEPTH_ADDENDUM — the model can never "
                "be told to choose it"
            )


class TestValidateEnvelope:
    def test_valid_execute(self):
        assert validate_decision(_decision(instruction="do it")) == []

    def test_unknown_move(self):
        errs = validate_decision(_decision(move="retry", instruction="x"))
        assert len(errs) == 1
        assert "unknown move" in errs[0]

    def test_empty_reasoning_rejected(self):
        errs = validate_decision(_decision(reasoning="", instruction="x"))
        assert any("reasoning" in e for e in errs)

    def test_confidence_out_of_range(self):
        errs = validate_decision(_decision(confidence=1.5, instruction="x"))
        assert any("confidence" in e for e in errs)

    def test_all_moves_have_required_payload_spec(self):
        # Every move in MOVES must be coverable by validation.
        for move in MOVES:
            errs = validate_decision(_decision(move=move))
            # With an empty payload, every move except none should complain
            # about its required keys — proving the spec covers all moves.
            assert any("missing required key" in e for e in errs), move


class TestValidateMovePayloads:
    def test_extend_requires_expected_artifact(self):
        errs = validate_decision(_decision(move="extend", instruction="plan it"))
        assert any("expected_artifact" in e for e in errs)

    def test_fork_children_must_be_nonempty(self):
        errs = validate_decision(_decision(move="fork", children=[]))
        assert any("children" in e for e in errs)

    def test_fork_cap_enforced(self):
        kids = [{"goal": f"g{i}", "context": ""} for i in range(FORK_CHILD_CAP + 1)]
        errs = validate_decision(_decision(move="fork", children=kids))
        assert any("cap" in e for e in errs)

    def test_fork_child_missing_goal(self):
        errs = validate_decision(
            _decision(move="fork", children=[{"context": "no goal here"}]))
        assert any("missing 'goal'" in e for e in errs)

    def test_fork_valid(self):
        kids = [{"goal": "check reddit", "context": "item X"},
                {"goal": "check craigslist", "context": "item X"}]
        assert validate_decision(_decision(move="fork", children=kids)) == []

    def test_collate_unknown_child_rejected(self):
        nav_in = NavigatorInput(goal="g", open_children=[_child("c1", "done")])
        errs = validate_decision(
            _decision(move="collate", instruction="merge",
                      child_handle_ids=["c1", "ghost"]),
            nav_in)
        assert any("ghost" in e for e in errs)

    def test_collate_valid(self):
        nav_in = NavigatorInput(goal="g", open_children=[
            _child("c1", "done"), _child("c2", "done")])
        errs = validate_decision(
            _decision(move="collate", instruction="merge",
                      child_handle_ids=["c1", "c2"]),
            nav_in)
        assert errs == []

    def test_escalate_requires_question_and_why(self):
        errs = validate_decision(_decision(move="escalate", question="proceed?"))
        assert any("'why'" in e for e in errs)

    def test_idunno_requires_confusion(self):
        errs = validate_decision(_decision(move="idunno"))
        assert any("confusion" in e for e in errs)


class TestCloseRule:
    """The fan-out lesson as a validator: close must disposition every
    open child."""

    def test_close_with_undispositioned_child_rejected(self):
        nav_in = NavigatorInput(goal="g", open_children=[_child("c1")])
        errs = validate_decision(
            _decision(move="close", closure="delivered", verdict="done"),
            nav_in)
        assert any("undispositioned" in e for e in errs)

    def test_close_must_cover_every_child(self):
        nav_in = NavigatorInput(goal="g", open_children=[
            _child("c1"), _child("c2", state="failed")])
        errs = validate_decision(
            _decision(move="close", closure="delivered", verdict="done",
                      children_disposition={"c1": "done"}),
            nav_in)
        assert any("c2" in e for e in errs)

    def test_close_fully_dispositioned_valid(self):
        nav_in = NavigatorInput(goal="g", open_children=[
            _child("c1"), _child("c2", state="failed")])
        errs = validate_decision(
            _decision(move="close", closure="delivered", verdict="2 of 3 sources",
                      children_disposition={"c1": "done", "c2": "abandoned"}),
            nav_in)
        assert errs == []

    def test_invalid_disposition_value(self):
        nav_in = NavigatorInput(goal="g", open_children=[_child("c1")])
        errs = validate_decision(
            _decision(move="close", closure="delivered", verdict="v",
                      children_disposition={"c1": "forgotten"}),
            nav_in)
        assert any("invalid" in e for e in errs)
        assert all(d in {"done", "abandoned", "absorbed"}
                   for d in CHILD_DISPOSITIONS)

    def test_close_without_children_needs_no_disposition(self):
        nav_in = NavigatorInput(goal="g")
        errs = validate_decision(
            _decision(move="close", closure="abandoned", verdict="superseded"),
            nav_in)
        assert errs == []

    def test_unknown_closure_type(self):
        errs = validate_decision(
            _decision(move="close", closure="finished", verdict="v"))
        assert any("closure" in e for e in errs)


class TestInstrumentationShapes:
    def test_input_digest_carries_shape_not_content(self):
        nav_in = NavigatorInput(
            goal="a goal " * 50,
            goal_brain="brain text",
            turn_index=3,
            last_work=WorkReport(
                move="execute", status="failed", summary="s",
                recommendation="retry", signals={"errors": 1}),
            open_children=[_child("c1"), _child("c2")],
            recall_block="x" * 500,
            budget={"tokens": 1200},
        )
        d = nav_in.digest()
        assert len(d["goal_preview"]) <= 120
        assert d["turn_index"] == 3
        assert d["open_children"] == 2
        assert d["has_last_work"] is True
        assert d["last_work_status"] == "failed"
        assert d["recall_chars"] == 500
        assert d["budget"] == {"tokens": 1200}
        # no raw recall/goal-brain text in the digest
        assert "x" * 50 not in json.dumps(d)

    def test_payload_digest_sizes_not_text(self):
        d = _decision(move="fork",
                      children=[{"goal": "g1"}, {"goal": "g2"}],
                      note="secret-ish long text")
        pd = d.payload_digest()
        assert pd["children"] == "list:2"
        assert pd["note"].startswith("str:")
        assert "secret" not in json.dumps(pd)

    def test_to_dict_round_trips_through_parse(self):
        d = _decision(instruction="do the thing")
        again = parse_decision(json.dumps(d.to_dict()))
        assert again.move == d.move
        assert again.payload == d.payload
        assert again.planning_depth == d.planning_depth == "plan"
        assert validate_decision(again) == []

    def test_to_dict_round_trips_non_default_planning_depth(self):
        d = NavigatorDecision(move="execute", reasoning="r", confidence=0.6,
                              payload={"instruction": "go"},
                              planning_depth="thin-plan")
        assert d.to_dict()["planning_depth"] == "thin-plan"
        again = parse_decision(json.dumps(d.to_dict()))
        assert again.planning_depth == "thin-plan"
