"""Containerized executor — shared constants, config, and docker probes.

The executor lane (worker steps carrying real tools) can optionally run
inside a docker container for filesystem/network isolation. This module is
the single home for everything that describes that container: the image
name, the baked CLI pin, the dedicated auth volume, and the cheap docker
probes that `doctor` and `maro-bootstrap container-setup` report on.

Chunk C1 (this file) ships the *description* of the container — image,
auth, doctor rows, setup instructions — but wires no run through it. Chunk
C2 adds the actual wrap at `llm._run_subprocess_safe` and will import the
command-vector / kill-path helpers from here (keeping that seam's hard-won
behavior in one place rather than forking a parallel adapter — the design
decision in docs/CONTAINER_EXECUTOR_DESIGN.md §2).

Design: docs/CONTAINER_EXECUTOR_DESIGN.md. Nothing here requires docker to
be present — every probe degrades to a clear "not available" reason, and
the tests mock the subprocess boundary entirely (no docker dependency in
CI).
"""

from __future__ import annotations

import subprocess
from typing import Tuple

from config import get

# ---------------------------------------------------------------------------
# Constants — single source of truth for the image identity
# ---------------------------------------------------------------------------

# The claude CLI version baked into the executor image. Confirmed against the
# npm registry at C1 time (2026-07-12); the image tag encodes it so a rebuild
# after a CLI bump produces a distinct, auditable tag. The Dockerfile takes
# this as a build-arg default — re-pin by editing this constant and rebuilding
# with the command `maro-bootstrap container-setup` prints. Confirm the
# current published version with `npm view @anthropic-ai/claude-code version`.
CLAUDE_CLI_VERSION = "2.1.207"

# Default executor image tag. Encodes the CLI pin (design §3: "image version
# is auditable"). Override via `executor.container_image`.
DEFAULT_IMAGE = f"maro-executor:{CLAUDE_CLI_VERSION}"

# Dedicated auth volume (design §3 "Auth — the trap, named"): the container
# never touches host ~/.claude — a named docker volume holds the container's
# own OAuth session, initialized once by the operator. Revoking it revokes
# nothing else.
AUTH_VOLUME = "maro-claude-auth"

# HOME inside the container. Fixed (not the invoking user's home) so the auth
# volume mounts at a known path regardless of the `--user <uid>:<gid>` the
# executor wrap runs with (design §4 uid/gid note). The image makes it
# world-writable so an arbitrary uid can refresh tokens there.
CONTAINER_HOME = "/home/maro"
AUTH_MOUNT = f"{CONTAINER_HOME}/.claude"

# Deterministic per-run container name prefix — makes the kill path
# (`docker kill <name>`) and the stranded-container sweep
# (`docker ps --filter name=maro-exec-`) trivial (design §2 kill path). C2
# consumes this; defined here so both sides agree on the spelling.
NAME_PREFIX = "maro-exec-"

DEFAULT_NETWORK = "bridge"

# Valid `executor.container` modes.
_MODES = ("off", "on", "require")

# Short timeouts — these are health probes, not workloads. `docker version`
# talks to the daemon and can hang if it's wedged; cap it.
_PROBE_TIMEOUT_S = 8
# The login probe actually launches a container and makes one cheap API call.
_LOGIN_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def container_mode() -> str:
    """Normalized `executor.container` mode: one of off / on / require.

    Coerces booleans (`on: true`) and unknown values fail-safe to "off"
    (host execution, fence-only) — an unrecognized mode must never silently
    enable or hard-require containers. `doctor` surfaces the raw value when
    it doesn't normalize cleanly, so the fail-safe isn't a silent swallow.
    """
    raw = get("executor.container", "off")
    if isinstance(raw, bool):
        return "on" if raw else "off"
    val = str(raw).strip().lower()
    return val if val in _MODES else "off"


def container_mode_raw() -> str:
    """The `executor.container` value as configured, for honest reporting."""
    return str(get("executor.container", "off")).strip()


def container_image() -> str:
    """The executor image tag (config `executor.container_image` or default)."""
    return str(get("executor.container_image", DEFAULT_IMAGE) or DEFAULT_IMAGE)


# ---------------------------------------------------------------------------
# Docker probes — thin, mockable subprocess wrappers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int) -> Tuple[bool, str]:
    """Run a docker command, returning (ok, detail).

    Maps the two ways docker can be "not present" — binary missing
    (FileNotFoundError) and daemon wedged (TimeoutExpired) — to a clear
    reason rather than a stack trace. A non-zero exit returns its stderr so
    the caller can report *why* (e.g. "Cannot connect to the Docker daemon").
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "docker binary not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"docker timed out after {timeout}s (daemon wedged?)"
    if proc.returncode == 0:
        return True, (proc.stdout or "").strip()
    # Non-zero exit: surface the first line of stderr (docker's own reason,
    # e.g. "Cannot connect to the Docker daemon"), falling back to the code.
    lines = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
    return False, lines[0] if lines else f"exit {proc.returncode}"


def docker_probe() -> Tuple[bool, str]:
    """Is the docker daemon reachable? Returns (ok, server-version-or-reason).

    Uses `docker version` (not `docker --version`) so the daemon, not just
    the client binary, is verified — running a container needs the daemon.
    """
    ok, detail = _run(
        ["docker", "version", "--format", "{{.Server.Version}}"], _PROBE_TIMEOUT_S
    )
    if ok:
        return True, f"docker {detail}" if detail else "docker daemon reachable"
    return False, detail


def image_probe(image: str | None = None) -> Tuple[bool, str]:
    """Is the executor image built locally? Returns (ok, detail)."""
    img = image or container_image()
    ok, detail = _run(["docker", "image", "inspect", img], _PROBE_TIMEOUT_S)
    if ok:
        return True, img
    return False, f"{img} not built — run `maro-bootstrap container-setup` for the build command"


def auth_volume_probe() -> Tuple[bool, str]:
    """Does the dedicated auth volume exist? Returns (ok, detail).

    Presence of the volume is the cheap, no-token signal that
    `container-setup`'s login step was run. It does NOT prove the session is
    still valid — that's what `login_probe` (token-spending) is for.
    """
    ok, _ = _run(["docker", "volume", "inspect", AUTH_VOLUME], _PROBE_TIMEOUT_S)
    if ok:
        return True, f"volume {AUTH_VOLUME} present"
    return False, (
        f"volume {AUTH_VOLUME} missing — run the login step from "
        "`maro-bootstrap container-setup`"
    )


def login_probe(image: str | None = None) -> Tuple[bool, str]:
    """Prove the container can actually reach the API when logged in.

    Runs one cheap `claude -p ok --tools ""` inside the container against the
    auth volume — the real "installed but not logged in" catch (design §3).
    Spends a token and needs the daemon + network, so callers gate this
    behind an explicit opt-in (doctor's `--live`), never the default sweep.
    """
    img = image or container_image()
    cmd = [
        "docker", "run", "--rm",
        "-e", f"HOME={CONTAINER_HOME}",
        "-v", f"{AUTH_VOLUME}:{AUTH_MOUNT}",
        "--network", str(get("executor.container_network", DEFAULT_NETWORK) or DEFAULT_NETWORK),
        img,
        "claude", "-p", "ok", "--tools", "",
    ]
    ok, detail = _run(cmd, _LOGIN_TIMEOUT_S)
    if ok:
        return True, "container login ok"
    return False, f"container login failed — run the login step; ({detail[:80]})"


# ---------------------------------------------------------------------------
# Operator instructions (printed by `maro-bootstrap container-setup`)
# ---------------------------------------------------------------------------

def build_command(image: str | None = None) -> str:
    """The exact `docker build` command for the executor image."""
    img = image or container_image()
    return (
        f"docker build \\\n"
        f"    --build-arg CLAUDE_CLI_VERSION={CLAUDE_CLI_VERSION} \\\n"
        f"    -t {img} \\\n"
        f"    -f deploy/docker/Dockerfile.executor ."
    )


def login_command(image: str | None = None) -> str:
    """The interactive `docker run ... claude /login` that seeds the auth volume."""
    img = image or container_image()
    return (
        f"docker run -it --rm \\\n"
        f"    -e HOME={CONTAINER_HOME} \\\n"
        f"    -v {AUTH_VOLUME}:{AUTH_MOUNT} \\\n"
        f"    {img} \\\n"
        f"    claude /login"
    )


def container_setup_instructions(image: str | None = None) -> str:
    """Full operator walkthrough: build the image, seed the auth volume.

    Creates nothing itself (hook-instructions posture, same as the
    supervision story) — the operator runs the two commands. Doctor's
    container rows then report the resulting state.
    """
    img = image or container_image()
    return f"""\
Containerized executor setup — worker steps that carry real tools run inside
a docker container for filesystem/network isolation. Off by default
(executor.container: off); this prepares the image + auth so you can flip it
on. Docker is never a hard Maro requirement — with it off, worker steps run
on the host under the write-fence exactly as before.

1. Build the executor image (bakes claude-code {CLAUDE_CLI_VERSION}):

{build_command(img)}

   Re-pin the CLI by editing CLAUDE_CLI_VERSION in src/container_exec.py
   (confirm the current version: npm view @anthropic-ai/claude-code version)
   and rebuilding — the image tag tracks the pin.

2. Seed the dedicated auth volume with a one-time interactive login. This is
   a SECOND OAuth session on your account (same subscription/quota/ToS as the
   host lane) living only in the {AUTH_VOLUME} volume — the container never
   touches your host ~/.claude:

{login_command(img)}

3. Verify:

    maro-doctor            # docker/image/auth-volume rows (cheap)
    maro-doctor --live     # also runs a real login probe through the container

4. Flip it on when ready — user or workspace config.yml:

    executor:
      container: on        # or `require` to refuse (not degrade) without docker

Reference: docs/CONTAINER_EXECUTOR_DESIGN.md.
"""
