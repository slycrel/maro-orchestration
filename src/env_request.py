"""Environment requests — Maro installs what a job needs, never with runtime root.

Decree (Jeremy 2026-09-07, decision ea9e311f): *"maro can't 'safely'
install software it needs to get its job done — we need to help facilitate
that"*, with the rule that no worker ever runs as root. The shape mirrors
the operator-ask lane (`operator_ask.py`): a request is a FILE, not a
sentence. A containerized worker that lacks a tool writes ONE JSON object
to `$MARO_ENV_REQUEST` and ends its step:

    {"need": "drive a headless browser for the Yahoo login",
     "apt": ["chromium"], "pip": [], "npm": ["playwright"],
     "tried": "which chromium / npx playwright — both absent, no sudo"}

The engine reads the file after the step (never the prose), archives it,
and decides by policy:

  * in policy → build a per-project image LAYER (`FROM <base>` + the
    package lines, root at `docker build` time only), tag it, record the
    Dockerfile + ledger under `<workspace>/executor-layers/<project>/`,
    and run the SAME step again on the new image with an "Environment
    updated" note. Later steps and later runs of the project start on it.
  * malformed / disabled / host lane → the step runs again with the
    reason; the worker adjusts or proceeds without.
  * out of policy → ESCALATE to the acting orchestrator (Hermes/Poe or
    the Claude session), never straight to the user: the run pauses on
    the same typed pause as an operator question, the `escalation` event
    carries a one-line decision, and `maro answer <handle> allow|deny
    [note]` resolves it. `allow` records a project GRANT (the override
    lives beside the policy, never inside it), builds, and resumes the
    run on the new image; `deny` resumes it with the note.

What the worker can never do: get root in its own container, persist
anything outside the run scratch, or install by prose. What the engine
never does: run a privileged worker, or install on the host.

Design: `docs/ENV_REQUEST_DESIGN.md`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("maro.env_request")

REQUEST_NAME = "env-request.json"
REQUEST_ENV = "MARO_ENV_REQUEST"
CONTAINER_REQUEST_PATH = "/tmp/" + REQUEST_NAME
SOURCES = ("apt", "pip", "npm")
# Playwright browsers are a build-time download, not a package: `playwright
# install --with-deps <name>` needs root for the system libs and ~400 MB
# per browser that would otherwise be re-downloaded every `--rm` step (seen
# live on 084d3c1f, 2026-09-07). Requested as `"browsers": ["firefox"]`;
# implies pip playwright.
BROWSERS = ("chromium", "firefox", "webkit")
MAX_PER_SOURCE = 20
MAX_RETRIES_PER_STEP = 2
LAYERS_DIR = "executor-layers"
ASK_KIND = "env_request"
EVENT_ESCALATION = "escalation"
ESCALATION_POINT = "env_request"

# Package-name shapes per source. Anything else is MALFORMED (rejected with
# the reason, never built, never escalated): the names reach a Dockerfile
# RUN line, so the grammar is the injection boundary.
_NAME_RE = {
    "apt": re.compile(r"^[a-z0-9][a-z0-9+.\-]{0,99}$"),
    "pip": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}"
                      r"(\[[A-Za-z0-9,_\-]{1,60}\])?"
                      r"((==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!\-]{1,40})?$"),
    "npm": re.compile(r"^(@[a-z0-9][a-z0-9._\-]{0,60}/)?[a-z0-9][a-z0-9._\-]{0,100}"
                      r"(@[A-Za-z0-9.^~*<>=\-]{1,40})?$"),
}

# Out of policy by default: things that change what the CONTAINER is (a
# service manager, a second sandbox, privilege tools), not what it can do.
# A grant can still allow any of them — the list is the default, the grant
# ledger is the override.
DEFAULT_DENY = ("sudo", "su", "openssh-server", "docker", "docker.io", "docker-ce",
                "containerd", "podman", "systemd", "cron", "polkitd", "dbus")


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _cfg(key: str, default: Any) -> Any:
    try:
        from config import get
        v = get(key, default)
        return default if v is None else v
    except Exception:
        return default


def enabled() -> bool:
    v = _cfg("env.install.enabled", True)
    return str(v).strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# The file contract (worker side)
# ---------------------------------------------------------------------------

def request_path(scratch: Optional[str]) -> Optional[Path]:
    if not scratch:
        return None
    return Path(scratch) / REQUEST_NAME


def instructions(path) -> str:
    """The `## Installing what you need` paragraph for the execute frame
    (container lane only — on the host the worker installs with its own
    user-level tools)."""
    return (
        "## Installing what you need\n"
        "This step runs in a container as a normal user: `apt`, `pip` and `npm` "
        "installs fail here and there is no sudo — do not try. If the task needs "
        "software the image lacks, write ONE JSON object to `" + str(path) + "` "
        "and end your step:\n"
        '{"need": "<what it is for>", "apt": ["<package>"], "pip": [], "npm": [], '
        '"browsers": [], "tried": "<what you tried without it>"}\n'
        "`browsers` takes chromium / firefox / webkit and bakes a Playwright browser "
        "with its system libraries into the image (a per-step `playwright install` "
        "re-downloads hundreds of MB and dies with the step). "
        "The engine builds a new image with those packages (root at build time, "
        "never in your step) and runs THIS step again on it; the retry opens with "
        "\"Environment updated\" naming what landed. A request outside policy goes to "
        "the orchestrator and you are told the outcome either way. Request only what "
        "the task needs, name packages exactly as their source knows them, and never "
        "claim to have installed anything yourself."
    )


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out = []
    for x in v:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def read_request(path) -> Optional[Dict[str, Any]]:
    """The request the worker wrote, or None when there is none. A file
    that is not a request (bad JSON, no packages, no `need`) raises
    ValueError — the caller tells the worker and moves on."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_text(encoding="utf-8", errors="replace")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"env-request.json is not JSON: {exc}")
    if not isinstance(d, dict):
        raise ValueError("env-request.json must be one JSON object")
    req: Dict[str, Any] = {
        "need": str(d.get("need") or d.get("why") or "").strip()[:600],
        "tried": str(d.get("tried") or d.get("no_install_alternative") or "").strip()[:600],
    }
    total = 0
    for src in SOURCES:
        items = _as_list(d.get(src))
        if len(items) > MAX_PER_SOURCE:
            raise ValueError(f"{src}: more than {MAX_PER_SOURCE} packages in one request")
        req[src] = items
        total += len(items)
    req["browsers"] = [b.lower() for b in _as_list(d.get("browsers") or d.get("playwright"))]
    total += len(req["browsers"])
    if not req["need"]:
        raise ValueError("env-request.json has no `need`")
    if total == 0:
        raise ValueError("env-request.json names no packages (apt/pip/npm/browsers)")
    return req


def archive_request(path) -> Optional[Path]:
    """Move the consumed request beside itself (never delete it)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = p.with_name(f"env-request.{stamp}.requested.json")
    n = 0
    while target.exists():
        n += 1
        target = p.with_name(f"env-request.{stamp}-{n}.requested.json")
    p.replace(target)
    return target


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    allowed: Dict[str, List[str]] = field(default_factory=lambda: {s: [] for s in SOURCES})
    escalate: List[Tuple[str, str, str]] = field(default_factory=list)  # (source, spec, why)
    rejected: List[Tuple[str, str, str]] = field(default_factory=list)  # (source, spec, why)
    browsers: List[str] = field(default_factory=list)

    @property
    def allowed_specs(self) -> List[str]:
        return ([f"{s}:{x}" for s in SOURCES for x in self.allowed.get(s, [])]
                + [f"browser:{b}" for b in self.browsers])

    @property
    def any_allowed(self) -> bool:
        return any(self.allowed.get(s) for s in SOURCES) or bool(self.browsers)


def _bare_name(source: str, spec: str) -> str:
    s = spec
    if source == "pip":
        s = re.split(r"[\[=<>~!]", s, maxsplit=1)[0]
    elif source == "npm":
        s = s[1:] if s.startswith("@") else s
        s = s.split("@", 1)[0]
        s = "@" + s if spec.startswith("@") else s
    return s.strip().lower()


def evaluate(req: Dict[str, Any], grants: Sequence[str] = ()) -> Verdict:
    """Policy over one request: well-formed + allowed source + not denied →
    allowed; well-formed but off-source or denied → escalate (unless a
    grant names it); malformed → rejected. Grants are `source:spec`."""
    v = Verdict()
    sources = [str(s).strip().lower() for s in _as_list(_cfg("env.install.sources", list(SOURCES)))]
    deny = {str(d).strip().lower() for d in _as_list(_cfg("env.install.deny", list(DEFAULT_DENY)))}
    granted = {str(g).strip() for g in grants}
    for src in SOURCES:
        for spec in req.get(src, []):
            if not _NAME_RE[src].match(spec):
                v.rejected.append((src, spec, "malformed package name"))
                continue
            key = f"{src}:{spec}"
            if key in granted:
                v.allowed[src].append(spec)
                continue
            if src not in sources:
                v.escalate.append((src, spec, f"{src} installs are not in policy (env.install.sources)"))
                continue
            if _bare_name(src, spec) in deny:
                v.escalate.append((src, spec, f"{spec} is on the deny list (env.install.deny)"))
                continue
            v.allowed[src].append(spec)
    for b in req.get("browsers", []) or []:
        if b not in BROWSERS:
            v.rejected.append(("browser", b, "unknown browser (chromium / firefox / webkit)"))
        elif "pip" not in sources:
            v.escalate.append(("browser", b, "browsers ride pip playwright, and pip installs are not in policy"))
        else:
            v.browsers.append(b)
    return v


# ---------------------------------------------------------------------------
# Per-project layers
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    ws = os.environ.get("MARO_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if ws:
        return Path(ws)
    try:
        from config import workspace_root
        return Path(workspace_root())
    except Exception:
        return Path.home() / ".maro" / "workspace"


def project_slug(project: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(project or "").strip().lower()).strip("-")
    return (s or "default")[:40].strip("-") or "default"


def layer_dir(project: str) -> Path:
    return _workspace_root() / LAYERS_DIR / project_slug(project)


def _base_image() -> str:
    try:
        from container_exec import container_image
        return container_image()
    except Exception:
        return "maro-executor:unknown"


def _image_suffix() -> str:
    """Keep the `-<cli>-r<N>` tail so the verbs-baked detection in
    container_exec (`^maro-executor:[^:]+-r(\\d+)$`) still reads the base
    revision off a project image."""
    try:
        from container_exec import CLAUDE_CLI_VERSION, IMAGE_REVISION
        return f"{CLAUDE_CLI_VERSION}-r{IMAGE_REVISION}"
    except Exception:
        return "0-r0"


def image_tag(project: str, layer: int) -> str:
    return f"maro-executor:p-{project_slug(project)}-l{layer}-{_image_suffix()}"


def load_manifest(project: str) -> Dict[str, Any]:
    p = layer_dir(project) / "manifest.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("env_request: manifest unreadable for %s: %s", project, exc)
    return {"project": project, "base": "", "layer": 0, "image": "",
            "apt": [], "pip": [], "npm": [], "grants": [], "updated_at": ""}


def save_manifest(project: str, m: Dict[str, Any]) -> Path:
    d = layer_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "manifest.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def add_grants(project: str, specs: Sequence[str]) -> Dict[str, Any]:
    m = load_manifest(project)
    have = list(m.get("grants") or [])
    for s in specs:
        if s and s not in have:
            have.append(s)
    m["grants"] = have
    m["updated_at"] = _iso(datetime.now(timezone.utc))
    save_manifest(project, m)
    return m


def render_dockerfile(base: str, apt: Sequence[str], pip: Sequence[str],
                      npm: Sequence[str], *, project: str = "", layer: int = 0,
                      browsers: Sequence[str] = ()) -> str:
    """The reviewable artifact. Root happens in these RUN lines and nowhere
    else; the runtime still starts every step with `--user <host uid>`.
    Playwright browsers land in a world-readable `/opt/ms-playwright` so the
    `--user <uid>` worker finds them (`PLAYWRIGHT_BROWSERS_PATH`)."""
    apt = list(apt)
    pip = list(pip)
    browsers = [b for b in browsers if b in BROWSERS]
    if browsers and not any(_bare_name("pip", x) == "playwright" for x in pip):
        pip.append("playwright")
    if pip and "python3-pip" not in apt:
        apt.append("python3-pip")  # the slim base has no pip
    lines = [
        f"# generated by maro env_request — project {project or '?'}, layer {layer}.",
        "# Edit manifest.json and rebuild; this file is regenerated from it.",
        f"FROM {base}",
        "USER root",
    ]
    if apt:
        lines.append(
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            + " ".join(shlex.quote(x) for x in apt)
            + " && rm -rf /var/lib/apt/lists/*")
    if pip:
        lines.append(
            "RUN python3 -m pip install --no-cache-dir --break-system-packages "
            + " ".join(shlex.quote(x) for x in pip))
    if npm:
        lines.append("RUN npm install -g " + " ".join(shlex.quote(x) for x in npm))
    if browsers:
        lines.append("ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright")
        lines.append("RUN python3 -m playwright install --with-deps "
                     + " ".join(shlex.quote(b) for b in browsers)
                     + " && chmod -R a+rX /opt/ms-playwright")
    return "\n".join(lines) + "\n"


@dataclass
class BuildResult:
    ok: bool
    image: str
    layer: int
    added: List[str]
    seconds: float
    detail: str = ""       # failure tail or "cached"
    dockerfile: str = ""


def _docker_build(tag: str, dockerfile: Path, context: Path, timeout_s: float) -> Tuple[bool, str]:
    """Run `docker build`; returns (ok, output tail). Injectable (tests)."""
    cmd = ["docker", "build", "--pull=false", "-t", tag, "-f", str(dockerfile), str(context)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return False, "docker not found"
    except subprocess.TimeoutExpired:
        return False, f"docker build exceeded {int(timeout_s)}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


def _image_exists(tag: str) -> bool:
    try:
        proc = subprocess.run(["docker", "image", "inspect", tag],
                              capture_output=True, text=True, timeout=10)
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# Test seams (CODING_NOTES: seams, not internals).
_BUILD: Callable[[str, Path, Path, float], Tuple[bool, str]] = _docker_build
_EXISTS: Callable[[str], bool] = _image_exists


def _tail(s: str, n: int = 1500) -> str:
    s = s or ""
    return s[-n:]


def build_layer(project: str, verdict: Verdict, *, reason: str = "") -> BuildResult:
    """Add the allowed packages to the project's manifest, render the
    Dockerfile, build the next layer image, record the ledger line. The
    manifest only advances on success; a failed build leaves the previous
    layer current and the failure tail in build.log + the ledger."""
    t0 = time.monotonic()
    m = load_manifest(project)
    base = _base_image()
    if m.get("base") and m["base"] != base:
        # The operator moved the base image: start the project's layer stack
        # over on the new base with the same accumulated packages.
        log.info("env_request: base changed %s → %s; rebuilding %s from scratch",
                 m["base"], base, project)
    added: List[str] = []
    pkgs = {s: list(m.get(s) or []) for s in SOURCES}
    for s in SOURCES:
        for spec in verdict.allowed.get(s, []):
            if spec not in pkgs[s]:
                pkgs[s].append(spec)
                added.append(f"{s}:{spec}")
    browsers = list(m.get("browsers") or [])
    for b in verdict.browsers:
        if b not in browsers:
            browsers.append(b)
            added.append(f"browser:{b}")
    layer = int(m.get("layer") or 0) + 1
    tag = image_tag(project, layer)
    if not added and m.get("image") and m.get("base") == base and _EXISTS(str(m["image"])):
        return BuildResult(True, str(m["image"]), int(m["layer"]), [], 0.0, detail="cached")
    d = layer_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    df_text = render_dockerfile(base, pkgs["apt"], pkgs["pip"], pkgs["npm"],
                                project=project, layer=layer, browsers=browsers)
    df = d / "Dockerfile"
    df.write_text(df_text, encoding="utf-8")
    timeout_s = float(_cfg("env.install.build_timeout_s", 900) or 900)
    ok, out = _BUILD(tag, df, d, timeout_s)
    secs = round(time.monotonic() - t0, 1)
    try:
        (d / "build.log").write_text(out or "", encoding="utf-8")
    except OSError:
        pass
    if ok:
        m.update({"project": project, "base": base, "layer": layer, "image": tag,
                  "browsers": browsers,
                  "updated_at": _iso(datetime.now(timezone.utc)), **pkgs})
        save_manifest(project, m)
    try:
        with (d / "layers.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": _iso(datetime.now(timezone.utc)), "layer": layer, "image": tag,
                "base": base, "added": added, "ok": ok, "seconds": secs,
                "reason": reason[:300], "detail": "" if ok else _tail(out, 600),
            }) + "\n")
    except OSError:
        pass
    try:
        from captains_log import log_event
        log_event("ENV_LAYER_BUILT" if ok else "ENV_LAYER_FAILED", project,
                  (f"{tag}: +{', '.join(added) or 'nothing'} ({secs}s)" if ok
                   else f"{tag}: build failed — {_tail(out, 200)}"))
    except Exception:
        pass
    log.warning("env_request: %s layer %d for %s (%s) %s", "built" if ok else "FAILED",
                layer, project, ", ".join(added) or "no new packages", f"{secs}s")
    return BuildResult(ok, tag if ok else str(m.get("image") or ""), layer if ok else int(m.get("layer") or 0),
                       added, secs, detail="" if ok else _tail(out), dockerfile=df_text)


def effective_image(project: Optional[str]) -> Optional[str]:
    """The project's current layer image when one exists for the current
    base and is present in docker, else None (→ the base image)."""
    if not project or not enabled():
        return None
    m = load_manifest(project)
    img = str(m.get("image") or "")
    if not img or m.get("base") != _base_image():
        return None
    if not _EXISTS(img):
        log.warning("env_request: project image %s missing from docker; using the base image", img)
        return None
    return img


def current_project() -> Optional[str]:
    """The project of the current run (its metadata), for image resolution
    at docker-run time where no loop context is at hand."""
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd is None:
            return None
        meta = json.loads((Path(rd) / "metadata.json").read_text(encoding="utf-8"))
        p = str(meta.get("project") or "").strip()
        return p or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# What the loop does with a request
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    kind: str                 # built | build_failed | rejected | escalate | nothing
    note: str                 # the text the retried step opens with (or the decision line)
    verdict: Optional[Verdict] = None
    build: Optional[BuildResult] = None
    request: Optional[Dict[str, Any]] = None


def _specs_line(v: Verdict) -> str:
    return ", ".join(v.allowed_specs)


def decision_line(project: str, verdict: Verdict, req: Dict[str, Any], handle_id: str) -> str:
    esc = "; ".join(f"{s}:{spec} — {why}" for s, spec, why in verdict.escalate)
    return (f"Decide: the run wants to install {esc} for project {project} "
            f"(need: {req.get('need', '')[:200]}). Out of policy. "
            f"Answer: maro answer {handle_id} allow  |  maro answer {handle_id} deny <why>")


def handle(req: Dict[str, Any], *, project: Optional[str], container: bool,
           handle_id: str = "", step: str = "") -> Outcome:
    """Decide and act on one request. Never raises."""
    if not enabled():
        return Outcome("rejected", "Environment requests are disabled on this install "
                       "(env.install.enabled); proceed without the packages or finish with a stated gap.",
                       request=req)
    if not container:
        return Outcome("rejected", "This step ran on the host, not in a container: install user-level "
                       "tools yourself (pip --user, npm --prefix ~/.local) or finish with a stated gap.",
                       request=req)
    proj = project or "default"
    m = load_manifest(proj)
    v = evaluate(req, grants=m.get("grants") or [])
    if v.rejected and not v.any_allowed and not v.escalate:
        why = "; ".join(f"{s}:{spec} ({w})" for s, spec, w in v.rejected)
        return Outcome("rejected", f"Environment request refused — {why}. Name packages exactly "
                       "as their source knows them, or proceed without them.", verdict=v, request=req)
    if v.escalate:
        return Outcome("escalate", decision_line(proj, v, req, handle_id or "<handle>"),
                       verdict=v, request=req)
    br = build_layer(proj, v, reason=str(req.get("need") or "")[:300])
    rej = ("; also refused (malformed): " + ", ".join(f"{s}:{spec}" for s, spec, _ in v.rejected)
           if v.rejected else "")
    if br.ok:
        note = (f"Environment updated: installed {', '.join(br.added) or 'nothing new (already present)'} "
                f"— this step now runs on image {br.image}{rej}. Do not request these again; "
                "continue the step with them.")
        return Outcome("built", note, verdict=v, build=br, request=req)
    note = (f"Environment request FAILED to build ({_specs_line(v)}): {br.detail[-600:]}{rej}. "
            "Adjust the request (exact package names, fewer packages) or proceed without it.")
    return Outcome("build_failed", note, verdict=v, build=br, request=req)


# ---------------------------------------------------------------------------
# Escalation to the orchestrator (pause + `escalation` event) and the answer
# ---------------------------------------------------------------------------

def pause_for_request(outcome: Outcome, *, handle_id: str, goal: str, project: str,
                      step: str = "", loop_id: Optional[str] = None) -> Dict[str, Any]:
    """Record the out-of-policy request on the run as an `operator_ask` of
    kind env_request (same typed pause and resume-by-handle as a question)
    and emit the `escalation` event — the orchestrator's channel, not the
    user's. Never raises."""
    from operator_ask import (META_KEY, STATUS_PENDING, deadline_for, answer_command)
    from stop_verdicts import PAUSE_OP_CLARIFICATION
    now = datetime.now(timezone.utc)
    v = outcome.verdict or Verdict()
    req = outcome.request or {}
    record = {
        "kind": ASK_KIND,
        "question": outcome.note,
        "why": str(req.get("need") or ""),
        "no_input_alternative": str(req.get("tried") or ""),
        "tried": bool(req.get("tried")),
        "step": (step or "")[:300],
        "asked_at": _iso(now),
        "deadline": deadline_for(now),
        "status": STATUS_PENDING,
        "source": "worker",
        "project": project,
        "request": {**{s: list(req.get(s) or []) for s in SOURCES},
                    "browsers": list(req.get("browsers") or [])},
        "escalate": [f"{s}:{spec}" for s, spec, _ in v.escalate],
        "escalate_why": [why for _, _, why in v.escalate],
    }
    try:
        from runs import stamp_run_metadata
        stamp_run_metadata({
            META_KEY: record,
            "clarification_question": record["question"],
            "pause_reason": PAUSE_OP_CLARIFICATION,
        })
    except Exception as exc:
        log.warning("env_request: run metadata stamp failed: %s", exc)
    try:
        from run_trace import record_edge
        record_edge("step.env_request", "pause." + PAUSE_OP_CLARIFICATION,
                    loop_id=loop_id, handle_id=handle_id or None,
                    escalate=record["escalate"])
    except Exception:
        pass
    try:
        from notify import emit
        emit(EVENT_ESCALATION, {
            "handle_id": handle_id,
            "goal": goal,
            "status": "paused",
            "point": ESCALATION_POINT,
            "decision": outcome.note,
            "summary": f"install {', '.join(record['escalate'])} for {project}: {record['why'][:200]}",
            "reason": "; ".join(record["escalate_why"]),
            "project": project,
            "request": record["request"],
            "step": record["step"],
            "deadline": record["deadline"],
            "answer_with": answer_command(handle_id),
            "audience": "orchestrator",
        })
    except Exception as exc:
        log.warning("env_request: notify failed: %s", exc)
    return record


def parse_answer(text: str) -> Tuple[str, str]:
    """`allow [note]` / `deny [note]` (also yes/approve/no/reject) → (verb, note)."""
    t = (text or "").strip()
    head, _, rest = t.partition(" ")
    h = head.strip().lower().rstrip(":,.")
    rest = rest.strip().lstrip("—–-:,. ").strip()
    if h in ("allow", "allowed", "approve", "approved", "yes", "ok", "grant", "granted"):
        return "allow", rest.strip()
    if h in ("deny", "denied", "no", "reject", "rejected", "refuse", "refused"):
        return "deny", rest.strip()
    return "", t


def apply_answer(rec: Dict[str, Any], text: str) -> Tuple[str, str]:
    """Resolve an env_request escalation with the orchestrator's answer.
    `allow` → grant the escalated specs to the project, build the layer
    now (this runs on the host, where docker is), and return what
    happened; `deny` → return the note. Returns (verb, outcome text) —
    the outcome text rides the resumed run's continuation reason."""
    verb, note = parse_answer(text)
    project = str(rec.get("project") or "default")
    specs = [str(s) for s in (rec.get("escalate") or [])]
    if verb == "deny":
        return "deny", (f"The orchestrator DENIED installing {', '.join(specs)}"
                        + (f": {note}" if note else "") + ". Proceed without it or finish with a stated gap.")
    if verb != "allow":
        return "", (f"The orchestrator replied: {text.strip()[:300]}. The install was neither allowed "
                    "nor denied; proceed without it unless the reply says otherwise.")
    add_grants(project, specs)
    req = {s: list((rec.get("request") or {}).get(s) or []) for s in SOURCES}
    req["browsers"] = list((rec.get("request") or {}).get("browsers") or [])
    req["need"] = str(rec.get("why") or "")
    m = load_manifest(project)
    v = evaluate(req, grants=m.get("grants") or [])
    br = build_layer(project, v, reason="orchestrator allowed: " + req["need"][:200])
    if br.ok:
        return "allow", (f"The orchestrator ALLOWED it{(': ' + note) if note else ''}. Environment updated: "
                         f"installed {', '.join(br.added) or 'nothing new'} — the run continues on image "
                         f"{br.image}. Do not request these again.")
    return "allow", (f"The orchestrator ALLOWED it but the build FAILED: {br.detail[-600:]}. "
                     "Adjust the request or proceed without it.")


def status(project: str) -> Dict[str, Any]:
    m = load_manifest(project)
    d = layer_dir(project)
    return {"project": project, "image": m.get("image") or "", "base": m.get("base") or "",
            "layer": int(m.get("layer") or 0),
            **{s: list(m.get(s) or []) for s in SOURCES},
            "browsers": list(m.get("browsers") or []),
            "grants": list(m.get("grants") or []),
            "dockerfile": str(d / "Dockerfile") if (d / "Dockerfile").is_file() else "",
            "updated_at": m.get("updated_at") or ""}
