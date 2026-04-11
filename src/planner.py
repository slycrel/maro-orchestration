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
# Goal scope estimation (Phase 58: pre-decompose classifier)
# ---------------------------------------------------------------------------
# Classifies goal complexity BEFORE decomposition so the planner can route
# accordingly: skip multi-plan for narrow goals, use staged-pass for wide goals.
# Zero-LLM heuristic — cheap, always available, <1ms.
# ---------------------------------------------------------------------------

_NARROW_SCOPE_KEYWORDS = frozenset({
    "what is", "what are", "list the", "show me", "find the", "look up",
    "check if", "does the", "is there", "how many", "which file",
    "what value", "what's the", "print the", "get the", "read the config",
    "check the", "what does", "who is",
})

_WIDE_SCOPE_KEYWORDS = frozenset({
    "entire codebase", "whole codebase", "full codebase",
    "entire repo", "whole repo", "full repo",
    "adversarial review", "comprehensive review", "complete review",
    "codebase review", "code review of", "full audit", "complete audit",
    "review the codebase", "review the repo", "audit the codebase",
    "audit the repo", "review all", "review every", "all modules",
    "all files", "every module",
    "research and analyze", "research and build", "research and implement",
    "weeks of", "months of", "long-term", "multi-day", "multi-week",
})

_DEEP_SCOPE_KEYWORDS = frozenset({
    "build a complete", "build a full", "design and implement", "architect and build",
    "from scratch", "production-ready", "enterprise-grade",
    "self-improving", "autonomous system", "learn everything about",
})


def estimate_goal_scope(goal: str) -> str:
    """Classify goal as narrow / medium / wide / deep using zero-LLM heuristics.

    Returns:
        "narrow"  — simple lookup, 1-3 steps expected
        "medium"  — moderate multi-step work, standard decompose
        "wide"    — larger than it looks, staged-pass preferred
        "deep"    — sub-goal recursion required, milestone decomposition

    Used by decompose() to route planning strategy before the LLM call.
    """
    low = goal.lower()
    word_count = len(goal.split())

    if any(kw in low for kw in _DEEP_SCOPE_KEYWORDS):
        return "deep"
    if any(kw in low for kw in _WIDE_SCOPE_KEYWORDS):
        return "wide"
    if word_count <= 8 and any(kw in low for kw in _NARROW_SCOPE_KEYWORDS):
        return "narrow"
    if word_count <= 12 and not any(
        kw in low for kw in ("research", "analyze", "implement", "build", "create", "design")
    ):
        return "narrow"
    # Default to medium for everything else
    return "medium"


def _is_large_scope_review(goal: str) -> bool:
    """Return True if the goal covers a scope too large for a single flat step list.

    Delegates to estimate_goal_scope for consistency — goal is wide or deep.
    """
    return estimate_goal_scope(goal) in ("wide", "deep")


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

    STEP GRANULARITY — STREAM, DON'T BATCH:
    Think of steps as a pipeline, not a monolith. Each step reads ONE thing, emits
    a finding, and hands off. Synthesis reads accumulated findings — not the sources again.

    The atomic unit is: ONE file read OR one command execution. Never two files in one step.
    Steps are cheap. Timeouts are expensive. Always split when uncertain.

    BAD:  "Read agent_loop.py, memory.py, and skills.py and summarize their APIs"
    GOOD: "Read agent_loop.py and note its entry points and state machine"
          "Read memory.py and note lesson extraction and reflection patterns"
          "Read skills.py and note scoring, promotion, and stemmer logic"
          "Synthesize findings from prior steps into architecture summary"

    BAD:  "Research topic X and compile a report"
    GOOD: "List the 3-5 most relevant sources for topic X"
          "Read source 1 and extract key findings"
          "Read source 2 and extract key findings"
          "Synthesize findings from all sources into a structured summary"

    SURVEY FIRST: If you don't know the file list or scope, make the first step a survey:
    "List all modules in src/ and categorize by function" — then subsequent steps
    read one file at a time based on what the survey found.

    CODE REVIEW: Never read more than ONE file per step. Split 20+ file directories
    by having a survey step first, then one read step per file of interest.
    Setup steps (clone, fetch, install) are their own step — never bundled.

    HARD RULE — exec and analyze are ALWAYS separate steps (no exceptions):
    Any step that runs a command MUST NOT also describe analyzing, interpreting,
    summarizing, evaluating, or checking the command's output.
    FORBIDDEN patterns (will be automatically split and count against your plan quality):
      "run X and analyze"    "execute X and interpret"   "run X and check results"
      "grep X and identify"  "fetch X and evaluate"      "run X and count failures"
      "invoke X and assess"  "call X and determine"      "run X and see if"
      "run X and read Y"     "run X and review Y"        "run X and establish"
    REQUIRED pattern:
      Step N:   Run <command> and save output to artifacts/<name>.txt
      Step N+1: Read artifacts/<name>.txt and <analysis goal>
    This applies to: pytest, make, npm, docker, git, grep, find, curl, rg,
    and ANY shell command whose output needs to be reasoned about.

    TIME BUDGET (guideline, not a gate):
    A subprocess step has roughly 5 minutes before it times out. Warning signs:
    - Reading more than ONE file in one step
    - Running a script AND reading any additional file in the same step
    - A "setup" action (clone, install, configure) bundled with "explore" (read, analyze)
    When in doubt, split. An extra step costs nothing; a timeout wastes the whole budget.

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
    thinking_budget: Optional[int] = None,
) -> List[str]:
    """Decompose a goal into steps.

    Uses multi-plan comparison: generates 3 candidate plans at higher temperature,
    then picks the best one (or composes from all three). Falls back to single
    plan at low temperature, then to heuristic.

    Args:
        thinking_budget: If set, enables extended thinking on the composition
            call (the final plan merge). Passed through to adapter.complete().
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

    # lat.md architecture context: inject relevant knowledge graph nodes for meta-work
    # (goals touching Poe's own systems). TF-IDF selection, zero-LLM, zero cost.
    # Only injects if relevant (score > 0). Empty string = no injection (no noise).
    try:
        from lat_inject import inject_relevant_nodes as _lat_inject
        _lat_ctx = _lat_inject(goal)
        if _lat_ctx:
            extras.append(_lat_ctx)
    except Exception:
        pass

    # Phase 58: pre-decompose scope estimate. Classifies goal complexity before the
    # LLM planner runs so we can route accordingly (skip multi-plan for narrow goals,
    # use staged-pass for wide/deep goals, inject scope hint for medium goals).
    _goal_scope = estimate_goal_scope(goal)
    if verbose:
        import sys
        print(f"[poe] decompose scope estimate: {_goal_scope}", file=sys.stderr, flush=True)

    if extras:
        system = DECOMPOSE_SYSTEM + "\n\n" + "\n\n".join(extras)

    # Inject scope hint into system prompt for medium goals so the planner calibrates step count.
    if _goal_scope == "medium":
        system += "\n\nSCOPE HINT: This goal is medium complexity — expect 5-10 steps."
    elif _goal_scope == "narrow":
        system += "\n\nSCOPE HINT: This goal is narrow — expect 1-4 steps. Do not over-decompose."

    user_msg = f"Goal: {goal}\n\nDecompose into {max_steps} or fewer concrete steps."

    # --- Wide/deep goals: staged-pass decomposition ---
    # When scope estimate is wide or deep, decompose into domain-area passes.
    # Each pass is independently executable within budget.
    # Previously gated on _is_large_scope_review (keyword match). Now uses the
    # general scope estimator (Phase 58: scope estimation before decomposition).
    if _goal_scope in ("wide", "deep"):
        try:
            _staged_kwargs: dict = {"max_tokens": 512, "temperature": 0.2}
            if thinking_budget:
                _staged_kwargs["thinking_budget"] = thinking_budget
            resp = adapter.complete(
                [LLMMessage("system", _STAGED_PASS_SYSTEM),
                 LLMMessage("user", f"Goal: {goal}\n\nDecompose into 3-5 staged passes.")],
                **_staged_kwargs,
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
    # Skip multi-plan for narrow goals — single shot is sufficient and saves 3 LLM calls.
    if _goal_scope == "narrow":
        try:
            resp = adapter.complete(
                [LLMMessage("system", system), LLMMessage("user", user_msg)],
                max_tokens=512,
                temperature=0.3,
            )
            simple_steps = parse_steps(resp.content.strip(), max_steps)
            if simple_steps:
                log.info("decompose narrow: single-shot %d steps", len(simple_steps))
                return simple_steps
        except Exception as exc:
            log.info("narrow single-shot failed, falling back to multi-plan: %s", exc)

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
            _compose_kwargs: dict = {
                "max_tokens": 1024,
                "temperature": 0.1,
            }
            if thinking_budget:
                _compose_kwargs["thinking_budget"] = thinking_budget
            compose_resp = adapter.complete(
                [
                    LLMMessage("system",
                        "You are a plan reviewer. Given multiple candidate step plans for the same goal, "
                        "produce the single best plan by selecting the strongest steps from each. "
                        "Prefer plans with: (1) concrete file/module names over vague descriptions, "
                        "(2) separation of commands from analysis, (3) atomic steps — one file or one "
                        "command per step, never merged. MORE steps is better than FEWER larger steps. "
                        "NEVER merge two steps that read different files, even if they seem related. "
                        "Output ONLY a JSON array of step strings."),
                    LLMMessage("user",
                        f"Goal: {goal}\n\n{plans_text}\n\n"
                        f"Compose the best plan ({max_steps} steps max). JSON array only."),
                ],
                **_compose_kwargs,
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
# Structured DAG decomposition
# ---------------------------------------------------------------------------

def decompose_to_dag(
    goal: str,
    adapter,
    max_steps: int = 8,
    verbose: bool = False,
    **kwargs,
) -> tuple:
    """Decompose a goal into a structured DAG with explicit dependency metadata.

    Calls ``decompose()`` (which already instructs the LLM to emit ``[after:N]``
    tags for parallel steps) then parses the result with ``parse_dependencies``
    and ``build_execution_levels`` so callers get a fully structured graph rather
    than raw strings.

    This is the coordinator-as-agent pattern from open-multi-agent: an LLM agent
    decomposes the goal into a dependency graph at runtime. The returned metadata
    is ready to feed directly into ``_run_steps_dag()``.

    Args:
        goal:      The high-level goal to decompose.
        adapter:   LLM adapter to use for decomposition.
        max_steps: Maximum number of steps to produce.
        **kwargs:  Forwarded to ``decompose()`` (lessons_context, ancestry_context, etc.).

    Returns:
        Tuple of (clean_steps, deps, levels, parallel_levels):
          - clean_steps:     List[str] — step texts with [after:N] tags stripped.
          - deps:            Dict[int, Set[int]] — 1-based step → set of dep indices.
          - levels:          List[List[int]] — steps grouped by topological level.
          - parallel_levels: List[List[int]] — levels that contain >1 step (parallelizable).
    """
    raw_steps = decompose(goal, adapter, max_steps=max_steps, verbose=verbose, **kwargs)
    clean_steps, deps = parse_dependencies(raw_steps)
    levels = build_execution_levels(deps)
    parallel_levels = [lvl for lvl in levels if len(lvl) > 1]
    if verbose and parallel_levels:
        import logging
        logging.getLogger("poe.planner").info(
            "decompose_to_dag: %d steps, %d levels, %d parallelizable",
            len(clean_steps), len(levels), len(parallel_levels),
        )
    return clean_steps, deps, levels, parallel_levels


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
