"""Single-source secret scrubbing for anything we persist or commit.

Used by the run recorder (runs.record_llm_call) before writing captured prompts
/responses to disk, and by scripts/harvest_corpus.py before committing fixtures.
Keep it one function so the two paths can never diverge on what counts as a
secret. Conservative by design: a false redaction is harmless, a leaked key is
not.
"""
from __future__ import annotations

import os
import platform
import re
from typing import Iterable, Optional

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
        return {(scrub(k) if isinstance(k, str) else k): scrub(v) for k, v in obj.items()}
    return obj


def scrub_identifiers(
    obj,
    *,
    home: Optional[str] = None,
    hostname: Optional[str] = None,
    denylist: Optional[Iterable[str]] = None,
):
    """Recursively redact known local identifiers from any JSON-ish value.

    Companion to scrub() in this same module (single-source rule) — a
    different guarantee. scrub() catches secret-SHAPED strings; this catches
    *known* local identifiers: this machine's $HOME path, the username
    derived from it, the hostname, and any caller-supplied deny-list of
    emails/handles (PORTABLE_LEARNING_DESIGN.md §4 — the exporter assembles
    the deny-list from config + environment; never hardcode identifiers
    here). $HOME and hostname use stable replacement tokens ([HOME]/[HOST])
    so exported skill text stays executable-in-spirit on the receiving
    side; deny-list matches use [REDACTED] since there's no shared meaning
    to preserve across machines.

    This is mechanical redaction of KNOWN strings, not anonymization — it
    only catches what it's told about. It is one of two guarantees behind
    a pack export (the other is scrub()); the mandatory human review gate
    (REVIEW.md / maro-pack seal) is what actually backstops both.
    """
    home = home if home is not None else os.path.expanduser("~")
    hostname = hostname if hostname is not None else platform.node()
    username = os.path.basename(home.rstrip("/")) if home else ""

    # Longest-needle-first so e.g. the full $HOME path is redacted before a
    # bare username substring would otherwise chew into what's left of it.
    literal_replacements = sorted(
        [p for p in [(home, "[HOME]")] if p[0]],
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    bounded_replacements = [
        (needle, token)
        for needle, token in (
            [(username, "[USER]"), (hostname, "[HOST]")]
            + [(item, "[REDACTED]") for item in (denylist or [])]
        )
        if needle
    ]
    bounded_replacements.sort(key=lambda pair: len(pair[0]), reverse=True)

    patterns = [(re.compile(re.escape(needle)), token) for needle, token in literal_replacements]
    patterns += [
        (re.compile(r"\b" + re.escape(needle) + r"\b"), token)
        for needle, token in bounded_replacements
    ]

    def _scrub(o):
        if isinstance(o, str):
            s = o
            for rx, token in patterns:
                s = rx.sub(token, s)
            return s
        if isinstance(o, list):
            return [_scrub(x) for x in o]
        if isinstance(o, tuple):
            return tuple(_scrub(x) for x in o)
        if isinstance(o, dict):
            return {(_scrub(k) if isinstance(k, str) else k): _scrub(v) for k, v in o.items()}
        return o

    return _scrub(obj)
