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
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

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
    # No trailing `if buffer: yield buffer` here, though it looks like there
    # should be: the loop's final iteration is the one that drives `position`
    # to 0, and that iteration takes the `else` branch and empties `buffer`.
    # So `buffer` is always b"" once the loop exits, and the first line of the
    # file is emitted as parts[-1] of that last chunk like any other. There
    # WAS such a block; it was unreachable (proved 2026-08-16 by assertion
    # probe over every chunk size 1..63 across ten file shapes) and removed
    # rather than left standing as a guard that cannot fire.


@dataclass(frozen=True)
class SkipReport:
    """What a read threw away, so a short list is distinguishable from a
    short store.

    Skipping a corrupt line is right — one bad append must not truncate the
    read of everything after it, which is the failure this module replaced.
    Skipping it *silently* is not: the caller gets a shorter list and no way
    to tell data loss from an empty ledger. Under the retention decree the
    path is part of the result, and the same goes for what fell out of it
    (tier-2 mutation-sweep scope, 2026-08-16 — 143 silent-drop sites across
    52 modules, this helper included).

    Blank lines are not counted: they are not records. ``partial`` marks a
    ``limit=N`` read that stopped early, so the counts describe the tail it
    scanned and are a LOWER BOUND on the file — reporting them as a whole-
    file total would be the same lie one level up.
    """
    undecodable: int = 0     # bytes that are not UTF-8 (crash-torn append)
    malformed: int = 0       # not valid JSON
    non_dict: int = 0        # valid JSON, but not a record
    unreadable: bool = False  # the file exists and could not be opened/read
    missing: bool = False     # no file — legitimately "no data yet", not loss
    partial: bool = False

    @property
    def dropped(self) -> int:
        return self.undecodable + self.malformed + self.non_dict

    def __bool__(self) -> bool:
        """Truthy when something was LOST. A missing file is not loss."""
        return self.dropped > 0 or self.unreadable

    def summary(self) -> str:
        if self.unreadable:
            return "unreadable (0 records returned — not an empty ledger)"
        bits = [f"{n} {label}" for n, label in
                ((self.undecodable, "undecodable"), (self.malformed, "malformed"),
                 (self.non_dict, "non-dict")) if n]
        scope = " in the scanned tail" if self.partial else ""
        return f"dropped {self.dropped} line(s){scope}: " + ", ".join(bits)


def read_jsonl_tail_counted(
    path: Path, limit: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], SkipReport]:
    """``read_jsonl_tail`` plus a SkipReport of what it threw away.

    Same records, same order, same never-raises contract. Use this wherever
    a short read could be mistaken for a small store — a census, a count in
    a readout, anything that reports "N lessons" to a human.
    """
    return _read(path, limit)


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

    Silently to the CALLER, not silently to the operator: drops are logged
    at WARNING with a count, and `read_jsonl_tail_counted` returns them as
    data. A short list must never be indistinguishable from a short store.
    """
    records, report = _read(path, limit)
    if report:
        log.warning("read_jsonl_tail(%s): %s", path, report.summary())
    return records


def _classify(raw_line: bytes, counts: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """One line → a record, or None with the reason counted.

    Single implementation on purpose: the tail path and the full-scan path
    were near-copies of this ladder, and the last time they diverged the
    full scan let UnicodeDecodeError escape against its own docstring.
    """
    raw_line = raw_line.strip()
    if not raw_line:
        return None                      # blank: not a record, not a loss
    try:
        text = raw_line.decode("utf-8")
    except UnicodeDecodeError:
        counts["undecodable"] += 1
        return None
    try:
        item = json.loads(text)
    except json.JSONDecodeError:
        counts["malformed"] += 1
        return None
    if not isinstance(item, dict):
        counts["non_dict"] += 1
        return None
    return item


def _read(path: Path, limit: Optional[int]
          ) -> Tuple[List[Dict[str, Any]], SkipReport]:
    if not path.exists():
        return [], SkipReport(missing=True)

    counts = {"undecodable": 0, "malformed": 0, "non_dict": 0}
    results: List[Dict[str, Any]] = []
    tail = limit is not None and limit > 0
    stopped_early = False
    try:
        with path.open("rb") as f:
            lines = _iter_lines_reverse(f, _TAIL_CHUNK_BYTES) if tail else f
            for raw_line in lines:
                item = _classify(raw_line, counts)
                if item is None:
                    continue
                results.append(item)
                if tail and len(results) >= limit:
                    stopped_early = True
                    break
    except OSError:
        return [], SkipReport(unreadable=True)
    if tail:
        results.reverse()
    return results, SkipReport(partial=stopped_early, **counts)
