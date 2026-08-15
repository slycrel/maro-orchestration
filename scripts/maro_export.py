#!/usr/bin/env python3
"""maro-export / maro-import — Maro data transfer (workspace + metadata).

Archive format v2 (2026-08-13, Jeremy: "the behavior IS data … intent has
always been data sharing, and that's always meant all of our metadata"):

  workspace/**          the workspace (secrets excluded, sqlite via
                        consistent snapshots)
  meta/user-config.yml  user-tier ~/.maro/config.yml — behavior config,
                        credential-shaped values redacted STRUCTURALLY
                        at export (fails closed if the YAML won't parse)
  meta/experiments/**   ~/.maro/experiments/ (regular files)
  meta/symlinks.json    machine-pointing links (absolute target, or
                        resolving/looping outside the workspace) recorded
                        as data, not shipped as links — a link to
                        /usr/bin/python3 resolves to the WRONG binary on
                        another OS. Internal relative links still travel.
  meta/provenance.json  who exported, from where, tool fingerprint, a
                        workspace-shape digest (self-attested, UNSIGNED —
                        an integrity hint, not tamper proof), and a
                        custody chain each import appends to and each
                        re-export carries forward.

SECURITY POSTURE (3-lens review of c257a48, 2026-08-13): an archive may
come from another machine or another person, so on IMPORT every member is
UNTRUSTED input. Import (a) gates on archive format, (b) preflights every
member against a type + link-target allowlist before touching the
destination, (c) validates provenance shape before any mutation, (d)
stages meta into a fresh per-import dir under <ws>/.import-meta/<ts>/
without ever changing this machine's behavior, screening secret-shaped
meta out, and (e) sanitizes archive-authored strings before printing.
The shape digest is explicitly a hint, not a signature — the real
security layer is later work; this is its groundwork.

Import stages meta NON-DESTRUCTIVELY. --apply-meta places THIS import's
user config at the real user-tier path (existing config backed up, never
deleted; symlink destinations refused). By default import MERGES;
--clean moves the existing workspace aside first (never deletes it).

Back-compat: v1 archives (no meta/) import cleanly.

Usage:
    python3 scripts/maro_export.py export [--output PATH]
    python3 scripts/maro_export.py inspect ARCHIVE
    python3 scripts/maro_export.py import ARCHIVE [--dry-run] [--clean]
                                          [--apply-meta]

`inspect` is look-before-you-import: it prints provenance + custody,
verifies the workspace-shape digest, and previews what import WOULD skip
(unsafe member types/links, secret-shaped, traversal) — all read-only,
nothing extracted. Use it to decide whether to trust an archive from
someone else before importing.

Still not carried (inherent): machine semantics embedded IN data —
absolute paths inside artifacts/checkpoints, CLI session ids — and file
ownership maps to the importing user. provenance records the source
workspace root so a future consumer can rewrite embedded paths (BACKLOG
path-token item).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ARCHIVE_FORMAT = 2
_META_PREFIX = "meta/"
_STAGING_DIRNAME = ".import-meta"
_REDACT_MARKER = "REDACTED-BY-EXPORT"

# Untrusted-archive resource caps (import side).
_MAX_MEMBERS = 2_000_000
_MAX_META_FILE_BYTES = 512 * 1024 * 1024   # streamed; skip+warn beyond
# Untrusted-import workspace payload caps (decompression-bomb defense —
# whole-changeset review 2026-08-13). Generous enough for real workspaces
# (large sqlite memory stores) while bounding a hostile archive's blast
# radius to something a host disk survives. OVERRIDABLE via env (fixpoint
# review 2026-08-13, Architect: export has no matching cap, so a legitimate
# >16 GiB workspace exported fine but could not be re-imported — a trusted
# owner needs an escape hatch the bomb defense otherwise denies).
_DEFAULT_MAX_WS_FILE_BYTES = 4 * 1024 * 1024 * 1024      # 4 GiB per file
_DEFAULT_MAX_WS_TOTAL_BYTES = 16 * 1024 * 1024 * 1024    # 16 GiB aggregate


def _env_bytes(name: str, default: int) -> int:
    """Positive int from env, else the default (fail-safe on garbage)."""
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (ValueError, AttributeError):
        return default


def _max_ws_file_bytes() -> int:
    return _env_bytes("MARO_IMPORT_MAX_FILE_BYTES", _DEFAULT_MAX_WS_FILE_BYTES)


def _max_ws_total_bytes() -> int:
    return _env_bytes("MARO_IMPORT_MAX_TOTAL_BYTES",
                      _DEFAULT_MAX_WS_TOTAL_BYTES)
_MAX_PROVENANCE_BYTES = 4 * 1024 * 1024
_MAX_CUSTODY_PRINT = 50
_PRINT_FIELD_CAP = 300
_COPY_CHUNK = 1024 * 1024

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

# Whole-WORD credential key match (review of c257a48: substring "token"
# clobbered benign `max_tokens`). Split the key on non-alphanumerics; a
# word hit OR an explicit compound phrase marks the key credential-shaped.
# Redaction additionally requires the VALUE be a string, so numeric
# settings like max_tokens / token_budget are never touched even when the
# key matches.
_CRED_WORDS = {
    "token", "secret", "password", "passwd", "apikey", "bearer",
    "credential", "credentials", "pat", "privatekey",
}
_CRED_PHRASES = (
    "api_key", "apikey", "access_key", "secret_key", "private_key",
    "client_secret", "auth_token",
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


def _is_cred_key(key) -> bool:
    k = str(key).lower()
    words = set(re.split(r"[^a-z0-9]+", k))
    if words & _CRED_WORDS:
        return True
    return any(p in k for p in _CRED_PHRASES)


# Two secret-leak vectors closed by the whole-changeset review 2026-08-13
# (Architect): (1) a YAML anchor aliases a secret value out from under its
# credential key (`api_key: &s SECRET` / `label: *s`) — safe_load resolves
# it into an independent leaf under a benign key _is_cred_key never flags;
# (2) a `!!binary`-encoded secret under a credential key is `bytes`, which
# the str-only check skipped. The fixpoint round (2026-08-13) found the
# first fix's value-equality sweep did both too little AND too much: it
# missed aliases copied into KEY positions and into !!set/!!omap containers,
# and it CORRUPTED benign values that coincidentally equalled a short secret
# (`api_key: prod` redacted an unrelated `environment: prod`). This version:
#   - redacts str/bytes under a credential key (numeric/bool settings under
#     cred-shaped keys survive — `max_tokens: 4096`, the pinned contract);
#   - FAILS CLOSED on any container type it can't traverse in place
#     (!!set/!!omap/tuple) under a credential context, so a secret can never
#     slip through as raw YAML with ok=True;
#   - sweeps anchor/alias copies by OBJECT IDENTITY, not value equality —
#     PyYAML resolves an alias to the SAME object as its anchor (verified),
#     while two coincidentally-equal scalars are distinct objects, so
#     identity catches every real alias (key OR value) and never touches a
#     look-alike. Redacted objects are pinned in a list so their id() can't
#     be reused by a later allocation during the walk.


class _UnredactableShape(Exception):
    """A credential subtree holds a container we can't redact in place
    (!!set/!!omap/tuple) — the caller must fail closed."""


def _is_redactable_scalar(v) -> bool:
    return isinstance(v, (str, bytes)) and len(v) > 0


def _redact_tree(node, parent_is_cred: bool = False,
                 secret_ids=None, pinned=None) -> int:
    """Redact str/bytes values under credential-shaped keys, in place.

    Raises _UnredactableShape if a credential context contains a container
    type this cannot safely redact (set/tuple/frozenset). When `secret_ids`
    /`pinned` are passed, each redacted secret object's id() is collected
    (and the object pinned alive) so _sweep_by_id can catch alias copies."""
    def _collect(v):
        if secret_ids is not None:
            pinned.append(v)          # keep alive → id() stable, no reuse
            secret_ids.add(id(v))

    count = 0
    if isinstance(node, dict):
        for k, v in list(node.items()):
            cred = parent_is_cred or _is_cred_key(k)
            if isinstance(v, (dict, list)):
                count += _redact_tree(v, cred, secret_ids, pinned)
            elif _is_redactable_scalar(v):
                if cred:
                    _collect(v)
                    node[k] = _REDACT_MARKER
                    count += 1
            elif v is None or isinstance(v, (int, float, bool)):
                pass  # numeric/bool/null settings survive
            elif cred:
                raise _UnredactableShape(type(v).__name__)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, (dict, list)):
                count += _redact_tree(v, parent_is_cred, secret_ids, pinned)
            elif _is_redactable_scalar(v):
                if parent_is_cred:
                    _collect(v)
                    node[i] = _REDACT_MARKER
                    count += 1
            elif v is None or isinstance(v, (int, float, bool)):
                pass
            elif parent_is_cred:
                raise _UnredactableShape(type(v).__name__)
    return count


def _sweep_by_id(node, secret_ids) -> int:
    """Second pass: replace any leaf — dict KEY or value, list item — whose
    object identity matches a redacted secret (an anchor aliased elsewhere).
    Identity, not equality, so a benign look-alike is never touched."""
    if not secret_ids:
        return 0
    count = 0
    if isinstance(node, dict):
        rekey = False
        items = []
        for k, v in list(node.items()):
            nk = k
            if id(k) in secret_ids:
                nk, rekey = _REDACT_MARKER, True
                count += 1
            if isinstance(v, (dict, list)):
                count += _sweep_by_id(v, secret_ids)
            elif id(v) in secret_ids:
                v = _REDACT_MARKER
                count += 1
            items.append((nk, v))
        node.clear()
        for nk, nv in items:
            node[nk] = nv
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, (dict, list)):
                count += _sweep_by_id(v, secret_ids)
            elif id(v) in secret_ids:
                node[i] = _REDACT_MARKER
                count += 1
    return count


def _redact_config_text(text: str):
    """Structurally redact credential-shaped values in YAML config text.

    Returns (out_text, redaction_count, ok). ok=False means the config
    could not be safely transformed (unparseable, not a mapping, or holding
    a credential subtree we can't redact in place) — the caller MUST NOT
    ship it (fail closed): shipping raw could leak a secret the line matcher
    would miss. When redactions==0 the ORIGINAL text is returned verbatim so
    comments/layout survive the common (credential-free) case; when >0 a
    structural re-dump is returned (comments lost — a deliberate trade for
    guaranteed redaction).
    """
    import yaml
    try:
        data = yaml.safe_load(text)
    except Exception:
        return "", 0, False
    if data is None:
        return text, 0, True  # empty config, nothing to hide
    if not isinstance(data, dict):
        return "", 0, False  # unexpected shape — refuse rather than guess
    secret_ids: set = set()
    pinned: list = []
    try:
        count = _redact_tree(data, secret_ids=secret_ids, pinned=pinned)
    except _UnredactableShape:
        return "", 0, False  # fail closed on an unredactable cred subtree
    count += _sweep_by_id(data, secret_ids)
    if count == 0:
        return text, 0, True
    return yaml.safe_dump(data, default_flow_style=False,
                          sort_keys=False), count, True


def _classify_symlink_target(target: str, member_rel_parts: tuple) -> bool:
    """True if the link is INTERNAL (relative, staying inside the tree).

    Pure string containment (no FS) so it works identically at export and
    import and can't be defeated by a resolve-loop. member_rel_parts are
    the link's own path parts relative to the workspace root.
    """
    if os.path.isabs(target):
        return False
    base = Path(*member_rel_parts[:-1]) if len(member_rel_parts) > 1 else Path()
    combined = os.path.normpath(str(base / target))
    return not (combined == ".." or combined.startswith(".." + os.sep))


def _snapshot_sqlite(src: Path, dst: Path) -> bool:
    """Copy a sqlite database consistently via the backup API.

    The workspace is exported hot (runs may be mid-write); a raw byte
    copy of a live sqlite file can be torn. Returns False when src is
    not a readable sqlite database — the caller falls back to raw bytes
    rather than dropping the file.

    Path goes in as a percent-encoded file: URI (via as_uri) — naive
    f-string interpolation let a '?' or '#' in the filename truncate the
    URI, silently opening a DIFFERENT (empty) database and "succeeding"
    with an empty snapshot (review of 707a541, reproduced). Success also
    requires page-count parity between source and snapshot.
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


def _manifest_digest(entries) -> str:
    """sha256 over sorted 'name<TAB>size' lines of workspace file members.

    A WORKSPACE-SHAPE hint (names+sizes only — NOT bytes, meta, or link
    targets) for detecting accidental corruption / casual tampering.
    Never called "verified" without the "shape, self-attested, unsigned"
    qualifier (review of c257a48: the old "manifest verified" overstated
    it). The future security layer signs a payload manifest; this is not
    that.
    """
    lines = sorted(f"{name}\t{size}" for name, size in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _sanitize_for_terminal(value, cap: int = _PRINT_FIELD_CAP) -> str:
    """Strip control characters and cap length before printing an
    archive-authored string. A hostile provenance blob otherwise injects
    newlines / ANSI escapes to forge digest or custody lines (review of
    c257a48, reproduced)."""
    s = str(value)
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", s)
    if len(s) > cap:
        s = s[:cap] + "…"
    return s


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


def _deref_hardlink(ti: tarfile.TarInfo, src: Path) -> tarfile.TarInfo:
    """tar encodes a second path to an already-added inode as a hardlink
    member; the importer rejects those (`_safe_workspace_member` islnk /
    meta staging isreg-only), so the second path silently vanished on
    restore (accepted residual of the 2026-08-13 review). Rewrite to a
    regular file with the real on-disk size so the bytes ship — both
    paths restore as independent files, the honest portable meaning.
    Applied on EVERY add lane: workspace tree filter, sqlite snapshot
    tarinfo, raw-db/sidecar adds, experiments adds."""
    if ti.islnk():
        ti.type = tarfile.REGTYPE
        ti.linkname = ""
        ti.size = src.stat().st_size
    return ti


def _tar_add_deref(tar: tarfile.TarFile, src: Path, arcname: str) -> None:
    """Single-file tar.add with the hardlink dereference — one spelling
    for the three raw-add lanes (raw-db fallback, sidecars, experiments)."""
    tar.add(str(src), arcname=arcname, recursive=False,
            filter=lambda ti: _deref_hardlink(ti, src))


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes,
               mode: int = 0o644) -> None:
    ti = tarfile.TarInfo(name=arcname)
    ti.size = len(data)
    ti.mode = mode
    ti.mtime = int(time.time())
    tar.addfile(ti, io.BytesIO(data))


def _prior_custody(ws: Path) -> list:
    """The custody chain of the newest prior import staged in this
    workspace — carried forward so lineage survives a re-export
    (A→B→C), the point of a custody chain (review of c257a48)."""
    staging = ws / _STAGING_DIRNAME
    if not staging.is_dir():
        return []
    provs = sorted(staging.glob("*/provenance.json"))
    if not provs:
        return []
    try:
        prov = json.loads(provs[-1].read_text())
        chain = prov.get("custody")
        return chain if isinstance(chain, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_workspace(output_path: Path = None, verbose: bool = False) -> Path:
    """Export workspace + user-tier metadata to a tar.gz archive."""
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
    manifest_entries: list[tuple[str, int]] = []
    db_files: list[tuple[str, Path]] = []
    sidecars: list[tuple[str, Path]] = []
    external_symlinks: list[dict] = []

    def _abs_for(rel: str) -> Path:
        return ws / Path(*_rel_parts(rel))

    def _record_external(parts: tuple, target: str) -> None:
        external_symlinks.append(
            {"path": str(Path(*parts)), "target": target})
        if verbose:
            print(f"  meta: {'/'.join(parts)} → symlinks.json "
                  f"(external target {target})", file=sys.stderr)

    def _filter(tarinfo: tarfile.TarInfo):
        nonlocal file_count, other_count, total_bytes
        rel = tarinfo.name
        if _should_exclude(rel):
            if verbose:
                print(f"  skip: {rel}", file=sys.stderr)
            return None
        if tarinfo.issym():
            parts = _rel_parts(rel)
            if _classify_symlink_target(tarinfo.linkname, parts):
                other_count += 1
                return tarinfo  # internal relative link stays in the tree
            _record_external(parts, tarinfo.linkname)
            return None
        _deref_hardlink(tarinfo, _abs_for(rel))  # see helper: islnk → REGTYPE
        name = Path(rel).name
        if tarinfo.isfile() and name.endswith((".db", ".db-wal", ".db-shm")):
            (db_files if name.endswith(".db") else sidecars).append(
                (rel, _abs_for(rel)))
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
        # sidecars fold into the snapshot and must NOT ship alongside (a
        # fresh snapshot next to a stale wal corrupts on open). On failure
        # (not sqlite / unreadable) fall back to raw bytes, WITH sidecars.
        snapshotted_bases: set = set()
        for i, (rel, src) in enumerate(db_files):
            snap = Path(tmpd) / f"snap-{i}.db"
            if _snapshot_sqlite(src, snap):
                # Carry the SOURCE file's metadata (mode/mtime) with the
                # snapshot bytes — tar.add(snap) would stamp 0644/now,
                # broadening a 0600 database on restore (review of 707a541).
                ti = _deref_hardlink(tar.gettarinfo(str(src), arcname=rel),
                                     snap)
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
                _tar_add_deref(tar, src, rel)
                file_count += 1
                total_bytes += src.stat().st_size
                manifest_entries.append(
                    (str(Path(*_rel_parts(rel))), src.stat().st_size))
                if verbose:
                    print(f"  add:  {rel} (raw — not a readable sqlite db)",
                          file=sys.stderr)
        for rel, src in sidecars:
            base = rel[: rel.rfind("-")]
            if base in snapshotted_bases:
                if verbose:
                    print(f"  skip: {rel} (folded into snapshot)",
                          file=sys.stderr)
                continue
            _tar_add_deref(tar, src, rel)
            file_count += 1
            total_bytes += src.stat().st_size
            manifest_entries.append(
                (str(Path(*_rel_parts(rel))), src.stat().st_size))

        # --- meta/ area: behavior IS data -------------------------------
        cfg_path = _user_config_path()
        if cfg_path.exists():
            try:
                red_text, user_config_redactions, ok = _redact_config_text(
                    cfg_path.read_text())
            except Exception as exc:
                ok = False
                print(f"  WARN: user config unreadable, not exported: {exc}",
                      file=sys.stderr)
            if ok:
                _add_bytes(tar, _META_PREFIX + "user-config.yml",
                           red_text.encode("utf-8"), mode=0o600)
                user_config_present = True
                meta_count += 1
                if user_config_redactions and verbose:
                    print(f"  meta: user-config.yml "
                          f"({user_config_redactions} value(s) redacted)",
                          file=sys.stderr)
            else:
                print("  WARN: user config could not be safely redacted "
                      "(unparseable YAML) — NOT exported (fail closed)",
                      file=sys.stderr)

        exp_dir = _maro_dir() / "experiments"
        exp_files = 0
        exp_links = 0
        if exp_dir.is_dir():
            for p in sorted(exp_dir.rglob("*")):
                rel = p.relative_to(exp_dir)
                mparts = ("experiments",) + rel.parts
                if _secret_shaped(mparts):
                    if verbose:
                        print(f"  skip: experiments/{rel} (secret-shaped)",
                              file=sys.stderr)
                    continue
                if p.is_symlink():
                    # Same portability policy as the workspace: external
                    # links become records, not shipped links.
                    target = os.readlink(p)
                    if _classify_symlink_target(target, mparts):
                        ti = tarfile.TarInfo(
                            _META_PREFIX + "experiments/" + str(rel))
                        ti.type = tarfile.SYMTYPE
                        ti.linkname = target
                        tar.addfile(ti)
                    else:
                        _record_external(mparts, target)
                        exp_links += 1
                    continue
                if not p.is_file():
                    continue  # empty dirs not preserved (documented)
                _tar_add_deref(
                    tar, p, _META_PREFIX + "experiments/" + str(rel))
                exp_files += 1
                meta_count += 1

        if external_symlinks:
            _add_bytes(
                tar, _META_PREFIX + "symlinks.json",
                json.dumps({"format": ARCHIVE_FORMAT,
                            "note": ("links whose targets are absolute or "
                                     "escape the workspace — recorded, not "
                                     "shipped; recreate by hand on a "
                                     "matching host if ever needed"),
                            "links": external_symlinks},
                           indent=2).encode("utf-8"))
            meta_count += 1

        try:
            script_sha = hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest()
        except Exception:
            script_sha = "unknown"

        # Carry prior lineage forward so custody survives re-export.
        custody = list(_prior_custody(ws))
        custody.append(
            {"event": "export", "at": _utcnow(), "by": _identity()})

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
                "workspace_shape_sha256": _manifest_digest(manifest_entries),
                "digest_covers": ("workspace file names+sizes only — NOT "
                                  "bytes, meta, or link targets; "
                                  "self-attested, UNSIGNED integrity hint"),
            },
            "meta": {
                "user_config": user_config_present,
                "user_config_redactions": user_config_redactions,
                "experiments_files": exp_files,
                "external_symlinks": len(external_symlinks),
            },
            "custody": custody,
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


# ---------------------------------------------------------------------------
# Import — every member is untrusted
# ---------------------------------------------------------------------------

def _validate_provenance(obj):
    """Coerce archive provenance to a safe shape. Returns a dict with the
    expected sub-types (never raises downstream) or None if unusable.
    Import must never crash on hostile provenance AFTER mutating the
    workspace (review of c257a48, reproduced).

    `format` must be a canonical int — a provenance whose format is "99" or
    3.0 is crafted or corrupt, and letting it duck the newer-format gate
    fails open (review 2026-08-13). bool is excluded explicitly (it passes
    isinstance int)."""
    if not isinstance(obj, dict):
        return None
    fmt = obj.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        return None
    def _d(k):
        v = obj.get(k)
        return v if isinstance(v, dict) else {}
    custody = obj.get("custody")
    custody = [e for e in custody if isinstance(e, dict)] \
        if isinstance(custody, list) else []
    return {
        "format": fmt,
        "created_at": obj.get("created_at", "?"),
        "exporter": obj.get("exporter", "?"),
        "source": _d("source"),
        "contents": _d("contents"),
        "meta": _d("meta"),
        "custody": custody,
    }


def _load_provenance(tar, meta_members):
    """(prov, status) — status ∈ valid / absent / oversized / unreadable /
    invalid-shape. The FIRST meta/provenance.json member wins, matching
    import's stop-at-first behavior — inspect previously skipped an
    oversized first member and could accept a later duplicate, so the two
    lanes could disagree about the same archive (review 2026-08-13)."""
    for m in meta_members:
        if m.name != _META_PREFIX + "provenance.json":
            continue
        if not m.isreg():
            return None, "invalid-shape"
        if m.size > _MAX_PROVENANCE_BYTES:
            return None, "oversized"
        try:
            fh = tar.extractfile(m)
            obj = json.loads(fh.read().decode("utf-8")) if fh else None
        except Exception:
            return None, "unreadable"
        prov = _validate_provenance(obj)
        return (prov, "valid") if prov is not None else (None, "invalid-shape")
    return None, "absent"


class _ArchiveCapExceeded(Exception):
    """Raised mid-scan when the archive exceeds a resource cap."""


# Reasonable ceiling for a single member's path length (GNU longname headers
# are otherwise unbounded — a hostile archive can pack megabyte names).
_MAX_NAME_CHARS = 4096
# Bound retained example names per skip reason — reporting stays honest via
# counts; a hostile archive must not inflate host memory through its skips.
_MAX_SKIP_EXAMPLES = 100


def _meta_member_risk(member, rel: str):
    """(code, detail) preview of why meta staging would not stage this
    member, or None if it stages. Pure-string mirror of _stage_meta's
    screens — the SINGLE policy both inspect and import consume (review
    2026-08-13: inspect only counted secret-shaped regular files and missed
    traversal, links, specials, and the size cap)."""
    if _secret_shaped(("meta",) + Path(rel).parts):
        return "secret-shaped meta (screened)", rel
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
        return "meta traversal", rel
    if member.isdir():
        return None
    if not member.isreg():
        return "meta non-regular member (link/special)", rel
    if member.size > _MAX_META_FILE_BYTES:
        return "meta file exceeds size cap", rel
    return None


def _scan_and_classify(tar):
    """One-pass streaming scan + classification of an untrusted archive —
    the shared policy core for inspect (preview) and import (enforcement).

    Caps are enforced AS MEMBERS STREAM, so a hostile archive is rejected
    at the cap rather than after the whole member list is materialized;
    the meta/workspace partition happens in the same pass (review
    2026-08-13: getmembers()-then-`m not in meta` was full-scan-then-
    quadratic in BOTH lanes). tarfile still retains TarInfo objects on the
    TarFile (needed for extraction), so peak memory is bounded by the cap,
    not by the archive.

    Only PURE-STRING policy runs here. Destination-dependent enforcement
    (_safe_workspace_member's ws-relative bind, _stage_meta's staging
    bind) stays at import time and is labeled as such in inspect output.

    Returns a dict; raises _ArchiveCapExceeded on a cap breach.
    """
    out = {
        "n_members": 0,
        "meta_members": [],
        "ws_candidates": [],      # (member, rel) that pass pure-string policy
        "ws_entries": [],         # (rel, size) regular files in digest scope
        "ws_total_bytes": 0,      # aggregate workspace payload (bomb cap)
        "excluded": [],           # rel paths _should_exclude'd (policy skips)
        "skips": {},              # code -> {"count": int, "examples": [str]}
        "meta_skips": {},         # same shape, for meta staging screens
    }

    def _add_skip(table, code, display):
        row = table.setdefault(code, {"count": 0, "examples": []})
        row["count"] += 1
        if len(row["examples"]) < _MAX_SKIP_EXAMPLES:
            row["examples"].append(display)

    while True:
        member = tar.next()
        if member is None:
            break
        out["n_members"] += 1
        if out["n_members"] > _MAX_MEMBERS:
            raise _ArchiveCapExceeded(
                f"archive exceeds {_MAX_MEMBERS} member cap")
        if len(member.name) > _MAX_NAME_CHARS:
            _add_skip(out["skips"], "unreasonable member name length",
                      member.name[:80] + "…")
            continue
        if member.name == "meta" or member.name.startswith(_META_PREFIX):
            out["meta_members"].append(member)
            rel = member.name[len(_META_PREFIX):]
            if member.name != "meta" and rel:
                risk = _meta_member_risk(member, rel)
                if risk:
                    _add_skip(out["meta_skips"], risk[0], risk[1])
            continue
        if member.name == "workspace":
            continue
        rel = member.name[len("workspace/"):] \
            if member.name.startswith("workspace/") else member.name
        if not rel:
            continue
        if _should_exclude(rel):
            if len(out["excluded"]) < _MAX_SKIP_EXAMPLES:
                out["excluded"].append(rel)
            else:
                out["excluded"].append(None)  # counted, name not retained
            continue
        code_detail = _member_import_risk(member, rel)
        if code_detail:
            _add_skip(out["skips"], code_detail[0], code_detail[1])
            continue
        out["ws_candidates"].append((member, rel))
        if member.isreg():
            # Decompression-bomb / disk-exhaustion cap (whole-changeset
            # review 2026-08-13, 3/3 consensus): meta had a per-file cap but
            # WORKSPACE members — the main payload — had none, so a tiny
            # gzip declaring a multi-TB regular file passed the member-count
            # check and inflated until the destination filled. member.size
            # is the tar header's true uncompressed size, so this rejects
            # the bomb BEFORE any extraction/mutation. Enforced as members
            # stream, same as the count cap.
            _file_cap = _max_ws_file_bytes()
            if member.size > _file_cap:
                raise _ArchiveCapExceeded(
                    f"workspace member {rel!r} is {member.size:,} bytes, "
                    f"over the {_file_cap:,}-byte per-file cap (raise "
                    "MARO_IMPORT_MAX_FILE_BYTES for a trusted large archive)")
            out["ws_total_bytes"] += member.size
            _total_cap = _max_ws_total_bytes()
            if out["ws_total_bytes"] > _total_cap:
                raise _ArchiveCapExceeded(
                    f"workspace payload exceeds the {_total_cap:,}-byte total "
                    "cap (raise MARO_IMPORT_MAX_TOTAL_BYTES for a trusted "
                    "large archive)")
            out["ws_entries"].append((rel, member.size))
    return out


def _safe_workspace_member(member, rel_name: str, ws: Path):
    """(ok, reason) — type + link-target allowlist for an untrusted
    member. Only regular files, dirs, and INTERNAL relative symlinks are
    allowed; special files, hardlinks, and absolute/escaping links are
    rejected (review of c257a48 — filter='tar' permitted FIFOs and
    absolute link targets; the <3.11.4 fallback was fully unfiltered)."""
    # Containment of the member's own name.
    dest = (ws / rel_name).resolve()
    try:
        dest.relative_to(ws.resolve())
    except ValueError:
        return False, "path traversal"
    # A workspace member must never land inside the staging dir.
    if _STAGING_DIRNAME in Path(rel_name).parts:
        return False, "targets staging dir"
    if member.isdir() or member.isreg():
        return True, ""
    if member.issym():
        if _classify_symlink_target(member.linkname, _rel_parts(rel_name)):
            return True, ""
        return False, f"external symlink target {member.linkname!r}"
    if member.islnk():
        return False, "hardlink member"
    return False, "special file (fifo/device)"


def _copy_member_streamed(tar, member, dest: Path) -> bool:
    """Stream a meta member to disk in chunks with a size cap (no full
    read into memory — hostile archives can hide a decompression bomb).
    Returns True if written, False if skipped."""
    src = tar.extractfile(member)
    if src is None:
        return False
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        while True:
            chunk = src.read(_COPY_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > _MAX_META_FILE_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                print(f"  SKIP (meta file exceeds "
                      f"{_MAX_META_FILE_BYTES} bytes): {member.name}",
                      file=sys.stderr)
                return False
            out.write(chunk)
    return True


def _stage_meta(tar, meta_members, staging: Path, verbose: bool):
    """Extract meta/** into a FRESH staging dir. Returns the set of
    staged relative names. Secret-shaped meta is screened out; modes are
    normalized (an attacker-chosen 0444 must not later break the custody
    write); traversal within meta is guarded."""
    staged = set()
    for member in meta_members:
        rel = member.name[len(_META_PREFIX):]
        if not rel:
            continue
        if _secret_shaped(("meta",) + Path(rel).parts):
            if verbose:
                print(f"  screen: {member.name} (secret-shaped meta)",
                      file=sys.stderr)
            continue
        dest = (staging / rel).resolve()
        try:
            dest.relative_to(staging.resolve())
        except ValueError:
            print(f"  SKIP (meta traversal): {member.name}", file=sys.stderr)
            continue
        if member.isdir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isreg():
            continue  # no links/specials in meta
        if _copy_member_streamed(tar, member, dest):
            try:
                dest.chmod(0o600)
            except OSError:
                pass
            staged.add(rel)
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
    expect_sha256: str = "",
) -> int:
    """Import a Maro export archive. See module docstring for the security
    posture. Returns the number of workspace files extracted."""
    from config import _user_config_path, workspace_root
    ws = workspace_root()

    if not archive_path.exists():
        print(f"Error: archive not found: {archive_path}", file=sys.stderr)
        sys.exit(1)

    # Extraction filters are the safety floor (the `data` filter clamps
    # link targets and rejects specials); the preflight screens are defense
    # in depth ABOVE it, not a substitute. On Pythons without them
    # (<3.11.4 and no backport) the old TypeError fallback extracted
    # unfiltered — orderable to escape through a pre-existing external
    # symlink on a merge import (accepted residual of the 2026-08-13
    # whole-changeset review). Refuse before touching anything.
    if not hasattr(tarfile, "data_filter"):
        print("Error: this Python's tarfile has no extraction filters "
              "(needs 3.11.4+ or a filter backport) — refusing to extract "
              "an untrusted archive without them. Nothing was changed.",
              file=sys.stderr)
        sys.exit(1)

    # Bind this import to the bytes a prior `inspect` vouched for (review
    # 2026-08-13: inspect and import reopened the pathname independently, so
    # the trust decision could apply to a swapped file). Prefixes are
    # accepted at >=16 hex chars, full digest preferred. ONE descriptor
    # carries both the digest and the extraction (the same review's TOCTOU
    # residual): hashing a reopened pathname left a swap window in which
    # tarfile.open could read bytes the digest never saw.
    archive_fh = open(archive_path, "rb")
    if expect_sha256:
        want = expect_sha256.strip().lower().rstrip("…")
        if len(want) < 16 or not all(c in "0123456789abcdef" for c in want):
            print("Error: --expect-sha256 needs at least 16 hex chars",
                  file=sys.stderr)
            sys.exit(1)
        sha = hashlib.sha256()
        for chunk in iter(lambda: archive_fh.read(1 << 20), b""):
            sha.update(chunk)
        archive_fh.seek(0)
        actual = sha.hexdigest()
        if not actual.startswith(want):
            print(f"Error: archive sha256 {actual[:16]}… does not match "
                  f"--expect-sha256 — the file changed since it was "
                  f"inspected. Nothing was changed.", file=sys.stderr)
            sys.exit(1)

    with archive_fh, tarfile.open(fileobj=archive_fh, mode="r:gz") as tar:
        # Shared one-pass scan/classify — the same policy inspect previews
        # (review 2026-08-13: two implementations had already diverged, and
        # both lanes paid getmembers()-then-quadratic-partition).
        try:
            scan = _scan_and_classify(tar)
        except _ArchiveCapExceeded as exc:
            print(f"Error: {exc} — refusing", file=sys.stderr)
            sys.exit(1)

        if dry_run:
            print(f"Archive contains {scan['n_members']} entries:",
                  file=sys.stderr)
            for m in tar.getmembers():
                print(f"  {_sanitize_for_terminal(m.name)} ({m.size:,} bytes)")
            return 0

        meta_members = scan["meta_members"]

        # --- preflight BEFORE any mutation ------------------------------
        prov, prov_status = _load_provenance(tar, meta_members)
        if prov_status not in ("valid", "absent"):
            # A provenance file that EXISTS but can't be trusted (oversized,
            # unreadable, malformed shape, non-int format) is a crafted or
            # corrupt archive — fail closed before touching anything
            # (review 2026-08-13: "treating as absent" let a malformed
            # lineage duck the format gate).
            print(f"Error: archive provenance is malformed ({prov_status}) "
                  "— refusing to import an archive whose lineage cannot be "
                  "read. Nothing was changed.", file=sys.stderr)
            sys.exit(1)

        # Format gate: refuse a newer archive rather than half-importing it.
        if prov is not None and prov["format"] > ARCHIVE_FORMAT:
            print(f"Error: archive format {prov['format']} is newer than "
                  f"this tool supports (v{ARCHIVE_FORMAT}). Upgrade "
                  f"maro-export before importing. Nothing was changed.",
                  file=sys.stderr)
            sys.exit(1)

        # Pure-string screens already ran in the scan; report them, then
        # apply the destination-bound half (_safe_workspace_member) that
        # inspect honestly does not preview.
        _print_skip_table("SKIP ", scan["skips"], verbose, file=sys.stderr)
        if verbose and scan["excluded"]:
            print(f"  skip: {len(scan['excluded'])} policy-excluded file(s) "
                  "(exporter never ships these)", file=sys.stderr)
        safe_members = []
        for member, rel in scan["ws_candidates"]:
            ok, reason = _safe_workspace_member(member, rel, ws)
            if not ok:
                print(f"  SKIP ({reason}): {_sanitize_for_terminal(rel)}",
                      file=sys.stderr)
                continue
            safe_members.append((member, rel))

        # --- mutation begins --------------------------------------------
        if ws.exists() and any(ws.iterdir()):
            if clean:
                base = ws.name + time.strftime(".pre-import-%Y%m%dT%H%M%S")
                aside = ws.with_name(base)
                n = 1
                while aside.exists():
                    n += 1
                    aside = ws.with_name(f"{base}-{n}")
                ws.rename(aside)
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
        other = 0
        manifest_entries: list[tuple[str, int]] = []
        for member, rel in safe_members:
            member.name = rel  # extract relative to ws (prefix stripped)
            if verbose:
                print(f"  extract: {rel}", file=sys.stderr)
            # 'data' filter clamps link targets and rejects special
            # files — defense in depth atop the preflight above. Filter
            # availability is guaranteed by the data_filter gate before
            # any mutation; there is deliberately NO unfiltered fallback
            # (the old TypeError branch was orderable into a symlink
            # escape on pre-3.11.4 targets — 2026-08-13 review).
            tar.extract(member, path=str(ws), filter="data")
            if member.isfile():
                extracted += 1
                manifest_entries.append((rel, member.size))
            else:
                other += 1

        # --- meta staging (non-destructive, fresh per import) -----------
        staged_count = 0
        this_import_dir = None
        if meta_members:
            base = ws / _STAGING_DIRNAME
            stamp = time.strftime("import-%Y%m%dT%H%M%S")
            this_import_dir = base / stamp
            n = 1
            while this_import_dir.exists():
                n += 1
                this_import_dir = base / f"{stamp}-{n}"
            this_import_dir.mkdir(parents=True)
            staged = _stage_meta(tar, meta_members, this_import_dir, verbose)
            staged_count = len(staged)

            actual_shape = _manifest_digest(manifest_entries)
            if prov is not None:
                claimed = prov["contents"].get("workspace_shape_sha256", "")
                shape_ok = bool(claimed) and claimed == actual_shape
                meta_info = prov["meta"]
                print("PROVENANCE (self-attested, UNSIGNED):", file=sys.stderr)
                print(f"  exported by {_sanitize_for_terminal(prov['exporter'])}"
                      f" at {_sanitize_for_terminal(prov['created_at'])} from "
                      f"{_sanitize_for_terminal(prov['source'].get('workspace_root', '?'))}",
                      file=sys.stderr)
                print(f"  workspace shape digest: "
                      f"{'OK' if shape_ok else 'MISMATCH'} "
                      f"(names+sizes only — not a signature)", file=sys.stderr)
                if not shape_ok:
                    print("  WARNING: workspace shape does not match the "
                          "exporter's digest — corrupted, tampered, or a "
                          "partial extract. Treat with suspicion.",
                          file=sys.stderr)
                print(f"  meta: user-config="
                      f"{bool(meta_info.get('user_config'))} "
                      f"(redactions={meta_info.get('user_config_redactions', 0)}), "
                      f"experiments={meta_info.get('experiments_files', 0)}, "
                      f"external-symlinks={meta_info.get('external_symlinks', 0)}",
                      file=sys.stderr)
                for ev in prov["custody"][:_MAX_CUSTODY_PRINT]:
                    print(f"  custody: {_sanitize_for_terminal(ev.get('event', '?'))}"
                          f" by {_sanitize_for_terminal(ev.get('by', '?'))} at "
                          f"{_sanitize_for_terminal(ev.get('at', '?'))}",
                          file=sys.stderr)
                if len(prov["custody"]) > _MAX_CUSTODY_PRINT:
                    print(f"  custody: … {len(prov['custody']) - _MAX_CUSTODY_PRINT}"
                          f" earlier event(s) not shown", file=sys.stderr)
                # Append this import to the staged chain (never the source).
                prov["custody"].append({
                    "event": "import", "at": _utcnow(), "by": _identity(),
                    "dest": str(ws), "shape_verified": shape_ok,
                })
                (this_import_dir / "provenance.json").write_text(
                    json.dumps(prov, indent=2))
            else:
                print("note: archive carries meta/ but no usable provenance.json",
                      file=sys.stderr)

            _apply_user_config(this_import_dir, prov, apply_meta,
                               _user_config_path())
            if (this_import_dir / "experiments").is_dir():
                print(f"meta staged: experiments at "
                      f"{this_import_dir / 'experiments'} — move into "
                      f"~/.maro/experiments/ if wanted", file=sys.stderr)
        else:
            print("note: v1 archive (no meta/) — no provenance record",
                  file=sys.stderr)

        print(f"Done: {extracted} files (+{other} dirs/links, "
              f"{staged_count} meta staged) extracted to {ws}",
              file=sys.stderr)
        return extracted


def _apply_user_config(import_dir: Path, prov, apply_meta: bool,
                       real_cfg: Path) -> None:
    """Place THIS import's user config, gated on provenance and validated
    against the current archive (review of c257a48: apply must not place a
    stale file from a prior import, nor follow a symlinked destination)."""
    staged_cfg = import_dir / "user-config.yml"
    if not staged_cfg.exists():
        return
    # Only apply what THIS archive actually declared.
    declared = bool(prov and prov["meta"].get("user_config"))
    if not declared:
        print("note: staged user config is not declared by this archive's "
              "provenance — not applying", file=sys.stderr)
        return
    if not apply_meta:
        print(f"meta staged (behavior not applied): user config at "
              f"{staged_cfg} — re-run with --apply-meta or place it "
              f"manually", file=sys.stderr)
        return
    # Refuse a symlinked destination (writing through it escapes the tier).
    if real_cfg.is_symlink() or (os.path.lexists(real_cfg)
                                 and not real_cfg.is_file()):
        print(f"--apply-meta: refusing — {real_cfg} exists and is not a "
              f"regular file (symlink/special); place config manually",
              file=sys.stderr)
        return
    real_cfg.parent.mkdir(parents=True, exist_ok=True)
    if real_cfg.exists():
        base = real_cfg.name + time.strftime(".pre-import-%Y%m%dT%H%M%S")
        backup = real_cfg.with_name(base)
        n = 1
        while backup.exists():
            n += 1
            backup = real_cfg.with_name(f"{base}-{n}")
        real_cfg.rename(backup)
        print(f"--apply-meta: existing user config backed up to {backup}",
              file=sys.stderr)
    # Atomic install via same-dir temp.
    tmp = real_cfg.with_name(real_cfg.name + ".tmp-import")
    tmp.write_bytes(staged_cfg.read_bytes())
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(real_cfg)
    print(f"--apply-meta: user config applied to {real_cfg}", file=sys.stderr)


def _member_import_risk(member, rel: str):
    """(code, detail) — pure-string (no destination) preview of why import
    would skip a workspace member, or None if it would import. Mirrors
    _safe_workspace_member's checks minus the ws-relative bind so inspect
    can run without a target workspace. Codes are FIXED strings (bounded
    grouping); archive-authored text rides only in `detail`, which every
    printer must route through _sanitize_for_terminal (review 2026-08-13:
    a linkname embedded in the reason string reached the terminal raw)."""
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm):
        return "path traversal / absolute", rel
    if _STAGING_DIRNAME in Path(rel).parts:
        return "targets staging dir", rel
    if member.isdir() or member.isreg():
        return None
    if member.issym():
        if _classify_symlink_target(member.linkname, _rel_parts(rel)):
            return None
        return "external symlink target", f"{rel} -> {member.linkname}"
    if member.islnk():
        return "hardlink member", rel
    return "special file (fifo/device)", rel


def _as_int(value, default=0) -> int:
    """Archive-authored counter → int, defensively (review 2026-08-13:
    meta counters printed raw could carry terminal escapes)."""
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _print_skip_table(label: str, table: dict, verbose: bool,
                      file=None) -> int:
    # file=None → current sys.stdout at CALL time (a def-time default would
    # bind the original stream and dodge redirection/capture).
    file = file if file is not None else sys.stdout
    total = sum(row["count"] for row in table.values())
    if not table:
        return 0
    for code, row in sorted(table.items()):
        examples = row["examples"]
        print(f"    - {label}{code}: {row['count']}"
              + (f"  e.g. {_sanitize_for_terminal(examples[0])}"
                 if examples and (verbose or row["count"] <= 3) else ""),
              file=file)
        if verbose:
            for nm in examples:
                print(f"        {_sanitize_for_terminal(nm)}", file=file)
            if row["count"] > len(examples):
                print(f"        … {row['count'] - len(examples)} more "
                      "(names not retained)", file=file)
    return total


def inspect_archive(archive_path: Path, verbose: bool = False) -> int:
    """Read-only: report an archive's provenance, verify its shape digest,
    and preview what an import would skip. Never extracts or mutates.

    Exit codes (precedence top-down — only a CLEAN archive exits 0; review
    2026-08-13: unsafe members and malformed provenance previously still
    exited 0, which defeats look-before-you-import for scripting):
      3 = unsupported/newer format
      2 = workspace shape digest MISMATCH
      5 = provenance present but malformed (unreadable/oversized/bad shape)
      4 = unsafe members or screened meta present (import would skip them)
      1 = archive missing/unscannable
      0 = clean
    """
    if not archive_path.exists():
        print(f"Error: archive not found: {archive_path}", file=sys.stderr)
        sys.exit(1)

    # Whole-file digest first: the trust decision should be bindable to the
    # exact bytes inspected — a swap between `inspect` and `import` is
    # otherwise invisible (review 2026-08-13). Import accepts it via
    # --expect-sha256.
    sha = hashlib.sha256()
    with open(archive_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    archive_sha256 = sha.hexdigest()

    with tarfile.open(archive_path, "r:gz") as tar:
        try:
            scan = _scan_and_classify(tar)
        except _ArchiveCapExceeded as exc:
            print(f"Error: {exc} — refusing to scan further", file=sys.stderr)
            return 1
        prov, prov_status = _load_provenance(tar, scan["meta_members"])

        print(f"Archive: {archive_path}")
        print(f"  sha256: {archive_sha256}")
        print(f"    (verify at import: maro-export import --expect-sha256 "
              f"{archive_sha256[:16]}…)")
        fmt = prov["format"] if prov else None
        if prov_status == "absent":
            print("  format: v1 (no meta/provenance) — no lineage to show")
        elif prov_status != "valid":
            print(f"  provenance: MALFORMED ({prov_status}) — a v2 archive "
                  "whose lineage cannot be trusted; treat with suspicion")
        else:
            print(f"  format: v{fmt}"
                  + ("  ⚠ NEWER than this tool (v%d) — import would refuse"
                     % ARCHIVE_FORMAT if fmt > ARCHIVE_FORMAT else ""))
            print(f"  exported by {_sanitize_for_terminal(prov['exporter'])} "
                  f"at {_sanitize_for_terminal(prov['created_at'])}")
            print(f"  source: "
                  f"{_sanitize_for_terminal(prov['source'].get('workspace_root', '?'))}")
            mi = prov["meta"]
            print(f"  meta: user-config={bool(mi.get('user_config'))} "
                  f"(redactions={_as_int(mi.get('user_config_redactions'))}), "
                  f"experiments={_as_int(mi.get('experiments_files'))}, "
                  f"external-symlinks={_as_int(mi.get('external_symlinks'))}")
            print("  custody:")
            for ev in prov["custody"][:_MAX_CUSTODY_PRINT]:
                print(f"    {_sanitize_for_terminal(ev.get('event', '?'))} by "
                      f"{_sanitize_for_terminal(ev.get('by', '?'))} at "
                      f"{_sanitize_for_terminal(ev.get('at', '?'))}")
            extra = len(prov["custody"]) - _MAX_CUSTODY_PRINT
            if extra > 0:
                print(f"    … {extra} earlier event(s) not shown")

        # Verify the workspace-shape digest against the archive's own bytes.
        digest_mismatch = False
        if prov is not None:
            claimed = prov["contents"].get("workspace_shape_sha256", "")
            actual = _manifest_digest(scan["ws_entries"])
            ok = bool(claimed) and claimed == actual
            print(f"  workspace shape digest: "
                  f"{'OK' if ok else 'MISMATCH'} "
                  f"(names+sizes only — self-attested, UNSIGNED)")
            if not ok:
                print("  ⚠ archive contents do not match the exporter's "
                      "digest — corrupted, tampered, or partial.")
                digest_mismatch = True

        # Preview what import would screen. Pure-string policy only —
        # destination-dependent checks (existing-symlink containment in the
        # real workspace, merge collisions) run at import time and are NOT
        # previewed here (review 2026-08-13: say so, don't imply coverage).
        n_unsafe = sum(row["count"] for row in scan["skips"].values())
        n_meta_skip = sum(row["count"] for row in scan["meta_skips"].values())
        n_excluded = len(scan["excluded"])
        print(f"  workspace files: {len(scan['ws_entries'])}; "
              f"import would SKIP {n_unsafe} unsafe member(s), screen "
              f"{n_meta_skip} meta member(s), exclude {n_excluded} "
              f"policy-excluded file(s)")
        _print_skip_table("", scan["skips"], verbose)
        _print_skip_table("meta: ", scan["meta_skips"], verbose)
        if n_excluded:
            shown = [x for x in scan["excluded"] if x]
            print(f"    - policy-excluded (exporter never ships these): "
                  f"{n_excluded}"
                  + (f"  e.g. {_sanitize_for_terminal(shown[0])}"
                     if shown else ""))
        print("  note: destination-dependent checks (existing-symlink "
              "containment, merge collisions) run at import time against "
              "the real workspace and are not previewed here.")

        if prov_status == "valid" and fmt > ARCHIVE_FORMAT:
            return 3
        if digest_mismatch:
            return 2
        if prov_status not in ("valid", "absent"):
            return 5
        if n_unsafe or n_meta_skip:
            return 4
        return 0


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

    ins = sub.add_parser("inspect", help="Show provenance + verify digest, "
                                         "no extraction")
    ins.add_argument("archive", type=Path, help="Archive to inspect")
    ins.add_argument("--verbose", "-v", action="store_true")

    imp = sub.add_parser("import", help="Import archive")
    imp.add_argument("archive", type=Path, help="Archive to import")
    imp.add_argument("--dry-run", action="store_true", help="List contents only")
    imp.add_argument("--clean", action="store_true",
                     help="Move an existing non-empty workspace aside "
                          "(never deleted) instead of merging into it")
    imp.add_argument("--apply-meta", action="store_true",
                     help="Also place the archive's user config at the real "
                          "user-tier path (existing config backed up)")
    imp.add_argument("--expect-sha256", default="",
                     help="Refuse unless the archive's sha256 starts with "
                          "this hex prefix (>=16 chars; printed by inspect) "
                          "— binds the import to the inspected bytes")
    imp.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.command == "export":
        export_workspace(output_path=args.output, verbose=args.verbose)
    elif args.command == "inspect":
        sys.exit(inspect_archive(args.archive, verbose=args.verbose))
    elif args.command == "import":
        import_workspace(args.archive, dry_run=args.dry_run,
                         verbose=args.verbose, clean=args.clean,
                         apply_meta=args.apply_meta,
                         expect_sha256=args.expect_sha256)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
