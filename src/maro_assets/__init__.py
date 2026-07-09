"""Shipped default assets (curated skills + personas) as package data.

The repo keeps `skills/` and `personas/` at the top level for dev ergonomics
(CLAUDE.md, docs, and tests all reference those paths); this package carries
the same files into pip installs, where the repo layout doesn't exist. In the
repo, `skills/` and `personas/` here are symlinks to the top-level dirs —
setuptools follows them at build time, so the wheel ships real copies.

Resolution order stays workspace → repo → this package: loaders fall back
here only when the repo dirs are absent (i.e. an installed environment).
"""

from pathlib import Path
from typing import Optional


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
