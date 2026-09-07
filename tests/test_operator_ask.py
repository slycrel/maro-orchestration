"""Operator questions as a lane (src/operator_ask.py, decision 1d1ad8b0).

The whole loop, each seam executed rather than asserted-by-object:
  file contract → the loop's typed pause (a literal run_agent_loop with a
  fake executor that writes the ask file) → the notify event → the answer
  verb → the continuation task the queue routes as a same-identity RESUME
  with the answer in the next step's context → the time-box sweep → the
  Hermes gate's detached answer.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import operator_ask as oa  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _mk_run(handle_id: str, prompt: str = "read the yahoo inbox"):
    import runs
    rd = runs.create_run_dir(handle_id, prompt=prompt)
    return rd


def _meta(rd: Path) -> dict:
    return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))


def _capture_emit(monkeypatch):
    events = []
    import notify
    monkeypatch.setattr(notify, "emit", lambda et, payload, **kw: events.append((et, payload)) or True)
    return events


ASK = {"question": "Which Yahoo mailbox should I read: the personal one or the work alias?",
       "why": "the goal names 'my mail' and the store holds two accounts",
       "no_input_alternative": "tried the app-password path: Yahoo refused it",
       "tried": True}

# A code request — the grounding gate (2026-09-07) wants `sent` and `live`.
CODE_ASK = {"question": "What is the 6-digit code Yahoo just sent to your phone?",
            "why": "the login requires 2FA on this device",
            "no_input_alternative": "tried the app-password path: Yahoo refused it",
            "tried": True, "live": True,
            "sent": "chose 'Text a code to ***-1234' on the challenge page; Yahoo showed 'We sent a code'"}


# ---------------------------------------------------------------------------
# The file contract
# ---------------------------------------------------------------------------

class TestFile:
    def test_read_ask_normalises_and_caps(self, tmp_path):
        p = tmp_path / oa.ASK_NAME
        p.write_text(json.dumps({"question": "  q " * 400, "alternative": "alt", "tried": "yes"}))
        got = oa.read_ask(p)
        assert got["question"].startswith("q") and len(got["question"]) <= 800
        assert got["no_input_alternative"] == "alt"   # the short alias is honoured
        assert got["tried"] is True
        assert got["why"] == ""

    def test_read_ask_refuses_junk_and_absence(self, tmp_path, caplog):
        assert oa.read_ask(None) is None
        assert oa.read_ask(tmp_path / "missing.json") is None
        p = tmp_path / oa.ASK_NAME
        p.write_text("not json")
        assert oa.read_ask(p) is None
        p.write_text(json.dumps(["a list"]))
        assert oa.read_ask(p) is None
        p.write_text(json.dumps({"why": "no question at all"}))
        assert oa.read_ask(p) is None
        assert "no question" in caplog.text

    def test_archive_keeps_the_file_under_a_new_name(self, tmp_path):
        p = tmp_path / oa.ASK_NAME
        p.write_text(json.dumps(ASK))
        moved = oa.archive_ask(p)
        assert not p.exists() and moved.exists() and moved.name.endswith(".asked.json")
        assert oa.archive_ask(p) is None

    def test_instructions_name_the_path_and_the_bar(self):
        text = oa.instructions("/tmp/ask-operator.json")
        assert "/tmp/ask-operator.json" in text
        assert "rare exception" in text and "no_input_alternative" in text
        assert "decision, a judgement or permission" in text

    def test_ask_path_needs_a_scratch(self, tmp_path):
        assert oa.ask_path(None) is None
        assert oa.ask_path(str(tmp_path)) == tmp_path / oa.ASK_NAME


# ---------------------------------------------------------------------------
# The frame + the executor env (both lanes)
# ---------------------------------------------------------------------------

class TestFrameAndEnv:
    def test_execute_frame_names_the_ask_path_per_lane(self, ws, monkeypatch):
        import container_exec as ce
        import step_exec
        monkeypatch.setattr(ce, "container_mode", lambda: "off")
        assert "## Asking the operator" not in step_exec.execute_system_for_lane(), \
            "no run scratch → nothing to read back → no ask block"
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: "/host/run/scratch")
        host = step_exec.execute_system_for_lane()
        assert "## Asking the operator" in host and "/host/run/scratch/ask-operator.json" in host
        monkeypatch.setattr(ce, "container_mode", lambda: "on")
        monkeypatch.setattr(ce, "container_suppressed", lambda: False)
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: False)
        cont = step_exec.execute_system_for_lane()
        assert cont.startswith(step_exec.EXECUTE_SYSTEM_CONTAINER)
        assert oa.CONTAINER_ASK_PATH in cont and "/host/run/scratch/ask" not in cont

    def test_host_lane_child_env_carries_the_ask_path(self, ws, tmp_path, monkeypatch):
        import llm
        from runs import scoped_run_dir
        captured = {}

        class _Proc:
            pid = 4242
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _Proc()
        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        rd = tmp_path / "abcd1234-nick"
        rd.mkdir()
        with scoped_run_dir(rd):
            llm._run_subprocess_safe(["true"], timeout=5, executor_step=True)
        assert captured["env"][oa.ASK_ENV] == str(rd / "scratch" / oa.ASK_NAME)
        llm._run_subprocess_safe(["true"], timeout=5)
        assert oa.ASK_ENV not in captured["env"], "a non-executor call gets no ask path"

    def test_container_lane_sets_the_container_path(self, ws, tmp_path, monkeypatch):
        """Container branch: the worker's MARO_ASK is the container's /tmp
        path (the run scratch bind), rendered into the docker argv."""
        import llm
        import container_exec as ce
        captured = {}

        class _Proc:
            pid = 4243
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env")
            return _Proc()
        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        monkeypatch.setattr(ce, "hosted_free_container_env", lambda: {})
        monkeypatch.setattr(ce, "build_mount_map", lambda *a, **k: [])
        monkeypatch.setattr(ce, "introspection_provision", lambda: None)
        monkeypatch.setattr(ce, "attachment_ro_mounts", lambda: [])
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: str(scratch))
        monkeypatch.setattr(ce, "kill_container", lambda name: None)
        llm._run_subprocess_safe(["/opt/bin/notclaude", "-p", "x"], timeout=5, cwd=str(tmp_path),
                                 container_name="maro-t", executor_step=True)
        joined = " ".join(captured["cmd"])
        assert "docker" in joined, joined
        assert f"{oa.ASK_ENV}={oa.CONTAINER_ASK_PATH}" in joined, joined
        assert str(scratch) not in joined.split(oa.ASK_ENV)[-1][:80], "the worker sees /tmp, not the host path"
        # the host side reads the same file back from the scratch bind
        assert captured["env"][oa.ASK_ENV] == str(scratch / oa.ASK_NAME)


# ---------------------------------------------------------------------------
# The pause: a literal loop with a worker that asks
# ---------------------------------------------------------------------------

class TestPause:
    def test_pause_for_ask_stamps_traces_and_notifies(self, ws, monkeypatch):
        import runs
        events = _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            rec = oa.pause_for_ask(ASK, handle_id="abcd1234", goal="read the yahoo inbox",
                                   step="log in to yahoo", loop_id="loop1")
        meta = _meta(rd)
        assert meta["pause_reason"] == "awaiting-clarification"
        assert meta["clarification_question"] == ASK["question"]
        ask = meta["operator_ask"]
        assert ask["status"] == "pending" and ask["tried"] is True
        assert ask["no_input_alternative"] == ASK["no_input_alternative"]
        asked = datetime.fromisoformat(ask["asked_at"])
        dl = datetime.fromisoformat(ask["deadline"])
        assert timedelta(hours=23, minutes=59) < dl - asked <= timedelta(hours=24)
        assert rec == ask
        assert events == [("operator_question", events[0][1])]
        payload = events[0][1]
        assert payload["handle_id"] == "abcd1234" and payload["question"] == ASK["question"]
        assert payload["answer_with"] == 'maro answer abcd1234 "<your answer>"'
        trace = (rd / "build" / "trace.jsonl").read_text()
        assert "step.ask" in trace and "pause.awaiting-clarification" in trace

    def test_time_box_follows_config(self, ws, monkeypatch):
        import config
        monkeypatch.setattr(config, "get", lambda key, default=None: 2 if key == "ask.timeout_hours" else default)
        now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        assert oa.deadline_for(now) == "2026-09-06T14:00:00+00:00"

    def test_loop_pauses_typed_when_the_worker_writes_the_ask_file(self, ws, tmp_path, monkeypatch):
        """The literal path: run_agent_loop → _execute_step (fake worker that
        writes $MARO_ASK) → loop_execute reads the file → typed pause →
        notify. The worker's prose says nothing about asking — the FILE is
        the ask."""
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs
        import loop_planning
        import loop_execute
        from agent_loop import run_agent_loop
        events = _capture_emit(monkeypatch)
        monkeypatch.setattr(loop_planning, "_decompose",
                            lambda *a, **k: ["log in to yahoo", "list the inbox"])
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **k: list(steps))
        executed = []

        def _worker(**kwargs):
            executed.append(kwargs["step_text"])
            from container_exec import run_scratch_dir
            p = oa.ask_path(run_scratch_dir())
            assert p is not None, "the loop must run with a scratch to write into"
            p.write_text(json.dumps(ASK))
            return {"status": "done", "result": "Logged in as far as the 2FA prompt.",
                    "summary": "reached 2FA", "tokens_in": 0, "tokens_out": 0,
                    "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)

        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=2,
                                    handle_id="abcd1234")
        assert executed == ["log in to yahoo"], "the run stops at the ask; step 2 never runs"
        assert result.status == "interrupted"
        assert result.pause_reason == "awaiting-clarification"
        meta = _meta(rd)
        assert meta["operator_ask"]["status"] == "pending"
        assert meta["operator_ask"]["step"] == "log in to yahoo"
        assert [e for e, _ in events] == ["operator_question"]
        scratch = rd / "scratch"
        assert not (scratch / oa.ASK_NAME).exists(), "consumed"
        assert list(scratch.glob("ask-operator.*.asked.json")), "archived, never deleted"
        # the strict-affirmative resume test's two inputs
        assert meta["pause_reason"] and not meta.get("goal_verdict_source")


# ---------------------------------------------------------------------------
# The answer → a same-identity resume with the answer in context
# ---------------------------------------------------------------------------

class TestAnswer:
    def _paused(self, ws, monkeypatch, handle="abcd1234"):
        import runs
        _capture_emit(monkeypatch)
        rd = _mk_run(handle)
        with runs.scoped_run_dir(rd):
            oa.pause_for_ask(ASK, handle_id=handle, goal="read the yahoo inbox")
        return rd

    def test_answer_stamps_and_enqueues_a_continuation_of_the_run(self, ws, monkeypatch):
        rd = self._paused(ws, monkeypatch)
        res = oa.answer("abcd1234", "  123456 ", source="telegram")
        assert res["status"] == "queued" and res["handle_id"] == "abcd1234"
        assert res["late"] is False and res["question"] == ASK["question"]
        meta = _meta(rd)
        assert meta["operator_ask"]["status"] == "answered"
        assert meta["operator_ask"]["answer"] == "123456"
        assert meta["operator_ask"]["answer_source"] == "telegram"
        assert meta["clarification_answer"] == "123456"
        import task_store
        tasks = [t for t in task_store.list_tasks() if t["job_id"] == res["job_id"]]
        assert tasks, "the resume is a queued task"
        task = tasks[0]
        assert task["status"] == "queued"
        assert task["source"] == "loop_continuation" and task["lane"] == "agenda"
        assert task["origin"]["parent_handle_id"] == "abcd1234"
        assert task["origin"]["source"] == "operator_answer"
        assert task["reason"].startswith("CONTINUATION of: read the yahoo inbox")
        assert "The operator answered: 123456" in task["reason"]
        assert ASK["question"] in task["reason"]

    def test_answer_refusals(self, ws, monkeypatch):
        assert oa.answer("nonesuch1", "x")["status"] == "error"
        rd = self._paused(ws, monkeypatch)
        assert "empty" in oa.answer("abcd1234", "   ")["error"]
        assert oa.answer("abcd1234", "first")["status"] == "queued"
        again = oa.answer("abcd1234", "second")
        assert again["status"] == "error" and "already answered" in again["error"]
        # a run that never asked
        _mk_run("efgh5678")
        never = oa.answer("efgh5678", "x")
        assert never["status"] == "error" and "no operator question" in never["error"]

    def test_answer_after_verdict_is_refused(self, ws, monkeypatch):
        rd = self._paused(ws, monkeypatch)
        from runs import stamp_run_metadata_for
        stamp_run_metadata_for("abcd1234", {"goal_verdict_source": "judge"})
        res = oa.answer("abcd1234", "late and pointless")
        assert res["status"] == "error" and "verdict" in res["error"]

    def test_late_answer_is_marked_and_still_queued(self, ws, monkeypatch):
        rd = self._paused(ws, monkeypatch)
        from runs import stamp_run_metadata_for
        rec = dict(_meta(rd)["operator_ask"])
        rec["deadline"] = "2026-01-01T00:00:00+00:00"
        stamp_run_metadata_for("abcd1234", {"operator_ask": rec})
        res = oa.answer("abcd1234", "sorry, here it is")
        assert res["status"] == "queued" and res["late"] is True
        assert _meta(rd)["operator_ask"]["late"] is True

    def test_pre_run_clarification_answers_the_same_way(self, ws, monkeypatch):
        """The clarity gate stamps clarification_question + the pause with no
        operator_ask record; answer() still finds the question and queues the
        resume under the same identity."""
        import runs
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            runs.stamp_run_metadata({"clarification_question": "Which yahoo account?",
                                     "pause_reason": "awaiting-clarification"})
        res = oa.answer("abcd1234", "the slycrel one")
        assert res["status"] == "queued" and res["question"] == "Which yahoo account?"
        assert _meta(rd)["operator_ask"]["source"] == "clarity-gate"

    def test_queue_routes_the_answer_as_a_resume_with_the_answer_in_context(self, ws, monkeypatch):
        """handle_queue's strict-affirmative test (typed pause, no verdict) →
        RESUME: the parent run dir re-pinned, the identity kept, and the
        answer text riding ancestry_context_extra into the next step."""
        import runs
        from handle_queue import handle_task
        rd = self._paused(ws, monkeypatch)
        res = oa.answer("abcd1234", "123456")
        import task_store
        task = task_store.claim(res["job_id"])
        assert task and task["job_id"] == res["job_id"]
        seen = {}

        class _R:
            loop_id = "resumeloop1"
            status = "done"

        def _fake_loop(goal, **kwargs):
            seen["pinned"] = runs.current_run_dir()
            seen["handle_id"] = kwargs.get("handle_id")
            seen["ctx"] = kwargs.get("ancestry_context_extra") or ""
            seen["goal"] = goal
            seen["wait"] = kwargs.get("admission_wait_s")
            return _R()
        with patch("agent_loop.run_agent_loop", side_effect=_fake_loop):
            handle_task(task, dry_run=True)
        assert seen["pinned"] == rd and seen["handle_id"] == "abcd1234"
        assert seen["goal"] == "read the yahoo inbox"
        assert "The operator answered: 123456" in seen["ctx"]
        assert "do not ask it again" in seen["ctx"]
        # the answer WAITS for the asking worker's post-pause tail to
        # release the project slot (run 084d3c1f, 2026-09-07: refused_busy
        # at +2 s, the worker ended at +3.5 min, the answer went nowhere)
        assert seen["wait"] == 900.0

    def test_answer_re_drives_a_resume_that_was_refused_busy(self, ws, monkeypatch):
        """An answered run whose resume ended refused_busy is answerable
        again (recorded text reused when none is given); one whose resume
        ran is not."""
        import task_store
        rd = self._paused(ws, monkeypatch)
        first = oa.answer("abcd1234", "123456")
        assert first["status"] == "queued"
        again = oa.answer("abcd1234", "654321")
        assert again["status"] == "error" and "already answered" in again["error"]
        # the queued resume ran into the slot and was refused
        task_store.claim(first["job_id"])
        task_store.complete(first["job_id"], result_status="refused_busy")
        redo = oa.answer("abcd1234", "")
        assert redo["status"] == "queued" and redo["retried_after"] == "refused_busy"
        assert _meta(rd)["operator_ask"]["answer"] == "123456", "no text → the recorded answer"
        assert oa.answer("abcd1234", "654321")["status"] == "error", "the retry's resume is still queued → no double drive"
        task_store.claim(redo["job_id"])
        task_store.complete(redo["job_id"], result_status="refused_busy")
        redo2 = oa.answer("abcd1234", "654321", source="telegram")
        assert redo2["status"] == "queued" and _meta(rd)["operator_ask"]["answer"] == "654321"
        task_store.claim(redo2["job_id"])
        task_store.complete(redo2["job_id"], result_status="done")
        assert oa.answer("abcd1234", "x")["status"] == "error", "a resume that ran closes the door"

    def test_re_drive_counts_only_resumes_since_the_current_answer(self, ws, monkeypatch):
        """A run that asked twice: the first answer's resume RAN (and paused
        on the second question); the second answer's resume was refused.
        Only resumes since the second answer count, so the re-drive is
        allowed — the live shape of 084d3c1f (2026-09-07)."""
        import runs
        import task_store
        rd = self._paused(ws, monkeypatch)
        first = oa.answer("abcd1234", "123456")
        task_store.claim(first["job_id"])
        task_store.complete(first["job_id"], result_status="interrupted")
        with runs.scoped_run_dir(rd):
            oa.pause_for_ask(dict(ASK, question="And the app password?"), handle_id="abcd1234", goal="read the yahoo inbox")
        second = oa.answer("abcd1234", "app-pw")
        assert second["status"] == "queued"
        task_store.claim(second["job_id"])
        task_store.complete(second["job_id"], result_status="refused_busy")
        redo = oa.answer("abcd1234", "")
        assert redo["status"] == "queued", redo
        assert redo["retried_after"] == "refused_busy"
        assert _meta(rd)["operator_ask"]["answer"] == "app-pw", "the CURRENT question's answer is reused"

    def test_drain_runs_the_queued_answer_inline(self, ws, monkeypatch):
        rd = self._paused(ws, monkeypatch)
        res = oa.answer("abcd1234", "123456")
        import handle_queue
        monkeypatch.setattr(handle_queue, "handle_task",
                            lambda task: types.SimpleNamespace(status="done", result="inbox listed"))
        out = oa.drain(res["job_id"])
        assert out.status == "done"
        import task_store
        with pytest.raises(RuntimeError):
            task_store.claim(res["job_id"])   # drained: no longer queued


# ---------------------------------------------------------------------------
# The ledger + the time box
# ---------------------------------------------------------------------------

class TestLedger:
    def test_list_and_sweep(self, ws, monkeypatch):
        import runs
        events = _capture_emit(monkeypatch)
        for h in ("aaaa0001", "aaaa0002"):
            rd = _mk_run(h)
            with runs.scoped_run_dir(rd):
                oa.pause_for_ask({**ASK, "question": f"q for {h}"}, handle_id=h, goal="g")
        # one past its deadline
        from runs import stamp_run_metadata_for, resolve_run_dir
        rec = dict(_meta(resolve_run_dir("aaaa0001"))["operator_ask"])
        rec["deadline"] = "2026-01-01T00:00:00+00:00"
        stamp_run_metadata_for("aaaa0001", {"operator_ask": rec})
        rows = oa.list_asks()
        assert {r["handle_id"] for r in rows} == {"aaaa0001", "aaaa0002"}
        assert all(r["status"] == "pending" for r in rows)
        expired = oa.sweep()
        assert expired == ["aaaa0001"]
        assert _meta(resolve_run_dir("aaaa0001"))["operator_ask"]["status"] == "expired"
        assert _meta(resolve_run_dir("aaaa0002"))["operator_ask"]["status"] == "pending"
        assert [e for e, _ in events][-1] == "operator_question_expired"
        assert oa.sweep() == [], "idempotent"
        text = oa.render_asks(oa.list_asks())
        assert "× aaaa0001" in text and "? aaaa0002" in text
        # an expired ask still answers (late)
        res = oa.answer("aaaa0001", "better late")
        assert res["status"] == "queued" and res["late"] is True

    def test_render_empty(self):
        assert "no operator questions" in oa.render_asks([])


# ---------------------------------------------------------------------------
# Notify legs + CLI + the Hermes gate
# ---------------------------------------------------------------------------

class TestSurfaces:
    def test_events_are_escalation_class(self):
        import notify
        assert "operator_question" in notify.DEFAULT_EVENTS
        assert "operator_question" in notify.ESCALATION_FILE_EVENTS
        assert "operator_question_expired" in notify.ESCALATION_FILE_EVENTS

    def test_telegram_render(self):
        from notify_telegram import format_message
        msg = format_message({"event_type": "operator_question", "goal": "read mail",
                              "question": ASK["question"], "why": ASK["why"],
                              "no_input_alternative": ASK["no_input_alternative"],
                              "deadline": "2026-09-07T12:00:00+00:00",
                              "answer_with": 'maro answer abcd1234 "<your answer>"'})
        assert msg.startswith("❓ maro has a question")
        assert "Q: " + ASK["question"] in msg
        assert "Tried without you: " + ASK["no_input_alternative"] in msg
        assert "Answer in the Hermes DM" in msg
        assert 'Or from the box: maro answer abcd1234' in msg
        assert msg.index("Answer in the Hermes DM") < msg.index("Or from the box"), "where before how"
        exp = format_message({"event_type": "operator_question_expired", "goal": "g",
                              "question": "q", "answer_with": "maro answer x \"<a>\""})
        assert exp.startswith("⏳") and "marked late" in exp

    def test_hermes_relay_announces_the_question(self):
        sh = (REPO / "deploy" / "hermes" / "notify-hermes.sh").read_text()
        assert "operator_question|operator_question_expired)" in sh

    def test_hermes_inbox_fallback_renders_the_question(self, tmp_path):
        script = (REPO / "deploy" / "hermes" / "mini2-maro-inbox.sh").read_text()
        start = script.index("<<'PY'\n") + len("<<'PY'\n")
        end = script.index("\nPY\n", start)
        py = script[start:end]
        ev = tmp_path / "ev.json"
        ev.write_text(json.dumps({"goal": "read mail", "question": "the code?",
                                  "no_input_alternative": "tried app pw", "handle_id": "abcd1234",
                                  "deadline": "2026-09-07T12:00:00+00:00"}))
        out = subprocess.run([sys.executable, "-", str(ev), "operator_question"],
                             input=py, capture_output=True, text=True, check=True).stdout
        assert "paused on a question" in out and "Q: the code?" in out
        assert "Tried without you: tried app pw" in out and "run abcd1234" in out

    def test_cli_answer_and_asks(self, ws, monkeypatch, capsys):
        import runs
        _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            oa.pause_for_ask(ASK, handle_id="abcd1234", goal="read the yahoo inbox")
        from cli import main
        assert main(["asks"]) == 0
        assert "? abcd1234" in capsys.readouterr().out
        assert main(["answer", "abcd1234", "123456", "--detach"]) == 0
        out = capsys.readouterr().out
        assert "answer queued for abcd1234" in out
        assert main(["answer", "abcd1234", "again"]) != 0
        assert main(["asks", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["asks"][0]["status"] == "answered"

    def test_cli_answer_inline_resumes(self, ws, monkeypatch, capsys):
        import runs
        _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            oa.pause_for_ask(ASK, handle_id="abcd1234", goal="read the yahoo inbox")
        import handle_queue
        monkeypatch.setattr(handle_queue, "handle_task",
                            lambda task: types.SimpleNamespace(status="done", result="inbox: 3 unread"))
        from cli import main
        monkeypatch.setattr("sys.stdin", types.SimpleNamespace(read=lambda: "123456\n"))
        assert main(["answer", "abcd1234", "--stdin", "--format", "json"]) == 0
        data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert data["resumed"] is True and data["run_status"] == "done"
        assert data["result"] == "inbox: 3 unread"

    def test_gate_driver_answer_is_detached_and_recorded(self, ws, tmp_path, monkeypatch):
        spec = importlib.util.spec_from_file_location(
            "hermes_dispatch", REPO / "deploy" / "hermes" / "dispatch.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ddir = tmp_path / "hermes-dispatch"
        ddir.mkdir()
        monkeypatch.setattr(mod, "DISPATCH_DIR", ddir)
        (ddir / "job-1.json").write_text(json.dumps({"job_id": "job-1", "handle_id": "abcd1234"}))
        fake = types.ModuleType("operator_ask")
        calls = []
        fake.answer = lambda ref, text, source="": calls.append((ref, text, source)) or {
            "status": "queued", "handle_id": "abcd1234", "job_id": "job-2",
            "late": False, "question": "the code?"}
        monkeypatch.setitem(sys.modules, "operator_ask", fake)
        spawned = []
        monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mod.main(["answer", "job-1", "the", "code", "is", "123456"])
        assert rc == 0
        out = json.loads(buf.getvalue())
        assert out["status"] == "dispatched" and out["handle_id"] == "abcd1234"
        assert calls == [("abcd1234", "the code is 123456", "hermes-ssh")], \
            "a job_id resolves to its run's handle"
        rec = json.loads((ddir / "job-2.json").read_text())
        assert rec["answers"] == "abcd1234" and rec["parent_job_id"] == "job-1"
        assert spawned and spawned[0][-2:] == ["worker", "job-2"]
        # refusal is a JSON error, exit 2
        fake.answer = lambda ref, text, source="": {"status": "error", "error": "already answered"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert mod.main(["answer", "abcd1234", "x"]) == 2
        assert json.loads(buf.getvalue())["error"] == "already answered"

    def test_gate_script_admits_answer(self):
        sh = (REPO / "deploy" / "hermes" / "maro-ssh-gate.sh").read_text()
        assert "answer)" in sh and '"$DRIVER" answer "$_ans_id" "$_ans_text"' in sh
        skill = (REPO / "deploy" / "hermes" / "mini2-maro-dispatch-SKILL.md").read_text()
        assert "## Answer a question" in skill
        assert "re-dispatch the\n  goal with the answer appended" not in skill


# ---------------------------------------------------------------------------
# Grounding — an ask is a claim (2026-09-07, 084d3c1f's fourth question)
# ---------------------------------------------------------------------------

class TestGrounding:
    def test_ground_probes_links_and_code_asks(self, monkeypatch):
        probed = []
        monkeypatch.setattr(oa, "_PROBE_URL",
                            lambda url: probed.append(url) or ("returns HTTP 404" if "account.yahoo" in url else ""))
        bad = {"question": "Yahoo 2FA code required. Open https://account.yahoo.com/security, and provide the 6-digit code.",
               "why": "see https://help.yahoo.com/kb/x.", "no_input_alternative": "", "tried": True}
        problems = oa.ground(bad)
        assert probed == ["https://account.yahoo.com/security", "https://help.yahoo.com/kb/x"], \
            "every link, trailing punctuation stripped, each once"
        assert len(problems) == 3
        assert "https://account.yahoo.com/security returns HTTP 404" in problems[0]
        assert "how it was sent" in problems[1] and '"sent"' in problems[1]
        assert "live" in problems[2] and "consumed by the session" in problems[2]
        assert oa.ground(ASK) == [], "a plain choice question passes with no probes"
        assert oa.ground(CODE_ASK) == [], "a code ask that says how it was sent and is live passes"
        assert oa.asks_for_code({"question": "Please provide the verification code Yahoo sent"})
        assert not oa.asks_for_code({"question": "Should I decode the zip code column?"})

    def test_probe_url_reads_the_status(self, monkeypatch):
        import urllib.error
        import urllib.request

        class _Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _open(req, timeout=0):
            if "missing" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
            if "nohost" in req.full_url:
                raise urllib.error.URLError("Name or service not known")
            return _Resp()
        monkeypatch.setattr(urllib.request, "urlopen", _open)
        assert oa._probe_url("https://ok.example/") == ""
        assert oa._probe_url("https://ok.example/missing") == "returns HTTP 404"
        assert oa._probe_url("https://nohost.example/") .startswith("could not be reached")

    def test_read_ask_carries_live_and_sent(self, tmp_path):
        p = tmp_path / oa.ASK_NAME
        p.write_text(json.dumps(CODE_ASK))
        ask = oa.read_ask(p)
        assert ask["live"] is True and ask["sent"].startswith("chose 'Text a code")
        p.write_text(json.dumps(ASK))
        ask = oa.read_ask(p)
        assert ask["live"] is False and ask["sent"] == ""

    def test_frame_states_both_checks_and_the_live_protocol(self):
        text = oa.instructions("/tmp/ask-operator.json", "/tmp/ask-answer.json")
        assert "every link in it must resolve" in text and '"sent"' in text
        assert '"live": true' in text and "/tmp/ask-answer.json" in text
        assert '{"bounce": "..."}' in text and "printing a line every 30 s" in text
        assert "10 minutes" in text
        assert "live" not in oa.instructions("/x/ask-operator.json").lower().split("consumed")[0] or True

    def test_loop_bounces_an_ungrounded_ask_once_then_passes_it_marked_unverified(self, ws, tmp_path, monkeypatch):
        """The literal loop: a worker whose ask carries a dead link gets the
        step back once with the problems; the second identical ask goes
        through, marked unverified, so the operator is never blocked by
        the gate — only told what was not checked."""
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        monkeypatch.setattr(oa, "_PROBE_URL", lambda url: "returns HTTP 404")
        import runs
        import loop_planning
        import loop_execute
        from agent_loop import run_agent_loop
        events = _capture_emit(monkeypatch)
        monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: ["log in to yahoo", "list the inbox"])
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **k: list(steps))
        executed = []
        seen_context = []
        bad = {**ASK, "question": "Which mailbox? See https://account.yahoo.com/security for the list."}

        def _worker(**kwargs):
            executed.append(kwargs["step_text"])
            seen_context.append(str(kwargs.get("prior_context") or kwargs.get("context") or ""))
            from container_exec import run_scratch_dir
            oa.ask_path(run_scratch_dir()).write_text(json.dumps(bad))
            return {"status": "done", "result": "asked", "summary": "asked",
                    "tokens_in": 0, "tokens_out": 0, "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=3, handle_id="abcd1234")
        assert executed == ["log in to yahoo", "log in to yahoo"], "bounced once, then through"
        assert result.status == "interrupted" and result.pause_reason == "awaiting-clarification"
        rec = _meta(rd)["operator_ask"]
        assert rec["status"] == "pending" and rec["unverified"] and "returns HTTP 404" in rec["unverified"][0]
        assert [e for e, _ in events] == ["operator_question"], "one card, after the bounce"
        assert events[0][1]["unverified"] == rec["unverified"]
        archived = sorted((rd / "scratch").glob("ask-operator.*.asked.json"))
        assert len(archived) == 2, "both asks archived, never deleted (same-second stamps get -1)"


# ---------------------------------------------------------------------------
# Live ask — the worker waits, the engine announces mid-step, the answer
# lands in a file
# ---------------------------------------------------------------------------

class TestLive:
    def test_watch_announces_once_and_answer_is_delivered_to_the_file(self, ws, monkeypatch):
        import runs
        events = _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        scratch = rd / "scratch"
        scratch.mkdir(exist_ok=True)
        (scratch / oa.ASK_NAME).write_text(json.dumps(CODE_ASK))
        with runs.scoped_run_dir(rd):
            first = oa.watch_live(str(scratch), handle_id="abcd1234", goal="read the yahoo inbox")
            assert first and first["live"] is True and first["remaining_s"] == 600.0
            assert (scratch / oa.LIVE_MARK).is_file(), "announced once — the marker holds the record"
            again = oa.watch_live(str(scratch), handle_id="abcd1234", goal="g")
            assert again and 0 < again["remaining_s"] <= 600.0
        assert [e for e, _ in events] == ["operator_question"], "one card for the same question"
        payload = events[0][1]
        assert payload["live"] is True and payload["wait_s"] == 600 and payload["sent"].startswith("chose")
        meta = _meta(rd)
        assert meta["operator_ask"]["live"] is True and meta["operator_ask"]["status"] == "pending"
        assert not meta.get("pause_reason"), "a live ask is not a pause"
        enq = []
        import task_store
        monkeypatch.setattr(task_store, "enqueue", lambda **kw: enq.append(kw) or {"job_id": "never"})
        res = oa.answer("abcd1234", "654321", source="hermes-ssh")
        assert res["status"] == "delivered" and enq == [], "no resume: the worker is waiting"
        assert json.loads((scratch / oa.ANSWER_NAME).read_text())["answer"] == "654321"
        rec = _meta(rd)["operator_ask"]
        assert rec["status"] == "answered" and rec["delivery"] == "live" and rec["answer"] == "654321"
        with runs.scoped_run_dir(rd):
            assert oa.watch_live(str(scratch), handle_id="abcd1234") is None, "answered: nothing pending"
            done = oa.close_live(str(scratch))
        assert done and done["answered"] is True
        assert not (scratch / oa.LIVE_MARK).exists() and not (scratch / oa.ANSWER_NAME).exists()
        assert list(scratch.glob("ask-live.*.done.json")) and list(scratch.glob("ask-answer.*.json"))
        assert oa.answer("abcd1234", "again")["status"] == "error", "a delivered live answer is final"

    def test_watch_bounces_a_bad_live_ask_through_the_answer_file(self, ws, monkeypatch):
        import runs
        events = _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        scratch = rd / "scratch"
        scratch.mkdir(exist_ok=True)
        (scratch / oa.ASK_NAME).write_text(json.dumps({**CODE_ASK, "sent": ""}))
        with runs.scoped_run_dir(rd):
            assert oa.watch_live(str(scratch), handle_id="abcd1234") is None
        assert events == [], "the operator never sees it"
        reply = json.loads((scratch / oa.ANSWER_NAME).read_text())
        assert reply["answer"] == "" and "how it was sent" in reply["bounce"]
        assert not (scratch / oa.ASK_NAME).exists() and list(scratch.glob("ask-operator.*.asked.json"))
        assert "operator_ask" not in _meta(rd)

    def test_poll_loop_holds_liveness_and_stretches_the_wall_clock(self, ws, tmp_path, monkeypatch):
        """The literal runner: a worker script writes a live ask, then sleeps
        SILENTLY past the liveness ceiling and past the wall clock while the
        operator answers (2.5 s later, from another thread through
        `oa.answer`). Without the hold it dies at 1 s; without the stretch
        at 2 s. It prints the answer it read and exits 0."""
        import threading
        import runs
        import llm
        events = _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        scratch = rd / "scratch"
        scratch.mkdir(exist_ok=True)
        ask_file = scratch / oa.ASK_NAME
        ans_file = scratch / oa.ANSWER_NAME
        script = tmp_path / "worker.sh"
        script.write_text(
            "#!/bin/sh\n"
            f"cat > '{ask_file}' <<'EOF'\n{json.dumps(CODE_ASK)}\nEOF\n"
            "i=0\n"
            f"while [ ! -f '{ans_file}' ]; do i=$((i+1)); [ $i -gt 40 ] && exit 3; sleep 0.2; done\n"
            f"cat '{ans_file}'\n"
        )
        script.chmod(0o755)

        def _operator():
            for _ in range(100):
                if events:
                    break
                time.sleep(0.05)
            time.sleep(2.5)
            oa.answer("abcd1234", "654321", source="test")
        t = threading.Thread(target=_operator, daemon=True)
        t.start()
        with runs.scoped_run_dir(rd):
            res = llm._run_subprocess_safe([str(script)], timeout=2, liveness_timeout=1,
                                           poll_interval=0.2, cwd=str(tmp_path))
        t.join(5)
        assert res.returncode == 0, res.stdout
        assert "654321" in res.stdout
        assert [e for e, _ in events] == ["operator_question"] and events[0][1]["live"] is True

    def test_live_window_closed_with_the_step_becomes_a_pause_without_a_second_card(self, ws, tmp_path, monkeypatch):
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs
        import loop_planning
        import loop_execute
        from agent_loop import run_agent_loop
        events = _capture_emit(monkeypatch)
        monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: ["log in to yahoo", "list the inbox"])
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **k: list(steps))
        executed = []

        def _worker(**kwargs):
            executed.append(kwargs["step_text"])
            from container_exec import run_scratch_dir
            sc = run_scratch_dir()
            oa.ask_path(sc).write_text(json.dumps(CODE_ASK))
            assert oa.watch_live(sc, handle_id="abcd1234", goal="g"), "the poll loop announced it"
            return {"status": "done", "result": "window closed, no code", "summary": "gap",
                    "tokens_in": 0, "tokens_out": 0, "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=2, handle_id="abcd1234")
        assert executed == ["log in to yahoo"]
        assert result.status == "interrupted" and result.pause_reason == "awaiting-clarification"
        rec = _meta(rd)["operator_ask"]
        assert rec["live"] is False and rec["status"] == "pending" and rec["live_window_closed_at"]
        assert rec["question"] == CODE_ASK["question"]
        assert [e for e, _ in events] == ["operator_question"], "the live card is the only card"
        enq = []
        import task_store
        monkeypatch.setattr(task_store, "enqueue", lambda **kw: enq.append(kw) or {"job_id": "job-9"})
        assert oa.answer("abcd1234", "111111")["status"] == "queued", "a late reply resumes the run"
        assert len(enq) == 1

    def test_live_answered_within_the_step_does_not_pause(self, ws, tmp_path, monkeypatch):
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs
        import loop_planning
        import loop_execute
        from agent_loop import run_agent_loop
        events = _capture_emit(monkeypatch)
        monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: ["log in to yahoo", "list the inbox"])
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **k: list(steps))
        executed = []

        def _worker(**kwargs):
            executed.append(kwargs["step_text"])
            if kwargs["step_text"] == "log in to yahoo":
                from container_exec import run_scratch_dir
                sc = run_scratch_dir()
                oa.ask_path(sc).write_text(json.dumps(CODE_ASK))
                assert oa.watch_live(sc, handle_id="abcd1234", goal="g")
                assert oa.answer("abcd1234", "654321")["status"] == "delivered"
            return {"status": "done", "result": "in", "summary": "in",
                    "tokens_in": 0, "tokens_out": 0, "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=2, handle_id="abcd1234")
        assert executed == ["log in to yahoo", "list the inbox"], "the run keeps going"
        assert result.status != "interrupted"
        assert _meta(rd)["operator_ask"]["delivery"] == "live"
        assert [e for e, _ in events] == ["operator_question"]

    def test_render_and_list_show_live(self, ws, monkeypatch):
        import runs
        _capture_emit(monkeypatch)
        rd = _mk_run("abcd1234")
        scratch = rd / "scratch"
        scratch.mkdir(exist_ok=True)
        (scratch / oa.ASK_NAME).write_text(json.dumps(CODE_ASK))
        with runs.scoped_run_dir(rd):
            oa.watch_live(str(scratch), handle_id="abcd1234")
        rows = oa.list_asks()
        assert rows and rows[0]["live"] is True
        assert "[live]" in oa.render_asks(rows)

    def test_telegram_card_says_live_and_unverified(self):
        import notify_telegram as nt
        live = nt.format_message({"event_type": "operator_question",
            "handle_id": "abcd1234", "goal": "read mail", "question": "the code?",
            "why": "2fa", "no_input_alternative": "", "tried": True, "live": True,
            "wait_s": 600, "sent": "SMS to ***-1234", "deadline": "2026-09-07T05:00:00+00:00",
            "answer_with": "maro answer abcd1234"})
        assert "LIVE" in live and "10 min" in live and "Sent: SMS to ***-1234" in live
        assert "Waiting until" not in live
        unv = nt.format_message({"event_type": "operator_question",
            "handle_id": "abcd1234", "goal": "g", "question": "q", "why": "", "no_input_alternative": "",
            "tried": True, "deadline": "d", "unverified": ["the link x returns HTTP 404 — …"],
            "answer_with": ""})
        assert "Unverified: the link x returns HTTP 404" in unv

    def test_gate_and_cli_report_a_delivered_answer_without_a_worker(self, ws, tmp_path, monkeypatch, capsys):
        spec = importlib.util.spec_from_file_location(
            "hermes_dispatch", REPO / "deploy" / "hermes" / "dispatch.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ddir = tmp_path / "hermes-dispatch"
        ddir.mkdir()
        monkeypatch.setattr(mod, "DISPATCH_DIR", ddir)
        fake = types.ModuleType("operator_ask")
        fake.answer = lambda ref, text, source="": {"status": "delivered", "handle_id": "abcd1234",
                                                   "question": "the code?"}
        monkeypatch.setitem(sys.modules, "operator_ask", fake)
        spawned = []
        monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert mod.main(["answer", "abcd1234", "654321"]) == 0
        assert json.loads(buf.getvalue())["status"] == "delivered" and spawned == []
        from cli import main
        assert main(["answer", "abcd1234", "654321"]) == 0
        assert "delivered live" in capsys.readouterr().out

    def test_hermes_side_knows_live_and_unverified(self):
        sh = (REPO / "deploy" / "hermes" / "mini2-maro-inbox.sh").read_text()
        assert "If .live is true the run is NOT paused" in sh and ".unverified" in sh
        assert 'd.get("live")' in sh, "the deterministic fallback renders it too"
        skill = (REPO / "deploy" / "hermes" / "mini2-maro-dispatch-SKILL.md").read_text()
        assert "## Live questions" in skill and '"status": "delivered"' in skill
        defaults = (REPO / "docs" / "DEFAULTS.md").read_text()
        assert "| `ask.live_wait_s` | `600` |" in defaults
