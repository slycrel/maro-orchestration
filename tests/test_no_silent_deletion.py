"""Retention-decree tripwire: no new file-deletion sites ship unreviewed.

Decree (Jeremy, 2026-07-10): the system never auto-deletes run/user data —
"I'd prefer to have the users choose to archive/delete old runs, rather than
have the system decide it's clutter." Retention is a user-level decision;
auto-cleanup exists only as explicit opt-in (default off); system-driven
removal must archive, never destroy.

This test AST-scans every module in src/ for file-deletion calls
(Path.unlink/rmdir, shutil.rmtree, os.remove/unlink/rmdir/removedirs) and
fails on any call site not in the allowlist below. Adding a deletion site
means adding an allowlist entry — which puts the justification in the diff
where a human reviews it. The companion is docs/DEFAULTS.md's census
tripwire (test_defaults_doc.py): both exist because "what I think is in
place isn't always" — decrees need enforcement, not memory.

Limits, stated so nobody reads more assurance off a green run than it
carries:
  * This catches FILE deletion. Record-level deletion (rewriting a JSONL
    store without some records) can't be detected generically — those
    paths (lesson GC, skill culls/retirement) were converted to
    archive-then-drop in the same change that added this test, with unit
    tests pinning the archive behavior.
  * The allowlist key is (module, function), so a SECOND deletion call
    added inside an already-allowlisted function ships silently. The 29
    allowed functions are the blind spot; escalation inside one of them
    (an `.unlink` becoming an `rmtree` over a run dir) is exactly the
    shape this would miss. Named 2026-08-16 rather than redesigned —
    tightening the key changes the maintenance contract for every future
    contributor, so it is a BACKLOG decision, not a drive-by.
  * A shell-out delete (`subprocess.run(["rm", "-rf", ...])`) bypasses
    the AST scan entirely. None exist in src/ today; verified 2026-08-16.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

# (module filename, enclosing function) -> why this deletion is allowed.
# Categories: user-invoked (explicit user action), opt-in (default-off config),
# ephemeral (temp/lock/marker files that are not run or user data), move
# (data is written elsewhere before the original is removed).
ALLOWED_DELETION_SITES = {
    ("container_exec.py", "_clear_auth_breaker_if"):
        "ephemeral breaker marker — identity-checked probe-driven clear "
        "(same class as clear_auth_breaker; review 2026-08-13)",
    ("container_exec.py", "clear_auth_breaker"):
        "ephemeral: the container auth breaker's own state marker "
        "(memory/container_auth_breaker.json) — same class as interrupt.py's "
        "loop-running markers; removed only when the auth volume is verified "
        "re-seeded (or by explicit operator call), never run/user data",
    ("checkpoint.py", "delete_checkpoint"):
        "user-invoked: `checkpoint delete` CLI only; no automatic caller "
        "(finalize's delete-on-done removed 2026-07-10, retention decree)",
    ("file_lock.py", "atomic_write"):
        "ephemeral: temp-file swap inside the atomic-write primitive",
    ("path_rewrite.py", "rewrite_file"):
        "ephemeral: removes only its own .maro-rewrite.tmp when the "
        "atomic swap fails; the file being rewritten is never unlinked",
    ("gc_memory.py", "_gc_narrative_logs"):
        "user-invoked: maro-memory gc CLI, dry-run by default",
    ("interrupt.py", "clear_loop_running"):
        "ephemeral: loop-running marker file",
    ("interrupt.py", "get_running_loop"):
        "ephemeral: clears stale loop-running marker (dead pid)",
    ("interrupt.py", "get_running_project_loop"):
        "ephemeral: clears stale loop-running marker (dead pid)",
    ("killswitch.py", "clear"):
        "user-invoked: the user clearing their own kill switch",
    ("llm.py", "_run_subprocess_safe"):
        "ephemeral: temp prompt file for subprocess adapter",
    ("runs.py", "record_llm_call"):
        "ephemeral: unlinks its own .call-tmp-* staging file after the "
        "os.link publication (R2-1, 2026-08-28) — the published "
        "call-NNNNN.json record itself is never deleted; the temp is a "
        "second hard link to the same inode, so no data is lost",
    ("web_fetch.py", "capture_raw"):
        "ephemeral: (a) unlinks its own just-created capture temp file when the "
        "write fails, and (b) unlinks a non-regular entry squatting the capture "
        "path — that entry is an attacker-planted symlink, not run/user data, "
        "and unlinking a symlink never touches its target (adversarial review "
        "2026-07-27: reusing it leaked the target's path to the model, which "
        "the execute prompt instructs to parse it)",
    ("run_lease.py", "acquire_run_lease"):
        "ephemeral: removes the empty lease file this same call just "
        "created when flock fails (a present-unheld lease reads as "
        "'owner dead' to probes — leaving it is an active wrong answer); "
        "never touches an existing holder's file (size==0 guard)",
    ("llm.py", "_cleanup_files"):
        "ephemeral: temp prompt files for subprocess adapter",
    ("loop_finalize.py", "cleanup_step_artifacts"):
        "opt-in: artifacts.auto_prune_days (default 0 = never), "
        "never touches the just-finished loop",
    ("loop_report.py", "_clear_debug_snapshots"):
        "ephemeral: regenerable debug HTML snapshots, cleared only when the "
        "opt-in debug flag is off so stale snapshots aren't mistaken for "
        "current ones",
    ("memory_quality.py", "main"):
        "ephemeral: benchmark tmpdir in __main__ harness",
    ("mission.py", "_release_drain_lock"):
        "ephemeral: drain lock file",
    ("run_curation.py", "prune_run"):
        "user-invoked: explicit `prune` CLI subcommand",
    ("runs.py", "invalidate_run_index"):
        "ephemeral: removes only the derived migration marker so metadata can "
        "rebuild the disposable run-reference index",
    ("runs.py", "remove_run_index"):
        "ephemeral: removes derived reference leaves for a user-pruned run; "
        "the run deletion itself remains gated by run_curation.prune_run",
    ("runs.py", "_indexed_run_dir"):
        "ephemeral: removes one corrupt/stale derived reference leaf before "
        "repairing it from retained metadata",
    ("sheriff.py", "check_system_health"):
        "ephemeral: deletes its own just-written health-probe file",
    ("task_store.py", "_atomic_write"):
        "ephemeral: temp-file swap inside atomic write",
    ("task_store.py", "archive"):
        "move: task is written to the archive dir before the original "
        "(and its lock file) is unlinked",
    ("worktree.py", "provision_clone"):
        "ephemeral: removes the just-created scratch clone when its own "
        "provisioning fails (branch checkout / clone error) — a throwaway "
        "copy with no worker data yet, never a run/user artifact",
    ("worktree.py", "cleanup_clone"):
        "move: the containerized self-dev scratch clone (and its owner sidecar) "
        "is removed only after merge_back_clone has merged its work into the "
        "live repo; on merge failure keep_on_failure=True preserves the clone, "
        "its branch, and the sidecar (so a later stale-clone sweep still finds it)",
    ("worktree.py", "_sanitize_untrusted_git"):
        "ephemeral: removes worker-planted .git/hooks from a throwaway scratch "
        "clone's control plane before host-side git runs against it (RCE "
        "hardening) — never run/user data",
}

_PATH_DELETION_ATTRS = {"unlink", "rmdir"}
_OS_DELETION_ATTRS = {"remove", "unlink", "rmdir", "removedirs"}


def _deletion_sites(path: Path):
    """Yield (function_name, call_repr, lineno) for each deletion call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = ["<module>"]

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            f = node.func
            if isinstance(f, ast.Attribute):
                base = f.value
                base_name = base.id if isinstance(base, ast.Name) else None
                if base_name == "os" and f.attr in _OS_DELETION_ATTRS:
                    hits.append((self.stack[-1], f"os.{f.attr}", node.lineno))
                elif f.attr == "rmtree":
                    # shutil.rmtree, aliased-module rmtree, from-imported rmtree
                    hits.append((self.stack[-1], "rmtree", node.lineno))
                elif base_name not in ("os", "shutil") and f.attr in _PATH_DELETION_ATTRS:
                    hits.append((self.stack[-1], f".{f.attr}", node.lineno))
            elif isinstance(f, ast.Name) and f.id in ("rmtree", "unlink"):
                # from shutil import rmtree / from os import unlink
                hits.append((self.stack[-1], f.id, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return hits


def _violations(src_root: Path = None, allowed=None) -> list:
    """Deletion call sites in src_root that no allowlist entry covers."""
    src_root = SRC if src_root is None else src_root
    allowed = ALLOWED_DELETION_SITES if allowed is None else allowed
    out = []
    # rglob, not glob: src/ has nested packages (maro_assets), and a
    # deletion that moves into one must stay scanned. No such call exists
    # today (verified 2026-08-16) — this closes it before it opens, the
    # same latent gap the DEFAULTS.md census had until chunk-8.
    for py in sorted(src_root.rglob("*.py")):
        for func, call, lineno in _deletion_sites(py):
            if (py.name, func) not in allowed:
                out.append(f"{py.name}:{lineno} {func}() calls {call}")
    return out


def _stale_entries(src_root: Path = None, allowed=None) -> list:
    src_root = SRC if src_root is None else src_root
    allowed = ALLOWED_DELETION_SITES if allowed is None else allowed
    live = set()
    for py in sorted(src_root.rglob("*.py")):
        for func, _call, _lineno in _deletion_sites(py):
            live.add((py.name, func))
    return [entry for entry in allowed if entry not in live]


def _delete_checkpoint_offenders(src_root: Path = None) -> list:
    src_root = SRC if src_root is None else src_root
    offenders = []
    for py in sorted(src_root.rglob("*.py")):
        if py.name == "checkpoint.py":
            continue
        if "delete_checkpoint" in py.read_text(encoding="utf-8"):
            offenders.append(py.name)
    return offenders


def test_every_deletion_site_is_allowlisted():
    violations = _violations()
    assert not violations, (
        "New file-deletion site(s) in src/ — retention decree (2026-07-10): "
        "the system never auto-deletes run/user data. If this deletion is "
        "user-invoked, an explicit default-off opt-in, an ephemeral "
        "temp/lock file, or a move (data written elsewhere first), add it "
        "to ALLOWED_DELETION_SITES in this test with that justification. "
        "Otherwise: archive, don't delete.\n  " + "\n  ".join(violations)
    )


def test_allowlist_has_no_stale_entries():
    """Entries whose call site disappeared must be removed — keeps the list honest."""
    stale = _stale_entries()
    assert not stale, f"Stale allowlist entries (deletion site no longer exists): {stale}"


def test_no_automatic_delete_checkpoint_caller():
    """delete_checkpoint is user-CLI only; finalize must not regrow the call.

    A checkpoint deleted at finalize destroys resume state that closure
    verification (which runs AFTER finalize) may still demote back to
    incomplete — the resume substrate must outlive the verdict.
    """
    offenders = _delete_checkpoint_offenders()
    assert not offenders, (
        f"delete_checkpoint referenced outside checkpoint.py: {offenders} — "
        "checkpoints are kept on completion (retention decree, 2026-07-10)"
    )


# ---------------------------------------------------------------------------
# Must-detect fixtures: proof that the three tripwires above CAN fail.
#
# Added 2026-08-16 after a file-derived mutation sweep scored 4/13 on this
# file. All three assertions could be gutted — `violations`, `stale` and
# `offenders` each replaced by [] left a green suite — so the decree's
# enforcement was a standing claim, not a guard. Worse, the four mutations
# that WERE caught all fired through the stale-entry test (deleting a scanner
# leg orphans allowlist rows), and that test was itself gutted by `stale = []`.
# One removable line was carrying the whole scanner's coverage.
#
# Cause was structural: the assertions walked SRC directly, so there was no
# way to hand them a known violation. The helpers now take (src_root, allowed)
# defaulting to the live repo, and each fixture injects one violation.
#
# A detection shape with no fixture is a claim, not a guard: when you teach
# the scanner a new deletion API, add the fixture in the same commit.
# ---------------------------------------------------------------------------

import pytest


def _src(tmp_path: Path, **files) -> Path:
    """Write {module_stem: source} as a synthetic src/ tree."""
    for stem, body in files.items():
        p = tmp_path / (stem.replace("__", "/") + ".py")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


@pytest.mark.parametrize("body,shape", [
    ("import os\ndef f(p):\n    os.remove(p)\n", "os.remove"),
    ("import os\ndef f(p):\n    os.unlink(p)\n", "os.unlink"),
    ("import os\ndef f(p):\n    os.rmdir(p)\n", "os.rmdir"),
    ("import os\ndef f(p):\n    os.removedirs(p)\n", "os.removedirs"),
    ("import shutil\ndef f(p):\n    shutil.rmtree(p)\n", "shutil.rmtree"),
    ("from shutil import rmtree\ndef f(p):\n    rmtree(p)\n", "bare rmtree"),
    ("from os import unlink\ndef f(p):\n    unlink(p)\n", "bare unlink"),
    ("def f(p):\n    p.unlink()\n", "Path.unlink"),
    ("def f(p):\n    p.rmdir()\n", "Path.rmdir"),
    ("from pathlib import Path\ndef f(s):\n    Path(s).unlink()\n",
     "Path(...).unlink on a call"),
])
def test_each_deletion_api_is_caught(tmp_path, body, shape):
    """Every API the scanner claims to recognise, one fixture each."""
    src = _src(tmp_path, m=body)
    assert _violations(src, allowed={}), f"{shape} slipped past the scan"


@pytest.mark.parametrize("kw", ["def", "async def"])
def test_a_deletion_is_attributed_to_its_function_sync_or_async(tmp_path, kw):
    """Assert the NAME, not just that something was reported.

    First cut of this asserted only `_violations(...)` was truthy, and the
    mutation dropping visit_AsyncFunctionDef survived it: without that
    binding the body is still walked, so a violation is still raised — just
    attributed to <module> instead of the function. A misattributed site is
    worse than a missed one here, because the allowlist is keyed by function
    name, so the wrong name silently licenses (or fails to license) the
    wrong thing. Caught by the sweep 2026-08-16.
    """
    src = _src(tmp_path, m=f"{kw} f(p):\n    p.unlink()\n")
    assert _violations(src, allowed={}) == ["m.py:2 f() calls .unlink"]
    assert _violations(src, allowed={("m.py", "f"): "why"}) == []


def test_a_deletion_in_a_nested_package_is_caught(tmp_path):
    # rglob, not glob. No nested deletion exists today; this keeps it that
    # way rather than discovering it after one ships.
    src = _src(tmp_path, pkg____init__="", pkg__deep="import os\ndef f(p):\n    os.remove(p)\n")
    assert _violations(src, allowed={}) == ["deep.py:3 f() calls os.remove"]


def test_an_allowlisted_site_is_not_reported(tmp_path):
    src = _src(tmp_path, m="import os\ndef f(p):\n    os.remove(p)\n")
    assert _violations(src, allowed={("m.py", "f"): "why"}) == []


def test_the_allowlist_key_is_module_scoped(tmp_path):
    # ("m.py","f") must not license ("other.py","f") — a same-named function
    # in another module is a different deletion site with a different reason.
    src = _src(tmp_path,
               m="import os\ndef f(p):\n    os.remove(p)\n",
               other="import os\ndef f(p):\n    os.remove(p)\n")
    got = _violations(src, allowed={("m.py", "f"): "why"})
    assert got == ["other.py:3 f() calls os.remove"]


def test_a_module_level_deletion_is_attributed_to_module(tmp_path):
    # The function stack must be popped: a deletion outside any def reports
    # <module>, which no allowlist entry covers, so it cannot hide behind the
    # name of the function that happened to precede it.
    src = _src(tmp_path, m="import os\ndef f(p):\n    pass\nos.remove('x')\n")
    assert _violations(src, allowed={("m.py", "f"): "why"}) == [
        "m.py:4 <module>() calls os.remove"]


def test_a_non_deletion_call_is_not_reported(tmp_path):
    src = _src(tmp_path, m="import os\ndef f(p):\n    os.rename(p, p)\n    p.write_text('x')\n")
    assert _violations(src, allowed={}) == []


class TestTheStaleCensusCanFail:
    def test_an_entry_whose_site_vanished_is_caught(self, tmp_path):
        src = _src(tmp_path, m="def f(p):\n    pass\n")
        assert _stale_entries(src, allowed={("m.py", "f"): "why"}) == [("m.py", "f")]

    def test_a_live_entry_is_not_reported(self, tmp_path):
        src = _src(tmp_path, m="import os\ndef f(p):\n    os.remove(p)\n")
        assert _stale_entries(src, allowed={("m.py", "f"): "why"}) == []


class TestTheCheckpointPinCanFail:
    def test_a_reference_outside_checkpoint_py_is_caught(self, tmp_path):
        src = _src(tmp_path, finalizer="from checkpoint import delete_checkpoint\n")
        assert _delete_checkpoint_offenders(src) == ["finalizer.py"]

    def test_checkpoint_pys_own_definition_is_exempt(self, tmp_path):
        src = _src(tmp_path, checkpoint="def delete_checkpoint(x):\n    pass\n")
        assert _delete_checkpoint_offenders(src) == []
