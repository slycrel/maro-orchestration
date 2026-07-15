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


class ContainerUnavailable(RuntimeError):
    """Raised when `executor.container: require` is set but docker can't run
    the call — the `require` contract refuses rather than silently degrading."""


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
    global _last_degrade_warn
    _last_degrade_warn = 0.0
    _container_suppressed.set(False)


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
        return None
    ok, reason = docker_probe()  # fresh every call — honest degrade/refuse
    if ok:
        return container_name(_current_loop_id(), next(_seq_counter))
    if mode == "require":
        raise ContainerUnavailable(
            f"executor.container=require but docker is unavailable: {reason}"
        )
    # mode == "on": degrade to host/fence-only, but say so (SF-6: the
    # difference between sandboxed and not must be visible) — throttled so a
    # persistently-down daemon doesn't warn once per step.
    now = time.monotonic()
    if now - _last_degrade_warn >= _WARN_THROTTLE_S:
        log.warning(
            "executor.container=on but docker is unavailable (%s) — worker steps "
            "run on the host under the write-fence, NOT containerized", reason
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
            log.warning("container mount: rw root %s is outside the container "
                        "write scope (workspace subtree + validate.write_fence_allow) "
                        "— refused; add it to validate.write_fence_allow to mount it "
                        "(C4-BOX containment whitelist, 2026-07-15)", translated)
            continue
        _rw_dirs.append(translated)
    for root in sorted(set(_rw_dirs), key=len):
        _add(root, "rw", 2, require_dir=True)
    for ref in sorted((r for r in (ro_mounts or []) if r), key=lambda p: len(os.path.realpath(str(p)))):
        _add(ref, "ro", 1, require_dir=True)

    return mounts


def build_run_command(
    inner_cmd: list,
    *,
    name: str,
    workdir: Optional[str] = None,
    mounts: Optional[list] = None,
    worker_env: Optional[dict] = None,
    owner_pid: Optional[int] = None,
    image: Optional[str] = None,
    network: Optional[str] = None,
) -> list:
    """Wrap an inner `claude -p ...` command vector in `docker run` (design §2).

    `mounts` is a list of (host_path, mode) with mode in {"rw","ro"}, each
    bind-mounted at the SAME absolute path inside the container so `-w` and the
    worker's relative writes resolve to the host dir. The auth volume + HOME are
    always mounted so the baked CLI is logged in. Owner PID + process-birth
    labels let the stranded sweep distinguish a live owner from PID reuse.

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
    # Auth volume + fixed HOME so the baked CLI finds its OAuth session.
    cmd += ["--mount", f"type=volume,source={AUTH_VOLUME},target={AUTH_MOUNT}",
            "-e", f"HOME={CONTAINER_HOME}"]
    cmd += ["--network", network]
    for key, val in (worker_env or {}).items():
        cmd += ["-e", f"{key}={val}"]
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
