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
SRC = REPO_ROOT / "src"
DOC = REPO_ROOT / "docs" / "DEFAULTS.md"

# Every census helper takes its inputs (a src root, the doc text) instead of
# reaching for the real repo. That is the whole reason the fixtures at the
# bottom of this file can exist: a tripwire wired only to live data cannot be
# shown to fire, and a guard that cannot fail is worse than no guard — it is a
# standing claim that the rule is enforced. Mutation sweep 2026-08-16 scored
# 3/14 on this file before the seam went in: `missing = []` and `dead = []`
# both survived, i.e. the census could be gutted whole with a green suite.
# Defaults are the real repo, so the live tripwires below read identically.

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


def _keys_read_by_code(src_root: Path = SRC) -> set:
    keys = set()
    # rglob, not glob (chunk-8 review): src/ has nested packages
    # (maro_assets); a config read that moves into one must stay censused.
    for path in sorted(src_root.rglob("*.py")):
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


def _missing_keys(src_root: Path = SRC, doc_text: str = None) -> list:
    """Keys read in src_root but absent from the doc — the forward census."""
    doc = DOC.read_text() if doc_text is None else doc_text
    documented = set(re.findall(r"`([a-z0-9_]+(?:\.[a-z0-9_.]+)?)`", doc))
    return sorted(k for k in _keys_read_by_code(src_root)
                  if k not in documented
                  and not any(part in documented for part in (k,)))


def _is_living(text: str) -> bool:
    return text.startswith("---\nstatus: living\n---")


def test_every_config_key_is_documented():
    missing = _missing_keys()
    assert not missing, (
        f"config keys read in src/ but absent from docs/DEFAULTS.md: {missing} "
        "— document default + why + flip effect")


def test_doc_exists_and_is_living():
    assert _is_living(DOC.read_text()), (
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

def _documented_table_keys(doc_text: str = None) -> set:
    """ALL dotted keys in the key cell (first column) of DEFAULTS.md table
    rows. Some rows document sibling keys together (`recall.guard_attempts` +
    `recall.guard_window_minutes` share a cell) — taking only the row-leading
    key let seven documented keys escape the census entirely (chunk-8 review,
    both lenses). Mirrors the forward census's dotted-key discipline —
    dotless rows are out of scope."""
    keys = set()
    doc = DOC.read_text() if doc_text is None else doc_text
    for line in doc.splitlines():
        if not line.startswith("| "):
            continue
        cell = line.split("|")[1]
        keys |= set(re.findall(r"`([a-z0-9_]+\.[a-z0-9_.]+)`", cell))
    return keys


def _src_literals_and_fstring_prefixes(src_root: Path = SRC):
    """(all string constants in src/, {file: (constants, f-string prefixes)}).

    An f-string prefix is the leading constant part of a JoinedStr whose next
    part is interpolated — the wrapper-key-construction shape."""
    all_literals = set()
    per_file = {}
    for path in sorted(src_root.rglob("*.py")):
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
        per_file[str(path.relative_to(src_root))] = (consts, prefixes)
    return all_literals, per_file


def _dead_keys(src_root: Path = SRC, doc_text: str = None) -> list:
    """Documented keys nothing in src_root reads — the reverse census."""
    ast_read = _keys_read_by_code(src_root)
    literals, per_file = _src_literals_and_fstring_prefixes(src_root)

    def _is_read(key: str) -> bool:
        if key in ast_read or key in literals:
            return True
        for consts, prefixes in per_file.values():
            for prefix in prefixes:
                if key.startswith(prefix) and key[len(prefix):] in consts:
                    return True
        return False

    return sorted(k for k in _documented_table_keys(doc_text) if not _is_read(k))


def test_every_documented_key_has_a_reader():
    dead = _dead_keys()
    assert not dead, (
        f"DEFAULTS.md documents keys nothing in src/ reads: {dead} — "
        "either the code was removed (delete the row) or the read moved "
        "behind a shape this census can't see (teach it the shape; do not "
        "add an exemption list)")


# ---------------------------------------------------------------------------
# Must-detect fixtures: proof that the two censuses above CAN fail.
#
# Added 2026-08-16 after a file-derived mutation sweep scored 3/14 on this
# file. `missing = []` and `dead = []` both survived — the whole census could
# be gutted and the suite stayed green — and the three mutations that were
# caught only fired because they broke hard enough against real repo data to
# raise a false positive. That is accidental detection, not coverage.
#
# The cause was structural, not an oversight: every helper reached for
# REPO_ROOT itself, so there was no way to hand the census a known violation.
# The seam above (src_root / doc_text parameters, defaulting to the real repo)
# exists for these tests. Each one injects one violation and asserts the
# census names it; the inverse cases pin the exemptions that keep it quiet.
#
# When you teach the census a new shape, add a fixture here in the same
# commit. A detection shape with no fixture is a claim, not a guard.
# ---------------------------------------------------------------------------

def _tree(root: Path, files: dict) -> Path:
    """Write {relative path: source} under root and return it."""
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def _doc(*rows: str) -> str:
    body = "---\nstatus: living\n---\n\n| Key | Default | Why |\n|---|---|---|\n"
    return body + "".join(f"| {r} | x | y |\n" for r in rows)


class TestTheForwardCensusCanFail:
    """One undocumented key, one detection shape, per test."""

    def test_a_plain_config_get_read_is_caught(self, tmp_path):
        src = _tree(tmp_path, {"m.py": 'get("alpha.undocumented")\n'})
        assert _missing_keys(src, _doc()) == ["alpha.undocumented"]

    def test_an_aliased_import_is_caught(self, tmp_path):
        # `from config import get as X` — the 2026-07-09 leak that motivated
        # per-file alias resolution in the first place.
        src = _tree(tmp_path, {
            "m.py": 'from config import get as _wf_cfg_get\n'
                    '_wf_cfg_get("beta.undocumented")\n'})
        assert _missing_keys(src, _doc()) == ["beta.undocumented"]

    def test_a_read_in_a_nested_package_is_caught(self, tmp_path):
        # rglob, not glob: a read that moves into src/maro_assets/ stays
        # censused.
        src = _tree(tmp_path, {
            "pkg/__init__.py": "", "pkg/deep.py": 'get("gamma.undocumented")\n'})
        assert _missing_keys(src, _doc()) == ["gamma.undocumented"]

    def test_a_named_getter_that_is_not_config_get_is_caught(self, tmp_path):
        # The _GETTERS floor: wrappers named _cfg_get/cfg_get/... read config
        # without importing `get` at all.
        src = _tree(tmp_path, {"m.py": '_cfg_get("delta.undocumented")\n'})
        assert _missing_keys(src, _doc()) == ["delta.undocumented"]

    def test_a_dotless_key_through_an_explicit_alias_is_caught(self, tmp_path):
        src = _tree(tmp_path, {"m.py": 'cfg("epsilon_undocumented")\n'})
        assert _missing_keys(src, _doc()) == ["epsilon_undocumented"]

    def test_a_documented_key_is_not_reported(self, tmp_path):
        src = _tree(tmp_path, {"m.py": 'get("alpha.documented")\n'})
        assert _missing_keys(src, _doc("`alpha.documented`")) == []

    def test_configs_own_internals_are_exempt(self, tmp_path):
        # config.py's docstring examples and internals are not reads; without
        # the exemption the census reports its own examples forever.
        src = _tree(tmp_path, {"config.py": 'get("internal.example")\n'})
        assert _missing_keys(src, _doc()) == []

    def test_a_bare_dict_get_is_not_mistaken_for_config(self, tmp_path):
        # Dotless keys through bare get()/_get() are indistinguishable from
        # ordinary dict helpers, so they stay out of scope on purpose.
        src = _tree(tmp_path, {"m.py": 'get("some_dict_key")\n'})
        assert _missing_keys(src, _doc()) == []


class TestTheReverseCensusCanFail:
    """One dead documented row, one exemption shape, per test."""

    def test_a_documented_key_nothing_reads_is_caught(self, tmp_path):
        src = _tree(tmp_path, {"m.py": "x = 1\n"})
        assert _dead_keys(src, _doc("`zeta.orphaned`")) == ["zeta.orphaned"]

    def test_a_second_key_sharing_a_row_is_still_censused(self, tmp_path):
        # Sibling keys share a cell (`recall.guard_attempts` +
        # `recall.guard_window_minutes`); taking only the row-leading key let
        # seven documented keys escape (chunk-8 review).
        src = _tree(tmp_path, {"m.py": 'get("eta.alive")\n'})
        row = "`eta.alive` + `eta.orphaned`"
        assert _dead_keys(src, _doc(row)) == ["eta.orphaned"]

    def test_a_key_read_only_as_a_bare_literal_is_alive(self, tmp_path):
        # Wrapper reads: _coerce_cap("budget.daily_usd", ...) never calls
        # config.get by a censused name, but the key is right there.
        src = _tree(tmp_path, {"m.py": '_coerce_cap("theta.wrapped", 1)\n'})
        assert _dead_keys(src, _doc("`theta.wrapped`")) == []

    def test_a_key_assembled_from_an_fstring_prefix_is_alive(self, tmp_path):
        # hosted_free._cfg builds f"validate.hosted_free.{key}" and callers
        # pass "enabled" — prefix and suffix must be matched in the SAME file.
        src = _tree(tmp_path, {
            "m.py": 'def _cfg(k):\n    return get(f"iota.pre.{k}")\n'
                    '_cfg("enabled")\n'})
        assert _dead_keys(src, _doc("`iota.pre.enabled`")) == []

    def test_the_fstring_match_does_not_cross_files(self, tmp_path):
        # Same-file is the rule: a prefix here and a suffix there is not
        # evidence of a read, and treating it as one would let dead rows hide.
        src = _tree(tmp_path, {
            "a.py": 'def _cfg(k):\n    return get(f"kappa.pre.{k}")\n',
            "b.py": 'OTHER = "enabled"\n'})
        assert _dead_keys(src, _doc("`kappa.pre.enabled`")) == ["kappa.pre.enabled"]

    def test_same_named_files_in_different_packages_do_not_shadow(self, tmp_path):
        # per_file is keyed by relative path: rglob yields many __init__.py,
        # and basename keying would silently drop every one but the last.
        src = _tree(tmp_path, {
            "a/__init__.py": 'def _cfg(k):\n    return get(f"lam.pre.{k}")\n'
                             'X = "enabled"\n',
            "b/__init__.py": "Y = 2\n"})
        assert _dead_keys(src, _doc("`lam.pre.enabled`")) == []


class TestTheLivingCheckCanFail:
    def test_a_record_header_is_rejected(self):
        assert not _is_living("---\nstatus: record\n---\n\n# Defaults\n")
        assert not _is_living("# Defaults\n")

    def test_a_living_header_is_accepted(self):
        assert _is_living("---\nstatus: living\n---\n\n# Defaults\n")
