"""Optional Apple Silicon backend using mlx / mlx_lm.

This does NOT share a prefilled KV cache across candidates the way
TorchEngine does -- mlx_lm's cache API doesn't expose a cheap batch-expand
primitive, and the task explicitly says this path should only be built if
it's cheap. Instead each candidate's full (prompt + candidate) sequence is
scored independently in one forward pass per candidate. Correctness is
identical (same complete-sequence summed log-prob, same PMI correction);
only the "prefill once" compute saving is skipped here. Selected via
PCD_DEVICE=mlx or automatically as a fallback when torch has no
accelerator on an Apple host -- see config.py.
"""
from __future__ import annotations

from typing import List

from .engine import Engine


class MlxEngine(Engine):
    def __init__(self, model_id: str, dtype=None, threads=None):
        import mlx.core as mx
        from mlx_lm.utils import load

        self._mx = mx
        self.model_id = model_id
        self.device = "mlx"
        self.model, self.tokenizer = load(model_id)

    def token_count(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    def score_candidates(self, prompt_text: str, candidate_texts: List[str]) -> List[float]:
        mx = self._mx
        if not candidate_texts:
            raise ValueError("score_candidates needs at least one candidate")

        totals = []
        for cand in candidate_texts:
            ids = self.tokenizer.encode(prompt_text + cand)
            if len(ids) < 2:
                raise ValueError("candidate sequence too short to score")
            input_ids = mx.array([ids[:-1]])
            logits = self.model(input_ids)
            logprobs = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
            targets = ids[1:]
            total = 0.0
            for t, tok in enumerate(targets):
                total += float(logprobs[0, t, tok])
            totals.append(total)
        return totals
