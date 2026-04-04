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
# Anti-sycophancy rules (injected into every planning prompt)
# ---------------------------------------------------------------------------

# Stolen from gstack/office-hours: explicit constraints prevent drift toward
# validation-seeking in long planning chains. The planner must take positions,
# not hedge — hedged plans produce hedged steps that produce hedged outcomes.
ANTI_SYCOPHANCY_RULES = textwrap.dedent("""\
    ANTI-SYCOPHANCY RULES (non-negotiable):
    - Take a position. State your recommendation clearly — never answer with "it depends" alone.
    - If the goal contains a bad assumption or is too vague to decompose, name it.
    - State what evidence or information would change your plan.
    - Never open with affirmations: no "Great!", "Certainly!", "Of course!", "Happy to help!".
    - Prefer honest uncertainty over false confidence. "I don't know X, so step N reads X first"
      is correct. Pretending to know X and producing a wrong plan is not.
""").strip()


# ---------------------------------------------------------------------------
# Large-scope review detection
# ---------------------------------------------------------------------------

_LARGE_SCOPE_KEYWORDS = frozenset({
    "entire codebase", "whole codebase", "full codebase",
    "entire repo", "whole repo", "full repo",
    "adversarial review", "comprehensive review", "complete review",
    "codebase review", "code review of", "full audit", "complete audit",
    "review the codebase", "review the repo", "audit the codebase",
    "audit the repo", "review all", "review every", "all modules",
    "all files", "every module",
})


def _is_large_scope_review(goal: str) -> bool:
    """Return True if the goal covers a scope too large for a single flat step list."""
    low = goal.lower()
    return any(kw in low for kw in _LARGE_SCOPE_KEYWORDS)


# Staged-pass decomposition prompt: when a goal is too broad for 8 flat steps,
# break it into domain-area passes each small enough to execute within budget.
_STAGED_PASS_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous planning agent.
    The goal covers a scope too large for a single execution pass.
    Decompose it into 3-5 STAGED PASSES — thematic sub-goals each independently executable.

    Each pass covers one domain area. Passes should be roughly equal in effort.
    Use [after:N] syntax for a final synthesis pass that depends on all prior passes.

    Example output for a codebase review:
    [
      "Pass 1/4 — Architecture: read CLAUDE.md, ROADMAP.md, map src/ modules and dependency graph",
      "Pass 2/4 — Core execution: audit agent_loop.py, step_exec.py, director.py for exec/analyze patterns",
      "Pass 3/4 — Tests + integrations: review test coverage, read telegram.py, slack_listener.py for security",
      "Pass 4/4 — Synthesize: compile findings from passes 1-3 into adversarial report with severity ratings [after:1,2,3]"
    ]

    OUTPUT FORMAT: JSON array of pass strings. No prose. Each pass is one sentence under 25 words.
""").strip()

_STAGED_PASS_SYSTEM = _STAGED_PASS_SYSTEM + "\n\n" + ANTI_SYCOPHANCY_RULES


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

    HARD RULE — exec and analyze are ALWAYS separate steps (no exceptions):
    Any step that runs a command MUST NOT also describe analyzing, interpreting,
    summarizing, evaluating, or checking the command's output.
    FORBIDDEN patterns (will be automatically split and count against your plan quality):
      "run X and analyze"    "execute X and interpret"   "run X and check results"
      "grep X and identify"  "fetch X and evaluate"      "run X and count failures"
      "invoke X and assess"  "call X and determine"      "run X and see if"
    REQUIRED pattern:
      Step N:   Run <command> and save output to artifacts/<name>.txt
      Step N+1: Read artifacts/<name>.txt and <analysis goal>
    This applies to: pytest, make, npm, docker, git, grep, find, curl, rg,
    and ANY shell command whose output needs to be reasoned about.

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

DECOMPOSE_SYSTEM = DECOMPOSE_SYSTEM + "\n\n" + ANTI_SYCOPHANCY_RULES


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

    # --- Large-scope review: staged-pass decomposition ---
    # When the goal spans a scope too broad for 8 flat steps (e.g. "review the entire codebase"),
    # decompose into domain-area passes. Each pass is independently executable within budget.
    # This is the first level of the dynamic tree traversal — the director or subsequent loops
    # handle each pass as its own sub-goal.
    if _is_large_scope_review(goal):
        try:
            resp = adapter.complete(
                [LLMMessage("system", _STAGED_PASS_SYSTEM),
                 LLMMessage("user", f"Goal: {goal}\n\nDecompose into 3-5 staged passes.")],
                max_tokens=512,
                temperature=0.2,
            )
            staged = parse_steps(resp.content.strip(), max_steps)
            if staged:
                log.info("decompose staged-pass: %d passes for large-scope goal", len(staged))
                if verbose:
                    import sys
                    print(f"[poe] large-scope goal → staged-pass decomposition: {len(staged)} passes",
                          file=sys.stderr, flush=True)
                return staged
        except Exception as exc:
            log.info("staged-pass decomposition failed, falling back to multi-plan: %s", exc)

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
