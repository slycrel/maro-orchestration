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


# ---------------------------------------------------------------------------
# Current-run context: lets agent_loop / scope writers land in the run-dir
# without threading run_dir through every signature.
#
# A handle() invocation runs in one process; one run-dir at a time. Setting
# this is opt-in: when unset, callers fall back to the legacy project-dir
# artifact path. Tests that don't set it see no behavior change.
# ---------------------------------------------------------------------------

_current_run_dir: Optional[Path] = None


def set_current_run_dir(path: Optional[Path]) -> None:
    """Set (or clear) the run-dir for the current handle. Accepts None to clear."""
    global _current_run_dir
    _current_run_dir = Path(path) if path is not None else None


def current_run_dir() -> Optional[Path]:
    """Return the run-dir for the current handle, or None if unset."""
    return _current_run_dir


def current_handle_id() -> Optional[str]:
    """Handle id of the active run, derived from the pinned run-dir name
    (`<handle_id>-<nickname>`). None when no run-dir is pinned."""
    rd = current_run_dir()
    if rd is None:
        return None
    return rd.name.split("-", 1)[0]


def artifact_dir(project: str, project_root_fn=None) -> Path:
    """Where to write per-loop artifacts (PARTIAL files, scratchpad, step outputs).

    If a run-dir is active, returns `<run-dir>/build/`. Otherwise falls back
    to `project_root_fn()/project/artifacts` for backwards compatibility.
    The fallback is what every existing call site already computed inline,
    so swapping in this helper is behavior-preserving when no run-dir is set.

    `project_root_fn` is injected so callers can keep their existing
    `_project_dir_root` import path without circular-import shenanigans.
    """
    rd = current_run_dir()
    if rd is not None:
        out = rd / "build"
        out.mkdir(parents=True, exist_ok=True)
        return out
    if project_root_fn is None:
        # No run-dir AND no fallback — punt to the workspace default.
        ws = os.environ.get("POE_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
        root = Path(ws) / "projects" if ws else Path.home() / ".poe" / "workspace" / "projects"
    else:
        root = project_root_fn()
    out = Path(root) / project / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out


def source_dir() -> Optional[Path]:
    """`<run-dir>/source/` if a run-dir is active, else None.

    Used by handle.py for scope.md and resolved_intent.md placement.
    Callers fall back to project_dir-based writes when this returns None.
    """
    rd = current_run_dir()
    if rd is None:
        return None
    out = rd / "source"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Captain's log slicing — record offset at run-start, slice on finalize.
# Same pattern scope_ab_runner.py uses externally; centralized here so
# handle() can do it for every run, not just the experiment harness.
# ---------------------------------------------------------------------------

_run_log_offsets: dict = {}


def _captains_log_path() -> Optional[Path]:
    """Locate the captain's log file. None if captains_log isn't available."""
    try:
        from captains_log import _log_path  # type: ignore
        return _log_path()
    except Exception:
        return None


def record_log_offset(handle_id: str) -> None:
    """Record the captain's log byte-length at run start.

    Call this once at the top of handle() *after* the run-dir exists.
    Pairs with `slice_log_for_run()` at finalize.
    """
    log_path = _captains_log_path()
    if log_path is None:
        return
    try:
        offset = log_path.stat().st_size if log_path.exists() else 0
    except Exception:
        offset = 0
    _run_log_offsets[handle_id] = offset


def slice_log_for_run(handle_id: str) -> Optional[Path]:
    """Write `<run-dir>/build/captains_log_slice.jsonl` covering this run.

    Reads from the offset recorded by `record_log_offset()` to the
    current end of file. Returns the slice path on success, None on
    failure or when no offset was recorded.
    """
    log_path = _captains_log_path()
    if log_path is None or not log_path.exists():
        return None
    rd = run_dir(handle_id)
    if not rd.exists():
        return None
    offset = _run_log_offsets.get(handle_id, 0)
    out = rd / "build" / "captains_log_slice.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("rb") as src, out.open("wb") as dst:
            src.seek(offset)
            while True:
                chunk = src.read(64 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
    except Exception:
        return None
    return out


# ---------------------------------------------------------------------------
# Repo bundle — captures git state at end-of-run so the artifact survives
# downstream resets. Restorable with `git clone repo.bundle`.
# ---------------------------------------------------------------------------

import subprocess
_run_repo_bases: dict = {}


def record_repo_base(handle_id: str, repo_path: str) -> None:
    """Record the current HEAD sha of repo_path so end-of-run can diff against it.

    Call this once at run start when a --repo is supplied. Pairs with
    `snapshot_repo_bundle()` at finalize.
    """
    if not repo_path:
        return
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            _run_repo_bases[handle_id] = (repo_path, result.stdout.strip())
    except Exception:
        pass


def snapshot_repo_bundle(handle_id: str) -> Optional[Path]:
    """Write `<run-dir>/artifact/repo.bundle` + git_log.txt + branch_diff.patch.

    Restorable with `git clone repo.bundle`. Captures the current state
    of the repo paired in `record_repo_base()`. Returns the bundle path
    on success, None if no repo was paired or the snapshot failed.
    """
    pair = _run_repo_bases.get(handle_id)
    if not pair:
        return None
    repo_path, base_sha = pair
    rd = run_dir(handle_id)
    if not rd.exists():
        return None
    out_dir = rd / "artifact"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / "repo.bundle"
    try:
        subprocess.run(
            ["git", "bundle", "create", str(bundle), "--all"],
            cwd=repo_path, capture_output=True, timeout=60, check=True,
        )
    except Exception:
        return None
    # Best-effort: log + diff. Failures here don't void the bundle.
    try:
        log_out = subprocess.run(
            ["git", "log", "--all", "--graph", "--oneline", "-100"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        (out_dir / "git_log.txt").write_text(log_out.stdout, encoding="utf-8")
    except Exception:
        pass
    try:
        diff_out = subprocess.run(
            ["git", "diff", f"{base_sha}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        (out_dir / "branch_diff.patch").write_text(diff_out.stdout, encoding="utf-8")
    except Exception:
        pass
    try:
        (out_dir / "base_sha.txt").write_text(base_sha + "\n", encoding="utf-8")
    except Exception:
        pass
    return bundle
