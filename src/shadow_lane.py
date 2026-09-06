"""Shadow lane — the lane-honesty standing test (champion-challenger).

See docs/SHADOW_LANE_DESIGN.md for the full contract. Short version: every
eligible completed primary run can get ONE shadow — a strictly isolated
re-run of the same goal text, arm randomized (deterministically) between
`star` (headless subprocess carrying the star SKILL.md as a system prompt)
and `plain` (bare goal, no orchestration teaching). The shadow fires from a
post-run sweep, never inside the primary run's process, and never touches
any learning path (outcomes, lessons, skills, evolver, knowledge) — the
challenger is a bare headless subprocess, not a maro run: no handle(), no
run dir of its own, no record_outcome.

Isolation is by construction, not by stamp (recon 2026-08-14): the only
writes this module makes live under `<run-dir>/shadow/` (RESULT.md,
meta.json, scratch/, SKIPPED, ERROR, ledger-row.json fallback) plus
`memory/shadow_ledger.jsonl` and its sweep lock sentinel. No learning
module (memory.py, memory_ledger.py, evolver.py, skills.py) may import this
module, and this module must never import any of them — enforced by
tests/test_shadow_lane.py's isolation pin, not just convention. (The
generic storage lanes — workspace export/import and the sqlite backend
migration — carry `shadow_ledger.jsonl` like any other memory file; that
is data transport, not learning ingestion, and is deliberately in-bounds.)

The Go track (2026-09-06): a third challenger, `go` — the Go successor
engine (`maro-go now|agenda`) run against its own persistent workspace
(`shadow.go.workspace`, so its landscape/memory accrue across shadows) in
a scratch work dir with every mutating/network tool denied by tool policy
(structural containment, no preamble). It is its OWN track: own switch
(`shadow.go.enabled`), own daily cap, own claim dir (`<run-dir>/shadow-go/`)
so it never competes with the star|plain pick for a run's one shadow slot,
own ledger rows (`arm: "go"`). Both lanes' primaries are eligible on the
basic checks alone — the tool policy is the side-effect guard (Jeremy
2026-09-06: "widen the Go track's AGENDA eligibility to the tool-policy
containment too; if we're going to shadow, let's do it right"). A
build-shaped goal therefore runs in Go with no write tools and fails
honestly; its row carries `primary_goal_shape` (worker type + action
tier) so adjudication can partition those pairs. Same isolation
invariant: the Go engine never reads or writes this workspace's learning
paths.

CLI (dev tool, like maro-introspect):
    PYTHONPATH=src python3 -m shadow_lane sweep [--limit N] [--verbose] [--dry-run]
    PYTHONPATH=src python3 -m shadow_lane status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jsonl_utils import loads_clean, store_text

log = logging.getLogger("maro.shadow_lane")

ARM_STAR = "star"
ARM_PLAIN = "plain"
ARM_GO = "go"

# The Go track's claim dir under the primary run dir — a SIBLING of
# `shadow/`, not inside it: the star|plain track claims a run by the mere
# existence of `shadow/`, so a Go claim under it would silently consume the
# run's star|plain slot (and vice versa). Two tracks, two claim dirs.
GO_DIR = "shadow-go"
# Tool policy for the Go challenger: every tool that can mutate or reach
# the network is denied by name (maro-go --deny-tools; read tools stay).
# This is the Go track's side-effect guard — structural (the engine
# refuses the tool), unlike the star|plain preamble (instruction-level).
GO_DENY_TOOLS = "Bash,Edit,MultiEdit,NotebookEdit,Write,WebFetch,WebSearch,Task,Agent"
_GO_RUN_LINE = re.compile(r"^run ([0-9a-f]{8}) attempt (\d+) · (\S+)", re.MULTILINE)

# Containment preamble, prepended to the goal for BOTH arms (symmetric, so
# it cancels out of any arm comparison). Born from the first live fire
# (2026-08-14, be7c618a/star): the textual READ-tier gate admits goals that
# ask for a written *report*, and the challenger wrote its report into the
# live project directory — overwriting the primary run's own deliverable —
# then found that deliverable first and verified-instead-of-redid, breaking
# arm independence. Instruction-level only (same best-effort class as the
# eligibility gate; NOT a sandbox — the honest framing in the design doc
# stands); bump the version when the text changes so the batch judge can
# partition rows.
CONTAINMENT_PREAMBLE_VERSION = 1
_CONTAINMENT_PREAMBLE = (
    "Constraints for this task (they override any conflicting instruction "
    "in the task text):\n"
    "1. You may READ any files the task requires, but create, modify, or "
    "delete files ONLY inside your current working directory. If the task "
    "asks for a written report or deliverable, write it in the current "
    "working directory.\n"
    "2. Produce your own independent answer from the primary sources. If "
    "you find an existing report, answer, or audit that already addresses "
    "this exact task, do not adopt, verify, amend, or supersede it — "
    "ignore it and do the work yourself from the underlying sources.\n"
    "\n"
    "The task:\n"
)


def challenger_prompt(goal: str) -> str:
    """The exact stdin prompt a challenger receives for `goal` (both arms)."""
    return _CONTAINMENT_PREAMBLE + goal

# Reason codes for eligible() — the FIRST failing check names the skip
# reason, so a re-scan of a stamped SKIPPED run never re-derives the reason.
REASON_NOT_DONE = "status!=done"
REASON_DRY_RUN = "dry_run"
REASON_NOT_ORGANIC = "measurement_class!=organic"
REASON_EMPTY_GOAL = "empty_goal"
REASON_NOT_RESEARCH = "worker_type!=research"
REASON_NOT_READ_TIER = "action_tier!=READ"

# Reasons that can NEVER change for a given run and therefore earn a
# permanent SKIPPED stamp (review 2026-08-14 r3: an explicit terminal set,
# not "everything except status"). NOT_DONE is mutable (a stuck run can be
# resumed to done); EMPTY_GOAL is conservatively treated as mutable too (a
# half-written metadata.json can be repaired in place) — both are simply
# rescanned while inside the lookback window.
_TERMINAL_REASONS = frozenset({
    REASON_DRY_RUN, REASON_NOT_ORGANIC, REASON_NOT_RESEARCH,
    REASON_NOT_READ_TIER,
})
REASON_LANE = "lane!=now|agenda"
# The Go track's terminal set is NARROWER than star|plain's: its gate never
# asks the goal's shape (the tool policy is the containment), so the
# research/read-tier reasons cannot be produced here. A SKIPPED stamp
# carrying a reason outside this set is a stamp from an older, narrower
# gate (2026-09-06: the first cron ticks stamped `worker_type!=research`
# before the widening landed) and must not outlive the gate that wrote
# it — `_stale_go_stamp` lets the sweep re-evaluate such a run.
_GO_TERMINAL_REASONS = frozenset({REASON_DRY_RUN, REASON_NOT_ORGANIC, REASON_LANE})


def eligible(goal: str, meta: dict) -> Tuple[bool, str]:
    """Eligibility gate for a shadow (design doc "Side-effect guard (hard)").

    Eligible iff ALL of: primary run status is "done"; not a dry run;
    measurement_class is "organic" (default when absent); goal is
    non-empty; the goal reads as research-shaped (workers.infer_worker_type);
    AND the goal classifies as read-only (constraint.classify_action_tier).
    The last two together (worker-type precedent + belt-and-braces action
    tier) are the hard side-effect guard — a shadow re-executes the goal
    text, so anything that could send/commit/spend/mutate is ineligible.

    Returns (ok, reason) where reason names the FIRST failing check.
    """
    meta = meta or {}
    if meta.get("status") != "done":
        return False, REASON_NOT_DONE
    if meta.get("dry_run"):
        return False, REASON_DRY_RUN
    if meta.get("measurement_class", "organic") != "organic":
        return False, REASON_NOT_ORGANIC
    if not (goal or "").strip():
        return False, REASON_EMPTY_GOAL

    from workers import infer_worker_type
    if infer_worker_type(goal) != "research":
        return False, REASON_NOT_RESEARCH

    import constraint
    if constraint.classify_action_tier(goal) != constraint.ACTION_TIER_READ:
        return False, REASON_NOT_READ_TIER

    return True, ""


def go_eligible(goal: str, meta: dict) -> Tuple[bool, str]:
    """Eligibility for the Go track: the basic checks (done, not dry,
    organic, non-empty) for BOTH lanes — the challenger's tool policy
    denies every mutating/network tool, so the goal text cannot act
    whatever its shape (structural containment; the read-tier gate is
    the star|plain track's, whose containment is a preamble). Any other
    lane is a terminal skip. The cost of the width: a build-shaped goal
    runs without write tools and fails honestly — `primary_goal_shape`
    on the row is what partitions those pairs at adjudication."""
    meta = meta or {}
    if meta.get("status") != "done":
        return False, REASON_NOT_DONE
    if meta.get("dry_run"):
        return False, REASON_DRY_RUN
    if meta.get("measurement_class", "organic") != "organic":
        return False, REASON_NOT_ORGANIC
    if not (goal or "").strip():
        return False, REASON_EMPTY_GOAL
    lane = str(meta.get("lane") or "")
    if lane in ("now", "agenda"):
        return True, ""
    return False, REASON_LANE


def goal_shape(goal: str) -> dict:
    """The primary goal's shape as the star|plain gate sees it (worker
    type + action tier) — recorded on every Go row so the adjudication
    can partition "could have succeeded without write tools" from "could
    not". Best-effort: a classifier failure yields None, never blocks."""
    out: Dict[str, Any] = {"worker_type": None, "action_tier": None}
    try:
        from workers import infer_worker_type
        out["worker_type"] = infer_worker_type(goal)
    except Exception:  # narrow-except: classification is annotation, not a gate
        pass
    try:
        import constraint
        out["action_tier"] = constraint.classify_action_tier(goal)
    except Exception:
        pass
    return out


def pick_arm(handle_id: str) -> str:
    """Deterministic star|plain arm pick from a stable hash of handle_id.

    Deterministic (not random) so re-sweeps and tests are stable: the same
    handle_id always picks the same arm. Uses sha256 hex-digest's first byte
    parity to spread evenly regardless of handle_id distribution (same
    technique as runs.nickname's adjective/noun spread, different hash so
    the two derived values don't correlate).
    """
    digest = hashlib.sha256((handle_id or "").encode("utf-8")).digest()
    return ARM_STAR if digest[0] % 2 == 0 else ARM_PLAIN


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
_VERSION_RE = re.compile(r"^version:\s*(\S+)\s*$", re.MULTILINE)


def _star_skill_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / ".claude" / "skills" / "star" / "SKILL.md"


def star_prompt() -> Tuple[str, dict]:
    """Read the star skill's SKILL.md, returning (text, {star_version, prompt_sha256}).

    Missing file or missing frontmatter version raises — the sweep catches
    and skips star-arm runs with a logged reason rather than silently
    running an unpinned instrument (design doc invariant 7:
    version-pinned instrument).
    """
    skill_path = _star_skill_path()
    text = skill_path.read_text(encoding="utf-8")

    fm_match = _FRONTMATTER_RE.match(text)
    version = None
    if fm_match:
        v_match = _VERSION_RE.search(fm_match.group(1))
        if v_match:
            version = v_match.group(1)
    if not version:
        raise ValueError(f"star skill frontmatter missing version field: {skill_path}")

    prompt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, {"star_version": version, "prompt_sha256": prompt_sha256}


def _handle_id_from_run_dir(run_dir: Path) -> str:
    try:
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        hid = meta.get("handle_id")
        if hid:
            return str(hid)
    except (OSError, ValueError):
        pass
    # Fallback: run-dir names are f"{handle_id}-{nickname}" and handle_id
    # itself never contains a dash (runs.py: str(uuid.uuid4())[:8]).
    return run_dir.name.split("-", 1)[0]


def _parse_cli_result(text: str) -> dict:
    """Best-effort parse of a `claude -p --output-format json` payload.

    Defensive by design (design doc: "missing fields -> None, never
    KeyError"): the merged stdout+stderr capture may carry warning noise
    around the single JSON result object, so this scans for the first
    JSON object that looks like a result payload rather than assuming
    the whole buffer is clean JSON.
    """
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Prefer the LAST result-shaped object, and a typed one over an
    # untyped one (review 2026-08-14, Skeptic): the stream merges stdout
    # and stderr, so a warning or tool-produced JSON blob carrying a
    # coincidental "result" key could precede the real CLI payload — the
    # genuine payload is the final result-typed object the CLI prints.
    decoder = json.JSONDecoder()
    best: dict = {}
    best_typed: dict = {}
    start = text.find("{")
    while start != -1:
        try:
            data, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(data, dict):
            if data.get("type") == "result":
                best_typed = data
            elif "result" in data:
                best = data
        start = text.find("{", start + consumed)
    return best_typed or best


def run_challenger(run_dir: Path, arm: str, goal: str, *, timeout: int,
                   star: Optional[Tuple[str, dict]] = None) -> dict:
    """Run one challenger (star|plain) for `goal` in a fresh scratch cwd.

    Writes <run_dir>/shadow/<arm>/RESULT.md + meta.json and returns the
    meta dict. The scratch cwd reservation is fail-closed
    (`exist_ok=False`, benchmark_isolation precedent): if it already
    exists a prior attempt owns it and this call raises rather than
    reusing or deleting it (data-retention rule).
    """
    if arm not in (ARM_STAR, ARM_PLAIN):
        raise ValueError(f"unknown shadow arm: {arm!r}")

    from llm import _CLAUDE_BIN, _run_subprocess_safe

    # Scratch lives INSIDE the isolation boundary (<run-dir>/shadow/<arm>/
    # scratch/) — review 2026-08-14, all three lenses: the original
    # output_root()/shadow-workspaces/ location was a third, untracked
    # write surface that violated the module's own confinement claim, and
    # its global <handle>-<arm> name meant a crashed attempt collided
    # forever. Per-run placement keeps the claim dir and the challenger's
    # working tree one inspectable unit; still fail-closed (a prior
    # attempt's scratch is evidence, never deleted or reused).
    scratch = run_dir / "shadow" / arm / "scratch"
    scratch.mkdir(parents=True, exist_ok=False)

    cmd = [_CLAUDE_BIN, "-p", "--output-format", "json", "--dangerously-skip-permissions"]
    arm_meta: Dict[str, Any] = {}
    if arm == ARM_STAR:
        # Single-read discipline (review 2026-08-14 r2, both lenses): the
        # sweep pre-reads the star prompt BEFORE claiming and hands it
        # down — a second read here would reintroduce the TOCTOU where a
        # transient skill problem lands AFTER the claim and terminally
        # consumes the run's shadow slot. `star=None` (standalone/test
        # callers) reads once here instead.
        star_text, star_meta = star if star is not None else star_prompt()
        cmd = cmd + ["--append-system-prompt", star_text]
        arm_meta.update(star_meta)

    # Black-box env scrub (review 2026-08-14 r1; widened r2): the wrapper
    # clones os.environ, so without this the challenger inherits every
    # MARO_* pointer to the real workspace. Round 2 caught the enumerated
    # list missing MARO_ORCH_ROOT and MARO_MEMORY_DIR (both real storage
    # roots) — an allowlist-of-denials is whack-a-mole, so scrub by
    # PREFIX: every MARO_*/OPENCLAW_* var present in this process, plus
    # WORKSPACE_ROOT, is unset in the child (None = unset-in-child, the
    # wrapper's documented contract; its own late re-adds are merged
    # BEFORE env_extra, so the None wins). A future MARO_ var can't leak
    # by omission. This is still NOT a sandbox — see the design doc's
    # honest side-effect-guard framing.
    # MARO_FETCH_CAPTURE_DIR is not always in os.environ but the wrapper
    # injects it conditionally — scrubbed explicitly. MARO_WORKER_RUN is NOT
    # scrubbed — force-set (review 2026-08-14 r3): the pre-push hook's git
    # guard keys on this marker (scripts/hooks/pre-push exits 0 when unset,
    # i.e. treats the process as human). The challenger is a spawned agent
    # and must stay marked as one, or it bypasses default-branch push
    # protection. An inert 1-bit marker is an accepted black-box impurity
    # in exchange for keeping the guard live. (Shared with the Go track.)
    _scrub_env = _scrub_env_extra()

    cli_version = None
    try:
        _v = subprocess.run([_CLAUDE_BIN, "--version"], capture_output=True,
                            text=True, timeout=10)
        cli_version = (_v.stdout or "").strip() or None
    except Exception:
        pass

    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    exit_status = "ok"
    result_text = ""
    parsed: dict = {}
    try:
        proc = _run_subprocess_safe(
            cmd, input=challenger_prompt(goal), timeout=timeout,
            cwd=str(scratch), env_extra=_scrub_env)
        stdout_text = proc.stdout or ""
        if proc.returncode != 0:
            exit_status = f"exit:{proc.returncode}"
        parsed = _parse_cli_result(stdout_text)
    except subprocess.TimeoutExpired as exc:
        exit_status = f"timeout:{getattr(exc, 'maro_kill_reason', 'unknown')}"
        parsed = _parse_cli_result(getattr(exc, "maro_partial_output", "") or "")
    except Exception as exc:  # narrow-except: unexpected subprocess failure is a
        # data point (challenger crashed), never a reason to blow up the sweep.
        log.warning("shadow challenger subprocess failed (arm=%s): %s", arm, exc)
        exit_status = f"error:{exc}"
    wall_seconds = time.monotonic() - t0

    if parsed:
        result_text = str(parsed.get("result") or "")
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    tokens_in = usage.get("input_tokens") if usage else None
    tokens_out = usage.get("output_tokens") if usage else None

    # Challenger model, defensively: the CLI result payload may carry
    # `model` or `modelUsage` keys depending on version; both absent → None
    # (interpretability field for the batch judge, review 2026-08-14 —
    # a changing CLI default model must not masquerade as an arm effect).
    model = parsed.get("model")
    if not model and isinstance(parsed.get("modelUsage"), dict):
        _mu = list(parsed["modelUsage"].keys())
        model = _mu[0] if len(_mu) == 1 else (_mu or None)

    meta = {
        "arm": arm,
        # `started_at`, not `ts` (review 2026-08-14, Skeptic): the ledger
        # row's `ts` is APPEND time — `**challenger_meta` was silently
        # overwriting it with this start time, which skewed the UTC daily
        # cap for challengers crossing midnight.
        "started_at": started_at.isoformat(),
        "wall_seconds": round(wall_seconds, 3),
        "exit_status": exit_status,
        "is_error": bool(parsed.get("is_error")) if parsed else None,
        "cost_usd": parsed.get("total_cost_usd"),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": model,
        "cli_version": cli_version,
        # Raw goal is the comparison identity; the wrapped stdin prompt is
        # reconstructable as challenger_prompt(goal) at this version.
        "containment_preamble_version": CONTAINMENT_PREAMBLE_VERSION,
        "goal": goal,
        # Challenger cmd WITHOUT the full star prompt text — a --append-system-prompt
        # arg is replaced with a length marker so meta.json stays small and never
        # duplicates the version-pinned prompt (star_version + prompt_sha256 below
        # are the pinned reference).
        "cmd": [
            (f"<star-prompt:{len(c)}chars>" if i > 0 and cmd[i - 1] == "--append-system-prompt"
             else c)
            for i, c in enumerate(cmd)
        ],
        "scratch_cwd": str(scratch),
        **arm_meta,
    }

    shadow_dir = run_dir / "shadow" / arm
    shadow_dir.mkdir(parents=True, exist_ok=True)
    (shadow_dir / "RESULT.md").write_text(result_text, encoding="utf-8")
    (shadow_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    return meta


def _scrub_env_extra() -> Dict[str, Any]:
    """The black-box env scrub every challenger gets (see run_challenger)."""
    import os as _os
    scrub: Dict[str, Any] = {
        k: None for k in _os.environ
        if k.startswith(("MARO_", "OPENCLAW_"))
    }
    scrub["WORKSPACE_ROOT"] = None
    scrub["MARO_FETCH_CAPTURE_DIR"] = None
    scrub["MARO_WORKER_RUN"] = "1"
    return scrub


def go_binary() -> Optional[Path]:
    """The Go engine binary from `shadow.go.binary` (a path, or a name
    resolved on PATH). None when it cannot be found — the sweep leaves the
    run unclaimed and counts an error, never claims on a missing engine."""
    import shutil
    from config import get
    raw = str(get("shadow.go.binary", "maro-go") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_file():
        return p
    found = shutil.which(raw)
    return Path(found) if found else None


def go_workspace() -> Path:
    """The Go engine's own persistent workspace for shadows: its landscape
    and lineage memory accrue across shadows the way the primary's do, and
    it is never this workspace (the engine must not read the champion's
    learning). Default `<workspace_root>/shadow-go`."""
    from config import get, workspace_root
    raw = str(get("shadow.go.workspace", "") or "").strip()
    return Path(raw).expanduser() if raw else workspace_root() / "shadow-go"


def _parse_go_summary(text: str) -> dict:
    """`maro-go runs show --json` prints the workspace announcement first,
    then the summary object: parse from the first line that opens an
    object; missing → {} (never KeyError)."""
    text = text or ""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            data, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(data, dict) and "handle" in data:
            return data
        start = text.find("{", start + consumed)
    return {}


GO_CONTEXT_DOCS = ("GOALS.md", "CONTEXT.md", "SIGNALS.md")
GO_CONTEXT_CAP = 4000  # per doc; the same breaker the planner uses


def _operator_context() -> Tuple[str, List[str]]:
    """The operator docs the Python planner injects, rendered the same way.

    Returns (text, docs): the concatenated "USER CONTEXT (<file>):" blocks
    and the names of the docs that were present. Mirrors planner.py's
    injection (workspace overlay over the repo template, clip() as the
    breaker) so the Go challenger reads what the champion read — the first
    live pair (37d0e041) failed on "what is the maro box?", a question the
    operator's CONTEXT.md answers. Empty when no doc exists.
    """
    from config import user_file
    from context_budget import clip

    blocks: List[str] = []
    docs: List[str] = []
    for name in GO_CONTEXT_DOCS:
        path = user_file(name)
        if path is None:
            continue
        try:
            text = clip(path.read_text(encoding="utf-8").strip(), GO_CONTEXT_CAP)
        except OSError:
            continue
        if text:
            blocks.append(f"USER CONTEXT ({name}):\n{text}")
            docs.append(name)
    return "\n\n".join(blocks), docs


def run_go_challenger(run_dir: Path, goal: str, *, lane: str, binary: Path,
                      timeout: int, model: Optional[str] = None,
                      workspace: Optional[Path] = None) -> dict:
    """Run the Go engine on `goal` (lane now|agenda) as the `go` arm.

    Writes <run_dir>/shadow-go/RESULT.md + meta.json and returns the meta
    dict. Two subprocess calls: the run itself (work dir = the scratch
    under the claim dir, every mutating/network tool denied), then
    `runs show --json <handle>` for the run's summary — calls, cost (the
    landscape judge included), outcome, landscape relation. Cost is None
    unless the engine says every call reported it (honest partial sums).
    """
    from llm import _run_subprocess_safe

    if lane not in ("now", "agenda"):
        raise ValueError(f"go challenger lane must be now|agenda: {lane!r}")
    model = model or "haiku"
    ws = workspace if workspace is not None else go_workspace()
    scratch = run_dir / GO_DIR / "scratch"
    scratch.mkdir(parents=True, exist_ok=False)

    # Operator context parity: the Go engine takes the operator docs as a
    # recorded input (--context <file>); the file is kept beside the result
    # so the pair can be read with what the challenger saw.
    context_text, context_docs = _operator_context()
    context_path = None
    context_sha = None
    if context_text:
        context_path = run_dir / GO_DIR / "context.md"
        context_path.write_text(context_text, encoding="utf-8")
        context_sha = hashlib.sha256(context_text.encode("utf-8")).hexdigest()

    env_extra = _scrub_env_extra()
    env_extra["MARO_GO_WORKSPACE"] = str(ws)
    cmd = [str(binary), lane, "--backend", "subprocess", "--model", model,
           "--work", str(scratch), "--deny-tools", GO_DENY_TOOLS]
    if context_path is not None:
        cmd += ["--context", str(context_path)]
    cmd.append(goal)

    binary_sha = None
    try:
        binary_sha = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    except OSError:
        pass

    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    exit_status = "ok"
    handle = None
    stdout_text = ""
    try:
        proc = _run_subprocess_safe(cmd, input="", timeout=timeout,
                                    liveness_timeout=0, cwd=str(scratch),
                                    env_extra=env_extra)
        stdout_text = proc.stdout or ""
        if proc.returncode != 0:
            exit_status = f"exit:{proc.returncode}"
    except subprocess.TimeoutExpired as exc:
        exit_status = f"timeout:{getattr(exc, 'maro_kill_reason', 'unknown')}"
        stdout_text = getattr(exc, "maro_partial_output", "") or ""
    except Exception as exc:  # narrow-except: a crashed engine is a data point
        log.warning("shadow go challenger subprocess failed: %s", exc)
        exit_status = f"error:{exc}"
    wall_seconds = time.monotonic() - t0

    m = _GO_RUN_LINE.search(stdout_text)
    if m:
        handle = m.group(1)
    elif exit_status == "ok":
        exit_status = "no_handle"

    summary: dict = {}
    if handle:
        try:
            proc = _run_subprocess_safe([str(binary), "runs", "show", "--json", handle],
                                        input="", timeout=60, liveness_timeout=0,
                                        cwd=str(scratch), env_extra=env_extra)
            summary = _parse_go_summary(proc.stdout or "")
        except Exception as exc:  # narrow-except: the summary is best-effort
            log.warning("shadow go challenger: runs show failed for %s: %s", handle, exc)

    usage = summary.get("usage") if isinstance(summary.get("usage"), dict) else {}
    ls = summary.get("landscape") if isinstance(summary.get("landscape"), dict) else None
    outcome = summary.get("outcome")
    reason = summary.get("reason") if isinstance(summary.get("reason"), str) else None
    needs_clarification = bool(reason and reason.startswith("needs clarification"))
    question = None
    if needs_clarification:
        question = reason.split(":", 1)[1].strip() if ":" in reason else ""
    models = sorted({str(c.get("model")) for c in summary.get("calls", [])
                     if isinstance(c, dict) and c.get("model")})
    meta = {
        "arm": ARM_GO,
        "started_at": started_at.isoformat(),
        "wall_seconds": round(wall_seconds, 3),
        "exit_status": exit_status,
        "is_error": (outcome != "delivered") if outcome else None,
        "cost_usd": usage.get("cost_usd") if usage.get("cost_reported") else None,
        "tokens_in": usage.get("input_tokens") if usage else None,
        "tokens_out": usage.get("output_tokens") if usage else None,
        "tokens_cached": usage.get("cache_read_tokens") if usage else None,
        "model": models[0] if len(models) == 1 else (models or model),
        "cli_version": None,
        # No preamble on this arm: containment is the work dir + the tool
        # policy (structural). Explicit None so the batch judge partitions.
        "containment_preamble_version": None,
        "containment": "work_dir+tool_policy",
        "tool_policy": {"deny": GO_DENY_TOOLS},
        "lane": lane,
        "goal": goal,
        "cmd": cmd,
        "scratch_cwd": str(scratch),
        "go_workspace": str(ws),
        "go_binary": str(binary),
        "go_binary_sha256": binary_sha,
        "go_handle": handle,
        "go_run_id": summary.get("run_id"),
        "go_outcome": outcome,
        "go_closure": summary.get("closure"),
        "go_calls": usage.get("calls") if usage else None,
        "go_reason": reason,
        # Recorded, not acted on: a clarification the shadow asked for
        # reaches nobody (there is no requester behind the arm). The row
        # says so, so the pair reads as "asked" rather than "failed".
        "go_needs_clarification": needs_clarification,
        "go_question": question,
        "context_docs": context_docs,
        "context_sha256": context_sha,
        "context_chars": len(context_text),
        "go_context": summary.get("context"),
        "go_landscape": ({"relation": ls.get("relation"), "chosen": ls.get("chosen"),
                          "rule": ls.get("rule")} if ls else None),
    }

    go_dir = run_dir / GO_DIR
    (go_dir / "RESULT.md").write_text(str(summary.get("result") or ""), encoding="utf-8")
    (go_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return meta


def _ledger_path():
    from config import workspace_root
    return workspace_root() / "memory" / "shadow_ledger.jsonl"


def _append_ledger_row(row: dict) -> None:
    from file_lock import locked_append
    locked_append(_ledger_path(), json.dumps(row, default=str))


def _iter_run_dirs_newest_first(lookback_hours: float) -> List[Path]:
    from runs import runs_root
    root = runs_root()
    if not root.is_dir():
        return []

    cutoff = None
    if lookback_hours and lookback_hours > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600

    dated: List[Tuple[float, Path]] = []
    for rd in root.iterdir():
        if not rd.is_dir():
            continue
        meta_path = rd / "metadata.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ended_at = meta.get("ended_at")
        ts = None
        if ended_at:
            try:
                ts = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = None
        if ts is None:
            ts = meta_path.stat().st_mtime
        if cutoff is not None and ts < cutoff:
            continue
        dated.append((ts, rd))

    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [rd for _, rd in dated]


def _today_ledger_count(arms: Optional[frozenset] = None) -> int:
    """Today's ledger rows, by `ts`, for the given arms (None = the
    star|plain track). Each track counts only its own rows: a Go shadow
    must not consume a star|plain cap slot, nor the reverse."""
    if arms is None:
        arms = frozenset({ARM_STAR, ARM_PLAIN})
    path = _ledger_path()
    if not path.is_file():
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ts = str(row.get("ts", ""))
            if ts.startswith(today) and row.get("arm") in arms:
                count += 1
    except OSError:
        pass
    return count


def _sample_rate_pass(handle_id: str, sample_rate: float) -> bool:
    """Deterministic sample-rate gate (not random): the same handle_id always
    resolves the same way, so a re-sweep can't flip a run in or out."""
    if sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    digest = hashlib.sha256(f"shadow-sample:{handle_id}".encode("utf-8")).digest()
    bucket = digest[0] / 255.0
    return bucket < sample_rate


def sweep(*, limit: int = 1, verbose: bool = False, dry_run: bool = False) -> dict:
    """Scan runs, fire at most `limit` shadow challengers, serially.

    Returns {"scanned", "skipped", "fired", "errors", "would_fire"} (the
    last only populated in dry-run mode).
    """
    from config import get

    summary: Dict[str, Any] = {"scanned": 0, "skipped": 0, "fired": 0, "errors": 0}
    if dry_run:
        summary["would_fire"] = []

    if not get("shadow.enabled", False):
        return summary

    # Cross-process serialization (review 2026-08-14, all three lenses):
    # the per-run mkdir claim alone left "serial + capped" an in-process
    # property — a CLI sweep and the heartbeat sweep could both read one
    # free cap slot and fire two challengers concurrently. One workspace-
    # level flock held for the WHOLE fire section makes serial a system
    # invariant and puts the cap read inside the same critical section.
    # Short timeout: a second sweep reports "locked" and exits rather than
    # queuing behind a 15-min challenger.
    from file_lock import locked_write, FileLockTimeout
    from config import workspace_root
    _sentinel = workspace_root() / "memory" / "shadow_sweep"
    try:
        with locked_write(_sentinel, timeout_s=2.0):
            return _sweep_locked(summary, limit=limit, verbose=verbose,
                                 dry_run=dry_run)
    except FileLockTimeout:
        summary["locked"] = True
        if verbose:
            log.info("shadow sweep: another sweep holds the lock — skipping")
        return summary


def _sweep_locked(summary: Dict[str, Any], *, limit: int, verbose: bool,
                  dry_run: bool) -> dict:
    from config import get

    sample_rate = float(get("shadow.sample_rate", 1.0))
    daily_cap = int(get("shadow.daily_cap", 4))
    timeout_seconds = int(get("shadow.timeout_seconds", 900))
    lookback_hours = float(get("shadow.lookback_hours", 48))

    fired_today = _today_ledger_count()
    fired = 0

    for run_dir in _iter_run_dirs_newest_first(lookback_hours):
        if fired >= limit:
            break
        if fired_today + fired >= daily_cap:
            if verbose:
                log.info("shadow sweep: daily cap (%d) reached", daily_cap)
            break

        summary["scanned"] += 1
        shadow_dir = run_dir / "shadow"
        if shadow_dir.exists():
            # Already shadowed (fired or SKIPPED) — never rescanned.
            summary["skipped"] += 1
            continue

        try:
            meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            summary["errors"] += 1
            if verbose:
                log.warning("shadow sweep: unreadable metadata.json in %s: %s", run_dir, exc)
            continue

        goal = str(meta.get("prompt") or "")
        handle_id = str(meta.get("handle_id") or run_dir.name.split("-", 1)[0])

        ok, reason = eligible(goal, meta)
        if not ok:
            # Stamp SKIPPED only for reasons in the explicit terminal set
            # (r3: not "everything except status"). Mutable-state reasons
            # (status, empty goal) leave no trace and are rescanned while
            # inside the lookback window; bounded cost, no permanent
            # exclusion (r1 Skeptic #1 confirmed live: running primaries
            # carry status None; r2 Architect #1: a resumed stuck run
            # keeps a stale ended_at while live again).
            if not dry_run and reason in _TERMINAL_REASONS:
                shadow_dir.mkdir(parents=True, exist_ok=True)
                (shadow_dir / "SKIPPED").write_text(reason + "\n", encoding="utf-8")
            summary["skipped"] += 1
            continue

        # Defer until the primary is fully FINALIZED: ended_at is stamped
        # by finalize (after the async tail drains and refreshes the card)
        # and run_card.json exists. Gating on the card alone (r2) still
        # snapshotted a PRELIMINARY card's cost into the append-only
        # ledger (r3) — ended_at is the durable "the numbers are final"
        # signal. No stamp — just not yet; a run whose curation never
        # lands falls out of the lookback window (bounded, and abnormal
        # enough that the primary's own health lanes are the right alarm).
        if not meta.get("ended_at") or not (run_dir / "run_card.json").is_file():
            summary["skipped"] += 1
            continue

        if not _sample_rate_pass(handle_id, sample_rate):
            if not dry_run:
                shadow_dir.mkdir(parents=True, exist_ok=True)
                (shadow_dir / "SKIPPED").write_text("sample_rate\n", encoding="utf-8")
            summary["skipped"] += 1
            continue

        arm = pick_arm(handle_id)

        if dry_run:
            summary["would_fire"].append({"handle_id": handle_id, "arm": arm, "run_dir": str(run_dir)})
            fired += 1
            continue

        # Star prompt is read ONCE, before the claim, and handed down
        # (review 2026-08-14 r1 Minimalist; r2 both lenses closed the
        # remaining TOCTOU): a transient star-skill problem must not
        # consume the run's one shadow slot — unclaimed, the run is
        # retried next sweep.
        star_payload: Optional[Tuple[str, dict]] = None
        if arm == ARM_STAR:
            try:
                star_payload = star_prompt()
            except Exception as exc:
                summary["errors"] += 1
                log.warning("shadow sweep: star prompt unavailable, leaving %s "
                            "unclaimed for a later sweep: %s", handle_id, exc)
                continue

        # Claim BEFORE launching: creating shadow/<arm>/ is the fail-closed
        # claim itself, so a concurrent sweep loses the mkdir race rather
        # than double-firing.
        try:
            (shadow_dir / arm).mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            summary["skipped"] += 1
            continue

        try:
            challenger_meta = run_challenger(run_dir, arm, goal,
                                             timeout=timeout_seconds,
                                             star=star_payload)
        except Exception as exc:
            # The claim stands (deliberately terminal — no auto-retry, the
            # partial state is evidence), but it must be VISIBLE: an empty
            # claim dir was indistinguishable from a crash mid-write
            # (review 2026-08-14, Skeptic/Architect).
            summary["errors"] += 1
            log.warning("shadow sweep: challenger failed for %s (arm=%s): %s", handle_id, arm, exc)
            try:
                (shadow_dir / arm / "ERROR").write_text(
                    f"{datetime.now(timezone.utc).isoformat()} {exc}\n",
                    encoding="utf-8")
            except OSError:
                pass
            continue

        row = {
            "handle_id": handle_id,
            "primary_lane": meta.get("lane"),
            "primary_goal_achieved": meta.get("goal_achieved"),
            "primary_ended_at": meta.get("ended_at"),
            **_primary_comparison_fields(run_dir, meta),
            **challenger_meta,
            # Append time — the daily cap's counting field. Placed AFTER
            # the challenger meta merge so nothing can shadow it.
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _append_ledger_row(row)
        except Exception as exc:
            # The ledger is the cap's only counting source — a lost row
            # means an executed challenger the next sweep can't see. Keep
            # a durable per-run copy for reconciliation (review
            # 2026-08-14, Architect).
            log.error("shadow sweep: ledger append failed for %s — writing "
                      "fallback row: %s", handle_id, exc)
            try:
                (shadow_dir / arm / "ledger-row.json").write_text(
                    json.dumps(row, indent=2, default=str), encoding="utf-8")
            except OSError:
                pass

        fired += 1
        summary["fired"] += 1
        if verbose:
            log.info("shadow sweep: fired arm=%s handle_id=%s", arm, handle_id)

    if get("shadow.go.enabled", False):
        _sweep_go_locked(summary, limit=limit, verbose=verbose, dry_run=dry_run,
                         lookback_hours=lookback_hours)
    return summary


def _stale_go_stamp(go_dir: Path) -> bool:
    """True iff the Go claim dir holds ONLY a SKIPPED stamp whose reason
    the current Go gate cannot produce (a stamp from an older, narrower
    gate). Anything else in the dir — a scratch, a result, an ERROR — is
    a real claim and is never touched."""
    try:
        entries = [p.name for p in go_dir.iterdir()]
    except OSError:
        return False
    if entries != ["SKIPPED"]:
        return False
    try:
        reason = (go_dir / "SKIPPED").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return reason not in _GO_TERMINAL_REASONS


def _sweep_go_locked(summary: Dict[str, Any], *, limit: int, verbose: bool,
                     dry_run: bool, lookback_hours: float) -> None:
    """The Go track of the sweep (same lock, own everything else): own
    eligibility, own claim dir, own cap, own summary keys (`go_*`)."""
    from config import get

    daily_cap = int(get("shadow.go.daily_cap", 2))
    timeout_seconds = int(get("shadow.go.timeout_seconds", 900))
    model = str(get("shadow.go.model", "haiku") or "haiku")
    for k in ("go_scanned", "go_skipped", "go_fired", "go_errors"):
        summary.setdefault(k, 0)
    if dry_run:
        summary.setdefault("go_would_fire", [])

    fired_today = _today_ledger_count(frozenset({ARM_GO}))
    fired = 0
    for run_dir in _iter_run_dirs_newest_first(lookback_hours):
        if fired >= limit:
            break
        if fired_today + fired >= daily_cap:
            if verbose:
                log.info("shadow sweep (go): daily cap (%d) reached", daily_cap)
            break
        summary["go_scanned"] += 1
        go_dir = run_dir / GO_DIR
        if go_dir.exists():
            if _stale_go_stamp(go_dir):
                if dry_run:
                    summary["go_skipped"] += 1
                    continue
                # A stamp from a retired reason is not a claim: retire the
                # stamp (the only file the dir holds) and re-evaluate.
                try:
                    (go_dir / "SKIPPED").unlink()
                    go_dir.rmdir()
                except OSError as exc:
                    summary["go_errors"] += 1
                    log.warning("shadow sweep (go): could not retire stale stamp in %s: %s", run_dir.name, exc)
                    continue
            else:
                summary["go_skipped"] += 1
                continue
        try:
            meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            summary["go_errors"] += 1
            continue
        goal = str(meta.get("prompt") or "")
        handle_id = str(meta.get("handle_id") or run_dir.name.split("-", 1)[0])
        ok, reason = go_eligible(goal, meta)
        if not ok:
            if not dry_run and reason in _GO_TERMINAL_REASONS:
                go_dir.mkdir(parents=True, exist_ok=True)
                (go_dir / "SKIPPED").write_text(reason + "\n", encoding="utf-8")
            summary["go_skipped"] += 1
            continue
        if not meta.get("ended_at") or not (run_dir / "run_card.json").is_file():
            summary["go_skipped"] += 1
            continue
        lane = str(meta.get("lane"))
        if dry_run:
            summary["go_would_fire"].append({"handle_id": handle_id, "arm": ARM_GO,
                                             "lane": lane, "run_dir": str(run_dir)})
            fired += 1
            continue
        # Engine resolved BEFORE the claim (the star-prompt precedent): a
        # missing binary must not consume the run's Go slot.
        binary = go_binary()
        if binary is None:
            summary["go_errors"] += 1
            log.warning("shadow sweep (go): engine binary not found (shadow.go.binary), "
                        "leaving %s unclaimed", handle_id)
            break  # no engine, no point scanning further this sweep
        try:
            go_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            summary["go_skipped"] += 1
            continue
        try:
            challenger_meta = run_go_challenger(run_dir, goal, lane=lane, binary=binary,
                                                timeout=timeout_seconds, model=model)
        except Exception as exc:
            summary["go_errors"] += 1
            log.warning("shadow sweep (go): challenger failed for %s: %s", handle_id, exc)
            try:
                (go_dir / "ERROR").write_text(
                    f"{datetime.now(timezone.utc).isoformat()} {exc}\n", encoding="utf-8")
            except OSError:
                pass
            continue
        row = {
            "handle_id": handle_id,
            "primary_lane": meta.get("lane"),
            "primary_goal_achieved": meta.get("goal_achieved"),
            "primary_ended_at": meta.get("ended_at"),
            "primary_goal_shape": goal_shape(goal),
            **_primary_comparison_fields(run_dir, meta),
            **challenger_meta,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _append_ledger_row(row)
        except Exception as exc:
            log.error("shadow sweep (go): ledger append failed for %s — writing "
                      "fallback row: %s", handle_id, exc)
            try:
                (go_dir / "ledger-row.json").write_text(
                    json.dumps(row, indent=2, default=str), encoding="utf-8")
            except OSError:
                pass
        fired += 1
        summary["go_fired"] += 1
        if verbose:
            log.info("shadow sweep (go): fired handle_id=%s go_handle=%s", handle_id,
                     challenger_meta.get("go_handle"))


def _primary_comparison_fields(run_dir: Path, meta: dict) -> dict:
    """Primary-side fields the batch adjudication needs (review 2026-08-14,
    Architect: a ledger row that can't say what the PRIMARY cost can't
    support the cost-comparison half of the pre-registered questions).
    Best-effort — absent sources yield None, never block the row."""
    out: Dict[str, Any] = {
        "primary_model": meta.get("model"),
        "primary_cost_usd": None,
        "primary_wall_seconds": None,
    }
    card_path = run_dir / "run_card.json"
    try:
        card = loads_clean(store_text(card_path))
        out["primary_cost_usd"] = card.get("total_cost_usd")
    except FileNotFoundError:
        pass  # absent-by-age is the documented None case, not loss
    except (OSError, ValueError):
        # Unreadable is NOT absent (probed 2026-08-18): the strict read
        # degraded a torn/tainted card to a silent None — a cost-comparison
        # row that cannot say the PRIMARY cost is unknown for a stated
        # reason. The row still never blocks; the WARN is the announce.
        log.warning("shadow ledger: run_card.json unreadable in %s — "
                    "primary_cost_usd omitted from the comparison row",
                    run_dir.name)
    try:
        _s = meta.get("started_at")
        _e = meta.get("ended_at")
        if _s and _e:
            _sd = datetime.fromisoformat(str(_s).replace("Z", "+00:00"))
            _ed = datetime.fromisoformat(str(_e).replace("Z", "+00:00"))
            out["primary_wall_seconds"] = round((_ed - _sd).total_seconds(), 3)
    except ValueError:
        pass
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _status() -> dict:
    path = _ledger_path()
    if not path.is_file():
        return {"rows": 0, "last": None, "per_arm": {}}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    per_arm: Dict[str, int] = {}
    for row in rows:
        arm = row.get("arm", "?")
        per_arm[arm] = per_arm.get(arm, 0) + 1
    return {"rows": len(rows), "last": rows[-1] if rows else None, "per_arm": per_arm}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shadow_lane",
        description="Shadow lane: post-run champion-challenger sweep + ledger status.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_sweep = sub.add_parser("sweep", help="scan runs and fire eligible shadow challengers")
    p_sweep.add_argument("--limit", type=int, default=1, help="max challengers to fire (default: 1)")
    p_sweep.add_argument("--verbose", action="store_true")
    p_sweep.add_argument("--dry-run", action="store_true", help="report what WOULD fire; write nothing")

    sub.add_parser("status", help="ledger row count, last row, per-arm counts")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cmd = args.cmd

    if cmd is None:
        parser.print_help()
        return 1

    if cmd == "sweep":
        if args.verbose:
            logging.basicConfig(level=logging.INFO)
        result = sweep(limit=args.limit, verbose=args.verbose, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return 0

    if cmd == "status":
        print(json.dumps(_status(), indent=2, default=str))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
