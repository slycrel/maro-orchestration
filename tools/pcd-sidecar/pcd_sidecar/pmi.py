"""Domain-conditional PMI baseline cache.

Upstream (harshatheg/Qwen-2.5-1B-RLCD) ranks candidates by raw summed
log-prob, which biases toward short strings / common tokens (surface form
competition, see Holtzman et al. 2021 "Surface Form Competition"). The
correction divides each candidate's likelihood under the real state by its
likelihood under a neutral, state-free prompt for the *same question*. That
baseline does not depend on `state`, so it's cached per (model, question)
the first time a question is seen and reused for every later request.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Dict, List, Optional, Tuple

from .prompt import build_baseline_prompt
from .schema import Question


def question_cache_key(question: Question) -> str:
    payload = json.dumps(
        {
            "type": question.type,
            "instructions": question.instructions,
            "criteria": question.criteria,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PMICache:
    """Wraps an Engine and memoises its state-free ("baseline") candidate
    log-probs per (model_id, question hash). Thread-safe; safe to disable
    entirely via `enabled=False` (PCD_PMI=0) for A/B comparison."""

    def __init__(self, engine, enabled: bool = True):
        self.engine = engine
        self.enabled = enabled
        self._cache: Dict[Tuple[str, str], List[float]] = {}
        self._lock = threading.Lock()

    def baseline_logprobs(self, question: Question) -> Optional[List[float]]:
        if not self.enabled:
            return None
        key = (self.engine.model_id, question_cache_key(question))
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        prompt_text = build_baseline_prompt(self.engine.tokenizer, question)
        candidate_texts = [text for _, text in question.candidates]
        scores = self.engine.score_candidates(prompt_text, candidate_texts)

        with self._lock:
            self._cache[key] = scores
        return scores

    def cache_size(self) -> int:
        with self._lock:
            return len(self._cache)
