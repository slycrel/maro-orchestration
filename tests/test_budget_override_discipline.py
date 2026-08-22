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
  (``max_chars``, ``max_chars_per_entry``, ``max_len``, ``max_length``),
  keyed ``file::func::kwarg=value`` with an occurrence count and a REQUIRED
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

Named v1 scope edges (documented gaps, not oversights — review 2026-08-21):

- Count caps (``max_results``, ``max_lessons``, ``max_entries``, ...) are a
  sibling family with a different starvation shape; wants its own measured
  pass.
- Positional ``clip(text, N)`` literals are NOT scanned. ``clip()`` is the
  sanctioned marked-cut mechanism, but nothing here polices the VALUE — a
  future ``clip(x, 50)`` lands unflagged. Policing it means adjudicating
  ~150 existing call sites; that is its own tranche, tracked in BACKLOG
  under the truncation-audit arc.
- A budget kwarg bound to a NAME (``max_chars=_CAP``) is invisible to the
  literal scan — one-assignment evasion. Same follow-up tranche.
"""
import ast
import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY_PATH = (Path(__file__).resolve().parent / "data"
                 / "budget_override_registry.json")

BUDGET_KWARGS = {"max_chars", "max_chars_per_entry", "max_len", "max_length"}


def _func_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return "<call>"


def _scan(src_root: Path = None) -> Counter:
    """Census literal budget-kwarg overrides under src_root (default: src/).

    rglob, not glob — a subpackage module must not dodge the census.
    """
    root = src_root if src_root is not None else REPO / "src"
    hits: Counter = Counter()
    for p in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        rel = p.relative_to(root.parent).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (kw.arg in BUDGET_KWARGS
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, int)
                        and not isinstance(kw.value.value, bool)):
                    key = (f"{rel}::{_func_name(node)}"
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


def test_scanner_detects_a_new_override(tmp_path):
    """Negative control (mutation-from-file discipline): a guard that
    cannot fail is worse than no guard. Plant an unregistered override in a
    real file tree and run the REAL ``_scan()`` — not a hand-copied
    predicate (review 2026-08-21: the v1 control tested ast-walking in the
    abstract and would have stayed green through a ``_scan()`` regression).
    A nested package dir proves the rglob sees subpackages too.
    """
    src = tmp_path / "src"
    pkg = src / "subpkg"
    pkg.mkdir(parents=True)
    (src / "top.py").write_text(
        "def f(x):\n    return render(x, max_chars=600)\n")
    (pkg / "deep.py").write_text(
        "def g(x):\n    return shape(x, max_len=77)\n")
    (src / "clean.py").write_text(
        "def h(x, *, max_chars=1200):\n    return x[:max_chars]\n")

    hits = _scan(src)
    assert hits == Counter({
        "src/top.py::render::max_chars=600": 1,
        "src/subpkg/deep.py::shape::max_len=77": 1,
    }), f"scanner missed a planted override or over-matched: {dict(hits)}"
    # clean.py's signature DEFAULT must not be flagged — the callee owning
    # its budget is the desired state, not an override.
