# @lat: [[core-loop#Key Source Files]]
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

log = logging.getLogger("maro.planner")


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
    You are an autonomous planning agent.
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
# Cuts-first planning (Qix-cuts decree, 2026-07-10)
# ---------------------------------------------------------------------------
# Jeremy's pattern: 0-4 narrowing cuts off the rectangle, then bounded work
# inside the lines, re-drawing when new information surfaces. The structural
# difference from scope generation (scope.py, one armchair inversion call):
# real cuts are EVIDENCE-FED — a cut, a cheap peek at the world, another cut.
# v0 gives two rounds of narrowing: draw_cuts() commits armchair constraints
# (prior knowledge / recall) and up to 2 cheap probes; the plan becomes
# [probes..., boundary step]; the boundary step is expanded at execution time
# WITH the probe findings in context (loop_execute.py boundary expansion).
# See docs/CONSTRAINT_ORCHESTRATION_DESIGN.md for the lineage.

from dataclasses import dataclass, field as _dc_field

BOUNDARY_TAG = "[boundary]"

_BOUNDARY_RE_STR = r"\[boundary\]"


def is_boundary_step(step: str) -> bool:
    """True when a step carries the [boundary] tag (anywhere in the text —
    tags like [after:N] may follow it)."""
    return bool(step) and BOUNDARY_TAG in step


def strip_boundary_tag(step: str) -> str:
    """Remove the [boundary] tag from a step string."""
    return re.sub(_BOUNDARY_RE_STR, "", step or "").strip()


@dataclass
class Cuts:
    """The narrowing pass drawn before decomposition.

    known_constraints — cuts drawable from the armchair (prior knowledge,
        recall, goal text), each with its basis. These bound the space now.
    probes — 0-2 single cheap actions (one search, one file read, one
        command) that would collapse the biggest remaining unknown.
    bounded — True when the space is already narrow enough to plan fully
        without probes.
    remainder — one sentence describing the work left inside the boundary
        once probes land; becomes the boundary step's text.
    """
    known_constraints: List[str] = _dc_field(default_factory=list)
    probes: List[str] = _dc_field(default_factory=list)
    bounded: bool = True
    remainder: str = ""
    raw_text: str = ""

    def is_empty(self) -> bool:
        return not (self.known_constraints or self.probes)

    def to_markdown(self) -> str:
        parts = ["## Cuts (committed constraints)"]
        for c in self.known_constraints:
            parts.append(f"- {c}")
        if self.probes:
            parts.append("\n## Probes (evidence before planning)")
            parts.extend(f"- {p}" for p in self.probes)
        if self.remainder:
            parts.append(f"\n## Bounded remainder\n{self.remainder}")
        return "\n".join(parts)


CUTS_SYSTEM = textwrap.dedent("""\
    You are the narrowing pass that runs BEFORE planning. Your job is NOT to
    plan the work — it is to collapse the possibility space so the plan that
    follows is small and cheap.

    Think like an expert who has done this before: what do you already know
    that eliminates most of the search space? What single cheap look at the
    world would eliminate most of what remains?

    Produce, as JSON:

    {
      "known_constraints": [
        "<constraint that bounds the space> (basis: <prior knowledge | goal text | provided context>)"
      ],
      "probes": [
        "<ONE cheap action - one web search, one file read, one command - that collapses the biggest unknown>"
      ],
      "bounded": <true if the space is already narrow enough to plan without probes>,
      "remainder": "<one sentence: the work left inside the boundary once probes land>"
    }

    Rules:
    - 0-2 probes, NEVER more. A probe is a single cheap action, not research.
      If you want three probes, pick the one that eliminates the most space.
    - known_constraints are commitments, not observations. "Maverik stations
      often sell ethanol-free gas (basis: prior knowledge)" is a cut — it
      turns 'search everywhere' into 'check Maveriks first'. "Gas stations
      sell gas" is not a cut.
    - If the goal is already narrow (a lookup, a single file edit, a known
      procedure), say bounded=true with no probes. Do not manufacture probes.
    - Do not plan the remainder. One sentence only. The plan happens after
      the probes report back.

    Output ONLY the JSON object. No prose.
""").strip()


def draw_cuts(
    goal: str,
    adapter,
    *,
    context_extras: str = "",
    max_tokens: int = 700,
) -> Optional[Cuts]:
    """Draw narrowing cuts for a goal before decomposition.

    One LLM call. Non-fatal: returns None on any failure — callers fall
    through to normal decomposition.
    """
    if not goal or not adapter:
        return None
    from llm import LLMMessage
    system = CUTS_SYSTEM
    if context_extras:
        system = system + "\n\nContext (prior knowledge available to you):\n" + context_extras
    try:
        resp = adapter.complete(
            [LLMMessage("system", system), LLMMessage("user", f"Goal: {goal}")],
            max_tokens=max_tokens,
            temperature=0.2,
            no_tools=True,
            purpose="cuts",
        )
    except Exception as exc:
        log.warning("cuts: adapter.complete failed: %s", exc)
        return None
    content = (getattr(resp, "content", "") or "").strip()
    if not content:
        log.warning("cuts: LLM returned empty content")
        return None
    data = extract_json(content, dict, log_tag="planner.draw_cuts")
    if not data or not isinstance(data, dict):
        log.warning("cuts: response did not parse as JSON object; raw=%r", content[:200])
        return None
    constraints = [str(c).strip() for c in data.get("known_constraints", []) if str(c).strip()]
    probes = [str(p).strip() for p in data.get("probes", []) if str(p).strip()][:2]
    cuts = Cuts(
        known_constraints=constraints,
        probes=probes,
        bounded=bool(data.get("bounded", not probes)),
        remainder=str(data.get("remainder", "")).strip(),
        raw_text=content,
    )
    log.info("cuts: %d constraint(s), %d probe(s), bounded=%s",
             len(cuts.known_constraints), len(cuts.probes), cuts.bounded)
    return cuts


def _cuts_plan(cuts: Cuts, goal: str) -> List[str]:
    """Build the probe-first plan from a Cuts result.

    Probes run as normal sequential steps; the boundary step is expanded at
    execution time with their findings in context (loop_execute.py). The
    remainder text keeps the original goal visible so expansion doesn't
    drift from the ask.
    """
    remainder = cuts.remainder or f"complete the goal: {goal}"
    boundary = (
        f"Plan and complete the remaining bounded work using findings from "
        f"the prior steps: {remainder} {BOUNDARY_TAG}"
    )
    return list(cuts.probes) + [boundary]


# ---------------------------------------------------------------------------
# Decompose system prompt
# ---------------------------------------------------------------------------

DECOMPOSE_SYSTEM = textwrap.dedent("""\
    You are an autonomous planning agent.
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
    - N sequential rate-limited network operations in ONE step: per-item
      cooldowns and API rate limits stack, so "fetch all 13 posts with
      per-post cooldowns" cannot fit one step. Batch at most ~5 such
      operations per step ("Fetch posts 1-5...", "Fetch posts 6-10...").
    When in doubt, split. An extra step costs nothing; a timeout wastes the whole budget.

    GOAL PRIORITY ORDER (binding):
    When the goal states an explicit priority order ("in priority order:",
    "priority 1 ... priority 2", "first X, then Y"), your step order MUST
    follow it: every step serving the first priority comes before any step
    serving a later one. Runs die mid-plan on budget or time — an exhausted
    budget must strand the LAST priorities, never the first. Do not reorder
    by convenience, topic affinity, or expected ease.

    NO ORPHAN READ STEPS (cost model):
    Every step pays a fixed overhead before any work happens (fresh worker
    session boot + full context re-injection — measured ~30-45s per step).
    A step that ONLY reads a file/artifact and hands the content to a later
    step spends that entire overhead on zero work. Fold the read into the
    step that consumes it:
    BAD:  "Read artifacts/timings.json"
          "Extract the top bottlenecks from the timings [after:1]"
    GOOD: "Read artifacts/timings.json and extract the top bottlenecks"
    This does NOT relax the exec/analyze HARD RULE above — commands stay
    separate from analysis of their output. It applies to READS: reading a
    file and reasoning about it is one step, never two.

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

    STEP DESCRIPTION STYLE:
    Describe the TASK or OUTCOME, not the shell commands to accomplish it.
    BAD:  "Clone repo (rm -rf first to clean up)"
    BAD:  "Run git clone https://... && cd ... && npm install"
    GOOD: "Clone the repository and install dependencies"
    GOOD: "Set up the project workspace"
    The execution agent will decide how to accomplish the task. Don't embed
    shell commands in step text — they confuse the safety layer and make steps
    fragile if the environment differs.

    OUTPUT FORMAT:
    Respond ONLY with a JSON array of step strings. No prose, no explanation.
    Each step is ONE sentence under 20 words — a precise work order for an execution agent.
""").strip()

DECOMPOSE_SYSTEM = DECOMPOSE_SYSTEM + "\n\n" + ANTI_SYCOPHANCY_RULES


# ---------------------------------------------------------------------------
# Goal-stated priority order (BACKLOG #23c)
# ---------------------------------------------------------------------------
# r3 specimen (run 5c40740e): the goal said "Remaining work, in priority
# order: 1. X/Twitter sweep ..." and the planner scheduled Reddit first,
# consuming the whole budget there — across THREE loops on that project no
# step transcript contained a single twitter invocation. The static prompt
# block above states the rule; this detector makes it loud for the specific
# goal, and reaches the lanes the extras don't (staged-pass, compose).

import re

_PRIORITY_ORDER_RE = re.compile(
    r"\bin\s+(?:priority\s+order|order\s+of\s+priority)\b"
    r"|\bpriority\s+order\s*:"
    r"|\bpriorit(?:y|ies)\s*:\s*\n?\s*1[.)]"
    r"|\bpriority\s+1\b",
    re.IGNORECASE,
)


def goal_states_priority_order(goal: str) -> bool:
    """True when the goal text declares an explicit priority order."""
    return bool(goal) and bool(_PRIORITY_ORDER_RE.search(goal))


_PRIORITY_DIRECTIVE = (
    "THIS GOAL STATES AN EXPLICIT PRIORITY ORDER — it is BINDING. Schedule "
    "ALL work for the first-listed priority before any work for a later "
    "one; budget exhaustion must strand the last priorities, never the "
    "first. Do not reorder by convenience, topic affinity, or expected ease."
)


# ---------------------------------------------------------------------------
# Goal-stated step-count ceiling (BACKLOG: step-count constraint ignored)
# ---------------------------------------------------------------------------
# Specimen: goal said "2-3 steps maximum"; the planner produced 7 steps /
# 296-line module + docs / 1.55M tokens / $0.21 for what one shell step
# answers. Same family as #23c binding-priority above: a deterministic
# goal-text detector, zero LLM, plus a loud directive injected into every
# prompt lane — and, unlike priority, MECHANICAL enforcement on the final
# plan (_enforce_step_ceiling), because a ceiling is checkable
# (len(plan) vs N) where ordering is not.
#
# Conservative by design: fires only when a step count is paired with an
# explicit bound qualifier (max / maximum of / at most / no more than /
# or fewer / limit to). Bare counts ("explain X in 3 steps"), plan-content
# references ("document the 3 steps of the deploy process", "step 2 of the
# migration"), and non-step numbers never fire. Word-numbers one–ten ARE
# supported — a single shared _STEP_NUM alternation keeps the patterns
# readable, and "three steps max" is exactly as binding as "3 steps max".

_STEP_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_STEP_NUM = r"(?:\d{1,2}|" + "|".join(_STEP_NUM_WORDS) + r")"

_STEP_CEILING_PATTERNS = [
    # "3 steps max" / "2-3 steps maximum" / "2 to 3 steps max"
    re.compile(
        rf"\b({_STEP_NUM})(?:(?:\s*[-–]\s*|\s+to\s+)({_STEP_NUM}))?"
        rf"\s+steps?\s+(?:maximum|max)\b", re.IGNORECASE),
    # "maximum of 3 steps" / "a max of 3 steps"
    re.compile(rf"\b(?:maximum|max)\s+of\s+({_STEP_NUM})\s+steps?\b", re.IGNORECASE),
    # "at most 3 steps"
    re.compile(rf"\bat\s+most\s+({_STEP_NUM})\s+steps?\b", re.IGNORECASE),
    # "no more than 3 steps"
    re.compile(rf"\bno\s+more\s+than\s+({_STEP_NUM})\s+steps?\b", re.IGNORECASE),
    # "3 steps or fewer" / "in 3 steps or less"
    re.compile(rf"\b({_STEP_NUM})\s+steps?\s+or\s+(?:fewer|less)\b", re.IGNORECASE),
    # "limit (the plan) to 3 steps" / "limited to 3 steps"
    re.compile(
        rf"\blimit(?:ed)?\s+(?:\S+\s+){{0,2}}?to\s+({_STEP_NUM})\s+steps?\b",
        re.IGNORECASE),
]

# "single step" / "one step" only WITH a bounding qualifier — bare "one step"
# is ambiguous ("one step of the process") and stays out.
_SINGLE_STEP_RE = re.compile(
    r"\b(?:just|only)\s+(?:a\s+)?(?:one|single)\s+step\b"
    r"|\b(?:in|as)\s+a\s+single\s+step\b"
    r"|\b(?:one|single)\s+step\s+only\b",
    re.IGNORECASE,
)


def _step_count_value(token: str) -> int:
    token = token.strip().lower()
    return int(token) if token.isdigit() else _STEP_NUM_WORDS[token]


def goal_step_ceiling(goal: str) -> Optional[int]:
    """Detect an explicit step-count ceiling in goal text — deterministic, zero LLM.

    Returns the ceiling (upper end of a range like "2-3 steps maximum"), or
    None when the goal does not bound its own plan. Conservative: a number
    near "steps" fires only alongside an explicit bound qualifier.
    """
    if not goal:
        return None
    for pattern in _STEP_CEILING_PATTERNS:
        m = pattern.search(goal)
        if m:
            bound = [g for g in m.groups() if g][-1]  # range → upper end
            ceiling = _step_count_value(bound)
            return ceiling if ceiling >= 1 else None
    if _SINGLE_STEP_RE.search(goal):
        return 1
    return None


_STEP_CEILING_DIRECTIVE = (
    "THIS GOAL STATES AN EXPLICIT STEP-COUNT CEILING — it is BINDING. The "
    "plan MUST contain AT MOST {n} step(s). Merge or condense related work "
    "to fit. The ceiling is a maximum, not a target — fewer steps is fine, "
    "more is a violation. Preserve the goal's full intent and any "
    "verification inside the ceiling."
)


# ---------------------------------------------------------------------------
# Dependency parsing
# ---------------------------------------------------------------------------

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
        parsed: List[str] = []
        for raw in steps:
            step = raw.strip()
            if not step:
                continue
            # Drop malformed dependency-only placeholders like "[after:4]":
            # they become empty tasks downstream and can stall autonomous runs.
            if _AFTER_RE.fullmatch(step):
                continue
            parsed.append(step)
        return parsed[:max_steps]
    return None


# ---------------------------------------------------------------------------
# Step-ceiling enforcement (mechanical — the ceiling is binding, not advisory)
# ---------------------------------------------------------------------------

def _enforce_step_ceiling(
    plan: Optional[List[str]],
    ceiling: Optional[int],
    goal: str,
    adapter,
    *,
    lane: str,
) -> Optional[List[str]]:
    """Mechanically enforce a goal-stated step ceiling on a final plan.

    The directive was already in every prompt, so an oversized plan here
    means advisory failed once: issue ONE corrective re-ask to merge/condense,
    then hard-truncate to the first `ceiling` steps if the retry still
    exceeds it (or errors). Never pads short plans — the ceiling is a max,
    not a target. No-op when no ceiling was detected or the plan already fits.
    """
    if ceiling is None or not plan or len(plan) <= ceiling:
        return plan
    from llm import LLMMessage
    oversized = plan
    try:
        resp = adapter.complete(
            [
                LLMMessage("system",
                           DECOMPOSE_SYSTEM + "\n\n"
                           + _STEP_CEILING_DIRECTIVE.format(n=ceiling)),
                LLMMessage("user",
                           f"Goal: {goal}\n\n"
                           f"You returned {len(plan)} steps:\n"
                           + json.dumps(plan, indent=2) + "\n\n"
                           f"The goal explicitly bounds the plan to at most "
                           f"{ceiling} steps. Merge/condense into at most "
                           f"{ceiling} steps, preserving the goal's full "
                           f"intent and any verification step. Respond ONLY "
                           f"with a JSON array of step strings."),
            ],
            max_tokens=1024,
            temperature=0.1,
        )
        # Parse with a cap ABOVE the ceiling so non-compliance stays visible
        # (parse_steps truncates silently at its max).
        condensed = parse_steps(resp.content.strip(), max(len(plan), ceiling))
        if condensed:
            if len(condensed) <= ceiling:
                log.info("step-ceiling (%s lane): corrective re-ask condensed "
                         "%d → %d step(s) (ceiling %d)",
                         lane, len(plan), len(condensed), ceiling)
                return condensed
            oversized = condensed
    except Exception as exc:
        log.warning("step-ceiling (%s lane): corrective re-ask failed (%s) — "
                    "hard-truncating", lane, exc)
    truncated = oversized[:ceiling]
    log.warning(
        "step-ceiling (%s lane): plan exceeds the goal-stated ceiling after "
        "one corrective re-ask (%d > %d) — hard-truncated to the first %d "
        "step(s); dropped: %s",
        lane, len(oversized), ceiling, ceiling,
        "; ".join(s[:60] for s in oversized[ceiling:]),
    )
    try:
        from captains_log import log_event, STEP_CEILING_ENFORCED
        log_event(
            STEP_CEILING_ENFORCED,
            subject="step_ceiling_enforced",
            summary=(f"Plan hard-truncated {len(oversized)} → {ceiling} "
                     f"step(s): goal-stated ceiling held after one "
                     f"corrective re-ask ({lane} lane)."),
            context={
                "goal_preview": goal[:200],
                "ceiling": ceiling,
                "returned_steps": len(oversized),
                "lane": lane,
                "dropped_steps": [s[:120] for s in oversized[ceiling:]],
            },
        )
    except Exception:
        pass
    return truncated


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
    allow_cuts: bool = True,
) -> List[str]:
    """Decompose a goal into steps.

    Uses multi-plan comparison: generates 3 candidate plans at higher temperature,
    then picks the best one (or composes from all three). Falls back to single
    plan at low temperature, then to heuristic.

    Args:
        thinking_budget: If set, enables extended thinking on the composition
            call (the final plan merge). Passed through to adapter.complete().
        allow_cuts: Gate for the cuts-first narrowing pass (also requires the
            `planner.cuts_first` config flag). Boundary expansion and milestone
            expansion pass False — a re-decompose inside the loop must not
            draw a second round of cuts.
    """
    from llm import LLMMessage

    # The framework plans as a neutral orchestration role — no persona by
    # default. Set `planner.persona` in config to a persona name (e.g. "poe")
    # to have planning wear that persona's identity. Unset = neutral role.
    system = DECOMPOSE_SYSTEM
    try:
        from config import get as _cfg_get
        _persona_name = _cfg_get("planner.persona", None)
    except Exception:
        _persona_name = None
    if _persona_name:
        try:
            from persona import PersonaRegistry
            _spec = PersonaRegistry().load(str(_persona_name))
            if _spec and _spec.system_prompt.strip():
                system = f"## Who I Am\n\n{_spec.system_prompt.strip()}\n\n---\n\n{DECOMPOSE_SYSTEM}"
        except Exception:
            pass
    extras = [x for x in [skills_context, ancestry_context, lessons_context, cost_context] if x]

    # Auto-inject user context if available (capped at 500 chars per file
    # to avoid inflating decomposition token cost). Resolution: workspace
    # overlay (~/.maro/workspace/user/) wins over the repo/install templates —
    # a fresh install gets the neutral shipped copies, never someone else's
    # personal context (SF-5/docs-02).
    try:
        from config import user_file as _user_file
        for _ctx_file in ("GOALS.md", "CONTEXT.md", "SIGNALS.md"):
            _ctx_path = _user_file(_ctx_file)
            if _ctx_path is not None:
                _ctx = _ctx_path.read_text(encoding="utf-8").strip()[:500]
                if _ctx:
                    extras.append(f"USER CONTEXT ({_ctx_file}):\n{_ctx}")
    except Exception:
        pass

    # lat.md architecture context: inject relevant knowledge graph nodes for meta-work
    # (goals touching Maro's own systems). TF-IDF selection, zero-LLM, zero cost.
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
        print(f"[maro] decompose scope estimate: {_goal_scope}", file=sys.stderr, flush=True)

    # Goal-stated priority order (BACKLOG #23c): binding, injected loudly.
    # Detected BEFORE the cuts-first block — the probe path returns early, so
    # the directive must already be in extras for draw_cuts to see it
    # (fix-validation-23 run 75fe8b4e: cuts-first swallowed the directive and
    # ordering held only because the cuts call happened to be careful).
    _has_priority_order = goal_states_priority_order(goal)
    if _has_priority_order:
        extras.append(_PRIORITY_DIRECTIVE)
        log.info("decompose: goal states an explicit priority order — binding directive injected")

    # Goal-stated step-count ceiling (same family as #23c): binding, injected
    # loudly, and mechanically enforced on the final plan
    # (_enforce_step_ceiling). Detected BEFORE the cuts-first block for the
    # same reason as priority: the probe path returns early, so the directive
    # must already be in extras for draw_cuts to see it — and it rides into
    # the boundary expansion via loop_execute's carry.
    _step_ceiling = goal_step_ceiling(goal)
    if _step_ceiling is not None:
        extras.append(_STEP_CEILING_DIRECTIVE.format(n=_step_ceiling))
        log.info("decompose: goal states a step-count ceiling of %d — "
                 "binding directive injected", _step_ceiling)

    # Cuts-first narrowing (Qix-cuts decree). Gated: caller allows it AND the
    # config flag is on (default OFF — one extra LLM call per goal, no silent
    # spend for fresh installs). Wide/deep goals skip — staged-pass already
    # narrows by domain. When cuts emit probes, the plan is probes + boundary
    # step and we return WITHOUT committing a full plan over the unbounded
    # space; the real plan is drawn at boundary expansion with probe evidence
    # in hand. When cuts say bounded (no probes), the committed constraints
    # inject into the normal decompose as boundary context.
    if allow_cuts and _goal_scope in ("narrow", "medium"):
        _cuts_on = False
        try:
            from config import get as _cfg_get_cuts
            _cuts_on = bool(_cfg_get_cuts("planner.cuts_first", False))
        except Exception:
            _cuts_on = False
        if _cuts_on:
            cuts = draw_cuts(goal, adapter, context_extras="\n\n".join(extras))
            if cuts is not None and not cuts.is_empty():
                try:
                    from captains_log import log_event, CUTS_DRAWN
                    log_event(
                        CUTS_DRAWN,
                        subject="cuts_drawn",
                        summary=(f"{len(cuts.known_constraints)} constraint(s), "
                                 f"{len(cuts.probes)} probe(s), bounded={cuts.bounded}"),
                        context={
                            "goal_preview": goal[:200],
                            "constraints": cuts.known_constraints[:6],
                            "probes": cuts.probes,
                            "bounded": cuts.bounded,
                            "remainder": cuts.remainder[:300],
                        },
                    )
                except Exception:
                    pass
                # Visibility: persist the cuts beside the run's other artifacts.
                try:
                    from runs import source_dir as _cuts_source_dir
                    _cuts_dir = _cuts_source_dir()
                    if _cuts_dir is not None:
                        (_cuts_dir / "cuts.md").write_text(
                            cuts.to_markdown() + "\n", encoding="utf-8")
                except Exception:
                    pass
                if cuts.probes:
                    plan = _cuts_plan(cuts, goal)
                    log.info("decompose cuts-first: %d probe(s) + boundary step", len(cuts.probes))
                    if verbose:
                        import sys
                        print(f"[maro] cuts-first: {len(cuts.known_constraints)} constraint(s), "
                              f"{len(cuts.probes)} probe(s) → boundary plan",
                              file=sys.stderr, flush=True)
                    return plan
                # Bounded without probes: plan inside the lines.
                extras.append("COMMITTED CONSTRAINTS (cuts — plan inside these bounds):\n"
                              + "\n".join(f"- {c}" for c in cuts.known_constraints))

    if extras:
        system = DECOMPOSE_SYSTEM + "\n\n" + "\n\n".join(extras)

    # Inject scope hint into system prompt for medium goals so the planner calibrates step count.
    # A detected ceiling suppresses the hint — "expect 5-10 steps" must not
    # contradict "3 steps maximum"; the binding directive wins outright.
    if _step_ceiling is None:
        if _goal_scope == "medium":
            system += "\n\nSCOPE HINT: This goal is medium complexity — expect 5-10 steps."
        elif _goal_scope == "narrow":
            system += "\n\nSCOPE HINT: This goal is narrow — expect 1-4 steps. Do not over-decompose."

    # Clamp the asked-for step count to a detected ceiling. parse_steps keeps
    # the UNclamped max so an over-ceiling response stays visible to
    # _enforce_step_ceiling (parse truncation is silent — no re-ask, no log).
    _prompt_max = max_steps if _step_ceiling is None else min(max_steps, _step_ceiling)
    user_msg = f"Goal: {goal}\n\nDecompose into {_prompt_max} or fewer concrete steps."

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
            _staged_system = _STAGED_PASS_SYSTEM
            if _has_priority_order:
                _staged_system += "\n\n" + _PRIORITY_DIRECTIVE
            if _step_ceiling is not None:
                _staged_system += "\n\n" + _STEP_CEILING_DIRECTIVE.format(n=_step_ceiling)
            resp = adapter.complete(
                [LLMMessage("system", _staged_system),
                 LLMMessage("user", f"Goal: {goal}\n\nDecompose into 3-5 staged passes.")],
                **_staged_kwargs,
            )
            staged = parse_steps(resp.content.strip(), max_steps)
            if staged:
                log.info("decompose staged-pass: %d passes for large-scope goal", len(staged))
                if verbose:
                    import sys
                    print(f"[maro] large-scope goal → staged-pass decomposition: {len(staged)} passes",
                          file=sys.stderr, flush=True)
                return _enforce_step_ceiling(staged, _step_ceiling, goal, adapter, lane="staged")
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
                return _enforce_step_ceiling(simple_steps, _step_ceiling, goal, adapter,
                                             lane="narrow")
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
                        "Output ONLY a JSON array of step strings."
                        + ("\n\n" + _PRIORITY_DIRECTIVE if _has_priority_order else "")
                        + ("\n\n" + _STEP_CEILING_DIRECTIVE.format(n=_step_ceiling)
                           if _step_ceiling is not None else "")),
                    LLMMessage("user",
                        f"Goal: {goal}\n\n{plans_text}\n\n"
                        f"Compose the best plan ({_prompt_max} steps max). JSON array only."),
                ],
                **_compose_kwargs,
            )
            composed = parse_steps(compose_resp.content.strip(), max_steps)
            if composed:
                log.info("decompose multi-plan: %d candidates → %d composed steps",
                         len(candidates), len(composed))
                if verbose:
                    import sys
                    print(f"[maro] decomposed into {len(composed)} steps (multi-plan from {len(candidates)} candidates)",
                          file=sys.stderr, flush=True)
                return _enforce_step_ceiling(composed, _step_ceiling, goal, adapter,
                                             lane="compose")
            # Fall through if compose failed — use the first valid candidate
            log.debug("decompose compose failed, using first candidate")
            return _enforce_step_ceiling(candidates[0], _step_ceiling, goal, adapter,
                                         lane="compose-fallback")

        elif len(candidates) == 1:
            log.info("decompose multi-plan: only 1 valid candidate")
            return _enforce_step_ceiling(candidates[0], _step_ceiling, goal, adapter,
                                         lane="single-candidate")

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
            return _enforce_step_ceiling(parsed, _step_ceiling, goal, adapter,
                                         lane="single-plan")
    except Exception as exc:
        log.warning("decompose LLM failed, falling back to heuristic: %s", exc)
        if verbose:
            import sys
            print(f"[maro] decompose LLM call failed, using heuristic: {exc}", file=sys.stderr, flush=True)

    # --- Fallback: the goal verbatim as a single step ---
    # The old heuristic here (orch.decompose_goal, split on [.;]) manufactured
    # nonsense goals from step text containing filenames or [after:N] markup
    # ("...flagged-claims.md [after:3,4,5]" → "md [after:3,4,5]" as its own
    # step), and it fired exactly when the LLM was failing — i.e. when the
    # system was least able to recover. Traced across ~40 error/stuck runs,
    # 2026-05-13..17. One verbatim step degrades gracefully; a regex chop
    # compounds the outage. (Goal-brain pressure test, 2026-06-10.)
    log.info("decompose falling back to single verbatim step (goal=%r)", goal[:60])
    return [goal]


# decompose_to_dag removed 2026-07-02 — zero production callers (agent_loop.py
# calls parse_dependencies/build_execution_levels directly instead), test-only.
# See docs/REFACTOR_PLAN.md Tier 1.

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

    # A goal-stated step ceiling binds here too — verification injection runs
    # AFTER decompose (loop_planning._decompose) and must not push a
    # ceiling-respecting plan over its bound.
    ceiling = goal_step_ceiling(goal)
    if ceiling is not None and len(steps) >= ceiling:
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
