"""Phase 44: Self-Reflection — Run Observer + Failure Classifier + Multi-Lens Introspection.

Analyzes execution traces (events.jsonl, step outcomes) and produces
structured diagnoses. Heuristic lenses run always (free); LLM lenses
run selectively on failure or high-stakes tasks.

Usage:
    from introspect import diagnose_loop, diagnose_latest, run_lenses
    diag = diagnose_loop("a3b2c1d0")
    lens_results = run_lenses(diag, profiles)
    print(diag.failure_class, diag.recommendation)

CLI:
    poe-introspect <loop_id>
    poe-introspect --latest
    poe-introspect --latest --lenses
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
    except Exception:
        return Path.cwd() / "memory" / "events.jsonl"


def _diagnoses_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "diagnoses.jsonl"
    except Exception:
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
            except ImportError:
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
# Multi-Lens Introspection
# ---------------------------------------------------------------------------

@dataclass
class LensResult:
    """Output from a single analytical lens."""
    lens_name: str
    findings: List[str] = field(default_factory=list)
    action: Optional[str] = None      # recommended next step (None = nothing notable)
    confidence: float = 0.0            # 0.0–1.0
    cost: str = "free"                 # "free" | "cheap" | "expensive"


# Type alias for a lens function
LensFn = Callable[[LoopDiagnosis, List[StepProfile]], LensResult]


class LensRegistry:
    """Registry of analytical lenses. Extensible — add lenses via register()."""

    def __init__(self) -> None:
        self._lenses: Dict[str, LensFn] = {}
        self._costs: Dict[str, str] = {}

    def register(self, name: str, fn: LensFn, cost: str = "free") -> None:
        self._lenses[name] = fn
        self._costs[name] = cost

    def list(self) -> List[str]:
        return sorted(self._lenses.keys())

    def run(self, name: str, diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
        fn = self._lenses[name]
        result = fn(diag, profiles)
        result.cost = self._costs.get(name, "free")
        return result

    def run_heuristic(self, diag: LoopDiagnosis, profiles: List[StepProfile]) -> List[LensResult]:
        """Run all free (heuristic) lenses."""
        results = []
        for name, fn in self._lenses.items():
            if self._costs.get(name) == "free":
                try:
                    results.append(self.run(name, diag, profiles))
                except Exception as exc:
                    log.debug("lens %s failed: %s", name, exc)
        return results

    def run_all(self, diag: LoopDiagnosis, profiles: List[StepProfile]) -> List[LensResult]:
        """Run all lenses (heuristic + LLM)."""
        results = []
        for name in self._lenses:
            try:
                results.append(self.run(name, diag, profiles))
            except Exception as exc:
                log.debug("lens %s failed: %s", name, exc)
        return results


# ---------------------------------------------------------------------------
# Built-in heuristic lenses (free — always run)
# ---------------------------------------------------------------------------

def _cost_lens(diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
    """Which steps burned the most tokens? Was there a cheaper path?"""
    findings: List[str] = []
    action = None

    if not profiles:
        return LensResult(lens_name="cost")

    total = sum(p.tokens for p in profiles)
    if total == 0:
        return LensResult(lens_name="cost", findings=["No tokens recorded"])

    # Find the most expensive step
    top = max(profiles, key=lambda p: p.tokens)
    top_pct = (top.tokens / total * 100) if total > 0 else 0

    if top_pct > 50:
        findings.append(
            f"Step {top.step_idx} consumed {top_pct:.0f}% of total tokens "
            f"({top.tokens:,} / {total:,})"
        )
        action = f"Split step {top.step_idx} into smaller substeps or route to cheaper model"

    # Check for steps that could have used a cheaper model
    # Simple heuristic: classify/verify/format steps with > 10K tokens are wasteful
    cheap_keywords = {"classify", "verify", "format", "list", "check", "validate", "count"}
    for p in profiles:
        if p.tokens > 10000:
            words = set(p.text.lower().split())
            if words & cheap_keywords:
                findings.append(
                    f"Step {p.step_idx} ({p.text[:50]}) used {p.tokens:,} tokens "
                    f"but looks like a classify/verify task — should use cheap model"
                )
                if action is None:
                    action = "Route simple classify/verify steps to cheap model tier"

    # Average cost per done step
    done_profiles = [p for p in profiles if p.status == "done"]
    if done_profiles:
        avg = total // len(done_profiles)
        findings.append(f"Average tokens per done step: {avg:,}")

    confidence = 0.8 if action else 0.3
    return LensResult(lens_name="cost", findings=findings, action=action, confidence=confidence)


def _architecture_lens(diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
    """Were independent steps run sequentially? Wrong ordering?"""
    findings: List[str] = []
    action = None

    if len(profiles) < 3:
        return LensResult(lens_name="architecture")

    # Check for steps that could have been parallel
    # Heuristic: steps with no shared keywords are likely independent
    _independent_pairs = 0
    for i in range(len(profiles) - 1):
        words_a = set(profiles[i].text.lower().split())
        words_b = set(profiles[i + 1].text.lower().split())
        # Remove common filler words
        _filler = {"the", "a", "an", "and", "or", "to", "in", "of", "for", "on", "with", "from"}
        words_a -= _filler
        words_b -= _filler
        overlap = words_a & words_b
        if len(overlap) <= 1 and profiles[i].status == "done" and profiles[i + 1].status == "done":
            _independent_pairs += 1

    if _independent_pairs >= 2:
        findings.append(
            f"{_independent_pairs} consecutive step pairs appear independent — "
            f"could have been parallelized"
        )
        action = "Enable parallel_fan_out for independent steps"

    # Check for blocked step followed by unrelated done step (ordering issue)
    for i in range(len(profiles) - 1):
        if profiles[i].status in ("stuck", "blocked") and profiles[i + 1].status == "done":
            findings.append(
                f"Step {profiles[i].step_idx} blocked, but step {profiles[i + 1].step_idx} "
                f"succeeded — reordering might avoid the block"
            )

    # Check for very uneven step sizes (suggests poor decomposition)
    tokens = [p.tokens for p in profiles if p.tokens > 0]
    if tokens and len(tokens) >= 3:
        avg = sum(tokens) // len(tokens)
        outliers = [p for p in profiles if p.tokens > avg * 3 and p.tokens > 50000]
        if outliers:
            findings.append(
                f"{len(outliers)} step(s) are 3x+ the average token cost — "
                f"decomposition is uneven"
            )
            if action is None:
                action = "Rebalance decomposition — split heavy steps, merge trivial ones"

    confidence = 0.7 if action else 0.2
    return LensResult(lens_name="architecture", findings=findings, action=action, confidence=confidence)


def _operator_lens(diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
    """Where is wall-clock time going? Is it making real progress?"""
    findings: List[str] = []
    action = None

    if not profiles:
        return LensResult(lens_name="operator")

    total_ms = sum(p.elapsed_ms for p in profiles)
    done_ms = sum(p.elapsed_ms for p in profiles if p.status == "done")
    blocked_ms = sum(p.elapsed_ms for p in profiles if p.status in ("stuck", "blocked"))

    if total_ms > 0:
        waste_pct = (blocked_ms / total_ms * 100)
        if waste_pct > 30:
            findings.append(
                f"{waste_pct:.0f}% of wall-clock time spent on blocked steps "
                f"({blocked_ms // 1000}s / {total_ms // 1000}s)"
            )
            action = "Too much time on blocked steps — improve decomposition or constraint tuning"

    # Check for steps that took > 2 minutes
    slow_steps = [p for p in profiles if p.elapsed_ms > 120000]
    if slow_steps:
        for p in slow_steps:
            findings.append(
                f"Step {p.step_idx} took {p.elapsed_ms // 1000}s: {p.text[:60]}"
            )
        if action is None:
            action = "Break slow steps into smaller units (target < 60s each)"

    # Progress rate: done steps per minute
    if total_ms > 60000:
        done_count = sum(1 for p in profiles if p.status == "done")
        rate = done_count / (total_ms / 60000)
        findings.append(f"Progress rate: {rate:.1f} steps/min ({done_count} done in {total_ms // 1000}s)")

    confidence = 0.8 if action else 0.3
    return LensResult(lens_name="operator", findings=findings, action=action, confidence=confidence)


def _forensics_lens(diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
    """What exact event caused failure? What was the last good state?"""
    findings: List[str] = []
    action = None

    if not profiles:
        return LensResult(lens_name="forensics")

    # Find the transition from done → blocked
    last_done_idx = -1
    first_block_idx = -1
    for i, p in enumerate(profiles):
        if p.status == "done":
            last_done_idx = i
        elif p.status in ("stuck", "blocked") and first_block_idx == -1:
            first_block_idx = i

    if last_done_idx >= 0 and first_block_idx >= 0:
        findings.append(
            f"Last successful step: #{profiles[last_done_idx].step_idx} "
            f"({profiles[last_done_idx].text[:60]})"
        )
        findings.append(
            f"First failure: #{profiles[first_block_idx].step_idx} "
            f"({profiles[first_block_idx].text[:60]})"
        )

    # Token delta at failure point
    if first_block_idx > 0:
        pre_tokens = sum(p.tokens for p in profiles[:first_block_idx])
        fail_tokens = profiles[first_block_idx].tokens
        if fail_tokens == 0:
            findings.append("Failure step consumed 0 tokens — blocked before LLM call")
            action = "Check constraint/policy layer or adapter resolution"
        elif fail_tokens > pre_tokens:
            findings.append(
                f"Failure step token jump: {pre_tokens:,} → {fail_tokens:,} "
                f"({fail_tokens / max(pre_tokens, 1):.1f}x)"
            )

    # Check if all blocked steps share a pattern
    blocked = [p for p in profiles if p.status in ("stuck", "blocked")]
    if len(blocked) >= 2:
        # Simple: check if all blocked step texts share common words
        word_sets = [set(p.text.lower().split()) for p in blocked]
        common = word_sets[0]
        for ws in word_sets[1:]:
            common &= ws
        common -= {"the", "a", "an", "and", "or", "to", "in", "of", "for", "step"}
        if len(common) >= 2:
            findings.append(f"Blocked steps share keywords: {', '.join(sorted(common)[:5])}")

    confidence = 0.9 if action else 0.5
    return LensResult(lens_name="forensics", findings=findings, action=action, confidence=confidence)


# ---------------------------------------------------------------------------
# Default lens registry
# ---------------------------------------------------------------------------

def _execution_lens(diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
    """Wraps the failure classifier as a lens — is the run making progress?"""
    findings: List[str] = []
    action = None

    if diag.failure_class != "healthy":
        findings.extend(diag.evidence)
        action = diag.recommendation

    if diag.steps_blocked > 0 and diag.steps_done > 0:
        ratio = diag.steps_blocked / (diag.steps_done + diag.steps_blocked)
        if ratio > 0.3:
            findings.append(f"Block rate: {ratio:.0%} ({diag.steps_blocked}/{diag.steps_total})")

    confidence = 0.9 if action else 0.3
    return LensResult(
        lens_name="execution",
        findings=findings,
        action=action,
        confidence=confidence,
    )


def _quality_lens(diag: LoopDiagnosis, profiles: List[StepProfile]) -> LensResult:
    """LLM-backed: does the output actually answer the goal? Uses reviewer persona.

    Only runs when explicitly requested (include_llm=True). Uses the cheapest
    model available to minimize token cost.
    """
    findings: List[str] = []
    action = None

    # Need done steps with actual content to evaluate
    done_profiles = [p for p in profiles if p.status == "done" and p.tokens > 0]
    if not done_profiles or not diag.steps_done:
        return LensResult(lens_name="quality", cost="cheap")

    # Load step artifacts for quality assessment
    step_summaries = []
    for p in done_profiles[:8]:  # Cap at 8 to control token usage
        step_summaries.append(f"Step {p.step_idx}: {p.text[:80]}")

    try:
        from llm import build_adapter, MODEL_CHEAP, LLMMessage
        adapter = build_adapter(model=MODEL_CHEAP)

        prompt = (
            f"Goal: {diag.loop_id}\n\n"  # loop_id isn't the goal — we'd need it passed in
            f"Completed {diag.steps_done}/{diag.steps_total} steps:\n"
            + "\n".join(f"  {s}" for s in step_summaries)
            + "\n\nBriefly assess:\n"
            "1. Did the steps address the goal or drift off-target?\n"
            "2. Is the output substantive or superficial?\n"
            "3. What's the single most important gap?\n"
            "Answer in 3 short bullet points."
        )
        resp = adapter.complete(
            [LLMMessage("user", prompt)],
            max_tokens=300,
            temperature=0.2,
        )
        if resp.content and len(resp.content) > 20:
            findings.append(resp.content.strip())
            # Check for obvious negative signals
            _lower = resp.content.lower()
            if any(w in _lower for w in ["drift", "off-target", "superficial", "shallow", "missed"]):
                action = "Output quality concern — review step results before accepting"
    except Exception as exc:
        log.debug("quality lens LLM call failed: %s", exc)
        findings.append(f"Quality assessment unavailable: {exc}")

    confidence = 0.6 if action else 0.4
    return LensResult(lens_name="quality", findings=findings, action=action, confidence=confidence, cost="cheap")


# ---------------------------------------------------------------------------
# Lens Aggregator — synthesize across lens results
# ---------------------------------------------------------------------------

@dataclass
class AggregatedDiagnosis:
    """Synthesized output from multiple lenses."""
    loop_id: str
    failure_class: str                  # from ExecutionLens / diagnose_loop
    severity: str
    confidence: float                   # aggregated confidence
    primary_action: str                 # highest-confidence recommended action
    supporting_evidence: List[str]      # merged evidence from all lenses
    lens_agreement: int                 # how many lenses suggest the same action category
    all_actions: List[str]              # every unique action recommended

    def summary(self) -> str:
        return (
            f"[{self.failure_class}] confidence={self.confidence:.2f} "
            f"agreement={self.lens_agreement} lenses | {self.primary_action}"
        )


def aggregate_lenses(
    diag: LoopDiagnosis,
    lens_results: List[LensResult],
) -> AggregatedDiagnosis:
    """Synthesize multiple lens results into a single diagnosis + action.

    Confidence is boosted when multiple lenses agree on the same action category.
    The highest-confidence action with the most lens agreement wins.
    """
    all_actions = []
    all_evidence = list(diag.evidence)

    for lr in lens_results:
        all_evidence.extend(lr.findings)
        if lr.action:
            all_actions.append((lr.action, lr.confidence, lr.lens_name))

    if not all_actions:
        return AggregatedDiagnosis(
            loop_id=diag.loop_id,
            failure_class=diag.failure_class,
            severity=diag.severity,
            confidence=0.3,
            primary_action=diag.recommendation or "No action recommended",
            supporting_evidence=all_evidence,
            lens_agreement=0,
            all_actions=[],
        )

    # Group actions by verb (first meaningful word) — "split X" and "split Y" are the same category
    def _action_key(a: str) -> str:
        words = [w for w in a.lower().split() if w not in {"the", "a", "an", "to", "into", "for"}]
        return words[0] if words else ""

    categories: Dict[str, List[tuple]] = {}
    for action, conf, lens in all_actions:
        key = _action_key(action)
        categories.setdefault(key, []).append((action, conf, lens))

    # Pick the category with the most agreement, breaking ties by confidence
    best_cat = max(categories.values(), key=lambda v: (len(v), max(c for _, c, _ in v)))
    primary_action = max(best_cat, key=lambda x: x[1])[0]
    agreement = len(best_cat)
    avg_conf = sum(c for _, c, _ in best_cat) / len(best_cat)

    # Boost confidence based on agreement
    boosted_conf = min(1.0, avg_conf + (agreement - 1) * 0.1)

    return AggregatedDiagnosis(
        loop_id=diag.loop_id,
        failure_class=diag.failure_class,
        severity=diag.severity,
        confidence=round(boosted_conf, 2),
        primary_action=primary_action,
        supporting_evidence=all_evidence[:20],  # cap for sanity
        lens_agreement=agreement,
        all_actions=[a for a, _, _ in all_actions],
    )


# ---------------------------------------------------------------------------
# Default lens registry
# ---------------------------------------------------------------------------

_default_registry: Optional[LensRegistry] = None

def get_lens_registry() -> LensRegistry:
    """Get or create the default lens registry with built-in lenses."""
    global _default_registry
    if _default_registry is None:
        _default_registry = LensRegistry()
        _default_registry.register("execution", _execution_lens, cost="free")
        _default_registry.register("cost", _cost_lens, cost="free")
        _default_registry.register("architecture", _architecture_lens, cost="free")
        _default_registry.register("operator", _operator_lens, cost="free")
        _default_registry.register("forensics", _forensics_lens, cost="free")
        _default_registry.register("quality", _quality_lens, cost="cheap")
    return _default_registry


def run_lenses(
    diag: LoopDiagnosis,
    profiles: List[StepProfile],
    *,
    include_llm: bool = False,
) -> List[LensResult]:
    """Run all applicable lenses on a diagnosis.

    Args:
        diag: The LoopDiagnosis from diagnose_loop()
        profiles: Step profiles from the same loop
        include_llm: If True, also run LLM-based lenses (costs tokens)

    Returns list of LensResult, one per lens that had findings.
    """
    registry = get_lens_registry()
    if include_llm:
        results = registry.run_all(diag, profiles)
    else:
        results = registry.run_heuristic(diag, profiles)

    # Filter to lenses that found something
    active = [r for r in results if r.findings]
    for r in active:
        log.info("lens %s: %d findings, action=%s confidence=%.1f",
                 r.lens_name, len(r.findings), r.action is not None, r.confidence)
    return active


# ---------------------------------------------------------------------------
# Phase 45: Recovery Planner — decision table for mechanical interventions
# ---------------------------------------------------------------------------

@dataclass
class RecoveryPlan:
    """A mechanical intervention the system can apply without human review."""
    failure_class: str
    action: str                         # human-readable description
    auto_apply: bool                    # True = system can do this itself
    risk: str                           # "low" | "medium" | "high"
    params: Dict[str, Any] = field(default_factory=dict)  # action-specific parameters


# Decision table: failure_class → recovery action
# Ordered by preference (cheapest/safest first)
_RECOVERY_TABLE: Dict[str, List[RecoveryPlan]] = {
    "decomposition_too_broad": [
        RecoveryPlan(
            failure_class="decomposition_too_broad",
            action="Re-run with tighter max_steps and code-surface cap",
            auto_apply=True, risk="low",
            params={"max_steps": 12, "hint": "limit to 3-5 files per step"},
        ),
    ],
    "constraint_false_positive": [
        RecoveryPlan(
            failure_class="constraint_false_positive",
            action="Retry blocked steps — constraint patterns may have been updated",
            auto_apply=True, risk="low",
            params={"retry_from": "first_blocked"},
        ),
    ],
    "adapter_timeout": [
        RecoveryPlan(
            failure_class="adapter_timeout",
            action="Retry with smaller step scope or switch to API adapter",
            auto_apply=False, risk="medium",
            params={"suggestion": "reduce step scope or use ANTHROPIC_API_KEY adapter"},
        ),
    ],
    "budget_exhaustion": [
        RecoveryPlan(
            failure_class="budget_exhaustion",
            action="Increase max_iterations and enable budget-aware landing",
            auto_apply=True, risk="low",
            params={"max_iterations": 60},
        ),
    ],
    "empty_model_output": [
        RecoveryPlan(
            failure_class="empty_model_output",
            action="Retry with explicit tool-call instruction in step text",
            auto_apply=True, risk="low",
            params={"hint": "You MUST call complete_step or flag_stuck. Do not return bare text."},
        ),
    ],
    "token_explosion": [
        RecoveryPlan(
            failure_class="token_explosion",
            action="Truncate completed_context to summaries and re-run from explosion point",
            auto_apply=False, risk="medium",
            params={"suggestion": "cap completed_context entries at 200 chars"},
        ),
    ],
    "retry_churn": [
        RecoveryPlan(
            failure_class="retry_churn",
            action="Skip the churning step and continue with remaining steps",
            auto_apply=True, risk="low",
            params={"action": "skip_and_continue"},
        ),
    ],
    "setup_failure": [
        RecoveryPlan(
            failure_class="setup_failure",
            action="Check adapter resolution and import chain; surface real exception",
            auto_apply=False, risk="medium",
            params={"suggestion": "run with POE_LOG_LEVEL=DEBUG to see the swallowed error"},
        ),
    ],
    "integration_drift": [
        RecoveryPlan(
            failure_class="integration_drift",
            action="Audit import names against actual module exports",
            auto_apply=False, risk="medium",
            params={"suggestion": "run the AST cross-check from the test tightening session"},
        ),
    ],
    "artifact_missing": [
        RecoveryPlan(
            failure_class="artifact_missing",
            action="Re-run with explicit artifact instruction in step hints",
            auto_apply=True, risk="low",
            params={"hint": "You MUST produce a concrete artifact (file, summary, or structured data). Do not end a step with only status text."},
        ),
    ],
}


def plan_recovery(diag: LoopDiagnosis) -> Optional[RecoveryPlan]:
    """Given a diagnosis, return the best recovery plan (if any).

    Returns the first applicable plan from the decision table.
    Plans with auto_apply=True are safe for the system to execute without
    human review. Plans with auto_apply=False should be surfaced as
    suggestions.
    """
    plans = _RECOVERY_TABLE.get(diag.failure_class, [])
    if not plans:
        return None
    return plans[0]


def plan_recovery_all(diag: LoopDiagnosis) -> List[RecoveryPlan]:
    """Return all applicable recovery plans for a diagnosis."""
    return list(_RECOVERY_TABLE.get(diag.failure_class, []))


# ---------------------------------------------------------------------------
# Phase 46: Intervention Graduation — track recurring patterns
# ---------------------------------------------------------------------------

@dataclass
class RecurringPattern:
    """A failure class that appears repeatedly, suggesting a durable fix is needed."""
    failure_class: str
    occurrences: int
    first_seen: str                     # loop_id of first occurrence
    last_seen: str                      # loop_id of most recent
    recovery_action: Optional[str]      # from recovery planner
    graduation_candidate: bool          # True if occurrences >= 3


def find_recurring_patterns(min_occurrences: int = 3, limit: int = 50) -> List[RecurringPattern]:
    """Scan diagnosis history for recurring failure classes.

    Returns patterns that appear >= min_occurrences times, sorted by frequency.
    These are candidates for graduation into permanent rules or guardrails.

    Pure history scan — no LLM calls.
    """
    diagnoses = load_diagnoses(limit=limit)
    if not diagnoses:
        return []

    # Count failure classes (skip healthy)
    counts: Dict[str, List[LoopDiagnosis]] = {}
    for d in diagnoses:
        if d.failure_class == "healthy":
            continue
        counts.setdefault(d.failure_class, []).append(d)

    patterns = []
    for fc, diags in sorted(counts.items(), key=lambda x: -len(x[1])):
        if len(diags) < min_occurrences:
            continue
        recovery = plan_recovery(diags[0])
        patterns.append(RecurringPattern(
            failure_class=fc,
            occurrences=len(diags),
            first_seen=diags[-1].loop_id,  # diagnoses are reverse-chronological
            last_seen=diags[0].loop_id,
            recovery_action=recovery.action if recovery else None,
            graduation_candidate=len(diags) >= min_occurrences,
        ))

    log.info("recurring_patterns: %d patterns with %d+ occurrences",
             len(patterns), min_occurrences)
    return patterns


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
    parser.add_argument("--lenses", action="store_true", help="Also run multi-lens analysis")
    parser.add_argument("--history", type=int, metavar="N", help="Show last N diagnoses")
    parser.add_argument("--patterns", action="store_true", help="Show recurring failure patterns (graduation candidates)")

    args = parser.parse_args(argv)

    if args.patterns:
        patterns = find_recurring_patterns()
        if not patterns:
            print("No recurring failure patterns found (need 3+ occurrences of the same failure class).")
        else:
            print(f"{len(patterns)} recurring pattern(s):")
            for p in patterns:
                _grad = " ** GRADUATION CANDIDATE" if p.graduation_candidate else ""
                print(f"\n  {p.failure_class}  ({p.occurrences}x){_grad}")
                print(f"    first: {p.first_seen[:8]}  last: {p.last_seen[:8]}")
                if p.recovery_action:
                    print(f"    recovery: {p.recovery_action}")
        return

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

    # Multi-lens analysis
    if getattr(args, "lenses", False):
        events = _load_loop_events(diag.loop_id)
        profiles = _build_step_profiles(events)
        lens_results = run_lenses(diag, profiles, include_llm=getattr(args, "llm", False))

        if lens_results:
            print(f"\n  Lens analysis ({len(lens_results)} active):")
            for lr in lens_results:
                _conf = f"confidence={lr.confidence:.1f}" if lr.confidence > 0 else ""
                _cost = f" [{lr.cost}]" if lr.cost != "free" else ""
                print(f"\n  [{lr.lens_name}]{_cost} {_conf}")
                for f in lr.findings:
                    print(f"    {f}")
                if lr.action:
                    print(f"    -> {lr.action}")

            # Aggregated synthesis
            agg = aggregate_lenses(diag, lens_results)
            print(f"\n  Synthesis:")
            print(f"    Confidence: {agg.confidence:.0%}")
            print(f"    Agreement:  {agg.lens_agreement} lens(es) converge")
            print(f"    Action:     {agg.primary_action}")
        else:
            print(f"\n  Lens analysis: no notable findings")

    # Recovery plan
    if diag.failure_class != "healthy":
        recovery = plan_recovery(diag)
        if recovery:
            _auto = "AUTO" if recovery.auto_apply else "SUGGEST"
            print(f"\n  Recovery plan [{_auto}] (risk={recovery.risk}):")
            print(f"    {recovery.action}")
            if recovery.params:
                for k, v in recovery.params.items():
                    print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
