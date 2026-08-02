#!/usr/bin/env python3
"""Run-scoped terrain memory — remember blocked hosts WITHIN a run.

RUN_TEACHINGS_DESIGN §5b (Jeremy 2026-08-02: *"should be a simple
memory-related thing to remember that certain sites are blocked rather than
churning on learning that over and over; seems like a goal short term memory
issue"*).

The waste this closes is measured, not hypothetical: the cold chlorination
run (4bf7f761) re-attempted the same blocked archives across ~6 steps at
roughly $15, and #5 (9d88acf2) wandered `STEP_TOO_BROAD` ×4 at $32. Nothing
carried "hathitrust.org is 403 from this box" from step N to step N+1 except
the step's own prose result, which the next step's planner does not reliably
act on.

Design notes:
- **Deterministic. No LLM call.** Facts come from the real tool transcript
  the executor already returns (`outcome["tool_events"]`), the same source
  the scavenge and fabrication guards read.
- **Run-scoped, not durable.** This is short-term memory; it dies with the
  run. Promotion to a durable `terrain` teaching (§3/§4) reads this
  accumulator as its evidence source, which also gives §4a a deterministic
  input instead of asking an LLM to notice blocks in prose.
- **Observation only.** Nothing here changes step status or routing; it
  renders one advisory contribution into later steps.
- **Conservative by construction.** Only hard, unambiguous blocks count
  (see `_BLOCK_SIGNALS`) — a 500 or a timeout is transient and must NOT
  teach "give up on this host". False "blocked" beliefs are worse than no
  belief: they would suppress a source that actually works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

# Hard blocks only. Each maps a signal to the short reason rendered to later
# steps. Transient failures (500/502/503, timeouts, connection resets) are
# deliberately absent: they warrant a retry, and recording them as terrain
# would teach the run to abandon a working source.
_BLOCK_SIGNALS: tuple = (
    (re.compile(r"\b403\b|forbidden", re.I), "403 forbidden"),
    (re.compile(r"\b401\b|unauthorized", re.I), "401 unauthorized"),
    (re.compile(r"\b429\b|rate.?limit|too many requests", re.I), "429 rate-limited"),
    (re.compile(r"cloudflare|just a moment|attention required", re.I), "cloudflare challenge"),
    # AWS WAF answers a bot challenge with HTTP *202* and an empty body — a
    # 2xx, so nothing above sees it. Found live 2026-08-02: hup.harvard.edu
    # returned `HTTP/2 202 / x-amzn-waf-action: challenge / content-length: 0`
    # to every probe in run 5accd392, and terrain only caught the host at all
    # because an unrelated path happened to 403. Anchored to the BLOCKING
    # values on purpose: the header name alone also appears in
    # `access-control-expose-headers: x-amzn-waf-action` on *successful*
    # responses, and `x-amzn-waf-action: allow` is a pass — matching the bare
    # name would mark every AWS-fronted host blocked. Bare `202` is never
    # matched either; 202 Accepted is a legitimate success.
    (re.compile(r"x-amzn-waf-action:\s*(?:challenge|captcha|block)", re.I),
     "aws waf challenge"),
    (re.compile(r"\b451\b", re.I), "451 legally unavailable"),
    (re.compile(r"quota (?:exceeded|exhausted)|out of quota", re.I), "quota exhausted"),
    (re.compile(r"paywall|subscription required|log ?in to continue", re.I), "paywalled"),
    (re.compile(r"robots\.txt disallow|blocked by robots", re.I), "robots.txt disallow"),
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+", re.I)

# Cap the rendered block: terrain is a hint, not a wall of text.
MAX_RENDERED_HOSTS = 12


@dataclass
class TerrainFact:
    """One host observed to hard-block, with the evidence that says so."""
    host: str
    reason: str
    first_step: int
    hits: int = 1
    steps: List[int] = field(default_factory=list)


@dataclass
class TerrainMemory:
    """Run-scoped accumulator. Lives on LoopContext; dies with the run."""
    facts: Dict[str, TerrainFact] = field(default_factory=dict)

    def observe(self, host: str, reason: str, step_idx: int) -> bool:
        """Record one hard block. Returns True if this host is newly blocked.

        First reason wins: a host that 403s then later shows a cloudflare
        challenge is still "the first thing that blocked us", which is the
        more actionable fact.
        """
        host = (host or "").strip().lower()
        if not host:
            return False
        existing = self.facts.get(host)
        if existing is None:
            self.facts[host] = TerrainFact(
                host=host, reason=reason, first_step=step_idx, steps=[step_idx])
            return True
        existing.hits += 1
        if step_idx not in existing.steps:
            existing.steps.append(step_idx)
        return False

    def render(self) -> str:
        """One advisory line per blocked host, or "" when nothing is known.

        Empty render is load-bearing: the ContributionLedger's hard contract
        is that zero contributions leave prompts byte-identical.
        """
        if not self.facts:
            return ""
        rows = sorted(self.facts.values(), key=lambda f: (-f.hits, f.host))
        lines = [
            "Known blocked from this box THIS RUN — do not retry these; "
            "state the gap honestly instead:"
        ]
        for f in rows[:MAX_RENDERED_HOSTS]:
            seen = f"{f.hits}× since step {f.first_step}"
            lines.append(f"  - {f.host}: {f.reason} ({seen})")
        if len(rows) > MAX_RENDERED_HOSTS:
            lines.append(f"  …and {len(rows) - MAX_RENDERED_HOSTS} more.")
        return "\n".join(lines)

    def promotable(self, min_steps: int = 2) -> List[TerrainFact]:
        """Facts corroborated across >= min_steps distinct steps.

        The evidence gate for a durable `terrain` teaching (§4): one blocked
        response could be a hiccup; the same host blocking across separate
        steps is an environment fact.
        """
        return [f for f in self.facts.values() if len(f.steps) >= min_steps]


def _host_of(text: str) -> str:
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    try:
        return (urlparse(m.group(0)).hostname or "").lower()
    except ValueError:
        return ""


def scan_tool_events(events: Optional[Iterable[Any]], step_idx: int,
                     memory: TerrainMemory) -> List[str]:
    """Record hard blocks visible in one step's tool transcript.

    Returns the list of newly-blocked hosts (for logging). Never raises —
    terrain memory is an optimization; a parse failure must not touch the
    step's outcome.
    """
    newly: List[str] = []
    if not events:
        return newly
    try:
        for ev in events:
            if not isinstance(ev, dict):
                continue
            # The host comes from the REQUEST side (input), the failure from
            # the RESPONSE side (output/error) — a URL in an error string is
            # not reliably the URL that was requested.
            host = _host_of(str(ev.get("input", "")))
            if not host:
                continue
            blob = f"{ev.get('output', '')} {ev.get('error', '')}"
            if ev.get("is_error"):
                blob += " error"
            for pattern, reason in _BLOCK_SIGNALS:
                if pattern.search(blob):
                    if memory.observe(host, reason, step_idx):
                        newly.append(f"{host} ({reason})")
                    break
    except Exception:
        return newly
    return newly
