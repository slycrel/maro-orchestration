"""Run-scoped regression obligations (LoopsBench follow-up, 2026-09-16).

The done≠achieved gap has a temporal twin: a step runs `pytest`, sees it
pass, reports done — and a LATER step breaks what it proved. Closure
(`closure_verify.verify_goal_completion`) generates fresh checks from the
goal and runs them once; nothing re-runs the verification a step itself
already paid for. LoopsBench (Microsoft, arXiv:2608.00267) names this the
regression-verification gap: "Regression Pressure" persists regardless of
context strategy because nothing carries the obligation forward.

This ledger carries it. Harvest is deterministic and positive-evidence:
from a DONE step's real tool transcript (`outcome["tool_events"]`, the
executor's own shell calls), keep each command that

  1. parses as ONE simple test-runner invocation (`parse_verification_command`
     — a shlex word list whose program is a known runner, optionally behind
     `cd <dir> &&`, leading `NAME=value` assignments, or `uv|poetry run`;
     no control operators, pipes, redirects, heredocs, substitutions or
     newlines anywhere — a shell PROGRAM is never an obligation, because
     closure re-runs argv with `shell=False` and must run exactly what the
     step ran, nothing more);
  2. was executed for real (no --collect-only / --dry-run form);
  3. PASSED on positive evidence: the executor saw a tool RESULT for the
     call (`result_seen`), it was not an error, its output is non-empty and
     not truncated by the transcript view, carries no failure tally, and —
     for runners with a known summary line (pytest / go test / cargo test)
     — carries the passing tally.

Each becomes an obligation: "this argv passed at step K in cwd D". Closure
re-runs every obligation mechanically in that recorded cwd; one that now
FAILS is a regression and downgrades the verdict deterministically — the
paper's "automated regression verification", grounded in what the run
itself proved rather than in what a judge guesses.

Inconclusive re-runs (recorded cwd gone, tool missing on the closure host,
permission, timeout — `closure_verify._check_outcome`'s verifier-failure
classes) never downgrade and never block a restart: an environment-blind
probe proves nothing either way.

Stdlib-only, like terrain.py / world_facts.py. Rides the run checkpoint
(`checkpoint.Checkpoint.regression`) so a resumed run keeps its obligations;
rows are re-validated on the way back in, so a hand-edited or stale
checkpoint cannot smuggle a program into the closure re-run.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Modality classifier shared with closure_verify (a probe that mentions a
# runner is a "test" probe). This is CLASSIFICATION only — the harvest
# demands the structural form below, never a regex hit.
VERIFICATION_RUNNER_RE = re.compile(
    r"\b(pytest|go test|cargo test|(npm|pnpm|yarn|bun) (run )?test|make test|tox)\b",
    re.I,
)
NON_EXEC_RUNNER_FLAGS_RE = re.compile(
    r"(^|\s)--?(no-run|collect-only|co|list-?tests?|dry-run|list)\b", re.I,
)
# Output that still reports failures even when the call did not error
# (a runner under `|| true` never reaches the ledger — it is a program —
# but a wrapper script can exit 0 over a failing run).
# Terminal-summary shapes only (round-3 review): pytest/jest "N failed" /
# "N errors", mocha "N failing", pytest's short-summary lines
# "FAILED path::test" / "ERROR path::test", go's "FAIL" line / "--- FAIL:",
# cargo's "test result: FAILED", npm's "Tests failed". A bare "ERROR …" log
# line from the code under test is NOT a runner verdict.
_FAILURE_TALLY_RE = re.compile(
    r"\b[1-9]\d*\s+(failed|failing|errors?)\b"
    r"|^(FAILED|ERROR)\s+\S+::"
    r"|^FAIL\b|^--- FAIL:"
    r"|\bTests failed\b|\btest result: FAILED\b",
    re.I | re.M,
)
# Positive tally per runner family with a known summary line.
_PASS_TALLY_BY_FAMILY = {
    "pytest": re.compile(r"\b[1-9]\d*\s+passed\b", re.I),
    "go": re.compile(r"^(ok\b|PASS\b)", re.M),
    "cargo": re.compile(r"\btest result: ok\b"),
}
# Executor tools that run a shell command. Claude Code names it Bash; keep
# the match loose for other executors (codex "shell", generic "run_command").
_SHELL_TOOL_RE = re.compile(r"^(bash|shell|run_command|execute|exec|sh)$", re.I)
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"^python(3(\.\d+)?)?$")
# Any of these inside a shlex word means the string was a shell PROGRAM
# (operator, redirect, substitution, glob the shell would expand) — not a
# single argv. `*`/`?` are refused because shell=False will not glob.
_PROGRAM_CHARS = frozenset(";|&<>`$(){}*?~")
_RUN_WRAPPERS = frozenset({"uv", "poetry", "pipenv"})

MAX_OBLIGATIONS = 8
_COMMAND_CAP = 400


@dataclass
class ParsedCommand:
    argv: List[str]             # EXACT execution argv, wrapper included (`uv run pytest -q`)
    family: str                 # pytest | go | cargo | npm | make | tox | bun
    env: Dict[str, str]         # leading NAME=value assignments
    cd: Optional[str] = None    # `cd <dir> &&` prefix, unresolved
    runner_argv: List[str] = None  # argv after any `uv|poetry|pipenv run` wrapper (classification only)


def _runner_family(argv: List[str]) -> str:
    """The runner family of an argv, or "" when it is not a test runner."""
    if not argv:
        return ""
    base = os.path.basename(argv[0])
    rest = argv[1:]
    if base in ("pytest", "py.test"):
        return "pytest"
    if _PYTHON_RE.match(base) and rest[:2] == ["-m", "pytest"]:
        return "pytest"
    if base == "tox":
        return "tox"
    if base == "go" and rest[:1] == ["test"]:
        return "go"
    if base == "cargo" and rest[:1] == ["test"]:
        return "cargo"
    if base in ("npm", "pnpm", "yarn"):
        if rest[:1] == ["test"] or rest[:2] == ["run", "test"]:
            return "npm"
        return ""
    if base == "bun" and rest[:1] == ["test"]:
        return "bun"
    if base == "make" and "test" in rest:
        return "make"
    return ""


def parse_verification_command(cmd: str) -> Optional[ParsedCommand]:
    """One simple test-runner invocation, or None.

    Accepts: `[cd DIR &&] [NAME=value ...] [uv|poetry|pipenv run] RUNNER ARGS`
    where every word is a literal (no operator, redirect, substitution,
    glob or tilde) and the whole string is one line. Anything else — a
    pipeline, `|| true`, a heredoc, a second command — is a shell program
    and is refused, so closure can re-run argv with shell=False and know it
    ran exactly what the step ran.
    """
    if not isinstance(cmd, str):
        return None
    text = cmd.strip()
    if not text or len(text) > _COMMAND_CAP:
        return None
    if "\n" in text or "\r" in text:
        return None
    try:
        words = shlex.split(text, comments=False, posix=True)
    except ValueError:
        return None
    if not words:
        return None
    cd: Optional[str] = None
    if words[0] == "cd":
        # `cd DIR && ...` exactly; `cd` alone, `cd; ...` or `cd DIR; ...`
        # are programs (the `;` form arrives as a word containing ';').
        if len(words) < 4 or words[2] != "&&":
            return None
        cd = words[1]
        if any(ch in _PROGRAM_CHARS for ch in cd):
            return None
        words = words[3:]
    for w in words:
        if not w or any(ch in _PROGRAM_CHARS for ch in w):
            return None
    env: Dict[str, str] = {}
    while words and _ENV_ASSIGN_RE.match(words[0]):
        k, _, v = words.pop(0).partition("=")
        env[k] = v
    # An unquoted `#` word starts a shell comment: bash runs `pytest -q # smoke`
    # as `pytest -q`, argv would carry the comment words. Refuse rather than
    # guess (a quoted "#" argument is indistinguishable after shlex).
    if any(w.startswith("#") for w in words):
        return None
    # The wrapper is part of what RAN (a project environment the bare runner
    # may not have); it stays in the execution argv and is stripped only to
    # classify the runner family.
    runner = list(words)
    if len(runner) >= 2 and os.path.basename(runner[0]) in _RUN_WRAPPERS and runner[1] == "run":
        runner = runner[2:]
    family = _runner_family(runner)
    if not family:
        return None
    if NON_EXEC_RUNNER_FLAGS_RE.search(" ".join(runner)):
        return None
    return ParsedCommand(argv=list(words), family=family, env=env, cd=cd, runner_argv=runner)


def has_failure_tally(output: str) -> bool:
    """The runner's own failure verdict is in this output (family summaries only)."""
    return bool(_FAILURE_TALLY_RE.search(output or ""))


def classify_rerun(family: str, returncode: int, output: str) -> str:
    """Outcome class of a closure re-run, by the SAME evidence the harvest
    demanded: a failure tally is a fail whatever the exit code (a wrapper
    or make target can swallow the runner's status); a zero exit without
    the family's passing tally is inconclusive, not a pass.
    """
    out = output or ""
    if _FAILURE_TALLY_RE.search(out):
        return "fail"
    if returncode != 0:
        return "fail"
    pass_re = _PASS_TALLY_BY_FAMILY.get(family)
    if pass_re is not None and not pass_re.search(out):
        return "inconclusive"
    return "pass"


def is_verification_command(cmd: str) -> bool:
    """A recognized test runner in a single, executing, literal form."""
    return parse_verification_command(cmd) is not None


def obligation_key(parsed: ParsedCommand, cwd: str) -> str:
    """Dedup identity: argv + env + cwd, quoted so spacing cannot collide."""
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(parsed.env.items()))
    return f"{cwd}\x00{env}\x00" + " ".join(shlex.quote(w) for w in parsed.argv)


def resolve_cwd(base_cwd: Optional[str], cd: Optional[str]) -> str:
    """Absolute directory the command ran in: executor cwd plus any `cd`."""
    base = base_cwd or os.getcwd()
    if cd:
        base = os.path.join(base, cd)
    return os.path.abspath(base)


def shell_command_of(event: Dict[str, Any]) -> str:
    """The shell command a tool event ran, or "" when it is not a shell call."""
    name = str(event.get("name", "") or "")
    if not _SHELL_TOOL_RE.match(name):
        return ""
    inp = event.get("input")
    if isinstance(inp, dict):
        for key in ("command", "cmd", "script"):
            val = inp.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return ""
    if isinstance(inp, str):
        return inp
    return ""


def event_passed(event: Dict[str, Any], family: str = "") -> bool:
    """Positive evidence the command succeeded.

    Requires a SEEN tool result (an unmatched tool_use renders as output ""
    / is_error False — silence, not success), no error, non-empty and
    untruncated output, no failure tally, and the family's passing tally
    when the runner has one.
    """
    if event.get("is_error"):
        return False
    if event.get("result_seen") is not True:
        return False
    if event.get("output_truncated"):
        return False
    out = str(event.get("output", "") or "")
    if not out.strip():
        return False
    if _FAILURE_TALLY_RE.search(out):
        return False
    pass_re = _PASS_TALLY_BY_FAMILY.get(family)
    if pass_re is not None and not pass_re.search(out):
        return False
    return True


@dataclass
class RegressionObligation:
    command: str           # exact string the step ran (re-parsed before every use)
    cwd: str               # absolute directory it ran in (executor cwd + any `cd`)
    step_index: int        # NEXT.md item index of the step that proved it (-1 for sub-steps)
    step_no: int           # 1-based execution counter at harvest time
    iteration: int = 0
    step_text: str = ""    # clipped, for the closure row's provenance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "step_index": self.step_index,
            "step_no": self.step_no,
            "iteration": self.iteration,
            "step_text": self.step_text,
        }


class RegressionLedger:
    """Ordered, deduplicated obligations; capped so closure stays bounded."""

    def __init__(self) -> None:
        self.obligations: List[RegressionObligation] = []
        self._seen: Dict[str, int] = {}

    def __len__(self) -> int:
        return len(self.obligations)

    def add(self, command: str, *, cwd: Optional[str], step_index: int, step_no: int,
            iteration: int = 0, step_text: str = "") -> bool:
        """Record one obligation. Returns False for a non-conforming command,
        a duplicate, or a full ledger — never raises."""
        parsed = parse_verification_command(command)
        if parsed is None or not isinstance(cwd, str) or not cwd:
            return False
        key = obligation_key(parsed, cwd)
        if key in self._seen:
            return False
        if len(self.obligations) >= MAX_OBLIGATIONS:
            return False
        self._seen[key] = len(self.obligations)
        self.obligations.append(RegressionObligation(
            command=command.strip(), cwd=cwd,
            step_index=_int_or(step_index, -1), step_no=_int_or(step_no, 0),
            iteration=_int_or(iteration, 0), step_text=str(step_text or "")[:160],
        ))
        return True

    def harvest(self, tool_events: Optional[Iterable[Any]], *, step_index: int,
                step_no: int, executor_cwd: Optional[str], iteration: int = 0,
                step_text: str = "") -> List[str]:
        """Record passing verification commands from one DONE step's transcript.

        `executor_cwd` is the directory the executor's shell calls started
        in (the worktree/clone or project dir); a `cd DIR &&` prefix is
        resolved against it. Returns the newly recorded commands (for
        logging). Never raises — a malformed transcript must not touch the
        step's outcome.
        """
        new: List[str] = []
        if not tool_events:
            return new
        try:
            for ev in tool_events:
                if not isinstance(ev, dict):
                    continue
                cmd = shell_command_of(ev)
                if not cmd:
                    continue
                parsed = parse_verification_command(cmd)
                if parsed is None:
                    continue
                if not event_passed(ev, parsed.family):
                    continue
                cwd = resolve_cwd(executor_cwd, parsed.cd)
                if self.add(cmd, cwd=cwd, step_index=step_index, step_no=step_no,
                            iteration=iteration, step_text=step_text):
                    new.append(cmd.strip())
        except Exception:
            return new
        return new

    def to_list(self) -> List[Dict[str, Any]]:
        return [o.to_dict() for o in self.obligations]

    @classmethod
    def from_list(cls, rows: Optional[Iterable[Any]]) -> "RegressionLedger":
        """Rebuild from checkpoint rows. Every row is re-validated through
        `add` (parse + cwd present); a row that no longer conforms is
        dropped, never raised. Malformed provenance ints degrade to their
        defaults — the command is the obligation."""
        led = cls()
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            cmd = row.get("command")
            cwd = row.get("cwd")
            if not isinstance(cmd, str) or not isinstance(cwd, str):
                continue
            led.add(cmd, cwd=cwd,
                    step_index=_int_or(row.get("step_index"), -1),
                    step_no=_int_or(row.get("step_no"), 0),
                    iteration=_int_or(row.get("iteration"), 0),
                    step_text=str(row.get("step_text", "") or ""))
        return led


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def regression_enabled() -> bool:
    """Single kill switch, honoured at harvest, at checkpoint restore and at
    the closure re-run (an off switch stays off — a checkpoint written while
    it was on must not re-arm it)."""
    try:
        from config import get_bool as _cfg_get_bool
        return _cfg_get_bool("regression.enabled", True)
    except Exception:
        return True
