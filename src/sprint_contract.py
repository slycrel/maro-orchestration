#!/usr/bin/env python3
"""Phase 19: Sprint Contracts for Poe orchestration.

Before any Feature Worker starts, it negotiates a contract with Inspector
defining explicit testable success criteria. Inspector grades against the
contract post-hoc. GAN principle: grading is always a separate LLM call from
the worker that produced the result.

Usage:
    from sprint_contract import negotiate_contract, grade_contract, save_contract
    contract = negotiate_contract("Build a research pipeline", "Automate Polymarket research")
    grade = grade_contract(contract, "Analyzed 10 wallets...")
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SprintContract:
    contract_id: str          # uuid[:8]
    feature_id: str
    feature_title: str
    mission_goal: str
    success_criteria: List[str]       # 2-5 explicit testable criteria
    acceptance_keywords: List[str]    # keywords that must appear in completed work
    created_at: str
    negotiated_by: str                # "llm" | "heuristic"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SprintContract":
        return cls(
            contract_id=d.get("contract_id", str(uuid.uuid4())[:8]),
            feature_id=d.get("feature_id", ""),
            feature_title=d.get("feature_title", ""),
            mission_goal=d.get("mission_goal", ""),
            success_criteria=d.get("success_criteria", []),
            acceptance_keywords=d.get("acceptance_keywords", []),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            negotiated_by=d.get("negotiated_by", "heuristic"),
        )


@dataclass
class ContractGrade:
    contract_id: str
    feature_id: str
    passed: bool
    criteria_results: List[dict]    # [{criterion, passed, evidence}]
    score: float                    # 0.0-1.0
    feedback: str
    graded_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ContractGrade":
        return cls(
            contract_id=d.get("contract_id", ""),
            feature_id=d.get("feature_id", ""),
            passed=d.get("passed", False),
            criteria_results=d.get("criteria_results", []),
            score=float(d.get("score", 0.0)),
            feedback=d.get("feedback", ""),
            graded_at=d.get("graded_at", datetime.now(timezone.utc).isoformat()),
        )


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

_NEGOTIATE_SYSTEM = """\
You are a QA contract negotiator for an AI agent system.
Given a feature and mission goal, define explicit, testable success criteria.
Respond ONLY with valid JSON. No prose. No markdown fences.
JSON shape:
{
  "success_criteria": ["Criterion 1", "Criterion 2", "Criterion 3"],
  "acceptance_keywords": ["keyword1", "keyword2", "keyword3"]
}
Rules:
- 2-4 success criteria; each must be concrete and independently testable
- acceptance_keywords: 2-5 nouns or phrases that MUST appear in the completed work
- Do NOT include vague criteria like "work is done" or "task is complete"
"""

_GRADE_SYSTEM = """\
You are an impartial QA grader for an AI agent system (GAN principle: you are separate from the worker).
Grade a work result against sprint contract criteria.
For each criterion, determine PASS or FAIL with brief evidence from the work result.
Respond ONLY with valid JSON. No prose. No markdown fences.
JSON shape:
{
  "criteria_results": [
    {"criterion": "Criterion text", "passed": true, "evidence": "Brief evidence"}
  ],
  "overall_feedback": "One sentence summary of what passed and what failed."
}
"""


# ---------------------------------------------------------------------------
# Core: negotiate
# ---------------------------------------------------------------------------

def negotiate_contract(
    feature_title: str,
    mission_goal: str,
    milestone_title: str = "",
    feature_id: str = "",
    adapter=None,
) -> SprintContract:
    """Negotiate a sprint contract for a feature.

    Args:
        feature_title:  Title of the feature to be worked on.
        mission_goal:   Overall mission goal.
        milestone_title: Parent milestone title (for context).
        feature_id:     Feature UUID[:8] (auto-generated if not given).
        adapter:        LLMAdapter. None → heuristic fallback.

    Returns:
        SprintContract with success criteria and acceptance keywords.
    """
    contract_id = str(uuid.uuid4())[:8]
    fid = feature_id or str(uuid.uuid4())[:8]
    created_at = datetime.now(timezone.utc).isoformat()

    success_criteria: List[str] = []
    acceptance_keywords: List[str] = []
    negotiated_by = "heuristic"

    if adapter is not None:
        try:
            from llm import LLMMessage, MODEL_CHEAP
            user_msg = (
                f"Feature: {feature_title}\n"
                f"Mission goal: {mission_goal}\n"
                + (f"Milestone: {milestone_title}\n" if milestone_title else "")
                + "\nWrite 2-4 explicit, testable success criteria and 2-5 acceptance keywords."
            )
            resp = adapter.complete(
                [
                    LLMMessage("system", _NEGOTIATE_SYSTEM),
                    LLMMessage("user", user_msg),
                ],
                max_tokens=512,
                temperature=0.2,
            )
            content = resp.content.strip()
            # Strip markdown fences
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                raw_criteria = data.get("success_criteria", [])
                raw_keywords = data.get("acceptance_keywords", [])
                if isinstance(raw_criteria, list) and raw_criteria:
                    success_criteria = [str(c).strip() for c in raw_criteria if str(c).strip()][:4]
                if isinstance(raw_keywords, list) and raw_keywords:
                    acceptance_keywords = [str(k).strip().lower() for k in raw_keywords if str(k).strip()][:5]
                if success_criteria:
                    negotiated_by = "llm"
        except Exception:
            pass  # fall through to heuristic

    if not success_criteria:
        success_criteria, acceptance_keywords = _heuristic_criteria(feature_title, mission_goal)
        negotiated_by = "heuristic"

    return SprintContract(
        contract_id=contract_id,
        feature_id=fid,
        feature_title=feature_title,
        mission_goal=mission_goal,
        success_criteria=success_criteria,
        acceptance_keywords=acceptance_keywords,
        created_at=created_at,
        negotiated_by=negotiated_by,
    )


def _heuristic_criteria(feature_title: str, mission_goal: str) -> tuple:
    """Extract heuristic criteria from feature title."""
    # Extract meaningful nouns from feature title as keywords
    stop_words = frozenset(["a", "an", "the", "and", "or", "but", "in", "on", "at",
                            "to", "for", "of", "with", "by", "from", "is", "are",
                            "will", "be", "has", "have", "this", "that", "it"])
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", feature_title.lower()).split()
    keywords = [w for w in words if len(w) > 3 and w not in stop_words][:4]
    if not keywords:
        # Fallback: use first meaningful word from mission goal
        goal_words = re.sub(r"[^a-zA-Z0-9 ]", " ", mission_goal.lower()).split()
        keywords = [w for w in goal_words if len(w) > 3 and w not in stop_words][:2]
    if not keywords:
        keywords = ["result"]

    criteria = [
        "Output is non-empty and contains substantive content",
        "No unrecovered errors in the result",
        f"Result directly addresses the feature: {feature_title[:60]}",
    ]
    return criteria, keywords


# ---------------------------------------------------------------------------
# Core: grade
# ---------------------------------------------------------------------------

def grade_contract(
    contract: SprintContract,
    work_result: str,
    adapter=None,
) -> ContractGrade:
    """Grade a work result against a sprint contract.

    GAN principle: this should always be called from a DIFFERENT context than
    the worker that produced work_result (e.g., from mission.py or Inspector).

    Args:
        contract:    The SprintContract to grade against.
        work_result: The text output produced by the Feature Worker.
        adapter:     LLMAdapter. None → heuristic keyword check.

    Returns:
        ContractGrade with per-criterion results and overall score.
    """
    graded_at = datetime.now(timezone.utc).isoformat()
    result_lower = work_result.lower() if work_result else ""

    # Heuristic: check acceptance_keywords in work_result
    if adapter is None or not work_result:
        return _heuristic_grade(contract, work_result, graded_at)

    # LLM grading
    try:
        from llm import LLMMessage, MODEL_CHEAP
        criteria_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(contract.success_criteria))
        user_msg = (
            f"Feature: {contract.feature_title}\n"
            f"Mission goal: {contract.mission_goal}\n\n"
            f"Sprint contract criteria:\n{criteria_text}\n\n"
            f"Work result (first 1500 chars):\n{work_result[:1500]}\n\n"
            "Grade each criterion with PASS or FAIL and evidence from the work result."
        )
        resp = adapter.complete(
            [
                LLMMessage("system", _GRADE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=1024,
            temperature=0.1,
        )
        content = resp.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            raw_results = data.get("criteria_results", [])
            criteria_results = []
            for i, cr in enumerate(raw_results):
                criteria_results.append({
                    "criterion": str(cr.get("criterion", contract.success_criteria[i] if i < len(contract.success_criteria) else "")),
                    "passed": bool(cr.get("passed", False)),
                    "evidence": str(cr.get("evidence", ""))[:200],
                })
            if not criteria_results:
                raise ValueError("no criteria_results in LLM response")

            passed_count = sum(1 for cr in criteria_results if cr["passed"])
            total = max(len(criteria_results), 1)
            score = passed_count / total
            passed = passed_count == total
            feedback = str(data.get("overall_feedback", ""))[:500]
            if not feedback:
                feedback = f"{passed_count}/{total} criteria passed."

            return ContractGrade(
                contract_id=contract.contract_id,
                feature_id=contract.feature_id,
                passed=passed,
                criteria_results=criteria_results,
                score=max(0.0, min(1.0, score)),
                feedback=feedback,
                graded_at=graded_at,
            )
    except Exception:
        pass  # fall through to heuristic

    return _heuristic_grade(contract, work_result, graded_at)


def _heuristic_grade(
    contract: SprintContract,
    work_result: str,
    graded_at: str,
) -> ContractGrade:
    """Heuristic grading: check keywords + basic content checks."""
    result_lower = work_result.lower() if work_result else ""
    result_nonempty = bool(work_result and work_result.strip())

    criteria_results = []
    for criterion in contract.success_criteria:
        c_lower = criterion.lower()
        # Check for "non-empty" type criterion
        if "non-empty" in c_lower or "nonempty" in c_lower or "output" in c_lower and "contain" in c_lower:
            passed = result_nonempty
            evidence = "Result is non-empty." if passed else "Result is empty."
        # Check for "no error" type criterion
        elif "no error" in c_lower or "error" in c_lower and "no " in c_lower:
            passed = result_nonempty and "error:" not in result_lower and "exception:" not in result_lower
            evidence = "No errors detected." if passed else "Errors found in result."
        # Keyword-based criterion check
        else:
            # Extract keywords from criterion itself
            crit_words = re.sub(r"[^a-zA-Z0-9 ]", " ", criterion.lower()).split()
            stop = frozenset(["the", "a", "an", "is", "are", "in", "on", "at", "to", "for",
                              "of", "with", "by", "this", "that", "it", "must", "should",
                              "contain", "include", "have", "be", "and", "or"])
            meaningful = [w for w in crit_words if len(w) > 3 and w not in stop]
            if meaningful:
                hits = sum(1 for w in meaningful if w in result_lower)
                passed = result_nonempty and hits >= max(1, len(meaningful) // 2)
                evidence = f"{hits}/{len(meaningful)} criterion keywords found in result."
            else:
                passed = result_nonempty
                evidence = "Result is non-empty (heuristic)."
        criteria_results.append({
            "criterion": criterion,
            "passed": passed,
            "evidence": evidence,
        })

    # Also check acceptance_keywords
    if contract.acceptance_keywords and result_nonempty:
        kw_hits = sum(1 for kw in contract.acceptance_keywords if kw.lower() in result_lower)
        # If less than half the keywords appear, count it as a penalty on score
        kw_score = kw_hits / max(len(contract.acceptance_keywords), 1)
    else:
        kw_score = 0.0 if not result_nonempty else 1.0

    passed_count = sum(1 for cr in criteria_results if cr["passed"])
    total = max(len(criteria_results), 1)
    criteria_score = passed_count / total
    # Blend criteria score with keyword score
    score = (criteria_score * 0.7 + kw_score * 0.3)
    passed = passed_count == total and result_nonempty

    feedback = (
        f"{passed_count}/{total} criteria passed (heuristic). "
        f"Keyword match: {kw_score:.0%}."
    )

    return ContractGrade(
        contract_id=contract.contract_id,
        feature_id=contract.feature_id,
        passed=passed,
        criteria_results=criteria_results,
        score=max(0.0, min(1.0, score)),
        feedback=feedback,
        graded_at=graded_at,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _contracts_path(project: str) -> Path:
    """Return path to contracts.jsonl for a project."""
    try:
        import orch
        base = orch.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "memory"
    except ImportError:
        base = Path.cwd() / "projects" / project / "memory"
    base.mkdir(parents=True, exist_ok=True)
    return base / "contracts.jsonl"


def save_contract(contract: SprintContract, project: str) -> None:
    """Append a SprintContract to memory/contracts.jsonl for a project."""
    path = _contracts_path(project)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(contract.to_dict()) + "\n")
    except Exception:
        pass  # Persistence failures are non-fatal


def load_contracts(project: str) -> List[SprintContract]:
    """Load all contracts from a project's contracts.jsonl."""
    path = _contracts_path(project)
    if not path.exists():
        return []
    contracts = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                contracts.append(SprintContract.from_dict(d))
            except Exception:
                continue
    except Exception:
        pass
    return contracts


def save_grade(grade: ContractGrade, project: str) -> None:
    """Append a ContractGrade to memory/contract-grades.jsonl for a project."""
    try:
        import orch
        base = orch.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "memory"
    except ImportError:
        base = Path.cwd() / "projects" / project / "memory"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "contract-grades.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(grade.to_dict()) + "\n")
    except Exception:
        pass
