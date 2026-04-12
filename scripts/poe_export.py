#!/usr/bin/env python3
"""poe-export / poe-import — workspace backup and restore.

Exports ~/.poe/workspace/ to a timestamped tar.gz, excluding secrets.
Import restores from a tar.gz, merging with existing data.

Usage:
    python3 scripts/poe_export.py export [--output PATH]
    python3 scripts/poe_export.py import ARCHIVE_PATH [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# Patterns to exclude from export (relative to workspace root)
_EXCLUDE_PATTERNS = {
    "secrets",          # API keys, tokens
    "secrets/",
    ".env",
    "*.key",
    "*.pem",
    "telegram_offset.txt",  # Ephemeral state
    "prototypes",       # Legacy, large, not needed for restore
    "prototypes/",
    "logs/",            # Transient logs
}


def _should_exclude(path: str) -> bool:
    """Check if a path should be excluded from export."""
    parts = Path(path).parts
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.endswith("/"):
            # Directory prefix match
            dirname = pattern.rstrip("/")
            if dirname in parts:
                return True
        elif "*" in pattern:
            # Glob-style match on filename
            import fnmatch
            if fnmatch.fnmatch(Path(path).name, pattern):
                return True
        else:
            # Exact component match
            if pattern in parts or Path(path).name == pattern:
                return True
    return False


def export_workspace(output_path: Path = None, verbose: bool = False) -> Path:
    """Export workspace to a tar.gz archive.

    Args:
        output_path: Where to write the archive. Default: ~/poe-export-TIMESTAMP.tar.gz
        verbose: Print files being added.

    Returns:
        Path to the created archive.
    """
    from config import workspace_root
    ws = workspace_root()

    if not ws.exists():
        print(f"Error: workspace not found at {ws}", file=sys.stderr)
        sys.exit(1)

    if output_path is None:
        timestamp = time.strftime("%Y%m%dT%H%M%S")
        output_path = Path.home() / f"poe-export-{timestamp}.tar.gz"

    file_count = 0
    total_bytes = 0

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal file_count, total_bytes
        # Get path relative to workspace
        rel = tarinfo.name
        if _should_exclude(rel):
            if verbose:
                print(f"  skip: {rel}", file=sys.stderr)
            return None
        file_count += 1
        total_bytes += tarinfo.size
        if verbose:
            print(f"  add:  {rel} ({tarinfo.size:,} bytes)", file=sys.stderr)
        return tarinfo

    print(f"Exporting {ws} → {output_path}", file=sys.stderr)

    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(str(ws), arcname="workspace", filter=_filter)

    archive_size = output_path.stat().st_size
    print(
        f"Done: {file_count} files, {total_bytes:,} bytes → {archive_size:,} bytes compressed",
        file=sys.stderr,
    )
    print(str(output_path))
    return output_path


def import_workspace(
    archive_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Import (restore) workspace from a tar.gz archive.

    Extracts into ~/.poe/workspace/, creating directories as needed.
    Existing files are overwritten. This is a merge, not a clean restore —
    files not in the archive are left untouched.

    Args:
        archive_path: Path to the .tar.gz archive.
        dry_run: List contents without extracting.
        verbose: Print files being extracted.

    Returns:
        Number of files extracted.
    """
    from config import workspace_root
    ws = workspace_root()

    if not archive_path.exists():
        print(f"Error: archive not found: {archive_path}", file=sys.stderr)
        sys.exit(1)

    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()

        if dry_run:
            print(f"Archive contains {len(members)} entries:", file=sys.stderr)
            for m in members:
                print(f"  {m.name} ({m.size:,} bytes)")
            return 0

        print(f"Importing {archive_path} → {ws}", file=sys.stderr)
        ws.mkdir(parents=True, exist_ok=True)

        extracted = 0
        for member in members:
            # Strip the "workspace/" prefix and extract relative to ws
            if member.name.startswith("workspace/"):
                member.name = member.name[len("workspace/"):]
            elif member.name == "workspace":
                continue  # Skip the root directory entry

            # Security: prevent path traversal
            dest = (ws / member.name).resolve()
            if not str(dest).startswith(str(ws.resolve())):
                print(f"  SKIP (path traversal): {member.name}", file=sys.stderr)
                continue

            if _should_exclude(member.name):
                if verbose:
                    print(f"  skip: {member.name}", file=sys.stderr)
                continue

            if verbose:
                print(f"  extract: {member.name}", file=sys.stderr)
            tar.extract(member, path=str(ws))
            extracted += 1

        print(f"Done: {extracted} files extracted to {ws}", file=sys.stderr)
        return extracted


def main():
    parser = argparse.ArgumentParser(
        prog="poe-export",
        description="Export/import Poe workspace for backup or machine transfer",
    )
    sub = parser.add_subparsers(dest="command")

    exp = sub.add_parser("export", help="Export workspace to tar.gz")
    exp.add_argument("--output", "-o", type=Path, help="Output archive path")
    exp.add_argument("--verbose", "-v", action="store_true")

    imp = sub.add_parser("import", help="Import workspace from tar.gz")
    imp.add_argument("archive", type=Path, help="Archive to import")
    imp.add_argument("--dry-run", action="store_true", help="List contents only")
    imp.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.command == "export":
        export_workspace(output_path=args.output, verbose=args.verbose)
    elif args.command == "import":
        import_workspace(args.archive, dry_run=args.dry_run, verbose=args.verbose)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
