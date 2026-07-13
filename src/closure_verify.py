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
    4. Behavioral probes: when any deliverable above is tagged [shape: runtime]
       (a server, CLI, endpoint, websocket, background process, or UI flow),
       your plan MUST include at least one behavioral probe that actually
       exercises it (http/ws/process/browser — not just a static file check).
       This is not a preference — a runtime-shaped deliverable "verified" only
       by a static check (file exists, code compiles) is unverified.
       You may skip the behavioral probe ONLY when it is genuinely impossible
       in this environment (no port available, requires external credentials
       or network access, no display for a browser probe, etc.) — say so
       explicitly by setting "behavioral_probe_waived" in your JSON response
       to the specific reason. Do not waive it because it's inconvenient, and
       do not fake a static substitute and call the deliverable checked.
       When no deliverable is tagged runtime (or none were declared), still
       prefer at least one behavioral/runtime probe whenever the work summary
       suggests a running artifact, service, CLI, endpoint, websocket, or UI
       flow — but this softer case has no waiver requirement; skipping it is a
       normal judgment call, not something to explain.
       Cheap scaffolding is encouraged when it makes runtime probing mechanical, for
       example:
       - start a server in background with cleanup: `tmp=$(mktemp); (python app.py >$tmp 2>&1 &) ; pid=$!; trap 'kill $pid' EXIT; sleep 2; curl -fsS http://127.0.0.1:8000/health`
       - probe websocket upgrade: `python server.py >/tmp/s.log 2>&1 & pid=$!; trap 'kill $pid' EXIT; sleep 2; curl -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' http://127.0.0.1:8080/ws | grep '101 Switching Protocols'`
       - exercise a CLI or built binary directly: `./bin/tool --help >/tmp/tool.out && grep -q 'usage' /tmp/tool.out`

    Output rules:
    - Generate 2–5 checks. Each must be a single shell command.
    - Each check MUST name which failure mode (or inversion hypothesis) it probes.
    - When a file inventory of the working directory is provided, probe those
      exact paths — do NOT invent expected filenames. A deliverable saved under
      a different name than you'd guess is still delivered; a check against a
      guessed name that fails proves nothing about the goal.
    - When probing the content of a file you have not read, prefer predicates
      over the whole file (e.g. `grep -qiE 'urgent|deadline' file`) to
      position/format-specific pipelines — numbered-list or quote-prefix
      assumptions break on tables and code fences and fail work that is fine.
    - Static checks (grep, file existence, compile-only) must be fast (<15s).
      Behavioral probes (server start, websocket handshake, CLI invocation)
      may take up to __TIMEOUT_PER_CHECK__s if they need brief startup time —
      that's the actual execution budget, use it rather than cutting a probe
      short. All checks must be safe (read-only or self-cleaning) and exit 0
      on success. Wrap background processes with `timeout` and always clean
      up PIDs.
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
    {"checks": [{"failure_mode": "...", "description": "...", "command": "..."}],
     "behavioral_probe_waived": "<reason — only when skipping a REQUIRED behavioral probe for a runtime-shaped deliverable; omit or empty string otherwise>"}
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

    Some failed checks carry "target_file_content" — the actual current content
    (bounded excerpt) of files the failed command referenced. That content is
    ground truth and outranks the probe's exit code: judge from it whether the
    feared failure actually occurred. A literal-string or format mismatch (a
    grep for wording the file phrases differently) against a file whose content
    plainly delivers the goal is a brittle check, not a gap — do not fail the
    goal on it, and do not guess which clause of a compound command failed when
    the content already answers the question. Treat the failed check as a real
    gap only when the content itself confirms the deficiency.

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
    # False when the verdict hinges entirely on inconclusive probes (verifier
    # tooling error, permission denied, missing tool) — i.e. no check actually
    # ran cleanly and disproved the work. An unjudged verdict must not be
    # recorded as goal_achieved=false: absence of the key means "not judged".
    # 2026-07-09 dogfood batch: 4/5 known-good runs were false-negatived by
    # verdicts resting on the verifier's own failures, not the goal's.
    judged: bool = True


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
    # Prose with an embedded slash ("Python/YAML parser to validate format",
    # "~/.maro/workspace writable") is not a path — real paths don't contain
    # whitespace in practice, LLM-emitted prose preconditions almost always do.
    # Without this, prose lands in the path branch, fails Path.exists, and the
    # bogus "failed precondition" poisons the verdict feed (2026-07-09 dogfood
    # batch: 3 of 4 false-negatived runs carried these synthetic failures).
    if re.search(r"\s", s):
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

    For each command-shaped precondition: shutil.which → passed (cwd-independent,
    runs regardless of whether cwd resolved).
    For each path-shaped precondition: Path(cwd)/preq exists → passed. When cwd
    didn't resolve, marked inconclusive/env_unresolved instead of falling back
    to Path.cwd() — same B3(a) contract as the main check loop below: probing
    Maro's own launch directory would produce a confident-looking but meaningless
    pass/fail (adversarial-review finding, 2026-07-12: this preflight predated
    B3(a)'s guard and had the exact wrong-cwd bug B3(a) was built to eliminate).
    Opaque preconditions are skipped (no synthetic check; the LLM still sees
    them in the deliverables block).

    Returns a list of synthetic check results in the same shape as the
    real check_results — so callers can prepend them and the existing
    interpretation pipeline treats them uniformly.
    """
    import shutil
    out: List[Dict[str, Any]] = []
    base = Path(cwd) if cwd else None
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
                # expanduser first: Path("~/x").is_absolute() is False, so a
                # tilde path would otherwise resolve to base/"~/x" — a literal
                # "~" directory that never exists.
                _pp = Path(preq).expanduser()
                if not _pp.is_absolute() and base is None:
                    out.append({
                        "description": f"precondition: {preq} (path for {_name})",
                        "command": f"Path({preq!r}).exists",
                        "modality": "preflight",
                        "exit_code": -1, "stdout": "",
                        "stderr": "cwd unresolved — precondition not checked",
                        "passed": False, "outcome": "inconclusive",
                        "env_unresolved": True,
                    })
                    continue
                target = _pp if _pp.is_absolute() else (base / _pp).resolve()
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


def _failed_check_file_evidence(
    cmd: str, cwd: Optional[str], *, max_files: int = 2, excerpt_chars: int = 1200
) -> Dict[str, str]:
    """Ground-truth excerpts of files a failed static check referenced.

    A failed content-match (grep for a literal header, a row-count predicate)
    against a file that EXISTS proves only that one string is absent — weak
    evidence, routinely misread as "file does not exist or is malformed"
    (run 8177541b: a 3-clause compound check failed on `grep -q 'Station
    Name'` while the deliverable's header said `| Rank | Station |`; the
    verdict LLM couldn't see which clause failed and false-negatived a good
    run). Instead of teaching the plan LLM better grep style (prompt-patching
    a taxonomy), attach the file's actual content to the verdict call and let
    it judge whether the feared failure really happened.

    Tokenizes the command, keeps tokens that resolve to existing regular
    files under cwd, and returns {relative_token: bounded_excerpt}. Skips
    flags and redirections. Returns {} when nothing resolves — a genuinely
    missing file attaches no evidence and the failure stands on its own.
    """
    if not cmd:
        return {}
    import shlex
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    base = Path(cwd) if cwd else Path.cwd()
    out: Dict[str, str] = {}
    for tok in tokens:
        if len(out) >= max_files:
            break
        t = tok.strip("'\"")
        if not t or t.startswith("-") or "<" in t or ">" in t:
            continue
        # Only path-shaped tokens: a bare word like `test` or `grep` that
        # happens to collide with a filename in cwd is not what the check
        # was probing.
        if "/" not in t and "." not in t:
            continue
        if t in out:
            continue
        try:
            p = Path(t).expanduser()
            target = p if p.is_absolute() else base / p
            if not target.is_file():
                continue
            with open(target, "r", errors="replace") as fh:
                excerpt = fh.read(excerpt_chars + 1)
            if len(excerpt) > excerpt_chars:
                excerpt = excerpt[:excerpt_chars] + "\n... (truncated)"
            out[t] = excerpt
        except OSError:
            continue
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
    # The probe's own tooling failed — the command never ran to a clean
    # true/false answer, so it can neither prove nor disprove the work.
    # Verifier-authored syntax errors: a python -c / heredoc one-liner that
    # didn't parse reports File "<string>" / "<stdin>" (witty-spruce run:
    # "format validation" was scored as a goal failure off exactly this);
    # a SyntaxError pointing at a real file is the WORK failing to parse
    # and stays "fail". Shell parse errors ("syntax error near unexpected
    # token") are likewise the verifier's own command text. Permission
    # denied = the verifier's environment lacks access the worker had
    # (keen-alder run: journalctl). AssertionError et al. stay "fail" —
    # those mean the check RAN and the asserted fact was false.
    if "syntaxerror" in err and ('"<string>"' in err or '"<stdin>"' in err):
        return "inconclusive"
    if "syntax error near unexpected token" in err or "syntax error: " in err:
        return "inconclusive"
    if "permission denied" in err or "operation not permitted" in err:
        return "inconclusive"
    return "fail"


def _detect_next_ledger_gap(project: str, workspace_path: str) -> str:
    """NEXT.md ledger vs repo activity divergence at closure (BACKLOG #6).

    Deterministic: when the project's NEXT.md still has unchecked items while
    the workspace repo has a commit NEWER than the ledger's last update, the
    ledger lags reality — either the loop did the work and never reflected it
    back (`mark_item`), or the items genuinely weren't done. Both readings
    mean the run's own record can't be trusted at face value.

    Returns a short description of the divergence, or "" when in sync or not
    applicable (no project, no NEXT.md, no unchecked items, not a git repo).
    Advisory only — surfaced to the verdict LLM and the CLOSURE_VERDICT
    event; never flips the verdict by itself.
    """
    try:
        import subprocess as _sp
        import orch_items as o
        if not project:
            return ""
        np = o.next_path(project)
        if not np.is_file():
            return ""
        _, items = o.parse_next(project)
        unchecked = [it for it in items if it.state != o.STATE_DONE]
        if not unchecked:
            return ""
        if not workspace_path or not (Path(workspace_path) / ".git").exists():
            return ""
        ledger_mtime = np.stat().st_mtime
        proc = _sp.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=workspace_path, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return ""
        last_commit = float(proc.stdout.strip().splitlines()[0])
        if last_commit <= ledger_mtime:
            return ""
        preview = "; ".join(it.text[:60] for it in unchecked[:3])
        return (
            f"NEXT.md has {len(unchecked)} unchecked item(s) but the repo has "
            f"commit activity newer than the ledger's last update — work may "
            f"have been done without being reflected back, or genuinely not "
            f"done. Unchecked: {preview}"
        )
    except Exception:
        return ""


_INVENTORY_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})


def _project_file_inventory(root: str, cap: int = 120) -> str:
    """Bounded relative-path listing of the verification cwd — ground truth
    for the closure plan so checks probe files that actually exist instead
    of filenames the LLM guesses from the work summary.

    2026-07-09 dogfood batch: two known-good runs were false-negatived by
    checks against invented names (`output/brief_2026-07-09_run1.md`,
    `output/fixture_diff.patch`) while the real deliverables sat next to
    them (`output/daily_brief_20260709_163825.md`, `output/fixture.diff`).

    Returns "" when root is missing/not a dir. Skips VCS/cache dirs and
    .lock files; caps at `cap` entries with a truncation marker so a big
    tree can't blow up the prompt.
    """
    import os
    try:
        rootp = Path(root)
        if not rootp.is_dir():
            return ""
        entries: List[str] = []
        for dirpath, dirnames, filenames in os.walk(rootp):
            dirnames[:] = sorted(d for d in dirnames if d not in _INVENTORY_SKIP_DIRS)
            rel = os.path.relpath(dirpath, rootp)
            for fn in sorted(filenames):
                if fn.endswith(".lock"):
                    continue
                entries.append(fn if rel == "." else os.path.join(rel, fn))
                if len(entries) >= cap:
                    entries.append(f"... (truncated at {cap} files)")
                    return "\n".join(entries)
        return "\n".join(entries)
    except OSError:
        return ""


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
    project: str = "",
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
    # Last resort: derive the project dir from the project slug — the same
    # identity agent_loop binds the ContextVar to. Covers callers reached
    # from a context where the run-scoped cwd was never set (or was reset):
    # without it, checks silently run in Maro's launch cwd and every
    # relative artifact probe is a false negative.
    if not workspace_path and project:
        try:
            from loop_types import _project_dir_root
            _proj_dir = _project_dir_root() / project
            if _proj_dir.is_dir():
                workspace_path = str(_proj_dir)
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
                _shape = getattr(d, "shape", None)
                _line = f"- {_name}"
                if _desc:
                    _line += f": {_desc}"
                if _preq:
                    _line += f" [preconditions: {', '.join(_preq)}]"
                if _shape:
                    _line += f" [shape: {_shape}]"
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

        # Ground-truth file inventory of the verification cwd — the plan
        # probes actual paths instead of guessing deliverable filenames.
        _inventory = _project_file_inventory(workspace_path) if workspace_path else ""
        _inventory_block = (
            "Files that actually exist under the working directory "
            "(ground truth — probe these exact paths, do not invent names):\n"
            f"{_inventory}\n\n"
        ) if _inventory else ""

        # Phase 1: generate verification plan
        _closure_plan_system = _CLOSURE_PLAN_SYSTEM.replace(
            "__TIMEOUT_PER_CHECK__", str(timeout_per_check)
        )
        plan_resp = adapter.complete(
            [
                LLMMessage("system", _closure_plan_system),
                LLMMessage("user",
                    f"Goal: {goal}\n\n"
                    f"Working directory: {workspace_path or '(unspecified)'}\n\n"
                    f"{_inventory_block}"
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
        behavioral_probe_waived = safe_str(
            plan_data.get("behavioral_probe_waived", "") if plan_data else ""
        )

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
            if cwd is None:
                # B3(a) probe-env hardening (docs/history/2026-07-12-routing-
                # and-probe-synthesis-design.md Part B): the full cwd-resolution chain above
                # (workspace_path -> get_default_subprocess_cwd -> project
                # dir) came up empty. Running here anyway would silently
                # probe Maro's own launch directory instead of wherever the
                # executor actually wrote — a confident-looking but
                # meaningless pass/fail. Mark it honestly inconclusive
                # instead of running it somewhere arbitrary.
                check_results.append({
                    "description": desc, "command": cmd,
                    "modality": modality,
                    "exit_code": -1, "stdout": "",
                    "stderr": "cwd unresolved — check not run",
                    "passed": False, "outcome": "inconclusive",
                    "env_unresolved": True,
                })
                continue
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=timeout_per_check, cwd=cwd,
                )
                outcome = _check_outcome(exit_code=proc.returncode, stderr=proc.stderr)
                result = {
                    "description": desc,
                    "command": cmd,
                    "modality": modality,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:500],
                    "stderr": proc.stderr[:300],
                    "passed": proc.returncode == 0,
                    "outcome": outcome,
                }
                # Failed static checks get ground-truth excerpts of the files
                # they probed, so the verdict judges the content instead of
                # guessing what a failed grep implies. Behavioral failures
                # (http/ws/process) stay as-is — their file args aren't the
                # thing being verified.
                if outcome == "fail" and modality == "static":
                    evidence = _failed_check_file_evidence(cmd, cwd)
                    if evidence:
                        result["target_file_content"] = evidence
                check_results.append(result)
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

        # NEXT.md ledger vs repo activity (BACKLOG #6) — deterministic
        # divergence note the director sees when declaring completeness.
        _ledger_gap = _detect_next_ledger_gap(project, workspace_path)
        _ledger_block = f"\nLedger divergence note: {_ledger_gap}\n" if _ledger_gap else ""
        if _ledger_gap:
            log.info("closure: %s", _ledger_gap)

        # Phase 3: director interprets results
        results_text = json.dumps(check_results, indent=2)
        verdict_resp = adapter.complete(
            [
                LLMMessage("system", _CLOSURE_VERDICT_SYSTEM),
                LLMMessage("user",
                    f"Goal: {goal}\n\n"
                    f"Work done:\n{work_summary}\n"
                    f"{_ledger_block}\n"
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
            resolved_intent=resolved_intent,
            behavioral_probe_waived=behavioral_probe_waived,
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

        # Honest tri-state: a negative verdict with zero cleanly-failed checks
        # rests entirely on probes that couldn't run (verifier syntax errors,
        # permission walls, missing tools) — missing data, not disproof. Mark
        # it unjudged so the recorder leaves goal_achieved absent instead of
        # writing false, and status demotion stands down. Behavioral/diagnosis
        # downgrades are exempt: those are evidence-based self-contradiction
        # findings, not probe-infra casualties.
        _fail_count = sum(1 for r in check_results if r.get("outcome") == "fail")
        judged = True
        if (
            not complete
            and _fail_count == 0
            and inconclusive_checks
            and not behavioral_gap_reason
            and not diagnosis_gap_reason
        ):
            judged = False
            log.info(
                "closure: verdict unjudged — negative verdict rests only on "
                "%d inconclusive probe(s), no check cleanly failed",
                len(inconclusive_checks),
            )

        # B3(b) probe-env hardening (docs/history/2026-07-12-routing-and-probe-synthesis-design.md
        # Part B): when most of what executed couldn't reach a clean answer
        # (missing tool, permission denied, timeout, verifier's own syntax
        # error — the _check_outcome inconclusive branches), that's the
        # verifier's tooling failing, not evidence about the goal. This is
        # deliberately narrower than it first looks: it requires
        # `_fail_count == 0`, mirroring the `judged` gate immediately above
        # (same variable, same line: a clean fail is never environmental
        # noise — that's the entire point of the pass/fail/inconclusive
        # tri-state). A negative verdict that rests on heavy environmental
        # noise PLUS a deterministic self-contradiction finding (behavioral
        # or diagnosis gap, which can report high confidence with zero
        # check-level fails) still gets capped below the demotion threshold.
        # A negative verdict backed by even one check that cleanly failed
        # does NOT get capped — that fail is real, mechanical evidence and
        # must be allowed to demote (adversarial-review pass 2, 2026-07-12:
        # the original unconditional form could suppress demotion for a
        # verdict where the ONLY decisive evidence was a real failure,
        # diluted by unrelated inconclusive noise — exactly the
        # "verified-done beats reported-done" case this file exists to
        # protect, not the environment-noise case B3(b) targets).
        #
        # Accepted residual risk (adversarial-review pass 3, 2026-07-12,
        # scoped skeptic review of this exact narrowing): `outcome == "fail"`
        # only proves a check executed cleanly and returned a boolean
        # negative — it does NOT prove the check itself was a *relevant*,
        # well-written test of the goal. A single brittle/irrelevant check
        # (e.g. a bad grep pattern the plan LLM wrote) diluted by unrelated
        # inconclusive noise now exempts the cap and can demote at full
        # confidence, same as a genuinely meaningful fail would. This is the
        # mirror image of the risk pass 2 fixed, and it's not mechanically
        # resolvable with only pass/fail/inconclusive counts — telling a
        # relevant fail from an irrelevant one needs either an LLM judge or
        # a check-to-deliverable relevance signal, neither of which exists
        # today (both are the kind of scope B1-B3's own design doc
        # explicitly deferred alongside the full BDD red-green loop).
        # Deliberately left as-is: an over-eager demotion here costs one
        # bounded closure_restart cycle (MAX_RESTART_DEPTH caps it); a
        # wrongly-suppressed real failure costs a silently-poisoned
        # goal_achieved record — the asymmetry favors trusting fails.
        if (
            not complete
            and checks_run
            and len(inconclusive_checks) > checks_run / 2
            and confidence >= 0.7
            and _fail_count == 0
        ):
            confidence = 0.69
            _env_note = (
                f"{len(inconclusive_checks)}/{checks_run} verification probe(s) "
                f"were inconclusive for environment reasons (missing tool, "
                f"permission denied, verifier syntax error, timeout) rather "
                f"than goal reasons — confidence capped below the demotion "
                f"threshold."
            )
            summary = f"{summary} {_env_note}" if summary else _env_note

        verdict = ClosureVerdict(
            complete=complete,
            confidence=confidence,
            gaps=gaps,
            summary=summary,
            checks_run=checks_run,
            checks_passed=checks_passed,
            inconclusive_count=len(inconclusive_checks),
            judged=judged,
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
                    "behavioral_probe_waived": behavioral_probe_waived,
                    "inconclusive_count": len(inconclusive_checks),
                    "judged": judged,
                    "behavioral_gap_downgrade": behavioral_gap_reason or "",
                    "diagnosis_failure_class": safe_str(getattr(diagnosis, "failure_class", "")),
                    "diagnosis_gap_downgrade": diagnosis_gap_reason or "",
                    "next_ledger_divergence": _ledger_gap[:300],
                    # How many failed static checks carried ground-truth file
                    # excerpts into the verdict call (brittle-probe evidence,
                    # 2026-07-10) — lets the false-negative fix be measured.
                    "evidence_attached": sum(
                        1 for r in check_results if r.get("target_file_content")
                    ),
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
    resolved_intent=None,
    behavioral_probe_waived: str = "",
) -> str:
    """Return a non-empty reason string when complete=True contradicts evidence.

    Three inference-shaped signals:
    1. The LLM's own summary/gaps admit runtime wasn't exercised (self-contradiction).
    2. Scope's failure_modes named runtime expectations but no behavioral probe ran.
    3. A deliverable is declared `[shape: runtime]` (Part B B1) but no behavioral
       probe ran and the plan didn't log a waiver — the MUST from B2 is prompt-only
       otherwise (an adversarial-review finding, 2026-07-12: 3 independent reviewers
       showed a `[shape: runtime]` deliverable with neutral failure-mode prose sails
       through Signal 2's gate untouched, since Signal 2 requires failure_modes text
       to hint at runtime BEFORE it ever consults declared shape). Skipped when
       `behavioral_probe_waived` is non-empty — the plan's own honest waiver is the
       designed escape hatch and this signal must not override it.

    Either fires only when `complete=True` AND modality_distribution has zero
    behavioral probes. The goal is to catch the precise slycrel-go pattern
    (closure summary: "runtime validation was not performed" → returns
    complete=True) using data the system already generated, not an external
    "if goal is a server, demand http probe" rule.

    Signal 2 additionally requires corroboration from the deliverables when a
    ResolvedIntent is supplied: a runtime keyword inside failure-mode *prose*
    is weak evidence on its own — run fd483efb (2026-07-11) had a document-only
    goal whose failure mode said "Proposal violates process logic" and the bare
    \\bprocess\\b hit downgraded a 5/5-checks 0.98-confidence verdict. When
    every deliverable is a document (no server/endpoint/service shape), static
    probes ARE the right modality and the downgrade must not fire. With no
    deliverables to consult, the original conservative behavior stands.
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
                    if not _deliverables_corroborate_runtime(resolved_intent):
                        break
                    return (
                        f"scope.failure_modes named runtime expectation "
                        f"({mode[:100]!r}) but no behavioral probe ran"
                    )
        except Exception:
            pass

    # Signal 3: a deliverable is declared runtime-shaped in its own right —
    # this is authoritative (B1) and must not depend on failure_mode prose
    # happening to mention it too. The waiver is the only legitimate escape.
    #
    # Accepted residual risk (adversarial-review pass 3, 2026-07-12): only
    # presence is checked, not content — any non-empty string suppresses
    # this signal, so a pretextual waiver ("static compile proves it")
    # bypasses the MUST exactly as well as a genuine one
    # ("no runtime harness available in this sandbox"). Judging whether a
    # waiver's stated reason is actually a legitimate environmental
    # impossibility needs either an LLM judge or a keyword taxonomy of
    # "acceptable excuses" — the former is new verifier-LLM scope, the
    # latter is the external-taxonomy anti-pattern this whole function
    # exists to avoid (see docstring above). Both are out of scope for B1-B3
    # ("honest-measurement prerequisites"); the design doc's own DECISION
    # defers exactly this class of judgment alongside the full BDD
    # red-green loop. Left as-is, not silently patched with a fragile check.
    if not behavioral_probe_waived and _any_declared_runtime_deliverable(resolved_intent):
        return "a declared [shape: runtime] deliverable has no behavioral probe and no logged waiver"

    return ""


def _any_declared_runtime_deliverable(resolved_intent) -> bool:
    """True when at least one deliverable explicitly declares `shape == "runtime"`.

    Unlike `_deliverables_corroborate_runtime`, this does NOT fall back to
    keyword inference — it only looks at the explicit B1 declaration, since
    it backs Signal 3's independent enforcement of the B2 MUST.
    """
    if resolved_intent is None:
        return False
    try:
        delivs = getattr(resolved_intent, "deliverables", None) or []
        return any(getattr(d, "shape", None) == "runtime" for d in delivs)
    except Exception:
        return False


def _deliverables_corroborate_runtime(resolved_intent) -> bool:
    """True when the deliverables leave the runtime-expectation hint credible.

    Returns True (keep Signal 2 armed) when there are no deliverables to
    consult, or when at least one deliverable is runtime-shaped. Returns
    False only when deliverables exist and every one is a plain
    document/data artifact — then an all-static probe set is the correct
    modality and a keyword hit in failure-mode prose is noise.

    Declared `Deliverable.shape` (docs/history/2026-07-12-routing-and-probe-synthesis-design.md
    Part B) is authoritative when present — the LLM said what kind of
    artifact this is at scope time, no need to re-guess from prose. Only
    unshaped (legacy) deliverables fall back to the original keyword-regex
    inference against name/description.
    """
    if resolved_intent is None:
        return True
    try:
        delivs = getattr(resolved_intent, "deliverables", None) or []
        if not delivs:
            return True
        for d in delivs:
            shape = getattr(d, "shape", None)
            if shape == "runtime":
                return True
            if shape in ("document", "data"):
                continue
            text = f"{getattr(d, 'name', '')} {getattr(d, 'description', '')}"
            if _RUNTIME_FAILURE_MODE_HINT.search(text):
                return True
        return False
    except Exception:
        return True


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

