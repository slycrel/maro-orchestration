#!/usr/bin/env python3
"""Phase 35 P1/P2: Constraint harness — pre-execution action validation.

Checks step text for risky patterns before execution.  Classifies risk as
LOW / MEDIUM / HIGH.  Callers decide what to do with each level; the typical
policy is:

    LOW     → proceed, no log entry needed
    MEDIUM  → proceed, log a warning
    HIGH    → block the step, record as stuck

Phase 35 P2 adds a HITL (Human-in-the-Loop) gating taxonomy. Actions are
classified by *semantic action tier*:

    READ     → no gate; proceed freely (observing only)
    WRITE    → warn gate; log and proceed (persistent side-effects)
    DESTROY  → block gate; requires explicit approval (irreversible)
    EXTERNAL → confirm gate; requires confirmation (leaves this system)

Use ``classify_action_tier()`` to determine the tier and ``hitl_policy()``
to get a combined policy decision.

Constraints are pluggable: add a callable to CONSTRAINT_REGISTRY to extend.
Each constraint is a function(step_text, goal) -> Optional[ConstraintFlag].

No LLM calls — this is a zero-cost, always-on safety layer that runs in-process
before the subprocess is spawned.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger("poe.constraint")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ConstraintFlag:
    """A single constraint violation found in a step."""
    name: str           # machine name, e.g. "destructive_op"
    risk: str           # "LOW" | "MEDIUM" | "HIGH"
    detail: str         # human-readable description of what was found
    pattern: str        # the text fragment that triggered this flag


@dataclass
class ConstraintResult:
    """Result of running all constraints against a step."""
    allowed: bool                       # False only if any HIGH-risk flag found
    risk_level: str                     # worst risk level across all flags
    flags: List[ConstraintFlag] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.allowed

    @property
    def reason(self) -> Optional[str]:
        if not self.flags:
            return None
        high = [f for f in self.flags if f.risk == "HIGH"]
        if high:
            return "; ".join(f.detail for f in high)
        return "; ".join(f.detail for f in self.flags)

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level,
            "flags": [{"name": f.name, "risk": f.risk, "detail": f.detail} for f in self.flags],
        }


# ---------------------------------------------------------------------------
# Individual constraint checkers
# ---------------------------------------------------------------------------

# Patterns that indicate destructive filesystem operations.
_DESTRUCTIVE_PATTERNS = [
    (r"\brm\s+-rf?\b",                       "HIGH",  "rm -r / rm -rf found in step"),
    (r"\bshutil\.rmtree\b",                  "HIGH",  "shutil.rmtree found in step"),
    (r"\bos\.remove\b.*\*",                  "HIGH",  "os.remove with wildcard found"),
    (r"\btruncate\s+--size\s+0\b",           "HIGH",  "truncate --size 0 found"),
    (r"\bdrop\s+table\b",                    "HIGH",  "DROP TABLE found in step"),
    (r"\bdelete\s+from\b",                   "MEDIUM","DELETE FROM found in step"),
    (r"\boverwrite\s+(all|every|the)\b",     "MEDIUM","broad overwrite intent found"),
    (r"\bwipe\s+(disk|drive|data|all)\b",    "HIGH",  "wipe disk/drive/all found"),
    (r"\bformat\s+/dev/\b",                  "HIGH",  "format /dev/ found in step"),
]

# Patterns that indicate access to secrets / credentials.
_SECRET_PATTERNS = [
    (r"~/\.env\b",                          "HIGH",  "access to ~/.env"),
    (r"~/secrets/",                         "HIGH",  "access to ~/secrets/"),
    (r"/etc/passwd\b",                      "HIGH",  "access to /etc/passwd"),
    (r"/etc/shadow\b",                      "HIGH",  "access to /etc/shadow"),
    (r"\.ssh/id_",                          "HIGH",  "access to SSH private key"),
    (r"openclaw\.json\b",                   "MEDIUM","access to openclaw.json (contains credentials)"),
    (r"\bapi[_\s-]?key\b.*\breadfile\b",    "MEDIUM","reading API key file"),
    (r"\bpassword\b.*\breadfile\b",         "MEDIUM","reading password file"),
    (r"credentials\.json\b",               "HIGH",  "access to credentials.json"),
    (r"\.aws/credentials\b",               "HIGH",  "access to AWS credentials"),
]

# Patterns that indicate writing to paths outside expected workspace.
_PATH_ESCAPE_PATTERNS = [
    (r"\bwrite\b.*/etc/",                   "HIGH",  "write to /etc/"),
    (r"\bwrite\b.*/usr/",                   "HIGH",  "write to /usr/"),
    (r"\bwrite\b.*/bin/",                   "HIGH",  "write to /bin/"),
    (r"\bwrite\b.*/sbin/",                  "HIGH",  "write to /sbin/"),
    (r"\bwrite\b.*/root/",                  "HIGH",  "write to /root/"),
    (r"\bwrite\b.*~/\.config/",             "MEDIUM","write to ~/.config/"),
    (r"\bwrite\b.*~/\.ssh/",                "HIGH",  "write to ~/.ssh/"),
    (r"\bcat\b.*>.*/etc/",                  "HIGH",  "shell redirect to /etc/"),
]

# Patterns that indicate dangerous network operations.
_NETWORK_PATTERNS = [
    (r"\bcurl\b.*-X\s*(DELETE|PUT)\b",      "MEDIUM","curl with DELETE/PUT method"),
    (r"\bwget\b.*--post\b",                 "MEDIUM","wget POST request"),
    (r"\bcurl\b.*(paypal|stripe|twilio)",   "HIGH",  "curl to payment API"),
    (r"\bsend\s+(an?\s+)?(email|sms|text|message)\s+(to|about)\b",
                                            "MEDIUM","send email/SMS/message"),
    (r"\bpost\s+(publicly|to\s+twitter|to\s+x\.com)\b",
                                            "MEDIUM","post publicly / to Twitter"),
    (r"\bpublish\s+(to\s+)?pypi\b",         "HIGH",  "publish to PyPI"),
    (r"\bgit\s+push\b.*--force\b",          "HIGH",  "git push --force"),
]

# Patterns that indicate code execution with user-controlled input.
_EXEC_PATTERNS = [
    (r"\beval\s*\(",                        "HIGH",  "eval() call found"),
    (r"\bexec\s*\(",                        "MEDIUM","exec() call found"),
    (r"\bos\.system\s*\(",                  "MEDIUM","os.system() call found"),
    (r"\bsubprocess\.call\s*\(.*shell\s*=\s*True","MEDIUM","subprocess with shell=True"),
    (r"\b__import__\s*\(",                  "HIGH",  "dynamic __import__() found"),
]

# All pattern groups with their constraint name
_ALL_PATTERNS: List[tuple] = [
    ("destructive_op",  _DESTRUCTIVE_PATTERNS),
    ("secret_access",   _SECRET_PATTERNS),
    ("path_escape",     _PATH_ESCAPE_PATTERNS),
    ("unsafe_network",  _NETWORK_PATTERNS),
    ("unsafe_exec",     _EXEC_PATTERNS),
]


def _check_patterns(
    constraint_name: str,
    patterns: list,
    step_text: str,
    goal: str,
) -> List[ConstraintFlag]:
    """Run a list of (regex, risk, detail) patterns against step + goal text."""
    combined = (step_text + " " + goal).lower()
    flags = []
    for pattern_str, risk, detail in patterns:
        m = re.search(pattern_str, combined, re.I)
        if m:
            flags.append(ConstraintFlag(
                name=constraint_name,
                risk=risk,
                detail=detail,
                pattern=m.group(0),
            ))
    return flags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

#: Pluggable constraint registry. Add callables here to extend checking.
#: Each callable signature: (step_text: str, goal: str) -> List[ConstraintFlag]
CONSTRAINT_REGISTRY: List[Callable[[str, str], List[ConstraintFlag]]] = []


def _load_dynamic_constraints() -> List[tuple]:
    """Load evolver-generated constraint patterns from memory/dynamic-constraints.jsonl.

    Returns list of (constraint_name, [(pattern, risk, detail)]) tuples, same
    shape as _ALL_PATTERNS entries.  Returns empty list if file missing or unreadable.
    """
    try:
        from orch_items import memory_dir
        path = memory_dir() / "dynamic-constraints.jsonl"
    except ImportError:
        path = Path.cwd() / "memory" / "dynamic-constraints.jsonl"

    if not path.exists():
        return []

    patterns: List[tuple] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                pat = entry.get("pattern", "")
                risk = entry.get("risk", "MEDIUM")
                detail = entry.get("detail", f"dynamic guardrail: {pat[:60]}")
                if pat:
                    patterns.append((pat, risk, detail))
            except Exception:
                continue
    except Exception:
        return []

    if not patterns:
        return []
    return [("dynamic_guardrail", patterns)]


def check_step_constraints(step_text: str, goal: str = "") -> ConstraintResult:
    """Run all registered constraints + built-in patterns against a step.

    Args:
        step_text: The step description string.
        goal: The overall goal (additional context for pattern matching).

    Returns:
        ConstraintResult with allowed=False if any HIGH-risk flags found.
    """
    all_flags: List[ConstraintFlag] = []

    # Built-in pattern checks
    for constraint_name, patterns in _ALL_PATTERNS:
        all_flags.extend(_check_patterns(constraint_name, patterns, step_text, goal))

    # Evolver-generated dynamic constraints (loaded from memory/dynamic-constraints.jsonl)
    for constraint_name, patterns in _load_dynamic_constraints():
        all_flags.extend(_check_patterns(constraint_name, patterns, step_text, goal))

    # Pluggable constraint extensions
    for checker in CONSTRAINT_REGISTRY:
        try:
            all_flags.extend(checker(step_text, goal))
        except Exception:
            pass  # constraint failures must never block legitimate work

    # Determine worst risk level
    if not all_flags:
        return ConstraintResult(allowed=True, risk_level="LOW", flags=[])

    risk_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    worst = max(all_flags, key=lambda f: risk_order.get(f.risk, 0))
    worst_level = worst.risk

    # HIGH flags block execution; MEDIUM/LOW are logged but allowed
    allowed = worst_level != "HIGH"

    return ConstraintResult(allowed=allowed, risk_level=worst_level, flags=all_flags)


def register_constraint(fn: Callable[[str, str], List[ConstraintFlag]]) -> None:
    """Register an additional constraint checker.

    Args:
        fn: callable(step_text, goal) -> List[ConstraintFlag]
    """
    CONSTRAINT_REGISTRY.append(fn)


# ---------------------------------------------------------------------------
# Phase 35 P2 — HITL gating taxonomy
# ---------------------------------------------------------------------------

# Action tiers (semantic classification of what kind of thing a step does).
ACTION_TIER_READ = "READ"
ACTION_TIER_WRITE = "WRITE"
ACTION_TIER_DESTROY = "DESTROY"
ACTION_TIER_EXTERNAL = "EXTERNAL"

# Gate policies: what the system should do when an action of each tier is found.
# none    → proceed silently
# warn    → log a warning, then proceed
# confirm → pause and require explicit approval before proceeding
# block   → halt; never execute without a human override
_TIER_GATE = {
    ACTION_TIER_READ:     "none",
    ACTION_TIER_WRITE:    "warn",
    ACTION_TIER_DESTROY:  "block",
    ACTION_TIER_EXTERNAL: "confirm",
}

# Patterns that indicate irreversible destruction.
# IMPORTANT: these match against natural-language step descriptions, not just
# shell commands.  Bare words like "remove" or "delete" cause false blocks on
# steps like "remove outliers from the data".  Patterns here must require
# system/command context (files, databases, packages, processes).
_DESTROY_TIER_PATTERNS = [
    r"\brm\s+-",                             # rm -rf, rm -r (not bare "rm")
    r"\brm\s+/",                             # rm /some/path
    r"\bshutil\.rmtree\b",
    r"\bdrop\s+table\b",
    r"\btruncate\s+(table|--size)\b",
    r"\bwipe\s+(disk|drive|data|all|volume)\b",
    r"\bformat\s+/dev/",
    r"\buninstall\s+\w",                     # uninstall <package>
    r"\bpurge\s+\w",                         # purge <package>
    r"\bdestroy\s+(server|instance|cluster|database|volume|bucket|stack)\b",
    r"\berase\s+(disk|drive|partition|volume)\b",
    r"\boverwrite\s+(all|every|the)\b",
    r"\bdelete\s+(file|dir|folder|repo|branch|database|table|bucket|volume|server|instance)\b",
    r"\bremove\s+(file|dir|folder|repo|branch|package|service|container|image)\b",
    r"\bgit\s+branch\s+-[dD]\b",
    r"\bkill\s+-9\b",
    r"\bkillall\b",
]

# Patterns that indicate actions leaving this system (network, APIs, git remote).
# Same principle: require enough context that natural-language analysis steps
# ("submit a summary") don't false-match.
_EXTERNAL_TIER_PATTERNS = [
    r"\bcurl\s+",                            # curl <url> (not bare "curl")
    r"\bwget\s+",
    r"\bgit\s+push\b",
    r"\bgit\s+pull\b",
    r"\bsend\s+(an?\s+)?(email|sms|text|message)\s+(to|via)\b",
    r"\bpost\s+(to\s+)(twitter|x\.com|slack|telegram|discord)\b",
    r"\bdeploy\s+(to|on|the)\b",
    r"\bupload\s+(to|the|a)\b",
    r"\bapi\s+call\b",
    r"\bwebhook\b",
    r"\bbroadcast\s+(to|via|on)\b",
    r"\btweet\b",
]

# Patterns that indicate file/state writes (persistent but not irreversible).
# Same principle as DESTROY/EXTERNAL: require system context so that
# "save your findings" or "write a summary" don't trigger WRITE tier.
_WRITE_TIER_PATTERNS = [
    r"\bwrite\s+(to\s+)?(file|disk|config|/)",
    r"\bsave\s+(to\s+)?(file|disk|/)",
    r"\bappend\s+(to\s+)?(file|log|/)",
    r"\bmkdir\b",
    r"\btouch\s+/",
    r"\bcopy\s+(file|/)",
    r"\bmove\s+(file|/)",
    r"\brename\s+(file|/)",
    r"\binsert\s+into\b",
    r"\bgit\s+commit\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bsed\s+-i\b",
    r"\btee\s+(-a\s+)?/",
]

# READ is the default — no explicit patterns needed (anything not matched above).


def classify_action_tier(step_text: str, goal: str = "") -> str:
    """Classify the semantic action tier of a step.

    Returns one of ACTION_TIER_READ, ACTION_TIER_WRITE, ACTION_TIER_DESTROY,
    or ACTION_TIER_EXTERNAL.  Precedence (highest first): DESTROY > EXTERNAL > WRITE > READ.

    Args:
        step_text: The step description string.
        goal: Optional goal context (not currently used in classification).

    Returns:
        One of the ACTION_TIER_* constants.
    """
    combined = step_text.lower()

    for pat in _DESTROY_TIER_PATTERNS:
        if re.search(pat, combined, re.I):
            return ACTION_TIER_DESTROY

    for pat in _EXTERNAL_TIER_PATTERNS:
        if re.search(pat, combined, re.I):
            return ACTION_TIER_EXTERNAL

    for pat in _WRITE_TIER_PATTERNS:
        if re.search(pat, combined, re.I):
            return ACTION_TIER_WRITE

    return ACTION_TIER_READ


def hitl_policy(step_text: str, goal: str = "") -> dict:
    """Return a combined HITL + constraint policy decision for a step.

    Runs both the existing constraint checks (risk level) and the semantic
    action-tier classification, then folds them into a single policy dict.

    Returns a dict with keys:
        tier          str   — ACTION_TIER_* constant
        gate          str   — "none" | "warn" | "confirm" | "block"
        allowed       bool  — False if HIGH risk or DESTROY tier
        risk_level    str   — "LOW" | "MEDIUM" | "HIGH"
        flags         list  — ConstraintFlag dicts from check_step_constraints()
        reason        str | None

    The ``allowed`` field is False when *either* the constraint harness raises
    HIGH or the tier is DESTROY.  The gate field is the *stricter* of the two
    signals (tier gate vs risk-derived gate).
    """
    constraint_result = check_step_constraints(step_text, goal)
    tier = classify_action_tier(step_text, goal)
    tier_gate = _TIER_GATE[tier]

    # Derive a gate from risk level for comparison.
    _risk_gate = {"HIGH": "block", "MEDIUM": "warn", "LOW": "none"}
    risk_gate = _risk_gate.get(constraint_result.risk_level, "none")

    # Pick the stricter gate.
    _gate_order = {"none": 0, "warn": 1, "confirm": 2, "block": 3}
    effective_gate = tier_gate if _gate_order[tier_gate] >= _gate_order[risk_gate] else risk_gate

    allowed = constraint_result.allowed and tier != ACTION_TIER_DESTROY

    if not allowed:
        log.debug("hitl_policy BLOCKED: tier=%s risk=%s gate=%s step=%r",
                  tier, constraint_result.risk_level, effective_gate, step_text[:80])
    elif effective_gate not in ("none",):
        log.debug("hitl_policy gate=%s: tier=%s risk=%s step=%r",
                  effective_gate, tier, constraint_result.risk_level, step_text[:60])

    return {
        "tier": tier,
        "gate": effective_gate,
        "allowed": allowed,
        "risk_level": constraint_result.risk_level,
        "flags": [{"name": f.name, "risk": f.risk, "detail": f.detail} for f in constraint_result.flags],
        "reason": constraint_result.reason,
    }
