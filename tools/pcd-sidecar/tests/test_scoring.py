import math

import pytest

from pcd_sidecar.schema import parse_question
from pcd_sidecar.scoring import (
    ScoringError,
    combine,
    normalised_entropy_confidence,
    pmi_adjust,
    softmax,
)


def test_softmax_sums_to_one():
    probs = softmax([1.0, 2.0, -1.0, 0.5])
    assert math.isclose(sum(probs), 1.0, rel_tol=1e-9)
    assert all(p >= 0 for p in probs)


def test_softmax_empty_raises():
    with pytest.raises(ScoringError):
        softmax([])


def test_confidence_uniform_is_zero():
    probs = [0.25, 0.25, 0.25, 0.25]
    assert normalised_entropy_confidence(probs) == pytest.approx(0.0, abs=1e-9)


def test_confidence_certain_is_one():
    probs = [1.0, 0.0, 0.0]
    assert normalised_entropy_confidence(probs) == pytest.approx(1.0, abs=1e-9)


def test_confidence_single_candidate_is_one():
    assert normalised_entropy_confidence([1.0]) == 1.0


def test_pmi_adjust_noop_when_no_baseline():
    logprobs = [-1.0, -2.0]
    assert pmi_adjust(logprobs, None) == logprobs


def test_pmi_adjust_subtracts_baseline():
    assert pmi_adjust([-1.0, -3.0], [-0.5, -0.5]) == [-0.5, -2.5]


def test_pmi_adjust_mismatched_lengths_raises():
    with pytest.raises(ScoringError):
        pmi_adjust([-1.0, -2.0], [-0.5])


def test_combine_noul_shape_and_probability():
    q = parse_question("q1", {"type": "noul", "instructions": "i"})
    answer = combine(q, [-0.1, -3.0], None)  # candidates = [yes, no]
    assert answer["type"] == "noul"
    assert 0.0 <= answer["noul"] <= 1.0
    assert answer["noul"] > 0.5  # yes scored much higher


def test_combine_choice_shape_probabilities_sum_to_one():
    q = parse_question(
        "q1", {"type": "choice", "instructions": "i", "criteria": {"done": None, "blocked": None, "unclear": None}}
    )
    answer = combine(q, [-0.1, -5.0, -5.0], None)
    assert answer["type"] == "choice"
    assert answer["choice"] == "done"
    assert math.isclose(sum(answer["probabilities"].values()), 1.0, rel_tol=1e-9)
    assert set(answer["probabilities"]) == {"done", "blocked", "unclear"}
    assert 0.0 <= answer["confidence"] <= 1.0


def test_combine_score_shape_expected_index_and_legend():
    q = parse_question("q1", {"type": "score", "instructions": "i", "criteria": ["low", "mid", "high"]})
    # heavy weight on the last level
    answer = combine(q, [-5.0, -5.0, -0.1], None)
    assert answer["type"] == "score"
    assert answer["legend"] == {"0": "low", "1": "mid", "2": "high"}
    assert math.isclose(sum(answer["probabilities"].values()), 1.0, rel_tol=1e-9)
    assert answer["score"] > 1.5  # leans toward index 2


def test_combine_with_pmi_correction_changes_ranking():
    q = parse_question("q1", {"type": "choice", "instructions": "i", "criteria": {"a": None, "b": None}})
    # "a" looks better raw, but is equally favored by the baseline (common word),
    # so after PMI correction "b" should win.
    raw = [-1.0, -2.0]
    baseline = [-1.0, -3.0]
    answer = combine(q, raw, baseline)
    assert answer["choice"] == "b"


def test_combine_rejects_wrong_candidate_count():
    q = parse_question("q1", {"type": "noul", "instructions": "i"})
    with pytest.raises(ScoringError):
        combine(q, [-1.0, -2.0, -3.0], None)
