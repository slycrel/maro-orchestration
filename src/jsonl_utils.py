"""Shared JSONL reading — one implementation instead of a dozen near-copies.

Tier 2 consolidation (docs/REFACTOR_PLAN.md): observe.py, introspect.py,
inspector.py, metrics.py, harness_optimizer.py, and tool_cost_report.py each
hand-rolled their own "read a JSONL file, skip bad lines, maybe take the last
N" loop, with inconsistent error handling — Tier 0 #3 (introspect.py
truncating the rest of the file on one malformed line) was a symptom of that
duplication. `read_jsonl_tail` is the one implementation everything else
should call.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def read_jsonl_tail(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read a JSONL file into a list of dicts, in original (chronological) order.

    A missing file, an unreadable file, blank lines, malformed JSON lines,
    and non-dict JSON values are all silently skipped — never raised. One
    corrupt line must never truncate or crash the read of everything after
    it, which is the failure mode this replaces.

    With `limit=None` (default), returns every valid record. With
    `limit=N`, returns only the last N valid records, still in chronological
    order (like `tail -n`) — callers that want newest-first should reverse
    the result themselves.
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    if limit is not None and limit > 0:
        results: List[Dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                results.append(item)
            if len(results) >= limit:
                break
        results.reverse()
        return results

    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            results.append(item)
    return results
