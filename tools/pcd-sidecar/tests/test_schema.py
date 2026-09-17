import pytest

from pcd_sidecar.schema import ValidationError, parse_question, parse_request


def test_parse_request_valid_minimal():
    body = {
        "model": "jev-1",
        "state": {"goal": "x", "result": "y"},
        "questions": {
            "q1": {"type": "noul", "instructions": "did it work?"},
        },
    }
    model_name, state, questions = parse_request(body)
    assert model_name == "jev-1"
    assert state == {"goal": "x", "result": "y"}
    assert set(questions) == {"q1"}
    assert questions["q1"].candidates == [("yes", "yes"), ("no", "no")]


@pytest.mark.parametrize("state", ["a plain string state", {"a": 1}, [1, 2, 3]])
def test_state_accepts_string_object_array(state):
    body = {"model": "m", "state": state, "questions": {"q": {"type": "noul", "instructions": "i"}}}
    _, parsed_state, _ = parse_request(body)
    assert parsed_state == state


@pytest.mark.parametrize("bad_state", [1, 1.5, True, None])
def test_state_rejects_scalars(bad_state):
    body = {"model": "m", "state": bad_state, "questions": {"q": {"type": "noul", "instructions": "i"}}}
    with pytest.raises(ValidationError):
        parse_request(body)


def test_missing_model_rejected():
    with pytest.raises(ValidationError):
        parse_request({"state": "s", "questions": {"q": {"type": "noul", "instructions": "i"}}})


def test_missing_state_rejected():
    with pytest.raises(ValidationError):
        parse_request({"model": "m", "questions": {"q": {"type": "noul", "instructions": "i"}}})


def test_empty_questions_rejected():
    with pytest.raises(ValidationError):
        parse_request({"model": "m", "state": "s", "questions": {}})


def test_non_object_body_rejected():
    with pytest.raises(ValidationError):
        parse_request(["not", "an", "object"])


def test_noul_with_criteria():
    q = parse_question("q1", {"type": "noul", "instructions": "i", "criteria": {"true": "T", "false": "F"}})
    assert q.legend == {"true": "T", "false": "F"}
    assert q.candidates == [("yes", "yes"), ("no", "no")]


def test_choice_requires_nonempty_criteria_dict():
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "choice", "instructions": "i", "criteria": {}})
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "choice", "instructions": "i", "criteria": ["a", "b"]})


def test_choice_valid():
    q = parse_question(
        "q1",
        {"type": "choice", "instructions": "i", "criteria": {"done": "finished", "blocked": None}},
    )
    assert q.candidates == [("done", "done"), ("blocked", "blocked")]
    assert q.legend == {"done": "finished", "blocked": None}


def test_score_requires_ordered_nonempty_list_of_strings():
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "score", "instructions": "i", "criteria": []})
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "score", "instructions": "i", "criteria": {"0": "a"}})
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "score", "instructions": "i", "criteria": ["ok", 5]})


def test_score_valid_preserves_order():
    q = parse_question("q1", {"type": "score", "instructions": "i", "criteria": ["low", "mid", "high"]})
    assert q.candidates == [("0", "low"), ("1", "mid"), ("2", "high")]
    assert q.legend == {"0": "low", "1": "mid", "2": "high"}


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "bogus", "instructions": "i", "criteria": {}})


def test_missing_instructions_rejected():
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "noul", "instructions": ""})
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "noul"})


def test_choice_with_single_option_rejected():
    # needs at least two distinguishable candidates to be rankable
    with pytest.raises(ValidationError):
        parse_question("q1", {"type": "choice", "instructions": "i", "criteria": {"only": "one"}})
