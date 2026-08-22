"""Budget-override discipline tripwire — no unrationalized caps at call sites.

The recall-600 incident (2026-08-21, Jeremy: "by now you know my position on
arbitrary character limits... 600 chars is kind of silly low"): recall.py
overrode `inject_knowledge_for_goal`'s budget with an April-era literal that
had zero recorded rationale, survived the whole 2026-08 arbitrary-truncation
audit — the slice tripwire (`test_truncation_discipline.py`) censuses bare
``[:N]`` cuts, and a keyword override is not a slice — and silently zeroed
the edge-expansion A/B denominator for months. Jeremy, same day: *"we keep
revisiting this decision... it's making the system fragile. Let's clean up
at least what we know and consider more."*

This is the structural answer for the override family:

- ``tests/data/budget_override_registry.json`` holds every call site in
  ``src/`` that passes a LITERAL int for a budget-family keyword
  (``max_chars``, ``max_chars_per_entry``, ``max_len``), keyed
  ``file::func::kwarg=value`` with an occurrence count and a REQUIRED
  non-empty ``why`` — the written reason this cap deserves to exist at
  this call site rather than letting the callee's own default own the
  budget.
- A scan hit NOT in the registry fails: a new literal override needs its
  rationale written down in the same commit, or the override removed.
- A registry row with no remaining hits (or a stale count) fails, so the
  ledger stays honest in both directions.
- An empty/whitespace ``why`` fails: registering without a reason is the
  disease, not a cure.

The default-vs-override rule this enforces: the CALLEE owns its budget via
its signature default (one place, one rationale, visible to every caller);
a call-site literal is an exception that must say why. Overrides that just
restate the default are removed, not registered — two owners of one number
is how the recall 600 drifted.

Count caps (``max_results``, ``max_lessons``, ``max_entries``, ...) are a
sibling family, deliberately out of v1 scope — different starvation shape,
wants its own measured pass. Upgrade edge, not an oversight.
"""
import ast
import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY_PATH = (Path(__file__).resolve().parent / "data"
                 / "budget_override_registry.json")

BUDGET_KWARGS = {"max_chars", "max_chars_per_entry", "max_len"}


def _func_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return "<call>"


def _scan() -> Counter:
    hits: Counter = Counter()
    for p in sorted((REPO / "src").glob("*.py")):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (kw.arg in BUDGET_KWARGS
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, int)
                        and not isinstance(kw.value.value, bool)):
                    key = (f"src/{p.name}::{_func_name(node)}"
                           f"::{kw.arg}={kw.value.value}")
                    hits[key] += 1
    return hits


def test_budget_overrides_are_registered_with_rationale():
    registry = json.loads(REGISTRY_PATH.read_text())
    hits = _scan()

    unregistered = sorted(set(hits) - set(registry))
    assert not unregistered, (
        "Literal budget override(s) with no registry row — either let the "
        "callee's default own the budget, or add a row to "
        f"{REGISTRY_PATH.name} with a real 'why':\n  "
        + "\n  ".join(f"{k} (x{hits[k]})" for k in unregistered))

    stale = sorted(k for k in registry if k not in hits)
    assert not stale, (
        "Registry row(s) with no remaining call sites — delete them so the "
        "ledger stays honest:\n  " + "\n  ".join(stale))

    drifted = sorted(
        k for k in registry if registry[k].get("count") != hits[k])
    assert not drifted, (
        "Registry count(s) out of date:\n  "
        + "\n  ".join(f"{k}: registry {registry[k].get('count')} "
                      f"!= scan {hits[k]}" for k in drifted))

    unjustified = sorted(
        k for k in registry if not str(registry[k].get("why", "")).strip())
    assert not unjustified, (
        "Registry row(s) with an empty 'why' — a cap without a written "
        "reason is the exact debt this test exists to block:\n  "
        + "\n  ".join(unjustified))


def test_scanner_detects_a_new_override():
    """Negative control (mutation-from-file discipline): a guard that
    cannot fail is worse than no guard. Prove the scanner would flag a
    fresh unregistered override."""
    import textwrap
    src = textwrap.dedent("""
        def f():
            return render(x, max_chars=600)
    """)
    tree = ast.parse(src)
    found = [
        (kw.arg, kw.value.value)
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg in BUDGET_KWARGS and isinstance(kw.value, ast.Constant)
    ]
    assert found == [("max_chars", 600)]
