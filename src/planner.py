# @lat: [[core-loop#Key Source Files]]  [[poe-identity#Injection Point]]
"""Goal decomposition — multi-plan comparison + heuristic fallback.

Extracted from agent_loop.py for readability and targeted file reads.
The decompose prompt, multi-plan logic, and JSON parsing all live here.

Usage:
    from planner import decompose, DECOMPOSE_SYSTEM
    steps = decompose(goal, adapter, max_steps=8)
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path
from typing import List, Optional
from llm_parse import extract_json

log = logging.getLogger("poe.planner")


# ---------------------------------------------------------------------------
# Decompose system prompt
# ---------------------------------------------------------------------------

DECOMPOSE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous planning agent.
    Decompose a goal into 3-8 concrete, independently-executable steps.
    Each step is a clear action or deliverable, not a vague meta-step.

    STEP GRANULARITY:
    Each step must complete in under 60 seconds of LLM thinking time.
    Never combine "acquire X and analyze X" into one step.

    BAD:  "Research topic X and compile a report"
    GOOD: "Identify 3-5 key sources for topic X"
          "Extract key findings from each source"
          "Synthesize findings into a structured summary"

    BAD:  "Read all Python source files in src/"
    GOOD: "Read the main entry point and map the module dependency graph"
          "Read core modules (agent_loop, memory, skills) and summarize their APIs"
          "Read the I/O layer (telegram, slack, gateway) and note integration points"

    CODE REVIEW: Never read "all" files in a directory. Target 3-5 related files
    per step, at most ~2000 lines. Split 20+ file directories by functional area.
    Setup steps (clone, fetch, install) are their own step — never bundled.

    LONG-RUNNING COMMANDS (tests, builds, installs):
    Never combine execution with analysis — always separate steps.
    BAD:  "Run the full test suite and analyze failures"
    GOOD: "Run pytest -q and capture output to a file"
          "Summarize pass/fail/skip counts and categorize failure types"
    Any external command (pytest, make, npm, docker, git) should ONLY run the
    command and capture output. Analysis is a separate subsequent step.

    OUTCOME-FIRST (Bitter Lesson principle):
    Decompose into OUTCOMES, not procedures. Ask: what is the desired end state?
    BAD:  Goal: "curl the API, parse JSON, filter by volume, sort descending"
          → Steps: "curl the API", "parse the JSON", "filter by volume"...
    GOOD: Goal: same → Step: "Identify top 10 accounts by trading volume"
          (agent discovers whether to use curl, a CLI tool, or a script)

    PARALLEL EXECUTION:
    Mark dependencies with [after:N] or [after:N,M] at the end of the step string.
    Unmarked steps run sequentially (safe default).
    ["Clone the repo",
     "Read core modules [after:1]",
     "Read I/O modules [after:1]",
     "Synthesize findings [after:2,3]"]

    OUTPUT FORMAT:
    Respond ONLY with a JSON array of step strings. No prose, no explanation.
    Each step is ONE sentence under 20 words — a precise work order for an execution agent.
""").strip()


# ---------------------------------------------------------------------------
# Dependency parsing
# ---------------------------------------------------------------------------

import re

_AFTER_RE = re.compile(r'\[after:(\d+(?:,\d+)*)\]\s*$')


def parse_dependencies(steps: List[str]) -> tuple:
    """Parse [after:N,M] tags from step strings.

    Returns:
        (clean_steps, deps) where clean_steps has tags stripped and
        deps is a dict mapping step_index (1-based) → set of dependency indices.
        Steps with no tag depend on the previous step (sequential default).
    """
    clean: List[str] = []
    deps: dict = {}

    for i, step in enumerate(steps, 1):
        m = _AFTER_RE.search(step)
        if m:
            clean.append(_AFTER_RE.sub("", step).rstrip())
            deps[i] = {int(x) for x in m.group(1).split(",")}
        else:
            clean.append(step)
            # Default: depends on previous step (sequential)
            if i > 1:
                deps[i] = {i - 1}
            else:
                deps[i] = set()

    return clean, deps


def build_execution_levels(deps: dict) -> List[List[int]]:
    """Group step indices into execution levels based on dependencies.

    Steps in the same level can run in parallel.
    Returns list of levels, each a list of step indices (1-based).
    """
    n = max(deps.keys()) if deps else 0
    levels: List[List[int]] = []
    completed: set = set()

    while len(completed) < n:
        # Find all steps whose dependencies are satisfied
        ready = [
            i for i in range(1, n + 1)
            if i not in completed and deps.get(i, set()).issubset(completed)
        ]
        if not ready:
            # Circular dependency or missing dep — add all remaining sequentially
            remaining = [i for i in range(1, n + 1) if i not in completed]
            for r in remaining:
                levels.append([r])
                completed.add(r)
            break
        levels.append(ready)
        completed.update(ready)

    return levels


# ---------------------------------------------------------------------------
# JSON step parser
# ---------------------------------------------------------------------------

def parse_steps(content: str, max_steps: int) -> Optional[List[str]]:
    """Extract a JSON step list from LLM response content."""
    steps = extract_json(content, list, log_tag="planner.parse_steps")
    if steps and isinstance(steps, list) and all(isinstance(s, str) for s in steps):
        return [s.strip() for s in steps if s.strip()][:max_steps]
    return None


# ---------------------------------------------------------------------------
# Multi-plan decomposition
# ---------------------------------------------------------------------------

def decompose(
    goal: str,
    adapter,
    max_steps: int,
    verbose: bool = False,
    lessons_context: str = "",
    ancestry_context: str = "",
    skills_context: str = "",
    cost_context: str = "",
) -> List[str]:
    """Decompose a goal into steps.

    Uses multi-plan comparison: generates 3 candidate plans at higher temperature,
    then picks the best one (or composes from all three). Falls back to single
    plan at low temperature, then to heuristic.
    """
    from llm import LLMMessage

    # Inject Poe's persistent identity block (GAP 1 fix — always in context)
    try:
        from poe_self import with_poe_identity
        system = with_poe_identity(DECOMPOSE_SYSTEM)
    except Exception:
        system = DECOMPOSE_SYSTEM
    extras = [x for x in [skills_context, ancestry_context, lessons_context, cost_context] if x]

    # Auto-inject user context if available (capped at 500 chars per file
    # to avoid inflating decomposition token cost)
    try:
        _user_dir = Path(__file__).resolve().parent.parent / "user"
        for _ctx_file in ("CONTEXT.md", "SIGNALS.md"):
            _ctx_path = _user_dir / _ctx_file
            if _ctx_path.exists():
                _ctx = _ctx_path.read_text(encoding="utf-8").strip()[:500]
                if _ctx:
                    extras.append(f"USER CONTEXT ({_ctx_file}):\n{_ctx}")
    except Exception:
        pass

    if extras:
        system = DECOMPOSE_SYSTEM + "\n\n" + "\n\n".join(extras)

    user_msg = f"Goal: {goal}\n\nDecompose into {max_steps} or fewer concrete steps."

    # --- Multi-plan: generate 3 candidates and compose ---
    try:
        candidates: List[List[str]] = []
        for i in range(3):
            resp = adapter.complete(
                [LLMMessage("system", system), LLMMessage("user", user_msg)],
                max_tokens=1024,
                temperature=0.7,  # higher temp for diversity
            )
            parsed = parse_steps(resp.content.strip(), max_steps)
            if parsed:
                candidates.append(parsed)

        if len(candidates) >= 2:
            # Ask a cheap LLM to compare and compose the best plan
            plans_text = "\n\n".join(
                f"Plan {i+1}:\n" + json.dumps(c, indent=2)
                for i, c in enumerate(candidates)
            )
            compose_resp = adapter.complete(
                [
                    LLMMessage("system",
                        "You are a plan reviewer. Given multiple candidate step plans for the same goal, "
                        "produce the single best plan by selecting the strongest steps from each. "
                        "Prefer plans with: (1) concrete file/module names over vague descriptions, "
                        "(2) separation of commands from analysis, (3) balanced step sizes. "
                        "Output ONLY a JSON array of step strings."),
                    LLMMessage("user",
                        f"Goal: {goal}\n\n{plans_text}\n\n"
                        f"Compose the best plan ({max_steps} steps max). JSON array only."),
                ],
                max_tokens=1024,
                temperature=0.1,
            )
            composed = parse_steps(compose_resp.content.strip(), max_steps)
            if composed:
                log.info("decompose multi-plan: %d candidates → %d composed steps",
                         len(candidates), len(composed))
                if verbose:
                    import sys
                    print(f"[poe] decomposed into {len(composed)} steps (multi-plan from {len(candidates)} candidates)",
                          file=sys.stderr, flush=True)
                return composed
            # Fall through if compose failed — use the first valid candidate
            log.debug("decompose compose failed, using first candidate")
            return candidates[0]

        elif len(candidates) == 1:
            log.info("decompose multi-plan: only 1 valid candidate")
            return candidates[0]

    except Exception as exc:
        log.info("decompose multi-plan failed, trying single plan: %s", exc)

    # --- Single plan fallback (original approach) ---
    try:
        resp = adapter.complete(
            [LLMMessage("system", system), LLMMessage("user", user_msg)],
            max_tokens=1024,
            temperature=0.2,
        )
        parsed = parse_steps(resp.content.strip(), max_steps)
        if parsed:
            return parsed
    except Exception as exc:
        log.warning("decompose LLM failed, falling back to heuristic: %s", exc)
        if verbose:
            import sys
            print(f"[poe] decompose LLM call failed, using heuristic: {exc}", file=sys.stderr, flush=True)

    # --- Heuristic fallback ---
    try:
        import orch
        _heuristic_steps = orch.decompose_goal(goal, max_steps=max_steps)
        log.info("decompose heuristic produced %d steps (goal=%r)", len(_heuristic_steps), goal[:60])
        return _heuristic_steps
    except Exception:
        # Last resort: split on sentences
        return [goal]


# ---------------------------------------------------------------------------
# Verification step injection
# ---------------------------------------------------------------------------

_RESEARCH_KEYWORDS = {
    "research", "analyze", "investigate", "study", "evidence", "clinical",
    "pubmed", "find out", "is it true", "verify", "compare", "review",
    "assess", "evaluate", "risk", "benefit", "safety",
}


def maybe_add_verification_step(steps: List[str], goal: str, max_steps: int = 8) -> List[str]:
    """Append an adversarial verification step for research-type goals.

    If the goal contains research keywords, adds a final step that
    cross-checks key claims from prior steps with adversarial framing.
    This catches sycophantic confirmation bias — the model will build
    a case for whatever it's asked, so we explicitly ask it to argue
    against the prior findings.

    Only adds the step if there's room under max_steps.
    """
    goal_lower = goal.lower()
    if not any(kw in goal_lower for kw in _RESEARCH_KEYWORDS):
        return steps

    # Don't exceed max_steps
    if len(steps) >= max_steps:
        return steps

    # Don't add if the last step already looks like verification
    if steps and any(v in steps[-1].lower() for v in ("verify", "check", "validate", "contra")):
        return steps

    n = len(steps)
    verify_step = (
        f"Adversarial verification: for each key claim from prior steps, "
        f"search for contradicting evidence. Flag claims with weak or "
        f"contested evidence. Rate each finding: strong/moderate/weak/contested. "
        f"[after:{n}]"
    )
    log.info("injecting verification step for research goal")
    return steps + [verify_step]
