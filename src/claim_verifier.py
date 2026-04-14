"""Claim verifier — zero-LLM file-existence check for step results.

Addresses the hallucination pattern identified in the 2026-04-06 blind
adversarial run: synthesis steps confabulate file paths that don't exist
(e.g. "lat.py", "test_workers.py"). This module:

  1. Extracts file-path claims from step result text using regex.
  2. Checks each claim against the filesystem.
  3. Returns a ClaimReport with verified / not-found / unresolvable lists.
  4. Annotates result text with [FILE:VERIFIED] / [FILE:NOT_FOUND] markers.

No LLM call. Runs in <1ms. Designed for integration into agent_loop.py
after synthesis steps to surface false file claims before they propagate.

Usage:
    from claim_verifier import verify_file_claims, annotate_result
    report = verify_file_claims(result_text, project_root=Path("."))
    if report.not_found:
        print("Hallucinated paths:", report.not_found)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ClaimReport:
    """Result of verifying file-path claims in a text block."""
    raw_claims: List[str]       # all paths extracted from text
    verified: List[str]         # paths that exist on disk
    not_found: List[str]        # paths that don't exist (potential hallucination)
    unresolvable: List[str]     # paths we couldn't check (no root, ambiguous)

    @property
    def has_hallucinations(self) -> bool:
        return len(self.not_found) > 0

    @property
    def hallucination_rate(self) -> float:
        checkable = len(self.verified) + len(self.not_found)
        return len(self.not_found) / checkable if checkable else 0.0

    def summary(self) -> str:
        parts = []
        if self.verified:
            parts.append(f"{len(self.verified)} verified")
        if self.not_found:
            parts.append(f"{len(self.not_found)} NOT FOUND: {', '.join(self.not_found[:3])}")
        if self.unresolvable:
            parts.append(f"{len(self.unresolvable)} unresolvable")
        return "; ".join(parts) if parts else "no file claims found"


# ---------------------------------------------------------------------------
# Extraction patterns
# ---------------------------------------------------------------------------

# Matches: src/foo.py, tests/test_bar.py, docs/ARCH.md, lat.md/index.md
_FILE_PATH_RE = re.compile(
    r"""
    (?<![\w`'"(])                        # not immediately after word char, quote, or paren
    (?:
        (?:src|tests?|docs?|lat\.md|scripts?|personas?|memory|deploy)/   # known dir prefix
        [\w/.-]+\.(?:py|md|json|yaml|yml|sh|txt|toml|cfg|ini)
    |
        [\w.-]+\.py                      # bare *.py anywhere
    |
        [\w.-]+\.md                      # bare *.md anywhere (capitalized too)
    )
    (?![\w`'")])                         # not immediately before word char, quote, or paren
    """,
    re.VERBOSE,
)

# Modules that are stdlib or common packages — skip these
_SKIP_NAMES = frozenset({
    "os.py", "sys.py", "json.py", "re.py", "io.py", "time.py", "uuid.py",
    "pathlib.py", "datetime.py", "logging.py", "typing.py", "abc.py",
    "dataclasses.py", "functools.py", "itertools.py", "collections.py",
    "threading.py", "subprocess.py", "shutil.py", "hashlib.py",
    "README.md", "CHANGELOG.md", "LICENSE.md",
})

# Strings that look like file paths but aren't (common false positives)
_SKIP_PATTERNS = frozenset({
    "setup.py",   # could be in project root
    "manage.py",  # Django — not our project
})


def extract_file_claims(text: str) -> List[str]:
    """Extract file-path claims from text. Returns deduplicated list."""
    raw = _FILE_PATH_RE.findall(text)
    seen = set()
    result = []
    for path in raw:
        path = path.strip(" .,;:\"'`")
        name = Path(path).name
        if name in _SKIP_NAMES or path in _SKIP_PATTERNS:
            continue
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def verify_file_claims(
    text: str,
    project_root: Optional[Path] = None,
) -> ClaimReport:
    """Extract file-path claims from text and verify existence on disk.

    Args:
        text: Step result or synthesis output to scan.
        project_root: Root directory to resolve relative paths against.
                      If None, tries to infer from CWD or repo structure.

    Returns:
        ClaimReport with verified/not_found/unresolvable lists.
    """
    if project_root is None:
        # Try to find repo root by looking for pyproject.toml upward from CWD
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            if (parent / "pyproject.toml").exists() or (parent / "src").exists():
                project_root = parent
                break
        else:
            project_root = cwd

    claims = extract_file_claims(text)
    verified = []
    not_found = []
    unresolvable = []

    for claim in claims:
        try:
            candidate = project_root / claim
            if candidate.exists():
                verified.append(claim)
            else:
                # Also try just the filename in src/ and tests/
                found = False
                for search_dir in [project_root / "src", project_root / "tests", project_root]:
                    if (search_dir / Path(claim).name).exists():
                        found = True
                        break
                if found:
                    verified.append(claim)
                else:
                    not_found.append(claim)
        except Exception:
            unresolvable.append(claim)

    return ClaimReport(
        raw_claims=claims,
        verified=verified,
        not_found=not_found,
        unresolvable=unresolvable,
    )


def annotate_result(
    result_text: str,
    project_root: Optional[Path] = None,
    *,
    only_if_hallucinations: bool = True,
) -> str:
    """Return result_text with a file-claim verification note appended.

    If no hallucinations found and only_if_hallucinations=True, returns
    the text unchanged (zero noise for clean steps).

    Args:
        result_text: Step result to annotate.
        project_root: Root directory for path resolution.
        only_if_hallucinations: If True, only append note when NOT_FOUND claims exist.

    Returns:
        Annotated text, or original text if clean.
    """
    report = verify_file_claims(result_text, project_root=project_root)

    if only_if_hallucinations and not report.has_hallucinations:
        return result_text

    parts = []
    if report.not_found:
        parts.append(f"FILE_CLAIMS_NOT_FOUND: {', '.join(report.not_found)}")
    if report.verified and not only_if_hallucinations:
        parts.append(f"FILE_CLAIMS_VERIFIED: {', '.join(report.verified[:5])}")

    if not parts:
        return result_text

    return result_text + "\n\n[claim-verifier] " + " | ".join(parts)


# ---------------------------------------------------------------------------
# Synthesis step detection
# ---------------------------------------------------------------------------

_SYNTHESIS_KEYWORDS = frozenset({
    "synthesize", "synthesis", "summarize", "compile findings", "write report",
    "analyze all", "review all", "aggregate", "combine findings",
    "final report", "final summary", "conclusion",
})


def is_synthesis_step(step_text: str) -> bool:
    """Heuristic: is this step a synthesis/summarization step?

    Synthesis steps are the highest hallucination risk because they read
    accumulated findings from many prior steps and must cite specific files.

    Returns True if the step text matches synthesis patterns.
    """
    lower = step_text.lower()
    return any(kw in lower for kw in _SYNTHESIS_KEYWORDS)
