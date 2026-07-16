"""Tests for viz_server.py — read-only HTTP server for run-visibility pages.

Design: BACKLOG.md "General-purpose visualization server". The core safety
property under test is the path allowlist (`_resolve_allowed_path`): only
"index.html" at the document root and "<run-dir-name>/build/**" are
servable — everything else (source/, artifact/, metadata.json, traversal
attempts) must be denied before any filesystem access.
"""

import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import viz_server as vs


# ---------------------------------------------------------------------------
# _resolve_allowed_path — pure allowlist logic, no server needed
# ---------------------------------------------------------------------------

@pytest.fixture
def rundir_root(tmp_path):
    root = tmp_path / "runs"
    rd = root / "eager-otter"
    (rd / "build" / "calls").mkdir(parents=True)
    (rd / "source").mkdir(parents=True)
    (rd / "artifact").mkdir(parents=True)
    (root / "index.html").write_text("<html>index</html>")
    (rd / "build" / "loop-abc-report.html").write_text("<html>report</html>")
    (rd / "build" / "calls" / "call-00001.json").write_text("{}")
    (rd / "source" / "metadata.json").write_text("{}")  # not the real location, just a denial probe
    (rd / "metadata.json").write_text("{}")
    (rd / "artifact" / "repo.bundle").write_text("bundle")
    return root


def test_allows_root_index(rundir_root):
    assert vs._resolve_allowed_path("/index.html", rundir_root) == (rundir_root / "index.html").resolve()


def test_allows_build_report(rundir_root):
    got = vs._resolve_allowed_path("/eager-otter/build/loop-abc-report.html", rundir_root)
    assert got == (rundir_root / "eager-otter" / "build" / "loop-abc-report.html").resolve()


def test_allows_nested_call_record(rundir_root):
    got = vs._resolve_allowed_path("/eager-otter/build/calls/call-00001.json", rundir_root)
    assert got == (rundir_root / "eager-otter" / "build" / "calls" / "call-00001.json").resolve()


def test_denies_source(rundir_root):
    assert vs._resolve_allowed_path("/eager-otter/source/metadata.json", rundir_root) is None


def test_denies_artifact(rundir_root):
    assert vs._resolve_allowed_path("/eager-otter/artifact/repo.bundle", rundir_root) is None


def test_denies_rundir_root_metadata(rundir_root):
    assert vs._resolve_allowed_path("/eager-otter/metadata.json", rundir_root) is None


def test_denies_bare_rundir(rundir_root):
    assert vs._resolve_allowed_path("/eager-otter/", rundir_root) is None
    assert vs._resolve_allowed_path("/eager-otter", rundir_root) is None


def test_denies_bare_build_dir(rundir_root):
    assert vs._resolve_allowed_path("/eager-otter/build", rundir_root) is None
    assert vs._resolve_allowed_path("/eager-otter/build/", rundir_root) is None


def test_denies_traversal(rundir_root):
    assert vs._resolve_allowed_path("/eager-otter/build/../../../etc/passwd", rundir_root) is None
    assert vs._resolve_allowed_path("/../secrets/.env", rundir_root) is None


def test_denies_empty_path(rundir_root):
    assert vs._resolve_allowed_path("/", rundir_root) is None


def test_allows_missing_file_shape(rundir_root):
    # Shape is allowed even if the file doesn't exist yet — existence is the
    # underlying HTTP handler's problem (404), not the allowlist's.
    got = vs._resolve_allowed_path("/eager-otter/build/not-written-yet.html", rundir_root)
    assert got == (rundir_root / "eager-otter" / "build" / "not-written-yet.html").resolve()


# ---------------------------------------------------------------------------
# Live server — integration smoke test
# ---------------------------------------------------------------------------

@pytest.fixture
def live_server(rundir_root):
    handler_cls = vs._make_handler_class(rundir_root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_serves_allowed_file(live_server):
    with urllib.request.urlopen(f"{live_server}/index.html") as r:
        assert r.status == 200
        assert b"index" in r.read()


def test_rejects_denied_path(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{live_server}/eager-otter/source/metadata.json")
    assert exc.value.code == 403


def test_rejects_post(live_server):
    req = urllib.request.Request(f"{live_server}/index.html", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 405


def test_rejects_directory_listing(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{live_server}/eager-otter/build/calls/")
    assert exc.value.code == 403


# ---------------------------------------------------------------------------
# ensure_running — viz.autostart (default off; spawn only when port is free)
# ---------------------------------------------------------------------------

@pytest.fixture
def autostart_env(monkeypatch, tmp_path):
    """Isolate the pid/log files and give tests a config knob."""
    import config
    monkeypatch.setattr(vs, "_PID_FILE", str(tmp_path / "viz.pid"))
    monkeypatch.setattr(vs, "_LOG_FILE", str(tmp_path / "viz.log"))
    cfg = {}
    monkeypatch.setattr(config, "get", lambda key, default=None: cfg.get(key, default))
    return cfg


def test_autostart_default_off_no_spawn(autostart_env, monkeypatch):
    import subprocess
    def _boom(*a, **k):
        raise AssertionError("must not spawn when viz.autostart is off")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    assert vs.ensure_running() is False


def test_autostart_skips_when_port_occupied(autostart_env, monkeypatch):
    import socket
    import subprocess
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    autostart_env.update({"viz.autostart": True, "viz.port": port,
                          "viz.host": "0.0.0.0"})
    def _boom(*a, **k):
        raise AssertionError("must not spawn when the port is already served")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    try:
        assert vs.ensure_running() is False
    finally:
        holder.close()


def test_autostart_spawns_detached_when_port_free(autostart_env, monkeypatch, tmp_path):
    import socket
    import subprocess
    with socket.socket() as s:  # find a definitely-free port, then release it
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    autostart_env.update({"viz.autostart": True, "viz.port": free_port})
    spawned = {}
    class _FakeProc:
        pid = 4242
    def _fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        return _FakeProc()
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    assert vs.ensure_running() is True
    assert spawned["cmd"][1].endswith("cli.py")
    assert spawned["cmd"][2:] == ["viz", "serve"]
    assert spawned["kwargs"]["start_new_session"] is True
    assert (tmp_path / "viz.pid").read_text().strip() == "4242"


def test_autostart_failure_is_nonfatal(autostart_env, monkeypatch):
    """Contract: called at goal-run entry — a broken spawn must not raise."""
    import socket
    import subprocess
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    autostart_env.update({"viz.autostart": True, "viz.port": free_port})
    def _boom(*a, **k):
        raise OSError("no exec for you")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    assert vs.ensure_running() is False
