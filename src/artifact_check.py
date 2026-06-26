"""Filesystem ground-truth check for fabricated artifact claims (zero-LLM).

The done≠achieved gap: a build step can report status="done" and narrate
"wrote fizzbuzz.py" / "ran the tests, 142 passed" / "verified output 1,2,Fizz…"
without any file actually landing in the workspace. A text-only verdict (the
LLM judge or local validator) can't see the filesystem, so the fabrication
reaches "done" unchallenged.

Now that executor writes are bounded to project_dir (the subprocess cwd is set,
see llm._run_subprocess_safe / step_exec.execute_step), a before/after snapshot
of project_dir is reliable ground truth: if a step *claims* a write but the
workspace didn't change and the claimed file exists nowhere on disk, the step
fabricated its result.

This is the AGENDA build-loop sibling of handle.py's NOW-lane provenance guard
(`_provenance_missing`). Zero LLM, runs in <10ms, and fails open — any internal
error yields a "not fabricated" verdict so the check never blocks legitimate
work on its own bug.

Deliberately conservative (v1): a step is flagged ONLY when it claims a write,
the project_dir diff is empty (zero files created/modified), AND none of the
claimed paths exist anywhere checkable. That triple is unambiguous fabrication;
misplaced-but-real writes (the #1 cwd bug, now fixed) and explicit
out-of-workspace writes both leave on-disk evidence and are not flagged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# Verb-anchored write-claim regex — mirrors handle.py:_OUTPUT_CLAIM_RE so the
# AGENDA check and the NOW provenance guard extract claims the same way. Matches
# "wrote/saved/created/generated/… to|into|at|as <path>". Requiring the verb is
# what separates a real write-claim from an incidental path mention.
_OUTPUT_CLAIM_RE = re.compile(
    # Mirrors handle.py:_OUTPUT_CLAIM_RE, plus "wrote" (handle's `writ\w*` matches
    # write/writes/writing/written but NOT the irregular past tense "wrote").
    r"\b(?:sav\w*|writ\w*|wrote|creat\w*|output\w*|stor\w*|export\w*|generat\w*|dump\w*)\b"
    r"[^.\n]*?\b(?:to|into|at|as)\s+[`'\"(]?(?P<path>[^\s`'\")]+)",
    re.IGNORECASE,
)
# A claimed token only counts if it looks like a file (has an extension) — this
# drops "saved to memory", "wrote to the database", "stored as a draft", etc.
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")

# Directories never worth walking for a workspace diff.
_SKIP_DIRS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"})
# Safety cap so a pathological workspace can't make the diff walk unbounded.
_MAX_FILES = 20000
# mtime jitter tolerance (seconds) — a file is "changed" only if its mtime
# advanced meaningfully past the snapshot.
_MTIME_EPS = 1e-4


@dataclass
class ArtifactVerdict:
    """Result of the filesystem ground-truth check for one step."""
    fabricated: bool
    claims: List[str] = field(default_factory=list)      # write-claims extracted from result
    missing: List[str] = field(default_factory=list)     # claims with no on-disk evidence
    changed_count: int = 0                               # files created/modified in project_dir
    reason: str = ""


def _clean_token(tok: str) -> str:
    return tok.strip().strip("`'\"()").rstrip(".,;:")


def extract_write_claims(text: str) -> List[str]:
    """Extract file paths a result *claims* to have written. Deduplicated.

    Only tokens that look like files (have an extension) are returned.
    """
    out: List[str] = []
    seen: Set[str] = set()
    for m in _OUTPUT_CLAIM_RE.finditer(text or ""):
        tok = _clean_token(m.group("path"))
        if not tok or tok in ("/", "./", "../") or tok.endswith("/"):
            continue
        if not _EXT_RE.search(tok):
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def snapshot_dir(root: Optional[str | os.PathLike]) -> Dict[str, float]:
    """Map relpath -> mtime for every file under `root`. {} if root is missing.

    Bounded walk: skips VCS/cache dirs and stops after _MAX_FILES. Never raises.
    """
    snap: Dict[str, float] = {}
    if not root:
        return snap
    base = Path(root)
    if not base.is_dir():
        return snap
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    rel = str(fp.relative_to(base))
                    snap[rel] = fp.stat().st_mtime
                except OSError:
                    continue
                count += 1
                if count >= _MAX_FILES:
                    return snap
    except OSError:
        return snap
    return snap


def changed_since(before: Dict[str, float], root: Optional[str | os.PathLike]) -> Set[str]:
    """Relpaths under `root` that are new or whose mtime advanced past `before`."""
    after = snapshot_dir(root)
    changed: Set[str] = set()
    for rel, mtime in after.items():
        prev = before.get(rel)
        if prev is None or (mtime - prev) > _MTIME_EPS:
            changed.add(rel)
    return changed


def _exists_anywhere(claim: str, project_dir: Optional[str | os.PathLike]) -> bool:
    """True if the claimed path resolves to a real file we can credit as evidence.

    An on-disk file is evidence the step did real work — so NOT fabrication. We
    deliberately do NOT consult Path.cwd(): the orchestrator's cwd is the repo
    root, full of unrelated files, and a basename collision there (e.g. a claim
    of "wrote config.py" finding the repo's config.py) would silently mask a
    fabrication. The executor subprocess already runs with cwd=project_dir, so a
    relative write lands in project_dir (checked here) and an absolute write is
    checked by its own path — the cwd of the parent process is never a write
    target worth crediting.
    """
    try:
        p = Path(claim)
        if p.is_absolute():
            return p.exists()
        if project_dir:
            base = Path(project_dir)
            if (base / claim).exists() or (base / p.name).exists():
                return True
    except OSError:
        return False
    return False


def check_fabrication(
    result_text: str,
    project_dir: Optional[str | os.PathLike],
    before_snapshot: Dict[str, float],
) -> ArtifactVerdict:
    """Ground-truth check: did a write-claiming step actually produce anything?

    Conservative v1 rule — fabricated iff ALL hold:
      1. the result contains >=1 file write-claim, AND
      2. the project_dir before/after diff is empty (no file created/modified), AND
      3. none of the claimed paths exist anywhere checkable.

    Fails open: any internal error returns fabricated=False.
    """
    try:
        claims = extract_write_claims(result_text)
        if not claims:
            return ArtifactVerdict(False, reason="no write-claims")

        changed = changed_since(before_snapshot, project_dir)
        missing = [c for c in claims if not _exists_anywhere(c, project_dir)]

        fabricated = len(changed) == 0 and len(missing) == len(claims)
        if fabricated:
            reason = (
                f"step claimed to write {claims} but produced no files in the "
                f"workspace and none exist on disk"
            )
        else:
            reason = "write-claims have on-disk evidence"
        return ArtifactVerdict(
            fabricated=fabricated,
            claims=claims,
            missing=missing,
            changed_count=len(changed),
            reason=reason,
        )
    except Exception:
        return ArtifactVerdict(False, reason="check error (fail-open)")
