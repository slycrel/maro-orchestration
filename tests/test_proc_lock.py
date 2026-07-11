"""proc_lock._run_dir isolation tripwire (ops-r2-05).

Purgatorio r2's one refuted-then-live-reproduced finding: a test that stubs
sys.modules["config"] with a partial namespace makes `from config import
workspace_root` fail inside proc_lock._run_dir, and the old home-only
fallback then stamped the REAL workspace's heartbeat.pid — silently
bypassing the MARO_WORKSPACE test isolation every full pytest run. The
fallback must resolve the same env vars config.workspace_root() does before
ever touching Path.home().
"""

import sys
import types

import proc_lock


def test_run_dir_fallback_respects_workspace_env(monkeypatch, tmp_path):
    """config import broken + MARO_WORKSPACE set → run dir stays isolated."""
    monkeypatch.setitem(sys.modules, "config", types.SimpleNamespace())
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))

    d = proc_lock._run_dir()

    assert d == tmp_path.resolve() / "run"


def test_pidfile_path_isolated_under_partial_config_stub(monkeypatch, tmp_path):
    """The exact ops-r2-05 shape: partial config stub, pidfile must not
    resolve anywhere near the real home workspace."""
    monkeypatch.setitem(
        sys.modules, "config",
        types.SimpleNamespace(get=lambda key, default=None: default),
    )
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))

    p = proc_lock.pidfile_path("heartbeat")

    assert str(p).startswith(str(tmp_path.resolve()))
    assert p.name == "heartbeat.pid"
