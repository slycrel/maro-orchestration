"""Project/item management, path utilities, and run record I/O for Maro orchestration.

Extracted from orch.py — no dependency on orch.py (safe to import from orch_bridges.py and orch.py).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Generator, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_TODO = " "
STATE_DOING = "~"
STATE_DONE = "x"
STATE_BLOCKED = "!"
VALID_STATES = {STATE_TODO, STATE_DOING, STATE_DONE, STATE_BLOCKED}
RUN_OUTCOMES = {"done", "blocked", "retry"}
WORKER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
X_CAPTURE_AUTH_MARKERS = [
    "this page isn't working",
    "this page isn\u2019t working",
    "captcha",
    "consent",
    "login",
    "sign in",
]
X_CAPTURE_RATE_LIMIT_MARKERS = ["429", "rate limit", "too many requests", "quota exceeded"]

ITEM_RE = re.compile(r"^(?P<indent>\s*)-\s*\[(?P<state>[ xX~!])\]\s*(?P<text>.+?)\s*$")

# Explicit, execution-scoped storage routing.  Unlike MARO_MEMORY_DIR this is
# isolated between concurrent threads/tasks and cannot leak into subprocesses
# or unrelated imports.  The environment variable remains a boundary-level
# configuration option for CLI/tests; internal callers that need a temporary
# target use memory_dir_context().
_MEMORY_DIR_CONTEXT: ContextVar[Optional[Path]] = ContextVar(
    "maro_memory_dir_context", default=None
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NextItem:
    index: int
    state: str
    text: str
    line: str
    indent: int = 0


@dataclass
class ProjectStatus:
    slug: str
    priority: int
    todo: int
    doing: int
    blocked: int
    done: int
    next_item: Optional[NextItem]


@dataclass
class RunRecord:
    run_id: str
    project: str
    index: int
    text: str
    status: str
    source: str
    worker: str
    started_at: str
    updated_at: str
    attempt: int = 1
    finished_at: Optional[str] = None
    note: Optional[str] = None
    artifact_path: Optional[str] = None


@dataclass
class ExecutionResult:
    status: str
    note: Optional[str] = None
    artifact_path: Optional[str] = None


@dataclass
class ValidationResult:
    status: str
    passed: bool
    note: Optional[str] = None


@dataclass
class TickResult:
    run: RunRecord
    execution: ExecutionResult
    validation: ValidationResult


@dataclass
class PlanResult:
    project: str
    goal: str
    steps: List[str]
    item_indices: List[int]


ExecutionBridge = Callable[[RunRecord], ExecutionResult]
ValidationBridge = Callable[[RunRecord, ExecutionResult], ValidationResult]


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------

def ws_root() -> Path:
    for var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        val = os.environ.get(var)
        if val:
            return Path(val).expanduser().resolve()
    # parents[3] is designed for the prototype layout:
    #   <ws>/prototypes/maro-orchestration/src/orch_items.py
    # Guard against shallow checkouts (container/CI) where parents[3] hits / or near-root.
    here = Path(__file__).resolve()
    try:
        candidate = here.parents[3]
    except IndexError:
        candidate = Path("/")
    _unsafe = {Path("/"), Path("/tmp"), Path("/var"), Path("/usr"), Path("/opt")}
    if candidate in _unsafe:
        # Fall back to two levels up (repo root) — better than writing to /
        return here.parents[1]
    return candidate


def orch_root() -> Path:
    """Resolve the maro-orchestration root directory.

    Resolution order:
      1. MARO_ORCH_ROOT env var — explicit override for containers / CI
      2. Traditional prototype path (ws_root/prototypes/maro-orchestration) if it exists
      3. Mainline repo root (src/agent_loop.py present) — only when NO workspace
         env var is set (preserves test isolation when OPENCLAW_WORKSPACE is pinned)
      4. Traditional path regardless (original fallback)
    """
    override = os.environ.get("MARO_ORCH_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    traditional = ws_root() / "prototypes" / "maro-orchestration"
    if traditional.exists():
        return traditional

    # Only fall through to repo-root detection when no explicit workspace is pinned.
    # If a workspace env var IS set, the caller expects isolation to that workspace
    # (e.g. tests use OPENCLAW_WORKSPACE=tmp_path) — don't escape to the real repo.
    _ws_pinned = any(os.environ.get(v) for v in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"))
    if not _ws_pinned:
        here = Path(__file__).resolve()
        repo_root = here.parents[1]  # one up from src/
        if (repo_root / "src" / "agent_loop.py").exists():
            return repo_root

    return traditional


def repo_root() -> Path:
    """Mainline repo root — the directory holding this source checkout.

    Use for CODE/repo concerns: git cwd for `git log` scans, repo-tracked
    files like BACKLOG.md. orch_root() is wrong for these by construction —
    it resolves the runtime orch layout and follows workspace pins, so it
    only coincides with the checkout when nothing is pinned (census item
    5b, 2026-08-06: git-cwd consumers were correct by coincidence).
    """
    return Path(__file__).resolve().parents[1]


def _orch_root_pinned() -> bool:
    """True when data roots should ride orch_root() instead of the workspace.

    History: BACKLOG #-1 (2026-07-03) unified the canonical pin —
    `MARO_WORKSPACE=x` means "the workspace IS x", all data roots resolve
    through config. The legacy vars (OPENCLAW_WORKSPACE / WORKSPACE_ROOT)
    kept the prototype layout, so pinning one — even to the real
    workspace — silently routed memory/projects/output into
    <ws>/prototypes/maro-orchestration/ (shadow-store hijack, 2026-08-06
    census item 5). config.workspace_root() has always treated all three
    vars identically, so since 2026-08-06 every workspace pin means "the
    workspace IS x".

    The one remaining orch-layout case: MARO_ORCH_ROOT set with NO
    workspace var — an explicit container/CI override where data
    deliberately rides next to the pinned orch root (build-loop.sh's
    no-arg repo-local mode relies on this).
    """
    if any(os.environ.get(v) for v in (
        "MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT",
    )):
        return False
    return bool(os.environ.get("MARO_ORCH_ROOT"))


def data_root() -> Path:
    """Anchor for workspace-level data that isn't memory/projects/output.

    Same rule those three follow: config.workspace_root() for any workspace
    pin (or the unpinned default), orch_root() when MARO_ORCH_ROOT is the
    only pin (repo-local / container mode). Consumers: checkpoint.py
    (non-run-dir checkpoints), hooks.py (registry). Added after the
    2026-08-06 adversarial review — the first cut of the 5b migration
    routed those two through config.workspace_root() directly, so
    repo-local mode leaked its checkpoints and hook registry into the
    production workspace.
    """
    if _orch_root_pinned():
        return orch_root()
    from config import workspace_root
    return workspace_root()


def memory_dir() -> Path:
    """Canonical memory directory — used by memory.py, observe.py, gc_memory.py, router.py.

    Resolution order:
      1. memory_dir_context() (explicit in-process storage context)
      2. $MARO_MEMORY_DIR     (process-level override — tests use this)
      3. config.memory_dir() (honors any workspace pin — MARO_WORKSPACE/
         OPENCLAW_WORKSPACE/WORKSPACE_ROOT — defaults to
         ~/.maro/workspace/memory) — unless MARO_ORCH_ROOT is the only pin
      4. orch_root()/memory  (MARO_ORCH_ROOT-only pin: containers/CI and
         build-loop.sh's repo-local mode — and import-failure fallback)
      5. cwd/memory          (last resort)

    IMPORTANT: This must resolve to the SAME directory as config.memory_dir()
    so that captain's log, outcomes, lessons, and skills all live together.
    Previous bug: orch_items.memory_dir() → repo/memory while config.memory_dir()
    → ~/.maro/workspace/memory, causing captain's log to live in a different
    location from the rest of the learning data.

    Always creates the directory.  Never raises.
    """
    scoped = _MEMORY_DIR_CONTEXT.get()
    if scoped is not None:
        scoped.mkdir(parents=True, exist_ok=True)
        return scoped

    override = os.environ.get("MARO_MEMORY_DIR")
    if override:
        p = Path(override).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Align with config.py — the canonical workspace path. Honors any
    # workspace pin ($WS/memory) and defaults to ~/.maro/workspace/memory,
    # exactly like config.memory_dir().
    if not _orch_root_pinned():
        try:
            from config import memory_dir as _cfg_memory_dir
            return _cfg_memory_dir()
        except Exception:
            pass  # fall through to orch_root layout / cwd fallback

    # MARO_ORCH_ROOT is the only pin — data rides the orch root
    p = orch_root() / "memory"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        fallback = Path.cwd() / "memory"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@contextmanager
def memory_dir_context(path: Path) -> Generator[Path, None, None]:
    """Route memory helpers to ``path`` for this execution context only.

    ContextVar scoping makes nested use restore correctly and keeps concurrent
    imports targeting different workspaces isolated without mutating process
    environment.  Callers pass the memory directory itself, not a workspace.
    """
    resolved = Path(path).expanduser().resolve()
    token = _MEMORY_DIR_CONTEXT.set(resolved)
    try:
        yield resolved
    finally:
        _MEMORY_DIR_CONTEXT.reset(token)


def projects_root() -> Path:
    """Canonical projects directory — aligns with config.projects_dir().

    Resolution order:
      1. config.projects_dir() (honors any workspace pin; defaults to
         ~/.maro/workspace/projects) unless MARO_ORCH_ROOT is the only pin
      2. orch_root()/projects when MARO_ORCH_ROOT is the only pin
    """
    if not _orch_root_pinned():
        from config import projects_dir
        return projects_dir()
    p = orch_root() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def output_root() -> Path:
    """Canonical output directory — aligns with config.output_dir().

    Resolution order:
      1. config.output_dir() (honors any workspace pin; defaults to
         ~/.maro/workspace/output) unless MARO_ORCH_ROOT is the only pin
      2. orch_root()/output when MARO_ORCH_ROOT is the only pin
    """
    if not _orch_root_pinned():
        from config import output_dir
        return output_dir()
    p = orch_root() / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


def relative_display_path(path: Path) -> str:
    """Return a short relative path for display/logging purposes.

    Tries orch_root() first (repo-relative), then workspace_root(),
    then falls back to the absolute path. Never raises.
    """
    p = Path(path).resolve()
    try:
        return str(p.relative_to(orch_root()))
    except ValueError:
        pass
    try:
        from config import workspace_root
        return "~workspace/" + str(p.relative_to(workspace_root()))
    except (ValueError, Exception):
        pass
    return str(p)


def resolve_artifact_path(rel) -> Path:
    """Resolve a stored artifact_path back to an absolute Path.

    Inverse of relative_display_path(): handles the orch_root-relative form,
    the "~workspace/..." form, and absolute paths. Before BACKLOG #-1
    (2026-07-03) consumers joined orch_root()/artifact_path directly, which
    silently broke whenever runs_root() lived outside orch_root — the
    production default (~/.maro/workspace/output vs repo orch_root).
    """
    s = str(rel or "")
    if s.startswith("~workspace/"):
        from config import workspace_root
        return workspace_root() / s[len("~workspace/"):]
    p = Path(s)
    if p.is_absolute():
        return p
    return orch_root() / p


def runs_root() -> Path:
    p = output_root() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def workers_root() -> Path:
    return orch_root() / "workers"


def operator_status_path() -> Path:
    return output_root() / "operator-status.json"


def project_dir(slug: str) -> Path:
    return projects_root() / slug


def next_path(slug: str) -> Path:
    return project_dir(slug) / "NEXT.md"


def decisions_path(slug: str) -> Path:
    return project_dir(slug) / "DECISIONS.md"


def risks_path(slug: str) -> Path:
    return project_dir(slug) / "RISKS.md"


def provenance_path(slug: str) -> Path:
    return project_dir(slug) / "PROVENANCE.md"


def priority_path(slug: str) -> Path:
    return project_dir(slug) / "PRIORITY"


def now_utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_run_id() -> str:
    return f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"


def list_projects() -> List[str]:
    root = projects_root()
    if not root.exists():
        return []
    slugs = []
    for p in root.iterdir():
        if p.is_dir() and next_path(p.name).exists():
            slugs.append(p.name)
    return sorted(slugs)


def project_priority(slug: str) -> int:
    p = priority_path(slug)
    if not p.exists():
        return 0
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Run record I/O
# ---------------------------------------------------------------------------

def _run_record_path(run_id: str) -> Path:
    return runs_root() / f"{run_id}.json"


def write_run_record(record: RunRecord) -> Path:
    path = _run_record_path(record.run_id)
    path.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    return path


def load_run_record(run_id: str) -> RunRecord:
    data = json.loads(_run_record_path(run_id).read_text(encoding="utf-8"))
    return RunRecord(**data)


def validation_summary_path(run: RunRecord) -> Optional[Path]:
    if not run.artifact_path:
        return None
    path = resolve_artifact_path(run.artifact_path) / "validation-summary.json"
    return path if path.exists() else None


def load_validation_summary(run_id: str) -> Optional[dict]:
    run = load_run_record(run_id)
    path = validation_summary_path(run)
    if not path:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_run_records() -> List[RunRecord]:
    out: List[RunRecord] = []
    root = runs_root()
    if not root.exists():
        return out
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if "attempt" not in data:
                data["attempt"] = 1
            data.setdefault("artifact_path", None)
            try:
                data["attempt"] = int(data["attempt"])
            except (TypeError, ValueError):
                data["attempt"] = 1
            out.append(RunRecord(**data))
        except Exception:
            continue
    return out


def _next_attempt(project: str, item_index: int) -> int:
    attempts = [r.attempt for r in _load_run_records() if r.project == project and r.index == item_index]
    return max(attempts, default=0) + 1


def _run_artifact_root(run: RunRecord) -> Path:
    if run.artifact_path:
        path = resolve_artifact_path(run.artifact_path)
    else:
        path = runs_root() / run.run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Item management
# ---------------------------------------------------------------------------

def parse_next(slug: str) -> Tuple[List[str], List[NextItem]]:
    p = next_path(slug)
    if not p.exists():
        raise ValueError(f"project {slug} has no NEXT.md")
    lines = p.read_text(encoding="utf-8").splitlines()
    items: List[NextItem] = []
    for i, line in enumerate(lines):
        m = ITEM_RE.match(line)
        if not m:
            continue
        state = m.group("state")
        if state == "X":
            state = STATE_DONE
        items.append(
            NextItem(
                index=i,
                state=state,
                text=m.group("text").strip(),
                line=line,
                indent=len(m.group("indent")),
            )
        )
    return lines, items


def write_next_lines(slug: str, lines: List[str]) -> None:
    from file_lock import atomic_write
    atomic_write(next_path(slug), "\n".join(lines).rstrip() + "\n")


def _doing_pids_path(slug: str) -> Path:
    """Sidecar recording which PID set each [~] DOING item (crash forensics).

    Lives next to NEXT.md rather than inside it — the [state] line format has
    many parsers; a sidecar changes none of them. Entries are written when an
    item flips to DOING and dropped on any other transition, so a surviving
    entry with a dead PID marks a stranded item.
    """
    return next_path(slug).parent / ".doing_pids.json"


def _read_doing_pids(slug: str) -> dict:
    try:
        return json.loads(_doing_pids_path(slug).read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_item(slug: str, item_index: int, new_state: str) -> None:
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid new state: {new_state}")
    # Parse + rewrite under NEXT.md's lock: two concurrent markers (e.g.
    # heartbeat backlog-drain flipping DOING while a finishing run flips
    # DONE) were a lost-update race. locked_write is reentrant, so
    # write_next_lines nesting inside is safe.
    from file_lock import locked_write
    with locked_write(next_path(slug)):
        lines, items = parse_next(slug)
        item = next((it for it in items if it.index == item_index), None)
        if item is None:
            raise ValueError(f"item_index {item_index} not found in NEXT.md for {slug}")
        lines[item.index] = re.sub(r"\[(.)\]", f"[{new_state}]", lines[item.index], count=1)
        write_next_lines(slug, lines)
        # PID stamp for DOING (same lock — the stamp and the state flip are
        # one transition). Best-effort: a failed stamp must not fail the mark.
        try:
            pids = _read_doing_pids(slug)
            key = str(item_index)
            if new_state == STATE_DOING:
                pids[key] = {"pid": os.getpid(),
                             "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            else:
                pids.pop(key, None)
            from file_lock import atomic_write
            atomic_write(_doing_pids_path(slug), json.dumps(pids, indent=2))
        except Exception:
            pass


def stranded_doing_items(slug: str) -> List[NextItem]:
    """DOING items whose recorded PID is dead — or that predate PID stamping.

    Both shapes are leaked locks: after 2026-07-09 every DOING flip stamps a
    PID under the same lock, so a missing entry means the stamp era hadn't
    started (or the sidecar was lost) and the item has no live owner either
    way. Callers revert these to TODO (mirroring the deliberate refused_busy
    revert in heartbeat's backlog drain).
    """
    _lines, items = parse_next(slug)
    doing = [it for it in items if it.state == STATE_DOING]
    if not doing:
        return []
    pids = _read_doing_pids(slug)

    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            return True

    stranded = []
    for it in doing:
        rec = pids.get(str(it.index))
        if rec is None or not _alive(int(rec.get("pid", 0) or 0)):
            stranded.append(it)
    return stranded


def append_next_items(slug: str, items: List[str]) -> List[int]:
    if not items:
        return []
    p = next_path(slug)
    from file_lock import locked_write
    with locked_write(p):
        if not p.exists():
            # Defensive: create minimal NEXT.md rather than crashing on FileNotFoundError.
            # Normally ensure_project() handles this; this guard covers partial-init cases.
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# NEXT — {slug}\n\n", encoding="utf-8")
        lines = p.read_text(encoding="utf-8").splitlines()
        start = len(lines)
        next_lines = [f"- [ ] {i}" for i in items]
        lines.extend(next_lines)
        write_next_lines(slug, lines)
    return list(range(start, start + len(next_lines)))


def get_item(slug: str, item_index: int) -> NextItem:
    _lines, items = parse_next(slug)
    item = next((it for it in items if it.index == item_index), None)
    if item is None:
        raise ValueError(f"item_index {item_index} not found in NEXT.md for {slug}")
    return item


def decompose_goal(goal: str, *, max_steps: int = 4) -> List[str]:
    if max_steps <= 0:
        raise ValueError("max_steps must be greater than zero")
    normalized = re.sub(r"\s+", " ", goal.strip())
    if not normalized:
        raise ValueError("goal cannot be empty")

    # Conservative heuristic decomposition, useful for now and deterministic for
    # tests and local automation.
    pieces = [part.strip() for part in re.split(r"[.;]|\b(?:and then|then)\b", normalized, flags=re.IGNORECASE)]
    pieces = [p for p in pieces if p and p.lower() != "and"]
    if len(pieces) == 1 and "," in normalized:
        if "," in normalized:
            pieces = [p.strip() for p in normalized.split(",") if p.strip()]
    if len(pieces) == 1 and len(normalized.split()) > 12:
        words = normalized.split()
        chunk = max(1, len(words) // max_steps + 1)
        pieces = [" ".join(words[i : i + chunk]).strip() for i in range(0, len(words), chunk)]

    cleaned_steps: List[str] = []
    for piece in pieces:
        step = piece.strip().strip(" -")
        if not step:
            continue
        if cleaned_steps and cleaned_steps[-1].lower() == step.lower():
            continue
        cleaned_steps.append(step)

    cleaned = [p for p in cleaned_steps if p]
    if not cleaned:
        raise ValueError(f"could not decompose goal: {goal}")
    return cleaned[:max_steps]


def plan_project(slug: str, goal: str, *, max_steps: int = 4) -> PlanResult:
    if not project_dir(slug).exists():
        raise ValueError(f"project {slug} does not exist")
    steps = decompose_goal(goal, max_steps=max_steps)
    item_indices = append_next_items(slug, steps)
    append_decision(slug, [f"Planned work from goal: {goal}", *[f"- step: {s}" for s in steps]])
    return PlanResult(project=slug, goal=goal, steps=steps, item_indices=item_indices)


def mark_first_todo_done(slug: str) -> Optional[NextItem]:
    item = select_next_item(slug)
    if not item:
        return None
    mark_item(slug, item.index, STATE_DONE)
    return item


def select_next_item(slug: str) -> Optional[NextItem]:
    _lines, items = parse_next(slug)
    for it in items:
        if it.state == STATE_TODO:
            return it
    return None


def item_counts(slug: str) -> dict:
    _lines, items = parse_next(slug)
    counts = {"todo": 0, "doing": 0, "blocked": 0, "done": 0}
    for item in items:
        if item.state == STATE_TODO:
            counts["todo"] += 1
        elif item.state == STATE_DOING:
            counts["doing"] += 1
        elif item.state == STATE_BLOCKED:
            counts["blocked"] += 1
        elif item.state == STATE_DONE:
            counts["done"] += 1
    return counts


def project_status(slug: str) -> ProjectStatus:
    counts = item_counts(slug)
    return ProjectStatus(
        slug=slug,
        priority=project_priority(slug),
        todo=counts["todo"],
        doing=counts["doing"],
        blocked=counts["blocked"],
        done=counts["done"],
        next_item=select_next_item(slug),
    )


def select_global_next() -> Optional[Tuple[str, NextItem]]:
    candidates: List[Tuple[int, float, str]] = []
    for slug in list_projects():
        p = next_path(slug)
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        # Skip failed or paused projects — they don't participate in backlog drain
        try:
            from sheriff import project_lifecycle_state
            if project_lifecycle_state(slug) in ("failed", "paused"):
                continue
        except Exception:
            pass
        candidates.append((project_priority(slug), mtime, slug))

    # Sort: highest priority first, then OLDEST mtime first (prevent starvation
    # of older equal-priority projects — the most neglected project gets picked).
    for _priority, _mtime, slug in sorted(candidates, key=lambda row: (row[0], -row[1]), reverse=True):
        it = select_next_item(slug)
        if it:
            return slug, it
    return None


def list_blocked_projects() -> List[ProjectStatus]:
    out: List[ProjectStatus] = []
    for slug in list_projects():
        try:
            status = project_status(slug)
        except ValueError:
            continue  # Skip projects with missing NEXT.md
        if status.blocked > 0:
            out.append(status)
    return sorted(out, key=lambda s: (s.priority, s.blocked, s.slug), reverse=True)


def append_section_lines(path: Path, heading: str, lines: Iterable[str],
                         dedupe_token: str = "") -> bool:
    # Multi-line block append > PIPE_BUF can interleave with concurrent
    # loops on the same project — hold the file lock for seed + append.
    # dedupe_token: when set, the presence-check happens INSIDE the lock so
    # check-then-append is one transaction (adversarial review 2026-08-10:
    # a caller-side pre-check alone is a TOCTOU — two finalizers can both
    # observe absence, then serialize only their appends).
    from file_lock import locked_write
    stamp = now_utc_iso()
    payload = ["", f"## {stamp}", *[f"- {ln}" for ln in lines]]
    with locked_write(path):
        if (dedupe_token and path.exists()
                and dedupe_token in path.read_text(encoding="utf-8")):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(heading + "\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(payload) + "\n")
    return True


def append_decision(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(decisions_path(slug), "# DECISIONS", lines)


def append_risk(slug: str, lines: Iterable[str],
                dedupe_token: str = "") -> bool:
    return append_section_lines(risks_path(slug), "# RISKS", lines,
                                dedupe_token=dedupe_token)


def append_provenance(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(provenance_path(slug), "# PROVENANCE", lines)


def ensure_project(slug: str, mission: str, priority: int = 0) -> Path:
    from file_lock import atomic_write
    pdir = project_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)
    if not next_path(slug).exists():
        atomic_write(
            next_path(slug),
            (
                f"# NEXT — {slug}\n\n"
                "Mission:\n\n"
                f"> {mission}\n\n"
                "## Checklist\n\n"
                "- [ ] Define success criteria\n"
                "- [ ] Create first-pass plan\n"
                "- [ ] Execute next leaf task\n"
            ),
        )
    # RISKS.md / PROVENANCE.md are NOT pre-created: append_risk /
    # append_provenance lazy-create them with their heading on first real
    # write. A "(fill in)" stub minted here outlives any run that has
    # nothing to record — and because it's file-modified-during-the-run,
    # curation served the stub as a run deliverable (8b8671bd 2026-08-06).
    if not decisions_path(slug).exists():
        atomic_write(decisions_path(slug), "# DECISIONS\n\n")
        append_decision(slug, ["Project created.", f"Mission: {mission}"])
    atomic_write(priority_path(slug), f"{priority}\n")
    return pdir
