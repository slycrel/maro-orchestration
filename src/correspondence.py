"""Correspondence retrieval — sqlite-vec over project docs, memory, and conversation logs.

SCOPE: this is DEV-FACING TOOLING. It helps *us* (the people building the
orchestration system) recall our own design decisions, conversations, reviews,
and rationale across sessions. It is NOT part of Poe's runtime self-improvement
loop — that's `knowledge_web.py`, `memory.py`, and the lesson/skill graduation
pipeline (`knowledge.py` dashboard at Stages 1-5). Those serve Poe operating on
its own goals. This module serves the developers.

The distinction matters: it's easy to conflate "how we build the system" with
"what we're building." The underlying library here is reusable — Poe could
eventually use the same retrieval substrate for self-recall of our
correspondence, closing the dogfooding loop — but v1 is strictly a dev tool
invoked via the `dev-recall` CLI. Keeping the call site explicit (no `poe-`
prefix, no import from Poe runtime paths) prevents accidental coupling.

Problem this solves: the project's "correspondence" (design conversations,
decisions, rationale, review feedback) accumulates across sessions in multiple
places:
  - docs/*.md (30+ design docs, reviews, audits, conversation logs)
  - lat.md/*.md (concept wiki)
  - MILESTONES.md, BACKLOG.md, ROADMAP.md, CLAUDE.md
  - ~/.claude/projects/.../memory/*.md (cross-session auto-memory)

None of these are composable today — to recall "what did we say about taste?"
or "why did we rename constraint to scope?", you read files one at a time and
hope you pick the right ones. This module builds a thin vector-retrieval layer
over the corpus so questions can be asked and relevant chunks surface.

Design:
  - sqlite-vec for storage (single file, no server, matches repo convention)
  - OpenAI-compatible embeddings endpoint (cheap, ~$0.02/1M tokens with
    text-embedding-3-small)
  - Markdown heading-aware chunking with source/section/mtime metadata
  - Graceful ImportError fallback — sqlite-vec and requests are optional deps

Bitter-principle posture: don't reinvent retrieval. This module is ~400 lines
of plumbing over primitives that already work (SQLite, HTTP embeddings,
markdown parsing). Start with pure vector; if quality is poor, graduate to
BM25+RRF fusion using the existing `hybrid_search.py`.

CLI:
    dev-recall ingest              # full re-ingest
    dev-recall ingest --since 1d   # only recently-modified files
    dev-recall query "taste"       # top-K chunks
    dev-recall status              # counts, last-ingest time
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("poe.correspondence")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path.home() / ".poe" / "workspace" / "correspondence.db"
_DEFAULT_EMBED_MODEL = "text-embedding-3-small"
_DEFAULT_EMBED_DIM = 1536
_DEFAULT_MAX_CHUNK_CHARS = 6000
_DEFAULT_TOP_K = 8


def _default_sources() -> List[str]:
    """Corpus roots to index. Absolute paths; missing paths are silently skipped."""
    home = Path.home()
    repo_root = Path(__file__).resolve().parent.parent
    return [
        str(repo_root / "docs"),
        str(repo_root / "lat.md"),
        str(repo_root / "MILESTONES.md"),
        str(repo_root / "BACKLOG.md"),
        str(repo_root / "ROADMAP.md"),
        str(repo_root / "CLAUDE.md"),
        str(home / ".claude" / "projects" / "-home-clawd-claude" / "memory"),
    ]


def _load_config() -> Dict[str, Any]:
    """Pull user config; fall back to defaults when config module unavailable."""
    cfg: Dict[str, Any] = {
        "db_path": str(_DEFAULT_DB_PATH),
        "sources": _default_sources(),
        "embed_model": _DEFAULT_EMBED_MODEL,
        "embed_dim": _DEFAULT_EMBED_DIM,
        "max_chunk_chars": _DEFAULT_MAX_CHUNK_CHARS,
        "top_k": _DEFAULT_TOP_K,
    }
    try:
        from config import get as _cfg_get
        corr = _cfg_get("correspondence", None)
        if isinstance(corr, dict):
            cfg.update(corr)
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------------------
# Chunking — markdown heading-aware
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Chunk:
    source: str        # absolute file path
    section: str       # heading chain, e.g. "MILESTONES > Done > Phase 65"
    content: str
    modified_at: int   # file mtime, unix seconds
    content_hash: str  # sha256 of content, stable across re-ingest


def _heading_chain(text: str, start_pos: int) -> str:
    """Walk all headings whose line-start is ≤ start_pos; return 'H1 > H2 > H3'.

    Inclusive of a heading AT start_pos itself — so a chunk that begins with
    '### Leaf' contributes 'Leaf' as the chain's deepest level.
    """
    stack: List[Tuple[int, str]] = []  # (level, title)
    for m in _HEADING_RE.finditer(text):
        if m.start() > start_pos:
            break
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    return " > ".join(t for _, t in stack) if stack else ""


def chunk_markdown(text: str, source: str, modified_at: int, *,
                   max_chars: int = _DEFAULT_MAX_CHUNK_CHARS) -> List[Chunk]:
    """Split markdown on headings; cap each section at max_chars via paragraph splits.

    Returns one Chunk per resulting section. Empty or whitespace-only chunks dropped.
    """
    if not text or not text.strip():
        return []

    # Split positions: every heading start + end-of-text sentinel
    positions: List[int] = [0]
    for m in _HEADING_RE.finditer(text):
        if m.start() > 0:
            positions.append(m.start())
    positions.append(len(text))

    chunks: List[Chunk] = []
    for i in range(len(positions) - 1):
        start, end = positions[i], positions[i + 1]
        segment = text[start:end].strip()
        if not segment:
            continue
        section = _heading_chain(text, start)
        # If the segment begins with its own heading, that's already in section
        for piece in _split_for_size(segment, max_chars):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(Chunk(
                source=source,
                section=section,
                content=piece,
                modified_at=modified_at,
                content_hash=_hash(piece, source, section),
            ))
    return chunks


def _split_for_size(text: str, max_chars: int) -> List[str]:
    """Split oversize text on blank lines; never exceeds max_chars per piece."""
    if len(text) <= max_chars:
        return [text]
    pieces: List[str] = []
    paragraphs = re.split(r"\n{2,}", text)
    buf = ""
    for p in paragraphs:
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= max_chars:
            buf = buf + "\n\n" + p
        else:
            pieces.append(buf)
            buf = p
    if buf:
        pieces.append(buf)
    # Hard-split anything still too big (single paragraph over cap)
    out: List[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            out.append(piece)
        else:
            for j in range(0, len(piece), max_chars):
                out.append(piece[j:j + max_chars])
    return out


def _hash(content: str, source: str, section: str) -> str:
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\x00")
    h.update(section.encode("utf-8"))
    h.update(b"\x00")
    h.update(content.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Embeddings — HTTP to OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

def _embed_openai(texts: List[str], *, model: str,
                  api_key: Optional[str] = None,
                  base_url: Optional[str] = None) -> List[List[float]]:
    """Embed texts via an OpenAI-compatible /embeddings endpoint.

    Key resolution: OPENAI_API_KEY first, then OPENROUTER_API_KEY. If only
    OPENROUTER is set and base_url isn't explicit, auto-switches to OpenRouter's
    endpoint — this keeps the "set one key, it works" path clean.

    Raises RuntimeError on any HTTP failure. Caller is expected to handle that.
    """
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "correspondence: `requests` is required for embeddings. "
            "Install with: pip install requests"
        ) from exc

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if api_key and base_url is None:
                base_url = "https://openrouter.ai/api/v1"
    if not api_key:
        raise RuntimeError(
            "correspondence: no API key. Set OPENAI_API_KEY (or OPENROUTER_API_KEY)."
        )
    if base_url is None:
        base_url = "https://api.openai.com/v1"

    resp = requests.post(
        f"{base_url.rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "input": texts},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"correspondence: embeddings HTTP {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    return [item["embedding"] for item in data.get("data", [])]


EmbedFn = Callable[[List[str]], List[List[float]]]


def _build_embed_fn(cfg: Dict[str, Any]) -> EmbedFn:
    model = cfg.get("embed_model", _DEFAULT_EMBED_MODEL)
    # Pass None so _embed_openai's auto-switch (OPENROUTER → openrouter endpoint)
    # can fire when OPENAI_API_KEY is absent. Only override if config sets it.
    base_url = cfg.get("embed_base_url")

    def _fn(texts: List[str]) -> List[List[float]]:
        # Chunk into batches of 100 to respect typical request limits
        out: List[List[float]] = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            out.extend(_embed_openai(batch, model=model, base_url=base_url))
        return out

    return _fn


# ---------------------------------------------------------------------------
# Storage — sqlite + sqlite-vec
# ---------------------------------------------------------------------------

def _open_db(db_path: str, *, embed_dim: int) -> sqlite3.Connection:
    """Open sqlite with sqlite-vec loaded; create schema if absent.

    Raises RuntimeError with install instructions if sqlite-vec unavailable.
    """
    try:
        import sqlite_vec
    except ImportError as exc:
        raise RuntimeError(
            "correspondence: `sqlite-vec` is required. "
            "Install with: pip install sqlite-vec"
        ) from exc

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            section TEXT NOT NULL DEFAULT '',
            modified_at INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
        CREATE INDEX IF NOT EXISTS idx_chunks_modified ON chunks(modified_at);

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            embedding float[{embed_dim}]
        );

        CREATE TABLE IF NOT EXISTS ingest_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def _pack_vec(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@dataclass
class IngestStats:
    files_scanned: int = 0
    files_skipped_stale: int = 0
    chunks_new: int = 0
    chunks_existing: int = 0
    errors: List[str] = field(default_factory=list)


def _iter_markdown_files(sources: Iterable[str]) -> Iterable[Path]:
    for src in sources:
        p = Path(src)
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() == ".md":
                yield p
        else:
            for child in p.rglob("*.md"):
                yield child


def ingest(*, cfg: Optional[Dict[str, Any]] = None,
           embed_fn: Optional[EmbedFn] = None,
           since_seconds: Optional[int] = None) -> IngestStats:
    """Scan configured sources, chunk, embed, and upsert into the db.

    Content-hash dedup — re-ingesting the same content is cheap.
    `since_seconds`: only process files with mtime newer than (now - since_seconds).
    """
    cfg = cfg or _load_config()
    if embed_fn is None:
        embed_fn = _build_embed_fn(cfg)

    conn = _open_db(cfg["db_path"], embed_dim=cfg["embed_dim"])
    stats = IngestStats()

    cutoff: Optional[int] = None
    if since_seconds is not None:
        cutoff = int(time.time()) - int(since_seconds)

    pending: List[Chunk] = []
    for path in _iter_markdown_files(cfg["sources"]):
        stats.files_scanned += 1
        try:
            mtime = int(path.stat().st_mtime)
            if cutoff is not None and mtime < cutoff:
                stats.files_skipped_stale += 1
                continue
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            stats.errors.append(f"{path}: read failed: {exc}")
            continue

        for ch in chunk_markdown(text, str(path), mtime,
                                 max_chars=cfg["max_chunk_chars"]):
            row = conn.execute(
                "SELECT 1 FROM chunks WHERE content_hash = ?",
                (ch.content_hash,),
            ).fetchone()
            if row:
                stats.chunks_existing += 1
            else:
                pending.append(ch)

    if pending:
        try:
            vectors = embed_fn([c.content for c in pending])
        except Exception as exc:
            stats.errors.append(f"embed failed: {exc}")
            conn.close()
            return stats
        if len(vectors) != len(pending):
            stats.errors.append(
                f"embedding count mismatch: {len(vectors)} vs {len(pending)}"
            )
            conn.close()
            return stats

        expected_dim = cfg["embed_dim"]
        for ch, vec in zip(pending, vectors):
            if len(vec) != expected_dim:
                stats.errors.append(
                    f"dim mismatch {len(vec)}≠{expected_dim} for {ch.source}"
                )
                continue
            cur = conn.execute(
                "INSERT INTO chunks(source, section, modified_at, content, content_hash) "
                "VALUES (?,?,?,?,?)",
                (ch.source, ch.section, ch.modified_at, ch.content, ch.content_hash),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                (chunk_id, _pack_vec(vec)),
            )
            stats.chunks_new += 1

    conn.execute(
        "INSERT OR REPLACE INTO ingest_meta(key, value) VALUES (?, ?)",
        ("last_ingest_utc", str(int(time.time()))),
    )
    conn.commit()
    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

@dataclass
class QueryHit:
    source: str
    section: str
    content: str
    modified_at: int
    distance: float


def query(text: str, *, top_k: Optional[int] = None,
          cfg: Optional[Dict[str, Any]] = None,
          embed_fn: Optional[EmbedFn] = None) -> List[QueryHit]:
    cfg = cfg or _load_config()
    if embed_fn is None:
        embed_fn = _build_embed_fn(cfg)
    if top_k is None:
        top_k = cfg["top_k"]

    vectors = embed_fn([text])
    if not vectors:
        return []
    qvec = _pack_vec(vectors[0])

    conn = _open_db(cfg["db_path"], embed_dim=cfg["embed_dim"])
    try:
        rows = conn.execute(
            "SELECT chunks.source, chunks.section, chunks.content, chunks.modified_at, "
            "vec_chunks.distance "
            "FROM vec_chunks "
            "JOIN chunks ON chunks.id = vec_chunks.rowid "
            "WHERE vec_chunks.embedding MATCH ? AND k = ? "
            "ORDER BY vec_chunks.distance",
            (qvec, top_k),
        ).fetchall()
    finally:
        conn.close()

    return [
        QueryHit(source=r[0], section=r[1], content=r[2],
                 modified_at=r[3], distance=r[4])
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status(*, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or _load_config()
    try:
        conn = _open_db(cfg["db_path"], embed_dim=cfg["embed_dim"])
    except RuntimeError as exc:
        return {"error": str(exc)}
    try:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) FROM chunks GROUP BY source ORDER BY 2 DESC"
        ).fetchall()
        last = conn.execute(
            "SELECT value FROM ingest_meta WHERE key = 'last_ingest_utc'"
        ).fetchone()
    finally:
        conn.close()
    return {
        "db_path": cfg["db_path"],
        "total_chunks": total,
        "last_ingest_utc": int(last[0]) if last else None,
        "top_sources": by_source[:10],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_ingest(args: argparse.Namespace) -> int:
    since = None
    if args.since:
        since = _parse_duration(args.since)
    stats = ingest(since_seconds=since)
    print(f"scanned={stats.files_scanned} "
          f"stale_skipped={stats.files_skipped_stale} "
          f"new={stats.chunks_new} existing={stats.chunks_existing}")
    for err in stats.errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return 0 if not stats.errors else 1


def _cmd_query(args: argparse.Namespace) -> int:
    hits = query(args.text, top_k=args.top_k)
    if not hits:
        print("(no hits)")
        return 0
    for h in hits:
        header = f"[d={h.distance:.3f}] {Path(h.source).name}"
        if h.section:
            header += f" — {h.section}"
        print(header)
        snippet = h.content.strip().replace("\n", " ")[:200]
        print(f"  {snippet}{'...' if len(h.content) > 200 else ''}")
        print(f"  ({h.source})")
        print()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    s = status()
    if "error" in s:
        print(f"ERROR: {s['error']}", file=sys.stderr)
        return 1
    print(f"db: {s['db_path']}")
    print(f"chunks: {s['total_chunks']}")
    if s["last_ingest_utc"]:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(s["last_ingest_utc"], tz=timezone.utc)
        print(f"last ingest: {dt.isoformat()}")
    print("top sources:")
    for src, n in s["top_sources"]:
        print(f"  {n:>4}  {src}")
    return 0


def _parse_duration(s: str) -> int:
    m = re.match(r"^(\d+)([smhd])$", s.strip())
    if not m:
        raise ValueError(f"unparseable duration: {s}")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        prog="dev-recall",
        description="Dev-facing correspondence retrieval (design docs, conversations, memory). "
                    "NOT part of Poe runtime — see `poe-knowledge` for Poe's own self-improvement layer.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="scan configured sources + embed new chunks")
    ing.add_argument("--since", help="only files modified within last duration (e.g. 1d, 3h)")
    ing.set_defaults(fn=_cmd_ingest)

    q = sub.add_parser("query", help="search corpus for text")
    q.add_argument("text", help="query text")
    q.add_argument("--top-k", type=int, default=None, help="number of hits to return")
    q.set_defaults(fn=_cmd_query)

    s = sub.add_parser("status", help="show db stats + last ingest time")
    s.set_defaults(fn=_cmd_status)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
