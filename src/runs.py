"""Per-run isolation: nickname + run-dir destination.

Each `handle()` invocation gets a run-dir at
`~/.poe/workspace/runs/<handle_id>-<nickname>/` that is the destination
for run-specific writes (prompt, scope, resolved_intent, scratchpad,
PARTIAL files, step outputs, captain's log slice, repo bundle).

Design principle (Jeremy, 2026-04-26): writes go to the run-dir from
the start. No end-of-run gather/copy phase — the run-dir is the
destination, not a capture target. We're already doing the work; this
is organization, not new instrumentation.

The nickname is a deterministic 2-word label derived from handle_id so
runs can be referenced in conversation without copy-pasting UUIDs.
~50 adjectives × ~50 nouns ≈ 2500 combinations — unique-enough for
local use, memorable, greppable.

This module is intentionally tiny: nickname() + create_run_dir() +
write_metadata(). Wiring agent_loop / scope writers to land in the
run-dir is incremental and lives at each call site.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_ADJECTIVES = (
    "amber", "azure", "brisk", "calm", "clever", "cobalt", "crisp",
    "dapper", "dusky", "eager", "fierce", "frosty", "gentle", "gilded",
    "glassy", "golden", "hardy", "humble", "icy", "jaunty", "keen",
    "lively", "lucid", "merry", "misty", "noble", "nimble", "olive",
    "patient", "plucky", "quick", "quiet", "rapid", "ruby", "rustic",
    "silent", "silver", "sleek", "spry", "stout", "sturdy", "sunny",
    "swift", "tawny", "tidy", "vivid", "warm", "wily", "witty", "zesty",
)

_NOUNS = (
    "alder", "ash", "badger", "beacon", "birch", "brook", "cedar",
    "comet", "crane", "delta", "echo", "ember", "falcon", "ferret",
    "finch", "forge", "glen", "harbor", "haven", "heron", "ibis",
    "jasper", "kestrel", "lantern", "ledger", "lichen", "magpie",
    "marsh", "meadow", "moss", "nettle", "oak", "orchard", "otter",
    "pebble", "pine", "quartz", "raven", "ridge", "river", "saffron",
    "shore", "spruce", "thicket", "thorn", "tundra", "vale", "wren",
    "yarrow", "zephyr",
)


def nickname(handle_id: str) -> str:
    """Deterministic 2-word nickname from handle_id.

    Same handle_id always yields the same nickname. Uses sha1 to spread
    across the adjective/noun space evenly regardless of handle_id
    distribution.
    """
    if not handle_id:
        return "unset-run"
    digest = hashlib.sha1(handle_id.encode("utf-8")).digest()
    adj_idx = digest[0] % len(_ADJECTIVES)
    noun_idx = digest[1] % len(_NOUNS)
    return f"{_ADJECTIVES[adj_idx]}-{_NOUNS[noun_idx]}"


def runs_root() -> Path:
    """Workspace runs/ directory. Honors POE_WORKSPACE for tests."""
    ws = os.environ.get("POE_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if ws:
        return Path(ws) / "runs"
    return Path.home() / ".poe" / "workspace" / "runs"


def run_dir(handle_id: str) -> Path:
    """Path of the run-dir for a given handle_id (does not create it)."""
    return runs_root() / f"{handle_id}-{nickname(handle_id)}"


def create_run_dir(
    handle_id: str,
    *,
    prompt: str,
    lane: Optional[str] = None,
    model: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> Path:
    """Create the run-dir and seed metadata.json + prompt.txt.

    Returns the run-dir path. Idempotent: re-calling on the same
    handle_id refreshes metadata.json without clobbering existing
    artifacts (the agent may have already written into source/ /
    build/ / artifact/ subtrees).
    """
    rd = run_dir(handle_id)
    rd.mkdir(parents=True, exist_ok=True)

    # Subtree skeleton — source/build/artifact (Jeremy's compile mental model).
    # Created lazily on first write would also work, but pre-creating
    # makes "where does this go?" obvious to anyone inspecting mid-run.
    (rd / "source").mkdir(exist_ok=True)
    (rd / "build").mkdir(exist_ok=True)
    (rd / "artifact").mkdir(exist_ok=True)

    # prompt.txt is the verbatim user input — don't overwrite if it
    # already exists (the first call wins; subsequent calls are
    # no-ops on this file).
    prompt_path = rd / "source" / "prompt.txt"
    if not prompt_path.exists():
        prompt_path.write_text(prompt, encoding="utf-8")

    write_metadata(
        rd,
        handle_id=handle_id,
        prompt=prompt,
        lane=lane,
        model=model,
        extra=extra_metadata,
    )
    return rd


def write_metadata(
    rd: Path,
    *,
    handle_id: str,
    prompt: str,
    lane: Optional[str] = None,
    model: Optional[str] = None,
    status: Optional[str] = None,
    ended_at: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Path:
    """Write/refresh metadata.json. Preserves started_at if already set."""
    meta_path = rd / "metadata.json"
    started_at: Optional[str] = None
    existing: dict = {}
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            started_at = existing.get("started_at")
        except Exception:
            existing = {}
    if not started_at:
        started_at = datetime.now(timezone.utc).isoformat()

    meta = {
        "handle_id": handle_id,
        "nickname": nickname(handle_id),
        "prompt": prompt,
        "lane": lane,
        "model": model,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
    }
    if extra:
        # Caller-supplied keys merge in but don't override the core set.
        for k, v in extra.items():
            meta.setdefault(k, v)
    # Preserve any prior keys not in the core set (e.g. ended_at written
    # by an earlier finalize call when the current call doesn't supply it).
    for k, v in existing.items():
        if k not in meta or meta[k] is None:
            meta[k] = v

    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta_path


def finalize_run(
    handle_id: str,
    *,
    status: str,
    ended_at: Optional[str] = None,
) -> Optional[Path]:
    """Mark a run as ended in metadata.json. Returns run-dir or None if absent."""
    rd = run_dir(handle_id)
    if not rd.exists():
        return None
    meta_path = rd / "metadata.json"
    if not meta_path.exists():
        return rd
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meta["status"] = status
    meta["ended_at"] = ended_at or datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return rd
