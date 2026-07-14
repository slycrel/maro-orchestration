"""Free regression eval for the local validator.

Replays labeled step-validation cases (tests/fixtures/validation_cases.json)
through the *local* (zero-cost) validator and asserts aggregate accuracy. This
catches validation-quality regressions — a model/prompt/wiring change that makes
the validator accept broken outputs or reject good ones — without spending API
tokens.

Gated: skips unless a local validator endpoint is actually serving the model, so
the normal CI suite (no MLX/Ollama server) is unaffected. To run it:

    scripts/local-validator.sh start            # serve VibeThinker on :8088
    PYTHONPATH=src pytest tests/test_validator_eval.py -q -s

Override target via env: MARO_VALIDATOR_EVAL_MODEL, LOCAL_VALIDATOR_ENDPOINT.
Assertion is on aggregate accuracy (not per-case verdicts) so it's robust to a
small model's sampling noise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import local_models as lm
from verification_agent import VerificationAgent

_FIXTURES = Path(__file__).parent / "fixtures" / "validation_cases.json"
_MODEL = os.environ.get("MARO_VALIDATOR_EVAL_MODEL", "mlx-community/VibeThinker-3B-4bit")
_ENDPOINT = os.environ.get("LOCAL_VALIDATOR_ENDPOINT", "http://127.0.0.1:8088/v1")
# A small specialist won't be perfect; require clear majority-correct discrimination.
_MIN_ACCURACY = float(os.environ.get("MARO_VALIDATOR_EVAL_MIN_ACC", "0.75"))


def _load_cases() -> list[dict]:
    data = json.loads(_FIXTURES.read_text())
    return data["cases"]


def _server_has_model() -> bool:
    try:
        return _MODEL in lm.loaded_models(_ENDPOINT)
    except Exception:
        return False


_SKIP_EVAL = pytest.mark.skipif(
    not _server_has_model(),
    reason=f"local validator {_MODEL} not loaded at {_ENDPOINT} — start it to run this eval",
)


@_SKIP_EVAL
def test_local_validator_discriminates_good_from_bad():
    cases = _load_cases()
    adapter = lm.LocalValidatorAdapter(_MODEL, endpoint=_ENDPOINT)
    va = VerificationAgent(
        adapter,
        confidence_threshold=lm.min_certainty(),
        max_input_chars=lm.input_char_budget(),
    )

    rows, correct = [], 0
    for c in cases:
        verdict = va.verify_step(c["step_text"], c["result"])
        got = verdict.passed
        ok = got == c["expect_pass"]
        correct += ok
        rows.append((
            c["id"], c["expect_pass"], got, round(verdict.confidence, 2), ok,
            verdict.confidence >= lm.min_certainty(),
        ))

    accuracy = correct / len(cases)
    print(f"\nlocal validator eval — model={_MODEL} accuracy={accuracy:.0%} ({correct}/{len(cases)})")
    for cid, exp, got, conf, ok, _decisive in rows:
        print(f"  [{'ok ' if ok else 'MISS'}] {cid:32s} expect_pass={exp!s:5s} got={got!s:5s} conf={conf}")

    # A low-confidence nominal PASS escalates and is operationally safe. A bad
    # case accepted at or above min_certainty is the regression that matters.
    unsafe_false_pass = [
        r[0] for r in rows
        if r[1] is False and r[2] is True and r[5]
    ]
    assert unsafe_false_pass == [], (
        f"validator produced unsafe decisive false-passes: {unsafe_false_pass}"
    )
    assert accuracy >= _MIN_ACCURACY, (
        f"validator accuracy {accuracy:.0%} < {_MIN_ACCURACY:.0%}; "
        f"unsafe_false_passes={unsafe_false_pass}"
    )


def test_fixtures_are_balanced_and_wellformed():
    # Cheap, always-runs guard so the corpus itself can't silently rot.
    cases = _load_cases()
    # Eight original task/result pairs plus six committed path, constraint,
    # and execution-result cases form the shared bake-off corpus.
    assert len(cases) >= 14
    pos = [c for c in cases if c["expect_pass"] is True]
    neg = [c for c in cases if c["expect_pass"] is False]
    assert pos and neg, "need both PASS and FAIL cases to test discrimination"
    assert len(pos) == len(neg), "shared bake-off corpus must remain balanced"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        assert c["step_text"].strip() and c["result"].strip()
