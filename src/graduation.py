"""Phase 46: Self-Reflection — Intervention Graduation.

Scans recent diagnoses for repeated failure patterns. When the same
failure class appears 3+ times (default), proposes a permanent rule as
a high-confidence suggestion that the evolver will auto-apply.

This closes the full self-reflection loop:
  observe (Phase 44) → classify → recover (Phase 45) → graduate (Phase 46)

Usage:
    from graduation import run_graduation
    count = run_graduation()                  # produces new suggestions if patterns found
    count = run_graduation(dry_run=True)      # scan only, no writes
    candidates = scan_candidates(min_count=2) # inspect what would fire
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("poe.graduation")


# ---------------------------------------------------------------------------
# Graduation templates — heuristic rules per failure class
# ---------------------------------------------------------------------------

_GRADUATION_TEMPLATES: Dict[str, dict] = {
    "adapter_timeout": {
        "category": "observation",
        "suggestion": (
            "adapter_timeout is a recurring failure ({count}x across loops {loop_ids}). "
            "Permanent fix: increase default ClaudeSubprocessAdapter timeout to 600s, "
            "or route long-running goal types to API adapter. Consider adding "
            "'--step-timeout 600' as a user/CONFIG.md default."
        ),
        "confidence": 0.75,
        # Verify that a >=600s timeout is present somewhere in the config or llm.py
        "verify_pattern": "grep -rn 'step.timeout.*600\\|timeout.*600\\|600.*timeout' src/ user/ 2>/dev/null | head -1",
    },
    "constraint_false_positive": {
        "category": "new_guardrail",
        "suggestion": (
            "Constraint false positives detected {count}x (loops {loop_ids}). "
            "Natural-language steps with action words are being blocked unnecessarily. "
            "Add to constraint allowlist: steps that contain action words but have "
            "no explicit system path or irreversible target should be tier READ, not DESTROY. "
            "Evidence: {evidence}"
        ),
        "confidence": 0.85,
        # Verify allowlist entry exists in constraint.py
        "verify_pattern": "grep -n 'allowlist\\|_ALLOWLIST\\|tier.*READ' src/constraint.py 2>/dev/null | head -1",
    },
    "decomposition_too_broad": {
        "category": "prompt_tweak",
        "suggestion": (
            "decomposition_too_broad detected {count}x (loops {loop_ids}). "
            "Steps are consistently exceeding 200K tokens or 120s. "
            "Add permanent decompose hint to Director system prompt: "
            "'Research steps must be scoped to a single source or claim cluster. "
            "Never bundle multiple research questions into one step.' "
            "Evidence: {evidence}"
        ),
        "confidence": 0.80,
        # Verify 'single source' guidance is in director.py
        "verify_pattern": "grep -n 'single source\\|single.*source\\|one step' src/director.py 2>/dev/null | head -1",
    },
    "token_explosion": {
        "category": "prompt_tweak",
        "suggestion": (
            "token_explosion detected {count}x (loops {loop_ids}): token growth > 3x "
            "between consecutive steps. Add to EXECUTE_SYSTEM: explicitly cap intermediate "
            "context storage. Completed context should summarize, not quote. "
            "Evidence: {evidence}"
        ),
        "confidence": 0.80,
        # Verify token cap instruction in step_exec.py
        "verify_pattern": "grep -n 'under 500\\|500 tokens\\|Target.*token' src/step_exec.py 2>/dev/null | head -1",
    },
    "empty_model_output": {
        "category": "new_guardrail",
        "suggestion": (
            "empty_model_output detected {count}x (loops {loop_ids}). "
            "Model returns tokens but no tool call and content < 20 chars. "
            "Add permanent guardrail: on empty output, immediately inject refinement hint "
            "rather than waiting for the second retry cycle. "
            "Evidence: {evidence}"
        ),
        "confidence": 0.75,
        # Verify early empty-output hint injection exists
        "verify_pattern": "grep -n 'empty.*hint\\|hint.*empty\\|empty_output\\|no.*tool.*call' src/step_exec.py src/agent_loop.py 2>/dev/null | head -1",
    },
    "retry_churn": {
        "category": "observation",
        "suggestion": (
            "retry_churn detected {count}x (loops {loop_ids}): same step retried 2+ times "
            "with different block reasons — a sign the step decomposition is ambiguous. "
            "Increase max_retries to 3 and add generate_refinement_hint() call on first churn "
            "rather than second. Evidence: {evidence}"
        ),
        "confidence": 0.70,
        # Verify max_retries is >= 3 somewhere in the loop config
        "verify_pattern": "grep -n 'max_retries.*[3-9]\\|MAX_RETRIES.*[3-9]' src/agent_loop.py src/step_exec.py 2>/dev/null | head -1",
    },
    "budget_exhaustion": {
        "category": "prompt_tweak",
        "suggestion": (
            "budget_exhaustion detected {count}x (loops {loop_ids}): max_iterations reached "
            "with remaining steps undone. Director is over-decomposing. Add to decompose "
            "prompt: 'Target 4-6 steps unless the goal explicitly requires more. "
            "Fewer, broader steps are better than many narrow steps.' Evidence: {evidence}"
        ),
        "confidence": 0.75,
        # Verify step-count guidance in director.py
        "verify_pattern": "grep -n '4-6 steps\\|4 to 6\\|fewer.*steps\\|Fewer.*steps' src/director.py 2>/dev/null | head -1",
    },
    "integration_drift": {
        "category": "observation",
        "suggestion": (
            "integration_drift detected {count}x (loops {loop_ids}): ImportError or "
            "AttributeError caught during execution. An internal module API changed "
            "without updating callers. Consider adding a startup self-test (doctor check) "
            "that validates imports before beginning a loop. Evidence: {evidence}"
        ),
        "confidence": 0.70,
        # Verify doctor check is wired at loop start
        "verify_pattern": "grep -n 'doctor\\|validate_imports\\|self.test' src/agent_loop.py src/handle.py 2>/dev/null | head -1",
    },
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GraduationCandidate:
    failure_class: str
    count: int
    loop_ids: List[str] = field(default_factory=list)
    evidence_samples: List[str] = field(default_factory=list)  # up to 3 evidence strings


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _diagnoses_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "diagnoses.jsonl"
    except Exception:
        return Path.cwd() / "memory" / "diagnoses.jsonl"


def _suggestions_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "suggestions.jsonl"
    except Exception:
        return Path.cwd() / "memory" / "suggestions.jsonl"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def scan_candidates(min_count: int = 3, lookback: int = 100) -> List[GraduationCandidate]:
    """Scan recent diagnoses for repeated failure classes.

    Returns candidates where count >= min_count, ordered by count descending.
    Excludes 'healthy' (not a failure) and patterns for which we have no template.
    """
    path = _diagnoses_path()
    if not path.exists():
        return []

    counts: Dict[str, List[dict]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-lookback:]:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                fc = d.get("failure_class", "")
                if fc and fc != "healthy" and fc in _GRADUATION_TEMPLATES:
                    if fc not in counts:
                        counts[fc] = []
                    counts[fc].append(d)
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception as exc:
        log.debug("scan_candidates: failed to read diagnoses: %s", exc)
        return []

    candidates = []
    for fc, diags in counts.items():
        if len(diags) < min_count:
            continue
        loop_ids = [d.get("loop_id", "?") for d in diags[-5:]]  # most recent 5
        # collect evidence samples (up to 3 unique evidence strings)
        evidence = []
        seen = set()
        for d in diags:
            for e in d.get("evidence", []):
                if e not in seen:
                    evidence.append(e)
                    seen.add(e)
                if len(evidence) >= 3:
                    break
            if len(evidence) >= 3:
                break
        candidates.append(GraduationCandidate(
            failure_class=fc,
            count=len(diags),
            loop_ids=loop_ids,
            evidence_samples=evidence,
        ))

    return sorted(candidates, key=lambda c: c.count, reverse=True)


def _already_proposed(failure_class: str, lookback: int = 200) -> bool:
    """Check whether we've already proposed a graduation suggestion for this failure class."""
    path = _suggestions_path()
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-lookback:]:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                fp = d.get("failure_pattern", "")
                cat = d.get("category", "")
                # graduation suggestions are tagged with "graduation:" in failure_pattern
                if f"graduation:{failure_class}" in fp:
                    return True
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:
        pass
    return False


def run_graduation(
    min_count: int = 3,
    lookback: int = 100,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Scan diagnoses and propose graduation suggestions for repeated failures.

    Each unique failure class that has appeared >= min_count times (and hasn't
    already been proposed) gets a new high-confidence Suggestion written to
    suggestions.jsonl. The evolver picks these up and auto-applies on the next run.

    Returns: number of new suggestions written (0 on dry_run).
    """
    run_id = uuid.uuid4().hex[:8]
    candidates = scan_candidates(min_count=min_count, lookback=lookback)

    if not candidates:
        log.debug("graduation: no candidates (min_count=%d)", min_count)
        return 0

    new_suggestions = []
    for candidate in candidates:
        fc = candidate.failure_class
        if _already_proposed(fc):
            log.debug("graduation: %s already proposed, skipping", fc)
            if verbose:
                print(f"[graduation] {fc}: already proposed, skipping", flush=True)
            continue

        template = _GRADUATION_TEMPLATES.get(fc)
        if not template:
            continue

        evidence_str = "; ".join(candidate.evidence_samples[:2]) or "no specific evidence"
        loop_ids_str = ", ".join(candidate.loop_ids[-3:])

        suggestion_text = template["suggestion"].format(
            count=candidate.count,
            loop_ids=loop_ids_str,
            evidence=evidence_str[:200],
        )

        entry: dict = {
            "suggestion_id": f"grad-{run_id}-{fc[:12]}",
            "category": template["category"],
            "target": "all",
            "suggestion": suggestion_text[:500],
            "failure_pattern": f"graduation:{fc}",
            "confidence": template["confidence"],
            "outcomes_analyzed": candidate.count,
            "generated_at": _now_iso(),
            "applied": False,
        }
        if template.get("verify_pattern"):
            entry["verify_pattern"] = template["verify_pattern"]
        new_suggestions.append(entry)

        log.info("graduation: new candidate fc=%s count=%d confidence=%.2f",
                 fc, candidate.count, template["confidence"])
        if verbose:
            print(f"[graduation] new: {fc} ({candidate.count}x) → {template['category']} "
                  f"confidence={template['confidence']}", flush=True)

    if not new_suggestions:
        return 0

    if dry_run:
        if verbose:
            print(f"[graduation] dry_run: would write {len(new_suggestions)} suggestions", flush=True)
        return 0

    path = _suggestions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for s in new_suggestions:
                f.write(json.dumps(s) + "\n")
        log.info("graduation: wrote %d suggestions to %s", len(new_suggestions), path)
        # Captain's log
        try:
            from captains_log import log_event, GRADUATION_PROPOSED
            for s in new_suggestions:
                log_event(
                    event_type=GRADUATION_PROPOSED,
                    subject=s.get("failure_pattern", ""),
                    summary=f"Graduation proposed: {s['suggestion'][:120]}",
                    context={"category": s["category"], "confidence": s["confidence"]},
                )
        except Exception:
            pass
    except Exception as exc:
        log.warning("graduation: failed to write suggestions: %s", exc)
        return 0

    return len(new_suggestions)


def verify_graduation_rules(lookback: int = 200) -> List[dict]:
    """Run verify_pattern for each applied graduation suggestion.

    For each graduation suggestion in suggestions.jsonl that has a verify_pattern,
    run the pattern as a shell command from the repo root. Return a list of
    verification results: {"failure_class", "verify_pattern", "passed", "output"}.

    Passed = exit code 0 AND non-empty stdout (the pattern found something).
    """
    import subprocess
    import re

    path = _suggestions_path()
    if not path.exists():
        return []

    results = []
    seen: set = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-lookback:]:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            fp = d.get("failure_pattern", "")
            verify_pattern = d.get("verify_pattern", "")
            if not verify_pattern or not fp.startswith("graduation:"):
                continue
            fc = fp[len("graduation:"):]
            if fc in seen:
                continue
            seen.add(fc)

            # Run the verify pattern from repo root
            try:
                _repo_root = str(Path(__file__).parent.parent)
                proc = subprocess.run(
                    verify_pattern,
                    shell=True,
                    cwd=_repo_root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                passed = proc.returncode == 0 and bool(proc.stdout.strip())
                output = proc.stdout.strip()[:200] or proc.stderr.strip()[:100]
            except Exception as exc:
                passed = False
                output = str(exc)[:100]

            results.append({
                "failure_class": fc,
                "verify_pattern": verify_pattern,
                "passed": passed,
                "output": output,
            })

    except Exception as exc:
        log.debug("verify_graduation_rules: error reading suggestions: %s", exc)

    return results


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Phase 46: intervention graduation scanner")
    p.add_argument("--min-count", type=int, default=3,
                   help="How many occurrences to trigger graduation (default: 3)")
    p.add_argument("--lookback", type=int, default=100,
                   help="How many recent diagnoses to scan (default: 100)")
    p.add_argument("--dry-run", action="store_true",
                   help="Scan only, do not write suggestions")
    p.add_argument("--verify", action="store_true",
                   help="Run verify_pattern for each graduated rule and report pass/fail")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    if args.verify:
        print("Verifying graduated rules (running verify_pattern for each):")
        vresults = verify_graduation_rules()
        if not vresults:
            print("  (no graduated rules with verify_pattern found)")
        for vr in vresults:
            icon = "PASS" if vr["passed"] else "FAIL"
            print(f"  [{icon}] {vr['failure_class']}")
            if vr["output"]:
                print(f"         → {vr['output']}")
        pass_count = sum(1 for v in vresults if v["passed"])
        print(f"\n{pass_count}/{len(vresults)} verified rules passing")
        return

    candidates = scan_candidates(min_count=args.min_count, lookback=args.lookback)
    print(f"Graduation candidates (min_count={args.min_count}, lookback={args.lookback}):")
    if not candidates:
        print("  (none)")
    for c in candidates:
        already = _already_proposed(c.failure_class)
        tag = " [already proposed]" if already else ""
        print(f"  {c.failure_class}: {c.count}x — loops {', '.join(c.loop_ids[-3:])}{tag}")

    n = run_graduation(
        min_count=args.min_count,
        lookback=args.lookback,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    if not args.dry_run:
        print(f"\nWrote {n} new graduation suggestion(s).")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    main()
