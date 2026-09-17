"""Request/response schema for the /v1/systemone wire contract.

No pydantic, no dataclasses-as-validators library -- plain dicts in,
small hand-checked validation, plain dicts out. Every rejection raises
ValidationError, which the server maps to HTTP 400 with {"error": msg}.
Nothing here ever fabricates a fallback answer; a bad request just fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

QUESTION_TYPES = ("noul", "choice", "score")


class ValidationError(Exception):
    """A malformed request. The server turns this into a 400."""


@dataclass
class Question:
    id: str
    type: str
    instructions: str
    criteria: Any
    # legend: label -> description shown to the caller/back in the answer.
    # For "score" this is {"0": level0, "1": level1, ...}.
    legend: Dict[str, Any]
    # candidates: ordered (label, completion_text) pairs. `label` is the key
    # used in the answer (option name, "yes"/"no", or the index string).
    # `completion_text` is the literal string the model is asked to complete
    # the assistant turn with, and is what actually gets tokenized/scored.
    candidates: List[Tuple[str, str]] = field(default_factory=list)


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{what} must be a non-empty string")
    return value


def parse_question(qid: str, raw: Any) -> Question:
    if not isinstance(qid, str) or not qid:
        raise ValidationError("question ids must be non-empty strings")
    if not isinstance(raw, dict):
        raise ValidationError(f"question '{qid}': must be an object")

    qtype = raw.get("type")
    if qtype not in QUESTION_TYPES:
        raise ValidationError(
            f"question '{qid}': 'type' must be one of {QUESTION_TYPES}, got {qtype!r}"
        )
    instructions = _require_str(raw.get("instructions"), f"question '{qid}': 'instructions'")
    criteria = raw.get("criteria")

    if qtype == "noul":
        if criteria is None:
            criteria = {}
        if not isinstance(criteria, dict):
            raise ValidationError(f"question '{qid}': noul 'criteria' must be an object or omitted")
        legend = {"true": criteria.get("true"), "false": criteria.get("false")}
        candidates = [("yes", "yes"), ("no", "no")]

    elif qtype == "choice":
        if not isinstance(criteria, dict) or not criteria:
            raise ValidationError(f"question '{qid}': choice 'criteria' must be a non-empty object")
        for opt in criteria:
            if not isinstance(opt, str) or not opt:
                raise ValidationError(f"question '{qid}': choice option keys must be non-empty strings")
        legend = dict(criteria)
        candidates = [(opt, opt) for opt in criteria.keys()]

    else:  # score
        if not isinstance(criteria, list) or not criteria:
            raise ValidationError(f"question '{qid}': score 'criteria' must be a non-empty ordered list")
        for level in criteria:
            if not isinstance(level, str) or not level:
                raise ValidationError(f"question '{qid}': every score level must be a non-empty string")
        legend = {str(i): level for i, level in enumerate(criteria)}
        candidates = [(str(i), level) for i, level in enumerate(criteria)]

    if len(candidates) < 2:
        raise ValidationError(f"question '{qid}': needs at least two distinguishable candidates")

    return Question(
        id=qid,
        type=qtype,
        instructions=instructions,
        criteria=criteria,
        legend=legend,
        candidates=candidates,
    )


def parse_request(body: Any) -> Tuple[str, Any, Dict[str, Question]]:
    """Validate a decoded JSON request body.

    Returns (model_name, state, {question_id: Question}).
    """
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")

    model_name = _require_str(body.get("model"), "'model'")

    if "state" not in body:
        raise ValidationError("'state' is required")
    state = body["state"]
    if not isinstance(state, (str, dict, list)):
        raise ValidationError("'state' must be a string, object, or array")

    raw_questions = body.get("questions")
    if not isinstance(raw_questions, dict) or not raw_questions:
        raise ValidationError("'questions' must be a non-empty object of id -> Question")

    questions: Dict[str, Question] = {}
    for qid, raw_q in raw_questions.items():
        questions[qid] = parse_question(qid, raw_q)

    return model_name, state, questions
