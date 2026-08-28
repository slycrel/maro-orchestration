"""Behavior-suite harness — drive the engine at the workspace boundary.

This module is the ONLY place the behavior suite touches the Python engine's
entry points. Everything else in tests/behavior/ asserts against on-disk
workspace artifacts and the shapes registered in docs/CONTRACTS.md — never
against Python internals, module state, or return objects. A future Go
engine passes the same suite by producing the same files; only this driver
layer gets rewritten (goal in → workspace out).

Workspace isolation comes from tests/conftest.py's autouse fixture
(MARO_WORKSPACE → per-test tmp dir); nothing here may ever resolve the live
~/.maro/workspace.

ScriptedAdapter is the suite's scripted LLM: a table of predetermined
responses (the same seam production dry-runs and tests/test_e2e_smoke.py
use). No scenario in this suite makes a network call or spends tokens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# ScriptedAdapter — programmable mock LLM (adapted from tests/test_e2e_smoke.py,
# the strongest existing no-network seam for driving handle()/the agenda loop).
# ---------------------------------------------------------------------------

class ScriptedAdapter:
    """LLM adapter returning scripted responses in sequence.

    responses: list of dicts, each consumed in order. Keys:
      - "steps": list of step strings (decompose calls → JSON array content)
      - "tool": "complete_step" | "flag_stuck" (execute calls)
      - "result" / "reason": tool-call arguments
      - "content": raw content for non-tool calls

    When the table is exhausted, the last tool-bearing response repeats for
    tool calls and the last non-tool response repeats for plain calls — so
    tail traffic (closure, validation, reflection) stays deterministic.
    """

    model_key = "scripted"
    backend = "scripted"

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self._call_idx = 0
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, *, tools=None, tool_choice="auto",
                 max_tokens=4096, temperature=0.3, **kwargs):
        from llm import LLMResponse, ToolCall

        user_content = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        self.calls.append({
            "idx": self._call_idx,
            "user_content_prefix": user_content[:200],
            "has_tools": bool(tools),
        })

        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
        elif tools:
            resp = next(
                (r for r in reversed(self._responses) if "tool" in r),
                self._responses[-1] if self._responses else {},
            )
        else:
            resp = next(
                (r for r in reversed(self._responses) if "tool" not in r),
                self._responses[-1] if self._responses else {},
            )
        self._call_idx += 1

        if "steps" in resp:
            return LLMResponse(
                content=json.dumps(resp["steps"]),
                stop_reason="end_turn", input_tokens=50, output_tokens=30,
            )
        if "tool" in resp and (tools or tool_choice == "required"):
            if resp["tool"] == "complete_step":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={
                            "result": resp.get("result", "[scripted] done"),
                            "summary": resp.get("result", "[scripted] done")[:60],
                        },
                    )],
                    stop_reason="tool_use", input_tokens=80, output_tokens=40,
                )
            if resp["tool"] == "flag_stuck":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={"reason": resp.get("reason", "[scripted] blocked")},
                    )],
                    stop_reason="tool_use", input_tokens=80, output_tokens=40,
                )
        return LLMResponse(
            content=resp.get("content", '{"passed": true}'),
            stop_reason="end_turn", input_tokens=20, output_tokens=10,
        )


# ---------------------------------------------------------------------------
# Workspace-artifact readers (the assertion side — files only)
# ---------------------------------------------------------------------------

def workspace() -> Path:
    """The isolated tmp workspace (never the live one — see conftest)."""
    from config import workspace_root
    ws = workspace_root()
    # Belt-and-braces per feedback_live_store_probes: assert the resolved
    # path before anything downstream trusts it.
    assert ".maro" not in str(ws), f"behavior suite resolved live workspace: {ws}"
    return ws


def read_jsonl(path: Path) -> List[dict]:
    """Tolerant JSONL reader per CONTRACTS.md B2: skip malformed lines."""
    rows: List[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def run_dirs(ws: Optional[Path] = None) -> List[Path]:
    root = (ws or workspace()) / "runs"
    if not root.exists():
        return []
    # B3: run dirs are <8-hex-handle_id>-<nickname>; ignore the derived
    # .run-ref-index-v2 lookup index (disposable, never a source of truth).
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def run_dir_for(handle_id: str, ws: Optional[Path] = None) -> Path:
    for rd in run_dirs(ws):
        if rd.name.startswith(f"{handle_id}-"):
            return rd
    raise AssertionError(f"no run dir for handle_id {handle_id!r}")


def read_meta(rd: Path) -> dict:
    return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))


def read_card(rd: Path) -> dict:
    return json.loads((rd / "run_card.json").read_text(encoding="utf-8"))


def newest_outcome(ws: Optional[Path] = None) -> dict:
    rows = read_jsonl((ws or workspace()) / "memory" / "outcomes.jsonl")
    assert rows, "no outcomes.jsonl rows"
    return rows[-1]


# CONTRACTS.md B5: the success_class vocabulary as registered today.
SUCCESS_CLASSES = {
    "success", "done-not-achieved", "done-unverified", "done-verdict-pending",
    "achieved-not-done", "partial", "failed", "interrupted", "unknown",
}

# CONTRACTS.md B9: single-write byte budget for memory/events.jsonl lines.
EVENTS_LINE_BUDGET = 4096


def assert_events_line_discipline(events_path: Path) -> int:
    """B9: every emitted line is valid JSON, ≤4096 bytes incl. newline, with
    event_type + ts. Returns the number of lines checked."""
    if not events_path.exists():
        return 0
    n = 0
    with open(events_path, "rb") as fh:
        for raw in fh:
            n += 1
            assert len(raw) <= EVENTS_LINE_BUDGET, (
                f"events.jsonl line {n} is {len(raw)} bytes (> {EVENTS_LINE_BUDGET})"
            )
            row = json.loads(raw.decode("utf-8"))
            assert "event_type" in row, f"events.jsonl line {n} missing event_type"
            assert "ts" in row, f"events.jsonl line {n} missing ts"
    return n


# ---------------------------------------------------------------------------
# Scenario driver — goal in, workspace artifacts out
# ---------------------------------------------------------------------------

@dataclass
class GoalScenario:
    """One data-first behavior scenario: a goal, a scripted-response table,
    and expected workspace-artifact facts. Kept declarative so a future Go
    harness can consume the same rows (driver-specific knobs are marked)."""

    id: str
    goal: str
    lane: str                                  # force_lane: "now" | "agenda"
    responses: List[Dict[str, Any]]            # ScriptedAdapter table
    project: Optional[str] = None
    record_calls: bool = False                 # drive through the recording seam (B4)
    expect_status: Optional[str] = None        # metadata.json status (B3)
    expect_meta: Dict[str, Any] = field(default_factory=dict)      # subset match
    expect_meta_keys: List[str] = field(default_factory=list)      # keys present
    expect_run_files: List[str] = field(default_factory=list)      # rel paths exist
    expect_outcome: Dict[str, Any] = field(default_factory=dict)   # subset match, newest row (B6)
    expect_outcome_unjudged: bool = False      # 'goal_achieved' ABSENT (B6 rule A6)
    expect_success_class: Optional[str] = None  # run_card success_class (B5)
    contracts: str = ""                        # cited CONTRACTS.md entries


def drive(sc: GoalScenario):
    """Run one scenario through handle() with a scripted adapter.

    Returns (result, run_dir). Assertions on the run dir belong to callers.
    """
    from handle import handle

    adapter: Any = ScriptedAdapter(sc.responses)
    if sc.record_calls:
        # B4: record_llm_call lives on the FailoverAdapter seam — wrap the
        # scripted adapter the way build_adapter wraps every real backend.
        from llm import FailoverAdapter
        adapter = FailoverAdapter([adapter])

    result = handle(
        sc.goal,
        project=sc.project,
        adapter=adapter,
        force_lane=sc.lane,
    )
    rd = run_dir_for(result.handle_id)
    return result, rd


def assert_common_contracts(sc: GoalScenario, result, rd: Path) -> None:
    """The cross-cutting checks every goal-driven scenario must satisfy.

    Each block cites the CONTRACTS.md entry it pins.
    """
    ws = workspace()

    # B11 handle-inputs: one intake row, raw_input verbatim, joinable by
    # handle_id.
    inputs = read_jsonl(ws / "memory" / "handle_inputs.jsonl")
    mine = [r for r in inputs if r.get("handle_id") == result.handle_id]
    assert len(mine) == 1, f"expected exactly one handle_inputs row, got {len(mine)}"
    assert mine[0]["raw_input"] == sc.goal
    assert mine[0].get("ts")

    # B3 run-dir: skeleton + verbatim prompt + metadata core keys.
    for sub in ("source", "build", "artifact"):
        assert (rd / sub).is_dir(), f"missing run-dir skeleton dir {sub}/"
    assert (rd / "source" / "prompt.txt").read_text(encoding="utf-8") == sc.goal
    meta = read_meta(rd)
    for key in ("handle_id", "nickname", "prompt", "started_at"):
        assert key in meta, f"metadata.json missing required core key {key}"
    assert meta["handle_id"] == result.handle_id
    assert meta["prompt"] == sc.goal
    assert rd.name == f"{meta['handle_id']}-{meta['nickname']}"
    assert meta.get("lane") == sc.lane
    if sc.expect_status is not None:
        assert meta.get("status") == sc.expect_status
        assert meta.get("ended_at"), "terminal run must carry ended_at (B3)"
    for key in sc.expect_meta_keys:
        assert key in meta, f"metadata.json missing expected key {key}"
    for key, val in sc.expect_meta.items():
        assert meta.get(key) == val, f"metadata[{key}]={meta.get(key)!r} != {val!r}"

    # Scenario-declared artifacts.
    for rel in sc.expect_run_files:
        assert (rd / rel).exists(), f"expected run artifact missing: {rel}"

    # B5 run-card: curated view exists, success_class in the registered
    # vocabulary, identity echoes metadata (metadata is authoritative).
    card = read_card(rd)
    assert card.get("handle_id") == result.handle_id
    assert card.get("goal") == sc.goal
    assert card.get("success_class") in SUCCESS_CLASSES
    if sc.expect_success_class is not None:
        assert card["success_class"] == sc.expect_success_class

    # B6 outcomes: a row for this run, tri-state verdict discipline.
    rows = read_jsonl(ws / "memory" / "outcomes.jsonl")
    mine = [r for r in rows if r.get("handle_id") == result.handle_id]
    assert mine, "run recorded no outcomes.jsonl row (B6)"
    row = mine[-1]
    for key in ("outcome_id", "goal", "summary", "status", "recorded_at"):
        assert key in row, f"outcomes row missing required key {key}"
    if "goal_achieved" in row:
        assert type(row["goal_achieved"]) is bool, (
            "goal_achieved must be exactly bool when present (B6)"
        )
    if sc.expect_outcome_unjudged:
        assert "goal_achieved" not in row, (
            "unjudged row must OMIT goal_achieved (absent != null/False, rule A6)"
        )
    for key, val in sc.expect_outcome.items():
        assert row.get(key) == val, f"outcome[{key}]={row.get(key)!r} != {val!r}"

    # B8 captains-log: append-only event bus saw this run (rows carry the
    # required timestamp/event_type; audience is the derived stamp).
    log_rows = read_jsonl(ws / "memory" / "captains_log.jsonl")
    assert log_rows, "captains_log.jsonl has no rows"
    for entry in log_rows:
        assert "timestamp" in entry and "event_type" in entry
        assert entry.get("audience") in ("user", "system")

    # B9 events feed: whatever the run emitted obeys the line discipline.
    assert_events_line_discipline(ws / "memory" / "events.jsonl")
