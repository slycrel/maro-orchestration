"""regression_ledger.py + closure re-run (2026-09-16).

Harvest is positive-evidence only; closure re-runs obligations as argv
(shell=False) in the RECORDED cwd and a hard fail downgrades the verdict.

Must-detect fixtures (round-1 review): every shell PROGRAM shape — heredoc,
`|| true`, pipeline, `;`/`&&` chains, substitution, glob, newline — and every
silence shape — no tool result, empty output, truncated output, no passing
tally — must NOT become an obligation. A ledger that cannot refuse these
would let closure execute a program the step never ran, or re-run a
command the step never proved.
"""
import json
import os
import stat
from unittest.mock import MagicMock

import pytest

import closure_verify
import regression_ledger as rl


def _ev(cmd, output="12 passed in 0.31s", is_error=False, name="Bash", result_seen=True, **extra):
    ev = {"name": name, "input": {"command": cmd}, "output": output, "is_error": is_error}
    if result_seen is not None:
        ev["result_seen"] = result_seen
    ev.update(extra)
    return ev


# --- grammar: one simple runner invocation ----------------------------------

@pytest.mark.parametrize("cmd,family,argv0", [
    ("pytest -q tests/test_x.py", "pytest", "pytest"),
    ("python -m pytest -q", "pytest", "python"),
    ("python3.12 -m pytest tests/", "pytest", "python3.12"),
    ("PYTHONPATH=src pytest -q", "pytest", "pytest"),
    ("uv run pytest -q", "pytest", "uv"),          # wrapper stays in the execution argv
    ("poetry run pytest", "pytest", "poetry"),
    ("cd sub && pytest -q", "pytest", "pytest"),
    ('pytest -k "not slow" -q', "pytest", "pytest"),
    ("/usr/local/bin/pytest -q", "pytest", "/usr/local/bin/pytest"),
    ("go test ./...", "go", "go"),
    ("cargo test --workspace", "cargo", "cargo"),
    ("npm test", "npm", "npm"),
    ("pnpm run test", "npm", "pnpm"),
    ("make test", "make", "make"),
    ("tox -e py311", "tox", "tox"),
    ("bun test", "bun", "bun"),
])
def test_parse_accepts_single_runner_forms(cmd, family, argv0):
    parsed = rl.parse_verification_command(cmd)
    assert parsed is not None and parsed.family == family and parsed.argv[0] == argv0
    assert rl.is_verification_command(cmd)


def test_parse_keeps_quoted_args_and_env_prefix():
    parsed = rl.parse_verification_command('FOO=bar BAZ="x y" pytest -k "not slow"')
    assert parsed.env == {"FOO": "bar", "BAZ": "x y"}
    assert parsed.argv == ["pytest", "-k", "not slow"]
    assert parsed.cd is None
    assert rl.parse_verification_command("cd sub/dir && pytest -q").cd == "sub/dir"


@pytest.mark.parametrize("cmd", [
    # shell programs — closure must never run anything but the runner argv
    "pytest -q || true",
    "pytest -q; rm -rf /tmp/x",
    "pytest -q && echo done",
    "pytest -q | tail -5",
    "pytest -q > out.txt",
    "pytest -q 2>&1",
    "pytest -q < input",
    "pytest -q <<EOF\nrm -rf /\nEOF",
    "pytest -q\nrm -rf /tmp/x",
    "pytest -q $(cat cmd)",
    "pytest -q `cat cmd`",
    "pytest ${TESTS}",
    "pytest tests/*.py",
    "pytest ~/proj/tests",
    "cd sub; pytest -q",
    "cd sub && pytest -q && rm x",
    "cd && pytest",
    "(pytest -q)",
    "{ pytest -q; }",
    "true && pytest -q",
    "pytest -q # smoke suite",     # unquoted comment: bash drops it, argv would not
    "pytest # -q",
    # not a runner at all, or a runner name inside something else
    "echo pytest passed",
    ": pytest",
    "grep -r pytest .",
    "cat pytest.ini",
    "python -c 'import pytest'",
    "npm run build",
    "go build ./...",
    "cargo build",
    "make",
    "ls -la",
    # non-executing runner forms
    "pytest --collect-only -q",
    "pytest --co",
    "go test -list . ./...",
    "cargo test --no-run",
    "pytest --dry-run",
    # degenerate
    "", "   ", "pytest 'unterminated", "x" * 500,
])
def test_parse_refuses_programs_non_runners_and_non_exec_forms(cmd):
    assert rl.parse_verification_command(cmd) is None
    assert not rl.is_verification_command(cmd)


def test_wrapper_is_executed_and_distinguishes_obligations(tmp_path):
    parsed = rl.parse_verification_command("uv run pytest -q")
    assert parsed.argv == ["uv", "run", "pytest", "-q"] and parsed.runner_argv == ["pytest", "-q"]
    led = rl.RegressionLedger()
    led.harvest([_ev("uv run pytest -q"), _ev("poetry run pytest -q"), _ev("pytest -q")],
                step_index=1, step_no=1, executor_cwd=str(tmp_path))
    assert len(led) == 3


@pytest.mark.parametrize("family,rc,out,want", [
    ("pytest", 0, "3 passed", "pass"),
    ("pytest", 0, "3 failed, 7 passed", "fail"),       # wrapper swallowed the status
    ("pytest", 1, "3 failed, 7 passed", "fail"),
    ("pytest", 0, "collected 3 items", "inconclusive"), # no passing tally
    ("go", 0, "ok  pkg 0.1s", "pass"),
    ("go", 0, "--- FAIL: TestX\nFAIL", "fail"),
    ("npm", 0, "5 passing", "pass"),
    ("npm", 0, "Tests failed", "fail"),
    ("npm", 0, "  1 failing\n  5 passing", "fail"),
    ("npm", 0, "", "pass"),                              # no grammar → return code decides
    ("pytest", 0, "ERROR expected validation error\n3 passed in 0.10s", "pass"),
    ("make", 0, "", "pass"),
    ("make", 2, "", "fail"),
])
def test_classify_rerun_uses_the_harvest_evidence_rule(family, rc, out, want):
    assert rl.classify_rerun(family, rc, out) == want


def test_parse_refuses_non_strings():
    assert rl.parse_verification_command(None) is None
    assert rl.parse_verification_command(["pytest"]) is None


# --- positive evidence -------------------------------------------------------

@pytest.mark.parametrize("event,family", [
    (_ev("pytest -q", is_error=True), "pytest"),
    (_ev("pytest -q", result_seen=False), "pytest"),            # unmatched tool_use
    (_ev("pytest -q", result_seen=None), "pytest"),             # producer never said
    (_ev("pytest -q", output=""), "pytest"),
    (_ev("pytest -q", output="   \n"), "pytest"),
    (_ev("pytest -q", output_truncated=True), "pytest"),        # tally may be cut off
    (_ev("pytest -q", output="1 failed, 3 passed"), "pytest"),
    (_ev("pytest -q", output="collected 3 items"), "pytest"),   # no passing tally
    (_ev("pytest -q", output="ok"), "pytest"),                  # wrong family's tally
    (_ev("go test ./...", output="--- FAIL: TestX\nFAIL"), "go"),
    (_ev("go test ./...", output="building..."), "go"),
    (_ev("cargo test", output="test result: FAILED. 1 passed; 1 failed"), "cargo"),
    (_ev("cargo test", output="Compiling foo"), "cargo"),
    (_ev("npm test", output="Tests failed"), "npm"),
    (_ev("npm test", output="  1 failing\n  5 passing"), "npm"),     # mocha summary
    (_ev("npm test", output="Tests:  1 failed, 2 passed"), "npm"),    # jest summary
    (_ev("pytest -q", output="FAILED tests/t.py::test_x\n3 passed"), "pytest"),
])
def test_event_passed_refuses_silence_and_failure(event, family):
    assert rl.event_passed(event, family) is False


@pytest.mark.parametrize("event,family", [
    (_ev("pytest -q", output="=== 12 passed in 0.3s ==="), "pytest"),
    (_ev("go test ./...", output="ok  \tpkg/x\t0.012s"), "go"),
    (_ev("go test ./...", output="PASS\nok pkg"), "go"),
    (_ev("cargo test", output="test result: ok. 4 passed; 0 failed"), "cargo"),
    (_ev("npm test", output="5 passing (20ms)"), "npm"),
    (_ev("make test", output="all good"), "make"),
    # a log line from the code under test is not a runner verdict
    (_ev("pytest -q", output="ERROR expected validation error\n3 passed in 0.10s"), "pytest"),
    (_ev("pytest -q", output="INFO ERROR: retrying\n2 passed"), "pytest"),
])
def test_event_passed_accepts_positive_evidence(event, family):
    assert rl.event_passed(event, family) is True


# --- harvest ----------------------------------------------------------------

def test_harvest_records_passing_runner_only(tmp_path):
    led = rl.RegressionLedger()
    new = led.harvest([
        _ev("pytest -q tests/test_x.py"),
        _ev("pytest --collect-only -q"),                    # non-exec form
        _ev("pytest -q || true"),                           # program
        _ev("pytest -q", output="1 failed, 3 passed"),      # tally
        _ev("go test ./...", is_error=True),                # tool error
        _ev("go test ./...", output="ok pkg", result_seen=False),  # no result seen
        _ev("pytest -q tests/", name="Read"),               # not a shell tool
        _ev("ls -la"),                                      # not a runner
        _ev("  pytest   -q tests/test_x.py "),              # dedupe (same argv)
        _ev("cargo test --workspace", output="test result: ok. 4 passed"),
    ], step_index=3, step_no=2, iteration=1, step_text="run the tests",
       executor_cwd=str(tmp_path))
    assert new == ["pytest -q tests/test_x.py", "cargo test --workspace"]
    rows = led.to_list()
    assert rows[0] == {"command": "pytest -q tests/test_x.py", "cwd": str(tmp_path),
                       "step_index": 3, "step_no": 2, "iteration": 1,
                       "step_text": "run the tests"}


def test_harvest_resolves_cd_prefix_against_executor_cwd(tmp_path):
    led = rl.RegressionLedger()
    led.harvest([_ev("cd pkg/sub && pytest -q")], step_index=1, step_no=1,
                executor_cwd=str(tmp_path))
    assert led.obligations[0].cwd == str(tmp_path / "pkg" / "sub")
    assert led.obligations[0].command == "cd pkg/sub && pytest -q"
    # Same argv in a different cwd is a different obligation.
    led.harvest([_ev("pytest -q")], step_index=1, step_no=1, executor_cwd=str(tmp_path))
    led.harvest([_ev("pytest -q")], step_index=2, step_no=2, executor_cwd=str(tmp_path / "pkg"))
    assert len(led) == 3


def test_harvest_without_executor_cwd_uses_process_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    led = rl.RegressionLedger()
    led.harvest([_ev("pytest -q")], step_index=1, step_no=1, executor_cwd=None)
    assert led.obligations[0].cwd == str(tmp_path.resolve())


def test_harvest_accepts_string_input_and_other_shell_tool_names(tmp_path):
    led = rl.RegressionLedger()
    assert led.harvest([{"name": "shell", "input": "npm test", "output": "5 passing",
                         "is_error": False, "result_seen": True}],
                       step_index=1, step_no=1, executor_cwd=str(tmp_path)) == ["npm test"]


def test_harvest_cap_and_malformed_rows(tmp_path):
    led = rl.RegressionLedger()
    events = [_ev(f"pytest -q tests/t{i}.py") for i in range(12)] + ["junk", None, {"name": "Bash"}]
    new = led.harvest(events, step_index=1, step_no=1, executor_cwd=str(tmp_path))
    assert len(new) == rl.MAX_OBLIGATIONS == len(led)


def test_from_list_revalidates_every_row(tmp_path):
    led = rl.RegressionLedger()
    led.add("pytest -q", cwd=str(tmp_path), step_index=2, step_no=1)
    rows = led.to_list() + [
        {"command": 5, "cwd": "/x"},                                # not a string
        "x",
        {"step_no": "a", "command": "tox", "cwd": str(tmp_path)},  # bad int → default
        {"command": "pytest -q || true", "cwd": str(tmp_path)},    # program smuggled in
        {"command": "pytest -q tests/", "step_no": 3},             # no cwd (old checkpoint)
        {"command": "pytest -q tests/", "cwd": ""},                # empty cwd
    ]
    back = rl.RegressionLedger.from_list(rows)
    assert [o.command for o in back.obligations] == ["pytest -q", "tox"]
    assert back.obligations[1].step_no == 0


def test_grammar_is_shared_with_closure():
    assert closure_verify._TEST_RUNNER is rl.VERIFICATION_RUNNER_RE
    assert closure_verify._NON_EXEC_RUNNER_FLAGS is rl.NON_EXEC_RUNNER_FLAGS_RE


def test_regression_enabled_reads_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "get_bool", lambda key, default=True: False)
    assert rl.regression_enabled() is False


# --- closure re-run ----------------------------------------------------------

def _fake_pytest(tmp_path):
    """A `pytest` executable that exits per PYTEST_FAKE_EXIT (env prefix form)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "pytest"
    exe.write_text("#!/bin/sh\necho \"argv: $@\"\necho \"${PYTEST_FAKE_OUT:-3 passed}\"\n"
                   "exit ${PYTEST_FAKE_EXIT:-0}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_run_regression_obligations_runs_argv_in_recorded_cwd(tmp_path):
    exe = _fake_pytest(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    rows = [
        {"command": f"{exe} -q tests/", "cwd": str(work), "step_no": 1},
        {"command": f"PYTEST_FAKE_EXIT=1 {exe} -q", "cwd": str(work), "step_no": 2,
         "step_text": "build it"},
        {"command": "nosuchtool_xyz_maro test", "cwd": str(work), "step_no": 3},   # not a runner
        {"command": f"{exe} -q || true", "cwd": str(work), "step_no": 4},          # program
        {"command": f"{exe} -q", "cwd": str(tmp_path / "gone"), "step_no": 5},     # cwd gone
        {"command": f"{exe} -q", "step_no": 6},                                    # no cwd → closure cwd
        {"command": f"PYTEST_FAKE_OUT='2 failed, 1 passed' {exe} -q", "cwd": str(work),
         "step_no": 8},                                                             # exit 0, tally says fail
        {"command": f"PYTEST_FAKE_OUT=collecting {exe} -q", "cwd": str(work), "step_no": 9},  # exit 0, no tally
        {"command": "", "step_no": 7}, "junk",
    ]
    out = closure_verify._run_regression_obligations(rows, cwd=str(work), timeout_per_check=10)
    assert [r["outcome"] for r in out] == ["pass", "fail", "inconclusive", "inconclusive",
                                           "inconclusive", "pass", "fail", "inconclusive"]
    assert out[6]["passed"] is False and out[6]["exit_code"] == 0
    assert all(r["regression"] is True and r["plan_index"] == -1 for r in out)
    assert out[0]["cwd"] == str(work) and "argv: -q tests/" in out[0]["stdout"]
    assert out[1]["origin_step"] == 2 and out[1]["origin_step_text"] == "build it"
    assert out[4]["env_unresolved"] is True
    assert "passed at step 2 and fails at closure (exit 1)" in closure_verify._detect_regression_gap(out)
    # Inconclusive alone never reads as a regression.
    assert closure_verify._detect_regression_gap([out[0], out[2]]) == ""


def test_run_regression_obligations_without_cwd_is_inconclusive():
    out = closure_verify._run_regression_obligations([{"command": "pytest -q", "step_no": 1}],
                                                     cwd=None, timeout_per_check=5)
    assert out[0]["outcome"] == "inconclusive" and out[0]["env_unresolved"] is True


def test_run_regression_obligations_honours_kill_switch(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "get_bool", lambda key, default=True: False)
    exe = _fake_pytest(tmp_path)
    out = closure_verify._run_regression_obligations(
        [{"command": f"{exe} -q", "cwd": str(tmp_path), "step_no": 1}],
        cwd=str(tmp_path), timeout_per_check=5)
    assert out == []


def test_run_regression_obligations_tally_beats_verifier_failure_heuristics(monkeypatch, tmp_path):
    """A completed process whose output carries the runner's failure tally is
    a FAIL even when the exit code / stderr look like a verifier failure."""
    import subprocess

    def fake_run(cmd, **kwargs):
        proc = MagicMock()
        proc.returncode, proc.stdout, proc.stderr = 127, "3 failed, 1 passed\n", "command not found"
        return proc
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = closure_verify._run_regression_obligations(
        [{"command": "pytest -q", "cwd": str(tmp_path), "step_no": 1}], cwd=None, timeout_per_check=5)
    assert out[0]["outcome"] == "fail" and out[0]["passed"] is False


def test_run_regression_obligations_never_uses_a_shell(monkeypatch, tmp_path):
    import subprocess
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["shell"] = kwargs.get("shell")
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        proc = MagicMock()
        proc.returncode, proc.stdout, proc.stderr = 0, "3 passed", ""
        return proc
    monkeypatch.setattr(subprocess, "run", fake_run)
    closure_verify._run_regression_obligations(
        [{"command": 'PYTHONPATH=src pytest -k "not slow" -q', "cwd": str(tmp_path), "step_no": 1}],
        cwd="/elsewhere", timeout_per_check=5)
    assert seen["cmd"] == ["pytest", "-k", "not slow", "-q"]
    assert seen["shell"] is False
    assert seen["cwd"] == str(tmp_path)          # recorded cwd, not closure's
    assert seen["env"]["PYTHONPATH"] == "src"


def _adapter(verdict):
    adapter = MagicMock()
    responses = []
    for payload in ({"checks": [{"description": "validator", "command": "python3 validate.py"}]}, verdict):
        resp = MagicMock()
        resp.content = json.dumps(payload)
        resp.input_tokens = 1
        resp.output_tokens = 1
        responses.append(resp)
    adapter.complete.side_effect = responses
    return adapter


def _closure(monkeypatch, tmp_path, *, regression_exit, obligations=True, confidence=0.9):
    """Generated check passes; the obligation re-run exits `regression_exit`."""
    def fake_run(cmd, **kwargs):
        proc = MagicMock()
        is_rg = isinstance(cmd, list) and cmd[0] == "pytest"
        proc.returncode = regression_exit if is_rg else 0
        proc.stdout = "3 passed\n" if is_rg and regression_exit == 0 else "ok\n"
        proc.stderr = "" if regression_exit in (0, 1) else "bash: pytest: command not found"
        return proc
    monkeypatch.setattr("subprocess.run", fake_run)
    return closure_verify.verify_goal_completion(
        goal="add the feature",
        steps=[{"result": "implemented; pytest passed"}],
        adapter=_adapter({"complete": True, "confidence": confidence, "gaps": [],
                          "summary": "Goal achieved."}),
        workspace_path=str(tmp_path),
        regression_obligations=[{"command": "pytest -q tests/", "cwd": str(tmp_path),
                                 "step_no": 2, "step_text": "implement it"}] if obligations else None,
    )


def test_closure_downgrades_on_regression(monkeypatch, tmp_path):
    v = _closure(monkeypatch, tmp_path, regression_exit=1)
    assert v.complete is False
    assert "passed at step 2 and fails at closure" in v.downgrade_reason
    assert v.summary.startswith("Downgraded to not-achieved")
    assert any(g.startswith("Regression:") for g in v.gaps)
    assert v.checks_run == 2 and v.checks_passed == 1
    assert any("pytest" in sig for sig in v.failed_checks)


def test_closure_regression_floors_confidence_for_the_director(monkeypatch, tmp_path):
    # Mechanical evidence: the director's demotion (>=0.7) and restart
    # (>=0.6) rules must see it even when the judge was unsure of itself.
    v = _closure(monkeypatch, tmp_path, regression_exit=1, confidence=0.3)
    assert v.complete is False and v.confidence >= 0.7


def test_closure_keeps_verdict_when_obligation_passes(monkeypatch, tmp_path):
    v = _closure(monkeypatch, tmp_path, regression_exit=0)
    assert v.complete is True and not v.downgrade_reason
    assert v.checks_run == 2 and v.checks_passed == 2


def test_closure_inconclusive_rerun_never_downgrades_nor_vetoes_restart(monkeypatch, tmp_path):
    v = _closure(monkeypatch, tmp_path, regression_exit=127)
    assert v.complete is True and not v.downgrade_reason
    # Not counted ANYWHERE: an unrunnable obligation must neither fail the
    # director's `inconclusive_count == 0` restart rule nor read as a hard
    # failure through checks_passed < checks_run.
    assert v.inconclusive_count == 0
    assert v.checks_run == 1 and v.checks_passed == 1


def test_closure_inconclusive_regression_row_steers_no_guard(monkeypatch, tmp_path):
    """The excluded row must not change the verdict the same evidence gives
    without it (round-3: it used to bypass the all-passed confidence cap)
    — and it still lands in the persisted record."""
    def judge(complete, confidence):
        return {"complete": complete, "confidence": confidence, "gaps": ["x"] if not complete else [],
                "summary": "judged"}

    def run(obligations):
        def fake_run(cmd, **kwargs):
            proc = MagicMock()
            is_rg = isinstance(cmd, list) and cmd[0] == "pytest"
            proc.returncode = 127 if is_rg else 0
            proc.stdout = "ok\n"
            proc.stderr = "bash: pytest: command not found" if is_rg else ""
            return proc
        monkeypatch.setattr("subprocess.run", fake_run)
        return closure_verify.verify_goal_completion(
            goal="add the feature", steps=[{"result": "implemented"}],
            adapter=_adapter(judge(False, 0.9)), workspace_path=str(tmp_path),
            regression_obligations=obligations)
    without = run(None)
    with_noise = run([{"command": "pytest -q", "cwd": str(tmp_path), "step_no": 2}])
    assert (with_noise.complete, with_noise.confidence, with_noise.checks_run,
            with_noise.checks_passed, with_noise.inconclusive_count) == \
           (without.complete, without.confidence, without.checks_run,
            without.checks_passed, without.inconclusive_count)


def test_closure_only_an_inconclusive_regression_row_is_unjudged(monkeypatch, tmp_path):
    adapter = MagicMock()
    resp = MagicMock(); resp.content = json.dumps({"checks": []}); resp.input_tokens = resp.output_tokens = 1
    adapter.complete.return_value = resp
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: (_ for _ in ()).throw(FileNotFoundError("pytest")))
    v = closure_verify.verify_goal_completion(
        goal="g", steps=[{"result": "r"}], adapter=adapter, workspace_path=str(tmp_path),
        regression_obligations=[{"command": "pytest -q", "cwd": str(tmp_path), "step_no": 1}])
    assert v.checks_run == 0 and v.judged is False


def test_closure_without_obligations_is_byte_identical_in_shape(monkeypatch, tmp_path):
    v = _closure(monkeypatch, tmp_path, regression_exit=1, obligations=False)
    assert v.complete is True and v.checks_run == 1


# --- loop-level harvest ------------------------------------------------------

def test_loop_harvests_obligations_from_tool_events_and_carries_them(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)
    seen_cwd = []

    class _Adapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            seen_cwd.append(kwargs.get("cwd"))
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step",
                                     arguments={"result": "tests pass", "summary": "ok"})],
                input_tokens=1, output_tokens=1,
                tool_events=[_ev("pytest -q tests/test_feature.py"),
                             _ev("pytest --collect-only"),
                             _ev("pytest -q tests/test_other.py || true"),
                             _ev("pytest -q tests/test_silent.py", result_seen=False)],
            )

    result = al.run_agent_loop(
        "harvest flow", adapter=_Adapter(),
        preset_steps=["Implement the feature and run its tests"],
        max_steps=1, max_iterations=3,
    )
    assert result.status == "done"
    assert [o["command"] for o in result.regression_obligations] == ["pytest -q tests/test_feature.py"]
    assert result.regression_obligations[0]["step_index"] == result.steps[0].index
    assert os.path.isabs(result.regression_obligations[0]["cwd"])
    # The cwd is the one the adapter was handed (step_exec stamps it beside
    # the transcript), not a guess from the loop's side.
    assert result.regression_obligations[0]["cwd"] == os.path.abspath(seen_cwd[0] or os.getcwd())


def test_loop_harvest_skips_step_demoted_after_execution(monkeypatch, tmp_path):
    """A step whose FINAL status is not done contributes nothing, even when
    the raw executor outcome was done with a passing runner in it."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)

    def _demote(ctx, step_text, step_idx, step_status, step_result, step_summary,
                step_elapsed, outcome, *a, **k):
        return "blocked", "demoted by post-step check", []
    monkeypatch.setattr(loop_execute, "_post_step_checks", _demote)
    monkeypatch.setattr(loop_execute, "_process_blocked_step",
                        lambda ctx, blk: ("normal", blk.step_idx, "", "", 0, 0, blk.replan_count))

    class _Adapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="", tool_calls=[ToolCall(name="complete_step",
                                                 arguments={"result": "ok", "summary": "ok"})],
                input_tokens=1, output_tokens=1,
                tool_events=[_ev("pytest -q tests/test_feature.py")])

    result = al.run_agent_loop("demote flow", adapter=_Adapter(),
                               preset_steps=["Implement it"], max_steps=1, max_iterations=3)
    assert result.regression_obligations == []


def test_checkpoint_round_trips_regression_rows(tmp_path, monkeypatch):
    from checkpoint import Checkpoint
    ck = Checkpoint(loop_id="l1", goal="g", project="p", steps=["a"], completed=[],
                    regression=[{"command": "pytest -q", "cwd": "/w", "step_no": 1}])
    back = Checkpoint.from_dict(json.loads(json.dumps(ck.to_dict())))
    assert back.regression == [{"command": "pytest -q", "cwd": "/w", "step_no": 1}]
    assert Checkpoint.from_dict({"loop_id": "l2", "goal": "g", "project": "p",
                                 "steps": [], "completed": []}).regression is None


def test_stream_parser_marks_seen_results():
    """An unmatched tool_use renders as output "" — silence the ledger must
    be able to tell from a seen empty result."""
    import llm
    events = "\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest -q x"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "3 passed"}]}},
        {"type": "result", "subtype": "success", "result": "done"},
    ])
    out = llm._parse_stream_json(events)
    by_id = {e["id"]: e for e in out["tool_events"]}
    assert by_id["t1"]["result_seen"] is True and by_id["t1"]["output"] == "3 passed"
    assert by_id["t2"]["result_seen"] is False and by_id["t2"]["output"] == ""


def test_stream_parser_refuses_ambiguous_joins():
    """A repeated or null tool-use id could hand one tool's result to
    another: no event on such an id is `result_seen`."""
    import llm
    events = "\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "dup", "name": "Bash", "input": {"command": "pytest -q"}},
            {"type": "tool_use", "id": "dup", "name": "Read", "input": {"file": "x"}},
            {"type": "tool_use", "id": None, "name": "Bash", "input": {"command": "pytest -q y"}},
            {"type": "tool_use", "id": "twice", "name": "Bash", "input": {"command": "pytest -q z"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "dup", "content": "3 passed"},
            {"type": "tool_result", "tool_use_id": None, "content": "3 passed"},
            {"type": "tool_result", "tool_use_id": "twice", "content": "3 passed"},
            {"type": "tool_result", "tool_use_id": "twice", "content": "1 failed"}]}},
        {"type": "result", "subtype": "success", "result": "done"},
    ])
    out = llm._parse_stream_json(events)
    assert all(e["result_seen"] is False for e in out["tool_events"])
    led = rl.RegressionLedger()
    assert led.harvest(out["tool_events"], step_index=1, step_no=1, executor_cwd="/tmp") == []


def test_stream_parser_rejects_bool_ids_that_collide_with_ints():
    import llm
    events = "\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": 1, "name": "Bash", "input": {"command": "pytest -q"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": True, "content": "3 passed"}]}},
        {"type": "result", "subtype": "success", "result": "done"},
    ])
    out = llm._parse_stream_json(events)
    assert out["tool_events"][0]["result_seen"] is False and out["tool_events"][0]["output"] == ""


def test_transcript_summarizer_keeps_result_seen_and_marks_truncation():
    from step_exec import _summarize_tool_events, _TRANSCRIPT_OUTPUT_CAP
    view = _summarize_tool_events([
        {"name": "Bash", "input": {"command": "pytest -q"}, "output": "x" * (_TRANSCRIPT_OUTPUT_CAP + 5),
         "is_error": False, "result_seen": True},
        {"name": "Bash", "input": {"command": "pytest -q y"}, "output": "3 passed",
         "is_error": False, "result_seen": False},
        {"name": "Bash", "input": {"command": "pytest -q z"}, "output": "3 passed", "is_error": False},
    ])
    assert view[0]["output_truncated"] is True and view[0]["result_seen"] is True
    assert view[1]["result_seen"] is False and "output_truncated" not in view[1]
    assert "result_seen" not in view[2]
    led = rl.RegressionLedger()
    assert led.harvest(view, step_index=1, step_no=1, executor_cwd="/tmp") == []
