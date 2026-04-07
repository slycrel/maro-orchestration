"""Project/item management, path utilities, and run record I/O for Poe orchestration.

Extracted from orch.py — no dependency on orch.py (safe to import from orch_bridges.py and orch.py).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Tuple

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
    for var in ("POE_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        val = os.environ.get(var)
        if val:
            return Path(val).expanduser().resolve()
    # parents[3] is designed for the prototype layout:
    #   <ws>/prototypes/poe-orchestration/src/orch_items.py
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
    """Resolve the poe-orchestration root directory.

    Resolution order:
      1. POE_ORCH_ROOT env var — explicit override for containers / CI
      2. Traditional prototype path (ws_root/prototypes/poe-orchestration) if it exists
      3. Mainline repo root (src/agent_loop.py present) — only when NO workspace
         env var is set (preserves test isolation when OPENCLAW_WORKSPACE is pinned)
      4. Traditional path regardless (original fallback)
    """
    override = os.environ.get("POE_ORCH_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    traditional = ws_root() / "prototypes" / "poe-orchestration"
    if traditional.exists():
        return traditional

    # Only fall through to repo-root detection when no explicit workspace is pinned.
    # If a workspace env var IS set, the caller expects isolation to that workspace
    # (e.g. tests use OPENCLAW_WORKSPACE=tmp_path) — don't escape to the real repo.
    _ws_pinned = any(os.environ.get(v) for v in ("POE_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"))
    if not _ws_pinned:
        here = Path(__file__).resolve()
        repo_root = here.parents[1]  # one up from src/
        if (repo_root / "src" / "agent_loop.py").exists():
            return repo_root

    return traditional


def memory_dir() -> Path:
    """Canonical memory directory — used by memory.py, observe.py, gc_memory.py, router.py.

    Resolution order:
      1. orch_root()/memory  (standard layout)
      2. $POE_MEMORY_DIR     (explicit override)
      3. cwd/memory          (fallback for tests / portable use)

    Always creates the directory.  Never raises.
    """
    override = os.environ.get("POE_MEMORY_DIR")
    if override:
        p = Path(override).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = orch_root() / "memory"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        fallback = Path.cwd() / "memory"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def projects_root() -> Path:
    return orch_root() / "projects"


def output_root() -> Path:
    p = orch_root() / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


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
    path = orch_root() / run.artifact_path / "validation-summary.json"
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
    root = orch_root()
    if run.artifact_path:
        path = root / run.artifact_path
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
    p = next_path(slug)
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def mark_item(slug: str, item_index: int, new_state: str) -> None:
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid new state: {new_state}")
    lines, items = parse_next(slug)
    item = next((it for it in items if it.index == item_index), None)
    if item is None:
        raise ValueError(f"item_index {item_index} not found in NEXT.md for {slug}")
    lines[item.index] = re.sub(r"\[(.)\]", f"[{new_state}]", lines[item.index], count=1)
    write_next_lines(slug, lines)


def append_next_items(slug: str, items: List[str]) -> List[int]:
    if not items:
        return []
    p = next_path(slug)
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

    for _priority, _mtime, slug in sorted(candidates, key=lambda row: (row[0], row[1]), reverse=True):
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


def append_section_lines(path: Path, heading: str, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(heading + "\n\n", encoding="utf-8")
    stamp = now_utc_iso()
    payload = ["", f"## {stamp}", *[f"- {ln}" for ln in lines]]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(payload) + "\n")


def append_decision(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(decisions_path(slug), "# DECISIONS", lines)


def append_risk(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(risks_path(slug), "# RISKS", lines)


def append_provenance(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(provenance_path(slug), "# PROVENANCE", lines)


def ensure_project(slug: str, mission: str, priority: int = 0) -> Path:
    pdir = project_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)
    if not next_path(slug).exists():
        next_path(slug).write_text(
            (
                f"# NEXT — {slug}\n\n"
                "Mission:\n\n"
                f"> {mission}\n\n"
                "## Checklist\n\n"
                "- [ ] Define success criteria\n"
                "- [ ] Create first-pass plan\n"
                "- [ ] Execute next leaf task\n"
            ),
            encoding="utf-8",
        )
    if not risks_path(slug).exists():
        risks_path(slug).write_text("# RISKS\n\n## Risks / Unknowns\n\n- (fill in)\n", encoding="utf-8")
    if not decisions_path(slug).exists():
        decisions_path(slug).write_text("# DECISIONS\n\n", encoding="utf-8")
        append_decision(slug, ["Project created.", f"Mission: {mission}"])
    if not provenance_path(slug).exists():
        provenance_path(slug).write_text("# PROVENANCE\n\n- (links to key artifacts, datasets, runs)\n", encoding="utf-8")
    priority_path(slug).write_text(f"{priority}\n", encoding="utf-8")
    return pdir
