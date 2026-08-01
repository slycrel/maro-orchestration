"""Recon step flavor (compound-thinking §4/§9.2, graduated from star 2026-08-01).

The flavor rides the step string as an inline ``[recon: <decision>]`` tag —
same convention as [after:N]/[boundary], so it survives manifests,
checkpoint resume, splits and injections with no side-channel plumbing.
Emission is taught to the planner behind `planner.recon_flavor`; detection
at every consumer is deliberately unconditional (chunk-6 killswitch
precedent: kill emission, never measurement). Consumers pinned here:

  - planner: RECON_FLAVOR_RULES reach the decompose system prompt (and
    stay out when the killswitch is off);
  - step_exec: tagged steps get the map-edit execution contract and the
    outcome-row flavor stamp on every outcome shape;
  - verification_agent: tagged steps get the map-change verification
    question instead of the deliverable question, at every ladder tier
    (both tiers route through VerificationAgent, so one detection covers
    hosted-free and paid by construction).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestStepFlavorParsing:
    def test_recon_with_voi(self):
        from planner import step_flavor
        flavor, voi = step_flavor(
            "Survey src/ modules [recon: decides which modules the refactor targets]")
        assert flavor == "recon"
        assert voi == "decides which modules the refactor targets"

    def test_bare_recon_keeps_flavor_with_empty_voi(self):
        # A bare tag is NOT demoted to commit — demotion would hand the step
        # to the deliverable verification question, which is the dishonest
        # default this flavor exists to fix. VOI-missing is visible (voi "").
        from planner import step_flavor
        assert step_flavor("Probe the API rate limits [recon]") == ("recon", "")

    def test_unmarked_is_commit(self):
        from planner import step_flavor
        assert step_flavor("Write the final report") == ("commit", "")

    def test_empty_and_none_are_commit(self):
        from planner import step_flavor
        assert step_flavor("") == ("commit", "")
        assert step_flavor(None) == ("commit", "")

    def test_coexists_with_after_tag(self):
        from planner import step_flavor
        assert step_flavor(
            "Read the manifest [recon: decides the split] [after:2]"
        ) == ("recon", "decides the split")

    def test_strip_recon_tag(self):
        from planner import strip_recon_tag
        assert strip_recon_tag("Survey src/ [recon: decides X]") == "Survey src/"
        assert strip_recon_tag("No tag here") == "No tag here"
        assert strip_recon_tag("") == ""


# ---------------------------------------------------------------------------
# Planner emission gate
# ---------------------------------------------------------------------------

class _CapturingAdapter:
    def __init__(self):
        self.system_prompts = []

    def complete(self, messages, **kwargs):
        for m in messages:
            if getattr(m, "role", "") == "system":
                self.system_prompts.append(getattr(m, "content", ""))
        return SimpleNamespace(content='["step one", "step two"]',
                               input_tokens=5, output_tokens=5)


class TestReconEmissionGate:
    def test_decompose_teaches_tag_by_default(self):
        from planner import decompose, RECON_FLAVOR_RULES
        adapter = _CapturingAdapter()
        decompose("check the config", adapter, max_steps=4)
        assert any(RECON_FLAVOR_RULES in s for s in adapter.system_prompts)

    def test_killswitch_gates_emission_only(self, monkeypatch):
        import config
        from planner import decompose, RECON_FLAVOR_RULES, step_flavor
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None:
                False if key == "planner.recon_flavor" else default)
        adapter = _CapturingAdapter()
        decompose("check the config", adapter, max_steps=4)
        assert not any(RECON_FLAVOR_RULES in s for s in adapter.system_prompts)
        # Detection stays live with emission off — markers already in flight
        # keep their honest treatment.
        assert step_flavor("Probe X [recon: decides Y]")[0] == "recon"

    def test_staged_pass_lane_teaches_tag(self, monkeypatch):
        # Wide/deep goals replace the assembled system prompt wholesale with
        # _STAGED_PASS_SYSTEM — the teaching must follow it there
        # (2026-08-01 adversarial review: the lane was never taught).
        import planner
        from planner import decompose, RECON_FLAVOR_RULES
        monkeypatch.setattr(planner, "estimate_goal_scope", lambda goal: "wide")
        adapter = _CapturingAdapter()
        decompose("audit the whole codebase", adapter, max_steps=8)
        assert any(RECON_FLAVOR_RULES in s for s in adapter.system_prompts)

    def test_compose_prompt_preserves_inline_tags(self, monkeypatch):
        # The multi-plan composer rewrites candidate steps with a fresh system
        # prompt; it must be told to keep inline tags attached (it could
        # silently drop [recon:]/[after:N]/[boundary] from every composed
        # plan otherwise).
        import planner
        from planner import decompose
        monkeypatch.setattr(planner, "estimate_goal_scope", lambda goal: "medium")
        adapter = _CapturingAdapter()
        decompose("refactor the config loader", adapter, max_steps=4)
        assert any("keep each tag attached verbatim" in s
                   for s in adapter.system_prompts)


# ---------------------------------------------------------------------------
# Execution contract + outcome stamp
# ---------------------------------------------------------------------------

class _ExecCaptureAdapter:
    model_key = "test"

    def __init__(self, tool_name="complete_step", arguments=None):
        self.messages = None
        self._tool_name = tool_name
        self._arguments = arguments or {"result": "found the seam at loader.py:42",
                                        "summary": "surveyed"}

    def complete(self, messages, **kwargs):
        from llm import LLMResponse, ToolCall
        self.messages = messages
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(name=self._tool_name, arguments=self._arguments)],
            input_tokens=10,
            output_tokens=5,
        )


def _user_msg(adapter):
    return next(m.content for m in adapter.messages
                if getattr(m, "role", "") == "user")


class TestReconExecution:
    def test_recon_step_prompt_carries_map_edit_contract_and_voi(self, tmp_path):
        from step_exec import execute_step
        adapter = _ExecCaptureAdapter()
        outcome = execute_step(
            goal="refactor the loader",
            step_text="Survey the loaders [recon: decides which loader the refactor targets]",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=[],
            project_dir=str(tmp_path),
        )
        msg = _user_msg(adapter)
        assert "RECON STEP" in msg
        assert "decides which loader the refactor targets" in msg
        assert outcome["flavor"] == "recon"
        assert outcome["recon_decision"] == "decides which loader the refactor targets"

    def test_commit_step_prompt_unchanged_and_unstamped(self, tmp_path):
        from step_exec import execute_step
        adapter = _ExecCaptureAdapter()
        outcome = execute_step(
            goal="refactor the loader",
            step_text="Write the migration script",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=[],
            project_dir=str(tmp_path),
        )
        assert "RECON STEP" not in _user_msg(adapter)
        assert "flavor" not in outcome
        assert "recon_decision" not in outcome

    def test_bare_recon_gets_contract_without_decision_stamp(self, tmp_path):
        from step_exec import execute_step
        adapter = _ExecCaptureAdapter()
        outcome = execute_step(
            goal="refactor the loader",
            step_text="Probe the API rate limits [recon]",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=[],
            project_dir=str(tmp_path),
        )
        msg = _user_msg(adapter)
        assert "RECON STEP" in msg
        # VOI-missing is surfaced to the executor, not silently dropped.
        assert "none named" in msg
        assert outcome["flavor"] == "recon"
        assert "recon_decision" not in outcome

    def test_constraint_blocked_recon_still_stamped(self, tmp_path, monkeypatch):
        # The HITL constraint check returns before the LLM ever runs — a
        # recon probe denied there is still a recon step to the outcome
        # reader (2026-08-01 adversarial review: early returns were
        # unstamped).
        import constraint
        from step_exec import execute_step
        monkeypatch.setattr(
            constraint, "hitl_policy",
            lambda *a, **k: {"allowed": False, "tier": "external",
                             "risk_level": "high", "reason": "denied"})
        adapter = _ExecCaptureAdapter()
        outcome = execute_step(
            goal="probe the vendor API",
            step_text="Probe the vendor API [recon: decides the integration path]",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=[],
            project_dir=str(tmp_path),
        )
        assert outcome["status"] == "blocked"
        assert outcome["flavor"] == "recon"
        assert outcome["recon_decision"] == "decides the integration path"

    def test_adapter_error_recon_still_stamped(self, tmp_path):
        # Adapter death is an outcome shape too — the blocked dict built in
        # the exception handler must carry the flavor.
        from step_exec import execute_step

        class _DyingAdapter:
            model_key = "test"

            def complete(self, messages, **kwargs):
                raise RuntimeError("transport down")

        outcome = execute_step(
            goal="refactor the loader",
            step_text="Survey the loaders [recon: decides the target]",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=_DyingAdapter(),
            tools=[],
            project_dir=str(tmp_path),
        )
        assert outcome["status"] == "blocked"
        assert outcome["flavor"] == "recon"

    def test_blocked_recon_step_still_stamped(self, tmp_path):
        # A recon step that got stuck is still a recon step to every
        # downstream reader — the stamp rides every outcome shape.
        from step_exec import execute_step
        adapter = _ExecCaptureAdapter(
            tool_name="flag_stuck",
            arguments={"reason": "workspace unreadable"})
        outcome = execute_step(
            goal="refactor the loader",
            step_text="Survey the loaders [recon: decides the target]",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=[],
            project_dir=str(tmp_path),
        )
        assert outcome["status"] == "blocked"
        assert outcome["flavor"] == "recon"


# ---------------------------------------------------------------------------
# Tag survival through plan transforms (the tag IS the schema — flavor is
# derived from step text everywhere, so text-rewriting transforms are the
# places the flavor can die)
# ---------------------------------------------------------------------------

class TestTagSurvival:
    def test_shaper_skips_recon_steps(self):
        # _split_exec_analyze rebuilds step text ("Run X…"/"Read the captured
        # output…"), which strands or drops an inline tag — recon steps skip
        # the split like boundary steps do (2026-08-01 adversarial review).
        from loop_planning import _shape_steps
        tagged = ("Run the full test suite and analyze the failures "
                  "[recon: decides which repair path to take]")
        assert _shape_steps([tagged]) == [tagged]
        # The same text untagged IS split — the guard is the tag, not the
        # phrasing.
        assert len(_shape_steps([tagged.split(" [recon")[0]])) == 2

    def test_tag_survives_dependency_parse(self):
        # parse_dependencies strips [after:N] into the deps map; the recon
        # tag must ride through into clean_steps — that string becomes
        # StepOutcome.text, which is the durable record flavor derives from.
        from planner import parse_dependencies, step_flavor
        clean, deps = parse_dependencies(
            ["Read the manifest", "Survey loaders [recon: decides the split] [after:1]"])
        assert step_flavor(clean[1]) == ("recon", "decides the split")
        assert deps[2] == {1}


# ---------------------------------------------------------------------------
# Verification question swap
# ---------------------------------------------------------------------------

def _verify_adapter(payload='{"verdict": "PASS", "reason": "map edit", "confidence": 0.9}'):
    resp = MagicMock()
    resp.content = payload
    adapter = MagicMock()
    adapter.complete.return_value = resp
    return adapter


class TestReconVerification:
    def test_recon_step_gets_map_change_question(self):
        from verification_agent import (VerificationAgent,
                                        _VERIFY_RECON_STEP_SYSTEM)
        adapter = _verify_adapter()
        va = VerificationAgent(adapter)
        va.verify_step("Survey the loaders [recon: decides the target]",
                       "resolved: loader B is dead code (no importers; rg output quoted)")
        messages = adapter.complete.call_args[0][0]
        assert messages[0].content == _VERIFY_RECON_STEP_SYSTEM

    def test_commit_step_keeps_deliverable_question(self):
        from verification_agent import VerificationAgent, _VERIFY_STEP_SYSTEM
        adapter = _verify_adapter()
        va = VerificationAgent(adapter)
        va.verify_step("Write the migration script", "wrote migrate.py, 120 lines")
        messages = adapter.complete.call_args[0][0]
        assert messages[0].content == _VERIFY_STEP_SYSTEM

    def test_recon_retry_verdict_fails_step(self):
        # Verdict semantics are unchanged by the flavor — only the question
        # differs. High-confidence RETRY on a recon step → not passed.
        from verification_agent import VerificationAgent
        adapter = _verify_adapter(
            '{"verdict": "RETRY", "reason": "no map change", "confidence": 0.9}')
        va = VerificationAgent(adapter)
        verdict = va.verify_step("Survey the loaders [recon: decides the target]",
                                 "loaders exist and load things")
        assert verdict.passed is False
        assert verdict.judged is True

    def test_recon_fail_open_still_unjudged(self):
        # §13e: the advisory-judge fail-open contract is flavor-independent.
        from verification_agent import VerificationAgent
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("transport down")
        va = VerificationAgent(adapter)
        verdict = va.verify_step("Survey the loaders [recon: decides the target]",
                                 "some result")
        assert verdict.passed is True
        assert verdict.judged is False
