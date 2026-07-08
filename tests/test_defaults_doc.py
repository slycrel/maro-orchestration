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
_GETTERS = {"get", "config_get", "_cfg_get", "cfg_get", "_config_get", "cfg"}

# Dotless keys are indistinguishable from dict lookups by name alone, so only
# the explicit config aliases count for them; bare get() needs a dotted key.
_DOTLESS_GETTERS = _GETTERS - {"get"}


def _keys_read_by_code() -> set:
    keys = set()
    for path in sorted((REPO_ROOT / "src").glob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in _GETTERS:
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
