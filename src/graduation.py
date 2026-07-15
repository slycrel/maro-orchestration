"""Phase 46: Self-Reflection — Intervention Graduation.

Scans recent diagnoses for repeated failure patterns. When the same
failure class appears 3+ times (default), writes a pending suggestion for
human review/application. Graduation rules stay advisor-gated (a human
applies them via ``maro evolver apply``) per the VERIFY_LEARN_ARC V3 owner
call — nothing here auto-applies a standing rule.

This module's ``verify_pattern`` check is structural observability only (is
the recommended code-level fix present?) — it never drives a verdict, because
for observation/prompt_tweak graduations "apply" records a lesson, not a code
edit, so a grep miss does not mean the change failed. The *behavioral* verdict
+ demote for an applied graduation row lives in VERIFY_LEARN_ARC V2/V3
(``evolver_scans.verify_applied_suggestions``), which since V3 verdicts it on
its own ``expected_signal`` (per-class failure_class_rate).

This closes the full self-reflection loop:
  observe (Phase 44) → classify → recover (Phase 45) → graduate (Phase 46)

Usage:
    from graduation import run_graduation
    count = run_graduation()                  # produces pending suggestions if patterns found
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
from typing import Dict, List

log = logging.getLogger("maro.graduation")


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
    "cost_spike": {
        "category": "observation",
        "suggestion": (
            "cost_spike detected {count}x (loops {loop_ids}): a single step or the whole "
            "loop exceeded the cache-aware dollar-cost threshold. Unlike token_explosion this "
            "is real spend (fresh tokens priced at full rate, cache reads at 0.1x), so it "
            "survives caching. Likely a pricey model doing genuinely large fresh work — route "
            "the costly step to a cheaper model tier, or split the step so the expensive model "
            "sees less fresh context. Evidence: {evidence}"
        ),
        "confidence": 0.70,
        # Verify model-tier routing / cost guard exists in the routing path
        "verify_pattern": "grep -n 'model_key\\|model_for\\|cheap.*mid.*power\\|estimate_cost' src/director.py src/agent_loop.py 2>/dev/null | head -1",
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

# VERIFY_LEARN_ARC V1: every template's behavioral expectation is "this
# failure class should occur less often once the fix lands" — derived from
# the template's own key so the class name can never drift from
# expected_signal's declaration. Set via setdefault so a future template can
# still declare something more specific (e.g. a second tracked metric)
# without this loop overwriting it.
for _fc, _tmpl in _GRADUATION_TEMPLATES.items():
    _tmpl.setdefault("expected_signal", [
        {"metric": "failure_class_rate", "class": _fc, "direction": "down"},
    ])
del _fc, _tmpl


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


def _verification_state_path() -> Path:
    return _suggestions_path().with_name("graduation-verification-state.json")


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
    suggestions.jsonl. These rows remain pending until a human applies them via
    ``maro evolver apply`` (advisor-gated by owner decision). Once applied they
    flow into the V2/V3 cadence verify→demote loop like any other applied change.

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
        if template.get("expected_signal"):
            entry["expected_signal"] = template["expected_signal"]
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
        from file_lock import locked_write
        with locked_write(path):
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
    path = _suggestions_path()
    if not path.exists():
        return []

    results = []
    seen: set = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        # Newest record wins if history contains more than one row for a
        # failure class. Reverted/held/pending rows must never be described as
        # live rule verification merely because they carry a verify_pattern.
        for line in reversed(lines[-lookback:]):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            fp = d.get("failure_pattern", "")
            verify_pattern = d.get("verify_pattern", "")
            if (
                not verify_pattern
                or not fp.startswith("graduation:")
                or d.get("applied") is not True
            ):
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
                "suggestion_id": d.get("suggestion_id", ""),
                "failure_class": fc,
                "category": d.get("category", ""),
                "applied_manually": bool(d.get("applied_manually", False)),
                "applied_at": d.get("applied_at", ""),
                "verify_pattern": verify_pattern,
                "passed": passed,
                "output": output,
                "structural_only": True,
            })

    except Exception as exc:
        log.debug("verify_graduation_rules: error reading suggestions: %s", exc)

    return results


def run_graduation_verification(
    *, lookback: int = 200, notify: bool = False
) -> List[dict]:
    """Run cheap structural checks for applied graduations at evolver cadence.

    This is the *structural observability* layer only — it emits
    GRADUATION_VERIFIED events (is the recommended code fix present in the
    tree?) and optionally notifies, but never reverts or demotes. That is
    deliberate: the grep is a weak signal for observation/prompt_tweak
    graduations (applying them records a lesson, not a code edit), so it must
    not gate state. The *behavioral* verify + demote for an applied graduation
    row is VERIFY_LEARN_ARC V2/V3's job — ``verify_applied_suggestions`` renders
    the per-class failure_class_rate verdict and, on degrade, surfaces a
    human-applied row for review (never auto-reverts it, symmetric authority).
    """
    results = verify_graduation_rules(lookback=lookback)
    state_path = _verification_state_path()
    if not results and not state_path.exists():
        return results

    # Cadence may be driven by heartbeat and finalization concurrently. Claim
    # each event/notification under the shared lock, deliver outside it, then
    # acknowledge success. Failed delivery clears its claim for the next
    # cadence; a crashed claimant's lease expires after five minutes.
    event_claims: List[tuple] = []
    notify_claims: List[tuple] = []
    claim_token = uuid.uuid4().hex
    now_epoch = time.time()
    identity_keys = ("suggestion_id", "applied_at", "passed")

    try:
        from file_lock import locked_rmw

        def _update_state(old_text: str) -> str:
            try:
                old = json.loads(old_text) if old_text.strip() else {}
                if not isinstance(old, dict):
                    old = {}
            except (json.JSONDecodeError, ValueError):
                old = {}
            current = {}
            for result in results:
                fc = result["failure_class"]
                before = old.get(fc, {})
                identity = {
                    "suggestion_id": result.get("suggestion_id", ""),
                    "applied_at": result.get("applied_at", ""),
                    "passed": bool(result["passed"]),
                }
                same = all(before.get(key) == value for key, value in identity.items())
                after = dict(before) if same else {
                    **identity,
                    "event_delivered": False,
                    "notify_delivered": bool(result["passed"]),
                }
                after["checked_at"] = _now_iso()

                def _claimable(kind: str) -> bool:
                    if after.get(f"{kind}_delivered"):
                        return False
                    claimed_at = float(after.get(f"{kind}_claimed_at", 0) or 0)
                    return not after.get(f"{kind}_claim") or now_epoch - claimed_at >= 300

                if _claimable("event"):
                    after["event_claim"] = claim_token
                    after["event_claimed_at"] = now_epoch
                    event_claims.append((result, claim_token))
                if not result["passed"] and notify and _claimable("notify"):
                    after["notify_claim"] = claim_token
                    after["notify_claimed_at"] = now_epoch
                    notify_claims.append((result, claim_token))
                current[fc] = after
            return json.dumps(current, indent=2, sort_keys=True) + "\n"

        locked_rmw(state_path, _update_state)
    except Exception as exc:
        # Verification results remain available to the caller, but if durable
        # dedup state cannot be established, suppress page/event side effects.
        log.warning("graduation verification state update failed: %s", exc)
        return results

    event_successes = set()
    for result, token in event_claims:
        try:
            from captains_log import log_event, GRADUATION_VERIFIED
            log_event(
                event_type=GRADUATION_VERIFIED,
                subject=f"graduation:{result['failure_class']}",
                summary=(
                    f"Applied graduation structural check "
                    f"{'passed' if result['passed'] else 'failed'}: "
                    f"{result['failure_class']}"
                ),
                context=result,
                raise_on_error=True,
            )
            event_successes.add((result["failure_class"], token))
        except Exception as exc:
            log.warning("graduation verification event delivery failed: %s", exc)

    failures = [result for result, _token in notify_claims]
    notify_successes = set()
    if failures:
        log.warning(
            "graduation structural verification failed for applied rows: %s",
            [result["failure_class"] for result in failures],
        )
        if notify:
            try:
                from telegram_listener import telegram_notify
                lines = [
                    "⚠️ Applied graduation structural check failed "
                    "(not an automatic regression verdict):"
                ]
                lines.extend(
                    f"• {result['failure_class']}: {result['output'][:120]}"
                    for result in failures[:5]
                )
                if telegram_notify("\n".join(lines)):
                    notify_successes.update(
                        (result["failure_class"], token)
                        for result, token in notify_claims
                    )
                else:
                    log.warning("graduation verification notify was not delivered")
            except Exception as exc:
                log.warning("graduation verification notify failed: %s", exc)

    # Acknowledge each successful delivery, and clear failed claims so the next
    # cadence retries. Identity+token checks prevent an old claimant from
    # acknowledging a newer transition.
    try:
        def _ack_state(old_text: str) -> str:
            try:
                state = json.loads(old_text) if old_text.strip() else {}
                if not isinstance(state, dict):
                    return old_text
            except (json.JSONDecodeError, ValueError):
                return old_text
            for result, token in event_claims:
                row = state.get(result["failure_class"], {})
                if row.get("event_claim") == token:
                    row.pop("event_claim", None)
                    row.pop("event_claimed_at", None)
                    if (result["failure_class"], token) in event_successes:
                        row["event_delivered"] = True
            for result, token in notify_claims:
                row = state.get(result["failure_class"], {})
                if row.get("notify_claim") == token:
                    row.pop("notify_claim", None)
                    row.pop("notify_claimed_at", None)
                    if (result["failure_class"], token) in notify_successes:
                        row["notify_delivered"] = True
            return json.dumps(state, indent=2, sort_keys=True) + "\n"

        locked_rmw(state_path, _ack_state)
    except Exception as exc:
        log.warning("graduation verification delivery acknowledgement failed: %s", exc)
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
