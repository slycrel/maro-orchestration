"""Director closure check — goal-level completion verification (Phase 65+).

Extracted from director.py (docs/REFACTOR_PLAN.md Tier 3): the
verify_goal_completion subsystem is self-contained with one caller
(handle.py), so this is a pure move — no behavior change.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from conversation import ConversationChannel

from llm_parse import extract_json, safe_float, safe_str, safe_list, content_or_empty

log = logging.getLogger("maro.closure_verify")

# ---------------------------------------------------------------------------
# Director Closure Check — goal-level completion verification
# ---------------------------------------------------------------------------

_CLOSURE_PLAN_SYSTEM = textwrap.dedent("""\
    You are the Director performing a closure check after an agent loop completed.

    You verify by INVERSION: given the goal and what was done, your job is to probe
    whether any of the ways this work could be silently wrong actually happened.

    How to reason:
    1. If the input includes "failure modes" (generated when planning this goal),
       those are your primary targets. For each failure mode, ask: "what single
       shell command, run right now, would detect whether this actually happened?"
       A failure mode with no mechanical probe is fine to skip — do not fabricate.
    2. If no failure modes are provided, do your own inversion first. Given this
       specific goal and this specific work summary, enumerate 3–5 ways a claim of
       "done" could be hiding a silent failure. Then derive checks from those.
    3. Reason from the actual work done, not from goal type templates. The right
       check for "build a server" depends on whether the work stopped at compiling
       (probe: does it actually respond?), at starting (probe: does it handle a
       real request?), or at integration (probe: does the documented client path
       work?). Let the work steer the check.
    4. Prefer at least one behavioral/runtime probe when the work summary suggests
       a running artifact, service, CLI, endpoint, websocket, or UI flow. Static
       file/grep checks are useful, but they are not enough by themselves if the
       claimed success depends on runtime behavior. If runtime probing is impossible
       here, say that by skipping the check rather than faking a static substitute.
       Cheap scaffolding is encouraged when it makes runtime probing mechanical, for
       example:
       - start a server in background with cleanup: `tmp=$(mktemp); (python app.py >$tmp 2>&1 &) ; pid=$!; trap 'kill $pid' EXIT; sleep 2; curl -fsS http://127.0.0.1:8000/health`
       - probe websocket upgrade: `python server.py >/tmp/s.log 2>&1 & pid=$!; trap 'kill $pid' EXIT; sleep 2; curl -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' http://127.0.0.1:8080/ws | grep '101 Switching Protocols'`
       - exercise a CLI or built binary directly: `./bin/tool --help >/tmp/tool.out && grep -q 'usage' /tmp/tool.out`

    Output rules:
    - Generate 2–5 checks. Each must be a single shell command.
    - Each check MUST name which failure mode (or inversion hypothesis) it probes.
    - Commands must be fast (<15s), safe (read-only or self-cleaning), and exit 0
      on success. Wrap background processes with `timeout` and always clean up PIDs.
    - Prefer robust checks over brittle string-matching theater. If a grep pattern
      would be sensitive to log formatting or harmless wording changes, prefer a
      stronger structural predicate (for example `jq`, exact JSON field checks,
      endpoint status codes, websocket handshakes, process exit codes, or `grep -E`
      patterns that only encode the essential invariant).
    - Working directory provided — use relative paths from there.
    - If the goal produces no executable artifact (research, writing, analysis),
      return an empty list. If a failure mode cannot be mechanically probed in
      this environment (missing port, external service, credential needed), skip
      that failure mode rather than fabricate a weak check.

    Respond with JSON only:
    {"checks": [{"failure_mode": "...", "description": "...", "command": "..."}]}
""").strip()

_CLOSURE_VERDICT_SYSTEM = textwrap.dedent("""\
    You are the Director reviewing verification results after an agent loop completed.

    Given the original goal, the agent's work summary, and the results of executable
    verification checks, decide whether the goal was genuinely achieved.

    Be honest. If checks failed or were skipped, say so. If any probe was
    inconclusive (missing tool, command not found, timeout, probe could not run),
    do not treat that as evidence the goal works — but do not treat it as
    evidence of failure either. An inconclusive probe is missing data: judge
    completeness from the checks that did run. If the passing checks cover the
    goal's deliverables and no check failed, complete=true is the honest
    verdict even with an inconclusive probe in the mix.

    Respond with JSON only:
    {
      "complete": true|false,
      "confidence": 0.0–1.0,
      "gaps": ["specific gap 1", "specific gap 2"],
      "summary": "one or two sentences"
    }
""").strip()


@dataclass
class ClosureVerdict:
    complete: bool
    confidence: float
    gaps: List[str]
    summary: str
    checks_run: int
    checks_passed: int
    inconclusive_count: int = 0


_PRECOND_SENTINELS = frozenset({"none", "n/a", "na", "-", "tbd", "(none)", "null", "nil"})

# Domain-looking prefix: catches Go module paths (github.com/x/y), import paths
# (golang.org/x/term, gopkg.in/yaml.v3), URLs without a scheme. Filesystem paths
# don't have a `<word>.<tld>/` prefix so this disambiguates module-vs-fs.
_DOMAIN_PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]+\.[a-z]{2,}/")


def _classify_precondition(preq: str) -> str:
    """Classify a Deliverable.precondition as 'command', 'path', or 'opaque'.

    - command: single token, no slashes, no spaces, no dots — try shutil.which.
    - path: filesystem-shaped (starts with /, ./, ../, ~, or has a slash but not
      a domain-looking prefix) — try Path.exists.
    - opaque: anything else — can't pre-flight mechanically. Includes:
      * sentinel non-values ("none", "n/a", "-", ...)
      * Go module paths and other domain-prefixed import strings
        (`gorilla/websocket`, `github.com/x/y`, `golang.org/x/term`)
      * URLs (anything containing `://`)
      * port numbers, env-var requirements, free-form notes
    """
    s = (preq or "").strip()
    if not s:
        return "opaque"
    # Sentinel non-values (lowercase compare)
    if s.lower() in _PRECOND_SENTINELS:
        return "opaque"
    # URLs and scheme-prefixed strings
    if "://" in s:
        return "opaque"
    # Domain-looking prefix → import path / module path, not filesystem
    if _DOMAIN_PREFIX_RE.match(s.lower()):
        return "opaque"
    # Two-segment slash-separated tokens that *look* like a Go module
    # (e.g. `gorilla/websocket`, `urfave/cli`) — single slash, both segments
    # are bare lowercase identifiers, no leading ./ or /. Heuristic but covers
    # the common case where the LLM emits a module-style precondition.
    if (
        s.count("/") == 1
        and not s.startswith(("/", "./", "../", "~"))
        and re.match(r"^[a-z0-9][\w.-]*/[a-z0-9][\w.-]*$", s, re.IGNORECASE)
    ):
        return "opaque"
    # Path-shaped: starts with /, ./, ../, ~, or contains a slash
    if s.startswith(("/", "./", "../", "~")) or "/" in s:
        return "path"
    # Command-shaped: single token, no spaces, no dots (dots usually mean a
    # version string, file extension, or similar — not a binary on PATH).
    if " " not in s and "\t" not in s and "." not in s:
        return "command"
    return "opaque"


def _run_precondition_preflight(
    deliverables: list, *, cwd: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Mechanically pre-flight Deliverable.preconditions before closure plan runs.

    For each command-shaped precondition: shutil.which → passed.
    For each path-shaped precondition: Path(cwd or '.')/preq exists → passed.
    Opaque preconditions are skipped (no synthetic check; the LLM still sees
    them in the deliverables block).

    Returns a list of synthetic check results in the same shape as the
    real check_results — so callers can prepend them and the existing
    interpretation pipeline treats them uniformly.
    """
    import shutil
    out: List[Dict[str, Any]] = []
    base = Path(cwd) if cwd else Path.cwd()
    for d in deliverables or []:
        _preqs = getattr(d, "preconditions", None) or []
        _name = getattr(d, "name", "") or "(unnamed deliverable)"
        for preq in _preqs:
            kind = _classify_precondition(preq)
            if kind == "command":
                found = shutil.which(preq)
                passed = found is not None
                stderr = "" if passed else f"command `{preq}` not on PATH"
                exit_code = 0 if passed else 127
                out.append({
                    "description": f"precondition: {preq} (command for {_name})",
                    "command": f"shutil.which({preq!r})",
                    "modality": "preflight",
                    "exit_code": exit_code,
                    "stdout": found or "",
                    "stderr": stderr,
                    "passed": passed,
                    "outcome": _check_outcome(exit_code=exit_code, stderr=stderr),
                })
            elif kind == "path":
                target = (base / preq).resolve() if not Path(preq).is_absolute() else Path(preq)
                passed = target.exists()
                stderr = "" if passed else f"path `{preq}` does not exist"
                exit_code = 0 if passed else 127
                out.append({
                    "description": f"precondition: {preq} (path for {_name})",
                    "command": f"Path({preq!r}).exists",
                    "modality": "preflight",
                    "exit_code": exit_code,
                    "stdout": str(target) if passed else "",
                    "stderr": stderr,
                    "passed": passed,
                    "outcome": _check_outcome(exit_code=exit_code, stderr=stderr),
                })
            # opaque kinds (port numbers, env-var requirements) are not pre-flighted
    return out


def _check_outcome(*, exit_code: int, stderr: str = "") -> str:
    """Classify a closure probe outcome as pass, fail, or inconclusive."""
    if exit_code == 0:
        return "pass"
    err = (stderr or "").lower()
    if exit_code in (-1, 126, 127):
        return "inconclusive"
    if "command not found" in err or "not on path" in err or "no such file or directory" in err:
        return "inconclusive"
    if "timed out" in err or "timeout" in err:
        return "inconclusive"
    return "fail"


def verify_goal_completion(
    goal: str,
    steps: list,
    adapter,
    *,
    workspace_path: str = "",
    channel: Optional["ConversationChannel"] = None,
    dry_run: bool = False,
    timeout_per_check: int = 30,
    scope=None,
    resolved_intent=None,
    diagnosis=None,
    loop_id: str = "",
) -> ClosureVerdict:
    """Director closure check: verify the goal was actually achieved.

    Reasons by INVERSION. When a ScopeSet is supplied, its failure_modes are the
    primary targets for check generation — each check probes whether a named
    failure mode actually occurred. When scope is absent, the LLM does its own
    inversion from the goal and work summary.

    When a ResolvedIntent is supplied, its deliverables list is injected as
    explicit "did we build these?" targets — the watcher half of
    docs/DRIVER_AND_WATCHER.md #4. Each Deliverable.name (with optional
    description and preconditions) is named so the closure plan can
    generate path-existence and behavioral checks against it directly,
    not just against generic failure modes.

    Runs the generated checks mechanically (no LLM judgment on exit codes), then
    asks the director to interpret outcomes and declare completeness.

    Non-fatal — returns complete=True on any error so it never blocks execution.
    Emits 'verification' and 'needs_work' events to channel if provided.
    """
    import subprocess

    # cwd contract (2026-07-02, burn-in batch 1): when the caller doesn't pass
    # workspace_path (repo_path is empty for non-repo goals), checks must run
    # where the executor actually wrote — the run-scoped subprocess cwd
    # (project dir), same ContextVar quality_gate and claim_probe read.
    # Falling back to Maro's launch cwd made every artifact check probe the
    # wrong directory: 3/3 burn-in verdicts were false negatives on work that
    # had fully succeeded.
    if not workspace_path:
        try:
            from llm import get_default_subprocess_cwd
            workspace_path = get_default_subprocess_cwd() or ""
        except Exception:
            pass

    _null = ClosureVerdict(
        complete=True, confidence=0.5, gaps=[],
        summary="Verification skipped.", checks_run=0, checks_passed=0,
        inconclusive_count=0,
    )

    # Emit CLOSURE_VERDICT for any early-exit path so the captain's log always
    # has a record that closure ran (or was skipped and why).  The normal
    # success path at the bottom emits its own richer event; this helper covers
    # the silent early returns where no checks were generated, no results came
    # back, or an unexpected exception was caught.  dry_run / no-adapter are
    # intentional skips and don't need a log entry.
    def _emit_skip(reason: str) -> None:
        try:
            from captains_log import log_event as _le, CLOSURE_VERDICT as _CV
            _le(
                _CV,
                subject="closure_verdict",
                summary=f"Closure skipped ({reason}): verification did not run",
                context={
                    "goal_preview": goal[:200],
                    "complete": True,
                    "confidence": 0.5,
                    "checks_run": 0,
                    "checks_passed": 0,
                    "gap_count": 0,
                    "scope_supplied": scope is not None,
                    "modality_distribution": {},
                    "inconclusive_count": 0,
                    "behavioral_gap_downgrade": "",
                    "diagnosis_failure_class": safe_str(
                        getattr(diagnosis, "failure_class", "")
                    ) if diagnosis is not None else "",
                    "diagnosis_gap_downgrade": "",
                    "commands": [],
                    "summary": "Verification skipped.",
                    "skip_reason": reason,
                },
                loop_id=loop_id or None,
            )
        except Exception:
            pass

    if dry_run or adapter is None:
        return _null

    # Build a compact work summary from step results
    step_summary_parts = []
    for i, s in enumerate(steps or []):
        _res = getattr(s, "result", "") or ""
        _txt = getattr(s, "text", "") or getattr(s, "step_text", "") or ""
        if _res or _txt:
            step_summary_parts.append(f"Step {i+1}: {(_txt or '')[:120]}\nResult: {(_res or '')[:300]}")
    work_summary = "\n\n".join(step_summary_parts[-6:]) if step_summary_parts else "(no step detail available)"

    # Pull scope's failure modes into the plan-call context.
    # Closure verification is inversion against the same possibilities scope
    # enumerated up front — this is the linking point between the two halves.
    _scope_block = ""
    if scope is not None:
        _fm = getattr(scope, "failure_modes", None) or []
        if _fm:
            _scope_block = (
                "Failure modes identified when planning (probe these specifically):\n"
                + "\n".join(f"- {fm}" for fm in _fm)
                + "\n\n"
            )

    # Pull resolved-intent deliverables into the plan-call context.
    # The "did we build these?" half of docs/DRIVER_AND_WATCHER.md #4 —
    # closure now sees the same concrete deliverable map the planner saw,
    # so checks can hit deliverable paths instead of inferring them.
    _deliverables_block = ""
    _preflight_results: List[Dict[str, Any]] = []
    if resolved_intent is not None:
        _deliv = getattr(resolved_intent, "deliverables", None) or []
        if _deliv:
            _lines = []
            for d in _deliv:
                _name = getattr(d, "name", "") or ""
                _desc = getattr(d, "description", "") or ""
                _preq = getattr(d, "preconditions", None) or []
                _line = f"- {_name}"
                if _desc:
                    _line += f": {_desc}"
                if _preq:
                    _line += f" [preconditions: {', '.join(_preq)}]"
                _lines.append(_line)
            _deliverables_block = (
                "Deliverables committed when planning (verify each was built):\n"
                + "\n".join(_lines)
                + "\n\n"
            )
            # Pre-flight: run preconditions before the closure plan executes.
            # A missing precondition (`go` not on PATH, port 8080 unreachable,
            # `./run.sh` not present) means the run could not have actually
            # exercised the deliverable — we want closure to mark this as a
            # gap rather than treat "command not found → exit 127 → check
            # failed" as just another check failure indistinguishable from
            # "the program is wrong." See INTENT_RESOLUTION_DESIGN.md.
            _preflight_results = _run_precondition_preflight(_deliv, cwd=workspace_path or None)

    try:
        from llm import LLMMessage

        # Phase 1: generate verification plan
        plan_resp = adapter.complete(
            [
                LLMMessage("system", _CLOSURE_PLAN_SYSTEM),
                LLMMessage("user",
                    f"Goal: {goal}\n\n"
                    f"Working directory: {workspace_path or '(unspecified)'}\n\n"
                    f"{_scope_block}"
                    f"{_deliverables_block}"
                    f"Work done:\n{work_summary}"
                ),
            ],
            max_tokens=512,
            temperature=0.1,
        )
        plan_data = extract_json(content_or_empty(plan_resp), dict,
                                 log_tag="director.closure_plan")
        checks = safe_list(plan_data.get("checks") if plan_data else None, element_type=dict)

        if not checks:
            # Research/writing goal — no executable checks, skip
            _emit_skip("no_checks_generated")
            return _null

        # Phase 2: run checks mechanically
        check_results = []
        cwd = workspace_path or None
        for check in checks[:5]:
            desc = safe_str(check.get("description", ""))
            cmd = safe_str(check.get("command", ""))
            modality = _classify_probe_modality(cmd)
            if not cmd:
                continue
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=timeout_per_check, cwd=cwd,
                )
                outcome = _check_outcome(exit_code=proc.returncode, stderr=proc.stderr)
                check_results.append({
                    "description": desc,
                    "command": cmd,
                    "modality": modality,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:500],
                    "stderr": proc.stderr[:300],
                    "passed": proc.returncode == 0,
                    "outcome": outcome,
                })
            except subprocess.TimeoutExpired:
                check_results.append({
                    "description": desc, "command": cmd,
                    "modality": modality,
                    "exit_code": -1, "stdout": "", "stderr": "timed out",
                    "passed": False,
                    "outcome": "inconclusive",
                })
            except Exception as exc:
                _stderr = str(exc)
                check_results.append({
                    "description": desc, "command": cmd,
                    "modality": modality,
                    "exit_code": -1, "stdout": "", "stderr": _stderr,
                    "passed": False,
                    "outcome": _check_outcome(exit_code=-1, stderr=_stderr),
                })

        # Prepend pre-flight results so the director sees missing
        # preconditions before it ever interprets the LLM-generated checks.
        # When everything passed at pre-flight there's no need to expose
        # them — keeps the check feed clean for the common case.
        if _preflight_results and any(not r["passed"] for r in _preflight_results):
            check_results = _preflight_results + check_results

        if not check_results:
            _emit_skip("no_check_results")
            return _null

        checks_run = len(check_results)
        checks_passed = sum(1 for r in check_results if r["passed"])
        inconclusive_checks = [r for r in check_results if r.get("outcome") == "inconclusive"]

        # Emit verification progress to channel
        if channel is not None:
            _lines = [f"Director closure check — {checks_passed}/{checks_run} passed"]
            for r in check_results:
                outcome = r.get("outcome", "pass" if r.get("passed") else "fail")
                icon = "✓" if outcome == "pass" else ("?" if outcome == "inconclusive" else "✗")
                _lines.append(f"  {icon} {r['description']} (exit {r['exit_code']})")
                if outcome != "pass" and r["stderr"]:
                    _lines.append(f"    {r['stderr'][:120]}")
            channel.emit("verification", text="\n".join(_lines),
                         checks_run=checks_run, checks_passed=checks_passed)

        # Phase 3: director interprets results
        results_text = json.dumps(check_results, indent=2)
        verdict_resp = adapter.complete(
            [
                LLMMessage("system", _CLOSURE_VERDICT_SYSTEM),
                LLMMessage("user",
                    f"Goal: {goal}\n\n"
                    f"Work done:\n{work_summary}\n\n"
                    f"Verification results:\n{results_text}"
                ),
            ],
            max_tokens=256,
            temperature=0.1,
        )
        verdict_data = extract_json(content_or_empty(verdict_resp), dict,
                                    log_tag="director.closure_verdict")

        if not verdict_data:
            _emit_skip("verdict_parse_failed")
            return _null

        complete = bool(verdict_data.get("complete", True))
        confidence = safe_float(verdict_data.get("confidence"), default=0.7,
                                min_val=0.0, max_val=1.0)
        gaps = [safe_str(g) for g in safe_list(verdict_data.get("gaps")) if g]
        summary = safe_str(verdict_data.get("summary", ""))

        # Build modality distribution now; we use it both for the behavioral-gap
        # downgrade below and for the CLOSURE_VERDICT event at the end.
        modality_dist: Dict[str, int] = {}
        for r in check_results:
            mode = _classify_probe_modality(r.get("command", ""))
            modality_dist[mode] = modality_dist.get(mode, 0) + 1

        # Behavioral-evidence downgrade: when the verdict claims complete=True
        # but the LLM's own summary/gaps admit runtime behavior wasn't exercised
        # AND modality shows zero behavioral probes, flip to complete=False so
        # the existing closure_restart machinery gets a chance to re-run with
        # behavioral expectations. This is self-contradiction detection —
        # reading what the system already generated, not a taxonomy imposed
        # from outside.
        behavioral_gap_reason = _detect_behavioral_gap(
            complete=complete,
            summary=summary,
            gaps=gaps,
            modality_dist=modality_dist,
            scope=scope,
        )
        diagnosis_gap_reason = _detect_diagnosis_gap(
            complete=complete,
            diagnosis=diagnosis,
            modality_dist=modality_dist,
        )
        if behavioral_gap_reason:
            log.warning(
                "closure: downgrading complete=True -> False — behavioral gap: %s",
                behavioral_gap_reason,
            )
            complete = False
            # Confidence must be ≥0.6 for closure_restart to engage.
            if confidence < 0.6:
                confidence = 0.6
            gaps = list(gaps) + [
                f"No behavioral probe exercised the runtime delivery "
                f"(modality={modality_dist}). {behavioral_gap_reason}"
            ]
        if diagnosis_gap_reason:
            log.warning(
                "closure: downgrading complete=True -> False — diagnosis gap: %s",
                diagnosis_gap_reason,
            )
            complete = False
            if confidence < 0.6:
                confidence = 0.6
            gaps = list(gaps) + [
                f"Loop diagnosis and closure disagree: {diagnosis_gap_reason}"
            ]
        if complete and inconclusive_checks and checks_passed == 0:
            # Positive-evidence rule: inconclusive probes can't prove
            # completion, so a verdict resting ONLY on inconclusive evidence
            # is flipped. But when other checks passed, those ARE mechanical
            # proof — an inconclusive probe is missing data (often the
            # verifier's own malformed command or a timeout), not
            # contradiction. Burn-in batch 2 (2026-07-02): the unconditional
            # flip turned two fully-delivered goals into false negatives on
            # 4/5-passed verdicts ("Goal achieved", conf 0.95 → achieved
            # False) because one probe-infra error poisoned the whole run.
            complete = False
            confidence = 0.6
            gaps = list(gaps) + [
                f"{len(inconclusive_checks)} verification probe(s) were inconclusive and cannot be counted as proof of completion"
            ]
            if summary:
                summary = f"{summary} Verification was inconclusive."
            else:
                summary = "Verification was inconclusive."

        verdict = ClosureVerdict(
            complete=complete,
            confidence=confidence,
            gaps=gaps,
            summary=summary,
            checks_run=checks_run,
            checks_passed=checks_passed,
            inconclusive_count=len(inconclusive_checks),
        )

        # Emit needs_work if gaps found
        if not complete and channel is not None:
            gap_text = "\n".join(f"• {g}" for g in gaps) if gaps else "(unspecified)"
            channel.emit("needs_work", text=f"{summary}\n\nGaps:\n{gap_text}")

        log.info(
            "closure check: complete=%s confidence=%.2f checks=%d/%d gaps=%d",
            complete, confidence, checks_passed, checks_run, len(gaps),
        )

        # Phase 65: emit CLOSURE_VERDICT to captain's log with per-check
        # modality distribution. Lets closure quality be measured instead of
        # guessed (floor: static vs runtime ratio across runs).
        try:
            from captains_log import log_event, CLOSURE_VERDICT
            log_event(
                CLOSURE_VERDICT,
                subject="closure_verdict",
                summary=(
                    f"Closure: complete={complete} confidence={confidence:.2f} "
                    f"checks {checks_passed}/{checks_run} gaps={len(gaps)}"
                ),
                context={
                    "goal_preview": goal[:200],
                    "complete": complete,
                    "confidence": confidence,
                    "checks_run": checks_run,
                    "checks_passed": checks_passed,
                    "gap_count": len(gaps),
                    # Gap text, not just count — burn-in batch 2 adjudication
                    # needed the actual gaps to attribute a wrong verdict.
                    "gaps": [str(g)[:200] for g in gaps[:5]],
                    "scope_supplied": scope is not None,
                    "modality_distribution": modality_dist,
                    "inconclusive_count": len(inconclusive_checks),
                    "behavioral_gap_downgrade": behavioral_gap_reason or "",
                    "diagnosis_failure_class": safe_str(getattr(diagnosis, "failure_class", "")),
                    "diagnosis_gap_downgrade": diagnosis_gap_reason or "",
                    "commands": [r.get("command", "")[:200] for r in check_results],
                    "summary": summary[:400],
                },
                loop_id=loop_id or None,
            )
        except Exception:
            pass

        return verdict

    except Exception:
        log.debug("closure check error — treating as complete", exc_info=True)
        _emit_skip("exception")
        return _null


# ---------------------------------------------------------------------------
# Probe modality classifier (Phase 65 closure observability)
#
# The sole classifier — used both for the per-check "modality" tag (in
# verify_goal_completion's check loop) and the modality_distribution
# aggregate below. A second, naive substring-matching classifier
# (_check_modality_from_command) existed alongside this one for a while —
# both were added the same day (2026-04-17), a few hours apart, without one
# noticing the other. It disagreed on common cases (npm/pnpm/bash/sh test
# runners, `go build ./x`, bare "websocket", nc/netcat) because it lacked
# this one's static-hint-before-process precedence and tighter regexes.
# Retired 2026-07-02 in favor of this one, which is the more carefully
# tuned implementation (see the process-pattern comment below re: the
# `go build ./...` false positive it was built to avoid).
# ---------------------------------------------------------------------------

# Order matters: first match wins. browser/ws/http/process before static so
# a command like `curl localhost:8080/health && grep foo bar` classifies as
# http (the behavioral part), not static.
_MODALITY_PATTERNS = (
    ("browser", re.compile(r"\b(playwright|puppeteer|selenium|chromium|chrome --headless|firefox --headless)\b", re.I)),
    ("ws",      re.compile(r"\b(wscat|websocat|wss?://)\b", re.I)),
    ("http",    re.compile(r"\b(curl|wget|httpie|http [A-Z]+|https?://)\b", re.I)),
    # "process" = runs a built binary or a script that likely exercises the
    # artifact without network (e.g. `./bin --help`, `timeout 5 ./server &`).
    # First char after `./` must be alphanumeric/underscore — rules out the
    # go wildcard `./...` (as in `go build ./...`) which is a package pattern,
    # not a binary invocation.
    ("process", re.compile(r"(^|[\s;&|])\./[A-Za-z0-9_-][A-Za-z0-9_./-]*|(^|[\s;&|])(go run|node |python[0-9.]* |timeout [0-9]+\s+\S+\s*&)", re.I)),
)

_STATIC_HINTS = re.compile(
    r"\b(grep|rg|test -[efdrs]|cat|head|tail|wc -[lc]|ls |find |jq |go build|go vet|go test -run|tsc --noEmit|ruff|flake8|mypy|pytest --collect-only)\b",
    re.I,
)


def _classify_probe_modality(cmd: str) -> str:
    """Classify a closure probe command by what it actually exercises.

    Returns one of: browser, ws, http, process, static. "static" is the
    residual — code inspection and compile-level checks that never touch
    the running artifact.
    """
    if not cmd:
        return "static"
    # Browser / ws / http are the strongest behavioral signals — they win
    # even when mixed with static tools (e.g. "curl ... && grep ...").
    for label, pat in _MODALITY_PATTERNS[:3]:
        if pat.search(cmd):
            return label
    # Before checking "process", defer to explicit static hints. A command
    # like `go build ./cmd/slycrel-server` otherwise matches "process" via
    # `./cmd/...` even though the actual verb is a compile-only check.
    if _STATIC_HINTS.search(cmd):
        return "static"
    # Process = runs a built binary / script that likely exercises the
    # artifact without network I/O.
    for label, pat in _MODALITY_PATTERNS[3:]:
        if pat.search(cmd):
            return label
    # No runtime indicator — treat as static.
    return "static"


# Runtime-gap admission phrases — what the LLM says when it knows it didn't
# actually exercise the thing. Drawn from the slycrel-go run's own closure
# summary: *"Gap: runtime validation (server startup + browser connection)
# was not performed."* Matching against the LLM's own words, not an external
# taxonomy of goal types.
_RUNTIME_GAP_ADMISSION = re.compile(
    r"\b(runtime (validation|check|verification|test)|"
    r"(?:not|never|wasn'?t|weren'?t) (?:run|tested|performed|exercised|executed|verified|started|booted)|"
    r"no \w+(?:\s+\w+){0,3} (?:was |were )?(?:run|tested|performed|exercised|executed|verified|started|booted)|"
    r"unexercised runtime|no behavioral|no runtime probe|"
    r"browser connection (?:was )?not|server (?:startup|boot) (?:was )?not)\b",
    re.I,
)

# Scope failure-modes that signal runtime delivery expectations. When scope
# generated these, the system already said it cared about behavioral evidence
# — closure probing only code is then an inversion miss.
_RUNTIME_FAILURE_MODE_HINT = re.compile(
    r"\b(server|daemon|process|websocket|ws connection|http|endpoint|port|"
    r"browser|client|session|listen|connect|disconnect|responds? to|"
    r"render|ui|deploy|service)\b",
    re.I,
)

_BEHAVIORAL_MODALITIES = ("http", "ws", "browser", "process")


def _detect_behavioral_gap(
    *,
    complete: bool,
    summary: str,
    gaps: List[str],
    modality_dist: Dict[str, int],
    scope=None,
) -> str:
    """Return a non-empty reason string when complete=True contradicts evidence.

    Two inference-shaped signals:
    1. The LLM's own summary/gaps admit runtime wasn't exercised (self-contradiction).
    2. Scope's failure_modes named runtime expectations but no behavioral probe ran.

    Either fires only when `complete=True` AND modality_distribution has zero
    behavioral probes. The goal is to catch the precise slycrel-go pattern
    (closure summary: "runtime validation was not performed" → returns
    complete=True) using data the system already generated, not an external
    "if goal is a server, demand http probe" rule.
    """
    if not complete:
        return ""

    has_behavioral = any(modality_dist.get(m, 0) > 0 for m in _BEHAVIORAL_MODALITIES)
    if has_behavioral:
        return ""

    # Signal 1: self-contradiction in summary / gap text.
    combined_text = summary + "\n" + "\n".join(gaps)
    if _RUNTIME_GAP_ADMISSION.search(combined_text):
        m = _RUNTIME_GAP_ADMISSION.search(combined_text)
        return f"LLM summary admits runtime was not exercised: {m.group(0)!r}"

    # Signal 2: scope failure modes named runtime expectations.
    if scope is not None:
        try:
            fm = getattr(scope, "failure_modes", None) or []
            for mode in fm:
                if _RUNTIME_FAILURE_MODE_HINT.search(mode or ""):
                    return (
                        f"scope.failure_modes named runtime expectation "
                        f"({mode[:100]!r}) but no behavioral probe ran"
                    )
        except Exception:
            pass

    return ""


def _detect_diagnosis_gap(
    *,
    complete: bool,
    diagnosis=None,
    modality_dist: Dict[str, int],
) -> str:
    """Return a reason when loop diagnosis contradicts a clean closure verdict.

    Targets the concrete backlog case where introspection already concluded the
    decomposition was too broad, but closure still blesses the run without any
    behavioral evidence.
    """
    if not complete or diagnosis is None:
        return ""

    try:
        failure_class = safe_str(getattr(diagnosis, "failure_class", ""))
        severity = safe_str(getattr(diagnosis, "severity", ""))
        recommendation = safe_str(getattr(diagnosis, "recommendation", ""))
    except Exception:
        return ""

    if failure_class != "decomposition_too_broad":
        return ""

    has_behavioral = any(modality_dist.get(m, 0) > 0 for m in _BEHAVIORAL_MODALITIES)
    if has_behavioral:
        return ""

    sev = f" severity={severity}" if severity else ""
    rec = f" recommendation={recommendation[:120]!r}" if recommendation else ""
    return (
        f"loop diagnosis reported decomposition_too_broad{sev} "
        f"before closure, but no behavioral probe ran.{rec}"
    )

