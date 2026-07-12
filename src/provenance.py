"""Deterministic provenance guard — done != achieved check.

Extracted from handle.py (pure move, Tier 3 of docs/REFACTOR_PLAN.md):
a text-only verdict (the LLM judge, or the local validator) can't see whether
a claimed input was actually read or a claimed output actually landed. Live
find (shadow-eval n=42, 2026-06-24): "save the listing to
artifacts/skills-listing.txt" saved to a DIFFERENT path and narrated success —
local PASSed at conf 1.00, paid FAILed.

Three deterministic checks:
  * OUTPUT, dir-qualified  ("save … to artifacts/X")  → STRICT: must be at that
    exact path. The user said *where*; honor it.
  * OUTPUT, bare filename   ("save … to report.md")    → LENIENT: the basename
    must exist *somewhere* reasonable (location ambiguous → don't punish it).
  * INPUT, dir-qualified    ("read /nonexistent/X")     → STRICT: the input must
    exist (you can't read a file that isn't there). Remote (URLs) and transient
    (/tmp, scratchpad) inputs are skipped — can't/shouldn't verify them.
Same provenance-blindness root as the fabricated-input recovery guard; this is
the verdict-layer net for fabrication that reaches "done" without blocking.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("maro.handle")

_OUTPUT_CLAIM_RE = re.compile(
    r"\b(?:sav\w*|writ\w*|creat\w*|output\w*|stor\w*|export\w*|generat\w*|dump\w*)\b"
    r"[^.\n]*?\b(?:to|into|at|as)\s+[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
_INPUT_CLAIM_RE = re.compile(
    r"\b(?:read|load|open|pars\w*|fetch|import|ingest)\w*\s+"
    r"(?:the\s+|a\s+|file\s+|from\s+|contents?\s+of\s+|in\s+)*"
    r"[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_REMOTE_PREFIXES = ("http://", "https://", "ftp://", "s3://", "gs://", "git@", "ssh://")
_TRANSIENT_SEGMENTS = ("/tmp/", "scratchpad", "/dev/", "/proc/", "/var/tmp/")


def _clean_path_token(tok: str) -> str:
    return tok.strip().strip("`'\"()").rstrip(".,;:")


def _claimed_output_paths(goal: str) -> List[str]:
    """Dir-qualified output paths the goal asks to be written (user said *where*)."""
    out: List[str] = []
    for m in _OUTPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        if "/" in tok and tok not in ("/", "./", "../") and not tok.endswith("/"):
            out.append(tok)
    return out


def _claimed_output_bare(goal: str) -> List[str]:
    """Bare output filenames (no dir, has an extension) — user said only *what*."""
    out: List[str] = []
    for m in _OUTPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        if "/" not in tok and _EXT_RE.search(tok):
            out.append(tok)
    return out


def _claimed_input_paths(goal: str) -> List[str]:
    """Dir-qualified, local, non-transient input paths the goal asks to read."""
    out: List[str] = []
    for m in _INPUT_CLAIM_RE.finditer(goal or ""):
        tok = _clean_path_token(m.group("path"))
        low = tok.lower()
        if low.startswith(_REMOTE_PREFIXES):
            continue                      # remote — can't cheaply verify
        if "/" not in tok:
            continue                      # bare name — ambiguous
        if any(seg in low for seg in _TRANSIENT_SEGMENTS):
            continue                      # may be gone by verdict time
        out.append(tok)
    return out


def _output_provenance_bases() -> List[Path]:
    """Candidate base dirs a relative path could legitimately resolve under.
    Generous on purpose — a false demotion is worse than a missed one."""
    bases: List[Path] = []
    for fn in (
        lambda: Path.cwd(),
        lambda: Path(__file__).resolve().parent.parent,
    ):
        try:
            bases.append(fn())
        except Exception:
            pass
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd:
            bases.append(Path(rd))
    except Exception:
        pass
    try:
        from config import workspace_root
        ws = Path(workspace_root())
        bases.extend([ws, ws / "output"])
    except Exception:
        pass
    return bases


def _exists_at_exact(rel: str, bases: List[Path]) -> bool:
    """True if a (possibly relative) path resolves to an existing file. For
    relative paths, also checks one level under workspace/projects/<slug>/."""
    p = Path(rel)
    if p.is_absolute():
        return p.exists()
    if any((b / rel).exists() for b in bases):
        return True
    try:
        from config import workspace_root
        ws_projects = Path(workspace_root()) / "projects"
        if ws_projects.is_dir():
            return any((d / rel).exists() for d in ws_projects.glob("*") if d.is_dir())
    except Exception:
        pass
    return False


def _bare_search_dirs() -> List[Path]:
    """Small, bounded landing spots to scan for a bare output basename."""
    dirs: List[Path] = []
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd:
            dirs.append(Path(rd))
    except Exception:
        pass
    try:
        from config import workspace_root
        ws = Path(workspace_root())
        dirs.extend([ws / "output", ws / "projects"])
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


def _exists_bare_anywhere(name: str, bases: List[Path]) -> bool:
    """True if a bare basename exists under any base (direct) or any landing
    spot (one or two levels deep — where run/project/output files land)."""
    if any((b / name).exists() for b in bases):
        return True
    for d in _bare_search_dirs():
        try:
            if (d / name).exists():
                return True
            if any(d.glob(f"*/{name}")) or any(d.glob(f"*/*/{name}")):
                return True
        except Exception:
            pass
    return False


def _missing_claimed_outputs(goal: str) -> List[str]:
    """Dir-qualified output paths named in the goal that don't exist at that
    exact location. Empty = nothing claimed, or everything landed. Fails open."""
    claimed = _claimed_output_paths(goal)
    if not claimed:
        return []
    bases = _output_provenance_bases()
    return [rel for rel in claimed if not _exists_at_exact(rel, bases)]


def _missing_output_bare(goal: str) -> List[str]:
    """Bare output filenames whose basename exists nowhere reasonable (the
    output was never produced). Lenient: location is not part of the contract."""
    bare = _claimed_output_bare(goal)
    if not bare:
        return []
    bases = _output_provenance_bases()
    return [name for name in bare if not _exists_bare_anywhere(name, bases)]


def _missing_claimed_inputs(goal: str) -> List[str]:
    """Dir-qualified local input paths the goal asks to read that don't exist —
    you can't legitimately read a file that isn't there. Fails open."""
    claimed = _claimed_input_paths(goal)
    if not claimed:
        return []
    bases = _output_provenance_bases()
    return [rel for rel in claimed if not _exists_at_exact(rel, bases)]


# --- Tool-evidence layer ----------------------------------------------------
# The three checks above scan the GOAL text. This one scans the RESULT text for
# paths the run CLAIMS it wrote ("saved to X", "wrote report.md") and demotes
# unless that path exists AND was modified during this run's wall-clock window.
# The mtime gate is the actual evidence of a side effect: a pre-existing stale
# file with the right name does NOT prove the run wrote it. This is what catches
# fabrication when the GOAL named no path (the *claim* names it) and the n=42
# "narrated success, saved elsewhere/nowhere" case the text-only judge missed.
# Window is intentionally generous (buffer) — a missed fabrication is cheaper
# than a false demotion (fail-open).
# Residual it CANNOT catch (no execution transcript is available from `claude -p
# --output-format json` — only the final text): a run that fabricates a result
# with no file claim at all ("ran the tests: 142 passed" writing nothing). That
# needs tool-call evidence the backend doesn't expose. Documented, not solved.
_WINDOW_BUFFER_SECS = 120.0


def _run_window_start(elapsed_ms) -> Optional[float]:
    """Wall-clock instant before which a file mtime can't be evidence of THIS
    run: now - elapsed - buffer. None (skip the gate) when elapsed is unknown."""
    try:
        ems = float(elapsed_ms or 0)
        if ems <= 0:
            return None
        return time.time() - ems / 1000.0 - _WINDOW_BUFFER_SECS
    except Exception:
        return None


def _resolve_exact(rel: str, bases: List[Path]) -> List[Path]:
    """ALL existing candidates a (possibly relative) path could resolve to.

    Generic names (step_data.json, artifacts/step-N-output.txt) exist in many
    workspace projects; freshness must be judged across every candidate, not
    the first glob hit — run 75fe8b4e was falsely demoted to incomplete when
    its fresh output resolved to an older project's file of the same name.
    """
    p = Path(rel)
    if p.is_absolute():
        return [p] if p.exists() else []
    hits: List[Path] = []
    for b in bases:
        if (b / rel).exists():
            hits.append(b / rel)
    try:
        from config import workspace_root
        ws_projects = Path(workspace_root()) / "projects"
        if ws_projects.is_dir():
            for d in ws_projects.glob("*"):
                if d.is_dir() and (d / rel).exists():
                    hits.append(d / rel)
    except Exception:
        pass
    return hits


def _resolve_bare(name: str, bases: List[Path]) -> List[Path]:
    """ALL existing candidates for a bare output basename (see _resolve_exact)."""
    hits: List[Path] = []
    for b in bases:
        if (b / name).exists():
            hits.append(b / name)
    for d in _bare_search_dirs():
        try:
            if (d / name).exists():
                hits.append(d / name)
            hits.extend(d.glob(f"*/{name}"))
            hits.extend(d.glob(f"*/*/{name}"))
        except Exception:
            pass
    return hits


def _is_fresh(path: Path, window_start: float) -> bool:
    """True if the file was modified at/after the run window start. Can't stat →
    True (fail open — never punish on an inability to check)."""
    try:
        return path.stat().st_mtime >= window_start
    except Exception:
        return True


def _result_claimed_outputs(text: str) -> List[str]:
    """Output paths a result narration claims to have written — dir-qualified
    and bare — minus remote/transient (can't have been written locally now)."""
    out: List[str] = []
    for rel in _claimed_output_paths(text) + _claimed_output_bare(text):
        low = rel.lower()
        if low.startswith(_REMOTE_PREFIXES):
            continue
        if any(seg in low for seg in _TRANSIENT_SEGMENTS):
            continue
        out.append(rel)
    return out


def _missing_or_stale_result_outputs(result_text: str, window_start: float) -> List[str]:
    """Output paths the RESULT claims to have written that either don't exist or
    predate the run window (so the run did not actually write them). Fails open."""
    claimed = _result_claimed_outputs(result_text or "")
    if not claimed:
        return []
    bases = _output_provenance_bases()
    flagged: List[str] = []
    for rel in claimed:
        if "/" in rel and not rel.endswith("/"):
            candidates = _resolve_exact(rel, bases)
        else:
            candidates = _resolve_bare(rel, bases)
        if not candidates:
            flagged.append(f"{rel} (claimed written, not found)")
        elif not any(_is_fresh(c, window_start) for c in candidates):
            flagged.append(f"{rel} (claimed written, but predates this run)")
    return flagged


def _provenance_missing(goal: str, *, result_text: Optional[str] = None,
                        window_start: Optional[float] = None) -> List[str]:
    """Aggregate deterministic provenance failures, honoring config flags. Scans
    the GOAL (output/input claims) and, when result_text + window_start are
    given, the RESULT (tool-evidence: claimed-written paths must exist and be
    fresh). Empty = nothing to flag. Never raises (fails open)."""
    missing: List[str] = []
    try:
        from config import get as _cfg_get
        if _cfg_get("validate.output_provenance", True):
            missing.extend(_missing_claimed_outputs(goal))
            missing.extend(_missing_output_bare(goal))
        if _cfg_get("validate.input_provenance", True):
            missing.extend(_missing_claimed_inputs(goal))
        if (result_text and window_start is not None
                and _cfg_get("validate.result_provenance", True)):
            missing.extend(_missing_or_stale_result_outputs(result_text, window_start))
    except Exception as exc:
        log.debug("provenance check skipped: %s", exc)
    return list(dict.fromkeys(missing))  # dedup, preserve order
