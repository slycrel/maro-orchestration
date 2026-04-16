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
    dev-recall ingest                        # scan + embed markdown/text sources
    dev-recall ingest --since 1d             # only recently-modified files
    dev-recall ingest-sessions               # Claude Code JSONL transcripts
    dev-recall ingest-telegram --path PATH   # Telegram Desktop result.json export
    dev-recall transcript SESSION.jsonl      # boil a JSONL down to readable chat
    dev-recall query "taste"                 # top-K chunks
    dev-recall status                        # counts, last-ingest time
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
    """Corpus roots to index. Absolute paths; missing paths are silently skipped.

    Captures the corpus of "correspondence" broadly: this repo's design docs and
    conversation logs, the concept wiki (lat.md), cross-session auto-memory, and
    the older OpenClaw workspace's top-level docs — which is where earlier grok
    reviews and cowork conversations accumulated. The word "correspondence" is
    deliberately load-bearing in both senses: the literal letters/reviews we
    write to each other AND the Mage: The Ascension sphere — seemingly
    unconnected knowledge sitting adjacent until something surfaces the link.
    Vector retrieval is exactly the substrate that makes the second meaning work.
    """
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
        # Early grok reviews — plain text, at the repo's parent claude/ dir.
        str(home / "claude"),
        # OpenClaw workspace top-level markdown (identity, principles, MEMORY,
        # playbook, etc.). NOTE: only top-level .md via glob — the memory/
        # subtree holds thousands of machine-generated outcome files we don't
        # want to pull in wholesale.
        str(home / ".openclaw" / "workspace"),
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


_TEXT_EXTS = {".md", ".txt"}

# Directory patterns to exclude from recursive text scans. Prevents ingesting
# auto-generated outcome/memory files, git internals, and venvs.
_EXCLUDE_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "memory",        # workspace auto-memory — too noisy, too many files
    "output",        # run artifacts
    "projects",      # per-project scratch
    "skills",        # evolved skill files
    "personas",      # persona YAMLs
    "old-reference",
}


def _iter_markdown_files(sources: Iterable[str]) -> Iterable[Path]:
    """Yield .md and .txt files from the configured sources.

    Files listed directly are always yielded if extension matches. Directories
    are walked recursively but excluded subtrees (see _EXCLUDE_DIR_NAMES) are
    skipped — they hold generated artifacts, not correspondence.
    """
    for src in sources:
        p = Path(src)
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in _TEXT_EXTS:
                yield p
            continue
        # Directory walk with pruning
        stack: List[Path] = [p]
        while stack:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name in _EXCLUDE_DIR_NAMES:
                        continue
                    stack.append(entry)
                elif entry.is_file() and entry.suffix.lower() in _TEXT_EXTS:
                    yield entry


# ---------------------------------------------------------------------------
# JSONL session transcripts — Claude Code session logs
# ---------------------------------------------------------------------------

_DEFAULT_SESSION_DIRS = [
    str(Path.home() / ".claude" / "projects" / "-home-clawd-claude"),
]

# User messages whose entire content is CLI scaffolding wrappers — skip.
_SCAFFOLD_PATTERNS = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<command-name>",
    "<command-stdout>",
    "<command-stderr>",
    "<command-message>",
    "<command-args>",
    "Caveat: The messages below were generated by the user while running local commands",
)


def _extract_user_text(content: Any) -> str:
    """From a user message's .content, return the text a human actually typed.

    Filters CLI scaffolding wrappers and tool_result blocks (both are noise for
    correspondence retrieval). Returns empty string when nothing useful remains.
    """
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            return ""
        if any(stripped.startswith(p) for p in _SCAFFOLD_PATTERNS):
            return ""
        return stripped
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if t and not any(t.startswith(p) for p in _SCAFFOLD_PATTERNS):
                    parts.append(t)
            # skip tool_result blocks — they're verbatim tool output, not dialog
        return "\n\n".join(parts).strip()
    return ""


def _extract_assistant_text(content: Any) -> str:
    """From an assistant message's .content, return just the text blocks.

    Skips `thinking` and `tool_use` blocks — neither is dialog we want to recall.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if t:
                    parts.append(t)
        return "\n\n".join(parts).strip()
    return ""


@dataclass
class _Turn:
    user_text: str = ""
    asst_parts: List[str] = field(default_factory=list)
    started_at: str = ""
    turn_index: int = 0


def _iter_turn_chunks(jsonl_path: Path, *, max_chars: int) -> Iterable[Chunk]:
    """Walk a session JSONL and yield one Chunk per user→assistant turn pair.

    Tool results, thinking blocks, tool_use, progress events, and CLI scaffolding
    are filtered out. A turn = user text + all assistant text blocks preceding
    the next user text. Empty turns (no user text AND no assistant text) skipped.

    Chunk metadata:
      source  = absolute path to the JSONL file
      section = "turn N — YYYY-MM-DD HH:MM" (human-readable for retrieval UX)
    """
    try:
        mtime = int(jsonl_path.stat().st_mtime)
    except OSError:
        return
    current = _Turn()
    turn_idx = 0

    def _emit(t: _Turn) -> Iterable[Chunk]:
        text = t.user_text
        if t.asst_parts:
            combined_asst = "\n\n".join(t.asst_parts)
            if text:
                text = f"USER: {text}\n\nASSISTANT: {combined_asst}"
            else:
                text = f"ASSISTANT: {combined_asst}"
        elif text:
            text = f"USER: {text}"
        else:
            return
        ts_fragment = t.started_at[:16].replace("T", " ") if t.started_at else "?"
        section = f"turn {t.turn_index} — {ts_fragment}"
        source = str(jsonl_path)
        for piece in _split_for_size(text, max_chars):
            piece = piece.strip()
            if not piece:
                continue
            yield Chunk(
                source=source, section=section, content=piece,
                modified_at=mtime,
                content_hash=_hash(piece, source, section),
            )

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                t = rec.get("type")
                if t == "user":
                    msg = rec.get("message") or {}
                    text = _extract_user_text(msg.get("content"))
                    if text:
                        # Flush prior turn (if any) before starting new one
                        if current.user_text or current.asst_parts:
                            yield from _emit(current)
                        turn_idx += 1
                        current = _Turn(
                            user_text=text,
                            started_at=rec.get("timestamp", ""),
                            turn_index=turn_idx,
                        )
                elif t == "assistant":
                    msg = rec.get("message") or {}
                    text = _extract_assistant_text(msg.get("content"))
                    if text:
                        current.asst_parts.append(text)
                        if not current.started_at:
                            current.started_at = rec.get("timestamp", "")
                # all other types: skip
    except OSError:
        return

    if current.user_text or current.asst_parts:
        yield from _emit(current)


# ---------------------------------------------------------------------------
# Telegram export — result.json from Telegram Desktop "Export chat history"
# ---------------------------------------------------------------------------

def _extract_telegram_text(text_field: Any) -> str:
    """Flatten a Telegram `text` field (string OR list of entities) to prose.

    Telegram exports inline entities (mentions, bot_commands, links, bold/italic)
    as a list where each element is either a plain string or a dict with
    {type, text}. We just want the concatenated human-readable text.
    """
    if isinstance(text_field, str):
        return text_field.strip()
    if isinstance(text_field, list):
        parts: List[str] = []
        for entity in text_field:
            if isinstance(entity, str):
                parts.append(entity)
            elif isinstance(entity, dict):
                t = entity.get("text") or ""
                if t:
                    parts.append(t)
        return "".join(parts).strip()
    return ""


def _iter_telegram_turns(json_path: Path, *, max_chars: int,
                         bot_sender: Optional[str] = None) -> Iterable[Chunk]:
    """Yield Chunks from a Telegram Desktop result.json chat export.

    Turn model mirrors the JSONL session adapter: a "turn" = human text(s)
    followed by the bot's reply(ies). Consecutive same-sender messages are
    concatenated inside a turn. A new human message flushes the current turn.

    If bot_sender is None, inferred as the sender with the most messages
    (typically the bot, since bots reply more verbosely than humans do).

    Each Chunk:
      source  = absolute path to result.json
      section = "turn N — YYYY-MM-DD HH:MM"
      content = "USER: ...\\n\\nASSISTANT: ..."
    """
    try:
        mtime = int(json_path.stat().st_mtime)
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    messages = data.get("messages") or []
    if not messages:
        return

    if bot_sender is None:
        counts: Dict[str, int] = {}
        for m in messages:
            if m.get("type") == "message":
                s = m.get("from")
                if s:
                    counts[s] = counts.get(s, 0) + 1
        if counts:
            bot_sender = max(counts.items(), key=lambda kv: kv[1])[0]

    current = _Turn()
    last_role: Optional[str] = None
    turn_idx = 0

    def _emit(t: _Turn) -> Iterable[Chunk]:
        text = t.user_text
        if t.asst_parts:
            combined = "\n\n".join(t.asst_parts)
            if text:
                text = f"USER: {text}\n\nASSISTANT: {combined}"
            else:
                text = f"ASSISTANT: {combined}"
        elif text:
            text = f"USER: {text}"
        else:
            return
        ts_fragment = t.started_at[:16].replace("T", " ") if t.started_at else "?"
        section = f"turn {t.turn_index} — {ts_fragment}"
        source = str(json_path)
        for piece in _split_for_size(text, max_chars):
            piece = piece.strip()
            if not piece:
                continue
            yield Chunk(
                source=source, section=section, content=piece,
                modified_at=mtime,
                content_hash=_hash(piece, source, section),
            )

    for m in messages:
        if m.get("type") != "message":
            continue
        text = _extract_telegram_text(m.get("text"))
        if not text:
            continue
        sender = m.get("from")
        is_bot = (sender == bot_sender)
        role = "bot" if is_bot else "user"
        ts = m.get("date", "")

        if role == "user":
            if last_role == "bot":
                # Bot reply finished; flush the turn before starting a new one.
                yield from _emit(current)
                current = _Turn()
            if current.user_text:
                current.user_text = current.user_text + "\n\n" + text
            else:
                current.user_text = text
                current.started_at = ts
                turn_idx += 1
                current.turn_index = turn_idx
        else:
            if not current.user_text and not current.asst_parts:
                # Bot spoke first (no preceding user message)
                turn_idx += 1
                current.turn_index = turn_idx
                current.started_at = ts
            current.asst_parts.append(text)
        last_role = role

    if current.user_text or current.asst_parts:
        yield from _emit(current)


def ingest_telegram(*, cfg: Optional[Dict[str, Any]] = None,
                    embed_fn: Optional[EmbedFn] = None,
                    paths: Optional[List[str]] = None) -> IngestStats:
    """Ingest Telegram Desktop chat exports (result.json) as turn-based chunks.

    `paths`: list of result.json files OR directories containing them.
    """
    cfg = cfg or _load_config()
    if embed_fn is None:
        embed_fn = _build_embed_fn(cfg)

    conn = _open_db(cfg["db_path"], embed_dim=cfg["embed_dim"])
    stats = IngestStats()

    resolved: List[Path] = []
    for p in (paths or []):
        pth = Path(p).expanduser()
        if pth.is_file() and pth.suffix.lower() == ".json":
            resolved.append(pth)
        elif pth.is_dir():
            # Telegram exports put result.json at the directory root
            rj = pth / "result.json"
            if rj.is_file():
                resolved.append(rj)

    pending: List[Chunk] = []
    for path in resolved:
        stats.files_scanned += 1
        try:
            for ch in _iter_telegram_turns(path, max_chars=cfg["max_chunk_chars"]):
                row = conn.execute(
                    "SELECT 1 FROM chunks WHERE content_hash = ?",
                    (ch.content_hash,),
                ).fetchone()
                if row:
                    stats.chunks_existing += 1
                else:
                    pending.append(ch)
        except Exception as exc:
            stats.errors.append(f"{path}: parse failed: {exc}")
            continue

    if pending:
        batch_size = 100
        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            try:
                vectors = embed_fn([c.content for c in batch])
            except Exception as exc:
                stats.errors.append(f"embed batch {i}: {exc}")
                continue
            if len(vectors) != len(batch):
                stats.errors.append(
                    f"embedding count mismatch: {len(vectors)} vs {len(batch)}"
                )
                continue
            expected_dim = cfg["embed_dim"]
            for ch, vec in zip(batch, vectors):
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
            conn.commit()

    conn.execute(
        "INSERT OR REPLACE INTO ingest_meta(key, value) VALUES (?, ?)",
        ("last_ingest_telegram_utc", str(int(time.time()))),
    )
    conn.commit()
    conn.close()
    return stats


def render_transcript(jsonl_path: Path) -> str:
    """Boil a session JSONL down to a readable chat transcript.

    Drops thinking, tool_use, tool_result, CLI scaffolding, progress events.
    Output is plain text, one blank line between turns, labeled USER/ASSISTANT.
    Useful on its own (pipe to a reader) and as a debugging aid for the ingest path.
    """
    turns: List[str] = []
    current = _Turn()
    turn_idx = 0

    def _flush(t: _Turn) -> Optional[str]:
        if not t.user_text and not t.asst_parts:
            return None
        ts = t.started_at[:16].replace("T", " ") if t.started_at else "?"
        header = f"--- turn {t.turn_index} — {ts} ---"
        pieces = [header]
        if t.user_text:
            pieces.append(f"USER:\n{t.user_text}")
        if t.asst_parts:
            pieces.append("ASSISTANT:\n" + "\n\n".join(t.asst_parts))
        return "\n\n".join(pieces)

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                t = rec.get("type")
                if t == "user":
                    msg = rec.get("message") or {}
                    text = _extract_user_text(msg.get("content"))
                    if text:
                        out = _flush(current)
                        if out:
                            turns.append(out)
                        turn_idx += 1
                        current = _Turn(
                            user_text=text,
                            started_at=rec.get("timestamp", ""),
                            turn_index=turn_idx,
                        )
                elif t == "assistant":
                    msg = rec.get("message") or {}
                    text = _extract_assistant_text(msg.get("content"))
                    if text:
                        current.asst_parts.append(text)
                        if not current.started_at:
                            current.started_at = rec.get("timestamp", "")
    except OSError:
        return ""

    out = _flush(current)
    if out:
        turns.append(out)
    header = f"# Session transcript: {jsonl_path.name}\n"
    return header + "\n\n".join(turns) + "\n"


def _iter_session_files(paths: Optional[List[str]], dirs: List[str]) -> Iterable[Path]:
    """Yield JSONL paths either from an explicit list or by walking session dirs."""
    if paths:
        for p in paths:
            path = Path(p).expanduser()
            if path.is_file() and path.suffix.lower() == ".jsonl":
                yield path
        return
    for d in dirs:
        root = Path(d).expanduser()
        if not root.exists():
            continue
        for child in root.glob("*.jsonl"):
            yield child


def ingest_sessions(*, cfg: Optional[Dict[str, Any]] = None,
                    embed_fn: Optional[EmbedFn] = None,
                    paths: Optional[List[str]] = None,
                    since_seconds: Optional[int] = None,
                    limit: Optional[int] = None) -> IngestStats:
    """Ingest Claude Code session JSONL files as turn-based chunks.

    `paths`: explicit list of JSONL files. If omitted, walks
             `cfg["session_dirs"]` (default: ~/.claude/projects/-home-clawd-claude).
    `since_seconds`: skip files whose mtime is older than now - since_seconds.
    `limit`: cap number of files processed (useful for probing corpus quality).

    Content-hash dedup identical to `ingest()` — safe to re-run.
    """
    cfg = cfg or _load_config()
    if embed_fn is None:
        embed_fn = _build_embed_fn(cfg)

    session_dirs = cfg.get("session_dirs") or _DEFAULT_SESSION_DIRS
    conn = _open_db(cfg["db_path"], embed_dim=cfg["embed_dim"])
    stats = IngestStats()

    cutoff: Optional[int] = None
    if since_seconds is not None:
        cutoff = int(time.time()) - int(since_seconds)

    pending: List[Chunk] = []
    count = 0
    for path in _iter_session_files(paths, session_dirs):
        if limit is not None and count >= limit:
            break
        count += 1
        stats.files_scanned += 1
        try:
            mtime = int(path.stat().st_mtime)
            if cutoff is not None and mtime < cutoff:
                stats.files_skipped_stale += 1
                continue
        except OSError as exc:
            stats.errors.append(f"{path}: stat failed: {exc}")
            continue

        try:
            for ch in _iter_turn_chunks(path, max_chars=cfg["max_chunk_chars"]):
                row = conn.execute(
                    "SELECT 1 FROM chunks WHERE content_hash = ?",
                    (ch.content_hash,),
                ).fetchone()
                if row:
                    stats.chunks_existing += 1
                else:
                    pending.append(ch)
        except Exception as exc:
            stats.errors.append(f"{path}: parse failed: {exc}")
            continue

    if pending:
        # Batch embed in chunks of 100 — embed_fn already handles batching, but
        # we slice here so a single failure doesn't lose everything.
        batch_size = 100
        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            try:
                vectors = embed_fn([c.content for c in batch])
            except Exception as exc:
                stats.errors.append(f"embed batch {i}: {exc}")
                continue
            if len(vectors) != len(batch):
                stats.errors.append(
                    f"embedding count mismatch: {len(vectors)} vs {len(batch)}"
                )
                continue
            expected_dim = cfg["embed_dim"]
            for ch, vec in zip(batch, vectors):
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
            conn.commit()

    conn.execute(
        "INSERT OR REPLACE INTO ingest_meta(key, value) VALUES (?, ?)",
        ("last_ingest_sessions_utc", str(int(time.time()))),
    )
    conn.commit()
    conn.close()
    return stats


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


def _cmd_ingest_sessions(args: argparse.Namespace) -> int:
    since = _parse_duration(args.since) if args.since else None
    stats = ingest_sessions(
        paths=args.path or None,
        since_seconds=since,
        limit=args.limit,
    )
    print(f"sessions_scanned={stats.files_scanned} "
          f"stale_skipped={stats.files_skipped_stale} "
          f"new_chunks={stats.chunks_new} existing={stats.chunks_existing}")
    for err in stats.errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return 0 if not stats.errors else 1


def _cmd_ingest_telegram(args: argparse.Namespace) -> int:
    if not args.path:
        print("ERROR: at least one --path is required (result.json file or export dir)",
              file=sys.stderr)
        return 1
    stats = ingest_telegram(paths=args.path)
    print(f"files_scanned={stats.files_scanned} "
          f"new_chunks={stats.chunks_new} existing={stats.chunks_existing}")
    for err in stats.errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return 0 if not stats.errors else 1


def _cmd_transcript(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if not path.is_file():
        print(f"ERROR: {path} is not a file", file=sys.stderr)
        return 1
    text = render_transcript(path)
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text)} chars)")
    else:
        sys.stdout.write(text)
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

    ingses = sub.add_parser(
        "ingest-sessions",
        help="ingest Claude Code session JSONL transcripts as turn-based chunks",
    )
    ingses.add_argument(
        "--path", action="append", default=[],
        help="specific JSONL to ingest (repeatable). If omitted, walks default session dirs.",
    )
    ingses.add_argument("--since", help="only files modified within last duration (e.g. 7d)")
    ingses.add_argument("--limit", type=int, default=None,
                        help="cap number of session files processed")
    ingses.set_defaults(fn=_cmd_ingest_sessions)

    ingtg = sub.add_parser(
        "ingest-telegram",
        help="ingest a Telegram Desktop chat export (result.json)",
    )
    ingtg.add_argument(
        "--path", action="append", default=[],
        help="path to result.json OR the export directory (repeatable)",
    )
    ingtg.set_defaults(fn=_cmd_ingest_telegram)

    tr = sub.add_parser(
        "transcript",
        help="boil a JSONL session down to a readable chat transcript",
    )
    tr.add_argument("path", help="path to a .jsonl session file")
    tr.add_argument("--out", default=None, help="write to file (default: stdout)")
    tr.set_defaults(fn=_cmd_transcript)

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
