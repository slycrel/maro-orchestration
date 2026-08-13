#!/usr/bin/env python3
"""maro-export / maro-import — workspace backup and restore.

Exports ~/.maro/workspace/ to a timestamped tar.gz, excluding secrets.
Import restores from a tar.gz into workspace_root() (MARO_WORKSPACE
overrides the destination). By default import MERGES: archive files
overwrite, existing files not in the archive remain; --clean moves the
existing workspace aside first (never deletes it).

Usage:
    python3 scripts/maro_export.py export [--output PATH]
    python3 scripts/maro_export.py import ARCHIVE_PATH [--dry-run] [--clean]

Known non-transfers (live-tested box→M1 2026-08-12, see the BACKLOG
"Workspace export/import" entry): user-tier ~/.maro/config.yml and
~/.maro/experiments/ live outside the workspace, and machine semantics
embedded in the data (absolute paths, symlink targets, CLI session ids)
ride along but do not resolve on another host.
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# Two exclusion classes, with deliberately different reach (2026-08-12
# cleanup — the old single anywhere-matched set ate .git/logs/* reflogs
# inside archived project repos):
#   - secret-shaped patterns match ANYWHERE: over-exclusion errs safe.
#   - bulk/transient patterns anchor to the WORKSPACE ROOT: data under
#     runs/ or projects/ that happens to contain a "logs" or
#     "prototypes" component is workspace data — retention wins.
_EXCLUDE_ANYWHERE = {
    "secrets",          # API keys, tokens (dir or file, any depth)
    ".env",
    "*.key",
    "*.pem",
}
_EXCLUDE_ROOT = {
    "telegram_offset.txt",  # Ephemeral state
    "prototypes",       # Legacy, large, not needed for restore
    "logs",             # Transient logs (workspace-root only)
}


def _rel_parts(path: str) -> tuple:
    """Path components relative to the workspace root.

    Export passes tar arcnames ("workspace/memory/..."); import passes
    already-stripped names ("memory/..."). Normalize to the stripped
    shape so root-anchored patterns mean the same thing on both sides.
    """
    parts = Path(path).parts
    if parts and parts[0] == "workspace":
        parts = parts[1:]
    return parts


def _should_exclude(path: str) -> bool:
    """Check if a path should be excluded from export/import."""
    import fnmatch
    parts = _rel_parts(path)
    if not parts:
        return False
    name = parts[-1]
    for pattern in _EXCLUDE_ANYWHERE:
        if "*" in pattern:
            if fnmatch.fnmatch(name, pattern):
                return True
        elif pattern in parts:
            return True
    return parts[0] in _EXCLUDE_ROOT


def _snapshot_sqlite(src: Path, dst: Path) -> bool:
    """Copy a sqlite database consistently via the backup API.

    The workspace is exported hot (runs may be mid-write); a raw byte
    copy of a live sqlite file can be torn. Returns False when src is
    not a readable sqlite database — the caller falls back to raw bytes
    rather than dropping the file.

    Path goes in as a percent-encoded file: URI (via as_uri) — naive
    f-string interpolation let a '?' or '#' in the filename truncate the
    URI, silently opening a DIFFERENT (empty) database and "succeeding"
    with an empty snapshot (3-lens review of 707a541, Skeptic HIGH,
    reproduced). Belt-and-suspenders, success also requires page-count
    parity between source and snapshot — kills that whole silent-empty
    class even if some other URI quirk slips through.
    """
    import sqlite3
    con = out = None
    try:
        con = sqlite3.connect(
            f"{src.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        out = sqlite3.connect(str(dst))
        con.backup(out)
        src_pages = con.execute("PRAGMA page_count").fetchone()[0]
        dst_pages = out.execute("PRAGMA page_count").fetchone()[0]
        if src_pages != dst_pages or (src.stat().st_size > 0
                                      and dst_pages == 0):
            raise RuntimeError(
                f"snapshot page-count mismatch: src={src_pages} "
                f"dst={dst_pages}")
        return True
    except Exception:
        try:
            dst.unlink()
        except OSError:
            pass
        return False
    finally:
        for c in (con, out):
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass


def export_workspace(output_path: Path = None, verbose: bool = False) -> Path:
    """Export workspace to a tar.gz archive.

    Args:
        output_path: Where to write the archive. Default: ~/maro-export-TIMESTAMP.tar.gz
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
        output_path = Path.home() / f"maro-export-{timestamp}.tar.gz"

    file_count = 0
    other_count = 0  # directories + symlinks (counted honestly, apart)
    total_bytes = 0
    # sqlite family deferred to the snapshot pass: (arcname, abs path)
    db_files: list[tuple[str, Path]] = []
    sidecars: list[tuple[str, Path]] = []  # -wal/-shm, keyed by base later

    def _abs_for(rel: str) -> Path:
        return ws / Path(*_rel_parts(rel))

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal file_count, other_count, total_bytes
        rel = tarinfo.name
        if _should_exclude(rel):
            if verbose:
                print(f"  skip: {rel}", file=sys.stderr)
            return None
        name = Path(rel).name
        if tarinfo.isfile() and name.endswith((".db", ".db-wal", ".db-shm")):
            # Hot sqlite files are torn-copy hazards — handled below.
            if name.endswith(".db"):
                db_files.append((rel, _abs_for(rel)))
            else:
                sidecars.append((rel, _abs_for(rel)))
            return None
        if tarinfo.isfile():
            file_count += 1
            total_bytes += tarinfo.size
        else:
            other_count += 1
        if verbose:
            print(f"  add:  {rel} ({tarinfo.size:,} bytes)", file=sys.stderr)
        return tarinfo

    print(f"Exporting {ws} → {output_path}", file=sys.stderr)

    import tempfile
    with tarfile.open(output_path, "w:gz") as tar, \
            tempfile.TemporaryDirectory(prefix="maro-export-db-") as tmpd:
        tar.add(str(ws), arcname="workspace", filter=_filter)

        # Snapshot pass: each .db goes in via the sqlite backup API so the
        # archived copy is consistent even mid-run. On success its -wal/-shm
        # sidecars are folded into the snapshot and must NOT ship alongside
        # (a fresh snapshot next to a stale wal corrupts on open). On
        # failure (not sqlite / unreadable) fall back to raw bytes, WITH
        # sidecars — a raw db+wal pair is at least recoverable.
        snapshotted_bases: set = set()
        for i, (rel, src) in enumerate(db_files):
            snap = Path(tmpd) / f"snap-{i}.db"
            if _snapshot_sqlite(src, snap):
                # Carry the SOURCE file's metadata (mode/mtime/owner) with
                # the snapshot's bytes — tar.add(snap) would stamp the temp
                # file's 0644/now, silently broadening a 0600 database on
                # restore (review of 707a541, reproduced).
                ti = tar.gettarinfo(str(src), arcname=rel)
                ti.size = snap.stat().st_size
                with open(snap, "rb") as fh:
                    tar.addfile(ti, fh)
                snapshotted_bases.add(rel)
                file_count += 1
                total_bytes += snap.stat().st_size
                if verbose:
                    print(f"  add:  {rel} (sqlite snapshot)", file=sys.stderr)
            else:
                tar.add(str(src), arcname=rel, recursive=False)
                file_count += 1
                total_bytes += src.stat().st_size
                if verbose:
                    print(f"  add:  {rel} (raw — not a readable sqlite db)",
                          file=sys.stderr)
        for rel, src in sidecars:
            base = rel[: rel.rfind("-")]  # strip -wal/-shm
            if base in snapshotted_bases:
                if verbose:
                    print(f"  skip: {rel} (folded into snapshot)",
                          file=sys.stderr)
                continue
            tar.add(str(src), arcname=rel, recursive=False)
            file_count += 1
            total_bytes += src.stat().st_size

    archive_size = output_path.stat().st_size
    print(
        f"Done: {file_count} files (+{other_count} dirs/links), "
        f"{total_bytes:,} bytes → {archive_size:,} bytes compressed",
        file=sys.stderr,
    )
    print(str(output_path))
    return output_path


def import_workspace(
    archive_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    clean: bool = False,
) -> int:
    """Import (restore) workspace from a tar.gz archive.

    Extracts into workspace_root(), creating directories as needed.
    Existing files are overwritten. By default this is a MERGE, not a
    clean restore — files not in the archive are left untouched, and a
    non-empty destination gets a loud warning. clean=True moves the
    existing workspace aside to <ws>.pre-import-<timestamp> first
    (retention decree: moved, never deleted).

    Args:
        archive_path: Path to the .tar.gz archive.
        dry_run: List contents without extracting.
        verbose: Print files being extracted.
        clean: Move an existing non-empty workspace aside before import.

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

        if ws.exists() and any(ws.iterdir()):
            if clean:
                # Unique aside name: second-resolution timestamps collide on
                # back-to-back clean imports (review of 707a541, reproduced
                # — rename onto an existing non-empty dir raises).
                base = ws.name + time.strftime(".pre-import-%Y%m%dT%H%M%S")
                aside = ws.with_name(base)
                n = 1
                while aside.exists():
                    n += 1
                    aside = ws.with_name(f"{base}-{n}")
                ws.rename(aside)
                # Not failure-atomic by design: if extraction dies midway,
                # the fresh workspace is partial but the aside still holds
                # the complete pre-import state — recovery is renaming it
                # back. Nothing is ever deleted.
                print(f"--clean: existing workspace moved aside to {aside} "
                      f"(nothing deleted; rename back to recover)",
                      file=sys.stderr)
            else:
                print(
                    "WARNING: importing into a non-empty workspace — this "
                    "MERGES: archive files overwrite, existing files not in "
                    "the archive remain (an unmarked hybrid). Pass --clean "
                    "to move the existing workspace aside first.",
                    file=sys.stderr,
                )

        print(f"Importing {archive_path} → {ws}", file=sys.stderr)
        ws.mkdir(parents=True, exist_ok=True)

        extracted = 0
        other = 0  # directories + symlinks, reported apart (honest counts)
        for member in members:
            # Strip the "workspace/" prefix and extract relative to ws
            if member.name.startswith("workspace/"):
                member.name = member.name[len("workspace/"):]
            elif member.name == "workspace":
                continue  # Skip the root directory entry

            # Security: prevent path traversal. relative_to, not a string
            # prefix — "/x/workspace-evil" startswith "/x/workspace", so the
            # old check passed sibling escapes; combined with the pre-filter
            # extract fallback below that was a real out-of-workspace write
            # on Python 3.10 (3-lens review of 707a541, consensus HIGH,
            # reproduced by all three reviewers).
            dest = (ws / member.name).resolve()
            try:
                dest.relative_to(ws.resolve())
            except ValueError:
                print(f"  SKIP (path traversal): {member.name}", file=sys.stderr)
                continue

            if _should_exclude(member.name):
                if verbose:
                    print(f"  skip: {member.name}", file=sys.stderr)
                continue

            if verbose:
                print(f"  extract: {member.name}", file=sys.stderr)
            try:
                # 'tar' filter: strips absolute paths / '..' escapes and
                # silences the Python 3.14 unfiltered-extract deprecation,
                # while still permitting symlink members (their targets are
                # machine semantics — dangling off-box is expected and
                # documented). The manual traversal guard above stays and is
                # the ONLY guard on pythons without the filter kwarg
                # (<3.11.4) — which is why it must be relative_to-sound.
                tar.extract(member, path=str(ws), filter="tar")
            except TypeError:  # Python without the filter kwarg (<3.11.4)
                tar.extract(member, path=str(ws))
            if member.isfile():
                extracted += 1
            else:
                other += 1

        print(f"Done: {extracted} files (+{other} dirs/links) extracted "
              f"to {ws}", file=sys.stderr)
        return extracted


def main():
    parser = argparse.ArgumentParser(
        prog="maro-export",
        description="Export/import Maro workspace for backup or machine transfer",
    )
    sub = parser.add_subparsers(dest="command")

    exp = sub.add_parser("export", help="Export workspace to tar.gz")
    exp.add_argument("--output", "-o", type=Path, help="Output archive path")
    exp.add_argument("--verbose", "-v", action="store_true")

    imp = sub.add_parser("import", help="Import workspace from tar.gz")
    imp.add_argument("archive", type=Path, help="Archive to import")
    imp.add_argument("--dry-run", action="store_true", help="List contents only")
    imp.add_argument("--clean", action="store_true",
                     help="Move an existing non-empty workspace aside "
                          "(never deleted) instead of merging into it")
    imp.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.command == "export":
        export_workspace(output_path=args.output, verbose=args.verbose)
    elif args.command == "import":
        import_workspace(args.archive, dry_run=args.dry_run,
                         verbose=args.verbose, clean=args.clean)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
