"""Test doubles: no torch, no network, no real model. FakeEngine implements
the same Engine interface TorchEngine/MlxEngine do, so schema/prompt/scoring/
pmi/server code paths get exercised exactly as they would in production."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pcd_sidecar.engine import Engine


class FakeTokenizer:
    """Deterministic word-level 'tokenizer'. Splitting on whitespace means a
    prompt's token ids never change depending on what follows it, so the
    common-prefix logic in TorchEngine has a stable, testable analogue here
    even though FakeEngine doesn't use common_prefix_len internally."""

    eos_token_id = 0
    pad_token_id = 0

    def __init__(self):
        self._vocab = {}

    def _id_for(self, word: str) -> int:
        return self._vocab.setdefault(word, len(self._vocab) + 1)

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        parts = [f"<{m['role']}>{m['content']}</{m['role']}>" for m in messages]
        text = " ".join(parts)
        if add_generation_prompt:
            text += " <assistant>"
        return text

    def encode(self, text, add_special_tokens=False):
        return [self._id_for(tok) for tok in text.split(" ") if tok]


class FakeEngine(Engine):
    """Deterministic, controllable scoring: callers can pass a `scorer`
    function (prompt_text, candidate) -> float, or use the default which
    just prefers candidates whose text appears in the prompt (a cheap stand-
    in for "the state supports this answer")."""

    model_id = "fake-model"
    device = "cpu"

    def __init__(self, scorer=None):
        self.tokenizer = FakeTokenizer()
        self.calls = []
        self._scorer = scorer or self._default_scorer

    @staticmethod
    def _default_scorer(prompt_text: str, candidate: str) -> float:
        base = -len(candidate) * 0.05
        if candidate.lower() in prompt_text.lower():
            base += 2.0
        return base

    def token_count(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    def score_candidates(self, prompt_text, candidate_texts):
        self.calls.append((prompt_text, tuple(candidate_texts)))
        return [self._scorer(prompt_text, c) for c in candidate_texts]


@pytest.fixture
def fake_engine():
    return FakeEngine()
