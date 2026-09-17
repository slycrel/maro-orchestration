"""Prompt construction: chat-templated (system=instructions+legend, user=state,
assistant prefix = "<question id>: "). No free-text generation ever happens --
this text is only ever used as the shared prefix that candidate completions
get scored against.
"""
from __future__ import annotations

import json

from .schema import Question

# Used only for PMI baselines: a state-free prompt, so the baseline
# likelihood is exactly "how likely is this candidate given the question
# alone", cacheable per (model, question) since it never depends on `state`.
NEUTRAL_STATE = "(no state given)"


def render_state(state) -> str:
    if isinstance(state, str):
        return state
    return json.dumps(state, separators=(",", ":"), sort_keys=False)


def render_legend(question: Question) -> str:
    lines = []
    if question.type == "noul":
        lines.append("Answer yes or no.")
        true_desc = question.legend.get("true")
        false_desc = question.legend.get("false")
        if true_desc:
            lines.append(f"yes: {true_desc}")
        if false_desc:
            lines.append(f"no: {false_desc}")
    elif question.type == "choice":
        lines.append("Choose exactly one of the following options:")
        for opt, desc in question.legend.items():
            lines.append(f"{opt}: {desc}" if desc else f"{opt}")
    else:  # score
        lines.append("Choose the level that best applies, from lowest to highest:")
        for idx, text in question.legend.items():
            lines.append(f"{idx}: {text}")
    return "\n".join(lines)


def build_prompt_text(tokenizer, state_text: str, question: Question) -> str:
    system = question.instructions.strip() + "\n\n" + render_legend(question)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": state_text},
    ]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    prompt += f"{question.id}: "
    return prompt


def build_state_prompt(tokenizer, state, question: Question) -> str:
    return build_prompt_text(tokenizer, render_state(state), question)


def build_baseline_prompt(tokenizer, question: Question) -> str:
    return build_prompt_text(tokenizer, NEUTRAL_STATE, question)
