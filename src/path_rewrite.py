"""Rewrite machine-embedded absolute paths when workspace data changes host.

A workspace is full of absolute paths that name the machine that produced
it — 44,941 occurrences of `/home/clawd/.maro/workspace` across 5,841
text files in the 2026-08-13 box copy alone. Copy that workspace to
another install and every one of them is a lie: lessons cite files that
do not exist, run metadata points at nothing, checkpoints name a home
directory belonging to someone else. The archive lane carries the bytes
perfectly and still hands you data that only made sense on the box.

This module is the transform that fixes that, kept deliberately small and
shared, because the same defect lives in every lane that moves workspace
data between installs (`maro-export import`, `maro-import --source`).

**Shape (b) of the three sketched in BACKLOG** (Jeremy filed this
2026-08-13 with his worry on record: *"I'm a little concerned that's
setting us up for troublesome bugs in the future if we don't go
there"*): rewrite at IMPORT time, text files only, recorded in custody.
The archive stays a byte-faithful copy of the source — reconciliation
against the source machine still works, and re-importing with the
rewrite disabled reproduces the original exactly. Only the extracted
copy is transformed, and every transformed file is named in a durable
record.

What it deliberately does NOT do, each trap pre-documented rather than
learned live:

* **Content-addressed stores are never rewritten.** Anything under a
  `.git`/`.hg`/`.svn` component is skipped whole. Rewriting bytes in
  `.git/objects/**` corrupts the object — the name IS the hash of the
  content — and archived project repos travel inside the workspace.
* **Binaries and sqlite are never rewritten.** A path lives INSIDE a
  database page; regexing a `.db` corrupts it. Screened twice: by
  suffix, and by a NUL sniff over the first 8 KiB that catches every
  unknown-extension binary (a sqlite header trips it in the first 16
  bytes).
* **Bare `$HOME` is not a root.** Only roots the source recorded about
  ITSELF (`workspace_root`, `maro_user_dir`, `repo_root`) are rewritten.
  `/home/clawd/claude/ledger-kata` stays as it is: a stale absolute path
  is visible and inert, while a confidently-wrong one resolves to a real
  local directory that is not the one meant. Under-rewriting is the safe
  direction and this errs that way on purpose.
* **A recorded root is untrusted input.** It arrives in an archive
  written by another machine, possibly another person. A source root of
  `/` or `/usr` would rewrite the world, so roots are validated (absolute,
  >= 2 components, not a system directory) and a bad one is dropped with
  a reason, never applied.

Known residual, stated because it cannot be detected here: a digest
recorded INSIDE the workspace over the content of a rewritten file goes
stale. Nothing in Maro verifies such a digest today, and the archive's
own `workspace_shape_sha256` covers names+sizes read from the archive
members, so import verification is unaffected. The per-file record this
module produces is what makes any future staleness auditable.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# Roles a source install records about itself, longest-lived name first.
# Adding a role here is enough to make it travel; both lanes iterate this.
ROLES = ("workspace_root", "maro_user_dir", "repo_root")

_SNIFF_BYTES = 8192
_DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024

# Whole directories whose contents are content-addressed or otherwise
# byte-sensitive. Matched as a PATH COMPONENT, at any depth: archived
# project repos live under runs/ and projects/.
_SKIP_DIR_PARTS = frozenset({".git", ".hg", ".svn"})

# Belt to the NUL sniff's braces. Extension screens are fast and catch
# the case where a file's first 8 KiB happen to be NUL-free.
_SKIP_SUFFIXES = frozenset({
    # databases (paths live inside pages — never regex these)
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    # compressed / archived
    ".gz", ".tgz", ".bz2", ".xz", ".zst", ".zip", ".tar", ".7z", ".rar",
    # images / media / documents
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".pdf", ".mp3", ".mp4", ".wav", ".mov", ".avi", ".webm", ".ogg",
    # compiled / packed
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a", ".class",
    ".jar", ".whl", ".bin", ".dat", ".parquet", ".npy", ".npz", ".pkl",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
})

# A single-component root is a system directory, not an install root —
# refuse rather than rewrite half the filesystem. Structure plus the
# denylist below is the whole guard; a character-count minimum was tried
# and dropped, because it adds nothing the two of them miss and it
# rejects legitimate short roots like /srv/ws.
_MIN_ROOT_COMPONENTS = 2
_SYSTEM_ROOTS = frozenset({
    "/", "/usr", "/etc", "/var", "/tmp", "/opt", "/bin", "/sbin", "/lib",
    "/lib64", "/boot", "/dev", "/proc", "/sys", "/run", "/mnt", "/media",
    "/home", "/root", "/Users", "/Applications", "/System", "/Library",
    "/private", "/private/tmp", "/private/var", "/Volumes", "/srv",
})

# A match must not be followed by a character that would make it a
# PREFIX of a different name. `/home/clawd/.maro` must not fire inside
# `/home/clawd/.maro-acceptance-probe`. `.` is deliberately allowed so
# prose like "recorded at <root>." rewrites — a trailing-dot sibling
# directory resolves nowhere either way, while unrewritten prose is
# exactly the reader-facing lie this module exists to fix.
_BOUNDARY = rb"(?![A-Za-z0-9_-])"


class BadRoot(ValueError):
    """A recorded root that must never be used as a rewrite source."""


def validate_root(value) -> str:
    """Normalize a recorded root, or raise BadRoot with the reason.

    The value comes from an archive written elsewhere; treat it as
    hostile. Fails closed — the caller drops the mapping and says so.
    """
    if not isinstance(value, str):
        raise BadRoot(f"not a string ({type(value).__name__})")
    root = value.strip()
    if not root:
        raise BadRoot("empty")
    if "\x00" in root:
        raise BadRoot("contains NUL")
    if not root.startswith("/"):
        raise BadRoot(f"not absolute: {root!r}")
    # Collapse `//` and any trailing slash; keep it purely lexical (the
    # path names a directory on a machine we do not have).
    root = re.sub(r"/+", "/", root).rstrip("/")
    if not root:
        raise BadRoot("resolves to filesystem root")
    if root in _SYSTEM_ROOTS:
        raise BadRoot(f"system directory: {root}")
    if len([p for p in root.split("/") if p]) < _MIN_ROOT_COMPONENTS:
        raise BadRoot(f"too shallow: {root}")
    return root


@dataclass(frozen=True)
class RewriteMap:
    """An ordered, validated set of source→destination root rewrites."""

    pairs: tuple = ()               # ((source, dest), ...) longest source first
    rejected: tuple = ()            # ((role, value, reason), ...)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def matcher(self):
        """A single compiled pattern over all sources (longest first).

        ONE pass, not one pass per pair: sequential passes let a
        destination that happens to contain a later source string be
        rewritten a second time. A single alternation cannot re-enter
        text it has already emitted.
        """
        if not self.pairs:
            return None
        alt = b"|".join(re.escape(s.encode("utf-8")) for s, _ in self.pairs)
        return re.compile(b"(?:" + alt + b")" + _BOUNDARY)

    def substitute(self, data: bytes) -> tuple:
        """(rewritten_bytes, replacements)."""
        pattern = self.matcher()
        if pattern is None:
            return data, 0
        table = {s.encode("utf-8"): d.encode("utf-8") for s, d in self.pairs}
        count = 0

        def _repl(m):
            nonlocal count
            count += 1
            return table[m.group(0)]

        return pattern.sub(_repl, data), count

    def substitute_text(self, text: str) -> tuple:
        """(rewritten_text, replacements) for callers holding str.

        The live-workspace merge lane rewrites ledger LINES before its
        exact-line dedup, so it never touches a file at all.
        """
        out, n = self.substitute(text.encode("utf-8"))
        return out.decode("utf-8"), n

    def describe(self) -> list:
        return [{"from": s, "to": d} for s, d in self.pairs]


def build_map(source_roots: Mapping, dest_roots: Mapping,
              roles: Sequence = ROLES) -> RewriteMap:
    """Pair each role's recorded source root with this install's own.

    A role is dropped — with a recorded reason, never silently — when the
    source did not record it, when either side fails validation, or when
    the two sides are identical (importing on the machine that exported).
    """
    pairs: list = []
    rejected: list = []
    for role in roles:
        raw_src = (source_roots or {}).get(role)
        raw_dst = (dest_roots or {}).get(role)
        if raw_src in (None, ""):
            continue                      # not recorded — nothing to map
        if raw_dst in (None, ""):
            rejected.append((role, str(raw_src), "no local counterpart"))
            continue
        try:
            src = validate_root(raw_src)
        except BadRoot as exc:
            rejected.append((role, str(raw_src)[:200], f"source {exc}"))
            continue
        try:
            dst = validate_root(raw_dst)
        except BadRoot as exc:
            rejected.append((role, str(raw_dst)[:200], f"destination {exc}"))
            continue
        if src == dst:
            continue                      # same machine — a no-op, not a fault
        pairs.append((src, dst))

    # Longest source first so a nested root (…/.maro/workspace) is
    # consumed before its parent (…/.maro) can claim the prefix.
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    # Two roles can record the same source (a workspace pinned at the
    # repo). Keep the first — the longest, already ordered.
    seen: set = set()
    deduped: list = []
    for src, dst in pairs:
        if src in seen:
            continue
        seen.add(src)
        deduped.append((src, dst))
    return RewriteMap(pairs=tuple(deduped), rejected=tuple(rejected))


def skip_reason(rel_path: str, abs_path: Path) -> str:
    """Why this file must not be rewritten, or "" if it may be.

    Path-shaped screens only; the content sniff lives in rewrite_file
    because it needs the bytes.
    """
    parts = Path(rel_path).parts
    if any(p in _SKIP_DIR_PARTS for p in parts):
        return "vcs-internal"
    if Path(rel_path).suffix.lower() in _SKIP_SUFFIXES:
        return "binary-suffix"
    try:
        st = os.lstat(abs_path)
    except OSError:
        return "missing"
    if os.path.islink(abs_path):
        return "symlink"          # never follow: the target may be outside
    if not os.path.isfile(abs_path):
        return "not-a-file"
    if st.st_size == 0:
        return "empty"
    return ""


def rewrite_file(abs_path: Path, mapping: RewriteMap, *,
                 max_bytes: int = _DEFAULT_MAX_FILE_BYTES) -> tuple:
    """(status, replacements). Rewrites in place, atomically.

    status ∈ {rewritten, unchanged, binary, oversize, unreadable}.
    Mode and mtime are preserved: the content is the source machine's
    content, and reconciliation against the source reads both.
    """
    try:
        size = abs_path.stat().st_size
    except OSError:
        return "unreadable", 0
    if size > max_bytes:
        return "oversize", 0
    try:
        with open(abs_path, "rb") as fh:
            head = fh.read(_SNIFF_BYTES)
            if b"\x00" in head:
                return "binary", 0
            data = head + fh.read()
    except OSError:
        return "unreadable", 0

    new, count = mapping.substitute(data)
    if not count:
        return "unchanged", 0

    st = abs_path.stat()
    tmp = abs_path.with_name(abs_path.name + ".maro-rewrite.tmp")
    try:
        with open(tmp, "wb") as out:
            out.write(new)
        shutil.copymode(abs_path, tmp)
        os.replace(tmp, abs_path)
        os.utime(abs_path, (st.st_atime, st.st_mtime))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return "unreadable", 0
    return "rewritten", count


@dataclass
class RewriteReport:
    """What the pass did — printed in summary, recorded in full."""

    mapping: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    files_scanned: int = 0
    files_rewritten: int = 0
    replacements: int = 0
    skipped: dict = field(default_factory=dict)
    files: list = field(default_factory=list)     # every file touched

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def as_record(self) -> dict:
        return {
            "mapping": self.mapping,
            "rejected_roots": self.rejected,
            "files_scanned": self.files_scanned,
            "files_rewritten": self.files_rewritten,
            "replacements": self.replacements,
            "skipped": dict(sorted(self.skipped.items())),
            "files": self.files,
        }

    def summary(self) -> str:
        if not self.mapping:
            return "path rewrite: nothing to map (no differing roots)"
        head = (f"path rewrite: {self.files_rewritten} file(s), "
                f"{self.replacements} occurrence(s)")
        skips = ", ".join(f"{k}={v}" for k, v in sorted(self.skipped.items()))
        return head + (f" — not rewritten: {skips}" if skips else "")


def rewrite_tree(root: Path, rel_names: Iterable, mapping: RewriteMap, *,
                 max_bytes: int = _DEFAULT_MAX_FILE_BYTES) -> RewriteReport:
    """Apply `mapping` to the named files under `root`.

    Takes an explicit file list rather than walking: on a MERGE import
    only the files THIS import wrote may be touched, and a walk cannot
    tell those from the ones already there.
    """
    report = RewriteReport(mapping=mapping.describe(),
                           rejected=[{"role": r, "value": v, "reason": why}
                                     for r, v, why in mapping.rejected])
    if not mapping:
        return report

    root_resolved = root.resolve()
    for rel in rel_names:
        abs_path = root / rel
        # Containment: the caller's list is derived from archive member
        # names, which are untrusted even after the extractor's screens.
        try:
            abs_path.resolve().relative_to(root_resolved)
        except (ValueError, OSError):
            report._skip("outside-root")
            continue
        reason = skip_reason(str(rel), abs_path)
        if reason:
            report._skip(reason)
            continue
        report.files_scanned += 1
        status, count = rewrite_file(abs_path, mapping, max_bytes=max_bytes)
        if status == "rewritten":
            report.files_rewritten += 1
            report.replacements += count
            report.files.append({"path": str(rel), "replacements": count})
        elif status != "unchanged":
            report._skip(status)
    return report
