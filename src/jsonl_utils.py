"""Shared JSONL reading — one implementation instead of a dozen near-copies.

Tier 2 consolidation (docs/REFACTOR_PLAN.md): observe.py, introspect.py,
inspector.py, metrics.py, harness_optimizer.py, and tool_cost_report.py each
hand-rolled their own "read a JSONL file, skip bad lines, maybe take the last
N" loop, with inconsistent error handling — Tier 0 #3 (introspect.py
truncating the rest of the file on one malformed line) was a symptom of that
duplication. `read_jsonl_tail` is the one implementation everything else
should call.

BACKLOG.md R6-D1 (adversarial review 2026-07-14): the `limit=N` path used to
read the entire file into memory before applying the limit — fine at 2.8 MB,
a real problem on a multi-GB events.jsonl. It now does a byte-bounded
backwards read (`_iter_lines_reverse`) that stops as soon as `limit` valid
records are found, reading at most O(limit) chunks of `_TAIL_CHUNK_BYTES`
plus one overshoot chunk — never the whole file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, List, Optional

# Chunk size for the backwards tail read. Exposed as a module constant so
# tests can shrink it to exercise multi-chunk boundary logic without huge
# fixture files.
_TAIL_CHUNK_BYTES = 64 * 1024


def _iter_lines_reverse(f: BinaryIO, chunk_size: int) -> Iterator[bytes]:
    """Yield raw byte lines from a binary file handle, from EOF backwards.

    Line terminators (``b"\\n"``) are stripped from each yielded line. Reads
    `chunk_size` bytes at a time, seeking backwards from the end, so total
    I/O is proportional to how many lines the caller actually consumes
    (callers typically `break` once they have enough), not file size.

    Splitting only ever happens on the single-byte ``b"\\n"`` — that byte
    cannot occur inside a multi-byte UTF-8 sequence (continuation bytes are
    always >= 0x80), so a chunk boundary landing mid-character never
    corrupts a line; it only ever splits between lines.
    """
    f.seek(0, 2)  # SEEK_END
    position = f.tell()
    buffer = b""
    while position > 0:
        read_size = min(chunk_size, position)
        position -= read_size
        f.seek(position)
        chunk = f.read(read_size)
        buffer = chunk + buffer
        parts = buffer.split(b"\n")
        if position > 0:
            # parts[0] may be an incomplete line whose start lives further
            # back in the file — hold it and prepend the next chunk to it.
            buffer = parts[0]
            parts = parts[1:]
        else:
            buffer = b""
        for part in reversed(parts):
            yield part
    if buffer:
        # Leftover from the very first line of the file (nothing precedes
        # it, so no chunk iteration ever emitted it as a completed part).
        yield buffer


def read_jsonl_tail(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read a JSONL file into a list of dicts, in original (chronological) order.

    A missing file, an unreadable file, blank lines, malformed JSON lines,
    and non-dict JSON values are all silently skipped — never raised. One
    corrupt line must never truncate or crash the read of everything after
    it, which is the failure mode this replaces.

    With `limit=None` (default), returns every valid record — the whole
    file is read (there's no way around that when every record is wanted),
    streamed line-by-line rather than materialized as one big string first.

    With `limit=N` (N > 0), returns only the last N valid records, still in
    chronological order (like `tail -n`) — callers that want newest-first
    should reverse the result themselves. This path never reads the whole
    file: it reads backwards in bounded chunks (see `_iter_lines_reverse`)
    and stops as soon as N valid records are collected.

    Both paths read binary and decode each candidate line individually, so
    a line with undecodable bytes (e.g. a crash-torn append) is silently
    skipped like any other malformed line — it never poisons the read of
    every other record in the ledger. (The old implementation let
    `UnicodeDecodeError` escape on the full-scan path, against its own
    docstring.)
    """
    if not path.exists():
        return []

    if limit is not None and limit > 0:
        results: List[Dict[str, Any]] = []
        try:
            with path.open("rb") as f:
                for raw_line in _iter_lines_reverse(f, _TAIL_CHUNK_BYTES):
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        text = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    try:
                        item = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        results.append(item)
                        if len(results) >= limit:
                            break
        except OSError:
            return []
        results.reverse()
        return results

    results = []
    try:
        with path.open("rb") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    results.append(item)
    except OSError:
        return []
    return results
