"""Local, zero-cost model runtime for step/quality validation.

Poe's most frequent LLM call is *validation* — "did this step result actually
satisfy the goal?" Those calls are high-volume and mostly easy, so paying a
frontier API for each one is the single biggest avoidable token sink. This
module lets a local model (running on the same box) act as the first-pass
validator for free, escalating to a paid model only when the local judge is
*uncertain*.

Design (intentionally small):

  * One HTTP adapter, `LocalValidatorAdapter`, speaks the OpenAI-compatible
    `/v1/chat/completions` schema. Both supported runtimes expose it:
      - **mlx**    — `mlx_lm.server` on Apple Silicon (default here)
      - **ollama** — `ollama serve` `/v1` endpoint (works on the Linux box)
    So one code path serves both; `validate.runtime` only picks the endpoint.

  * A **0..n list** of local models (`validate.local_models`, priority order)
    is wrapped in the existing `FailoverAdapter`. Empty list → this module is
    inert and validation behaves exactly as before (fully backward-compatible).

  * **Detect-and-use-if-present.** If the endpoint isn't reachable or no
    configured model is loaded, `build_local_validator_adapter()` returns
    None and callers fall back to the paid path. Installing the runtime is
    optional (see `scripts/local-validator.sh`); nothing breaks without it.

Pure stdlib (`urllib`) on purpose: the framework interpreter needs no MLX/torch
deps — the model runs in a separate process. See VISION.md §9 (cost philosophy)
and the validation ladder in quality_gate.py / verification_agent.py.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from llm import LLMAdapter, LLMMessage, LLMResponse

log = logging.getLogger("maro.local_models")

# Default endpoints for each runtime (OpenAI-compatible base URLs).
_DEFAULT_ENDPOINTS = {
    "mlx": "http://127.0.0.1:8088/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
_REACH_TIMEOUT = 2.0   # seconds — endpoint liveness probe must be cheap
_GEN_TIMEOUT = 60.0    # seconds — a single validation generation


# ---------------------------------------------------------------------------
# Config accessors (all under the `validate.*` namespace)
# ---------------------------------------------------------------------------

def _cfg(key: str, default):
    """Read `validate.<key>`; tolerate config.py being unavailable in tests."""
    try:
        from config import get
        return get(f"validate.{key}", default)
    except Exception:
        return default


def resolve_runtime() -> str:
    """Resolve the local runtime: explicit config, else auto by platform.

    auto → 'mlx' on Apple Silicon (Darwin/arm64), 'ollama' everywhere else.
    """
    runtime = str(_cfg("runtime", "auto")).strip().lower()
    if runtime in ("mlx", "ollama"):
        return runtime
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "ollama"


def resolve_endpoint(runtime: Optional[str] = None) -> str:
    """OpenAI-compatible base URL for the active runtime.

    Priority: `validate.endpoint` override → `LOCAL_VALIDATOR_ENDPOINT` env →
    runtime default.
    """
    override = str(_cfg("endpoint", "") or "").strip()
    if override:
        return override.rstrip("/")
    env = os.environ.get("LOCAL_VALIDATOR_ENDPOINT", "").strip()
    if env:
        return env.rstrip("/")
    runtime = runtime or resolve_runtime()
    return _DEFAULT_ENDPOINTS.get(runtime, _DEFAULT_ENDPOINTS["ollama"]).rstrip("/")


def configured_models() -> List[str]:
    """The 0..n local validator models, in priority (failover) order."""
    raw = _cfg("local_models", []) or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(m).strip() for m in raw if str(m).strip()]


def min_certainty() -> float:
    """Confidence below which a local verdict is UNDECIDED → escalate to paid."""
    try:
        return max(0.0, min(1.0, float(_cfg("min_certainty", 0.6))))
    except (TypeError, ValueError):
        return 0.6


def input_char_budget() -> int:
    """How much of a step result the *local* (free) validator sees. Larger than
    the paid default (1200) since local validation costs nothing — judging a
    fuller view beats judging the first 1200 chars. Bounded; for very large
    artifacts an agentic verifier that reads selectively is the better tool
    (see the deep-eval task in BACKLOG.md)."""
    try:
        return max(1200, int(_cfg("max_input_chars", 6000)))
    except (TypeError, ValueError):
        return 6000


def escalation_target() -> str:
    """Where an UNDECIDED local verdict escalates: 'cheap' (one paid gate) or
    'council' (the 3-persona trio in quality_gate.run_llm_council)."""
    target = str(_cfg("escalation", "cheap")).strip().lower()
    return target if target in ("cheap", "council") else "cheap"


def idle_shutdown_secs() -> int:
    """Seconds of validation inactivity after which an orchestration-managed local
    server is reaped. 0 disables idle reaping (kept until process exit). The
    lifecycle is owned by the orchestration, not an OS service — spun up on demand,
    down when idle."""
    try:
        return max(0, int(_cfg("idle_shutdown_secs", 300)))
    except (TypeError, ValueError):
        return 300


def _cpu_count() -> int:
    return os.cpu_count() or 1


def _default_cpu_affinity() -> str:
    """Portable default: reserve the lower half of logical CPUs for the system and
    pin the validator to the upper half (a `taskset -c` range). Derived from the
    actual CPU count so the harness works unchanged on any Linux box — a hardcoded
    list would make `taskset` fail on a machine that lacks those cores, silently
    preventing the validator from starting. Boxes with ≤2 CPUs can't spare a core
    to isolate, so they get no pin (nice alone keeps inference polite). Examples:
    4 CPUs → "2-3", 8 → "4-7", 64 → "32-63", ≤2 → ""."""
    n = _cpu_count()
    if n <= 2:
        return ""
    lo = max(2, n // 2)
    return f"{lo}-{n - 1}"


def _parse_cpu_list(spec: str) -> set:
    """Parse a `taskset -c` spec ("0,2,4" or "2-3" or a mix) into a set of ints."""
    out: set = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                continue
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return out


def cpu_affinity() -> str:
    """Logical CPUs the *managed* local runtime may use (normalized `taskset -c`
    list). Caps the validator so inference can't starve the box on a small machine
    — local inference pins >1 core for seconds per call. Config `validate.cpu_affinity`
    overrides the portable derived default (`_default_cpu_affinity`). Either way the
    cores are clamped to those that actually exist, so a stale or borrowed config
    can't make `taskset` fail (which would silently keep the validator from
    starting). Empty → no pinning (nice only). Linux only (taskset)."""
    raw = _cfg("cpu_affinity", None)
    spec = _default_cpu_affinity() if raw is None else str(raw or "").strip()
    if not spec:
        return ""
    n = _cpu_count()
    valid = sorted(c for c in _parse_cpu_list(spec) if 0 <= c < n)
    return ",".join(str(c) for c in valid)


def cpu_nice() -> int:
    """nice increment for the managed local runtime, so it always yields to the
    orchestration and interactive work. 0 disables. Clamped 0..19; default 10."""
    try:
        return max(0, min(19, int(_cfg("cpu_nice", 10))))
    except (TypeError, ValueError):
        return 10


def ollama_keep_alive() -> str:
    """OLLAMA_KEEP_ALIVE for a *managed* ollama: how long a model stays resident
    after its last request. Short keeps idle RAM low (the model reloads in a few
    seconds on the next call); the model burns ~0% CPU while merely resident, so
    this is a RAM knob, not a CPU one. Default "5m" (ollama's own default)."""
    return str(_cfg("ollama_keep_alive", "5m") or "5m").strip()


def autostart_enabled() -> bool:
    """Whether the orchestration may spin the local runtime up itself on demand.
    Opt out with validate.autostart: false (then run scripts/local-validator.sh
    or point at an externally-managed endpoint)."""
    val = _cfg("autostart", True)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off")
    return bool(val)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def mlx_python() -> str:
    """Interpreter that runs mlx_lm.server. Default: the repo's uv-managed venv."""
    override = str(_cfg("mlx_python", "") or "").strip()
    if override:
        return override
    return str(_repo_root() / ".venv-mlx" / "bin" / "python")


def _port_from_endpoint(endpoint: str) -> int:
    try:
        return int(endpoint.rsplit(":", 1)[1].split("/", 1)[0])
    except (IndexError, ValueError):
        return 8088


# ---------------------------------------------------------------------------
# Endpoint detection
# ---------------------------------------------------------------------------

def _http_json(method: str, url: str, payload: Optional[dict], timeout: float) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def loaded_models(endpoint: Optional[str] = None) -> List[str]:
    """Model ids the endpoint reports as available, or [] if unreachable."""
    endpoint = endpoint or resolve_endpoint()
    try:
        data = _http_json("GET", f"{endpoint}/models", None, _REACH_TIMEOUT)
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception as exc:
        log.debug("local validator endpoint unreachable at %s: %s", endpoint, exc)
        return []


def endpoint_available(endpoint: Optional[str] = None) -> bool:
    return bool(loaded_models(endpoint))


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class LocalValidatorAdapter(LLMAdapter):
    """OpenAI-compatible HTTP client for a local model (mlx_lm.server / ollama).

    Non-fatal by contract: connection/5xx errors raise so a wrapping
    `FailoverAdapter` falls through to the next model or the paid fallback.
    """

    def __init__(self, model: str, endpoint: Optional[str] = None,
                 runtime: Optional[str] = None, timeout: float = _GEN_TIMEOUT,
                 min_tokens: Optional[int] = None):
        self.model_key = model
        self._model = model
        self._runtime = runtime or resolve_runtime()
        self.backend = self._runtime  # "mlx" | "ollama"
        self._endpoint = (endpoint or resolve_endpoint(self._runtime)).rstrip("/")
        self._timeout = timeout
        # Token floor: local *reasoning* models (e.g. VibeThinker) emit a long
        # <think> trace before the answer. The paid validation caller passes a
        # tiny budget (128) that's fine for non-reasoners but starves a reasoner
        # mid-thought, leaving `content` empty. Floor the budget so it finishes.
        # Default floor 2048: live runs showed a reasoning model's <think> trace
        # on a *real* (long) step result overruns 1024, truncating before the JSON
        # verdict → empty content → spurious escalation. Tune per model in deep-eval.
        self._min_tokens = int(_cfg("local_max_tokens", 2048) if min_tokens is None else min_tokens)

    def complete(self, messages: List[LLMMessage], *, tools=None,
                 tool_choice: str = "auto", max_tokens: int = 256,
                 temperature: float = 0.1, thinking_budget=None,
                 **kwargs) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max(int(max_tokens), self._min_tokens),
            "temperature": temperature,
            "stream": False,
        }
        try:
            data = _http_json("POST", f"{self._endpoint}/chat/completions",
                              payload, self._timeout)
        except urllib.error.URLError as exc:
            # Surfaces as a failover-eligible error (connection/unavailable).
            raise RuntimeError(
                f"local validator unavailable ({self._runtime} @ {self._endpoint}): {exc}"
            ) from exc
        choices = data.get("choices") or []
        content = ""
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content", "") or ""
            # Reasoning runtimes split CoT into a separate field and leave the
            # answer in `content`. If the budget truncated mid-thought, `content`
            # is empty — fall back to the reasoning trace so a trailing JSON
            # verdict (if any) is still recoverable by extract_json downstream.
            if not content.strip():
                content = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            stop_reason=(choices[0].get("finish_reason") if choices else "stop") or "stop",
            model=self._model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            backend=self._runtime,
        )


# ---------------------------------------------------------------------------
# Builder — the public entry point
# ---------------------------------------------------------------------------

# Session cache so we don't re-probe the endpoint on every step. Keyed by the
# (runtime, endpoint, tuple(models)) signature; reset via reset_cache() in tests.
_CACHE: dict = {}


def reset_cache() -> None:
    _CACHE.clear()


def validator_available() -> bool:
    """True if a local validator is configured AND at least one configured model
    is currently loaded at the endpoint. Cached for the session (the endpoint is
    not re-probed on every call)."""
    models = configured_models()
    if not models:
        return False
    key = ("_avail", resolve_runtime(), resolve_endpoint(), tuple(models))
    # Cache POSITIVES only. A model that is loaded stays loaded for the session,
    # so caching "available" avoids re-probing every step. But a negative is
    # transient: spin-up brings the endpoint online mid-process (lazy start), and
    # build_local_validator_adapter() already re-probes on every call — caching a
    # negative here would freeze auto_verify_enabled() OFF for the whole run even
    # after the model comes up, an inconsistency between the two code paths.
    if _CACHE.get(key):
        return True
    avail = bool(set(models) & set(loaded_models()))
    if avail:
        _CACHE[key] = True
    return avail


def auto_verify_configured() -> bool:
    """The `validate.auto_verify` config flag alone (default True), independent of
    whether a server is currently reachable. Used to decide run-start spin-up,
    where the endpoint is still down (chicken-and-egg with availability)."""
    val = _cfg("auto_verify", True)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off")
    return bool(val)


def auto_verify_enabled() -> bool:
    """Whether to default the ralph verify loop ON because a usable local
    validator exists (verification is then free). Opt out with
    `validate.auto_verify: false`. Returns False when no local validator is
    actually available, so we never silently switch verification to the paid
    path just because models were listed in config."""
    return auto_verify_configured() and validator_available()


def build_local_validator_adapter(fallback: Optional[LLMAdapter] = None
                                  ) -> Optional[LLMAdapter]:
    """Build the local-first validator adapter, or None to use the paid path.

    Returns:
      * None — no local models configured, or none are loaded at the endpoint.
        Callers should validate with their existing (paid) adapter, unchanged.
      * A `FailoverAdapter` over the configured local models that are actually
        loaded, with `fallback` appended last (if given) for graceful
        degradation when every local model errors mid-run.

    The result is cached for the session keyed by config + loaded-model set.
    """
    models = configured_models()
    if not models:
        return None

    runtime = resolve_runtime()
    endpoint = resolve_endpoint(runtime)
    available = set(loaded_models(endpoint))
    usable = [m for m in models if m in available]

    sig = (runtime, endpoint, tuple(usable), id(fallback))
    if sig in _CACHE:
        return _CACHE[sig]

    if not usable:
        log.info("local validator: configured %s but none loaded at %s (%s) — using paid path",
                 models, endpoint, runtime)
        _CACHE[sig] = None
        return None

    from llm import FailoverAdapter
    adapters: List[LLMAdapter] = [
        LocalValidatorAdapter(m, endpoint=endpoint, runtime=runtime) for m in usable
    ]
    if fallback is not None:
        adapters.append(fallback)
    result = adapters[0] if len(adapters) == 1 else FailoverAdapter(adapters)
    log.info("local validator active: %s via %s (%s)%s",
             usable, endpoint, runtime, " + paid fallback" if fallback else "")
    _CACHE[sig] = result
    return result


# ---------------------------------------------------------------------------
# Orchestration-managed runtime lifecycle
# ---------------------------------------------------------------------------
# The local model is NOT an OS service. The orchestration spins it up on demand
# (first validation), keeps it warm while validations flow, and reaps it after
# idle (and on process exit). An already-running server — ours or one started by
# scripts/local-validator.sh — is reused, never duplicated. Both runtimes (mlx
# and ollama's `serve`) are managed the same way: launched under a CPU cap and
# torn down by process group, so neither can outlive the run or starve the box.

_PROC_LOCK = threading.Lock()
_MANAGED: dict = {"proc": None, "last_use": 0.0, "reaper": None}


def _touch_validator() -> None:
    _MANAGED["last_use"] = time.monotonic()


def _cpu_cap_prefix() -> List[str]:
    """`nice`/`taskset` prefix that caps a managed runtime's CPU so it can't
    starve the box. Linux only, and only for tools that exist; returns [] on
    macOS (mlx) or when the tools are absent."""
    if platform.system() != "Linux":
        return []
    prefix: List[str] = []
    n = cpu_nice()
    if n > 0 and shutil.which("nice"):
        prefix += ["nice", "-n", str(n)]
    cores = cpu_affinity()
    if cores and shutil.which("taskset"):
        prefix += ["taskset", "-c", cores]
    return prefix


def _launch_argv_env(runtime: str, model: str, endpoint: str):
    """Build (argv, env_overrides) to start `runtime` serving `model` at
    `endpoint`. Returns (None, None) when the runtime can't be managed here
    (missing interpreter/binary, or an unknown runtime → use the paid path)."""
    if runtime == "mlx":
        py = mlx_python()
        if not Path(py).exists():
            log.warning("local validator autostart: interpreter missing at %s — "
                        "run scripts/local-validator.sh setup", py)
            return None, None
        port = _port_from_endpoint(endpoint)
        return [py, "-m", "mlx_lm", "server", "--model", model, "--port", str(port)], {}
    if runtime == "ollama":
        exe = shutil.which("ollama")
        if not exe:
            log.warning("local validator autostart: 'ollama' not on PATH — "
                        "install ollama or point validate.endpoint at a running one")
            return None, None
        # Serve serially with one resident model; the model loads lazily on the
        # first request. KEEP_ALIVE bounds idle RAM (CPU is capped via the prefix).
        env = {
            "OLLAMA_KEEP_ALIVE": ollama_keep_alive(),
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
        }
        return [exe, "serve"], env
    return None, None


def _terminate_group(proc, timeout: float = 10.0) -> None:
    """SIGTERM (then SIGKILL) the managed server's whole process group, so a
    daemon's children die with it — ollama's `serve` spawns a `llama-server`
    child that a bare proc.terminate() would orphan (last seen surviving a
    pkill and pinning cores). Safe when the process is already gone."""
    import signal
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except Exception:
            if pgid is not None:
                with contextlib.suppress(OSError):
                    os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
    except (ProcessLookupError, OSError):
        pass


def ensure_validator_running(*, wait_secs: float = 60.0, start_reaper: bool = True) -> bool:
    """Make a local validator available, spinning one up on demand if needed.

    Reuses any reachable server (ours or external). Manages both supported
    runtimes — **mlx** (`mlx_lm.server`) and **ollama** (`ollama serve`) — each
    launched under a CPU cap (`nice`/`taskset`, see `_cpu_cap_prefix`) so local
    inference can't starve the box. No-op when no models are configured or
    `validate.autostart` is false. Never raises; returns True if a usable
    validator is available afterward.

    start_reaper=False suppresses idle reaping — used when a run owns the
    lifecycle (spins up at run start, tears down at run end) so the server stays
    warm for the whole run instead of being reaped between steps.
    """
    if not configured_models():
        return False
    if validator_available():            # already up → reuse, just mark activity
        _touch_validator()
        return True
    if not autostart_enabled():
        return False
    # Never spin a *real* local server up from inside the test harness. Integration
    # and e2e tests run real loop code (managed_for_run → here); with the live config
    # carrying autostart + local_models, that would otherwise launch an actual
    # ollama/mlx process mid-suite. conftest sets MARO_PYTEST_ACTIVE in os.environ
    # (inherited by subprocesses); unit tests that exercise the spawn path clear it.
    if os.environ.get("MARO_PYTEST_ACTIVE"):
        return False

    runtime = resolve_runtime()
    model = configured_models()[0]
    endpoint = resolve_endpoint(runtime)
    argv, env_over = _launch_argv_env(runtime, model, endpoint)
    if argv is None:                     # unmanageable runtime / missing binary
        return False
    argv = _cpu_cap_prefix() + argv

    with _PROC_LOCK:
        if validator_available():        # another thread won the race
            _touch_validator()
            return True
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, **env_over} if env_over else None,
                start_new_session=True,  # own process group → clean group teardown
            )
        except Exception as exc:
            log.warning("local validator autostart failed to spawn: %s", exc)
            return False
        _MANAGED["proc"] = proc
        atexit.register(shutdown_validator)
        log.info("local validator: spinning up %s (%s) → %s (pid %s)",
                 model, runtime, endpoint, proc.pid)

    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.warning("local validator server exited during startup (code %s)", proc.returncode)
            with _PROC_LOCK:
                _MANAGED["proc"] = None
            return False
        reset_cache()
        if model in loaded_models(endpoint):
            _touch_validator()
            if start_reaper:
                _start_reaper()
            log.info("local validator ready: %s @ %s", model, endpoint)
            return True
        time.sleep(1.0)
    log.warning("local validator did not become ready within %.0fs", wait_secs)
    return False


def _start_reaper() -> None:
    if _MANAGED["reaper"] is not None and _MANAGED["reaper"].is_alive():
        return
    secs = idle_shutdown_secs()
    if secs <= 0:
        return  # reaping disabled; teardown only at process exit

    def _reap() -> None:
        while True:
            proc = _MANAGED["proc"]
            if proc is None or proc.poll() is not None:
                reset_cache()
                return
            if time.monotonic() - _MANAGED["last_use"] >= secs:
                log.info("local validator idle ≥ %ds — spinning down", secs)
                shutdown_validator()
                return
            time.sleep(min(secs, 30))

    t = threading.Thread(target=_reap, name="local-validator-reaper", daemon=True)
    _MANAGED["reaper"] = t
    t.start()


def shutdown_validator() -> None:
    """Terminate the managed server if we started one. Safe to call repeatedly;
    no-op when the validator is external or already down."""
    with _PROC_LOCK:
        proc = _MANAGED["proc"]
        _MANAGED["proc"] = None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            _terminate_group(proc)
        log.info("local validator: spun down (pid %s)", getattr(proc, "pid", "?"))
    except Exception as exc:
        log.debug("local validator shutdown error (non-fatal): %s", exc)
    reset_cache()


@contextlib.contextmanager
def managed_for_run(goal: str = "", ralph_verify: bool = False):
    """Run-scoped validator lifecycle: spin the model up for the duration of a
    run (if it'll be used) and tear down what *this run* started when it ends —
    on success or failure. Reused/external servers and parent-run servers (nested
    calls) are left running; only the run that actually spawned reaps. Idle
    reaping is suppressed so the server stays warm across the whole run.
    """
    want = (bool(configured_models()) and autostart_enabled()
            and (ralph_verify
                 or str(goal or "").lower().startswith(("ralph:", "verify:"))
                 or auto_verify_configured()))
    owner = False
    if want and not validator_available():
        ensure_validator_running(start_reaper=False)
        owner = _MANAGED["proc"] is not None   # only the spawner owns teardown
    try:
        yield
    finally:
        if owner:
            shutdown_validator()
