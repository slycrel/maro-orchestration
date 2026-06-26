"""Single-source secret scrubbing for anything we persist or commit.

Used by the run recorder (runs.record_llm_call) before writing captured prompts
/responses to disk, and by scripts/harvest_corpus.py before committing fixtures.
Keep it one function so the two paths can never diverge on what counts as a
secret. Conservative by design: a false redaction is harmless, a leaked key is
not.
"""
from __future__ import annotations

import re

_SECRET_RES = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer|authorization|api[_-]?key|token|secret|password)\s*[:=]\s*\S{8,}"),
]


def scrub(obj):
    """Recursively redact secret-shaped substrings from any JSON-ish value."""
    if isinstance(obj, str):
        s = obj
        for rx in _SECRET_RES:
            s = rx.sub("[REDACTED]", s)
        return s
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(scrub(x) for x in obj)
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj
