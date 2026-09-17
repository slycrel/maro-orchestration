"""Scoring engines: score every candidate answer's *complete* token sequence
from the logits of a single shared prefill, batched in one forward pass.

Why complete sequences, not first tokens: upstream (harshatheg/Qwen-2.5-1B-
RLCD) ranks candidates by their first token and, when two candidates share a
token prefix, falls back to a synthesized 0.75-confidence split -- fabricated
numbers. Scoring the whole candidate sequence and summing log-probs removes
that collision path entirely: two candidates with a shared first token just
diverge later in the sum, no special case needed.

The "prefill once" part: the prompt text is identical for every candidate of
a given question, but naive string-splitting the prompt out of each
candidate's tokenization re-introduces exactly the bug above (a tokenizer is
not guaranteed to segment "prompt" the same way in isolation as it does
followed by different continuations). So instead each candidate is
tokenized as (prompt + candidate) independently, and the *longest common
token prefix actually observed* across all candidates -- not the prompt's
standalone token count -- is what gets prefilled once and shared via KV
cache. Only the diverging suffixes are scored in the second, batched pass.
"""
from __future__ import annotations

import abc
from typing import List, Optional


class Engine(abc.ABC):
    """Backend-agnostic scoring interface. `model_id` and `device` are
    surfaced verbatim in /healthz and the response envelope."""

    model_id: str
    device: str
    tokenizer: object  # exposes .encode() / .apply_chat_template(); duck-typed

    @abc.abstractmethod
    def score_candidates(self, prompt_text: str, candidate_texts: List[str]) -> List[float]:
        """Return one summed natural-log-probability per candidate, in the
        same order as `candidate_texts`, for completing `prompt_text` with
        that exact candidate string. Candidates that tokenize identically to
        another (fully collapse into the shared prefix) are a caller error;
        implementations should raise rather than silently drop a candidate.
        """


def common_prefix_len(token_lists: List[List[int]]) -> int:
    if not token_lists:
        return 0
    shortest = min(len(t) for t in token_lists)
    n = 0
    while n < shortest and all(t[n] == token_lists[0][n] for t in token_lists):
        n += 1
    return n


def resolve_device(requested: str) -> str:
    import torch

    if requested and requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(device: str, requested: Optional[str]):
    import torch

    name_map = {
        "bf16": torch.bfloat16,
        "bfloat16": torch.bfloat16,
        "fp16": torch.float16,
        "float16": torch.float16,
        "fp32": torch.float32,
        "float32": torch.float32,
    }
    if requested:
        key = requested.lower()
        if key not in name_map:
            raise ValueError(f"unknown PCD_DTYPE {requested!r}")
        return name_map[key]

    if device == "cuda":
        return torch.bfloat16
    if device == "mps":
        return torch.float16
    # cpu: bf16 is only fast here with hardware GEMM support (AVX512-BF16 or
    # AMX). Without it, torch still happily *runs* bf16 -- correctly, just
    # 10-50x slower (measured: a single 2048x2048 bf16 matmul took >120s on
    # an AVX2-only Coffee Lake box where the equivalent fp32 matmul is
    # near-instant). So this checks for the hardware feature rather than
    # timing a probe matmul, which could itself hang server startup on
    # exactly the hardware this is meant to protect. Falls back to fp32
    # (per deploy instructions) whenever that support can't be confirmed,
    # including non-Linux hosts where /proc/cpuinfo doesn't exist.
    if _cpu_has_fast_bf16_gemm():
        return torch.bfloat16
    return torch.float32


def _cpu_has_fast_bf16_gemm() -> bool:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("flags"):
                    return "avx512_bf16" in line or "amx_bf16" in line
    except OSError:
        pass
    return False


class TorchEngine(Engine):
    def __init__(self, model_id: str, device: str = "auto", dtype: Optional[str] = None,
                 threads: Optional[int] = None):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self.model_id = model_id
        self.device = resolve_device(device)
        resolved_dtype = resolve_dtype(self.device, dtype)

        if threads and self.device == "cpu":
            torch.set_num_threads(threads)

        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, dtype=resolved_dtype)
        self.model.to(self.device)
        self.model.eval()

        self.pad_token_id = self.tokenizer.pad_token_id
        if self.pad_token_id is None:
            self.pad_token_id = self.tokenizer.eos_token_id
        if self.pad_token_id is None:
            self.pad_token_id = 0

    def token_count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def score_candidates(self, prompt_text: str, candidate_texts: List[str]) -> List[float]:
        torch = self._torch
        if len(candidate_texts) < 1:
            raise ValueError("score_candidates needs at least one candidate")

        full_token_lists = [
            self.tokenizer.encode(prompt_text + c, add_special_tokens=False) for c in candidate_texts
        ]
        common_len = common_prefix_len(full_token_lists)
        suffixes = [ids[common_len:] for ids in full_token_lists]
        if any(len(s) == 0 for s in suffixes):
            raise ValueError(
                "two or more candidates tokenize identically for this prompt -- cannot rank"
            )

        B = len(candidate_texts)
        max_len = max(len(s) for s in suffixes)

        with torch.no_grad():
            past = None
            first_logprobs = None
            if common_len > 0:
                prefix_ids = torch.tensor([full_token_lists[0][:common_len]], device=self.device)
                prefill_out = self.model(input_ids=prefix_ids, use_cache=True)
                past = prefill_out.past_key_values
                past.batch_repeat_interleave(B)
                first_logprobs = torch.log_softmax(prefill_out.logits[:, -1, :].float(), dim=-1)
                first_logprobs = first_logprobs.expand(B, -1)

            input_ids = torch.full((B, max_len), self.pad_token_id, dtype=torch.long, device=self.device)
            suffix_mask = torch.zeros((B, max_len), dtype=torch.long, device=self.device)
            for i, s in enumerate(suffixes):
                input_ids[i, : len(s)] = torch.tensor(s, device=self.device)
                suffix_mask[i, : len(s)] = 1

            if past is not None:
                prefix_mask = torch.ones((B, common_len), dtype=torch.long, device=self.device)
                attention_mask = torch.cat([prefix_mask, suffix_mask], dim=1)
                position_ids = (
                    torch.arange(common_len, common_len + max_len, device=self.device)
                    .unsqueeze(0)
                    .expand(B, -1)
                )
                out = self.model(
                    input_ids=input_ids,
                    past_key_values=past,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                    use_cache=False,
                )
            else:
                out = self.model(input_ids=input_ids, attention_mask=suffix_mask, use_cache=False)

            cand_logprobs = torch.log_softmax(out.logits.float(), dim=-1)  # [B, max_len, vocab]

            totals = []
            for i, s in enumerate(suffixes):
                total = 0.0
                for t, tok in enumerate(s):
                    if t == 0:
                        if first_logprobs is not None:
                            total += first_logprobs[i, tok].item()
                        # else: no shared prefix at all (degenerate/empty
                        # prompt); the very first token is unconditioned and
                        # contributes 0 to every candidate equally, which is
                        # fine since only relative scores matter.
                    else:
                        total += cand_logprobs[i, t - 1, tok].item()
                totals.append(total)
            return totals
