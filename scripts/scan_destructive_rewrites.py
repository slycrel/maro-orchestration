#!/usr/bin/env python3
"""Find per-line rewrites that DROP what they cannot parse.

The distinction this hunts is not silence — `tests/test_no_silent_drop.py`
owns that — it is DURABILITY. A row dropped from a read is recoverable:
the bytes are still on disk. A row dropped from a loop whose result is
written back is gone. That shape (drop + write-back) is the destructive
subset of the census debt, and it is what took skill-stats.jsonl from 4
lines to 1 and wiped a knowledge tier before it was found.

Reported shapes
---------------
RISK  a function that FRAMES a store into lines — `.splitlines()` or
      `.split("\n")` — and participates in a write-back: directly, via a
      lexically enclosing function, or via a SAME-MODULE helper it calls
      by name. It does NOT check that a drop is present; framing plus a
      write-back is the whole signal, which is why most hits are benign.
OK    the same, but a taint-refusing parse is applied on the drop path.

The call-graph leg exists because the scanner's first version could not
see the very shape the skills.py fix introduced: `_read_skill_stats`
(splitlines, no write) + `_write_skill_stats` (write, no splitlines) +
`record_skill_outcome` (calls both, contains neither). Three adversarial
lenses caught that independently — a scanner whose "found 0" cannot be
trusted is worse than no scanner. Fixtures in
`tests/test_scan_destructive_rewrites.py` pin that it can still find.

Limits, stated so nobody reads more off a clean run than it carries:
  * OK is a HINT, not a proof. It means a taint-refusing parse appears on
    the drop path, not that every unparseable line is provably carried.
    Read the function.
  * Cross-MODULE helpers are not followed.
  * `.split("\n")` counts as framing since 2026-08-20. It has to: this arc
    CONVERTED the sites it hardened from `splitlines()` to `split("\n")`
    (splitlines also breaks on U+2028/U+2029, which are legal inside a JSON
    string), which walked every one of them out of the scanner's field of
    view. Adversarial r3 proved it: reverting `interrupt.poll` to the exact
    destructive shape this arc removed produced ZERO hits, and the drift
    gate's `regressed` check — whose whole job is to catch that — could
    never fire. A fix that blinds the detector to its own subject is a
    worse outcome than the bug.
  * Markdown/single-object rewrites match the write markers and show up
    as RISK; they are usually false positives. Triage by reading.
  * No drop is required for a RISK. An earlier version of this docstring
    said "drops on a parse failure" was part of the test; it never was,
    and the 64-of-70 false-positive rate is the direct consequence
    (adversarial r3, 2026-08-20 — the claim had no executing line).
"""
from __future__ import annotations

import ast
import pathlib
import sys

_PARSERS: "tuple[set[str], set[str]]" = (set(), set())

WRITE_MARKERS = ("atomic_write", "write_text", "write_bytes", "locked_rmw",
                 "locked_append", "locked_write")
CLEAN_MARKERS = ("loads_clean", "_loads_clean")



def _parser_names(tree) -> "tuple[set[str], set[str]]":
    """(names proven to be the clean wrapper, names proven to be a raw parser).

    Adversarial r7 (2026-08-20, 5/5) killed r6's spelling-based version four
    ways in one round: `from json import loads as parse` was invisible
    (`parse` is neither `loads` nor `*_loads`), `parse_json = json.loads`
    likewise, and — the other direction — `from json import loads as
    _loads_clean` was TRUSTED, because the marker list matched the name and
    nothing checked where it came from. A verdict about safety cannot be
    read off an identifier. It comes from the binding:

        from jsonl_utils import loads_clean [as X]   -> X is clean
        import jsonl_utils [as M]                    -> M.loads_clean is clean
        X = loads_clean                              -> X is clean
        from json import loads [as X]                -> X is raw
        X = json.loads / X = J.loads                 -> X is raw
        import json [as J]                           -> J.loads is raw

    Anything unresolved stays unproven, and unproven parses do not earn OK.

    r7 kept a fallback for the conventional SPELLING (`loads_clean(...)`
    with no visible import earned OK), added so the round's own fixtures
    would pass. Adversarial r8 (3 lenses, probed) walked in through it:
    `from untrusted_parser import loads_clean` was trusted on the name
    alone. A fallback added under the pressure of a finding is exactly the
    shape the previous six rounds keep finding, and this one was in the
    docstring's own blind spot — the sentence above says unproven parses do
    not earn OK, and the fallback said otherwise. It is gone; every real
    caller in this repo imports the wrapper, and the scan proves it (77 RISK
    sites before and after).
    """
    clean, raw, json_mods = set(), set(), set()
    walk = ast.walk if isinstance(tree, ast.Module) else _own_scope
    for n in walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "json":
                    json_mods.add(a.asname or "json")
                elif a.name == "jsonl_utils":
                    clean.add(f"{a.asname or a.name}.loads_clean")
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                bound = a.asname or a.name
                if n.module == "json" and a.name == "loads":
                    raw.add(bound)
                elif a.name in ("loads_clean", "_loads_clean") \
                        and n.module == "jsonl_utils":
                    clean.add(bound)
        else:
            # Every binding form, not just plain Assign (adversarial r13,
            # Minimalist, probed: `parser: object = json.loads` and
            # `(parser := json.loads)` both walked past the Assign-only
            # alias pass — r12 taught `_bindings` these forms and this
            # scan kept its own private walk).
            # ...and every binding must ALSO destructure (adversarial
            # r14, Minimalist, probed: `parser, _x = json.loads, None`
            # put a Tuple in the target slot and this walk rejected it
            # one line before _expand_binding could expose the pair —
            # the same private-copy-of-a-shared-walk shape as r13).
            for target, v in (pair for b in _bindings(n)
                              for pair in _expand_binding(*b)):
                if not isinstance(target, ast.Name):
                    continue
                bound = target.id
                if isinstance(v, ast.Name) and v.id in clean:
                    clean.add(bound)
                elif isinstance(v, ast.Attribute) and v.attr == "loads" \
                        and isinstance(v.value, ast.Name) \
                        and (v.value.id in json_mods or v.value.id == "json"):
                    raw.add(bound)
                elif isinstance(v, ast.Name) and v.id in raw:
                    raw.add(bound)
    for mod in json_mods | {"json"}:
        raw.add(f"{mod}.loads")
    return clean - raw, raw


def _own_scope(node):
    """Walk `node`, but do NOT descend into a nested scope.

    Adversarial r9 (2 lenses, probed): `ast.walk(fn)` descends into nested
    functions, so a helper defined inside `rewrite` that imports the real
    wrapper re-proved `rewrite`'s OWN parameter — which defaulted to
    `json.loads`. The scanner said OK about a function parsing every line
    with the raw parser. A scope-aware rule that reads the wrong scope is
    not scope-aware; Python's scopes are the unit, so this is the walk that
    respects them. (Nested functions are scanned as their own sites, so
    nothing stops being looked at.)
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.Lambda, ast.ClassDef)):
            continue                       # its own scope, its own proofs
        stack.extend(ast.iter_child_nodes(cur))


def _binding_census(fn) -> "dict[str, list]":
    """{name: [value per binding]} for every name bound inside `fn`.

    A binding whose value is not a proven constant is `_UNRESOLVED`, and a
    name bound more than once is unresolved by construction (see the caller).

    r7 enumerated binding NODE TYPES — Assign, AnnAssign, AugAssign,
    NamedExpr, For, With — and adversarial r8 (2 lenses, probed) found the
    two it had not thought of: a tuple target (`sep, ignored = "\n", 0`) and
    a `match` capture (`case {"separator": sep}`). Both made a live JSONL
    rewrite vanish from the scan entirely — neither RISK nor OK, the same
    disappearance the arc has now paid for three times.

    Enumerating forms is a denylist, so this does not enumerate. Python
    marks every name it binds by ASSIGNMENT with `ctx=ast.Store`, so that is
    what gets counted; the handful of binders that carry a bare `str`
    instead of a Name node (`except E as x`, `case ... as x`, `case [*rest]`,
    `import x`, parameters) are listed after it — a short, closed set that
    the grammar itself defines.
    """
    binds: "dict[str, list]" = {}
    resolved: "dict[int, object]" = {}
    for n in _own_scope(fn):
        # A simple `x = <constant>` is the ONLY shape whose value is known.
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    resolved[id(target)] = n.value.value
    for n in _own_scope(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            binds.setdefault(n.id, []).append(
                resolved.get(id(n), _UNRESOLVED))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            binds.setdefault(n.name, []).append(_UNRESOLVED)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            binds.setdefault(n.name, []).append(_UNRESOLVED)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            binds.setdefault(n.rest, []).append(_UNRESOLVED)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                binds.setdefault((a.asname or a.name).split(".")[0],
                                 []).append(_UNRESOLVED)
        elif isinstance(n, ast.arg):
            binds.setdefault(n.arg, []).append(_UNRESOLVED)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            binds.setdefault(n.name, []).append(_UNRESOLVED)
    return binds


def _shadowed(fn) -> set:
    """Names this function rebinds locally, whatever the module says.

    Adversarial r8 (4 lenses, probed): parser identity was collected
    module-wide, so `def rewrite(path, loads_clean=json.loads)` and a local
    `loads_clean = lambda s: json.loads(s)` both parsed with the raw parser
    while the scanner read the module-level import and said OK. A binding
    that is not proven clean IN THIS SCOPE cannot inherit the module's
    proof.
    """
    local = set(_binding_census(fn))
    module_clean, _ = _PARSERS
    # A local binding can RE-prove itself: importing the wrapper inside the
    # function is how half this codebase does it (`doctor.py` and
    # `gc_memory.py` both do), and that import is the proof, not a shadow.
    keep, local_raw = _parser_names(fn)
    for n in _own_scope(fn):
        # ...as is `X = <a name the module already proved clean>`.
        # _own_scope, not ast.walk: r9 gave the census and the proof scan
        # lexical scopes and left THIS re-proof loop walking the whole
        # subtree, so `def nested(): loads_clean = clean` inside the body
        # cleared the shadow on the outer function's own
        # `loads_clean=json.loads` parameter and certified it OK
        # (adversarial r10, Minimalist, probed).
        # Every binding form, destructuring expanded — the third private
        # copy of this walk in as many rounds (adversarial r14, found by
        # this round's own negative control: `parser, _x = loads_clean,
        # None` re-proved nothing because this loop read only plain
        # Assign-of-Name, so the clean alias stayed shadowed).
        for target, v in (pair for b in _bindings(n)
                          for pair in _expand_binding(*b)):
            if isinstance(target, ast.Name) and isinstance(v, ast.Name) \
                    and v.id in module_clean:
                keep.add(target.id)
    return local - (keep - local_raw)


def _called_names(node: ast.AST) -> set[str]:
    """Bare names this node calls: f(...) and self.f(...) alike."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _class_alias_map(tree, by_class_name):
    """id(ClassDef) -> {name -> set of same-module class names it may
    denote at that class's definition site}.

    Aliases are inheritance too (adversarial r15, four seats, probed):
    `Alias = Base` followed by `class Child(Alias)` carried Base's
    decoder provenance in every real sense, but the class graph matched
    bases against literal ClassDef names only — so the inherited raw
    decoder walked past the scan and an unrelated clean call earned
    Child.rewrite an OK. Module-scope Name bindings whose value chain
    ends at a known class resolve to it; a name bound ambiguously
    unions ALL candidates (RISK direction); a chain that ends anywhere
    else resolves to nothing, which keeps an alias to a bytes-holding
    class from minting false provenance.
    """
    # EVERY scope, not just the module (adversarial r16, four seats,
    # probed): `Alias = Base` inside a class body or a factory function
    # is same-module inheritance too, and the module-only walk left a
    # nested Child(Alias) without its base's decoder provenance. But
    # scoped by the LEXICAL CHAIN, not flattened (adversarial r17, four
    # seats, probed): the r16 flattening let an unrelated function's
    # `Alias = Dangerous` taint a module-level `class Child(Alias)`
    # whose runtime base is the module's clean `Alias = Safe` — a false
    # RISK that erodes the instrument. Each ClassDef resolves its bases
    # against the bindings of its own enclosing scopes only (imprecision
    # WITHIN that chain still unions toward RISK — including enclosing
    # class bodies Python would skip at runtime). A dotted target
    # (Outer.Alias = Base) still contributes its final attribute from
    # ANY scope — an attribute binding's home namespace is not
    # statically known here, so it stays chain-independent (RISK
    # direction), for the same reason base extraction reads Attribute
    # bases by .attr.
    scope_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, scope_types)]
    per_scope: "dict[int, dict[str, set[str]]]" = {}
    # Bindings reachable through an ATTRIBUTE base from anywhere in the
    # module: dotted targets (Outer.Alias = Base), and Name bindings in
    # any CLASS body (`class Outer: Alias = Base` is `Outer.Alias` to
    # the rest of the module — the r16 class-body fixture). Function
    # locals are not attribute-reachable and stay chain-only.
    attr_reachable: "dict[str, set[str]]" = {}
    for scope in scopes:
        m = per_scope.setdefault(id(scope), {})
        for target, value in _scope_bindings(scope):
            if isinstance(target, ast.Name):
                tname = target.id
                buckets = [m]
                if isinstance(scope, ast.ClassDef):
                    buckets.append(attr_reachable)
            elif isinstance(target, ast.Attribute):
                tname = target.attr
                buckets = [attr_reachable]
            else:
                continue
            v = value
            while isinstance(v, ast.Subscript):
                v = v.value
            vname = v.id if isinstance(v, ast.Name) else \
                v.attr if isinstance(v, ast.Attribute) else None
            if vname is not None and vname != tname:
                for bucket in buckets:
                    bucket.setdefault(tname, set()).add(vname)

    parents: "dict[ast.AST, ast.AST]" = {}
    for p in ast.walk(tree):
        for ch in ast.iter_child_nodes(p):
            parents[ch] = p

    def _chain(node):
        # Enclosing scopes, innermost first, module last. The class's
        # own body is excluded on purpose: base expressions evaluate
        # before the body exists.
        chain = []
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, scope_types) or isinstance(cur, ast.Module):
                chain.append(cur)
            cur = parents.get(cur)
        return chain

    def _resolved(ref):
        resolved: "dict[str, set[str]]" = {}

        def _resolve(name, seen):
            # A name that is BOTH a ClassDef and an alias target (`class
            # Safe: ...; Safe = Dangerous`) carries BOTH provenances — the
            # literal class must not short-circuit the rebinding
            # (adversarial r16, Minimalist, probed).
            base = {name} if name in by_class_name else set()
            if name in resolved:
                return base | resolved[name]
            if name in seen or name not in ref:
                return base
            seen.add(name)
            out: set = set()
            for r in ref[name]:
                out |= _resolve(r, seen)
            resolved[name] = out
            return base | out

        return {a: _resolve(a, set()) for a in ref}

    # Per class: a NAME base sees the lexical chain; an ATTRIBUTE base
    # additionally sees every attribute-reachable binding — that is how
    # `class Child(Outer.Alias)` finds the class-body alias without an
    # unrelated function local ever tainting a bare-name base.
    out_maps: "dict[int, tuple]" = {}
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        name_ref: "dict[str, set[str]]" = {}
        for scope in _chain(cls):
            for tname, cands in per_scope.get(id(scope), {}).items():
                name_ref.setdefault(tname, set()).update(cands)
        attr_ref: "dict[str, set[str]]" = {
            k: set(v) for k, v in name_ref.items()}
        for tname, cands in attr_reachable.items():
            attr_ref.setdefault(tname, set()).update(cands)
        out_maps[id(cls)] = (_resolved(name_ref), _resolved(attr_ref))
    return out_maps


def scan_module(tree: ast.Module) -> list[tuple[str, int, str]]:
    """Return (verdict, lineno, qualified_name) for every flagged function."""
    global _PARSERS, _MODULE_DECODER_CTORS, _MODULE_DECODERS, \
        _MODULE_DECODER_METHODS
    _PARSERS = _parser_names(tree)
    _MODULE_DECODER_CTORS = _decoder_ctors(tree)
    _MODULE_DECODERS, _MODULE_DECODER_METHODS = \
        _decoder_names(tree, _MODULE_DECODER_CTORS)
    _CLASS_DECODER_MAP.clear()
    classes = [c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]
    by_class_name: "dict[str, list]" = {}
    for c in classes:
        by_class_name.setdefault(c.name, []).append(c)
    sets_by_id = {id(c): (set(), set(), set()) for c in classes}
    alias_map = _class_alias_map(tree, by_class_name)
    # Inheritance is provenance too (adversarial r14, two seats,
    # probed): a decoder initialized in `Base.__init__` was invisible
    # from `Child(Base).rewrite` because the map stopped at the
    # ClassDef boundary — a routine base-class extraction turned a raw
    # destructive parse scanner-green. Same-module bases propagate to a
    # fixpoint; a base name bound to several classes unions ALL
    # candidates (ambiguity errs toward RISK, the direction the scanner
    # is allowed to be wrong in).
    changed = True
    while changed:
        changed = False
        for c in classes:
            seed = [set(x) for x in sets_by_id[id(c)]]
            for b in c.bases:
                # Base[str] is an ast.Subscript wrapping the base
                # (adversarial r15, two seats, probed): generics were
                # discarded outright, severing provenance at every
                # Generic[...] boundary. Unwrap to the real base.
                while isinstance(b, ast.Subscript):
                    b = b.value
                bname = b.id if isinstance(b, ast.Name) else \
                    b.attr if isinstance(b, ast.Attribute) else None
                # UNION the literal class with any alias candidates
                # (adversarial r16, Minimalist, probed): a name that is
                # both a ClassDef and a later alias (`class Safe: ...;
                # Safe = Dangerous`) must carry BOTH provenances —
                # letting the literal class shadow the rebinding turned
                # a rebound raw decoder scanner-green.
                _name_m, _attr_m = alias_map.get(id(c), ({}, {}))
                _m = _attr_m if isinstance(b, ast.Attribute) else _name_m
                names = ({bname} if bname in by_class_name else set()) \
                    | _m.get(bname, set())
                for bc in (bc for n in names
                           for bc in by_class_name.get(n, ())):
                    if bc is not c:
                        for k in range(3):
                            seed[k] |= sets_by_id[id(bc)][k]
            new_sets = _class_decoder_sets(
                c, _MODULE_DECODER_CTORS, seed=tuple(seed))
            if new_sets != sets_by_id[id(c)]:
                sets_by_id[id(c)] = new_sets
                changed = True
    for c in classes:
        cc, ci, cm = sets_by_id[id(c)]
        if cc or ci or cm:
            for m in c.body:
                if isinstance(m, (ast.FunctionDef,
                                  ast.AsyncFunctionDef)):
                    _CLASS_DECODER_MAP[id(m)] = (cc, ci, cm)
    parents: dict[ast.AST, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p

    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # name -> EVERY function with that name. Adversarial r9 (Skeptic,
    # probed): a dict kept the last one, so an unrelated `B.save` replaced
    # `A.save` and `A.rewrite`'s write leg resolved to the wrong body —
    # `A.helper`, a destructive JSONL loop, vanished from the scan
    # entirely (neither RISK nor OK). A name collision is not evidence of
    # anything; when the call is ambiguous the scanner says "someone should
    # read this", which is the direction it is allowed to be wrong in.
    by_name: "dict[str, list]" = {}
    for f in funcs:
        by_name.setdefault(f.name, []).append(f)

    def qual(fn):
        name, cur = fn.name, fn
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{cur.name}.{name}"
        return name

    def writes(fn, seen=None):
        """Does fn write back — itself, via an enclosing fn, or a callee?"""
        seen = seen if seen is not None else set()
        # Cycle detection keys on the NODE, not the name. r9 taught the
        # call graph that an ambiguous name means "any candidate writes",
        # then poisoned it with a name-keyed `seen`: evaluating a harmless
        # `save` first inserted "save", and the destructive `A.save` right
        # behind it returned False before its body was read. The r9 fixture
        # happened to put the writer first, so the whole disappearance came
        # back the moment the definitions were reordered — the reader
        # vanished from the scan entirely, neither RISK nor OK (adversarial
        # r10, 3 seats, independently probed).
        if id(fn) in seen:
            return False
        seen.add(id(fn))
        dump = ast.dump(fn)
        if any(m in dump for m in WRITE_MARKERS):
            return True
        cur = fn
        while cur in parents:                      # lexically enclosing
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(m in ast.dump(cur) for m in WRITE_MARKERS):
                    return True
                break
        for callee in _called_names(fn):           # same-module call graph
            if any(writes(tgt, seen) for tgt in by_name.get(callee, ())):
                return True
        return False

    def written_by_a_caller(fn):
        """A read-helper is destructive when a caller writes what it returns."""
        for other in funcs:
            if other is fn:
                continue
            if fn.name in _called_names(other) and writes(other):
                return True
        return False

    def frames_lines(fn):
        """Does fn split a blob into lines?

        Every idiom that means the same thing: `.splitlines()`,
        `.readlines()`, `.split("\\n")` / `.split(b"\\n")`, the keyword form
        `.split(sep="\\n")`, a separator held in a variable, and plain
        iteration over an open file handle. Adversarial r4 (3 lenses) added
        the second and third — including `split(b"\\n")`, which
        `src/jsonl_utils.py` itself uses, so it was never hypothetical.
        Adversarial r5 (3 lenses, probed) added the last two, both invisible
        to r4's version and both one routine refactor away from any hardened
        site: hoisting the separator to a `sep = "\\n"` local, or moving from
        `.readlines()` to `with path.open() as fh: for line in fh:`.

        The direction of the doubt is the point. A `.split()` whose separator
        this cannot resolve is treated as framing, because the cost of a
        false RISK is one line of triage (the manifest already carries 69 of
        them) and the cost of a false OK is a destructive rewrite nobody can
        see. Only a separator PROVEN to be something other than a newline
        buys silence.
        """
        # A name buys silence only when ONE binding in the whole function
        # proves it non-newline. Adversarial r6 (2026-08-20, 2 lenses,
        # probed): the first cut kept the LAST assignment seen in AST order,
        # which is not control flow — `if newline: sep = "\n" else: sep =
        # ","` and a plain later `sep = ","` both made a live JSONL rewrite
        # vanish from the scan entirely. Counting bindings is not flow
        # analysis either; it just refuses to pretend. Two bindings means
        # unresolved, and unresolved means framing.
        binds = _binding_census(fn)
        consts = {k: v[0] for k, v in binds.items() if len(v) == 1}
        file_handles = set()                       # names bound from open()
        # _own_scope, like the census and the proof scan. The SCOPE is the
        # site: r10 made the parse proof lexical and left this walking the
        # whole subtree, and the mismatch flagged 21 functions whose only
        # framing AND only parse both live in a `locked_rmw` closure — the
        # outer function inherited the nested framing but no longer
        # inherited the nested proof, so `memory_ledger.stamp_outcome_*`
        # and friends turned RISK without one line of their code changing.
        # Each closure is already scanned as its own site (the manifest has
        # carried `poll._mark_applied` and `stamp_outcome_verdict._stamp`
        # since r1), so nothing stops being watched; it is watched under
        # the name that owns the framing.
        for n in _own_scope(fn):
            for target, value in _bindings(n):
                if _is_open_call(value) and isinstance(target, ast.Name):
                    file_handles.add(target.id)
        for n in _own_scope(fn):
            if isinstance(n, (ast.For, ast.AsyncFor)):
                it = n.iter
                if isinstance(it, ast.Name) and it.id in file_handles:
                    return True
                if _is_open_call(it):              # for line in open(p):
                    return True
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr in ("splitlines", "readlines"):
                return True
            if n.func.attr == "split":
                if isinstance(n.func.value, ast.Name) and \
                        n.func.value.id == "shlex":
                    continue          # shell words, never store lines
                args = list(n.args) + [k.value for k in n.keywords
                                       if k.arg == "sep"]
                if not args:
                    continue                       # .split() on whitespace
                a = args[0]
                if isinstance(a, ast.Constant):
                    if a.value in ("\n", b"\n"):
                        return True
                elif isinstance(a, ast.Name):
                    if consts.get(a.id, "\n") in ("\n", b"\n", _UNRESOLVED):
                        return True               # unresolved -> assume framing
                else:
                    return True                    # unresolvable expression
        return False

    out = []
    for fn in funcs:
        dump = ast.dump(fn)
        if not frames_lines(fn):
            continue
        if not (writes(fn) or written_by_a_caller(fn)):
            continue
        # OK is a HINT, and adversarial r5 (Architect, probed) showed how
        # weak a hint: the marker only had to APPEAR somewhere in the
        # function, so a rewrite that parses each line with bare
        # `json.loads` and merely mentions `loads_clean` elsewhere reported
        # OK — visible to the manifest's `vanished` leg, and green. A
        # function that still parses with the unguarded call has not been
        # cleared, whatever else it mentions.
        used_clean, used_raw = _parse_calls(fn)
        verdict = "OK  " if used_clean and not used_raw else "RISK"
        out.append((verdict, fn.lineno, qual(fn)))
    return out



_UNRESOLVED = object()


def _bindings(node):
    """(target, value) pairs for assignments and `with ... as` clauses.

    Annotated (`decoder: object = JSONDecoder()`) and walrus bindings are
    bindings too — adversarial r12 (QA, probed): an AnnAssign-bound decoder
    was invisible to `_decoder_names`, so its raw `.decode` walked past the
    scan while an unrelated clean call earned the function OK.
    """
    if isinstance(node, ast.Assign):
        for t in node.targets:
            yield t, node.value
    elif isinstance(node, ast.AnnAssign):
        if node.value is not None:
            yield node.target, node.value
    elif isinstance(node, ast.NamedExpr):
        yield node.target, node.value
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                yield item.optional_vars, item.context_expr


def _dotted_target(node):
    """Dotted spelling for a Name or an Attribute-of-Names chain
    (`decoder`, `self.decoder`) — None for anything else.

    Adversarial r13 (Failure Operator, probed): an instance stored on
    `self` was invisible because every binding rule required a bare
    `ast.Name` target, so `self.decoder = json.JSONDecoder()` plus
    `self.decoder.decode(line)` earned OK from an unrelated clean call.
    """
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _expand_binding(target, value):
    """Element-wise pairs for destructuring bindings.

    `decoder, _x = json.JSONDecoder(), None` binds through a Tuple on
    both sides — adversarial r13 (Architect + QA, probed): the tuple
    target made the decoder invisible. Lengths must match and starred
    targets are left alone (conservatively unresolvable).
    """
    if (isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
            and not any(isinstance(t, ast.Starred) for t in target.elts)):
        for t, v in zip(target.elts, value.elts):
            yield from _expand_binding(t, v)
    else:
        yield target, value


def _scope_bindings(scope):
    """Every (target, value) binding pair in scope, destructuring expanded."""
    for n in _own_scope(scope):
        for tv in _bindings(n):
            yield from _expand_binding(*tv)


def _is_open_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "open") or \
           (isinstance(f, ast.Attribute) and f.attr == "open")


_MODULE_DECODER_CTORS: set = set()
_MODULE_DECODERS: set = set()
_MODULE_DECODER_METHODS: set = set()
# id(method) -> (ctors, insts, methods) proven on the instance by ANY sibling
# method of the same class, normalized to an "@." prefix ("@.decoder").
# Adversarial r13 (Failure Operator, probed): `self.decoder =
# json.JSONDecoder()` in __init__ plus `self.decoder.decode(line)` in the
# rewrite earned OK — instance attributes outlive the method scope, so
# their provenance must too. Consumers re-spell "@" as their own first
# parameter, so a class whose methods disagree on the receiver name
# (`self` vs `cls` vs anything) still resolves.
_CLASS_DECODER_MAP: dict = {}


def _receiver_arg(fn):
    """Name of fn's first parameter — positional-only params INCLUDED.

    Adversarial r14 (Skeptic, probed): `def rewrite(self, path, /)` has
    no entries in args.args, only posonlyargs, so both the class-map
    builder and its consumer skipped the method entirely and a raw
    `self.decoder.decode(line)` was invisible."""
    params = list(fn.args.posonlyargs) + list(fn.args.args)
    return params[0].arg if params else None


def _class_decoder_sets(cls, module_ctors,
                        seed=(frozenset(), frozenset(),
                              frozenset())) -> "tuple[set, set, set]":
    """(ctor aliases, decoder instances, bound parse methods) proven on
    this class's instances, normalized to an "@." prefix.

    Three r14 holes, all probed: constructor ALIASES held on the
    instance (`self.Ctor = json.JSONDecoder`) were never carried across
    methods, only finished instances were; CLASS-BODY bindings
    (`decoder = json.JSONDecoder()` in the class body, read as
    `self.decoder` from every method) were not read at all; and the
    per-method pass ran once in definition order, so provenance
    established by a LATER method never reached an earlier one. The
    walk is now a fixpoint over the methods with the class-level sets
    re-spelled into each method's receiver as seeds. `seed` carries
    provenance inherited from base classes (see scan_module)."""
    ctors_at: set = set(seed[0])
    insts: set = set(seed[1])
    methods: set = set(seed[2])
    # Class-body bindings are instance-reachable state: `decoder =
    # json.JSONDecoder()` at class level is `self.decoder` in a method.
    body_ctors = _decoder_ctors(cls) - {"JSONDecoder"}
    body_insts, body_methods = _decoder_names(cls, _decoder_ctors(cls)
                                              | module_ctors)
    ctors_at |= {"@." + n for n in body_ctors if "." not in n}
    insts |= {"@." + n for n in body_insts if "." not in n}
    methods |= {"@." + n for n in body_methods if "." not in n}
    # ONE sweep over the methods. Convergence is driven by the caller:
    # scan_module re-seeds and re-runs this per class until the sets
    # stop growing (its class-graph fixpoint), so an inner fixpoint
    # here would be a redundant second loop on the same door — the r14
    # sweep proved it (the single-pass mutant survived because the
    # outer loop converges either way).
    for m in cls.body:
        if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = _receiver_arg(m)
        if first is None:
            continue
        pref = first + "."
        seed_ctors = {pref + n[2:] for n in ctors_at}
        seed_insts = {pref + n[2:] for n in insts}
        seed_methods = {pref + n[2:] for n in methods}
        ctors = _decoder_ctors(m, seed_ctors) | module_ctors
        mi, mm = _decoder_names(m, ctors, insts=seed_insts,
                                methods=seed_methods)
        for found, into in ((ctors, ctors_at), (mi, insts),
                            (mm, methods)):
            into |= {"@." + n[len(pref):] for n in found
                     if n.startswith(pref)}
    return ctors_at, insts, methods


def _lazy_nodes(fn) -> set:
    """ids of every node inside a GeneratorExp in this scope.

    Adversarial r11 (Expert QA, probed): `_own_scope` stops at function
    scopes but a generator expression is ALSO deferred code — it may never
    be consumed, so `_unused = (loads_clean(s) for s in ())` certified a
    rewrite that parsed every real row with a raw parser. The rule is
    asymmetric on purpose: a clean call inside a genexp proves nothing
    (it might never run), but a raw call inside one still poisons (it
    might). Eager comprehensions (list/set/dict) DO execute in the
    enclosing control flow and keep their proof value — the negative
    control pins that, because half the repo parses via listcomp.
    """
    out: set = set()
    for n in _own_scope(fn):
        if isinstance(n, ast.GeneratorExp):
            for inner in ast.walk(n):
                out.add(id(inner))
    return out


def _decoder_ctors(scope, seed=frozenset()) -> set:
    """Names that CONSTRUCT a JSONDecoder in this scope: the literal
    spelling plus `from json import JSONDecoder as X` aliases (adversarial
    r12, Failure Operator, probed: the import alias was a standard spelling
    the literal match walked past). `seed` carries ctor names proven
    OUTSIDE this scope (a sibling method, the class body) so chains off
    them resolve here (adversarial r14)."""
    out = {"JSONDecoder"} | set(seed)
    for n in _own_scope(scope):
        if isinstance(n, ast.ImportFrom) and n.module == "json":
            for a in n.names:
                if a.name == "JSONDecoder":
                    out.add(a.asname or a.name)
    # The constructor itself can be aliased by assignment, not just by
    # import (adversarial r13, four seats, probed: `Ctor =
    # json.JSONDecoder; decoder = Ctor()` earned OK). Fixpoint so chains
    # (`Other = Ctor`) resolve.
    changed = True
    while changed:
        changed = False
        for target, value in _scope_bindings(scope):
            # Dotted targets too (adversarial r14, three seats, probed):
            # `self.Ctor = json.JSONDecoder` bound the constructor to an
            # instance attribute this Name-only walk never recorded, so
            # `self.Ctor()` read as an ordinary call and the raw decoder
            # it built was invisible.
            tname = _dotted_target(target)
            if tname is None or tname in out:
                continue
            if ((isinstance(value, ast.Attribute)
                 and value.attr == "JSONDecoder")
                    or _dotted_target(value) in out):
                out.add(tname)
                changed = True
    return out


def _decoder_names(scope, ctors=frozenset(), insts=(),
                   methods=()) -> "tuple[set, set]":
    """(names holding a JSONDecoder instance, names bound to its parse
    methods) in this scope.

    Adversarial r11 (Expert QA, probed): `decoder.decode(line)` is a raw
    JSON parse wearing a spelling the parse-shaped rule never matched —
    `.decode` is overwhelmingly bytes.decode, so it cannot be treated as
    parse-shaped wholesale. The receiver decides: a name proven to hold a
    JSONDecoder makes its `.decode`/`.raw_decode` a raw parse; every other
    `.decode` stays invisible, which is what keeps this from flagging
    every UTF-8 decode in the tree.

    Adversarial r12 (all five seats, each with a different spelling):
    provenance, not the final call syntax, is what decides. `alias =
    decoder`, `raw = decoder.decode`, an AnnAssign binding, an import
    alias and `raw_decode` all bypassed the r11 literal match. Aliases
    resolve to a fixpoint so chains (`a = decoder; b = a`) are covered.
    """
    # Seeds carry provenance proven outside this scope — instance
    # attributes a sibling method or the class body established
    # (adversarial r14): seeding BEFORE the fixpoint lets in-scope
    # chains (`d = self.decoder; d.decode(line)`) resolve, where the
    # old post-hoc union could only catch direct spellings.
    ctors = set(ctors) | _decoder_ctors(scope, ctors)
    insts: set = set(insts)
    methods: set = set(methods)
    changed = True
    while changed:
        changed = False
        for target, value in _scope_bindings(scope):
            tname = _dotted_target(target)
            if tname is None:
                continue
            got = None
            if isinstance(value, ast.Call):
                f = value.func
                name = f.id if isinstance(f, ast.Name) else \
                    f.attr if isinstance(f, ast.Attribute) else ""
                # The dotted spelling too: `self.Ctor()` has attr
                # "Ctor", but the proof is filed under "self.Ctor"
                # (adversarial r14, three seats, probed).
                if name in ctors or _dotted_target(f) in ctors:
                    got = (insts, tname)
            elif (isinstance(value, ast.Attribute)
                    and value.attr in ("decode", "raw_decode")
                    and _dotted_target(value.value) in insts):
                got = (methods, tname)        # raw = decoder.decode
            else:
                vname = _dotted_target(value)
                if vname in insts:
                    got = (insts, tname)      # alias = decoder
                elif vname in methods:
                    got = (methods, tname)    # rebound = raw
            if got and got[1] not in got[0]:
                got[0].add(got[1])
                changed = True
    return insts, methods


def _parse_calls(fn) -> "tuple[bool, bool]":
    """(calls a proven-clean parser, calls anything else parse-shaped)."""
    clean_names, raw_names = _PARSERS
    shadow = _shadowed(fn)
    lazy = _lazy_nodes(fn)
    ctors = _decoder_ctors(fn) | _MODULE_DECODER_CTORS
    # Re-spell the class-level provenance into this method's receiver
    # BEFORE the name analysis runs, so in-method chains off inherited
    # state resolve (adversarial r14): ctor aliases feed the ctor set,
    # instances/methods seed the fixpoint. The receiver helper counts
    # positional-only params (r14, Skeptic).
    cc, ci, cm = _CLASS_DECODER_MAP.get(id(fn), (set(), set(), set()))
    seed_insts: set = set()
    seed_methods: set = set()
    first = _receiver_arg(fn)
    if (cc or ci or cm) and first:
        ctors |= {first + n[1:] for n in cc}
        seed_insts = {first + n[1:] for n in ci}
        seed_methods = {first + n[1:] for n in cm}
    decoders, decoder_methods = _decoder_names(
        fn, ctors, insts=seed_insts, methods=seed_methods)
    decoders |= _MODULE_DECODERS
    decoder_methods |= _MODULE_DECODER_METHODS
    # A dotted proof is only as good as its RECEIVER. `import jsonl_utils`
    # proves `jsonl_utils.loads_clean`, and `def rewrite(path, jsonl_utils)`
    # or a local `jsonl_utils = shim` replaces the object that name points
    # at — adversarial r9 (3 lenses, probed) certified both OK, because the
    # r8 revocation subtracted bare names from a set holding dotted ones.
    clean_names = {n for n in clean_names - shadow
                   if n.split(".")[0] not in shadow}
    used_clean = used_raw = False
    # _own_scope, not ast.walk. The same r9 half-conversion: a
    # `loads_clean(s)` call sitting in a nested helper — which need never
    # execute — counted as the OUTER function's taint-refusing parse, so a
    # rewrite parsing every line with a raw `parser(line)` came out OK
    # (adversarial r10, 2 seats, probed). Nested functions are scanned as
    # their own sites, so the call is still looked at, in the scope that
    # actually makes it.
    for n in _own_scope(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute):
            recv = _dotted_target(f.value) or "?"
            name, dotted = f.attr, f"{recv}.{f.attr}"
        elif isinstance(f, ast.Name):
            name = dotted = f.id
        else:
            continue
        # RAW WINS. `from json import loads as _loads_clean` binds a
        # trusted-looking name to the untrusted parser, and r7 probed that
        # exact shape earning OK — so a proven raw binding beats every
        # naming convention below it.
        if dotted in raw_names or name in raw_names:
            used_raw = True
        elif dotted in decoder_methods or \
                (isinstance(f, ast.Name) and f.id in decoder_methods):
            used_raw = True          # raw = decoder.decode; raw(line)
        elif name in ("decode", "raw_decode") and isinstance(f, ast.Attribute) and (
                (_dotted_target(f.value) in decoders)
                or (isinstance(f.value, ast.Call)
                    and isinstance(f.value.func, (ast.Name, ast.Attribute))
                    and (getattr(f.value.func, "id", "")
                         or getattr(f.value.func, "attr", ""))
                    in ctors)):
            used_raw = True          # json.JSONDecoder().decode — a raw parse
        elif name in clean_names or dotted in clean_names:
            if id(n) not in lazy:    # a proof inside a genexp may never run
                used_clean = True
        elif name == "loads" or name.endswith("_loads") \
                or name in ("loads_clean", "_loads_clean"):
            used_raw = True          # parse-shaped and unproven
    return used_clean, used_raw


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = pathlib.Path(argv[0]) if argv else pathlib.Path("src")
    paths = sorted(root.glob("*.py")) if root.is_dir() else [root]
    risks = 0
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="surrogateescape"))
        except SyntaxError:
            continue
        for verdict, lineno, name in scan_module(tree):
            print(f"{verdict} {path.name}:{lineno} {name}()")
            risks += verdict.strip() == "RISK"
    print(f"\n{risks} RISK site(s) — triage by reading; OK is a hint, "
          f"not a proof (see module docstring).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
