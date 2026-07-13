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

Limits: this catches FILE deletion. Record-level deletion (rewriting a
JSONL store without some records) can't be detected generically — those
paths (lesson GC, skill culls/retirement) were converted to archive-then-
drop in the same change that added this test, with unit tests pinning the
archive behavior.
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
    ("checkpoint.py", "delete_checkpoint"):
        "user-invoked: `checkpoint delete` CLI only; no automatic caller "
        "(finalize's delete-on-done removed 2026-07-10, retention decree)",
    ("file_lock.py", "atomic_write"):
        "ephemeral: temp-file swap inside the atomic-write primitive",
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
    ("sandbox.py", "run_skill_sandboxed"):
        "ephemeral: sandbox tmpdir",
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
        "move: the containerized self-dev scratch clone is removed only after "
        "merge_back_clone has merged its work into the live repo; on merge "
        "failure keep_on_failure=True preserves both the clone and its branch",
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


def test_every_deletion_site_is_allowlisted():
    violations = []
    for py in sorted(SRC.glob("*.py")):
        for func, call, lineno in _deletion_sites(py):
            if (py.name, func) not in ALLOWED_DELETION_SITES:
                violations.append(f"{py.name}:{lineno} {func}() calls {call}")
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
    live = set()
    for py in sorted(SRC.glob("*.py")):
        for func, _call, _lineno in _deletion_sites(py):
            live.add((py.name, func))
    stale = [entry for entry in ALLOWED_DELETION_SITES if entry not in live]
    assert not stale, f"Stale allowlist entries (deletion site no longer exists): {stale}"


def test_no_automatic_delete_checkpoint_caller():
    """delete_checkpoint is user-CLI only; finalize must not regrow the call.

    A checkpoint deleted at finalize destroys resume state that closure
    verification (which runs AFTER finalize) may still demote back to
    incomplete — the resume substrate must outlive the verdict.
    """
    offenders = []
    for py in sorted(SRC.glob("*.py")):
        if py.name == "checkpoint.py":
            continue
        if "delete_checkpoint" in py.read_text(encoding="utf-8"):
            offenders.append(py.name)
    assert not offenders, (
        f"delete_checkpoint referenced outside checkpoint.py: {offenders} — "
        "checkpoints are kept on completion (retention decree, 2026-07-10)"
    )
