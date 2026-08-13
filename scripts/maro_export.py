#!/usr/bin/env python3
"""maro-export / maro-import — Maro data transfer (workspace + metadata).

Archive format v2 (2026-08-13, Jeremy: "the behavior IS data … intent has
always been data sharing, and that's always meant all of our metadata"):

  workspace/**          the workspace, as before (secrets excluded,
                        sqlite via consistent snapshots)
  meta/user-config.yml  user-tier ~/.maro/config.yml — behavior config
                        (model prefs, scope_generation, …), credential-
                        shaped values redacted at export
  meta/experiments/**   ~/.maro/experiments/
  meta/symlinks.json    external symlinks (absolute or escaping the
                        workspace) are recorded HERE instead of shipped —
                        a link to /usr/bin/python3 resolves to the WRONG
                        binary on another OS rather than dangling, so
                        machine-pointing links travel as data, not links.
                        Internal relative links still travel as links.
  meta/provenance.json  who exported, from where, with what tool, content
                        digest, and a custody chain that every import
                        appends to. First brick of the future security /
                        sharing layer — an integrity hint, NOT tamper
                        proof (no signing yet).

Import extracts workspace/** into workspace_root() (MARO_WORKSPACE
overrides) and stages meta/** NON-DESTRUCTIVELY under
<workspace>/.import-meta/ — it never silently changes the importing
machine's behavior. --apply-meta places user-config.yml at the real
user-tier path (existing config backed up, never deleted). By default
import MERGES; --clean moves the existing workspace aside first (never
deletes it).

Back-compat: v1 archives (no meta/) import cleanly; a v1 importer given
a v2 archive extracts meta/ as a plain directory inside the workspace —
untidy but harmless.

Usage:
    python3 scripts/maro_export.py export [--output PATH]
    python3 scripts/maro_export.py import ARCHIVE [--dry-run] [--clean]
                                          [--apply-meta]

Still not carried (inherent): machine semantics embedded in the data —
absolute paths inside artifacts/checkpoints, CLI session ids — and file
ownership maps to the importing user. provenance.json records the source
workspace root so a future consumer can rewrite embedded paths.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ARCHIVE_FORMAT = 2
_META_PREFIX = "meta/"
_STAGING_DIRNAME = ".import-meta"

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
    _STAGING_DIRNAME,   # a re-export must not re-ship staged import meta
}

# Config keys whose VALUES get redacted when the user config rides the
# archive. Config.yml should hold no credentials by convention (they live
# in env / secrets/.env) — this is enforcement for the sharing use case,
# not a substitute for the convention.
_REDACT_KEY_TOKENS = (
    "token", "secret", "password", "api_key", "apikey", "bearer",
    "credential",
)


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


def _secret_shaped(parts: tuple) -> bool:
    """The ANYWHERE exclusion class — used for workspace and meta alike."""
    import fnmatch
    if not parts:
        return False
    name = parts[-1]
    for pattern in _EXCLUDE_ANYWHERE:
        if "*" in pattern:
            if fnmatch.fnmatch(name, pattern):
                return True
        elif pattern in parts:
            return True
    return False


def _should_exclude(path: str) -> bool:
    """Check if a workspace path should be excluded from export/import."""
    parts = _rel_parts(path)
    if not parts:
        return False
    if _secret_shaped(parts):
        return True
    return parts[0] in _EXCLUDE_ROOT


def _redact_config_text(text: str) -> tuple[str, int]:
    """Redact values of credential-shaped keys in YAML-ish config text.

    Line-based on purpose: preserves comments/layout and never fails on
    odd YAML. Returns (redacted_text, redaction_count).
    """
    out_lines = []
    redacted = 0
    for line in text.splitlines(keepends=True):
        stripped = line.split("#", 1)[0]
        if ":" in stripped:
            key = stripped.split(":", 1)[0].strip().lower()
            value = stripped.split(":", 1)[1].strip()
            if value and any(t in key for t in _REDACT_KEY_TOKENS):
                indent = line[: len(line) - len(line.lstrip())]
                raw_key = stripped.split(":", 1)[0].strip()
                out_lines.append(
                    f"{indent}{raw_key}: REDACTED-BY-EXPORT\n")
                redacted += 1
                continue
        out_lines.append(line)
    return "".join(out_lines), redacted


def _manifest_digest(entries) -> str:
    """sha256 over sorted 'name<TAB>size' lines of workspace file members.

    An integrity HINT for provenance (detects accidental corruption and
    casual tampering with names/sizes) — not content-proof and not
    signed. The future security layer strengthens this; provenance says
    so explicitly.
    """
    lines = sorted(f"{name}\t{size}" for name, size in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _identity() -> str:
    import getpass
    import socket
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return f"{user}@{host}"


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes,
               mode: int = 0o644) -> None:
    ti = tarfile.TarInfo(name=arcname)
    ti.size = len(data)
    ti.mode = mode
    ti.mtime = int(time.time())
    tar.addfile(ti, io.BytesIO(data))


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
    """Export workspace + user-tier metadata to a tar.gz archive.

    Args:
        output_path: Where to write the archive. Default: ~/maro-export-TIMESTAMP.tar.gz
        verbose: Print files being added.

    Returns:
        Path to the created archive.
    """
    from config import _maro_dir, _user_config_path, workspace_root
    ws = workspace_root()

    if not ws.exists():
        print(f"Error: workspace not found at {ws}", file=sys.stderr)
        sys.exit(1)

    if output_path is None:
        timestamp = time.strftime("%Y%m%dT%H%M%S")
        output_path = Path.home() / f"maro-export-{timestamp}.tar.gz"

    file_count = 0
    other_count = 0  # directories + internal symlinks (counted apart)
    meta_count = 0
    total_bytes = 0
    # (stripped rel name, size) for every workspace file member — feeds
    # the provenance manifest digest.
    manifest_entries: list[tuple[str, int]] = []
    # sqlite family deferred to the snapshot pass: (arcname, abs path)
    db_files: list[tuple[str, Path]] = []
    sidecars: list[tuple[str, Path]] = []  # -wal/-shm, keyed by base later
    # Machine-pointing symlinks travel as data (meta/symlinks.json), not
    # as links (Jeremy 2026-08-13: config-driven or at least
    # non-destructive — portability over verisimilitude).
    external_symlinks: list[dict] = []

    def _abs_for(rel: str) -> Path:
        return ws / Path(*_rel_parts(rel))

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal file_count, other_count, total_bytes
        rel = tarinfo.name
        if _should_exclude(rel):
            if verbose:
                print(f"  skip: {rel}", file=sys.stderr)
            return None
        if tarinfo.issym():
            parts = _rel_parts(rel)
            target = tarinfo.linkname
            portable = False
            if not os.path.isabs(target):
                try:
                    resolved = (ws / Path(*parts[:-1]) / target).resolve()
                    resolved.relative_to(ws.resolve())
                    portable = True
                except (ValueError, OSError):
                    portable = False
            if portable:
                other_count += 1
                return tarinfo  # relative link staying inside the tree
            external_symlinks.append(
                {"path": str(Path(*parts)), "target": target})
            if verbose:
                print(f"  meta: {rel} → symlinks.json (external target "
                      f"{target})", file=sys.stderr)
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
            manifest_entries.append(
                (str(Path(*_rel_parts(rel))), tarinfo.size))
        else:
            other_count += 1
        if verbose:
            print(f"  add:  {rel} ({tarinfo.size:,} bytes)", file=sys.stderr)
        return tarinfo

    print(f"Exporting {ws} → {output_path}", file=sys.stderr)

    import tempfile
    user_config_present = False
    user_config_redactions = 0
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
                manifest_entries.append(
                    (str(Path(*_rel_parts(rel))), snap.stat().st_size))
                if verbose:
                    print(f"  add:  {rel} (sqlite snapshot)", file=sys.stderr)
            else:
                tar.add(str(src), arcname=rel, recursive=False)
                file_count += 1
                total_bytes += src.stat().st_size
                manifest_entries.append(
                    (str(Path(*_rel_parts(rel))), src.stat().st_size))
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
            manifest_entries.append(
                (str(Path(*_rel_parts(rel))), src.stat().st_size))

        # --- meta/ area: behavior IS data -------------------------------
        cfg_path = _user_config_path()
        if cfg_path.exists():
            try:
                text = cfg_path.read_text()
                text, user_config_redactions = _redact_config_text(text)
                _add_bytes(tar, _META_PREFIX + "user-config.yml",
                           text.encode("utf-8"), mode=0o600)
                user_config_present = True
                meta_count += 1
                if user_config_redactions and verbose:
                    print(f"  meta: user-config.yml "
                          f"({user_config_redactions} value(s) redacted)",
                          file=sys.stderr)
            except Exception as exc:
                print(f"  WARN: user config unreadable, not exported: {exc}",
                      file=sys.stderr)

        exp_dir = _maro_dir() / "experiments"
        exp_files = 0
        if exp_dir.is_dir():
            for p in sorted(exp_dir.rglob("*")):
                if not p.is_file() or p.is_symlink():
                    continue
                rel = p.relative_to(exp_dir)
                if _secret_shaped(("experiments",) + rel.parts):
                    if verbose:
                        print(f"  skip: experiments/{rel} (secret-shaped)",
                              file=sys.stderr)
                    continue
                tar.add(str(p),
                        arcname=_META_PREFIX + "experiments/" + str(rel),
                        recursive=False)
                exp_files += 1
                meta_count += 1

        if external_symlinks:
            _add_bytes(
                tar, _META_PREFIX + "symlinks.json",
                json.dumps({"format": ARCHIVE_FORMAT,
                            "note": ("symlinks whose targets are absolute "
                                     "or escape the workspace — recorded, "
                                     "not shipped; recreate by hand on a "
                                     "matching host if ever needed"),
                            "links": external_symlinks},
                           indent=2).encode("utf-8"))
            meta_count += 1

        try:
            script_sha = hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest()[:16]
        except Exception:
            script_sha = "unknown"
        provenance = {
            "format": ARCHIVE_FORMAT,
            "created_at": _utcnow(),
            "exporter": _identity(),
            "source": {
                "workspace_root": str(ws),
                "maro_user_dir": str(_maro_dir()),
            },
            "tool": {"name": "maro-export", "format_version": ARCHIVE_FORMAT,
                     "script_sha256": script_sha},
            "contents": {
                "files": file_count,
                "bytes": total_bytes,
                "manifest_sha256": _manifest_digest(manifest_entries),
                "manifest_note": ("sha256 over sorted 'name\\tsize' lines "
                                  "of workspace file members — integrity "
                                  "hint, not tamper-proof (no signing yet)"),
            },
            "meta": {
                "user_config": user_config_present,
                "user_config_redactions": user_config_redactions,
                "experiments_files": exp_files,
                "external_symlinks": len(external_symlinks),
            },
            # Chain of custody: every import appends an event. The point
            # (Jeremy 2026-08-13): when pieces of functionality get shared,
            # know where they came from and who shared them — groundwork
            # for the injection-guard security layer, not the layer itself.
            "custody": [
                {"event": "export", "at": _utcnow(), "by": _identity()},
            ],
        }
        _add_bytes(tar, _META_PREFIX + "provenance.json",
                   json.dumps(provenance, indent=2).encode("utf-8"))
        meta_count += 1

    archive_size = output_path.stat().st_size
    print(
        f"Done: {file_count} files (+{other_count} dirs/links, "
        f"{meta_count} meta), "
        f"{total_bytes:,} bytes → {archive_size:,} bytes compressed",
        file=sys.stderr,
    )
    print(str(output_path))
    return output_path


def _stage_meta(tar: tarfile.TarFile, meta_members, staging: Path,
                verbose: bool) -> int:
    """Extract meta/** members into the staging dir. Returns files staged."""
    staged = 0
    for member in meta_members:
        rel = member.name[len(_META_PREFIX):]
        if not rel:
            continue
        dest = (staging / rel).resolve()
        try:
            dest.relative_to(staging.resolve())
        except ValueError:
            print(f"  SKIP (path traversal in meta): {member.name}",
                  file=sys.stderr)
            continue
        if member.isdir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            continue  # no links or specials in the meta area
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = tar.extractfile(member)
        if src is None:
            continue
        dest.write_bytes(src.read())
        try:
            dest.chmod(member.mode & 0o777)
        except OSError:
            pass
        staged += 1
        if verbose:
            print(f"  stage: {member.name} → {dest}", file=sys.stderr)
    return staged


def import_workspace(
    archive_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    clean: bool = False,
    apply_meta: bool = False,
) -> int:
    """Import (restore) a Maro export archive.

    workspace/** extracts into workspace_root(), creating directories as
    needed. meta/** (v2 archives: user config, experiments, symlink
    record, provenance) stages NON-DESTRUCTIVELY under
    <workspace>/.import-meta/ — importing never silently changes this
    machine's behavior. apply_meta=True additionally places
    user-config.yml at the real user-tier path, backing up any existing
    config (never deleted); experiments stay staged either way, with a
    printed pointer.

    By default this is a MERGE, not a clean restore — files not in the
    archive are left untouched, and a non-empty destination gets a loud
    warning. clean=True moves the existing workspace aside to
    <ws>.pre-import-<timestamp> first (retention decree: moved, never
    deleted).

    The archive's provenance (if present) is printed, its manifest
    digest verified, and an import event appended to the staged custody
    chain.

    Returns:
        Number of workspace files extracted.
    """
    from config import _user_config_path, workspace_root
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

        meta_members = [m for m in members
                        if m.name == "meta" or m.name.startswith(_META_PREFIX)]
        ws_members = [m for m in members if m not in meta_members]

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
        manifest_entries: list[tuple[str, int]] = []
        for member in ws_members:
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
                # while still permitting symlink members (v2 archives only
                # carry INTERNAL relative links; external ones ride
                # meta/symlinks.json). The manual traversal guard above
                # stays and is the ONLY guard on pythons without the filter
                # kwarg (<3.11.4) — which is why it must be
                # relative_to-sound.
                tar.extract(member, path=str(ws), filter="tar")
            except TypeError:  # Python without the filter kwarg (<3.11.4)
                tar.extract(member, path=str(ws))
            if member.isfile():
                extracted += 1
                manifest_entries.append((member.name, member.size))
            else:
                other += 1

        staged = 0
        if meta_members:
            staging = ws / _STAGING_DIRNAME
            staging.mkdir(parents=True, exist_ok=True)
            staged = _stage_meta(tar, meta_members, staging, verbose)

            prov_path = staging / "provenance.json"
            if prov_path.exists():
                try:
                    prov = json.loads(prov_path.read_text())
                except Exception as exc:
                    prov = None
                    print(f"WARNING: provenance.json unreadable: {exc}",
                          file=sys.stderr)
                if prov:
                    claimed = (prov.get("contents") or {}).get(
                        "manifest_sha256", "")
                    actual = _manifest_digest(manifest_entries)
                    digest_ok = bool(claimed) and claimed == actual
                    meta_info = prov.get("meta") or {}
                    print("PROVENANCE:", file=sys.stderr)
                    print(f"  exported by {prov.get('exporter', '?')} at "
                          f"{prov.get('created_at', '?')} from "
                          f"{(prov.get('source') or {}).get('workspace_root', '?')}",
                          file=sys.stderr)
                    print(f"  contents: {extracted} files — manifest digest "
                          f"{'OK' if digest_ok else 'MISMATCH'}",
                          file=sys.stderr)
                    if not digest_ok:
                        print("  WARNING: archive contents do not match the "
                              "exporter's manifest — corrupted, tampered, "
                              "or a partial extract. Treat with suspicion.",
                              file=sys.stderr)
                    print(f"  meta: user-config="
                          f"{meta_info.get('user_config', False)} "
                          f"(redactions="
                          f"{meta_info.get('user_config_redactions', 0)}), "
                          f"experiments={meta_info.get('experiments_files', 0)}, "
                          f"external-symlinks="
                          f"{meta_info.get('external_symlinks', 0)}",
                          file=sys.stderr)
                    for ev in prov.get("custody", []):
                        print(f"  custody: {ev.get('event', '?')} by "
                              f"{ev.get('by', '?')} at {ev.get('at', '?')}",
                              file=sys.stderr)
                    prov.setdefault("custody", []).append({
                        "event": "import",
                        "at": _utcnow(),
                        "by": _identity(),
                        "dest": str(ws),
                        "manifest_verified": digest_ok,
                    })
                    prov_path.write_text(json.dumps(prov, indent=2))
            else:
                print("note: archive carries meta/ but no provenance.json",
                      file=sys.stderr)

            staged_cfg = staging / "user-config.yml"
            if staged_cfg.exists():
                if apply_meta:
                    real_cfg = _user_config_path()
                    real_cfg.parent.mkdir(parents=True, exist_ok=True)
                    if real_cfg.exists():
                        base = (real_cfg.name
                                + time.strftime(".pre-import-%Y%m%dT%H%M%S"))
                        backup = real_cfg.with_name(base)
                        n = 1
                        while backup.exists():
                            n += 1
                            backup = real_cfg.with_name(f"{base}-{n}")
                        real_cfg.rename(backup)
                        print(f"--apply-meta: existing user config backed up "
                              f"to {backup}", file=sys.stderr)
                    real_cfg.write_bytes(staged_cfg.read_bytes())
                    try:
                        real_cfg.chmod(0o600)
                    except OSError:
                        pass
                    print(f"--apply-meta: user config applied to {real_cfg}",
                          file=sys.stderr)
                else:
                    print(f"meta staged (behavior not applied): user config "
                          f"at {staged_cfg} — re-run with --apply-meta or "
                          f"place it manually", file=sys.stderr)
            if (staging / "experiments").is_dir():
                print(f"meta staged: experiments at {staging / 'experiments'}"
                      f" — move into ~/.maro/experiments/ if wanted",
                      file=sys.stderr)
        else:
            print("note: v1 archive (no meta/) — no provenance record",
                  file=sys.stderr)

        print(f"Done: {extracted} files (+{other} dirs/links, "
              f"{staged} meta staged) extracted to {ws}", file=sys.stderr)
        return extracted


def main():
    parser = argparse.ArgumentParser(
        prog="maro-export",
        description="Export/import Maro data (workspace + metadata) for "
                    "backup or machine transfer",
    )
    sub = parser.add_subparsers(dest="command")

    exp = sub.add_parser("export", help="Export workspace + meta to tar.gz")
    exp.add_argument("--output", "-o", type=Path, help="Output archive path")
    exp.add_argument("--verbose", "-v", action="store_true")

    imp = sub.add_parser("import", help="Import archive")
    imp.add_argument("archive", type=Path, help="Archive to import")
    imp.add_argument("--dry-run", action="store_true", help="List contents only")
    imp.add_argument("--clean", action="store_true",
                     help="Move an existing non-empty workspace aside "
                          "(never deleted) instead of merging into it")
    imp.add_argument("--apply-meta", action="store_true",
                     help="Also place the archive's user config at the real "
                          "user-tier path (existing config backed up)")
    imp.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.command == "export":
        export_workspace(output_path=args.output, verbose=args.verbose)
    elif args.command == "import":
        import_workspace(args.archive, dry_run=args.dry_run,
                         verbose=args.verbose, clean=args.clean,
                         apply_meta=args.apply_meta)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
