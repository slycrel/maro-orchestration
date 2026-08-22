"""Shared grounding for adversarial-review contestations (inversion-at-verification).

An adversarial reviewer is itself an LLM and can fabricate contradictions — we
have logged it asserting "Go not installed" and "branch X does not exist" when
both were false. A text-only reviewer that states such claims as fact is just a
second hallucinator. The fix: the reviewer must, alongside each contestation,
supply a single read-only shell command that would *settle* whether the
contestation is correct, and we run it. The reviewer's claim and the probe that
adjudicates it travel together; the verdict becomes mechanical, not a second LLM
judgment.

This module is the single source of truth for BOTH halves so the two adversarial
prompts (quality_gate + verification_agent) can't drift apart again — the diverged
prompts were the original root cause (only one asked for `settled_by_command`).

  - SETTLED_BY_COMMAND_CLAUSE — the prompt fragment every adversarial prompt
    appends, so they all request a probe.
  - probe_contested_claims() — runs each probe and reclassifies.

Zero LLM. Fails safe per claim (an unrunnable probe never grants a free win OR a
free dismissal). Consumers: quality_gate, factory_thin, verification_agent.
"""

from __future__ import annotations

import logging
import re
import shlex
import textwrap
from typing import List

from context_budget import clip
from llm_parse import safe_str

log = logging.getLogger("maro.claim_probe")

# Read-only probes must be quick; the reviewer is told <15s.
PROBE_TIMEOUT_SEC = 15

# --- Numeric-claim sufficiency guard ---------------------------------------
# 2026-08-11 (run 2a3b1f85): a reviewer contested "obra/superpowers has
# 270,734 stars" and supplied
#   curl -fs … | grep -o '"stargazers_count":[0-9]*'
# which exits 0 for ANY star count — it tests that the field exists, not what
# it holds (and GitHub's `": "` spacing meant zero digits were even captured).
# The dispute was dismissed by a probe that could not have failed; the claim
# happened to be true, so the outcome was right by luck. The guard below is
# shape-level: when a contested claim disputes a number, an exit-0 probe that
# neither references any of the claim's numbers nor performs a numeric
# comparison cannot settle the dispute, so it maps to the neutral status
# `insufficient` (verdict untouched) instead of `dismissed`.
#
# Erring direction is deliberate and matches module doctrine: a wrongly-
# `insufficient` probe leaves a contestation standing (advisory noise, same
# posture as unrunnable); a wrongly-`dismissed` one lets a fabricated number
# pass unchallenged and corrupts reviewer calibration.
_CLAIM_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?![A-Za-z0-9_])")
_CMD_COMPARISON_RE = re.compile(
    r"[<>]=?\s*\d"                     # jq/awk-style: > 250000, >= 4
    r"|==\s*\d"                        # jq equality: == 49
    r"|-(?:eq|ne|ge|gt|le|lt)\s+\d"    # test(1) integer comparisons
)


def _claim_numbers(text: str) -> set:
    """Standalone numbers in a claim, ≥2 digits, comma-stripped.

    Digit runs embedded in identifiers/hashes (``X2.py``, ``4d20b559``) are
    excluded by the word-boundary lookarounds; single digits don't count —
    too often incidental prose ("one of 2 repos"). The fabrication-prone
    class (star counts, sizes, dates) is multi-digit in practice.
    """
    out = set()
    for tok in _CLAIM_NUM_RE.findall(text or ""):
        digits = tok.replace(",", "")
        if len(digits) >= 2:
            out.add(digits)
    return out


def probe_insufficient_for_numbers(claim_text: str, cmd: str) -> str:
    """Why an exit-0 probe cannot settle this numeric claim ('' = it can).

    A probe is sufficient for a numeric claim when it either references one
    of the claim's numbers (comma-insensitive substring — covers `grep 270734`
    and URL/threshold mentions) or contains an explicit numeric comparison
    (`jq -e '.x > N'`, `test N -ge M`). Anything else is an existence check
    wearing a value-check's clothes — exit 0 proves the field/file/endpoint
    exists, never that the disputed number is wrong.
    """
    nums = _claim_numbers(claim_text)
    if not nums:
        return ""
    if _CMD_COMPARISON_RE.search(cmd):
        return ""
    cmd_digits = cmd.replace(",", "")
    if any(n in cmd_digits for n in nums):
        return ""
    return ("claim disputes a number but the probe neither references it "
            "nor compares any value — exit 0 can't settle it")

# Probes are authored by reviewer LLMs (gate Pass-2 contestations, the council
# probe-armed seat, verification_agent) and executed with shell=True. Prompt
# text asking for "read-only" must not be the only thing between a
# hallucinating or injected reviewer and a mutating command — reviewed content
# includes fetched web text, so the prompt-injection chain is real. The guard
# is mechanical: allowlisted head commands only, git restricted to read
# subcommands, find/curl stripped of their mutating flags, no command
# substitution / redirects / chaining (a single pipe between allowlisted
# commands is fine — the prompt's own examples use one). A blocked command
# maps to probe_status="blocked": the concern STANDS, exactly like
# "unrunnable" — the guard can degrade calibration data but can never dismiss
# a claim or grant the reviewer a win.
_PROBE_SAFE_CMDS = {
    "grep", "rg", "test", "[", "ls", "cat", "head", "tail", "wc", "stat",
    "file", "diff", "cmp", "command", "which", "type", "jq", "sort", "uniq",
    "cut", "tr", "find", "git", "curl", "basename", "dirname", "realpath",
}
_GIT_READ_SUBCMDS = {
    "status", "log", "show", "diff", "grep", "ls-files", "ls-remote",
    "rev-parse", "describe", "blame", "shortlog", "cat-file",
}
_FIND_MUTATING_FLAGS = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                        "-fprint", "-fprintf", "-fls"}
_CURL_MUTATING_FLAGS = {"-X", "--request", "-d", "--data", "--data-raw",
                        "--data-binary", "--data-urlencode", "-F", "--form",
                        "-T", "--upload-file", "-o", "--output", "-O",
                        "--remote-name", "-J", "--remote-header-name"}
_SHELL_OPERATORS_BLOCKED = {";", "&", "&&", "||", ">", ">>", "<", "<<", "(", ")"}


def probe_command_rejected(cmd: str) -> str:
    """Why a reviewer-authored probe is not read-only-safe ('' = safe to run)."""
    if "`" in cmd or "$(" in cmd or "${" in cmd or "\n" in cmd or "\r" in cmd:
        return "command substitution / multi-line"
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars="|&;<>()")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError as exc:
        return f"unparsable: {exc}"
    if not tokens:
        return "empty"
    blocked_ops = [t for t in tokens if t in _SHELL_OPERATORS_BLOCKED]
    if blocked_ops:
        return f"shell operator {blocked_ops[0]!r}"
    # Split on single pipes; each segment must independently be allowlisted.
    segments: List[List[str]] = [[]]
    for t in tokens:
        if t == "|":
            segments.append([])
        else:
            segments[-1].append(t)
    for seg in segments:
        if not seg:
            return "empty pipe segment"
        head = seg[0].rsplit("/", 1)[-1]
        if head not in _PROBE_SAFE_CMDS:
            return f"command not allowlisted: {head}"
        if head == "git":
            sub = next((t for t in seg[1:] if not t.startswith("-")), "")
            if sub not in _GIT_READ_SUBCMDS:
                return f"git subcommand not allowlisted: {sub or '?'}"
        if head == "find" and any(t in _FIND_MUTATING_FLAGS for t in seg):
            return "find with mutating action"
        if head == "curl" and any(t in _CURL_MUTATING_FLAGS for t in seg):
            return "curl with mutating/output flag"
    return ""


# The shared prompt fragment. Appended to each adversarial system prompt so every
# adversarial path requests a probe — keeping them in one place prevents the
# divergence that let ungrounded contestations through verification_agent.
SETTLED_BY_COMMAND_CLAUSE = textwrap.dedent("""\
    For each contested claim, ALSO supply `settled_by_command`: a single-line
    shell command (read-only, <15s, exits 0 on success) that would decisively
    settle whether your contestation is correct. Examples:
      - Claim "file X does not exist" → `test -f path/to/X`
      - Claim "tool Y is not installed" → `command -v Y`
      - Claim "branch Z does not exist" → `git ls-remote --heads origin | grep -q Z`
      - Claim "server does not respond" → `curl -fs -m 5 http://localhost:PORT/path`
    When your contestation disputes a NUMBER (a count, size, date, version),
    the command must test the VALUE, not just that the field exists — use a
    threshold with slack for natural drift, e.g. contesting "repo has ~270k
    stars" → `curl -fs -m 5 https://api.github.com/repos/OWNER/NAME | jq -e '.stargazers_count > 250000'`
    A probe that only checks the field exists exits 0 for any value, settles
    nothing, and is scored `insufficient` rather than dismissing your claim.
    Set `settled_by_command` to null when the claim is genuinely un-probe-able
    (subjective interpretation, future-looking, requires an unreachable system).
    Don't invent commands that can't run — null is correct when you can't
    name a concrete check.""").strip()


def probe_contested_claims(claims: list) -> list:
    """Run each claim's `settled_by_command` and reclassify based on outcome.

    Inversion-at-verification, mirrored onto the adversarial reviewer's own
    output: the reviewer generated the contestation AND the probe that would
    settle it. Running the probe makes the ground-truth check mechanical,
    not a second LLM judgment.

    Reclassification rule (first applicable):
      - No `settled_by_command` → mutate in-place, add `probe_status=unprobed`
      - Command fails the read-only guard (`probe_command_rejected`) →
        `probe_status=blocked`, never executed; verdict untouched — the
        concern stands, same neutrality as unrunnable
      - Probe exits 0 against a NUMERIC claim without testing the value
        (`probe_insufficient_for_numbers`) → `probe_status=insufficient`,
        verdict untouched — an existence check can't settle a quantity
        dispute, so nobody wins (2026-08-11: a field-exists grep dismissed
        a 270k-star contestation it structurally could not have refuted).
      - Probe exits 0 → reviewer's contestation was likely wrong about the
        concrete fact: downgrade verdict to "DISMISSED_BY_PROBE", set
        `probe_status=dismissed`. The claim will still appear in the record
        for calibration but won't be appended to user-facing output.
      - Probe exits non-zero → reviewer was right or the probe was wrong;
        keep original verdict, set `probe_status=validated`. Contestation
        stands.
      - Probe raises / times out → leave verdict alone, set
        `probe_status=unrunnable`. Don't grant the reviewer a free win, don't
        grant dismissal either.

    The convention `exit 0 == claim-as-stated-by-reviewer-is-wrong` is what
    the prompt asks for: "a command that would decisively settle whether
    your contestation is correct." If reviewer says "Go not installed",
    probe `command -v go` exits 0 when Go IS installed — contestation wrong.

    Emits a CLAIM_PROBED captain's log event per claim so calibration can
    track the reviewer's false-positive rate over time.
    """
    import subprocess

    out: list = []
    for raw in claims:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        claim = dict(raw)  # shallow copy — never mutate caller's dict
        cmd = claim.get("settled_by_command")
        if not cmd or not isinstance(cmd, str) or not cmd.strip():
            claim["probe_status"] = "unprobed"
            out.append(claim)
            continue

        probe_status = "unrunnable"
        probe_exit = None
        probe_out = ""
        _rejected = probe_command_rejected(cmd)
        if _rejected:
            probe_status = "blocked"
            probe_out = f"[blocked: not read-only-safe — {_rejected}]"
            log.info("claim probe blocked (%s): %s", _rejected, cmd[:120])
            claim["probe_status"] = probe_status
            claim["probe_exit_code"] = probe_exit
            claim["probe_output_preview"] = probe_out
            _emit_claim_probed(claim, cmd, probe_status, probe_exit, probe_out)
            out.append(claim)
            continue
        try:
            # Run the probe in the run-scoped project dir, not Maro's launch
            # cwd — otherwise `git status` / file checks resolve against the
            # wrong directory (the bug that made probes dismiss correct
            # path-mismatch contestations). None → inherit launch cwd (NOW lane).
            from llm import get_default_subprocess_cwd
            _probe_cwd = get_default_subprocess_cwd()
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=PROBE_TIMEOUT_SEC, cwd=_probe_cwd,
            )
            probe_exit = result.returncode
            combined = (result.stdout or "") + (result.stderr or "")
            # The receipt behind a DISMISSED_BY_PROBE verdict flip. Live
            # receipts (measured at the OLD tighter 300 emit re-cut, caps
            # sweep 2026-08-21: n=447, median 30, 57 saturated) had a
            # cap-censored tail — the true over-400 rate is unknowable
            # because the cap destroyed the metric. clip() marks any cut;
            # the verdict itself rides the exit code, not this text.
            probe_out = clip(combined, 2000)
            if result.returncode == 0:
                _why_insufficient = probe_insufficient_for_numbers(
                    safe_str(claim.get("claim", "")), cmd)
                if _why_insufficient:
                    probe_status = "insufficient"
                    # Prefix is our own short reason string; probe_out is
                    # already bounded above — no re-cut (it could eat the
                    # reason's tail or the marker).
                    probe_out = f"[{_why_insufficient}] {probe_out}"
                else:
                    probe_status = "dismissed"
                    original_verdict = safe_str(claim.get("verdict", "CONTESTED"))
                    claim["original_verdict"] = original_verdict
                    claim["verdict"] = "DISMISSED_BY_PROBE"
            else:
                probe_status = "validated"
        except subprocess.TimeoutExpired:
            probe_status = "unrunnable"
            probe_out = f"[timeout after {PROBE_TIMEOUT_SEC}s]"
        except Exception as exc:  # noqa: BLE001 — probe exec is best-effort
            probe_status = "unrunnable"
            probe_out = f"[exec error: {exc}]"

        claim["probe_status"] = probe_status
        claim["probe_exit_code"] = probe_exit
        claim["probe_output_preview"] = probe_out
        _emit_claim_probed(claim, cmd, probe_status, probe_exit, probe_out)
        out.append(claim)

    # Summary log for the whole batch — one line per run, not per claim.
    from collections import Counter
    status_counts = Counter(c.get("probe_status") for c in out if isinstance(c, dict))
    if status_counts:
        log.info("adversarial probe outcomes: %s", dict(status_counts))

    return out


def _emit_claim_probed(claim: dict, cmd: str, probe_status: str,
                       probe_exit, probe_out: str) -> None:
    """Per-claim captain's log event so reviewer calibration can be measured
    instead of guessed. Same shape as closure's modality chart. Blocked
    probes emit too — the guard's rejections are calibration data."""
    try:
        from captains_log import log_event, CLAIM_PROBED
        log_event(
            CLAIM_PROBED,
            subject="claim_probed",
            summary=(
                f"Claim probe {probe_status}: "
                f"{safe_str(claim.get('claim', ''))[:120]}"
            ),
            # Per-field bounds only; a worst-case row (claim + cmd + output
            # all saturated) runs ~4.4KB in the append-forever log — accepted,
            # the fields are individually marked (review r2, 2026-08-21).
            context={
                # Honest preview of the LLM-emitted claim (short by prompt
                # contract, unlike cmd/probe_out) — deliberately narrower
                # than the evidence fields below.
                "claim_preview": safe_str(claim.get("claim", ""))[:200],
                "reviewer_verdict": safe_str(claim.get("original_verdict")
                                              or claim.get("verdict", "")),
                "final_verdict": safe_str(claim.get("verdict", "")),
                # The replay handle for re-running the probe — a cut command
                # is un-runnable, but cmd is LLM-emitted (prompted to be a
                # single line, unenforced) and this log is append-forever, so
                # it gets a generous marked breaker, not no bound (review
                # 2026-08-21: unbounded was the opposite failure mode).
                "probe_command": clip(cmd, 2000),
                "probe_status": probe_status,
                "probe_exit_code": probe_exit,
                # Already bounded (marked clip) at capture — the old [:300]
                # re-cut here saturated 57/447 live receipts (13%) on the
                # calibration surface. Written-only as of 2026-08-21: no
                # consumer reads this field back into a prompt (verdicts ride
                # probe_status); re-check budgets before wiring one that does.
                "probe_output_preview": probe_out,
            },
        )
    except Exception:
        pass
