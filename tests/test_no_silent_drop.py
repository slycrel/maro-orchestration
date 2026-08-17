"""Silent-drop tripwire: a read that throws a record away must say so.

Skipping a corrupt record is usually RIGHT. One bad append must not
truncate the read of everything after it — that failure (introspect.py,
Tier 0 #3) is why `jsonl_utils` exists at all. This test is not about the
drop. It is about the silence.

A read that returns 40 of 41 rows and says nothing is indistinguishable
from a store that holds 40. Downstream that becomes a count in a readout,
a census that reports "0 candidates", a lesson that was never considered
— all of them honest-looking. It contradicts the retention decree
("the path is part of the result", 2026-07-10 / feedback 2026-08-16) and
artifacts-over-streams (2026-07-27) at the same time: the artifact is
durable on disk and the view over it quietly is not.

WHAT COUNTS AS A SILENT DROP (the exact rule this scanner applies):
an `except` handler where all three hold —
  1. its `try` lexically contains a `json`/`yaml`/`pickle`/`tomllib`
     load call;
  2. that `try` sits inside a `for`/`while` in the SAME function, so the
     handler skips one record and the loop keeps going; and
  3. the handler body is nothing but `pass` / `continue` / `break`.
Condition 3 is what "silent" means here, and it is deliberately narrow:
a handler that logs, re-raises, counts the tear (`cov['lines_torn'] += 1`
in verdict_flow.py), appends to an error list, or so much as mentions the
bound exception is NOT reported. Those already say something.

HOW TO CLEAR ONE. In order of preference: route the read through
`jsonl_utils.read_jsonl_tail_counted` and surface its SkipReport; or log
at WARNING naming the store; or count the drop and return the count with
the records. If the silence is genuinely correct, add a REVIEWED entry
below with the reason — that puts the justification in a diff a human
reads, which is the whole mechanism.

Limits, stated so nobody reads more off a green run than it carries:
  * Condition 3 is evadable by one dead statement: `except Exception: x
    = None; continue` is not reported. Every census in this repo is
    evadable (see test_no_silent_deletion.py); the point is to make the
    silent path require a deliberate step, not to be unforgeable.
  * Single-object reads are out of scope. `except json.JSONDecodeError:
    return None` on a whole-config read loses everything, not one record,
    and reads as "no config" rather than "corrupt config" — a real
    defect, a different shape. The wider count when this gate landed
    (2026-08-16): 313 silent handlers over a parse call across 83
    modules, of which 137 were per-record drops — the slice this gate
    covers, and the size of the baseline it landed with. The live number
    is whatever the dict below sums to; it only goes down. (An earlier
    ad-hoc scan reported "143 across 52 modules" before the rule was
    pinned down; that figure is superseded.)
  * A parse that never raises because it was pre-validated elsewhere
    still lands in the census. That is what REVIEWED is for.

ON THE KEY. The function half is QUALIFIED — `outer.inner` for a nested
def, `Class.method` for a method — because bare names collide, and a
collision merges two unrelated sites into one allowance. Two real ones
found when this was tightened (2026-08-17): `memory_ledger.py`'s five
distinct `_stamp` closures shared a single key, and `memory_backends.py`
`read_all` was one entry covering both JSONLBackend and SQLiteBackend.
Under bare names, reviewing either one would have silently blessed the
others.

Entries are keyed (module, qualified function) -> COUNT, not -> bool. The retention tripwire's bool key means a
second deletion added inside an already-allowed function ships silently
(named as a limit there 2026-08-16, filed as a BACKLOG decision with
counting recommended). This is that recommendation, built: adding a
second silent drop to a listed function trips the gate, and removing one
trips the stale check.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

_PARSE_CALLS = {
    ("json", "loads"), ("json", "load"),
    ("yaml", "safe_load"), ("yaml", "load"),
    ("pickle", "loads"), ("pickle", "load"),
    ("tomllib", "loads"), ("tomllib", "load"),
}

# Local wrappers around a parse call, matched as bare names. A wrapper is
# otherwise an evasion vector: route json.loads through a one-line helper
# and every drop around it leaves the census. memory_ledger._loads_clean
# (2026-08-17, byte-taint refusal) was the first; it now lives in
# jsonl_utils as `loads_clean`, imported under both names. Add new ones
# here in the same diff that introduces them.
_PARSE_WRAPPERS = {"_loads_clean", "loads_clean"}

# (module filename, enclosing function) -> why the silence is correct here.
# Entries move here OUT of UNREVIEWED_SILENT_DROPS, one at a time, with a
# reason. A REVIEWED entry allows exactly one site, so a second silent drop
# added to a reviewed function still trips the gate.
#
# The six below share one shape: an in-place row stamper. Each splits the
# store into `lines`, scans for the row it wants, assigns `lines[i] = ...`,
# and rejoins the WHOLE list. A line it cannot parse is skipped by the
# SEARCH and re-emitted verbatim by the rewrite, so nothing is lost and
# there is nothing to announce — the distinction this census exists to
# draw. That property is not free, though: a rewrite built from the parsed
# rows instead would destroy every torn line in the file, which is exactly
# the bug fixed in `_dedup` and `compress_old_outcomes` this same chunk.
# So it is pinned by tests (TestTheStampersPreserveWhatTheyCannotParse in
# tests/test_memory.py) and by tests/mutation/stamp_preserve.json.
_STAMPER = ("in-place row stamper: the rewrite rejoins all lines, so an "
            "unparseable row is skipped by the search and preserved by the "
            "write — no loss, nothing to announce. Holds for undecodable "
            "BYTES too since 2026-08-17: the read rides surrogateescape "
            "(memory_ledger._store_text / file_lock.locked_rmw), so a torn "
            "byte is one skippable row, not a whole-file UnicodeDecodeError "
            "— which it was when this entry was first written, and the "
            "adversarial round caught it. Pinned by "
            "TestTheStampersPreserveWhatTheyCannotParse (three torn shapes, "
            "raw bytes included).")
# knowledge_web's node-store rewrites share the stamper shape (byte-safe
# read via jsonl_utils.store_text, taint-refusing loads_clean, matched rows
# re-dumped, everything else re-emitted verbatim, surrogateescape write).
# Pinned by TestNodeRewritesPreserveWhatTheyCannotParse in
# tests/test_knowledge_web.py and tests/mutation/knowledge_web_preserve.json.
_NODE_STAMPER = ("in-place node rewrite: unmatched and unparseable lines are "
                 "re-emitted verbatim (byte-safe read + loads_clean since "
                 "2026-08-17, so a torn or tainted line is one skippable row "
                 "and never laundered) — no loss, nothing to announce. Pinned "
                 "by TestNodeRewritesPreserveWhatTheyCannotParse.")
_SUGGESTION_STAMPER = ("keyed-merge rewrite under locked_rmw (byte-safe): a line "
                       "that fails the taint-refusing parse never matches the "
                       "merge/drop key and is re-emitted verbatim — no loss, "
                       "nothing to announce. Pinned by "
                       "TestSuggestionRewritesPreserveWhatTheyCannotParse in "
                       "tests/test_evolver_store.py and "
                       "tests/mutation/evolver_store_preserve.json.")
# rules.py / background.py keyed upserts share the same shape (found by
# adversarial r2 of the evolver_store chunk, 2026-08-17: the old versions
# re-dumped every row AND deleted unparseable lines on every rewrite).
# Pinned by TestKeyedUpsertsPreserveWhatTheyCannotParse in
# tests/test_rules.py / tests/test_background.py and
# tests/mutation/rules_background_preserve.json.
_UPSERT_STAMPER = ("keyed upsert under locked_rmw (byte-safe): a line that "
                   "fails the taint-refusing parse never matches the id and "
                   "is re-emitted verbatim — no loss, nothing to announce. "
                   "Pinned by TestKeyedUpsertsPreserveWhatTheyCannotParse "
                   "and tests/mutation/rules_background_preserve.json.")
REVIEWED_SILENT_DROPS: dict[tuple[str, str], str] = {
    ("memory_ledger.py", "mark_outcomes_superseded._mark"): _STAMPER,
    ("memory_ledger.py", "stamp_outcome_verdict._stamp"): _STAMPER,
    ("memory_ledger.py", "stamp_outcome_stop_verdict._stamp"): _STAMPER,
    ("memory_ledger.py", "stamp_outcome_step_lessons._stamp"): _STAMPER,
    ("memory_ledger.py", "annotate_outcome_lessons._stamp"): _STAMPER,
    ("memory_ledger.py", "annotate_outcome_extraction_failure._stamp"): _STAMPER,
    ("knowledge_web.py", "_bump_node_times_applied"): _NODE_STAMPER,
    ("knowledge_web.py", "promote_knowledge_candidates"): _NODE_STAMPER,
    ("evolver_store.py", "apply_suggestion._merge"): _SUGGESTION_STAMPER,
    ("evolver_store.py", "revert_suggestion._drop_constraint"): _SUGGESTION_STAMPER,
    ("rules.py", "save_rule._upsert"): _UPSERT_STAMPER,
    ("background.py", "_append_task_log._merge"): _UPSERT_STAMPER,
}

# The 2026-08-16 baseline: every per-record silent drop that existed when
# this gate landed, with its count. This is DEBT, not approval — nobody has
# looked at these yet. The gate's job is that the number cannot grow by
# accident. Fixing one means deleting its line here, in the same diff.
UNREVIEWED_SILENT_DROPS: dict[tuple[str, str], int] = {
    ("attribution.py", "load_attributions"): 1,

    ("camera_readout.py", "_lesson_origins"): 1,
    ("camera_readout.py", "main"): 1,

    ("captains_log.py", "load_log"): 1,
    ("captains_log.py", "query_log"): 1,
    ("captains_log.py", "timeline"): 1,

    ("constraint.py", "_load_dynamic_constraints"): 1,

    ("convo_miner.py", "scan_session_logs"): 1,

    ("correspondence.py", "_iter_turn_chunks"): 1,
    ("correspondence.py", "render_transcript"): 1,

    ("delta_replay.py", "find_decision_calls"): 1,
    ("delta_replay.py", "gather_oracle_decision_calls"): 1,

    ("doctor.py", "run_doctor"): 1,

    ("eval.py", "load_eval_trend"): 1,
    ("eval.py", "load_generated_evals"): 1,
    ("eval.py", "save_generated_evals"): 1,

    ("evolver_scans.py", "_load_baselines"): 1,
    ("evolver_scans.py", "_load_dated_diagnoses"): 1,
    ("evolver_scans.py", "_record_suggestion_outcomes"): 1,
    ("evolver_scans.py", "scan_calibration_log"): 1,
    ("evolver_scans.py", "scan_suggestion_outcomes"): 1,


    ("goal_map.py", "build_goal_map"): 2,

    ("graduation.py", "_already_proposed"): 1,
    ("graduation.py", "scan_candidates"): 1,
    ("graduation.py", "verify_graduation_rules"): 1,

    ("interrupt.py", "InterruptQueue.peek"): 1,
    ("interrupt.py", "acquire_project_slot"): 1,

    ("knowledge_bridge.py", "_extract_llm"): 1,

    ("knowledge_lens.py", "_lesson_texts_by_id"): 1,
    ("knowledge_lens.py", "load_hypotheses"): 1,
    ("knowledge_lens.py", "load_standing_rules"): 1,
    ("knowledge_lens.py", "search_decisions"): 1,


    ("llm.py", "CodexCLIAdapter._stream_events"): 1,
    ("llm.py", "_is_plain_missing_session_error"): 1,
    ("llm.py", "_parse_stream_json"): 1,
    ("llm.py", "_run_subprocess_safe._drain_new_events"): 1,

    ("loop_finalize.py", "_mint_run_risks_to_project"): 1,

    ("loop_report.py", "_gather_run_summaries"): 2,
    ("loop_report.py", "_read_log_slice"): 1,
    ("loop_report.py", "_render_environment"): 1,
    ("loop_report.py", "_too_broad_events"): 1,
    ("loop_report.py", "backfill_run_reports"): 1,

    ("memory_backends.py", "JSONLBackend.read_all"): 1,
    ("memory_backends.py", "SQLiteBackend.read_all"): 1,

    ("metrics.py", "spend_for_loops"): 1,
    ("metrics.py", "spend_today"): 1,
    ("metrics.py", "successful_run_cost_p90"): 1,

    ("mint_grounding.py", "collect_run_tool_events"): 1,

    ("mission.py", "list_missions"): 1,

    ("navigator.py", "_extract_json"): 1,

    ("navigator_shadow.py", "_load_navigator_events"): 2,
    ("navigator_shadow.py", "load_navigator_lessons"): 1,

    ("observe.py", "_read_ancestry_tree"): 1,

    ("orch.py", "_active_salvage_runs"): 1,

    ("orch_bridges.py", "_extract_session_result_from_text"): 1,
    ("orch_bridges.py", "_read_jsonl_records"): 1,

    ("orch_items.py", "_load_run_records"): 1,

    ("pack.py", "_import_hypotheses"): 1,
    ("pack.py", "_import_lessons"): 1,
    ("pack.py", "_import_rules_as_hypotheses"): 1,
    ("pack.py", "_import_skill_records"): 1,

    ("packaging_readout.py", "_read_jsonl"): 1,

    ("pre_flight.py", "preflight_calibration_stats"): 1,

    ("provenance.py", "_memory_store_rows"): 2,

    ("rerun_identity.py", "prior_attempts"): 1,

    ("revisit.py", "_dead_ends"): 1,

    ("router.py", "build_training_data"): 2,


    ("run_curation.py", "_load_loop_log"): 1,
    ("run_curation.py", "find_unconsumed_skill_candidates"): 1,
    ("run_curation.py", "inventory_assets"): 1,
    ("run_curation.py", "list_runs"): 1,
    ("run_curation.py", "surface_step_flags"): 1,

    ("runs.py", "_scan_legacy_run_dirs"): 1,
    ("runs.py", "read_injected_skill_ids"): 1,
    ("runs.py", "remove_run_index"): 1,

    ("shadow_lane.py", "_iter_run_dirs_newest_first"): 1,
    ("shadow_lane.py", "_status"): 1,
    ("shadow_lane.py", "_today_ledger_count"): 1,

    ("skills.py", "_load_skill_tests"): 1,
    ("skills.py", "get_all_skill_stats"): 1,
    ("skills.py", "load_skill_provenance"): 1,
    ("skills.py", "load_skills"): 1,
    ("skills.py", "record_skill_injection_outcome"): 1,
    ("skills.py", "record_skill_outcome"): 1,
    ("skills.py", "save_skill"): 1,

    ("sprint_contract.py", "load_contracts"): 1,

    ("system_health.py", "_probe_lesson_receipts"): 3,
    ("system_health.py", "_recent_outcomes"): 1,

    ("thinkback.py", "load_latest_outcome"): 1,
    ("thinkback.py", "load_outcome_by_id"): 1,

    ("validation_shadow.py", "_read_events"): 2,

    ("validator_roi.py", "_read_events"): 2,
}


def _parses(nodes) -> bool:
    """True if a JSON/YAML/pickle/TOML load call appears anywhere in `nodes`.

    Matches module.attr calls (_PARSE_CALLS) and known local wrappers by
    bare name (_PARSE_WRAPPERS) — without the latter, wrapping json.loads
    in a helper silently removes every drop around it from the census.
    """
    for stmt in nodes:
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Call):
                continue
            if isinstance(n.func, ast.Attribute):
                if (getattr(n.func.value, "id", None), n.func.attr) in _PARSE_CALLS:
                    return True
            elif isinstance(n.func, ast.Name) and n.func.id in _PARSE_WRAPPERS:
                return True
    return False


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """True if the handler's whole body is control flow — it says nothing.

    Note this is a check on the handler's OWN statements, not a walk: a
    `continue` nested inside an `if` that also logs must not read as
    silent, and it doesn't, because the `If` is not a control-flow leaf.
    """
    return all(isinstance(s, (ast.Pass, ast.Continue, ast.Break))
               for s in handler.body)


def _sites_in_scope(scope) -> list[tuple[str, int]]:
    """Silent per-record drops owned by one function (or module) body.

    Walks the scope's own statements, refusing to descend into nested
    `def`/`class` bodies — those are their own scopes and get visited
    separately, so a drop is attributed to the function that actually
    contains it rather than to whatever encloses it.
    """
    name = getattr(scope, "name", "<module>")
    out: list[tuple[str, int]] = []
    stack = [(child, False) for child in ast.iter_child_nodes(scope)]
    while stack:
        node, in_loop = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Try) and in_loop and _parses(node.body):
            for handler in node.handlers:
                if _is_silent(handler):
                    out.append((name, handler.lineno))
        in_loop = in_loop or isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        stack.extend((child, in_loop) for child in ast.iter_child_nodes(node))
    return out


def _child_scopes(scope) -> list:
    """The def/class nodes this scope directly owns.

    Reaches through `if`/`for`/`try` (a conditionally-defined function is
    still this scope's child) but stops at the first def/class, whose own
    nested scopes belong to it and are found on its own visit.
    """
    out = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _sites(py: Path) -> list[tuple[str, int]]:
    """Every silent per-record drop in one module, as (qualified name, lineno).

    The name carries its enclosing defs (`outer.inner`) because bare names
    collide: memory_ledger.py alone holds five distinct `_stamp` closures in
    five different public functions. Under a bare key one REVIEWED entry
    would bless all five, and reviewing one site would silently approve four
    nobody read — a guard that cannot fail.
    """
    tree = ast.parse(py.read_text(encoding="utf-8"))
    out: list[tuple[str, int]] = []

    def visit(scope, qual: str) -> None:
        out.extend((qual, lineno) for _name, lineno in _sites_in_scope(scope))
        for child in _child_scopes(scope):
            name = getattr(child, "name", "<module>")
            visit(child, name if qual == "<module>" else f"{qual}.{name}")

    visit(tree, "<module>")
    return sorted(out, key=lambda s: s[1])


def _census(src_root: Path = None) -> Counter:
    """(module, function) -> how many silent drops it holds."""
    src_root = SRC if src_root is None else src_root
    found: Counter = Counter()
    # rglob, not glob: src/ has nested packages (maro_assets), and a store
    # reader that moves into one must stay scanned.
    for py in sorted(src_root.rglob("*.py")):
        # Key on the path relative to src_root, not the basename: two files
        # named store.py in different packages must not share one allowance.
        # Same collision the 2026-08-17 function-qualification fix closed,
        # one axis over — caught by the adversarial round on that fix. For
        # top-level modules (all current entries) the key is unchanged.
        mod = py.relative_to(src_root).as_posix()
        for func, _lineno in _sites(py):
            found[(mod, func)] += 1
    return found


def _listed(reviewed=None, unreviewed=None) -> Counter:
    """The allowance, merged. A REVIEWED entry allows exactly one site."""
    reviewed = REVIEWED_SILENT_DROPS if reviewed is None else reviewed
    unreviewed = UNREVIEWED_SILENT_DROPS if unreviewed is None else unreviewed
    out: Counter = Counter(unreviewed)
    for key in reviewed:
        out[key] += 1
    return out


def _unlisted(src_root: Path = None, reviewed=None, unreviewed=None) -> list[str]:
    """Silent drops in excess of what the lists allow."""
    src_root = SRC if src_root is None else src_root
    live = _census(src_root)
    allowed = _listed(reviewed, unreviewed)
    out = []
    for (module, func), count in sorted(live.items()):
        excess = count - allowed.get((module, func), 0)
        if excess > 0:
            lines = [ln for py in sorted(src_root.rglob("*.py"))
                     if py.relative_to(src_root).as_posix() == module
                     for f, ln in _sites(py) if f == func]
            out.append(f"{module}:{func}() has {count} silent per-record "
                       f"drop(s) (allowed {count - excess}) at line(s) "
                       + ", ".join(str(ln) for ln in lines))
    return out


def _stale(src_root: Path = None, reviewed=None, unreviewed=None) -> list[str]:
    """Listed entries the code no longer has — the list must not outlive it."""
    src_root = SRC if src_root is None else src_root
    live = _census(src_root)
    allowed = _listed(reviewed, unreviewed)
    out = []
    for key, count in sorted(allowed.items()):
        have = live.get(key, 0)
        if have < count:
            module, func = key
            out.append(f"{module}:{func}() is listed {count}x but has {have}")
    return out


def test_no_unlisted_silent_drops():
    unlisted = _unlisted()
    assert not unlisted, (
        "Silent per-record drop(s) in src/ that no list covers. A read that "
        "throws a record away must say so — otherwise a short list is "
        "indistinguishable from a short store (retention decree: the path is "
        "part of the result).\n"
        "Fix, in order of preference: (1) read through "
        "jsonl_utils.read_jsonl_tail_counted and surface its SkipReport; "
        "(2) log.warning naming the store; (3) count the drop and return the "
        "count alongside the records. If the silence is genuinely correct, "
        "add a REVIEWED_SILENT_DROPS entry with the reason.\n  "
        + "\n  ".join(unlisted)
    )


def test_the_gates_are_pointed_at_the_real_src_tree():
    """Non-vacuity. Every fixture below hands the helpers a tmp_path tree, so
    a broken SRC would leave all of them green while the two live gates above
    scanned nothing and passed for the emptiest possible reason.
    """
    modules = list(SRC.rglob("*.py"))
    assert len(modules) > 50, (
        f"SRC={SRC} resolved to {len(modules)} module(s) — the silent-drop "
        "gates are scanning the wrong tree, so their green is meaningless")


def test_the_lists_have_no_stale_entries():
    """A listed site that no longer exists must be removed.

    Without this, the debt count only ever looks like it went down: you fix
    a reader, leave its line in the baseline, and the slot stays open for
    the next silent drop to slide into unnoticed.
    """
    stale = _stale()
    assert not stale, (
        "Stale entries — the code has fewer silent drops than listed, so "
        "delete these lines (that is what progress looks like):\n  "
        + "\n  ".join(stale)
    )


# ---------------------------------------------------------------------------
# Must-detect fixtures: proof that the two tripwires above CAN fail.
#
# Written WITH the gate, not after it, because of what the tier-1 sweep
# found: three of five decree tripwires in this repo could be gutted whole
# with a green suite, because they walked SRC directly and there was no way
# to hand them a known violation. A detection shape with no fixture is a
# claim, not a guard.
#
# Both directions are fixtured: what must be caught, and what must NOT be —
# a census is only trustworthy if you can show what it stays quiet about.
# ---------------------------------------------------------------------------

def _src(tmp_path: Path, **files: str) -> Path:
    """Write a throwaway src tree. Keys are filenames, `pkg__mod.py` nests."""
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        target = root / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


_SILENT = """
import json

def read(path):
    out = []
    for line in path.open():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
"""


class TestTheCensusCanFail:

    def test_a_bare_silent_drop_is_caught(self, tmp_path):
        root = _src(tmp_path, **{"store.py": _SILENT})
        assert _unlisted(root, {}, {}) == [
            "store.py:read() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 9"
        ]

    def test_the_site_is_attributed_to_its_own_function(self, tmp_path):
        # Not to the module, and not to an enclosing function: the key is
        # what the allowlist is written against, so a wrong name silently
        # mis-allows a different site.
        root = _src(tmp_path, **{"store.py": """
import json

def outer():
    def inner():
        for line in []:
            try:
                json.loads(line)
            except Exception:
                continue
    return inner
"""})
        assert _unlisted(root, {}, {}) == [
            "store.py:outer.inner() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 9"
        ]

    def test_two_same_named_nested_defs_get_two_keys(self, tmp_path):
        # The collision that made qualification necessary: memory_ledger.py
        # holds five `_stamp` closures. Under a bare key they share one
        # allowance, so reviewing one blesses the rest. Here, allowing the
        # first must leave the second reported.
        root = _src(tmp_path, **{"store.py": """
import json

def alpha():
    def _stamp():
        for line in []:
            try:
                json.loads(line)
            except Exception:
                continue

def beta():
    def _stamp():
        for line in []:
            try:
                json.loads(line)
            except Exception:
                continue
"""})
        assert _unlisted(root, {}, {("store.py", "alpha._stamp"): 1}) == [
            "store.py:beta._stamp() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 17"
        ]

    def test_a_method_is_keyed_under_its_class(self, tmp_path):
        # memory_backends.py had ONE `read_all` entry covering two backend
        # classes. Allowing one class's method must not allow the other's.
        root = _src(tmp_path, **{"store.py": """
import json

class Jsonl:
    def read_all(self):
        for line in []:
            try:
                json.loads(line)
            except Exception:
                continue

class Sqlite:
    def read_all(self):
        for line in []:
            try:
                json.loads(line)
            except Exception:
                continue
"""})
        assert _unlisted(root, {}, {("store.py", "Jsonl.read_all"): 1}) == [
            "store.py:Sqlite.read_all() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 17"
        ]

    def test_a_wrapper_parse_call_is_still_a_parse_call(self, tmp_path):
        # Routing json.loads through a named wrapper must not remove the
        # drop from the census — memory_ledger._loads_clean is live code,
        # and without _PARSE_WRAPPERS its six REVIEWED sites would only be
        # caught by the stale check, not by this scanner reading the file.
        root = _src(tmp_path, **{"store.py": """
def read_all():
    for line in []:
        try:
            _loads_clean(line)
        except Exception:
            continue
"""})
        assert _unlisted(root, {}, {}) == [
            "store.py:read_all() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 6"
        ]

    def test_two_same_named_modules_get_two_keys(self, tmp_path):
        # The module half of the key has the same collision the function
        # half had: src/ holds nested packages, and two files named
        # store.py must not share one allowance. Keys are relative paths.
        body = """
import json

def read_all():
    for line in []:
        try:
            json.loads(line)
        except Exception:
            continue
"""
        root = _src(tmp_path, **{"store.py": body, "pkg__store.py": body})
        assert _unlisted(root, {}, {("store.py", "read_all"): 1}) == [
            "pkg/store.py:read_all() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 8"
        ]

    def test_an_async_reader_is_scanned_too(self, tmp_path):
        root = _src(tmp_path, **{"store.py": """
import json

async def read(rows):
    async for line in rows:
        try:
            json.loads(line)
        except Exception:
            continue
"""})
        assert _unlisted(root, {}, {}) == [
            "store.py:read() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 8"
        ]

    def test_a_module_level_loop_is_scanned_too(self, tmp_path):
        root = _src(tmp_path, **{"store.py": """
import json
ROWS = []
for line in open("x"):
    try:
        ROWS.append(json.loads(line))
    except Exception:
        continue
"""})
        assert "<module>()" in _unlisted(root, {}, {})[0]

    def test_a_nested_package_is_scanned(self, tmp_path):
        # glob would miss this; rglob is the difference. The retention
        # tripwire shipped with the glob version for a month.
        root = _src(tmp_path, **{"pkg__store.py": _SILENT})
        assert len(_unlisted(root, {}, {})) == 1

    def test_a_second_drop_in_a_listed_function_is_caught(self, tmp_path):
        # This is the whole reason the key counts instead of flagging.
        root = _src(tmp_path, **{"store.py": """
import json

def read(rows):
    for line in rows:
        try:
            json.loads(line)
        except ValueError:
            continue
        try:
            json.loads(line)
        except ValueError:
            continue
"""})
        assert _unlisted(root, {}, {("store.py", "read"): 1}) == [
            "store.py:read() has 2 silent per-record drop(s) "
            "(allowed 1) at line(s) 8, 12"
        ]
        assert _unlisted(root, {}, {("store.py", "read"): 2}) == []

    def test_each_parser_is_covered(self, tmp_path):
        # Spelled as dotted strings, not as tuples: written as tuples these
        # literals are byte-identical to lines of _PARSE_CALLS, and a
        # mutation anchored on one of those lines matches here too and gets
        # skipped as ambiguous — a fixture that quietly disarms its own
        # mutation. (Cost half a sweep to find, 2026-08-16.)
        for spec in ("json.loads", "json.load",
                     "yaml.safe_load", "yaml.load",
                     "pickle.loads", "pickle.load",
                     "tomllib.loads", "tomllib.load"):
            module, call = spec.split(".")
            root = _src(tmp_path, **{"store.py": f"""
import {module}

def read(rows):
    for line in rows:
        try:
            {module}.{call}(line)
        except Exception:
            continue
"""})
            assert len(_unlisted(root, {}, {})) == 1, f"{module}.{call} missed"

    def test_a_pass_handler_is_a_drop_too(self, tmp_path):
        root = _src(tmp_path, **{"store.py": """
import json

def read(rows):
    for line in rows:
        try:
            json.loads(line)
        except Exception:
            pass
"""})
        assert len(_unlisted(root, {}, {})) == 1

    def test_a_reviewed_entry_allows_exactly_one_site(self, tmp_path):
        root = _src(tmp_path, **{"store.py": """
import json

def read(rows):
    for line in rows:
        try:
            json.loads(line)
        except ValueError:
            continue
        try:
            json.loads(line)
        except ValueError:
            continue
"""})
        one = {("store.py", "read"): "probe: a non-JSON line is expected here"}
        assert len(_unlisted(root, one, {})) == 1, "one reason, one site"
        assert _unlisted(root, one, {("store.py", "read"): 1}) == []


class TestTheCensusStaysQuietForHonestCode:

    def _quiet(self, tmp_path, body):
        root = _src(tmp_path, **{"store.py": body})
        assert _unlisted(root, {}, {}) == [], body

    def test_a_handler_that_logs_is_not_a_silent_drop(self, tmp_path):
        self._quiet(tmp_path, """
import json, logging
log = logging.getLogger(__name__)

def read(rows):
    for line in rows:
        try:
            json.loads(line)
        except Exception:
            log.warning("bad row in %s", rows)
            continue
""")

    def test_a_handler_that_counts_the_tear_is_not_a_silent_drop(self, tmp_path):
        # verdict_flow.py's real shape: cov['lines_torn'] += 1; continue.
        self._quiet(tmp_path, """
import json

def read(rows):
    cov = {"torn": 0}
    for line in rows:
        try:
            json.loads(line)
        except Exception:
            cov["torn"] += 1
            continue
    return cov
""")

    def test_a_handler_that_reraises_is_not_a_silent_drop(self, tmp_path):
        self._quiet(tmp_path, """
import json

def read(rows):
    for line in rows:
        try:
            json.loads(line)
        except Exception:
            raise
""")

    def test_a_conditional_continue_beside_a_log_is_not_silent(self, tmp_path):
        # The handler's body is [If], not [Continue] — a walk-based check
        # would call this silent and let a logging reader into the census.
        self._quiet(tmp_path, """
import json, logging
log = logging.getLogger(__name__)

def read(rows, strict):
    for line in rows:
        try:
            json.loads(line)
        except Exception:
            if strict:
                raise
            log.warning("skipping a row")
            continue
""")

    def test_a_parse_outside_a_loop_is_out_of_scope(self, tmp_path):
        # Losing a whole config is a different (worse) shape, deliberately
        # not gated here — see the module docstring's Limits.
        self._quiet(tmp_path, """
import json

def read(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
""")

    def test_a_loop_with_no_parse_call_is_out_of_scope(self, tmp_path):
        self._quiet(tmp_path, """
def read(rows):
    for row in rows:
        try:
            row.validate()
        except Exception:
            continue
""")

    def test_a_loop_inside_a_nested_def_does_not_taint_the_outer_scope(self, tmp_path):
        # The `try` belongs to inner's loop. If scope-walking leaked across
        # the def boundary, outer would be reported as well and the count
        # for a real module would silently double.
        root = _src(tmp_path, **{"store.py": """
import json

def outer(rows):
    for r in rows:
        pass
    def inner():
        for line in []:
            try:
                json.loads(line)
            except Exception:
                continue
    return inner
"""})
        assert _unlisted(root, {}, {}) == [
            "store.py:outer.inner() has 1 silent per-record drop(s) "
            "(allowed 0) at line(s) 11"
        ]


class TestTheStaleCheckCanFail:

    def test_an_entry_with_no_live_site_is_stale(self, tmp_path):
        root = _src(tmp_path, **{"store.py": "x = 1\n"})
        assert _stale(root, {}, {("store.py", "read"): 1}) == [
            "store.py:read() is listed 1x but has 0"]

    def test_an_entry_listed_more_times_than_it_occurs_is_stale(self, tmp_path):
        # Half-fixing a function has to show up, or the freed slot stays
        # open for the next silent drop to land in unnoticed.
        root = _src(tmp_path, **{"store.py": _SILENT})
        assert _stale(root, {}, {("store.py", "read"): 3}) == [
            "store.py:read() is listed 3x but has 1"]

    def test_a_reviewed_entry_can_go_stale_too(self, tmp_path):
        root = _src(tmp_path, **{"store.py": "x = 1\n"})
        assert _stale(root, {("store.py", "read"): "because"}, {}) == [
            "store.py:read() is listed 1x but has 0"]

    def test_an_exactly_matched_list_is_not_stale(self, tmp_path):
        root = _src(tmp_path, **{"store.py": _SILENT})
        assert _stale(root, {}, {("store.py", "read"): 1}) == []
