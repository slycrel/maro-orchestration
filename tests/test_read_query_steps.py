"""Planner-level read-verb teaching (A/B-4 consequence, 2026-08-13).

Two corpus runs showed the ambient EXECUTE_SYSTEM advertisement never
gets the sub-query verb invoked; the pre-registered lever is plan steps
that NAME the invocation. These pins cover the emission gates: config
switch, verb killswitch, and lane (never teach a command the executor's
environment can't run — the container verb-parity principle).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class _PlanCapturingAdapter:
    def __init__(self, content='["step one", "step two"]'):
        self.system_prompts = []
        self._content = content

    def complete(self, messages, **kwargs):
        from types import SimpleNamespace
        for m in messages:
            if getattr(m, "role", "") == "system":
                self.system_prompts.append(getattr(m, "content", ""))
        return SimpleNamespace(content=self._content,
                               input_tokens=5, output_tokens=5)


def _taught(adapter):
    # The constant carries the __READ_CLI__ placeholder; the taught copy
    # has it substituted — match on invariant rule text instead.
    return any("LARGE-FILE EXTRACTION STEPS" in s
               for s in adapter.system_prompts)


class TestReadStepEmission:

    def test_decompose_teaches_read_steps_by_default(self):
        from planner import decompose
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert _taught(adapter)

    def test_taught_copy_resolves_the_cli_placeholder(self):
        from planner import decompose
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        taught = [s for s in adapter.system_prompts
                  if "LARGE-FILE EXTRACTION STEPS" in s]
        assert taught
        assert all("__READ_CLI__" not in s for s in taught)

    def test_emission_switch_gates_teaching(self, monkeypatch):
        import config
        from planner import decompose
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None:
                False if key == "planner.read_query_steps" else default)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_verb_killswitch_gates_teaching(self, monkeypatch):
        # A disabled verb must never be advertised into plans — the step
        # would shell out to a CLI that answers "disabled by config".
        import config
        from planner import decompose
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None:
                False if key == "executor.read_query" else default)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_container_lane_gates_teaching(self, monkeypatch):
        # The executor image bakes no maro-read and build_mount_map
        # excludes the repo path the fallback resolves to — a
        # container-configured run must not get plans naming the verb.
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: False)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_suppressed_container_degrades_to_host_and_teaches(
            self, monkeypatch):
        # Breaker-suppressed container runs execute on the host, where
        # the verb IS reachable — same lane logic as
        # step_exec.execute_system_for_lane.
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: True)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert _taught(adapter)

    def test_staged_pass_lane_is_taught(self, monkeypatch):
        # Unlike WORLD_FACT_RULES (needs injected context), this is a
        # step-writing form rule grounded in the goal text — staged
        # passes become executed steps and corpus audits are exactly the
        # wide-ish goal shape.
        import planner
        from planner import decompose
        monkeypatch.setattr(planner, "estimate_goal_scope",
                            lambda goal: "wide")
        adapter = _PlanCapturingAdapter(
            '["pass one", "pass two", "pass three"]')
        decompose("audit every claim in the corpus", adapter, max_steps=8)
        assert _taught(adapter)
