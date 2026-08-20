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
from itertools import chain
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


def read_jsonl_announced(path: Path, what: str) -> List[Dict[str, Any]]:
    """Every JSON object in a JSONL store, with any loss announced.

    One torn byte costs one record instead of the whole file. The loaders
    this replaced (memory_ledger first, 2026-08-17; knowledge_web next)
    called `path.read_text()` on the entire file inside a broad `try`,
    which turned a single non-UTF-8 byte into one of two failures, both
    verified by probe:

      * an EMPTY corpus, silently — `read_text` raised before the loop and
        an outer `except Exception: pass` swallowed it;
      * an uncaught `UnicodeDecodeError` in the CALLER — loaders guarded
        with `except OSError`, and a decode error is a ValueError.

    `what` names the loader in the warning so an operator can tell WHICH
    corpus is short, not just that something somewhere dropped lines.
    """
    rows, report = read_jsonl_tail_counted(path)
    if report:
        log.warning("%s: %s (%s)", what, report.summary(), path)
    return rows


def read_rows_as(path: Path, what: str, build) -> list:
    """`read_jsonl_announced` plus a per-row constructor, counting schema drift.

    Two different losses, reported separately on purpose: a row that is not
    JSON is corruption, a row that is JSON but the current dataclass rejects
    is schema drift. Collapsing them hides which one is happening, and drift
    is the one that grows quietly as the schema moves.

    The catch is deliberately broad — one weird row must never abort the
    load, which is this module's whole doctrine — so it will also absorb a
    genuine bug in `build`. The warning names the exception class for that
    reason: `KeyError` every row is a code defect wearing a drift costume,
    and a label that always says "schema" would hide it.
    """
    out, drifted, first_err = [], 0, None
    for d in read_jsonl_announced(path, what):
        try:
            out.append(build(d))
        except Exception as exc:
            drifted += 1
            if first_err is None:
                first_err = f"{type(exc).__name__}: {exc}"
    if drifted:
        log.warning("%s: %d row(s) in %s are JSON but not loadable under the "
                    "current schema — excluded from the %d returned "
                    "(first: %s)",
                    what, drifted, path, len(out), first_err)
    return out


def store_text(path: Path) -> str:
    """Whole-store text with undecodable bytes carried as lone surrogates.

    In-place rewrite paths (memory_ledger's stampers, knowledge_web's node
    flips) rebuild a store by rejoining every line, which only preserves a
    torn line if the read survives it. A strict decode does not: before
    this (2026-08-17 adversarial round), one non-UTF-8 byte anywhere in
    outcomes.jsonl made all six memory_ledger stampers raise
    UnicodeDecodeError — `except OSError` does not catch a ValueError —
    so a single crash-torn append disabled verdict/lesson/supersede
    stamping on that store until someone repaired the file by hand.
    surrogateescape pairs with atomic_write's opt-in encoder (see
    file_lock) to round-trip those bytes verbatim; json.loads on the
    affected line fails like any malformed row, so a scan skips it and
    the rejoin keeps it. Raises FileNotFoundError like read_text —
    callers keep their existing guard.
    """
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def loads_clean(s: str):
    """json.loads that refuses byte-tainted lines.

    A line whose bytes were not valid UTF-8 reaches the per-line scanners
    as text carrying lone surrogates (U+DC80–U+DCFF, from store_text /
    locked_rmw's surrogateescape decode). Such a line can still be
    STRUCTURALLY valid JSON — `{"goal": "\\udcff"}` parses — and round 2
    of the 2026-08-17 adversarial review showed what happens next: a
    rewrite path that parses it re-serializes with json.dumps, which
    emits the surrogate as a clean ASCII escape. The file decodes
    strictly ever after, the undecodable count that announced the
    corruption goes silent, and garbage text persists as legitimate
    content — a lie, where the doctrine demands an announced loss.

    So the scanners must treat "byte-tainted" exactly like "unparseable":
    skip it, preserve the raw line verbatim, let the loaders keep
    announcing it. Raising JSONDecodeError (not a bare ValueError) means
    every existing `except json.JSONDecodeError` skip branch handles
    taint with no new code at the call sites.

    Two more shapes join the family, both from adversarial r5 of the
    2026-08-20 arc (Failure Operator, probed):

    * A surrogate can also arrive as a JSON *escape* — `{"tier": "\\udcff"}`
      is a pure-ASCII line whose bytes are perfectly valid UTF-8, and the
      raw-text scan above cannot see it, but it parses to exactly the same
      tainted string. The taint check therefore has to run on the PARSED
      value, not only on the raw line. A cheap ASCII pre-filter keeps the
      hot path (`\\ud` cannot appear unless an escape does) and the deep
      walk only runs when it might find something.
    * Duplicate names in one object. `json.loads` silently keeps the LAST
      one, so `{"applied": false, "applied": true}` reads as applied —
      probed: a STOP interrupt swallowed with no warning — and a rewrite
      that re-dumps the row destroys the other value. Two values, one of
      which we discard by an implementation detail, is not something this
      layer may decide: it is a corrupt row, and corrupt rows strand.
    """
    if any("\ud800" <= ch <= "\udfff" for ch in s):
        raise json.JSONDecodeError("byte-tainted line (raw non-UTF-8 "
                                   "bytes carried as surrogates)", s, 0)
    try:
        value = json.loads(s, object_pairs_hook=_no_duplicate_names,
                           parse_constant=_refuse_constant)
    except json.JSONDecodeError:
        raise                       # already the shape every caller catches
    except Exception as e:
        # EVERY other way the parser can refuse a line, translated — not a
        # list of the ones we have met. r7 caught `RecursionError` (json's
        # own depth limit, raised on a line `json.loads` cannot parse) and
        # adversarial r8 walked straight past it with the next one:
        # `int` conversion is capped at 4300 digits and raises ValueError,
        # so an interrupt row carrying a 5000-digit number killed
        # `InterruptQueue.poll()` before it could strand the row or announce
        # anything — the same failure r7 had just fixed, one exception class
        # over. Naming the classes we have seen is a denylist, and this is
        # the third round it has failed open. The rule instead: if the
        # parser did not return a value, the line does not parse, and a line
        # that does not parse strands. The class is named in the message so
        # the operator can still see WHY.
        raise json.JSONDecodeError(f"{type(e).__name__} while parsing: {e}",
                                   s, 0) from None

    if "\\u" in s and _carries_surrogate(value):
        raise json.JSONDecodeError("byte-tainted line (surrogate written as "
                                   "a \\uDCxx escape)", s, 0)
    return value


def _refuse_constant(token: str):
    """`NaN`, `Infinity`, `-Infinity` — accepted by `json.loads`, not JSON.

    Adversarial r8 raised this and it was REJECTED with the reason "they
    round-trip faithfully through json.dumps, so nothing is laundered".
    That was true and beside the point: r9 (Failure Operator, probed) showed
    the row does not need to be laundered to do damage — it needs only to be
    ADMITTED. A row carrying `{"extra": NaN}` is not JSON, and once admitted
    it takes part in a removal decision (`_dedup_identity` serialises the
    token straight back, the group forms, and the older row is deleted). The
    doctrine is that a row this layer cannot vouch for strands; "can be
    parsed by CPython's extensions" is not the same as "is a row".

    Measured before flipping: zero rows in the live workspace carry any of
    the three tokens, so nothing real strands.
    """
    raise json.JSONDecodeError(f"{token} is not JSON (a CPython extension "
                               f"this store does not accept)", "", 0)


def is_frame_blank(raw: str) -> bool:
    """Is this fragment framing, rather than a row that lost its content?

    `text.split("\n")` yields one empty fragment for the trailing newline
    every JSONL file ends with, and that fragment is the ONLY thing a
    rewrite may drop for free. Everything else is a row.

    Every reader in this codebase used to write `if not raw.strip():
    continue`, and adversarial r9 (2 lenses, probed) showed what that costs
    a DESTRUCTIVE reader. `str.strip()` removes Unicode whitespace, which
    JSON does not permit (JSON's whitespace is space, tab, CR, LF and
    nothing else). So `"\u2028" + valid_row` was stripped into something
    that parses, admitted, and re-serialised — the row's bytes gone, the
    file reported as clean, no announcement. And a line of `"\u00a0"` alone
    counted as blank and was dropped from the rewrite entirely. Both are the
    launder this arc exists to prevent, arriving through the whitespace door
    instead of the surrogate one.

    So: a fragment is framing only when it is empty. Anything else goes to
    the parser, and if the parser refuses it, it strands and is announced.
    """
    return raw == ""


def _no_duplicate_names(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise json.JSONDecodeError(f"duplicate object name {k!r} — two "
                                       f"values, and JSON does not say which",
                                       "", 0)
        seen.add(k)
    return dict(pairs)


def _carries_surrogate(value) -> bool:
    """Any lone surrogate anywhere in a parsed row — keys included.

    Iterative on purpose. Adversarial r6 (2026-08-20, 2 lenses, probed): the
    recursive version blew the interpreter's stack on JSON nested ~600 deep
    — which `json.loads` itself parses without complaint — and RecursionError
    is not a JSONDecodeError, so it flew straight through the `except
    (json.JSONDecodeError, TypeError)` that every caller in this codebase
    uses to strand a bad row. Probed: one valid, deeply nested line took
    `InterruptQueue.poll()` down before it could announce anything, which is
    the silent control-channel loss this whole arc is about. A shared helper
    with 84 call sites does not get to raise something its callers do not
    catch.
    """
    # A stack of ITERATORS, not of elements. Adversarial r7 (Architect,
    # probed under a 96 MiB cap): pushing every element of a container at
    # once made the walk's own memory proportional to the widest container,
    # so a valid five-million-item row raised MemoryError — again something
    # no caller catches. Auxiliary storage is proportional to nesting depth
    # now, which is what the recursive version got right before r6 removed
    # it for being proportional to nesting depth in STACK frames.
    stack = [iter((value,))]
    while stack:
        try:
            v = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(v, str):
            if any("\ud800" <= ch <= "\udfff" for ch in v):
                return True
        elif isinstance(v, dict):
            stack.append(iter(chain.from_iterable(v.items())))
        elif isinstance(v, (list, tuple)):
            stack.append(iter(v))
    return False


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
