"""Turn per-candidate log-probabilities into the three Jev answer shapes.

Confidence is documented, not hidden: 1 - normalised Shannon entropy of the
candidate distribution (0 = uniform/maximally unsure, 1 = all mass on one
candidate). Callers who want a different statistic have the raw
`probabilities` map to compute it themselves.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .schema import Question


class ScoringError(Exception):
    """Raised when candidates can't be meaningfully scored/ranked.
    The server maps this to a 500 -- never a silent fallback answer."""


def softmax(logprobs: List[float]) -> List[float]:
    if not logprobs:
        raise ScoringError("no candidates to score")
    m = max(logprobs)
    exps = [math.exp(x - m) for x in logprobs]
    total = sum(exps)
    if total <= 0:
        raise ScoringError("degenerate candidate distribution (softmax collapsed to zero)")
    return [e / total for e in exps]


def normalised_entropy_confidence(probs: List[float]) -> float:
    k = len(probs)
    if k <= 1:
        return 1.0
    h = -sum(p * math.log(p) for p in probs if p > 0)
    h_norm = h / math.log(k)
    return max(0.0, min(1.0, 1.0 - h_norm))


def pmi_adjust(state_logprobs: List[float], baseline_logprobs: Optional[List[float]]) -> List[float]:
    if baseline_logprobs is None:
        return state_logprobs
    if len(baseline_logprobs) != len(state_logprobs):
        raise ScoringError("PMI baseline candidate count does not match state candidate count")
    return [s - b for s, b in zip(state_logprobs, baseline_logprobs)]


def combine(
    question: Question,
    state_logprobs: List[float],
    baseline_logprobs: Optional[List[float]],
) -> Dict:
    labels = [label for label, _ in question.candidates]
    if len(state_logprobs) != len(labels):
        raise ScoringError(
            f"question '{question.id}': expected {len(labels)} candidate scores, got {len(state_logprobs)}"
        )

    adjusted = pmi_adjust(state_logprobs, baseline_logprobs)
    probs = softmax(adjusted)
    prob_map = dict(zip(labels, probs))
    confidence = normalised_entropy_confidence(probs)

    if question.type == "noul":
        return {"type": "noul", "noul": prob_map.get("yes", 0.0)}

    if question.type == "choice":
        best_idx = max(range(len(probs)), key=lambda i: probs[i])
        return {
            "type": "choice",
            "choice": labels[best_idx],
            "confidence": confidence,
            "probabilities": prob_map,
        }

    if question.type == "score":
        expected = sum(i * p for i, p in enumerate(probs))
        return {
            "type": "score",
            "score": expected,
            "confidence": confidence,
            "legend": dict(question.legend),
            "probabilities": prob_map,
        }

    raise ScoringError(f"unknown question type '{question.type}'")
