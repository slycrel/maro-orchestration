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
import textwrap
from typing import List

from llm_parse import safe_str

log = logging.getLogger("maro.claim_probe")

# Read-only probes must be quick; the reviewer is told <15s.
PROBE_TIMEOUT_SEC = 15


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
            probe_out = combined[:400]
            if result.returncode == 0:
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

        # Per-claim captain's log event so reviewer calibration can be
        # measured instead of guessed. Same shape as closure's modality chart.
        try:
            from captains_log import log_event, CLAIM_PROBED
            log_event(
                CLAIM_PROBED,
                subject="claim_probed",
                summary=(
                    f"Claim probe {probe_status}: "
                    f"{safe_str(claim.get('claim', ''))[:120]}"
                ),
                context={
                    "claim_preview": safe_str(claim.get("claim", ""))[:200],
                    "reviewer_verdict": safe_str(claim.get("original_verdict")
                                                  or claim.get("verdict", "")),
                    "final_verdict": safe_str(claim.get("verdict", "")),
                    "probe_command": cmd[:300],
                    "probe_status": probe_status,
                    "probe_exit_code": probe_exit,
                    "probe_output_preview": probe_out[:300],
                },
            )
        except Exception:
            pass

        out.append(claim)

    # Summary log for the whole batch — one line per run, not per claim.
    from collections import Counter
    status_counts = Counter(c.get("probe_status") for c in out if isinstance(c, dict))
    if status_counts:
        log.info("adversarial probe outcomes: %s", dict(status_counts))

    return out
