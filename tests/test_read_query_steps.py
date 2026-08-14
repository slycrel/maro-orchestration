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

    def test_container_lane_without_baked_verbs_gates_teaching(
            self, monkeypatch):
        # A pre-r3/custom image bakes no maro-read and build_mount_map
        # excludes the repo path the fallback resolves to — such a
        # container-configured run must not get plans naming the verb.
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: False)
        monkeypatch.setattr(container_exec, "image_bakes_verbs",
                            lambda: False)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_container_lane_with_baked_verbs_teaches_baked_name(
            self, monkeypatch):
        # r3+ images bake the shims (2026-08-13 decree chunk): the
        # container lane teaches by the BAKED name, never a host path.
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: False)
        monkeypatch.setattr(container_exec, "image_bakes_verbs",
                            lambda: True)
        monkeypatch.setattr(container_exec, "docker_probe",
                            lambda: (True, "test"))
        monkeypatch.setattr(container_exec, "auth_breaker_blocks",
                            lambda: None)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        taught = [s for s in adapter.system_prompts
                  if "LARGE-FILE EXTRACTION STEPS" in s]
        assert taught
        assert all("maro-read" in s for s in taught)
        assert all("/src/read_query.py" not in s for s in taught)

    def test_suppressed_container_is_still_not_taught(self, monkeypatch):
        # Skeptic round (2026-08-13): plan text OUTLIVES the moment it
        # was written. A suppressed lane executes on the HOST, where the
        # baked name is not on PATH — suppression never SELECTS a lane's
        # command, it only suppresses the container teaching; a
        # suppressed run just loses the optimization (even on a
        # verb-baking image).
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: True)
        monkeypatch.setattr(container_exec, "image_bakes_verbs",
                            lambda: True)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_container_lane_docker_down_is_not_taught(self, monkeypatch):
        # Adversarial review 2026-08-13: a plan written while docker is
        # down (or the auth breaker is tripped, below) would teach a name
        # its steps — degrading to host — can't run. Availability joins
        # the plan-time gate; a post-plan outage stays the loud residual.
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: False)
        monkeypatch.setattr(container_exec, "image_bakes_verbs",
                            lambda: True)
        monkeypatch.setattr(container_exec, "docker_probe",
                            lambda: (False, "daemon down"))
        monkeypatch.setattr(container_exec, "auth_breaker_blocks",
                            lambda: None)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_container_lane_auth_breaker_is_not_taught(self, monkeypatch):
        import container_exec
        from planner import decompose
        monkeypatch.setattr(container_exec, "container_mode", lambda: "on")
        monkeypatch.setattr(container_exec, "container_suppressed",
                            lambda: False)
        monkeypatch.setattr(container_exec, "image_bakes_verbs",
                            lambda: True)
        monkeypatch.setattr(container_exec, "docker_probe",
                            lambda: (True, "test"))
        monkeypatch.setattr(container_exec, "auth_breaker_blocks",
                            lambda: "auth session expired")
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_lane_detection_failure_fails_closed(self, monkeypatch):
        # Uncertainty must suppress advertisement, not enable it.
        import container_exec
        from planner import decompose

        def _boom():
            raise RuntimeError("lane detection down")

        monkeypatch.setattr(container_exec, "container_mode", _boom)
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        assert not _taught(adapter)

    def test_taught_command_is_the_resolved_cli(self):
        # Not just placeholder-substituted: the taught copy carries the
        # exact command step_exec resolves for this environment, so the
        # advertisement and the executable path can't drift apart.
        from planner import decompose
        from step_exec import _read_cli_path
        adapter = _PlanCapturingAdapter()
        decompose("audit the spec against the raw captures", adapter,
                  max_steps=4)
        taught = [s for s in adapter.system_prompts
                  if "LARGE-FILE EXTRACTION STEPS" in s]
        assert taught and all(_read_cli_path() in s for s in taught)

    def test_empty_string_switch_matches_sibling_enabled(self, monkeypatch):
        # Fixpoint review 2026-08-13 (Skeptic + Architect): the planner
        # switch treated a quoted "" as OFF while the sibling
        # executor.read_query treated "" as ON. Shared normalize_flag now
        # makes "" mean unset -> enabled for both.
        import config
        from planner import decompose
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None:
                "" if key == "planner.read_query_steps" else default)
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
