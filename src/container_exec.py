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

import contextvars
import itertools
import logging
import os
import re
import subprocess
import tempfile
import time
from typing import Callable, Optional, Tuple

from config import get
from process_identity import owner_is_current, pid_alive as process_pid_alive, process_start_token

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — single source of truth for the image identity
# ---------------------------------------------------------------------------

# The claude CLI version baked into the executor image. Confirmed against the
# npm registry at C1 time (2026-07-12); the image tag encodes it so a rebuild
# after a CLI bump produces a distinct, auditable tag. The Dockerfile takes
# this as a build-arg default — re-pin by editing this constant and rebuilding
# with the command `maro-bootstrap container-setup` prints. Confirm the
# current published version with `npm view @anthropic-ai/claude-code version`.
CLAUDE_CLI_VERSION = "2.1.210"

# Image contents revision, independent of the CLI pin (Jeremy 2026-08-06:
# "in favor of adorning the version somehow… otherwise let's keep it simple").
#
# The tag used to be `maro-executor:{CLAUDE_CLI_VERSION}` alone, which made it
# a LIE about everything except the CLI: change the apt toolset, rebuild under
# the same tag, and `maro-executor:2.1.210` now names two different images —
# defeating the Dockerfile's own "image version auditable" goal (design §3).
# The CLI pin answers "which claude"; this answers "which image". Bump it
# whenever the Dockerfile's contents change without a CLI bump; it resets to
# 1 when CLAUDE_CLI_VERSION moves, since that mints a fresh tag namespace.
#
# Deliberately a hand-bumped integer rather than a content digest: a digest is
# self-maintaining but unreadable in `docker images` and in an operator's
# muscle memory, and the thing being audited here is a human decision to
# change the toolset. If drift between this and the Dockerfile ever bites in
# practice, that is the moment to switch to a digest — not before.
IMAGE_REVISION = 3

# The first image revision whose build bakes the maro verb CLIs
# (maro-read / maro-fetch shims + src tree, Dockerfile r3 2026-08-13).
# image_bakes_verbs() keys on this so prompts advertise the verbs only
# into containers that actually have them (honest absence, A/B-4 class).
_VERBS_BAKED_FROM_REVISION = 3

# Default executor image tag. Encodes the CLI pin AND the contents revision
# (design §3: "image version is auditable"). Override via
# `executor.container_image`.
DEFAULT_IMAGE = f"maro-executor:{CLAUDE_CLI_VERSION}-r{IMAGE_REVISION}"

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


_IMAGE_TAG_RE = re.compile(r"^maro-executor:[^:]+-r(\d+)$")


def hosted_free_container_env() -> dict:
    """Env values that transport hosted-free capability into an executor
    container at SPIN-UP (Jeremy decree 2026-08-13: keys are injected as
    ENV values from host-held storage, never baked into the image).

    Returns {} unless the image bakes the verbs, the host operator has
    opted into hosted-free egress (consent is a host-side decision — the
    env flag below only TRANSPORTS it), and at least one provider key is
    present on the host (process env or the credentials .env). The values
    ride the docker client's process env + bare `-e NAME` flags, never
    the docker argv, so keys don't appear in host process listings.
    Never raises; never logs values."""
    try:
        if not image_bakes_verbs():
            return {}
        import hosted_free as _hf
        if not _hf.hosted_free_enabled():
            return {}
        env_file = _hf._load_env()
        out = {}
        for provider in _hf.provider_order():
            key_name = _hf._ENV_KEYS.get(provider)
            if not key_name:
                continue
            val = _hf._get_key(key_name, env_file)
            if val:
                out[key_name] = val
        if not out:
            return {}
        out["MARO_HOSTED_FREE_ENABLED"] = "1"
        return out
    except Exception:
        log.debug("hosted_free_container_env failed (no keys injected)",
                  exc_info=True)
        return {}


def image_bakes_verbs() -> bool:
    """Does the CONFIGURED executor image carry the baked maro verbs?

    Parsed from the maro-executor tag's revision suffix; a custom image
    (executor.container_image naming anything else) answers False —
    conservative, because advertising a verb into an image that lacks it
    is the A/B-4 confound, while under-advertising only costs the
    optimization. Honest limit (review 2026-08-13): the tag is the
    OPERATOR'S declaration, not capability evidence — docker tags are
    mutable, and an operator who retags an r2 image as `-r3` has asserted
    verbs that aren't there. Config is a host-trust surface here, same as
    executor.container itself; a runtime shim probe would be the upgrade
    if that trust ever proves misplaced. Never raises."""
    try:
        m = _IMAGE_TAG_RE.match(container_image())
        return bool(m) and int(m.group(1)) >= _VERBS_BAKED_FROM_REVISION
    except Exception:
        return False


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


def _user_args() -> list:
    """`--user <uid>:<gid>` for the invoking user (empty on non-posix).

    The login step, the login probe, AND the executor wrap must ALL run as the
    SAME uid: the auth volume's OAuth files are written by whichever uid runs
    `/login`, and a mismatched uid can't read/refresh them (adversarial-review
    2026-07-12 — probing/logging in as root while the executor runs as the host
    uid silently broke auth and let `--live` falsely certify it)."""
    if hasattr(os, "getuid"):
        return ["--user", f"{os.getuid()}:{os.getgid()}"]
    return []


def login_probe(image: str | None = None) -> Tuple[bool, str]:
    """Prove the container can actually reach the API when logged in.

    Runs one cheap `claude -p ok --tools ""` inside the container against the
    auth volume — the real "installed but not logged in" catch (design §3).
    Runs as the SAME uid the executor uses (see _user_args) so it validates the
    real production identity, not root. Spends a token and needs the daemon +
    network, so callers gate this behind doctor's `--live`, never the sweep.
    """
    img = image or container_image()
    cmd = [
        "docker", "run", "--rm", *_user_args(),
        "-e", f"HOME={CONTAINER_HOME}",
        "--mount", f"type=volume,source={AUTH_VOLUME},target={AUTH_MOUNT}",
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
    """The interactive `docker run ... claude /login` that seeds the auth volume.

    Runs as `$(id -u):$(id -g)` — the SAME uid the executor wrap uses — so the
    OAuth files land owned by that uid and stay readable/refreshable at run time
    (adversarial-review 2026-07-12)."""
    img = image or container_image()
    return (
        f"docker run -it --rm \\\n"
        f"    --user $(id -u):$(id -g) \\\n"
        f"    -e HOME={CONTAINER_HOME} \\\n"
        f"    --mount type=volume,source={AUTH_VOLUME},target={AUTH_MOUNT} \\\n"
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
   and rebuilding — the image tag tracks the pin. The `-r<N>` suffix is
   IMAGE_REVISION, bumped when the image CONTENTS change without a CLI
   bump, so a tag always names exactly one image.

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


# ===========================================================================
# C2 — the wrap: containerize an executor call, kill by name, reap strays
# ===========================================================================
#
# The decision ("should THIS call run in a container?") is made by the caller
# that knows `no_tools` (llm.ClaudeSubprocessAdapter.complete); the wrapping +
# kill path live at the `_run_subprocess_safe` seam (design §2) so every
# hard-won behavior there (liveness, stream probe, payload-first rc) is reused
# rather than forked. C2 ships a MINIMAL mount set (the working dir rw + the
# auth volume); the full fence-root → mount translation and the self-dev
# scratch-clone flow are C3.

_KILL_TIMEOUT_S = 10
_SWEEP_TIMEOUT_S = 10

# Per-process monotonic sequence for unique container names (combined with the
# PID it makes names collision-free across concurrent AND successive processes).
_seq_counter = itertools.count()

# Degrade-warning throttle: docker is probed FRESH on every executor call (they
# are heavy multi-second steps, so a ~100ms `docker version` is negligible and
# — unlike a cached probe — keeps the degrade/refuse decision honest when the
# daemon comes up or goes down mid-process; adversarial-review 2026-07-12).
# We only throttle the WARNING so a persistently-down daemon doesn't log once
# per step. Reset by reset_container_caches() (test hook).
_WARN_THROTTLE_S = 60.0
_last_degrade_warn = 0.0
# Separate stamp for the incapable-BACKEND warning below — sharing the
# docker-down throttle would let one warning class silence the other.
_last_backend_warn = 0.0


class ContainerUnavailable(RuntimeError):
    """Raised when `executor.container: require` is set but docker can't run
    the call — the `require` contract refuses rather than silently degrading."""


def enforce_backend_container_contract(adapter, executor: bool) -> None:
    """Executor-lane backend gate (2026-08-13 review residual, fixed
    2026-08-15): `resolve_container_run` lives inside the subprocess
    adapter, so any OTHER backend serving an `executor=True` call simply
    never made a container decision — under `require` that silently
    voided a security control (isolation-or-nothing), and under `on` the
    host run was invisible.

    Called at the executor seams (step_exec / workers) with the adapter
    about to serve the call. `adapter.container_capable` is a class
    attribute defaulting to False on the LLMAdapter base — a new backend
    is refused under `require` by construction until it actually wires a
    container decision. FailoverAdapter reports any-inner-capable and its
    walk skips incapable adapters for require-mode executor calls, so a
    capable fallback still serves.

    require + incapable → ContainerUnavailable (refuse loudly).
    on + incapable → run on the host, with a throttled warning (SF-6:
    the difference between sandboxed and not must be visible).
    off / non-executor / capable → no-op.
    """
    global _last_backend_warn
    if not executor or bool(getattr(adapter, "container_capable", False)):
        return
    mode = container_mode()
    if mode == "off":
        return
    backend = getattr(adapter, "backend", "?")
    if mode == "require":
        raise ContainerUnavailable(
            f"executor.container=require but the {backend!r} backend cannot "
            "containerize executor work — refusing to run it on the host. "
            "Use the subprocess (claude) backend for executor steps, or set "
            "executor.container to on/off.")
    warn_backend_host_run(backend)


def warn_backend_host_run(backend: str) -> None:
    """Throttled SF-6 visibility warning: an executor call is served by a
    backend that cannot containerize while executor.container=on.

    ONE implementation with one throttle stamp, two callers: the seam
    guard above (bare adapters / all-incapable wrappers) and the
    FailoverAdapter walk (2026-08-15 review, 3-lens consensus: the seam
    guard runs on the WRAPPER, whose capability report is ANY-inner —
    it structurally cannot see which inner actually serves, so a mixed
    list's incapable first pick ran executor work on the host with zero
    warning while the docstrings claimed otherwise)."""
    global _last_backend_warn
    now = time.monotonic()
    if now - _last_backend_warn >= _WARN_THROTTLE_S:
        log.warning(
            "executor.container=on but the %r backend cannot containerize — "
            "executor steps run on the host under the write-fence, NOT "
            "containerized", backend)
        _last_backend_warn = now


# Run-scoped kill switch: when a git-repo run's self-dev scratch clone could NOT
# be provisioned, containerizing it would mount the LIVE repo rw (the exact thing
# the clone exists to prevent). agent_loop sets this so resolve_container_run
# fails CLOSED to host execution — never mounting a live repo in a container
# (adversarial-review 2026-07-13, findings A/M3/S2/A1). A ContextVar so thread
# fan-out inherits it; reset per run by agent_loop.
_container_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "maro_container_suppressed", default=False
)


def set_container_suppressed(on: bool = True) -> None:
    """Force host execution for this run's executor calls (see _container_suppressed)."""
    _container_suppressed.set(bool(on))


def container_suppressed() -> bool:
    return _container_suppressed.get()


def reset_container_caches() -> None:
    """Reset the degrade-warning throttle and the container kill switch. Test hook."""
    global _last_degrade_warn, _last_backend_warn
    _last_degrade_warn = 0.0
    _last_backend_warn = 0.0
    _container_suppressed.set(False)
    _introspection_run.set(False)


# Run-scoped introspection flag (decree 2026-07-18, GOAL_BRAIN Decisions:
# "Install in the container only for the runs that need access"). When a goal
# is classified introspection-shaped — it asks about Maro's own runs/behavior —
# the containerized executor gets read-only provisioning (run records + the
# maro source, see introspection_provision()) instead of blind isolation:
# brisk-saffron (task-…80466244) spent 2.8M tokens / 28min proving only that
# it couldn't see the host records it was asked to diagnose. A ContextVar so
# thread fan-out inherits it; reset per run by agent_loop (finding-D pattern,
# same as the suppression flag above).
_introspection_run: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "maro_introspection_run", default=False
)


def set_introspection_run(on: bool = True) -> None:
    """Mark this run introspection-shaped (see _introspection_run)."""
    _introspection_run.set(bool(on))


def introspection_run() -> bool:
    return _introspection_run.get()


def introspection_provision() -> Optional[dict]:
    """Read-only container provisioning for an introspection-shaped run.

    Returns {"ro_mounts": [host_path, ...], "env": {name: value, ...}} when
    this run is flagged introspective AND `executor.introspection_access`
    (default on; inert unless the container executor itself is on) — else None.

    What is provisioned, deliberately narrow (decree 2026-07-18):
      - the workspace `runs/` dir, ro — the run records the goal is asked to
        reason about. A *descendant* of the workspace root, which itself stays
        hard-forbidden (design §4): memory/, config, secrets are NOT mounted,
        and build_mount_map's forbidden filter still applies to whatever this
        returns (defense in depth — a bug here cannot mount the workspace root).
        Must be a REAL directory: a symlinked `runs/` is refused, or the link
        target (memory/, secrets/, anywhere) would ride the introspection
        grant past the descendant allowance (adversarial-review 2026-07-18).
      - the maro source module dir, ro, plus PYTHONPATH — best-effort CLI:
        `python3 -m <module>` works in-container for stdlib-only readers (the
        image ships python3); modules needing third-party deps fail loudly
        there, and the records mount above remains the actual guarantee.
      - env markers so the worker knows what it has: MARO_INTROSPECTION=1,
        MARO_INTROSPECTION_RUNS=<records path> (identity-mapped host path),
        and MARO_MOUNT_VIEW describing the partial view (no repo root, no
        .git) so the worker never reports its blinkered view as host fact.

    Fails CLOSED, all-or-nothing: no resolvable run-records dir → None, never
    a source-only mount with the introspection marker set — that would tell
    the worker it can introspect while reproducing the exact records-blind
    failure this exists to fix (adversarial-review 2026-07-18, consensus
    finding). Worst case is always the pre-decree behavior, never a broader
    mount.
    """
    try:
        if not introspection_run():
            return None
        if not get("executor.introspection_access", True):
            return None
        from config import workspace_root
        ws_real = os.path.realpath(str(workspace_root()))
        runs_dir = os.path.join(ws_real, "runs")
        runs_real = os.path.realpath(runs_dir)
        if runs_real != runs_dir or not os.path.isdir(runs_real):
            log.warning(
                "introspection_provision: %s missing or symlinked — "
                "run stays isolated", runs_dir)
            return None
        ro_mounts = [runs_real]
        env = {
            "MARO_INTROSPECTION": "1",
            "MARO_INTROSPECTION_RUNS": runs_real,
            # Mount-blindness marker (BACKLOG 2026-08-02, run d9607baa's
            # sibling find): nothing told the worker its view was partial, so
            # it reported "the checkout was never git-initialized" as a
            # machine fact — false on host. The prompt-side warning lives in
            # step_exec; this is the in-container ground truth a probe can
            # check.
            "MARO_MOUNT_VIEW": (
                "partial: maro source + run records only "
                "(no repo root, no .git, no workspace memory/config)"),
        }
        module_dir = os.path.dirname(os.path.realpath(__file__))
        if os.path.isdir(module_dir):
            ro_mounts.append(module_dir)
            env["PYTHONPATH"] = module_dir
        return {"ro_mounts": ro_mounts, "env": env}
    except Exception as exc:
        log.warning("introspection_provision failed (run stays isolated): %s", exc)
        return None


# ---------------------------------------------------------------------------
# Container auth breaker (2026-08-13)
# ---------------------------------------------------------------------------
# The degrade path in resolve_container_run originally asked one question —
# "is docker up?" — so when the auth volume's OAuth session expired (08-12)
# docker was fine, every executor step entered the container and died with
# "OAuth session expired and could not be refreshed", and the dispatch lane
# was down until a human flipped executor.container off by hand. This breaker
# makes auth failure a degrade condition too, the same reactive shape as the
# llm.py backend circuit: the first failing step trips it (no happy-path
# probe spend), `on` then degrades to host/fence-only, `require` refuses.
#
# Reset is automatic and CHEAP — no login_probe/token spend in the resolve
# path. While tripped, at most every _AUTH_RECHECK_TTL_S we read the volume's
# credentials file (one docker cat, ro mount) and clear the breaker only when
# the file is (a) shaped like a live session (refreshToken present — the CLI
# wipes it after a failed refresh, which is exactly the tripped state) AND
# (b) newer than the trip — i.e. the operator actually re-seeded. Without the
# newer-than-trip guard, a server-side revocation with an intact-looking file
# would clear and re-trip every TTL forever. If a re-seeded session is still
# bad, the first step re-trips: worst case one failed step per re-seed, never
# a flapping lane. State is a file (not module state) so every process in a
# multi-process run sees one breaker, and doctor/system_health can report it.

# Substrings that identify a CLI login/auth failure — deliberately NARROWER
# than llm_errors._AUTH_PATTERNS: generic "401"/"403"/"forbidden" would let a
# worker step that merely *encountered* an HTTP error in its work trip the
# lane. These only match the claude CLI's own login-failure surfaces.
_AUTH_ERROR_MARKERS = (
    "oauth session expired",
    "oauth session has expired",
    "oauth token expired",
    "oauth token has expired",
    "oauth access token expired",
    "oauth token revoked",
    "not logged in",
    "please run /login",
    "invalid bearer token",
    "authentication_error",
)
# While tripped, recheck the volume's credentials at most this often.
_AUTH_RECHECK_TTL_S = 300.0
# The recheck launches a container just to cat a file; cold starts included.
_RESEED_PROBE_TIMEOUT_S = 20


def is_auth_error_text(text: str) -> bool:
    """Does this CLI error output name a login/auth failure (see markers)?"""
    low = (text or "").lower()
    return any(m in low for m in _AUTH_ERROR_MARKERS)


def _auth_breaker_path():
    from pathlib import Path
    from config import memory_dir
    return Path(memory_dir()) / "container_auth_breaker.json"


# Re-notify the operator about the SAME dead-auth condition at most this
# often. Lives in a sidecar (not the breaker file) so it survives
# clear_auth_breaker: a false clear (e.g. credentials touched without a real
# re-seed) re-trips on the next call, and without this the flap would send a
# fresh Telegram per cycle (skeptic review 2026-08-13, finding 3). The
# sidecar stamps DELIVERED notifies only — composition with the notified
# flag below: the flag guarantees at-least-one delivery per trip (retry on
# the recheck cadence), the sidecar caps cross-trip flap spam.
_AUTH_NOTIFY_TTL_S = 6 * 3600.0


def _auth_notify_path():
    from pathlib import Path
    from config import memory_dir
    return Path(memory_dir()) / "container_auth_notified.json"


def _auth_notify_due() -> bool:
    """True when enough time has passed since the last operator notify.
    Read side of the throttle; never raises (fails open to notifying —
    a broken throttle must not silence a real outage)."""
    import json
    try:
        from llm_parse import safe_float
        path = _auth_notify_path()
        if not path.exists():
            return True
        data = json.loads(path.read_text(encoding="utf-8"))
        last = safe_float(data.get("at"), default=0.0)
        return time.time() - last >= _AUTH_NOTIFY_TTL_S
    except Exception:
        return True


def _stamp_auth_notify() -> None:
    import json
    from file_lock import atomic_write
    try:
        path = _auth_notify_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({"at": time.time()}) + "\n")
    except Exception:
        log.debug("auth notify stamp failed", exc_info=True)


def auth_breaker_state() -> Tuple[Optional[dict], str]:
    """(state, status) — status is 'clear' / 'tripped' / 'unreadable'.

    A PRESENT-but-unparseable marker is 'unreadable', distinct from clear:
    the resolve path still fails open on it (doctrine below: a dead session
    just re-trips, self-healing), but doctor/system_health must not report
    a corrupt marker as a clear lane (review 2026-08-13)."""
    import json
    path = _auth_breaker_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "clear"
    except Exception:
        return None, "unreadable"
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("tripped_at"):
            return data, "tripped"
    except Exception:
        pass
    return None, "unreadable"


def auth_breaker_snapshot() -> Optional[dict]:
    """The persisted breaker state, or None when clear/unreadable. Cheap
    (file read only) — doctor and system_health report from this without
    touching docker; surfaces that need the unreadable distinction use
    auth_breaker_state()."""
    return auth_breaker_state()[0]


# Process-local fallback when the marker can't be PERSISTED (read-only fs,
# permission break): this process still degrades instead of re-paying doomed
# container runs; other processes re-trip on their own first failure
# (review 2026-08-13: a trip-write failure must not restore the silent
# outage the breaker exists to prevent).
_MEM_AUTH_BREAKER: Optional[dict] = None


def _trip_auth_breaker(reason: str) -> Optional[dict]:
    """Serialized trip transition (review 2026-08-13: check→write→notify was
    unlocked, so two concurrent first failures both 'won' the trip). Returns
    the new state when THIS call tripped the breaker; None when a peer
    already had — the winner owns the notification."""
    import json
    from file_lock import locked_write, atomic_write
    path = _auth_breaker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_write(path):
        current, _status = auth_breaker_state()
        if current is not None:
            return None
        # 'unreadable' falls through: replace the corrupt marker with a
        # fresh valid trip (self-heal, worst case one duplicate notify).
        state = {
            "tripped_at": time.time(),
            "reason": reason,
            "last_recheck": time.time(),
            "notified": False,
        }
        atomic_write(path, json.dumps(state, sort_keys=True) + "\n")
        return state


def _touch_auth_breaker_recheck(tripped_at: float) -> None:
    """Record a failed re-seed probe against the SAME trip we probed for.
    A peer may have cleared the breaker (or a new trip landed) while our
    probe ran — rewriting our stale snapshot would resurrect a cleared
    breaker (review 2026-08-13). Never raises."""
    import json
    from file_lock import locked_write, atomic_write
    path = _auth_breaker_path()
    try:
        with locked_write(path):
            state, _status = auth_breaker_state()
            if state is None:
                return
            try:
                if float(state.get("tripped_at", 0.0)) != tripped_at:
                    return
            except (TypeError, ValueError):
                return
            state["last_recheck"] = time.time()
            atomic_write(path, json.dumps(state, sort_keys=True) + "\n")
    except Exception:
        log.debug("_touch_auth_breaker_recheck failed", exc_info=True)


def _mark_auth_breaker_notified() -> None:
    """Persist that the trip's actionable notification was DELIVERED.
    Until this lands, auth_breaker_blocks retries the emit on the recheck
    cadence — 'exactly one notification' previously meant 'at most one
    attempt' (review 2026-08-13). Never raises."""
    global _MEM_AUTH_BREAKER
    import json
    from file_lock import locked_write, atomic_write
    if _MEM_AUTH_BREAKER is not None:
        _MEM_AUTH_BREAKER["notified"] = True
    path = _auth_breaker_path()
    try:
        with locked_write(path):
            state, _status = auth_breaker_state()
            if state is None:
                return
            state["notified"] = True
            atomic_write(path, json.dumps(state, sort_keys=True) + "\n")
    except Exception:
        log.debug("_mark_auth_breaker_notified failed", exc_info=True)


def clear_auth_breaker(reason: str = "") -> None:
    """Remove the breaker state (operator action / census). Unconditional —
    the probe-driven self-clear uses _clear_auth_breaker_if. Never raises."""
    global _MEM_AUTH_BREAKER
    try:
        _MEM_AUTH_BREAKER = None
        path = _auth_breaker_path()
        if path.exists():
            path.unlink()
            log.info("container auth breaker CLEARED%s — executor calls "
                     "containerize again", f" ({reason})" if reason else "")
    except Exception:
        log.debug("clear_auth_breaker failed", exc_info=True)


def _clear_auth_breaker_if(tripped_at: float, reason: str) -> None:
    """Clear ONLY the trip our probe vouched for. A new trip landing while
    the probe ran must survive — deleting it would erase an unprobed
    failure (review 2026-08-13). Never raises."""
    from file_lock import locked_write
    path = _auth_breaker_path()
    try:
        with locked_write(path):
            state, _status = auth_breaker_state()
            if state is not None:
                try:
                    if float(state.get("tripped_at", 0.0)) != tripped_at:
                        return
                except (TypeError, ValueError):
                    pass  # corrupt tripped_at: the probed clear still stands
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        log.info("container auth breaker CLEARED (%s) — executor calls "
                 "containerize again", reason)
    except Exception:
        log.debug("_clear_auth_breaker_if failed", exc_info=True)


def _auth_breaker_consequence(mode: str) -> str:
    return ("executor steps now REFUSE (executor.container=require)"
            if mode == "require"
            else "executor steps now run on the HOST under the write-fence")


def _auth_breaker_payload(reason: str, consequence: str) -> dict:
    return {
        "reason": f"container executor auth expired: {reason[:160]}",
        "summary": (
            f"Container executor OAuth session is dead — {consequence}. "
            "Re-seed the maro-claude-auth volume: run "
            "`maro-bootstrap container-setup` step 2 (interactive "
            "claude /login; widen the terminal or de-wrap the URL)."),
        "status": "auth_expired",
    }


def note_container_failure(detail: str) -> None:
    """Called by the subprocess seam when a CONTAINERIZED call fails: trips
    the breaker iff the failure is an auth failure. Never raises — this rides
    an error path that must keep propagating the original error."""
    global _MEM_AUTH_BREAKER
    try:
        if not is_auth_error_text(detail):
            return
        # Auth error text can carry credential-shaped material — scrub before
        # it reaches the marker, the log, or a notification (review 2026-08-13).
        try:
            from context_budget import clip as _cb_clip
            from secret_scrub import scrub
            # 500 announced (round-15 review: the scrub wrapper hid this
            # durable breaker reason from the truncation census, and the
            # bare 300 cut the CLI's error text silently).
            reason = _cb_clip(str(scrub(str(detail))), 500)
        except Exception:
            reason = "container auth failure (detail unavailable)"
        state = None
        persist_failed = False
        try:
            state = _trip_auth_breaker(reason)
        except Exception:
            persist_failed = True
        if state is None and not persist_failed:
            return  # a peer's trip won — it owns the notification
        if persist_failed:
            if _MEM_AUTH_BREAKER is not None:
                return  # this process already degraded and notified
            _MEM_AUTH_BREAKER = {
                "tripped_at": time.time(),
                "reason": reason,
                "last_recheck": time.time(),
                "notified": False,
            }
            log.error("container auth breaker could not be persisted — "
                      "degrading process-locally only", exc_info=True)
        mode = container_mode()
        consequence = _auth_breaker_consequence(mode)
        log.warning(
            "container auth breaker TRIPPED: %s — %s until the auth volume "
            "is re-seeded (maro-bootstrap container-setup step 2)",
            reason[:200], consequence)
        # backend_actionable: auth failure with a fix only the operator can
        # apply (interactive /login). Two guards compose (both reviews,
        # 2026-08-13): the trip's winner emits, and a delivery failure
        # leaves notified=False so auth_breaker_blocks retries on the
        # recheck cadence (the notify must not be forfeited by a persist
        # failure or a transient Telegram outage) — while the cross-trip
        # sidecar throttle keeps a clear→re-trip FLAP from sending a fresh
        # Telegram per cycle: a recent DELIVERED notify marks this trip
        # notified without re-sending.
        if not _auth_notify_due():
            log.info("auth breaker re-tripped within the notify window — "
                     "operator already notified, not re-sending")
            _mark_auth_breaker_notified()
            return
        sent = False
        try:
            from notify import emit
            sent = emit("backend_actionable",
                        _auth_breaker_payload(reason, consequence))
        except Exception:
            log.debug("auth breaker notify failed", exc_info=True)
        if sent:
            _stamp_auth_notify()
            _mark_auth_breaker_notified()
    except Exception:
        log.debug("note_container_failure failed", exc_info=True)


def _reseed_probe(tripped_at: float) -> Tuple[bool, str]:
    """Has the operator re-seeded the auth volume since the trip? One docker
    stat+grep of the credentials file (ro mount, executor uid — see
    _user_args). SHAPE only: the credential bytes never transit to the host
    process or its logs (review 2026-08-13 — the previous probe cat'd the
    full file). True only for a live-shaped file (refreshToken present —
    the CLI wipes it after a failed refresh) NEWER than the trip; see the
    section comment for why both conditions."""
    cred_path = f"{AUTH_MOUNT}/.credentials.json"
    ok, out = _run([
        "docker", "run", "--rm", *_user_args(),
        "-e", f"HOME={CONTAINER_HOME}",
        "--mount", (f"type=volume,source={AUTH_VOLUME},"
                    f"target={AUTH_MOUNT},readonly"),
        "--entrypoint", "sh", container_image(),
        "-c", (f"stat -c %Y {cred_path} && "
               f"{{ grep -c refreshToken {cred_path} || true; }}"),
    ], _RESEED_PROBE_TIMEOUT_S)
    if not ok:
        return False, f"credentials probe failed: {out[:120]}"
    try:
        first, _, rest = out.partition("\n")
        mtime = float(first.strip())
        has_refresh = int(rest.strip().splitlines()[0]) > 0
    except Exception as exc:
        return False, f"credentials probe unparseable: {exc}"
    if not has_refresh:
        return False, "credentials still wiped (no refresh token) — not re-seeded"
    if mtime <= tripped_at:
        # Whole-second stat vs fractional trip time: a re-seed landing in the
        # SAME wall-clock second as the trip is missed. Accepted (review
        # 2026-08-13, rejected finding): interactive re-seeds take minutes,
        # and loosening the comparison errs toward false self-clear →
        # re-trip → notification loop, the worse direction.
        return False, "credentials unchanged since trip — not re-seeded"
    return True, "auth volume re-seeded (fresh credentials with refresh token)"


def auth_breaker_blocks() -> Optional[str]:
    """The reason the container lane is auth-blocked, or None when usable.

    While tripped, rechecks the volume via _reseed_probe at most every
    _AUTH_RECHECK_TTL_S and self-clears on a real re-seed — the operator
    never has to touch config to restore the lane. The probe runs OUTSIDE
    the state lock; clear/recheck commits are identity-checked against the
    probed trip so a stale probe can't resurrect a peer's clear (review
    2026-08-13). An undelivered trip notification is retried on the same
    cadence. Never raises; a broken/unreadable breaker store must not take
    down the resolve path (fails open to the container, where a bad session
    just re-trips)."""
    global _MEM_AUTH_BREAKER
    try:
        state, _status = auth_breaker_state()
        if state is None:
            state = _MEM_AUTH_BREAKER  # persisted marker unavailable
        if state is None:
            return None
        reason = str(state.get("reason", "auth failure"))
        try:
            last = float(state.get("last_recheck", 0.0))
            tripped_at = float(state.get("tripped_at", 0.0))
        except (TypeError, ValueError):
            last, tripped_at = 0.0, 0.0
        if time.time() - last >= _AUTH_RECHECK_TTL_S:
            ok, detail = _reseed_probe(tripped_at)
            if ok:
                _clear_auth_breaker_if(tripped_at, detail)
                _MEM_AUTH_BREAKER = None
                return None
            _touch_auth_breaker_recheck(tripped_at)
            if _MEM_AUTH_BREAKER is not None:
                _MEM_AUTH_BREAKER["last_recheck"] = time.time()
            log.debug("auth breaker recheck: still tripped (%s)", detail)
            if not state.get("notified") and _auth_notify_due():
                # Delivery failed at trip time (Telegram down, crash before
                # send) — retry until one emit succeeds, throttled to the
                # recheck cadence (and the cross-trip sidecar window).
                sent = False
                try:
                    from notify import emit
                    sent = emit(
                        "backend_actionable",
                        _auth_breaker_payload(
                            reason, _auth_breaker_consequence(container_mode())))
                except Exception:
                    log.debug("auth breaker notify retry failed", exc_info=True)
                if sent:
                    _stamp_auth_notify()
                    _mark_auth_breaker_notified()
        return reason
    except Exception:
        log.debug("auth_breaker_blocks failed (failing open)", exc_info=True)
        return None


def _current_loop_id() -> str:
    """Best-effort owning-run id for the container name (design: maro-exec-
    <loop_id>-…). Falls back to the PID when no run dir is active."""
    try:
        from runs import current_handle_id
        hid = current_handle_id()
        if hid:
            return str(hid)
    except Exception:
        pass
    return f"pid{os.getpid()}"


def container_name(loop_id: str, seq: int) -> str:
    """Docker-legal container name, unique across processes and calls:
    maro-exec-<loop_id>-<pid>-<seq>. The PID prevents a resumed run in a fresh
    process from colliding on a not-yet-reaped stale same-name container
    (adversarial-review 2026-07-12); the stranded sweep keys on the owner-PID
    label, not this name, so extra name components are free. Docker names must
    match [a-zA-Z0-9][a-zA-Z0-9_.-]* — sanitize the loop id."""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", str(loop_id))
    return f"{NAME_PREFIX}{safe}-{os.getpid()}-{seq}"


def resolve_container_run(no_tools: bool, executor: bool) -> Optional[str]:
    """Decide whether this call runs in a container; return its name or None
    (host path). Raises ContainerUnavailable for require-mode + no docker.

    `executor` must be True: ONLY worker executor steps (the agentic
    `claude -p --dangerously-skip-permissions` goal work) are containerized —
    NOT Maro's own reasoning calls (verify, quality-gate, refinement, planning,
    doctor probe), which also carry tools but are not the executor lane
    (design §1; adversarial-review 2026-07-12 caught `not no_tools` over-
    capturing them). Default-False means anything unflagged stays on the host —
    safe by construction (worst case a worker step isn't isolated, never a
    non-worker call wrongly containerized).

    Order (cheap checks first — mode 'off', the default, returns before any
    docker probe): off / not-executor / utility(no_tools) → host; docker up →
    container; docker down → refuse (require) or degrade-with-warning (on).
    """
    global _last_degrade_warn
    if not executor or no_tools:
        return None
    mode = container_mode()
    if mode == "off":
        return None
    if container_suppressed():
        # A git-repo run whose scratch clone could not be provisioned: never
        # containerize it, because its cwd is the LIVE repo and mounting that rw
        # is the one thing the clone exists to prevent (fail closed to host).
        if mode == "require":
            # require means isolation-or-nothing: refusing beats silently
            # executing on the host (review 2026-08-13 — the suppression
            # early-return used to bypass the require contract entirely).
            raise ContainerUnavailable(
                "executor.container=require but containerization is "
                "suppressed for this run (scratch clone unavailable)")
        return None
    ok, reason = docker_probe()  # fresh every call — honest degrade/refuse
    if ok:
        # Docker is up but the auth volume's OAuth session may be dead — the
        # 08-12 outage shape the docker probe can't see. Same refuse/degrade
        # contract as docker-down; self-clears after a re-seed (see breaker).
        auth_block = auth_breaker_blocks()
        if auth_block is None:
            return container_name(_current_loop_id(), next(_seq_counter))
        reason = f"container auth breaker tripped ({auth_block[:120]})"
    if mode == "require":
        raise ContainerUnavailable(
            f"executor.container=require but the container lane is unavailable: {reason}"
        )
    # mode == "on": degrade to host/fence-only, but say so (SF-6: the
    # difference between sandboxed and not must be visible) — throttled so a
    # persistently-down daemon doesn't warn once per step.
    now = time.monotonic()
    if now - _last_degrade_warn >= _WARN_THROTTLE_S:
        log.warning(
            "executor.container=on but the container lane is unavailable (%s) — "
            "worker steps run on the host under the write-fence, NOT containerized",
            reason
        )
        _last_degrade_warn = now
    return None


def container_configured() -> bool:
    """Is the executor configured to containerize (mode on/require)?

    Run-level gate for the self-dev scratch-clone provisioning (design §4).
    Deliberately CONFIG-ONLY — no docker probe — so the provisioning decision
    can't race a daemon coming up/down between setup and an executor call
    (adversarial-review 2026-07-13, finding A): the clone is prepared on config
    intent, and the per-call resolve_container_run remains the authority on
    whether docker actually runs the call (degrading to host in the clone if the
    daemon is down — harmless). off (default) → False, zero boot tax.
    """
    return container_mode() in ("on", "require")


def _forbidden_mount_roots() -> list:
    """Absolute host roots that must NEVER be bind-mounted into an executor
    container, realpath-resolved (design §4 "deliberately absent"): the
    workspace root (mounts the orchestration — memory, config, secrets) and host
    `/tmp`/tempdir (the container gets its own ephemeral /tmp). A candidate that
    EQUALS or is an ANCESTOR of any of these is rejected — mounting `/`, `/home`,
    or the workspace itself would expose the orchestration. (Descendants of the
    workspace, e.g. a run's own scratch clone, are fine and not matched here.)"""
    roots = []
    try:
        from config import workspace_root
        roots.append(os.path.realpath(str(workspace_root())))
    except Exception:
        pass
    for t in ("/tmp", tempfile.gettempdir()):
        try:
            roots.append(os.path.realpath(t))
        except Exception:
            pass
    return roots


def _is_ancestor_or_equal(candidate: str, other: str) -> bool:
    """True if `candidate` == `other` or is an ancestor directory of it."""
    return other == candidate or other.startswith(candidate.rstrip(os.sep) + os.sep)


def _container_write_scope_roots() -> list:
    """Realpath'd host roots UNDER which a goal-declared rw mount is allowed —
    the containment whitelist (C4-BOX burn-in finding, 2026-07-15).

    The forbidden list (`_forbidden_mount_roots`) is a blacklist: it keeps the
    orchestration and `/tmp` out, but it cannot enumerate every sensitive host
    path. A hostile *goal* that names an absolute path outside the workspace
    (`~/.ssh/authorized_keys`, a host secret file) would otherwise ride
    `goal_declared_roots` straight into an rw bind — the container reads/writes
    the host secret, defeating containment. So a goal-declared rw root is
    mounted only when it falls WITHIN one of these scopes:

      - the workspace subtree (its descendants — the run's project/output dirs,
        self-dev scratch clones; the workspace root ITSELF stays forbidden), and
      - each explicit `validate.write_fence_allow` root — the operator's
        deliberate escape hatch for a legitimate out-of-workspace target.

    Anything else a goal declares is dropped LOUDLY in `build_mount_map` rather
    than mounted. `cwd` (the run's own working dir) and configured `ro` reference
    mounts (`executor.container_extra_mounts`) are operator-trusted and exempt.
    Fails CLOSED: an unreadable workspace/config yields a narrower scope (fewer
    mounts), never a broader one."""
    roots: list = []
    try:
        from config import workspace_root
        roots.append(os.path.realpath(str(workspace_root())))
    except Exception:
        pass
    try:
        for r in (get("validate.write_fence_allow", []) or []):
            if r:
                try:
                    roots.append(os.path.realpath(os.path.expanduser(str(r))))
                except Exception:
                    pass
    except Exception:
        pass
    return roots


def build_mount_map(
    cwd: Optional[str],
    *,
    rw_roots: Optional[list] = None,
    ro_mounts: Optional[list] = None,
    forbidden_roots: Optional[list] = None,
    write_scope_roots: Optional[list] = None,
) -> list:
    """Translate a run's write-fence into a docker mount list [(host_path, mode)].

    The mount set mirrors what the fence lets the run WRITE, minus what the
    design deliberately keeps out of the container (design §4):

      - `cwd` (the fence/working dir, or the self-dev scratch clone) → **rw**.
      - `rw_roots` (goal-declared roots + `validate.write_fence_allow`) → **rw**,
        but ONLY those within the write scope (see below).
      - `ro_mounts` (`executor.container_extra_mounts`) → **ro**.
      - The workspace root, host `/tmp`, and any caller-supplied `forbidden_roots`
        (e.g. the live repo of a self-dev run) are HARD-EXCLUDED — the exclusion
        is enforced here, not merely documented (adversarial-review 2026-07-13,
        finding B): `run_agent_loop` could otherwise pass `/tmp` (via
        `validate.write_fence_allow`) or a workspace/live-repo root straight
        through to a rw bind.

    **Write scope (containment whitelist, C4-BOX burn-in 2026-07-15).** The
    forbidden list is a blacklist and cannot name every sensitive host path, so
    a goal-declared rw root is additionally required to fall WITHIN the write
    scope — the workspace subtree plus each explicit `validate.write_fence_allow`
    root (`write_scope_roots`, read from config when not injected). A hostile
    *goal* that names a host secret outside the workspace (`~/.ssh/...`, a
    credentials file) is therefore DROPPED loudly rather than mounted — closing
    the containment gap the burn-in's acceptance probe surfaced (a goal that
    declares its own target defeated both fence detection and container
    containment). `cwd` and `ro_mounts` are operator-trusted and scope-exempt;
    only goal-declared rw roots are scoped.

    Every source is realpath-resolved BEFORE the exclusion + containment checks,
    so a symlink whose target is a forbidden/sensitive dir can't smuggle a mount
    past the filter (docker resolves the symlink host-side anyway; the emitted
    path is the resolved target, matching what actually gets bound and the `-w`
    the seam derives the same way).

    Pure (no filesystem mutation): a rw root that does NOT exist on the host is
    skipped, not created — a bind of a missing path would have docker create it
    root-owned. A FILE-shaped rw root (the fence authorizes exact paths, so
    goals legally name files) is translated to its immediate parent directory —
    one level, only when that parent already exists — because single-file binds
    detach on atomic-rename writes; an untranslatable root is dropped with a
    warning, never silently (C4-BOX burn-in finding, 2026-07-14). A path
    containing a comma or newline is skipped (docker's
    `--mount` CSV syntax can't encode it safely; better to drop than mis-mount).
    Dedup is containment-aware and order-independent (sources sorted
    shortest-first so a parent is placed before its children): a path already
    covered by an equal-or-broader mount of at least the requested permission is
    dropped (a rw parent covers a ro child; a ro parent does NOT cover a rw
    child, which stays as a nested rw mount).
    """
    forbidden = list(_forbidden_mount_roots())
    for f in (forbidden_roots or []):
        if f:
            try:
                forbidden.append(os.path.realpath(str(f)))
            except Exception:
                pass

    # Containment whitelist: a goal-declared rw root must live within one of
    # these scopes (workspace subtree + explicit write_fence_allow) or it is
    # dropped — the blacklist above can't enumerate every host secret path.
    scope = list(write_scope_roots) if write_scope_roots is not None \
        else _container_write_scope_roots()

    def _in_write_scope(rp: str) -> bool:
        return any(_is_ancestor_or_equal(s, rp) for s in scope)

    def _clean(p, *, check_forbidden: bool) -> Optional[str]:
        rp = os.path.realpath(str(p))
        if "," in rp or "\n" in rp:
            log.warning("container mount: path %r contains a comma/newline docker "
                        "--mount can't encode — skipped", rp)
            return None
        # The cwd is the run's own working dir (kept off the live repo by the
        # scratch-clone / suppression logic upstream) — always mountable. The
        # forbidden filter guards the EXTRA roots, where the reviewer's threat
        # lives (write_fence_allow=/tmp, a goal naming the workspace/live repo).
        if check_forbidden:
            for bad in forbidden:
                # Reject if the candidate IS or CONTAINS a forbidden root
                # (mounting it would expose the orchestration/tmp). Descendants
                # of a forbidden root are fine and not matched.
                if _is_ancestor_or_equal(rp, bad):
                    log.warning("container mount: %s is/contains a forbidden root %s — "
                                "refused (design §4: orchestration/tmp never mounted)", rp, bad)
                    return None
        return rp

    mounts: list = []
    seen: list = []  # (realpath, rank) — rw=2, ro=1

    def _covered(rp: str, rank: int) -> bool:
        for q, qr in seen:
            if qr >= rank and _is_ancestor_or_equal(q, rp):
                return True
        return False

    def _add(raw, mode: str, rank: int, *, require_dir: bool, check_forbidden: bool = True) -> None:
        if not raw:
            return
        rp = _clean(raw, check_forbidden=check_forbidden)
        if rp is None:
            return
        if _covered(rp, rank):
            return
        if require_dir and not os.path.isdir(rp):
            log.debug("container mount: %s root %s is not an existing directory "
                      "on the host — skipped", mode, rp)
            return
        mounts.append((rp, mode))
        seen.append((rp, rank))

    def _mountable_rw_dir(raw) -> Optional[str]:
        # Fence rw roots may be file-shaped: _in_fence authorizes an exact
        # path (`p == r`), so a goal naming files works fence-only but a
        # docker bind needs a directory (single-file binds silently detach on
        # atomic-rename writes — exactly what code-editing workers do).
        # Translate a file root — existing, or declared-but-not-yet-created —
        # to its immediate parent, ONE level only and only when that parent
        # already exists as a dir (binding a missing source would have docker
        # create it root-owned). Never walk further up: a missing parent means
        # the declared path has no mountable home and is dropped LOUDLY —
        # a silent drop here is how burn-in goal 585f95f2 saw an "absent"
        # directory that existed on the host (C4-BOX finding, 2026-07-14).
        rp = os.path.realpath(str(raw))
        if os.path.isdir(rp):
            return rp
        parent = os.path.dirname(rp)
        if os.path.isdir(parent):
            return parent
        log.warning("container mount: rw root %s has no existing directory to "
                    "mount (parent %s absent) — dropped; the container will "
                    "not see this goal-declared path", rp, parent)
        return None

    # cwd first (rw); its realpath is what `-w` must also use. Exempt from the
    # forbidden filter (it is the run's own working dir).
    _add(cwd, "rw", 2, require_dir=False, check_forbidden=False)
    # Translate file-shaped roots to their parent dir first, then sort
    # shortest-first so a parent is seen before its children — makes
    # containment dedup independent of caller input order.
    _rw_dirs = []
    for root in (rw_roots or []):
        if not root:
            continue
        translated = _mountable_rw_dir(root)
        if not translated:
            continue
        if not _in_write_scope(translated):
            # Say WHICH of the two outcomes this is. A path already in
            # ro_mounts is readable — reporting it the same way as an
            # unmountable one reads as "not available", which is a
            # different fact and the one a worker acts on. Found
            # 2026-08-16: a reference corpus was ro-mounted and working
            # while this line said it had been refused, and the operator
            # believed the line.
            _ro_real = {os.path.realpath(str(r)) for r in (ro_mounts or []) if r}
            if translated in _ro_real:
                log.info("container mount: %s is not writable from the "
                         "container (outside the write scope) but IS mounted "
                         "READ-ONLY via executor.container_extra_mounts — "
                         "reads work, writes do not", translated)
            else:
                log.warning(
                    "container mount: rw root %s is outside the container "
                    "write scope (workspace subtree + validate.write_fence_allow) "
                    "— refused, and it is not in executor.container_extra_mounts "
                    "either, so the container cannot READ it at all. Add it to "
                    "container_extra_mounts for read access, or to "
                    "validate.write_fence_allow for write "
                    "(C4-BOX containment whitelist, 2026-07-15)", translated)
            continue
        _rw_dirs.append(translated)
    for root in sorted(set(_rw_dirs), key=len):
        _add(root, "rw", 2, require_dir=True)
    for ref in sorted((r for r in (ro_mounts or []) if r), key=lambda p: len(os.path.realpath(str(p)))):
        _add(ref, "ro", 1, require_dir=True)

    return mounts


def attachment_ro_mounts() -> list:
    """Read-only mounts for the areas holding operator/dispatcher artifacts.

    A named function rather than three lines inline in llm.py, because the
    inline version was untestable: a mutation deleting it survived the whole
    suite, since the only test drove build_mount_map with the area passed in
    by hand. The wiring that SUPPLIES the area is the part that broke live —
    attachments landed correctly and the worker still could not see them.

    Read-only is the whole grant. These directories hold exactly what a run
    is meant to read and never meant to write, and mounting them is far
    narrower than relaxing the workspace-root exclusion that hid them.
    """
    out: list = []
    try:
        from config import output_dir
        for area in ("operator-attachments", "dispatch-artifacts"):
            p = output_dir() / area
            if p.is_dir():
                out.append(str(p))
    except Exception as exc:
        log.debug("attachment mounts unavailable (non-fatal): %s", exc)
    return out


def run_scratch_dir() -> Optional[str]:
    """Per-run host scratch dir to bind at the container's `/tmp` so cross-step
    scratch survives within a run (C4-BOX burn-in follow-up, 2026-07-15).

    Each executor step is a fresh `--rm` container, so an unbound `/tmp` is
    ephemeral *per step*: a worker that writes `/tmp/notes` in step 1 finds it
    gone in step 2. We bind `<run_dir>/scratch` at `/tmp` instead — a single
    dir stable across the run's (sequential) steps. It lives on the workspace
    subtree (inside the containment write-scope), is owned by our uid (we create
    it, the container runs `--user` as the same uid), and is retained with the
    run per the data-retention decree (never auto-deleted — it becomes part of
    the run's artifacts). Per-run, NOT per-step: a step-named dir would relocate
    /tmp to the host but still lose it across steps, which is the whole bug.

    Returns None — leaving /tmp ephemeral, unchanged — when there is no active
    run dir (utility calls, tests), scratch is disabled
    (`executor.container_run_scratch: false`), or provisioning fails. Reversible.
    """
    try:
        if not get("executor.container_run_scratch", True):
            return None
    except Exception:
        pass
    try:
        from runs import current_run_dir
        rd = current_run_dir()
    except Exception:
        return None
    if rd is None:
        return None
    try:
        scratch = os.path.join(os.path.realpath(str(rd)), "scratch")
        os.makedirs(scratch, exist_ok=True)
        return scratch
    except OSError as exc:
        log.debug("run_scratch_dir: could not provision scratch (non-fatal): %s", exc)
        return None


def build_run_command(
    inner_cmd: list,
    *,
    name: str,
    workdir: Optional[str] = None,
    mounts: Optional[list] = None,
    worker_env: Optional[dict] = None,
    passthrough_env: Optional[list] = None,
    owner_pid: Optional[int] = None,
    image: Optional[str] = None,
    network: Optional[str] = None,
    scratch_dir: Optional[str] = None,
) -> list:
    """Wrap an inner `claude -p ...` command vector in `docker run` (design §2).

    `mounts` is a list of (host_path, mode) with mode in {"rw","ro"}, each
    bind-mounted at the SAME absolute path inside the container so `-w` and the
    worker's relative writes resolve to the host dir. The auth volume + HOME are
    always mounted so the baked CLI is logged in. Owner PID + process-birth
    labels let the stranded sweep distinguish a live owner from PID reuse.

    `scratch_dir` (host path, from `run_scratch_dir()`) is bind-mounted at the
    container's `/tmp` — the one mount that is NOT identity-mapped — so /tmp
    scratch persists across a run's steps instead of vanishing with each --rm
    container. None (the default) leaves /tmp ephemeral, as before.

    The inner command's argv[0] is reduced to its basename: the host-resolved
    claude path (e.g. /opt/homebrew/bin/claude) does not exist inside the image;
    the BAKED CLI is on the container PATH as `claude` (adversarial-review
    2026-07-12 — the host path would make every containerized call fail).
    """
    image = image or container_image()
    network = network or str(get("executor.container_network", DEFAULT_NETWORK) or DEFAULT_NETWORK)
    owner_pid = os.getpid() if owner_pid is None else owner_pid

    # -i: pipe the prompt (fed on stdin by _run_subprocess_safe) into the
    # container. --init: a real PID 1 that reaps zombies + forwards signals.
    # --rm: no leftover container on normal exit (the sweep handles crashes).
    # --user: mounted files stay operator-owned; auth-volume files written by
    # the login step (which runs as the SAME uid) stay readable/refreshable
    # here (design §4 uid/gid; see _user_args).
    cmd = ["docker", "run", "--rm", "-i", "--init", "--name", name, *_user_args()]
    cmd += ["--label", f"maro.owner_pid={owner_pid}"]
    owner_start = process_start_token(owner_pid)
    if owner_start:
        cmd += ["--label", f"maro.owner_start={owner_start}"]
    # --mount (not -v host:host:mode): colon-safe for host paths that legally
    # contain ':' (adversarial-review 2026-07-12).
    for host_path, mode in (mounts or []):
        spec = f"type=bind,source={host_path},target={host_path}"
        if mode == "ro":
            spec += ",readonly"
        cmd += ["--mount", spec]
    # Per-run scratch bound at the container's /tmp so scratch written to /tmp
    # survives across a run's (sequential) steps — each --rm step otherwise gets
    # a fresh ephemeral /tmp (C4-BOX burn-in follow-up 2026-07-15). Fixed
    # container target /tmp (NOT identity-mapped like the fence binds above) so
    # the worker's literal /tmp writes land in the on-host run scratch dir.
    if scratch_dir:
        cmd += ["--mount", f"type=bind,source={scratch_dir},target=/tmp"]
    # Auth volume + fixed HOME so the baked CLI finds its OAuth session.
    cmd += ["--mount", f"type=volume,source={AUTH_VOLUME},target={AUTH_MOUNT}",
            "-e", f"HOME={CONTAINER_HOME}"]
    cmd += ["--network", network]
    for key, val in (worker_env or {}).items():
        cmd += ["-e", f"{key}={val}"]
    # Bare -e NAME: docker copies the value from the CLIENT's process env,
    # so secrets never appear in the docker argv (host process listings).
    # The caller must put the value into the docker client's Popen env.
    for key in (passthrough_env or []):
        cmd += ["-e", str(key)]
    if workdir:
        cmd += ["-w", workdir]
    cmd.append(image)
    inner = list(inner_cmd)
    if inner:
        inner[0] = os.path.basename(str(inner[0])) or inner[0]
    cmd.extend(inner)
    return cmd


def kill_container(name: str) -> None:
    """`docker kill <name>` — best-effort. os.killpg kills the docker *client*,
    not the container (design §2 kill path); this kills the container itself.
    Swallows errors: the container may already be gone (--rm) or docker down."""
    if not name:
        return
    try:
        subprocess.run(
            ["docker", "kill", name],
            capture_output=True, text=True, timeout=_KILL_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("docker kill %s failed (non-fatal): %s", name, exc)


def sweep_stranded_containers(
    pid_alive: Optional[Callable[[int], bool]] = None,
    process_token: Optional[Callable[[int], Optional[str]]] = None,
) -> list:
    """Reap executor containers whose owning run process is dead.

    A container survives its docker client being SIGKILL'd or the box crashing
    (--rm only fires on clean container exit). Ownership is decided by the
    `maro.owner_pid` LABEL plus `maro.owner_start` when available, never the
    name: we filter `docker ps` by the PID label
    (only containers WE launched carry it) AND require the `maro-exec-` name
    prefix, then kill only those whose owner PID is dead. A container without
    our label — or one merely matching the name substring — is NOT ours and is
    left alone (adversarial-review 2026-07-12: the old name-substring filter +
    kill-if-unlabeled could destroy an unrelated `…maro-exec…` container).
    Returns the names killed; docker absent → empty list. Known limitation:
    a container leaked while its owning process stays alive (a wedged
    `docker kill` in a long-lived process) is NOT reaped here — process-PID
    liveness can't distinguish it from the live owner's current container;
    tracked for a run-scoped-liveness follow-on.
    """
    alive = pid_alive or process_pid_alive
    token_reader = process_token or process_start_token
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", "label=maro.owner_pid",
             "--format", '{{.Names}}\t{{.Label "maro.owner_pid"}}\t'
                         '{{.Label "maro.owner_start"}}'],
            capture_output=True, text=True, timeout=_SWEEP_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("stranded-container sweep: docker ps failed (non-fatal): %s", exc)
        return []
    if proc.returncode != 0:
        return []

    killed = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        cname = parts[0].strip()
        owner_raw = parts[1].strip() if len(parts) > 1 else ""
        owner_start = parts[2].strip() if len(parts) > 2 else ""
        # Defense in depth: even among our-labelled containers, only touch ones
        # with our name prefix, and only when the owner PID parses and is dead.
        # An unparseable owner label => skip (never kill on ambiguity).
        if not cname.startswith(NAME_PREFIX):
            continue
        try:
            owner_pid = int(owner_raw)
        except ValueError:
            continue
        if not owner_is_current(
                owner_pid, owner_start, alive=alive, token_reader=token_reader):
            kill_container(cname)
            killed.append(cname)
    return killed
