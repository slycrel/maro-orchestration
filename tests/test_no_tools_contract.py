"""Repo-wide no_tools contract lint (BACKLOG #27, 2026-07-18).

Every LLM `adapter.complete(` call site in src/ must declare itself:

  - pure-text/JSON-contract calls pass `no_tools=True` (plus a `purpose`
    label) — on this box every call rides the agentic `claude -p` CLI, and
    an unmarked contract call can EXECUTE the text it was asked to judge
    (calm-echo 2026-07-17: a boundary-expansion decompose ran the remainder
    goal for ~4 minutes and shipped a wrong deliverable), or
  - intentionally agentic calls (worker executor seams, factory step
    execution) carry an `# agentic:` marker naming why tools are intended.

This lint makes the classification a standing property instead of a one-time
sweep: a NEW call site that does neither fails here and forces the author to
decide which kind of call it is.

Mechanics: balanced-paren extraction of each `.complete(` call; only calls
that pass messages (LLMMessage/messages) count as adapter calls — prose
mentions in docstrings and non-adapter `.complete()` methods are ignored.
Contract compliance requires a literal `no_tools=True` / `"no_tools": True`
AND a `purpose` kwarg — a comment mentioning no_tools, or `no_tools=False`
left behind after debugging, does NOT satisfy it (adversarial-review
2026-07-18: the first draft accepted any substring). Either may appear
inside the call or within the 20 preceding lines (kwargs-dict construction,
e.g. planner's _staged_kwargs); the `# agentic:` marker may appear inside
the call or within the 3 preceding lines.

Exempt files (adapter internals, not call sites of the contract):
  - src/llm.py — defines the adapters; its internal delegation forwards the
    caller's kwargs, and its own probe call is marked.
  - src/hosted_free.py — ladder delegation, forwards caller kwargs verbatim.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

EXEMPT_FILES = {"llm.py", "hosted_free.py"}

# How far back (in lines) each marker may sit from the call's first line.
_NO_TOOLS_LOOKBACK = 20
_AGENTIC_LOOKBACK = 3


def _call_sites(text: str):
    """Yield (lineno, call_text, preceding_lines) for each .complete( call."""
    for m in re.finditer(r"\.complete\(", text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line = text[line_start:text.index("\n", m.start())]
        if re.search(r"def complete\(", line):
            continue
        i, depth = m.end(), 1
        while depth and i < len(text):
            c = text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        call_text = text[m.start():i]
        lineno = text.count("\n", 0, m.start()) + 1
        all_lines = text.split("\n")
        preceding = all_lines[max(0, lineno - 1 - _NO_TOOLS_LOOKBACK):lineno - 1]
        yield lineno, call_text, preceding


def _is_adapter_call(call_text: str) -> bool:
    return "LLMMessage" in call_text or "messages" in call_text


# Literal kwarg forms only: no_tools=True (call) or "no_tools": True (kwargs
# dict). A comment or a no_tools=False must not count.
_NO_TOOLS_RE = re.compile(r"no_tools['\"]?\s*[:=]\s*True")
_PURPOSE_RE = re.compile(r"purpose['\"]?\s*[:=]")


def _compliant(call_text: str, preceding: list) -> bool:
    if "# agentic" in call_text:
        return True
    if any("# agentic" in ln for ln in preceding[-_AGENTIC_LOOKBACK:]):
        return True
    nearby = call_text + "\n" + "\n".join(preceding)
    return bool(_NO_TOOLS_RE.search(nearby)) and bool(_PURPOSE_RE.search(nearby))


def test_every_complete_call_declares_no_tools_or_agentic():
    offenders = []
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".py") or fn in EXEMPT_FILES:
            continue
        text = (SRC / fn).read_text()
        for lineno, call_text, preceding in _call_sites(text):
            if not _is_adapter_call(call_text):
                continue
            if not _compliant(call_text, preceding):
                offenders.append(f"src/{fn}:{lineno}")
    assert not offenders, (
        "adapter.complete() call site(s) neither pass no_tools=True AND "
        "purpose=... nor carry an '# agentic:' marker — decide which kind of "
        "call each is (contract calls: add no_tools=True + purpose=...; "
        "agentic calls: add '# agentic: <reason>' above the call). See "
        "tests/test_no_tools_contract.py docstring. Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_lint_actually_sees_call_sites():
    """Guard the lint against silently matching nothing (a regex drift that
    stops finding call sites would make the contract test vacuously green)."""
    total = 0
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".py") or fn in EXEMPT_FILES:
            continue
        text = (SRC / fn).read_text()
        total += sum(1 for _, call_text, _ in _call_sites(text)
                     if _is_adapter_call(call_text))
    assert total >= 40, f"expected the repo's ~70 adapter call sites, saw {total}"
