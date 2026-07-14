"""Real-docker E2E tier for the containerized executor (C4 burn-in).

C1–C3's ~130 tests mock the docker subprocess boundary entirely. This tier
covers what mocks CANNOT: that real docker actually honors the command vectors
and mount specs our code emits — container lifecycle, kill-by-name, the
stranded-container reaper reaping a genuinely dead-labeled container, bind-mount
read-only fencing, `--user` uid mapping (host-owned writes), colon-in-path mount
safety, `--network none` isolation, and the executor image's baked toolset.

None of this needs Claude auth: the container mechanics are proven with plain
`alpine` commands and `claude --version`, never a token-spending `claude -p` run.

Skips cleanly when docker is unavailable (module-level skipif), so a docker-less
CI box is never broken by this file. The executor-image tests skip additionally
when the image isn't built. Runs against alpine (pre-pulled) for mechanics.

    docs/CONTAINER_EXECUTOR_DESIGN.md §9 (C3 residual "Real-docker E2E … is a C4
    item"), docs/CONTAINER_BURN_IN.md §5 (go/no-go).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import container_exec as ce
from process_identity import pid_alive


# ---------------------------------------------------------------------------
# Availability gates + helpers
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    ok, _ = ce.docker_probe()
    return ok


def _image_available() -> bool:
    ok, _ = ce.image_probe(ce.DEFAULT_IMAGE)
    return ok


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="real docker daemon not reachable — this tier is box-only, never CI",
)

_ALPINE = "alpine:latest"
# Unique per test-process run so parallel/other runs never collide or clobber.
_RUN_TAG = f"e2e{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _docker(args, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _rm(name: str) -> None:
    """Best-effort force-remove — teardown must never leave strays on the box."""
    try:
        _docker(["rm", "-f", name], timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


def _is_running(name: str) -> bool:
    r = _docker(["ps", "--filter", f"name={name}", "--format", "{{.Names}}"])
    return name in (r.stdout or "").split()


def _exists(name: str) -> bool:
    r = _docker(["ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"])
    return name in (r.stdout or "").split()


def _wait_stopped(name: str, timeout: float = 20.0) -> bool:
    """`docker kill` signals then returns; the container's actual stop + `--rm`
    removal is asynchronous (and slow on a 2014 box under CPU load). Poll for it
    to leave the running set instead of racing the daemon."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_running(name):
            return True
        time.sleep(0.25)
    return not _is_running(name)


@pytest.fixture()
def container_names():
    """Track container names and force-remove them all on teardown."""
    names: list = []
    yield names
    for n in names:
        _rm(n)


def _dead_pid() -> int:
    """A PID that has definitely exited (reaped child). Skips on PID reuse."""
    p = subprocess.Popen(["sh", "-c", "exit 0"])
    p.wait()
    if pid_alive(p.pid):  # pragma: no cover — reuse is rare
        pytest.skip("child PID was reused before assertion — retry")
    return p.pid


# ---------------------------------------------------------------------------
# Container lifecycle + kill-by-name (design §2 kill path)
# ---------------------------------------------------------------------------

class TestLifecycleAndKill:
    def test_kill_container_by_name(self, container_names):
        name = f"{ce.NAME_PREFIX}{_RUN_TAG}-kill"
        container_names.append(name)
        r = _docker(["run", "-d", "--name", name, _ALPINE, "sleep", "60"])
        assert r.returncode == 0, r.stderr
        assert _is_running(name)
        # The real kill path the wrap uses (os.killpg only reaps the client).
        ce.kill_container(name)
        assert _wait_stopped(name), "container still running after kill_container"

    def test_kill_missing_container_is_noop(self):
        # Swallows errors — the container may already be gone (--rm) or absent.
        ce.kill_container(f"{ce.NAME_PREFIX}{_RUN_TAG}-does-not-exist")


# ---------------------------------------------------------------------------
# Stranded-container reaper — against REAL dead/live/unlabeled containers
# ---------------------------------------------------------------------------

class TestStrandedReaper:
    def test_reaps_container_with_dead_owner_label(self, container_names):
        dead = _dead_pid()
        name = f"{ce.NAME_PREFIX}{_RUN_TAG}-{os.getpid()}-0"
        container_names.append(name)
        r = _docker(["run", "-d", "--rm", "--name", name,
                     "--label", f"maro.owner_pid={dead}", _ALPINE, "sleep", "300"])
        assert r.returncode == 0, r.stderr
        assert _is_running(name)

        killed = ce.sweep_stranded_containers()  # real _pid_alive
        assert name in killed
        assert _wait_stopped(name)  # --rm removes it on kill (async)

    def test_spares_container_with_live_owner(self, container_names):
        # Owned by THIS (alive) process — a live run's in-flight container.
        name = f"{ce.NAME_PREFIX}{_RUN_TAG}-{os.getpid()}-1"
        container_names.append(name)
        r = _docker(["run", "-d", "--rm", "--name", name,
                     "--label", f"maro.owner_pid={os.getpid()}", _ALPINE, "sleep", "300"])
        assert r.returncode == 0, r.stderr

        killed = ce.sweep_stranded_containers()
        assert name not in killed
        assert _is_running(name)  # untouched

    def test_ignores_unlabeled_lookalike(self, container_names):
        # Same name prefix, NO maro.owner_pid label → not ours, never reaped
        # (adversarial-review 2026-07-12: the old substring filter could kill it).
        dead = _dead_pid()
        name = f"{ce.NAME_PREFIX}{_RUN_TAG}-unlabeled"
        container_names.append(name)
        r = _docker(["run", "-d", "--name", name, _ALPINE, "sleep", "300"])
        assert r.returncode == 0, r.stderr

        killed = ce.sweep_stranded_containers(pid_alive=lambda p: p != dead)
        assert name not in killed
        assert _is_running(name)


# ---------------------------------------------------------------------------
# Mount-fence enforcement — real bind mounts honor the fence (design §4/§5)
# ---------------------------------------------------------------------------

def _bind_specs(mounts):
    """Turn build_mount_map output into `docker run --mount` args — the exact
    grammar build_run_command emits."""
    args = []
    for host, mode in mounts:
        spec = f"type=bind,source={host},target={host}"
        if mode == "ro":
            spec += ",readonly"
        args += ["--mount", spec]
    return args


class TestMountFence:
    def test_readonly_bind_blocks_writes(self, tmp_path):
        ref = tmp_path / "ref"; ref.mkdir()
        (ref / "data.txt").write_text("original\n", encoding="utf-8")
        r = _docker(["run", "--rm",
                     "--mount", f"type=bind,source={ref},target={ref},readonly",
                     _ALPINE, "sh", "-c", f"echo tampered >> {ref}/data.txt"])
        assert r.returncode != 0  # read-only file system
        assert (ref / "data.txt").read_text(encoding="utf-8") == "original\n"

    def test_rw_bind_write_is_host_owned(self, tmp_path):
        work = tmp_path / "work"; work.mkdir()
        r = _docker(["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                     "--mount", f"type=bind,source={work},target={work}",
                     _ALPINE, "sh", "-c", f"echo made > {work}/out.txt"])
        assert r.returncode == 0, r.stderr
        out = work / "out.txt"
        assert out.read_text(encoding="utf-8") == "made\n"
        # uid/gid friction check: the merged-back file is operator-owned, not root.
        assert out.stat().st_uid == os.getuid()

    def test_colon_in_path_binds_cleanly(self, tmp_path):
        # `--mount type=bind` handles a ':' in the path; `-v host:host:mode`
        # would misparse it (adversarial-review 2026-07-12, why we switched).
        weird = tmp_path / "a:b"; weird.mkdir()
        r = _docker(["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                     "--mount", f"type=bind,source={weird},target={weird}",
                     _ALPINE, "sh", "-c", f"echo ok > '{weird}/marker'"])
        assert r.returncode == 0, r.stderr
        assert (weird / "marker").read_text(encoding="utf-8") == "ok\n"

    def test_build_mount_map_specs_fence_ro_from_rw(self, tmp_path):
        # End-to-end: our real build_mount_map output, fed to real docker,
        # makes cwd writable and a ro reference mount read-only.
        cwd = tmp_path / "proj"; cwd.mkdir()
        ref = tmp_path / "ref"; ref.mkdir()
        (ref / "d.txt").write_text("ro\n", encoding="utf-8")
        mounts = ce.build_mount_map(str(cwd), ro_mounts=[str(ref)])
        specs = _bind_specs(mounts)
        user = ["--user", f"{os.getuid()}:{os.getgid()}"]

        rw = _docker(["run", "--rm", *user, *specs, _ALPINE,
                      "sh", "-c", f"echo w > {cwd}/w.txt"])
        assert rw.returncode == 0, rw.stderr
        assert (cwd / "w.txt").exists()

        ro = _docker(["run", "--rm", *user, *specs, _ALPINE,
                      "sh", "-c", f"echo x >> {ref}/d.txt"])
        assert ro.returncode != 0  # ro reference mount is not writable
        assert (ref / "d.txt").read_text(encoding="utf-8") == "ro\n"


# ---------------------------------------------------------------------------
# Network isolation — container_network: none truly isolates (design §6)
# ---------------------------------------------------------------------------

class TestNetworkIsolation:
    def test_network_none_has_no_ethernet(self):
        r = _docker(["run", "--rm", "--network", "none", _ALPINE, "ls", "/sys/class/net"])
        assert r.returncode == 0, r.stderr
        ifaces = set(r.stdout.split())
        assert "lo" in ifaces
        assert not any(i.startswith("eth") for i in ifaces), ifaces

    def test_default_bridge_has_ethernet(self):
        r = _docker(["run", "--rm", "--network", ce.DEFAULT_NETWORK, _ALPINE,
                     "ls", "/sys/class/net"])
        assert r.returncode == 0, r.stderr
        assert any(i.startswith("eth") for i in r.stdout.split())


# ---------------------------------------------------------------------------
# Executor image — baked toolset + identity (needs the image, NOT auth)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _image_available(),
    reason="executor image not built — run `maro-bootstrap container-setup`",
)
class TestExecutorImage:
    def test_baked_toolset_present(self):
        # The design's env-dependency contract: git/python3/curl/claude/node.
        for tool in ("git", "python3", "curl", "claude", "node"):
            r = _docker(["run", "--rm", ce.DEFAULT_IMAGE, "sh", "-c", f"command -v {tool}"])
            assert r.returncode == 0 and r.stdout.strip(), f"{tool} missing from image"

    def test_baked_cli_version_matches_pin(self):
        r = _docker(["run", "--rm", ce.DEFAULT_IMAGE, "claude", "--version"])
        assert r.returncode == 0, r.stderr
        assert ce.CLAUDE_CLI_VERSION in r.stdout

    def test_runs_as_invoking_uid(self):
        r = _docker(["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                     ce.DEFAULT_IMAGE, "id", "-u"])
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == str(os.getuid())

    def test_home_is_writable_under_arbitrary_uid(self):
        # The auth volume mounts at $HOME/.claude; token refresh must be able to
        # write there under `--user <host-uid>` (design §4 / Dockerfile chmod).
        r = _docker(["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                     "-e", f"HOME={ce.CONTAINER_HOME}", ce.DEFAULT_IMAGE,
                     "sh", "-c", f"touch {ce.CONTAINER_HOME}/.claude/probe && echo ok"])
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "ok"
