"""Global test fixtures — workspace isolation + recursion guard.

Every test gets its own tmp workspace so nothing leaks into ~/.maro/workspace/.
Individual tests that already set OPENCLAW_WORKSPACE via monkeypatch will
override this (monkeypatch wins over os.environ in the same scope).

The recursion guard (pytest_configure) refuses to start a second pytest
session if one is already active in the process tree. See rationale below.

The clean-checkout tripwire (pytest_sessionstart/finish) fails the run if the
suite left new files behind in the working tree. See rationale below.
"""

import os
from pathlib import Path

import pytest

import _checkout_tripwire


# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------
#
# History: session 18 regression runs hit a process-leak scenario where a
# subprocess `claude -p` spawned 160+ pytest workers against the wrong
# codebase, consuming 12GB RAM. Evolver's verify_post_apply path also shells
# out to `pytest` on this same tree after auto-applying mutations. If either
# of those fires *from inside* a pytest session, we get nested/recursive full-
# suite runs — at best wasting minutes, at worst fork-bombing the box.
#
# Scoped subprocess pytest (a skill validator running pytest against a tmp
# test dir) is fine — that child won't load this conftest.py. The guard only
# catches children that re-enter *this* project's test tree.

_ACTIVE_ENV = "MARO_PYTEST_ACTIVE"


def _is_own_xdist_worker(active: str) -> bool:
    """True only for a `-n` worker of the very pytest run that set the marker.

    xdist fans ONE session out across processes, which is the opposite of the
    recursion this guard exists to stop — but a worker has its own pid, so the
    naive check would exit(2) every worker and make `-n auto` unusable.

    Being an xdist worker is not sufficient on its own: a `claude -p` (or
    evolver) subprocess launched from inside a worker inherits
    PYTEST_XDIST_WORKER too, and letting that through would reopen the exact
    fork-bomb hole. So we also require this process to be a DIRECT child of
    the marked pytest — execnet's popen gateway spawns workers straight from
    the controller, while a recursive grandchild's ppid is the worker's pid.
    Workers deliberately leave the marker pointing at the controller (see
    pytest_configure) so that ppid check keeps discriminating one level down.
    """
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return False
    try:
        return os.getppid() == int(active)
    except (TypeError, ValueError):
        return False


def pytest_configure(config):
    active = os.environ.get(_ACTIVE_ENV)
    if active and active != str(os.getpid()):
        if _is_own_xdist_worker(active):
            # Legitimate fan-out. Do NOT re-stamp the marker with this
            # worker's pid — leaving the controller's pid in place is what
            # makes a pytest spawned FROM this worker still get blocked.
            return
        pytest.exit(
            f"Recursive pytest blocked: parent pytest pid={active} is already "
            "running against this tree. Full-suite recursion causes runaway "
            "fork-bombs (session 18: 160+ workers / 12GB RAM). If a test needs "
            "to invoke pytest, target a scoped tmp directory whose tree does "
            "not include tests/conftest.py — that child won't trip this guard.",
            returncode=2,
        )
    os.environ[_ACTIVE_ENV] = str(os.getpid())


# ---------------------------------------------------------------------------
# Clean-checkout tripwire
# ---------------------------------------------------------------------------
#
# Enforces the test-suite half of Jeremy's 2026-07-28 out-of-the-box decree:
# snapshot the working tree at session start, compare at session finish, fail
# the run if the suite ADDED anything. Rationale, prior violations, and the
# pruning rules live in tests/_checkout_tripwire.py — kept there rather than
# inline so the mechanism can be exercised by a test rather than trusted.

_REPO_ROOT = Path(__file__).resolve().parents[1]

_tripwire_baseline: "set[str] | None" = None


def pytest_sessionstart(session):
    global _tripwire_baseline
    if os.environ.get(_checkout_tripwire.ALLOW_ENV):
        return
    if os.environ.get("PYTEST_XDIST_WORKER"):
        # The controller brackets the whole run — its baseline is taken before
        # any worker starts and its comparison after all of them finish, which
        # is exactly the window the tripwire wants. A per-worker snapshot would
        # walk the repo 2N extra times AND report every OTHER worker's
        # in-flight writes as this worker's leak.
        return
    _tripwire_baseline = _checkout_tripwire.snapshot(_REPO_ROOT)


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get(_ACTIVE_ENV) == str(os.getpid()):
        os.environ.pop(_ACTIVE_ENV, None)

    if _tripwire_baseline is None:
        return
    added = sorted(_checkout_tripwire.snapshot(_REPO_ROOT) - _tripwire_baseline)
    if not added:
        return

    message = _checkout_tripwire.format_report(added)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message, red=True)
    else:  # pragma: no cover - terminalreporter is always present under pytest
        print(message)
    if exitstatus == 0:
        session.exitstatus = 1

# API key env vars that should never leak into tests.  Tests that need a real
# adapter explicitly set these; everything else gets isolation for free.
_API_KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)

# Env vars that point at credential files — redirect to nowhere so tests
# never discover real keys from disk (e.g. legacy ~/.openclaw/ fallback).
_CREDENTIAL_PATH_VARS = (
    "MARO_ENV_FILE",
    "OPENCLAW_CFG",
)


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path):
    """Route all workspace resolution to a per-test tmp dir.

    Sets MARO_WORKSPACE (highest priority in config.workspace_root()) so that
    memory_dir(), output_dir(), projects_dir(), and everything downstream
    writes to tmp_path instead of the real workspace.

    Also hides API keys so tests never accidentally hit real LLM endpoints.
    Tests that explicitly need a key can set it via monkeypatch.
    """
    saved = {}
    # Workspace isolation
    saved["MARO_WORKSPACE"] = os.environ.get("MARO_WORKSPACE")
    os.environ["MARO_WORKSPACE"] = str(tmp_path)

    # tail.spawn flipped ON by default 2026-08-21 — without this pin, every
    # test reaching handle()'s finalize with pending tail jobs would fork a
    # REAL finalize-tail child. Tests that exercise the spawn itself delenv
    # this explicitly.
    saved["MARO_TAIL_SPAWN"] = os.environ.get("MARO_TAIL_SPAWN")
    os.environ["MARO_TAIL_SPAWN"] = "0"

    # User-config isolation — the box's real ~/.maro/config.yml must never
    # feed a test (found 2026-07-01: notify/telegram keys leaked into
    # _resolve_allowed_chats through the user tier; the workspace tier was
    # isolated in session 17, this tier wasn't).
    saved["MARO_USER_DIR"] = os.environ.get("MARO_USER_DIR")
    os.environ["MARO_USER_DIR"] = str(tmp_path / "maro-user")

    # API key isolation — stash and remove
    for var in _API_KEY_VARS:
        saved[var] = os.environ.pop(var, None)

    # Credential file isolation — point at nonexistent paths so legacy
    # fallbacks (e.g. ~/.openclaw/) never feed real keys to build_adapter().
    for var in _CREDENTIAL_PATH_VARS:
        saved[var] = os.environ.get(var)
        os.environ[var] = str(tmp_path / "no-such-credentials")

    yield

    # Restore everything
    for var, val in saved.items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val


# Subprocess LLM adapters (claude -p / codex CLI) authenticate via the box's
# CLI session, not an API key — so the key isolation above doesn't stop them.
# Session 40 found dry_run leaks where tests silently invoked the real
# authenticated `claude` CLI: each call burned real tokens and took minutes
# (test_handle.py alone ran 2h06m). Block the CLI binaries at the one seam
# all subprocess adapters share, leaving non-LLM commands (sh, echo) alone so
# the _run_subprocess_safe unit tests still exercise the real implementation.
_BLOCKED_LLM_BINS = ("claude", "codex")


@pytest.fixture(autouse=True)
def _block_subprocess_llm(monkeypatch):
    try:
        import llm
    except Exception:
        yield
        return

    _real = llm._run_subprocess_safe

    def _guarded(cmd, **kwargs):
        bin_name = os.path.basename(str(cmd[0])) if cmd else ""
        if bin_name in _BLOCKED_LLM_BINS:
            raise RuntimeError(
                f"Blocked real LLM CLI call in tests: {bin_name!r}. This would "
                "invoke the box's authenticated CLI and burn real tokens. Use "
                "a mock adapter (_DryRunAdapter) or patch llm._run_subprocess_safe."
            )
        return _real(cmd, **kwargs)

    monkeypatch.setattr(llm, "_run_subprocess_safe", _guarded)
    yield


@pytest.fixture(autouse=True)
def _no_session_fork(monkeypatch):
    """subprocess.session_fork defaults ON (Jeremy decree 2026-08-08); in
    tests the lane is forced OFF so no adapter test ever spawns a real
    `claude -p` seed call (the seed path uses raw subprocess.run, which
    _block_subprocess_llm does not intercept). TestSessionFork re-enables
    it explicitly against a mocked subprocess.run."""
    try:
        import llm
    except Exception:
        yield
        return
    monkeypatch.setattr(llm, "_session_fork_enabled", lambda: False)
    yield


@pytest.fixture(autouse=True)
def _clear_current_run_dir():
    """Reset the runs.py current-run-dir global between tests.

    handle() pins the run dir via set_current_run_dir() and only the CLI
    entry point clears it (the documented contract: programmatic callers
    that care about isolation clear it themselves). Tests are exactly such
    callers — a leaked run dir makes runs.artifact_dir() route later tests'
    artifacts into the stale run's build/ instead of projects/<p>/artifacts
    (the order-dependent plan-manifest failures in test_agent_loop.py).
    """
    yield
    try:
        import runs
        runs.set_current_run_dir(None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clear_default_subprocess_cwd():
    """Reset the run-scoped ambient agentic cwd between tests.

    run_agent_loop sets llm._DEFAULT_SUBPROCESS_CWD to the project dir and does
    NOT reset it in production (quality_gate, which runs after the loop, should
    inherit it). Across tests that global would otherwise bleed — a loop test
    leaving it set could make a later test's subprocess resolution point at a
    stale tmp project dir. Clear it both before and after each test.
    """
    try:
        import llm
        llm.set_default_subprocess_cwd(None)
    except Exception:
        pass
    yield
    try:
        import llm
        llm.set_default_subprocess_cwd(None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clear_backend_circuit():
    """Reset the process-wide billing/auth circuit breaker between tests.

    A test that trips the circuit (e.g. a 402 through FailoverAdapter) would
    otherwise make a later test's same-named fake backend get skipped as
    deterministically dead. Clear before and after, like the cwd fixture.
    """
    try:
        import llm
        llm._circuit_clear()
    except Exception:
        pass
    yield
    try:
        import llm
        llm._circuit_clear()
    except Exception:
        pass
