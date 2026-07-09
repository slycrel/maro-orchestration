"""Shipped default assets (curated skills + personas) as package data.

The repo keeps `skills/` and `personas/` at the top level for dev ergonomics
(CLAUDE.md, docs, and tests all reference those paths); this package carries
the curated subset into pip installs, where the repo layout doesn't exist.
In the repo, `skills/` and `personas/` here are directories of per-file
symlinks to the top-level files — setuptools follows them at build time, so
the wheel ships real copies of exactly the ship set and nothing else.

SHIPPED below is the canonical catalog manifest (2026-07-09 survey,
docs/audit-2026-07/persona-skill-survey.md §3). Box-specific personas
(jeremy, poe, companion, garrytan, psyche-researcher) and test-fixture
skills stay in the repo but out of the wheel; the workspace override layer
is where box-specific specs belong. tests/test_packaging.py enforces that
the symlink dirs match this manifest.

Resolution order stays workspace → repo → this package: loaders fall back
here only when the repo dirs are absent (i.e. an installed environment).
"""

from pathlib import Path
from typing import Optional

SHIPPED = {
    # 9 catalog personas + 4 infrastructure personas (director-proxy,
    # loop-validator, plan-critic, reality-checker-evidence-gate)
    "personas": [
        "assistant",
        "builder",
        "creative-director",
        "critic",
        "data-analyst",
        "director-proxy",
        "loop-validator",
        "ops",
        "plan-critic",
        "reality-checker-evidence-gate",
        "reporter",
        "research-assistant-deep-synth",
        "scrapling-adaptive-web-recon",
    ],
    "skills": [
        "code_implement",
        "code_review",
        "compact_notation",
        "data_analysis",
        "debug_investigate",
        "deep_research",
        "document_process",
        "monitor_diagnose",
        "report_synthesize",
        "resolve_ambiguity",
        "web_extract",
        "web_research",
    ],
}


def assets_dir(kind: str) -> Optional[Path]:
    """Return the packaged directory for `kind` ("skills" | "personas").

    None if the resource isn't available (source checkout without the
    symlinks, or a broken install) — callers treat that as "no fallback".
    """
    try:
        from importlib.resources import files
        p = Path(str(files("maro_assets") / kind))
        return p if p.is_dir() else None
    except Exception:
        return None
