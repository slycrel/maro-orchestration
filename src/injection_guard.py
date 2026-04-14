"""Prompt injection hardening for persona and skill ingestion.

Any externally-sourced persona or skill (from YAML files, LLM-generated, or
imported from external repos) is a potential instruction-injection vector.

This module provides:
1. Content scanning for known-bad injection patterns
2. Source allowlisting for auto-apply decisions
3. A clean is_safe_to_apply() API used by evolver and persona system

Usage:
    from injection_guard import scan_content, is_safe_to_apply

    report = scan_content(skill_yaml_text, source="workspace")
    if not report.is_clean:
        log.warning("injection risk: %s", report.findings)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known injection patterns
# ---------------------------------------------------------------------------

# Patterns that look like attempts to override the AI's system instructions
_OVERRIDE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bignore\s+(all\s+)?previous\s+(instructions?|context|above)\b", re.IGNORECASE),
    re.compile(r"\bforget\s+(all\s+)?previous\b", re.IGNORECASE),
    re.compile(r"\bsystem\s*:\s*you\s+are\b", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(all\s+)?previous\b", re.IGNORECASE),
    re.compile(r"\boverride\s+your\s+(instructions?|rules|guidelines)\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+a\b", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+(?:an?\s+)?(?:different|new|unrestricted|jailbroken)\b", re.IGNORECASE),
    re.compile(r"\bDAN\s*mode\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
]

# Tool call injection: raw tool invocation strings outside expected YAML fields
_TOOL_CALL_PATTERNS: List[re.Pattern] = [
    re.compile(r"<tool_use>", re.IGNORECASE),
    re.compile(r'"tool_name"\s*:\s*"', re.IGNORECASE),
    re.compile(r'\btool_call\s*\(', re.IGNORECASE),
    re.compile(r"<function[_\s]call>", re.IGNORECASE),
]

# Exfiltration / redirect patterns
_EXFIL_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bsend\s+(all\s+)?secrets?\s+to\b", re.IGNORECASE),
    re.compile(r"\bexfiltrat[ei]\b", re.IGNORECASE),
    re.compile(r"\bleak\s+(the\s+)?(credentials?|api\s*keys?|tokens?)\b", re.IGNORECASE),
    re.compile(r"\bhttp[s]?://(?!r\.jina\.ai|api\.anthropic\.com)[^\s]{3,50}\.(com|io|net)/[^\s]{5,}", re.IGNORECASE),
]

# Allowed source locations (auto-apply permitted without manual review)
_ALLOWED_SOURCE_DIRS: frozenset = frozenset([
    "skills",        # repo skills/
    "personas",      # repo personas/
    "workspace",     # ~/.poe/workspace/skills/ or personas/
    "builtin",       # shipped default
    "internal",      # programmatic (evolver, graduation)
])


# ---------------------------------------------------------------------------
# Scan result
# ---------------------------------------------------------------------------

@dataclass
class InjectionScanReport:
    """Result of scanning content for injection risk."""
    content_hash: str
    source: str
    is_clean: bool
    findings: List[str] = field(default_factory=list)
    risk_level: str = "low"   # "low" | "medium" | "high"
    blocked_patterns: List[str] = field(default_factory=list)

    @property
    def safe_to_auto_apply(self) -> bool:
        """True only if content is clean AND source is allowlisted."""
        return self.is_clean and _source_is_allowed(self.source)


def _source_is_allowed(source: str) -> bool:
    """Check if a source identifier is in the allowlist.

    Uses exact match for short programmatic tokens and path-component match
    for file paths.  Substring match is intentionally avoided — it allows
    keyword-stuffing attacks like 'github.com/evil/workspace-tools' matching
    the 'workspace' allowlist entry.
    """
    if not source:
        return False
    source_lower = source.lower()
    # Fast path: exact match (covers "internal", "builtin", "skills", etc.)
    if source_lower in _ALLOWED_SOURCE_DIRS:
        return True
    # Path-component match for file paths (e.g. "/path/to/personas/file.yaml")
    # Split on path separators only — "workspace-tools" won't match "workspace"
    try:
        path_parts = set(Path(source_lower).parts)
        return bool(path_parts & _ALLOWED_SOURCE_DIRS)
    except Exception:
        return False


def _truncated_hash(content: str) -> str:
    """Fast 8-char hash for identification (not security)."""
    import hashlib
    return hashlib.sha1(content.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_content(
    content: str,
    *,
    source: str = "",
    max_chars: int = 50_000,
) -> InjectionScanReport:
    """Scan text content for prompt injection risk patterns.

    Args:
        content:   The text to scan (YAML, Markdown, plain text).
        source:    Where this content came from (e.g. "skills", "workspace", "github.com/...").
        max_chars: Truncate at this length before scanning (50K cap prevents DOS).

    Returns:
        InjectionScanReport with is_clean=True if no injection risk found.
    """
    scan_target = content[:max_chars]
    content_hash = _truncated_hash(scan_target)
    findings: List[str] = []
    blocked: List[str] = []

    # Check override patterns
    for pat in _OVERRIDE_PATTERNS:
        m = pat.search(scan_target)
        if m:
            findings.append(f"override attempt: {m.group()[:60]!r}")
            blocked.append(pat.pattern)

    # Check tool call injection
    for pat in _TOOL_CALL_PATTERNS:
        m = pat.search(scan_target)
        if m:
            findings.append(f"tool call injection: {m.group()[:60]!r}")
            blocked.append(pat.pattern)

    # Check exfiltration patterns
    has_exfil = False
    for pat in _EXFIL_PATTERNS:
        m = pat.search(scan_target)
        if m:
            findings.append(f"exfiltration pattern: {m.group()[:60]!r}")
            blocked.append(pat.pattern)
            has_exfil = True

    is_clean = len(findings) == 0
    risk_level = "low"
    if has_exfil or len(findings) >= 3:
        # Any exfiltration pattern = immediately HIGH regardless of count
        risk_level = "high"
    elif len(findings) >= 1:
        risk_level = "medium"

    if not is_clean:
        log.warning(
            "injection_guard: %d finding(s) in %s source=%r hash=%s",
            len(findings), risk_level, source, content_hash,
        )

    return InjectionScanReport(
        content_hash=content_hash,
        source=source,
        is_clean=is_clean,
        findings=findings,
        risk_level=risk_level,
        blocked_patterns=blocked,
    )


def is_safe_to_apply(
    content: str,
    *,
    source: str = "",
    require_allowlisted_source: bool = True,
) -> bool:
    """Convenience check: is this content safe to auto-apply?

    Returns True only when:
    1. No injection patterns detected
    2. Source is allowlisted (if require_allowlisted_source=True)

    Non-fatal — returns False on any error (fail-closed for security).
    """
    try:
        report = scan_content(content, source=source)
        if require_allowlisted_source:
            return report.safe_to_auto_apply
        return report.is_clean
    except Exception as exc:
        log.debug("injection_guard.is_safe_to_apply failed: %s", exc)
        return False  # fail-closed


def scan_skill_yaml(skill_yaml: str, *, source: str = "") -> InjectionScanReport:
    """Convenience wrapper for skill YAML scanning."""
    return scan_content(skill_yaml, source=source)


def scan_persona_yaml(persona_yaml: str, *, source: str = "") -> InjectionScanReport:
    """Convenience wrapper for persona YAML scanning."""
    return scan_content(persona_yaml, source=source)
