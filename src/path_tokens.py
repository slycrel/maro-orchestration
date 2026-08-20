"""Root placeholders — make a path portable without turning it into a payload.

An exported workspace carries the source machine's absolute paths. Import used
to make them true locally by rewriting them in place (`path_rewrite`), which is
the only place in this codebase that deliberately edits historical records —
and it is incomplete: 2,029 files / 25,278 occurrences still embedded
`/home/clawd` after the last import.

Jeremy, 2026-08-18: *"on export we do some sort of replaceable like
`$MARO_ROOT/` instead, and export the root path that was there prior. Then it's
'correct' going out and not 'painful' going back in."*

So a path stays a readable, greppable string with a variable prefix:

    /home/clawd/.maro/workspace/runs/abc/build/x.json
    %%MARO_WORKSPACE%%/runs/abc/build/x.json

**Why `%%…%%` and not `$MARO_…` or `{{…}}`** — both alternatives were probed
against the live corpus and the codebase, and both collide:

  * `$MARO_WORKSPACE` already appears 30 times as a literal, and
    `$MARO_MOUNT_VIEW` 428 times (a real env var the container sets). A
    substituted prefix would be indistinguishable from source text that always
    said that, which breaks invertibility outright.
  * `{{…}}` is the escape syntax of `str.format`/`format_map`, and
    `hooks.py:_render_template` uses `format_map`. A token passing through it
    would silently become `{…}`.

`%%…%%` survives JSON, URL quote/unquote (no valid hex pair follows either
`%`), and `.format()`. Nothing in the record path applies `%`-interpolation to
a stored value.

**Invertibility is the contract.** Substitution is prefix-only and the token
cannot occur in source content (asserted at export, fail-closed), so `expand`
is an exact inverse of `substitute`. That matters because archives are
byte-faithful today and it has already paid off — the flat lesson ledger was
restored 466/466 rows straight out of an archive.

**What must NOT be substituted.** A path we *wrote*, naming our own data, is
`owned` and portable. A path we *observed* — a scavenge hit, a write-fence
violation — is evidence, and the absolute string is the finding. Substituting
one destroys it. This module provides the transform; the caller owns that
distinction (see `docs/PATH_PORTABILITY_DESIGN.md`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# role -> placeholder. Adding a role here is enough to make it travel.
TOKENS: Dict[str, str] = {
    "workspace_root": "%%MARO_WORKSPACE%%",
    "maro_user_dir": "%%MARO_HOME%%",
    "repo_root": "%%MARO_REPO%%",
    "scratch_root": "%%MARO_SCRATCH%%",   # host scratch_dir <-> container /tmp
    "clone_root": "%%MARO_CLONE%%",       # self-dev scratch clone (maro-wt-*)
}

# Historical roots that mean the same place as a current role. The repo was
# renamed 2026-06-26; 6,150 occurrences in the corpus still name the old
# directory, which exists on neither machine. Translating them is deliberate
# (Jeremy, 2026-08-18: "ok with the translation to avoid dealing with the 'old'
# format"); the manifest records that two source roots mapped to one token, so
# which name a record originally used stays recoverable.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "repo_root": ("openclaw-orchestration",),
}


class TokenCollision(RuntimeError):
    """A placeholder already occurs in the content being exported.

    Fail closed: substituting anyway would produce an archive that cannot be
    expanded back to its original bytes.
    """


class TokenMap:
    """Ordered (absolute_root, token) pairs plus their inverse.

    Substitution is many-to-one (a token may have alias roots); expansion is
    one-to-one and always yields the CANONICAL root. Without that split,
    expand() picked whichever root sorted first — which is the alias, since
    `openclaw-orchestration` is longer than `maro-orchestration` — and a
    round trip silently rewrote every repo path to a directory name that no
    longer exists.
    """

    def __init__(self, pairs: List[Tuple[str, str]],
                 canonical: Optional[Dict[str, str]] = None):
        # Longest root first: `~/.maro/workspace` is INSIDE `~/.maro`, so the
        # shorter root would otherwise claim the prefix and strand the rest.
        self.pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)
        # token -> the one root expansion restores.
        self.canonical: Dict[str, str] = dict(canonical or {})
        for root, token in self.pairs:
            self.canonical.setdefault(token, root)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    @property
    def tokens(self) -> List[str]:
        return [t for _, t in self.pairs]

    def substitute(self, data: bytes) -> Tuple[bytes, int]:
        n = 0
        for root, token in self.pairs:
            rb, tb = root.encode(), token.encode()
            if rb in data:
                n += data.count(rb)
                data = data.replace(rb, tb)
        return data, n

    def expand(self, data: bytes) -> Tuple[bytes, int]:
        n = 0
        for token, root in self.canonical.items():
            tb, rb = token.encode(), root.encode()
            if tb in data:
                n += data.count(tb)
                data = data.replace(tb, rb)
        return data, n

    def as_manifest(self) -> List[dict]:
        """What the archive records so any reader can expand or invert.

        Alias roots ride along marked, so "this record named the repo under its
        old directory name" survives the translation instead of being silently
        equated (the objection to aliasing at all).
        """
        return [{"token": t, "root": r,
                 "canonical": self.canonical.get(t) == r}
                for r, t in self.pairs]


def build_map(roots: Dict[str, str], *, aliases: bool = True,
              extra_roots: Optional[Dict[str, Iterable[str]]] = None) -> TokenMap:
    """Pair each known root with its placeholder.

    `roots` is role -> absolute path (provenance already records exactly this).
    A role with no path is skipped rather than guessed at.

    `extra_roots` adds further SOURCE spellings for a role that must also
    match -- they substitute to the same token but never become canonical.
    The caller supplies these because only it knows them: a workspace can be
    recorded resolved (`config.workspace_root()`) while the content holds the
    unresolved form the operator actually set in `MARO_WORKSPACE`, and a
    symlinked root then never matches itself. Nothing derives the unresolved
    form from the resolved one, so guessing is not an option -- it has to be
    passed in.
    """
    pairs: List[Tuple[str, str]] = []
    canonical: Dict[str, str] = {}
    for role, token in TOKENS.items():
        raw = (roots or {}).get(role)
        if not raw:
            continue
        root = str(raw).rstrip("/")
        if not root or root == "/":
            continue          # a single-component root is a system dir, not an install root
        pairs.append((root, token))
        canonical[token] = root          # the real root, never an alias
        # Symlink-resolved twin. A root can be recorded in one form while the
        # content records the other -- on macOS `/var` is a symlink to
        # `/private/var`, so a workspace under `/var/folders/...` never matched
        # its own recorded root and substitution silently did nothing. Found by
        # the live-workspace tripwire, not by reasoning. Canonical stays the
        # configured form; the twin is only an additional match source.
        try:
            real = os.path.realpath(root).rstrip("/")
            if real and real != root:
                pairs.append((real, token))
        except OSError:
            pass
        for extra in (extra_roots or {}).get(role, ()):
            e = str(extra).rstrip("/")
            if e and e != root and e != "/":
                pairs.append((e, token))
                try:
                    er = os.path.realpath(e).rstrip("/")
                    if er and er not in (e, root):
                        pairs.append((er, token))
                except OSError:
                    pass
        if aliases:
            for alt in ALIASES.get(role, ()):
                parent, name = os.path.split(root)
                if parent and name and name != alt:
                    pairs.append((os.path.join(parent, alt), token))
    return TokenMap(pairs, canonical)


def assert_no_collision(chunks: Iterable[bytes], tmap: TokenMap) -> None:
    """Raise TokenCollision if any token already occurs in the content.

    Called before an export writes anything: a collision means the transform
    would not be invertible, and shipping a non-invertible archive is worse
    than not shipping one.
    """
    toks = [t.encode() for t in tmap.tokens]
    for data in chunks:
        for t in toks:
            if t in data:
                raise TokenCollision(
                    f"{t.decode()} already occurs in the content being exported "
                    "— substitution would not be invertible")


# --------------------------------------------------------------------------
# The reader side. Q4 (Jeremy): "let's use a method if possible rather than
# custom code in those places" — every consumer that resolves a stored path
# back to a file calls resolve_stored_path, never its own expansion.
# --------------------------------------------------------------------------

_local: Optional[TokenMap] = None


def local_map() -> TokenMap:
    """This install's own roots, for expanding a stored path on read."""
    global _local
    if _local is None:
        roots = {}
        try:
            from config import workspace_root
            roots["workspace_root"] = str(workspace_root())
        except Exception:
            pass
        try:
            from runs import _maro_dir  # type: ignore
            roots["maro_user_dir"] = str(_maro_dir())
        except Exception:
            pass
        try:
            roots["repo_root"] = str(Path(__file__).resolve().parent.parent)
        except Exception:
            pass
        _local = build_map(roots, aliases=False)   # never write an alias back out
    return _local


def has_token(value) -> bool:
    s = str(value)
    return any(t in s for t in TOKENS.values())


def resolve_stored_path(value) -> Path:
    """Expand a stored path against THIS install's roots and return it.

    Safe on a plain absolute path (returns it unchanged), so consumers can call
    it unconditionally rather than testing first.
    """
    s = str(value)
    if not has_token(s):
        return Path(s)
    out, _ = local_map().expand(s.encode())
    return Path(out.decode("utf-8", errors="surrogateescape"))


def reset_local_map_cache() -> None:
    """Tests move the workspace root between cases."""
    global _local
    _local = None
