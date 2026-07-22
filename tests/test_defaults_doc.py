"""Census tripwire: every config key the code reads must appear in docs/DEFAULTS.md.

docs/DEFAULTS.md documents each key's default, the reasoning behind it, and
flip consequences — for clean-room discovery (Jeremy, 2026-07-08). A registry
doc rots the moment a key ships undocumented, so this walks src/ with the AST
and diffs against the doc. When this fails: add the key to DEFAULTS.md with
its why/flip-effect (or, if a key was removed from code, delete its row).
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "DEFAULTS.md"

# Function names that read config (config.get + its import aliases in src/).
# "_get" added 2026-07-08: run-visibility shipped `from config import get as
# _get` and the census missed all three of its keys. 2026-07-09: the fixed
# name set itself proved leaky (`_wf_cfg_get`, `_budget_get` — the write-fence
# and budget-cap keys shipped undocumented) — the census now ALSO resolves
# `from config import get as X` aliases per file via the AST, so any alias
# spelling is caught automatically. This set remains as the floor.
_GETTERS = {"get", "_get", "config_get", "_cfg_get", "cfg_get", "_config_get", "cfg"}

# Dotless keys are indistinguishable from dict lookups by name alone, so only
# the explicit config aliases count for them; bare get()/_get() (common dict
# helper names too) need a dotted key.
_DOTLESS_GETTERS = _GETTERS - {"get", "_get"}


def _config_get_aliases(tree: ast.AST) -> set:
    """Names bound to config.get in this module via `from config import get as X`."""
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "config":
            for alias in node.names:
                if alias.name == "get":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _keys_read_by_code() -> set:
    keys = set()
    # rglob, not glob (chunk-8 review): src/ has nested packages
    # (maro_assets); a config read that moves into one must stay censused.
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        file_getters = _GETTERS | _config_get_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in file_getters:
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            key = node.args[0].value
            if not isinstance(key, str) or not key or not key[0].isalpha():
                continue
            if path.name == "config.py":
                continue  # docstring examples / internals, not reads
            if "." in key:
                keys.add(key)
            elif node.func.id in _DOTLESS_GETTERS and "_" in key:
                keys.add(key)
    return keys


def test_every_config_key_is_documented():
    doc = DOC.read_text()
    documented = set(re.findall(r"`([a-z0-9_]+(?:\.[a-z0-9_.]+)?)`", doc))
    missing = sorted(k for k in _keys_read_by_code()
                     if k not in documented
                     and not any(part in documented for part in (k,)))
    assert not missing, (
        f"config keys read in src/ but absent from docs/DEFAULTS.md: {missing} "
        "— document default + why + flip effect")


def test_doc_exists_and_is_living():
    text = DOC.read_text()
    assert text.startswith("---\nstatus: living\n---"), (
        "DEFAULTS.md must carry living frontmatter — it is a registry, not a record")


# ---------------------------------------------------------------------------
# Reverse census (swarm-review chunk 8, the enforcement pin): every documented
# key must have a reader in src/. The forward census stops keys shipping
# undocumented; this direction stops rows outliving their code — a documented
# flag nothing reads is rot that misleads clean-room discovery.
#
# "Read" is established mechanically, no hand-maintained exemption list (the
# checkpoint's warning: an exemption registry is itself a rot list):
#   1. the AST census found a direct config.get()/alias read, or
#   2. the full dotted key appears as a string literal anywhere in src/
#      (wrapper reads: _coerce_cap("budget.daily_usd", ...),
#      notify_telegram._cfg("notify.viewer_url", ...)), or
#   3. some file f-string-constructs keys from a constant prefix and the
#      key's remaining suffix appears as a literal in that same file
#      (hosted_free._cfg builds f"validate.hosted_free.{key}" and call
#      sites pass "enabled" / "max_latency_ms").
# This is a pytest census, not a run-once script, on purpose — the 05-12
# md-claims census ran once and died; a suite test cannot die silently.
# ---------------------------------------------------------------------------

def _documented_table_keys() -> set:
    """ALL dotted keys in the key cell (first column) of DEFAULTS.md table
    rows. Some rows document sibling keys together (`recall.guard_attempts` +
    `recall.guard_window_minutes` share a cell) — taking only the row-leading
    key let seven documented keys escape the census entirely (chunk-8 review,
    both lenses). Mirrors the forward census's dotted-key discipline —
    dotless rows are out of scope."""
    keys = set()
    for line in DOC.read_text().splitlines():
        if not line.startswith("| "):
            continue
        cell = line.split("|")[1]
        keys |= set(re.findall(r"`([a-z0-9_]+\.[a-z0-9_.]+)`", cell))
    return keys


def _src_literals_and_fstring_prefixes():
    """(all string constants in src/, {file: (constants, f-string prefixes)}).

    An f-string prefix is the leading constant part of a JoinedStr whose next
    part is interpolated — the wrapper-key-construction shape."""
    all_literals = set()
    per_file = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        consts = set()
        prefixes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                consts.add(node.value)
            elif isinstance(node, ast.JoinedStr) and len(node.values) >= 2:
                first = node.values[0]
                if (isinstance(first, ast.Constant)
                        and isinstance(first.value, str) and first.value):
                    prefixes.add(first.value)
        all_literals |= consts
        # relative path, not basename — rglob can yield duplicate basenames
        # (__init__.py) and a basename key would silently drop a file's scan
        per_file[str(path.relative_to(REPO_ROOT))] = (consts, prefixes)
    return all_literals, per_file


def test_every_documented_key_has_a_reader():
    ast_read = _keys_read_by_code()
    literals, per_file = _src_literals_and_fstring_prefixes()

    def _is_read(key: str) -> bool:
        if key in ast_read or key in literals:
            return True
        for consts, prefixes in per_file.values():
            for prefix in prefixes:
                if key.startswith(prefix) and key[len(prefix):] in consts:
                    return True
        return False

    dead = sorted(k for k in _documented_table_keys() if not _is_read(k))
    assert not dead, (
        f"DEFAULTS.md documents keys nothing in src/ reads: {dead} — "
        "either the code was removed (delete the row) or the read moved "
        "behind a shape this census can't see (teach it the shape; do not "
        "add an exemption list)")
