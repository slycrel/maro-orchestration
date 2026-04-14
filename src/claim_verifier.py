"""Claim verifier — zero-LLM file-existence and symbol-existence check.

Addresses the hallucination pattern identified in the 2026-04-06 blind
adversarial run: synthesis steps confabulate file paths that don't exist
(e.g. "lat.py", "test_workers.py") or reference functions/classes that
don't exist in the codebase.

This module:
  1. Extracts file-path claims and checks them against the filesystem.
  2. Extracts symbol claims (function/class/method names) and checks them
     via a direct scan of Python source files in the project.
  3. Returns ClaimReport (files) and SymbolReport (symbols) — combined
     in a CompoundClaimReport.
  4. Annotates result text with NOT_FOUND markers for hallucinated claims.

No LLM call. Runs in <10ms. Designed for integration into agent_loop.py
after synthesis steps to surface false claims before they propagate.

Usage:
    from claim_verifier import verify_file_claims, verify_all_claims, annotate_result
    report = verify_all_claims(result_text, project_root=Path("."))
    if report.has_hallucinations:
        print("Not found:", report.not_found_files, report.not_found_symbols)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


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


@dataclass
class SymbolReport:
    """Result of verifying Python symbol claims in a text block."""
    raw_claims: List[str]        # all symbol names extracted from text
    verified: List[str]          # symbols found in source files
    not_found: List[str]         # symbols not found anywhere (potential hallucination)

    @property
    def has_hallucinations(self) -> bool:
        return len(self.not_found) > 0

    def summary(self) -> str:
        parts = []
        if self.verified:
            parts.append(f"{len(self.verified)} symbol(s) verified")
        if self.not_found:
            parts.append(f"{len(self.not_found)} symbol(s) NOT FOUND: {', '.join(self.not_found[:3])}")
        return "; ".join(parts) if parts else "no symbol claims found"


@dataclass
class CompoundClaimReport:
    """Combined file + symbol claim verification result."""
    file_report: ClaimReport
    symbol_report: SymbolReport

    @property
    def has_hallucinations(self) -> bool:
        return self.file_report.has_hallucinations or self.symbol_report.has_hallucinations

    @property
    def not_found_files(self) -> List[str]:
        return self.file_report.not_found

    @property
    def not_found_symbols(self) -> List[str]:
        return self.symbol_report.not_found

    def summary(self) -> str:
        parts = []
        f = self.file_report.summary()
        s = self.symbol_report.summary()
        if f and f != "no file claims found":
            parts.append(f"files: {f}")
        if s and s != "no symbol claims found":
            parts.append(f"symbols: {s}")
        return " | ".join(parts) if parts else "no claims found"


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


# ---------------------------------------------------------------------------
# Symbol extraction and verification
# ---------------------------------------------------------------------------

# Matches symbol names in claims like:
#   `apply_suggestion()`      → "apply_suggestion"
#   `class Director`          → "Director"
#   `def run_agent_loop`      → "run_agent_loop"
#   "the scan_outcomes_for_signals function"  → "scan_outcomes_for_signals"
#   "`_verify_post_apply`"    → "_verify_post_apply"
_SYMBOL_BACKTICK_RE = re.compile(r"`([A-Za-z_]\w+)\s*(?:\([^)]{0,40}\))?`")
_SYMBOL_DEF_RE = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w+)")
_SYMBOL_CONTEXT_RE = re.compile(
    r"\b([A-Za-z_]\w+)\s*(?:\(\)|\([^)]{0,40}\))?\s+(?:function|method|class|property|attribute)\b"
    r"|\b(?:function|method|class|property)\s+`?([A-Za-z_]\w+)`?",
    re.IGNORECASE,
)

# Skip common keywords, builtins, and single-letter names that are not real claims
_SYMBOL_SKIP = frozenset({
    "True", "False", "None", "self", "cls", "args", "kwargs",
    "int", "str", "bool", "list", "dict", "set", "tuple", "float",
    "Optional", "List", "Dict", "Set", "Tuple", "Any", "Union",
    "Path", "Exception", "ValueError", "TypeError", "KeyError",
    "RuntimeError", "StopIteration", "yield", "return", "raise",
    "import", "from", "as", "if", "else", "elif", "for", "while",
    "with", "try", "except", "finally", "pass", "break", "continue",
    "lambda", "and", "or", "not", "in", "is", "class", "def",
    "print", "len", "range", "open", "type", "super",
})

# Minimum symbol name length to bother checking (avoids noise on e.g. "id", "db")
_SYMBOL_MIN_LEN = 5


def extract_symbol_claims(text: str) -> List[str]:
    """Extract Python symbol names claimed to exist in the codebase.

    Looks for:
    - Backtick-quoted names: `apply_suggestion()`, `Director`
    - def/class declarations: "def run_agent_loop", "class Director"
    - Contextual patterns: "the scan_outcomes_for_signals function"

    Returns deduplicated list, skipping stdlib/builtins/short names.
    """
    candidates: Set[str] = set()

    for m in _SYMBOL_BACKTICK_RE.finditer(text):
        candidates.add(m.group(1))
    for m in _SYMBOL_DEF_RE.finditer(text):
        candidates.add(m.group(1))
    for m in _SYMBOL_CONTEXT_RE.finditer(text):
        sym = m.group(1) or m.group(2)
        if sym:
            candidates.add(sym)

    return [
        s for s in sorted(candidates)
        if s not in _SYMBOL_SKIP
        and len(s) >= _SYMBOL_MIN_LEN
        and not s.startswith("__")
    ]


def _build_symbol_index(project_root: Path) -> Set[str]:
    """Scan Python source files in the project and return a set of top-level symbols.

    Looks for `def name` and `class name` lines in .py files under `src/`
    and `tests/`. Runs in <5ms on a typical project.
    """
    symbols: Set[str] = set()
    _def_or_class = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w+)|^\s*class\s+([A-Za-z_]\w+)")
    search_dirs = [project_root / "src", project_root / "tests", project_root]
    seen_dirs: Set[Path] = set()
    for search_dir in search_dirs:
        if not search_dir.is_dir() or search_dir in seen_dirs:
            continue
        seen_dirs.add(search_dir)
        for py_file in search_dir.glob("*.py"):
            try:
                for line in py_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    m = _def_or_class.match(line)
                    if m:
                        sym = m.group(1) or m.group(2)
                        if sym:
                            symbols.add(sym)
            except OSError:
                continue
    return symbols


def verify_symbol_claims(
    text: str,
    project_root: Optional[Path] = None,
) -> SymbolReport:
    """Extract symbol claims from text and verify they exist in the codebase.

    Uses a direct scan of .py files in src/ and tests/ — no grep subprocess,
    no LLM. Returns SymbolReport with verified/not_found lists.
    """
    if project_root is None:
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            if (parent / "pyproject.toml").exists() or (parent / "src").exists():
                project_root = parent
                break
        else:
            project_root = cwd

    claims = extract_symbol_claims(text)
    if not claims:
        return SymbolReport(raw_claims=[], verified=[], not_found=[])

    index = _build_symbol_index(project_root)
    verified = [s for s in claims if s in index]
    not_found = [s for s in claims if s not in index]
    return SymbolReport(raw_claims=claims, verified=verified, not_found=not_found)


def verify_all_claims(
    text: str,
    project_root: Optional[Path] = None,
) -> CompoundClaimReport:
    """Run both file-path and symbol verification on a text block.

    Returns a CompoundClaimReport combining both report types.
    """
    file_report = verify_file_claims(text, project_root=project_root)
    symbol_report = verify_symbol_claims(text, project_root=project_root)
    return CompoundClaimReport(file_report=file_report, symbol_report=symbol_report)


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
    check_symbols: bool = True,
) -> str:
    """Return result_text with a claim verification note appended.

    Checks both file-path claims and (optionally) Python symbol claims.
    If no hallucinations found and only_if_hallucinations=True, returns
    the text unchanged (zero noise for clean steps).

    Args:
        result_text: Step result to annotate.
        project_root: Root directory for path resolution.
        only_if_hallucinations: If True, only append note when NOT_FOUND claims exist.
        check_symbols: If True, also check Python symbol existence.

    Returns:
        Annotated text, or original text if clean.
    """
    file_report = verify_file_claims(result_text, project_root=project_root)
    symbol_report = (
        verify_symbol_claims(result_text, project_root=project_root)
        if check_symbols else SymbolReport(raw_claims=[], verified=[], not_found=[])
    )

    has_any = file_report.has_hallucinations or symbol_report.has_hallucinations
    if only_if_hallucinations and not has_any:
        return result_text

    parts = []
    if file_report.not_found:
        parts.append(f"FILE_CLAIMS_NOT_FOUND: {', '.join(file_report.not_found)}")
    if file_report.verified and not only_if_hallucinations:
        parts.append(f"FILE_CLAIMS_VERIFIED: {', '.join(file_report.verified[:5])}")
    if symbol_report.not_found:
        parts.append(f"SYMBOL_CLAIMS_NOT_FOUND: {', '.join(symbol_report.not_found)}")

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
