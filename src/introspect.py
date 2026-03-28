"""Phase 44: Self-Reflection — Run Observer + Failure Classifier.

Analyzes execution traces (events.jsonl, step outcomes) and produces
structured diagnoses. No LLM calls — pure heuristics on trace data.

Usage:
    from introspect import diagnose_loop, diagnose_latest
    diag = diagnose_loop("a3b2c1d0")
    print(diag.failure_class, diag.recommendation)

CLI:
    poe-introspect <loop_id>
    poe-introspect --latest
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("poe.introspect")


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

FAILURE_CLASSES = {
    "setup_failure":              "Step 1 blocks with adapter/import error before real work starts",
    "adapter_timeout":            "Step blocks with tokens=0 and elapsed > 60s (subprocess timeout)",
    "constraint_false_positive":  "Step blocked by constraint with tokens=0 on natural-language step",
    "decomposition_too_broad":    "Single step consumed > 200K tokens or > 120s",
    "empty_model_output":         "Model returned tokens but content < 20 chars with no tool call",
    "retry_churn":                "Same step retried 2+ times with different block reasons",
    "budget_exhaustion":          "max_iterations reached with remaining steps undone",
    "token_explosion":            "Token growth rate > 3x between consecutive steps",
    "artifact_missing":           "Loop completed but no readable output in done steps",
    "integration_drift":          "ImportError or AttributeError caught in execution path",
    "healthy":                    "No pathology detected",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StepProfile:
    step_idx: int
    text: str
    status: str
    tokens: int
    elapsed_ms: int
    event_type: str = ""

@dataclass
class LoopDiagnosis:
    loop_id: str
    failure_class: str
    severity: str               # "info" | "warning" | "critical"
    evidence: List[str] = field(default_factory=list)
    recommendation: str = ""
    token_profile: List[dict] = field(default_factory=list)
    timing_profile: List[dict] = field(default_factory=list)
    total_tokens: int = 0
    total_elapsed_ms: int = 0
    steps_done: int = 0
    steps_blocked: int = 0
    steps_total: int = 0

    def summary(self) -> str:
        return (
            f"[{self.failure_class}] severity={self.severity} "
            f"steps={self.steps_done}/{self.steps_total}done "
            f"tokens={self.total_tokens} elapsed={self.total_elapsed_ms}ms "
            f"| {self.recommendation}"
        )

    def to_dict(self) -> dict:
        return {
            "loop_id": self.loop_id,
            "failure_class": self.failure_class,
            "severity": self.severity,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "total_tokens": self.total_tokens,
            "total_elapsed_ms": self.total_elapsed_ms,
            "steps_done": self.steps_done,
            "steps_blocked": self.steps_blocked,
            "steps_total": self.steps_total,
        }


# ---------------------------------------------------------------------------
# Event loading
# ---------------------------------------------------------------------------

def _events_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "events.jsonl"
    except ImportError:
        return Path.cwd() / "memory" / "events.jsonl"


def _diagnoses_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "diagnoses.jsonl"
    except ImportError:
        return Path.cwd() / "memory" / "diagnoses.jsonl"


def _load_loop_events(loop_id: str) -> List[dict]:
    """Load all events for a specific loop_id from events.jsonl."""
    path = _events_path()
    if not path.exists():
        return []
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("loop_id", "").startswith(loop_id):
                    events.append(e)
            except Exception:
                continue
    except Exception:
        pass
    return events


def _load_latest_loop_id() -> Optional[str]:
    """Find the most recent loop_id from events.jsonl."""
    path = _events_path()
    if not path.exists():
        return None
    last_id = None
    try:
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                lid = e.get("loop_id", "")
                if lid:
                    last_id = lid
                    break
            except Exception:
                continue
    except Exception:
        pass
    return last_id


# ---------------------------------------------------------------------------
# Diagnosis heuristics
# ---------------------------------------------------------------------------

def _build_step_profiles(events: List[dict]) -> List[StepProfile]:
    """Build per-step profiles from raw events."""
    profiles: List[StepProfile] = []
    for e in events:
        if e.get("event_type") in ("step_done", "step_stuck"):
            profiles.append(StepProfile(
                step_idx=e.get("step_idx", 0),
                text=e.get("step", ""),
                status=e.get("status", ""),
                tokens=e.get("tokens_in", 0) + e.get("tokens_out", 0),
                elapsed_ms=e.get("elapsed_ms", 0),
                event_type=e.get("event_type", ""),
            ))
    return profiles


def diagnose_loop(loop_id: str) -> LoopDiagnosis:
    """Analyze a completed loop's execution trace and classify any failures.

    Pure heuristics — no LLM calls. Reads events.jsonl for the given loop_id
    and produces a structured diagnosis.

    Returns LoopDiagnosis with failure_class from the taxonomy.
    """
    events = _load_loop_events(loop_id)
    if not events:
        return LoopDiagnosis(
            loop_id=loop_id,
            failure_class="artifact_missing",
            severity="warning",
            evidence=["No events found for this loop_id"],
            recommendation="Check that events.jsonl is being written (observe.write_event)",
        )

    profiles = _build_step_profiles(events)
    done = [p for p in profiles if p.status == "done"]
    blocked = [p for p in profiles if p.status in ("stuck", "blocked")]
    total_tokens = sum(p.tokens for p in profiles)
    total_elapsed = sum(p.elapsed_ms for p in profiles)

    # Check for loop_done event
    loop_done = [e for e in events if e.get("event_type") == "loop_done"]
    loop_status = loop_done[0].get("status", "") if loop_done else ""
    stuck_reason = loop_done[0].get("detail", "") if loop_done else ""

    evidence: List[str] = []
    failure_class = "healthy"
    severity = "info"
    recommendation = ""

    # --- Heuristic checks (most specific first) ---

    # 1. Setup failure: first step blocks with zero tokens
    if profiles and profiles[0].status in ("stuck", "blocked") and profiles[0].tokens == 0:
        if profiles[0].elapsed_ms < 5000:
            failure_class = "setup_failure"
            severity = "critical"
            evidence.append(f"Step 1 blocked with 0 tokens in {profiles[0].elapsed_ms}ms")
            evidence.append(f"Step text: {profiles[0].text[:100]}")
            recommendation = "Check adapter resolution, import errors, or constraint false positives"

    # 2. Adapter timeout: step blocks with 0 tokens and > 60s
    for p in blocked:
        if p.tokens == 0 and p.elapsed_ms > 60000:
            failure_class = "adapter_timeout"
            severity = "critical"
            evidence.append(f"Step {p.step_idx} blocked with 0 tokens after {p.elapsed_ms}ms")
            recommendation = "Subprocess adapter likely timed out. Consider API adapter or smaller step scope."
            break

    # 3. Constraint false positive: blocked with 0 tokens but fast (< 1s)
    if failure_class == "healthy":
        fp_steps = [p for p in blocked if p.tokens == 0 and p.elapsed_ms < 1000]
        if len(fp_steps) >= 2:
            failure_class = "constraint_false_positive"
            severity = "warning"
            evidence.append(f"{len(fp_steps)} steps blocked with 0 tokens in < 1s each")
            for p in fp_steps[:3]:
                evidence.append(f"  Step {p.step_idx}: {p.text[:80]}")
            recommendation = "Review constraint tier patterns — likely overly broad for natural language steps"

    # 4. Decomposition too broad: single step > 200K tokens or > 120s
    if failure_class == "healthy":
        for p in profiles:
            if p.tokens > 200000:
                failure_class = "decomposition_too_broad"
                severity = "warning"
                evidence.append(f"Step {p.step_idx} consumed {p.tokens} tokens ({p.elapsed_ms}ms)")
                evidence.append(f"Step text: {p.text[:100]}")
                recommendation = "Decompose further — cap code review at 3-5 files / ~2000 lines per step"
                break
            if p.elapsed_ms > 120000 and p.tokens > 50000:
                failure_class = "decomposition_too_broad"
                severity = "warning"
                evidence.append(f"Step {p.step_idx} took {p.elapsed_ms}ms with {p.tokens} tokens")
                evidence.append(f"Step text: {p.text[:100]}")
                recommendation = "Step is too large — split into smaller focused substeps"
                break

    # 5. Token explosion: > 3x growth between consecutive steps
    if failure_class == "healthy" and len(profiles) >= 3:
        for i in range(1, len(profiles)):
            prev_tok = profiles[i-1].tokens
            curr_tok = profiles[i].tokens
            if prev_tok > 1000 and curr_tok > prev_tok * 3:
                failure_class = "token_explosion"
                severity = "warning"
                evidence.append(
                    f"Step {profiles[i].step_idx}: {curr_tok} tokens "
                    f"(vs {prev_tok} for step {profiles[i-1].step_idx} — {curr_tok/prev_tok:.1f}x growth)"
                )
                recommendation = "Truncate completed_context or limit step output size"
                break

    # 6. Empty model output: tokens > 0 but blocked
    if failure_class == "healthy":
        empty_steps = [p for p in blocked if p.tokens > 0 and p.elapsed_ms < 30000]
        if len(empty_steps) >= 2:
            failure_class = "empty_model_output"
            severity = "warning"
            evidence.append(f"{len(empty_steps)} steps blocked despite receiving tokens")
            recommendation = "Model may not be calling tools — check tool_choice='required' and response parsing"

    # 7. Budget exhaustion: loop hit max_iterations
    if "max_iterations" in stuck_reason:
        if failure_class == "healthy":
            failure_class = "budget_exhaustion"
        severity = "warning" if failure_class == "budget_exhaustion" else severity
        evidence.append(f"Loop stuck: {stuck_reason}")
        if not recommendation:
            recommendation = "Increase max_iterations or enable budget-aware landing"

    # 8. Retry churn: check for repeated step attempts
    step_texts = [p.text for p in blocked]
    seen = {}
    for t in step_texts:
        key = t[:60]
        seen[key] = seen.get(key, 0) + 1
    churned = {k: v for k, v in seen.items() if v >= 2}
    if churned and failure_class == "healthy":
        failure_class = "retry_churn"
        severity = "warning"
        for k, v in churned.items():
            evidence.append(f"Step retried {v}x: {k}")
        recommendation = "Step is consistently failing — skip and note as partial, or decompose differently"

    # Build token/timing profiles for the response
    token_profile = [{"step": p.step_idx, "tokens": p.tokens, "status": p.status} for p in profiles]
    timing_profile = [{"step": p.step_idx, "elapsed_ms": p.elapsed_ms, "status": p.status} for p in profiles]

    diag = LoopDiagnosis(
        loop_id=loop_id,
        failure_class=failure_class,
        severity=severity,
        evidence=evidence,
        recommendation=recommendation,
        token_profile=token_profile,
        timing_profile=timing_profile,
        total_tokens=total_tokens,
        total_elapsed_ms=total_elapsed,
        steps_done=len(done),
        steps_blocked=len(blocked),
        steps_total=len(profiles),
    )

    log.info("diagnosis loop_id=%s class=%s severity=%s steps=%d/%d tokens=%d",
             loop_id, failure_class, severity, len(done), len(profiles), total_tokens)

    return diag


def diagnose_latest() -> Optional[LoopDiagnosis]:
    """Diagnose the most recent loop from events.jsonl."""
    loop_id = _load_latest_loop_id()
    if loop_id is None:
        return None
    return diagnose_loop(loop_id)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_diagnosis(diag: LoopDiagnosis) -> None:
    """Append a diagnosis to memory/diagnoses.jsonl."""
    path = _diagnoses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(diag.to_dict()) + "\n")


def load_diagnoses(limit: int = 50) -> List[LoopDiagnosis]:
    """Load recent diagnoses from memory/diagnoses.jsonl."""
    path = _diagnoses_path()
    if not path.exists():
        return []
    results = []
    try:
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                results.append(LoopDiagnosis(**{
                    k: d[k] for k in LoopDiagnosis.__dataclass_fields__ if k in d
                }))
            except Exception:
                continue
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="poe-introspect",
        description="Diagnose execution traces — classify failures and recommend fixes",
    )
    parser.add_argument("loop_id", nargs="?", help="Loop ID to diagnose (or --latest)")
    parser.add_argument("--latest", action="store_true", help="Diagnose the most recent loop")
    parser.add_argument("--history", type=int, metavar="N", help="Show last N diagnoses")

    args = parser.parse_args(argv)

    if args.history:
        diagnoses = load_diagnoses(limit=args.history)
        if not diagnoses:
            print("No diagnoses recorded yet.")
            return
        for d in reversed(diagnoses):
            icon = {"info": " ", "warning": "!", "critical": "X"}.get(d.severity, "?")
            print(f"  [{icon}] {d.loop_id[:8]}  {d.failure_class:<28} "
                  f"steps={d.steps_done}/{d.steps_total} tokens={d.total_tokens}")
        return

    if args.latest:
        diag = diagnose_latest()
    elif args.loop_id:
        diag = diagnose_loop(args.loop_id)
    else:
        diag = diagnose_latest()

    if diag is None:
        print("No loop events found.")
        return

    print(f"Loop {diag.loop_id}")
    print(f"  Class:    {diag.failure_class}")
    print(f"  Severity: {diag.severity}")
    print(f"  Steps:    {diag.steps_done} done / {diag.steps_blocked} blocked / {diag.steps_total} total")
    print(f"  Tokens:   {diag.total_tokens:,}")
    print(f"  Elapsed:  {diag.total_elapsed_ms:,}ms")
    if diag.evidence:
        print(f"  Evidence:")
        for e in diag.evidence:
            print(f"    - {e}")
    if diag.recommendation:
        print(f"  Recommendation: {diag.recommendation}")

    # Show token profile
    if diag.token_profile:
        print(f"\n  Token profile:")
        for tp in diag.token_profile:
            bar = "=" * min(50, tp["tokens"] // 5000) if tp["tokens"] > 0 else ""
            icon = "+" if tp["status"] == "done" else "x"
            print(f"    step {tp['step']:2d} [{icon}] {tp['tokens']:>8,}  {bar}")


if __name__ == "__main__":
    main()
