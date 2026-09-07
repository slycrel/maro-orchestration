"""Environment requests — Maro installs what a job needs, never with runtime
root (docs/ENV_REQUEST_DESIGN.md, decision ea9e311f).

The file contract, the policy (allowed / escalate / rejected + grants), the
Dockerfile as the reviewable artifact, the per-project layer store with an
injected builder, image resolution at docker-run time, the loop's re-run of
the requesting step, and the orchestrator escalation → `answer allow`
→ grant + build + resume.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import env_request as er  # noqa: E402
import operator_ask as oa  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_build(monkeypatch):
    """No docker in tests: the builder records what it was asked to build
    and answers ok unless told otherwise; `exists` says every built tag exists."""
    calls = []
    built = set()
    state = {"ok": True, "out": "Successfully built"}

    def _build(tag, dockerfile, context, timeout_s):
        calls.append({"tag": tag, "dockerfile": Path(dockerfile).read_text(), "timeout": timeout_s})
        if state["ok"]:
            built.add(tag)
        return state["ok"], state["out"]
    monkeypatch.setattr(er, "_BUILD", _build)
    monkeypatch.setattr(er, "_EXISTS", lambda tag: tag in built)
    return {"calls": calls, "built": built, "state": state}


REQ = {"need": "drive a headless browser for the Yahoo login",
       "apt": ["chromium"], "npm": ["playwright"],
       "tried": "which chromium / npx playwright: absent, no sudo"}


def _mk_run(handle_id: str, project: str = "yahoo-mail"):
    import runs
    rd = runs.create_run_dir(handle_id, prompt="read the yahoo inbox")
    with runs.scoped_run_dir(rd):
        runs.stamp_run_metadata({"project": project})
    return rd


def _meta(rd: Path) -> dict:
    return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))


def _capture_emit(monkeypatch):
    events = []
    import notify
    monkeypatch.setattr(notify, "emit", lambda et, payload, **kw: events.append((et, payload)) or True)
    return events


# ---------------------------------------------------------------------------
# The file contract
# ---------------------------------------------------------------------------

class TestFile:
    def test_absent_is_no_request(self, tmp_path):
        assert er.read_request(tmp_path / er.REQUEST_NAME) is None
        assert er.read_request(None) is None
        assert er.request_path(None) is None
        assert er.request_path("/s") == Path("/s") / er.REQUEST_NAME

    def test_reads_lists_and_aliases(self, tmp_path):
        p = tmp_path / er.REQUEST_NAME
        p.write_text(json.dumps({"why": "browser", "apt": "chromium", "pip": ["requests==2.32"],
                                 "no_install_alternative": "tried curl"}))
        r = er.read_request(p)
        assert r["need"] == "browser" and r["apt"] == ["chromium"] and r["pip"] == ["requests==2.32"]
        assert r["npm"] == [] and r["tried"] == "tried curl"

    @pytest.mark.parametrize("body,msg", [
        ("not json", "not JSON"),
        ("[1]", "one JSON object"),
        (json.dumps({"apt": ["x"]}), "no `need`"),
        (json.dumps({"need": "x"}), "names no packages"),
        (json.dumps({"need": "x", "pip": [f"p{i}" for i in range(21)]}), "more than 20"),
    ])
    def test_unusable_file_is_an_error_not_a_request(self, tmp_path, body, msg):
        p = tmp_path / er.REQUEST_NAME
        p.write_text(body)
        with pytest.raises(ValueError, match=msg):
            er.read_request(p)

    def test_archive_keeps_the_file(self, tmp_path):
        p = tmp_path / er.REQUEST_NAME
        p.write_text(json.dumps(REQ))
        moved = er.archive_request(p)
        assert moved and moved.exists() and not p.exists()
        assert re.match(r"env-request\.\d{8}T\d{6}Z\.requested\.json", moved.name)
        assert er.archive_request(p) is None

    def test_instructions_name_the_path_and_the_posture(self):
        t = er.instructions("/tmp/env-request.json")
        assert "/tmp/env-request.json" in t and "no sudo" in t and "Environment updated" in t
        assert "never claim to have installed" in t


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class TestPolicy:
    def test_ordinary_packages_are_allowed(self, ws):
        v = er.evaluate(REQ)
        assert v.allowed["apt"] == ["chromium"] and v.allowed["npm"] == ["playwright"]
        assert not v.escalate and not v.rejected and v.any_allowed
        assert v.allowed_specs == ["apt:chromium", "npm:playwright"]

    def test_deny_list_escalates_and_a_grant_overrides(self, ws):
        v = er.evaluate({"need": "x", "apt": ["sudo", "curl"]})
        assert v.allowed["apt"] == ["curl"]
        assert [(s, x) for s, x, _ in v.escalate] == [("apt", "sudo")]
        assert "deny list" in v.escalate[0][2]
        v2 = er.evaluate({"need": "x", "apt": ["sudo"]}, grants=["apt:sudo"])
        assert v2.allowed["apt"] == ["sudo"] and not v2.escalate

    def test_off_source_escalates(self, ws, monkeypatch):
        import config
        monkeypatch.setattr(config, "get", lambda k, d=None: ["apt"] if k == "env.install.sources" else d)
        v = er.evaluate({"need": "x", "pip": ["requests"], "apt": ["curl"]})
        assert v.allowed["apt"] == ["curl"] and [(s, x) for s, x, _ in v.escalate] == [("pip", "requests")]

    @pytest.mark.parametrize("src,spec", [
        ("apt", "curl; rm -rf /"), ("apt", "-y"), ("apt", "Chromium"),
        ("pip", "requests && echo"), ("pip", "$(x)"),
        ("npm", "pkg`id`"), ("npm", "../evil"),
    ])
    def test_shell_shapes_are_malformed_never_built_never_escalated(self, ws, src, spec):
        v = er.evaluate({"need": "x", src: [spec]})
        assert not v.any_allowed and not v.escalate
        assert v.rejected == [(src, spec, "malformed package name")]

    def test_versioned_specs_are_well_formed(self, ws):
        v = er.evaluate({"need": "x", "pip": ["requests[security]>=2.31", "numpy~=1.26"],
                         "npm": ["@playwright/test@1.47.0", "playwright@^1"]})
        assert not v.rejected and len(v.allowed["pip"]) == 2 and len(v.allowed["npm"]) == 2
        assert er._bare_name("pip", "requests[security]>=2.31") == "requests"
        assert er._bare_name("npm", "@playwright/test@1.47.0") == "@playwright/test"


# ---------------------------------------------------------------------------
# The Dockerfile — the reviewable artifact
# ---------------------------------------------------------------------------

class TestDockerfile:
    def test_root_only_in_run_lines_and_pip_pulls_python3_pip(self):
        df = er.render_dockerfile("maro-executor:2.1.210-r3", ["chromium"], ["requests==2.32"],
                                  ["playwright"], project="p", layer=2)
        assert df.startswith("# generated by maro env_request — project p, layer 2.")
        assert "FROM maro-executor:2.1.210-r3\nUSER root\n" in df
        assert "apt-get install -y --no-install-recommends chromium python3-pip &&" in df
        assert "pip install --no-cache-dir --break-system-packages requests==2.32" in df
        assert "npm install -g playwright" in df
        assert "sudo" not in df

    def test_specs_are_shell_quoted(self):
        df = er.render_dockerfile("b", [], ["requests[security]>=2.31"], ["@playwright/test@^1"])
        assert "'requests[security]>=2.31'" in df and "'@playwright/test@^1'" in df

    def test_empty_sources_render_no_run_line(self):
        df = er.render_dockerfile("b", ["curl"], [], [])
        assert df.count("\nRUN ") == 1 and "pip" not in df and "npm" not in df


# ---------------------------------------------------------------------------
# The per-project layer store
# ---------------------------------------------------------------------------

class TestLayers:
    def test_tag_keeps_the_base_revision_tail(self, ws):
        import container_exec as ce
        tag = er.image_tag("Yahoo Mail!", 1)
        assert tag == f"maro-executor:p-yahoo-mail-l1-{ce.CLAUDE_CLI_VERSION}-r{ce.IMAGE_REVISION}"
        assert ce._IMAGE_TAG_RE.match(tag), "verbs-baked detection must still read the revision"

    def test_build_advances_the_manifest_and_writes_the_ledger(self, ws, fake_build):
        v = er.evaluate(REQ)
        br = er.build_layer("yahoo-mail", v, reason="browser")
        assert br.ok and br.layer == 1 and br.added == ["apt:chromium", "npm:playwright"]
        d = er.layer_dir("yahoo-mail")
        m = json.loads((d / "manifest.json").read_text())
        assert m["image"] == br.image and m["apt"] == ["chromium"] and m["npm"] == ["playwright"]
        assert m["base"] == er._base_image() and m["layer"] == 1
        assert (d / "Dockerfile").read_text() == br.dockerfile
        assert (d / "build.log").read_text() == "Successfully built"
        rows = [json.loads(l) for l in (d / "layers.jsonl").read_text().splitlines()]
        assert rows[-1]["ok"] is True and rows[-1]["added"] == br.added and rows[-1]["reason"] == "browser"
        assert fake_build["calls"][0]["tag"] == br.image and fake_build["calls"][0]["timeout"] == 900.0
        # a second request accumulates: layer 2 carries both the old and the new
        br2 = er.build_layer("yahoo-mail", er.evaluate({"need": "y", "pip": ["requests"]}))
        assert br2.layer == 2 and br2.added == ["pip:requests"]
        assert "chromium" in br2.dockerfile and "requests" in br2.dockerfile
        assert er.status("yahoo-mail")["pip"] == ["requests"]

    def test_already_present_packages_are_cached_not_rebuilt(self, ws, fake_build):
        er.build_layer("p", er.evaluate(REQ))
        n = len(fake_build["calls"])
        br = er.build_layer("p", er.evaluate(REQ))
        assert br.ok and br.detail == "cached" and br.added == [] and len(fake_build["calls"]) == n

    def test_failed_build_keeps_the_previous_layer(self, ws, fake_build):
        first = er.build_layer("p", er.evaluate({"need": "x", "apt": ["curl"]}))
        fake_build["state"].update(ok=False, out="E: Unable to locate package nope\n" * 3)
        br = er.build_layer("p", er.evaluate({"need": "x", "apt": ["nope"]}))
        assert not br.ok and "Unable to locate package" in br.detail
        assert br.image == first.image and br.layer == first.layer
        m = er.load_manifest("p")
        assert m["image"] == first.image and m["apt"] == ["curl"], "manifest only advances on success"
        rows = [json.loads(l) for l in (er.layer_dir("p") / "layers.jsonl").read_text().splitlines()]
        assert rows[-1]["ok"] is False and "Unable to locate" in rows[-1]["detail"]

    def test_effective_image_follows_the_base_and_docker(self, ws, fake_build, monkeypatch):
        assert er.effective_image("p") is None
        br = er.build_layer("p", er.evaluate(REQ))
        assert er.effective_image("p") == br.image
        assert er.effective_image(None) is None
        monkeypatch.setattr(er, "_EXISTS", lambda tag: False)
        assert er.effective_image("p") is None, "a pruned image falls back to the base"
        monkeypatch.setattr(er, "_EXISTS", lambda tag: True)
        monkeypatch.setattr(er, "_base_image", lambda: "maro-executor:9.9.9-r9")
        assert er.effective_image("p") is None, "a new base invalidates the project's layer"
        import config
        monkeypatch.setattr(er, "_base_image", lambda: er.load_manifest("p")["base"])
        assert er.effective_image("p") == br.image
        monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "env.install.enabled" else d)
        assert er.effective_image("p") is None, "disabled → base image"

    def test_current_project_reads_the_run_metadata(self, ws):
        import runs
        assert er.current_project() is None
        rd = _mk_run("abcd1234", project="yahoo-mail")
        with runs.scoped_run_dir(rd):
            assert er.current_project() == "yahoo-mail"


# ---------------------------------------------------------------------------
# handle(): what the engine does with one request
# ---------------------------------------------------------------------------

class TestHandle:
    def test_host_lane_and_disabled_are_refusals_with_a_reason(self, ws, monkeypatch, fake_build):
        out = er.handle(REQ, project="p", container=False)
        assert out.kind == "rejected" and "host" in out.note and not fake_build["calls"]
        import config
        monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "env.install.enabled" else d)
        out = er.handle(REQ, project="p", container=True)
        assert out.kind == "rejected" and "disabled" in out.note

    def test_in_policy_builds_and_says_what_landed(self, ws, fake_build):
        out = er.handle(REQ, project="p", container=True)
        assert out.kind == "built" and out.build.ok
        assert out.note.startswith("Environment updated: installed apt:chromium, npm:playwright")
        assert out.build.image in out.note and "Do not request these again" in out.note

    def test_malformed_only_is_rejected_and_mixed_is_built_with_a_note(self, ws, fake_build):
        out = er.handle({"need": "x", "apt": ["bad;name"]}, project="p", container=True)
        assert out.kind == "rejected" and "malformed" in out.note and not fake_build["calls"]
        out = er.handle({"need": "x", "apt": ["curl", "bad;name"]}, project="p", container=True)
        assert out.kind == "built" and "also refused (malformed): apt:bad;name" in out.note

    def test_build_failure_hands_the_tail_back(self, ws, fake_build):
        fake_build["state"].update(ok=False, out="E: Unable to locate package chromiumm")
        out = er.handle(REQ, project="p", container=True)
        assert out.kind == "build_failed" and "Unable to locate package chromiumm" in out.note

    def test_out_of_policy_escalates_with_a_decision_line(self, ws, fake_build):
        out = er.handle({"need": "run a service", "apt": ["systemd", "curl"]},
                        project="p", container=True, handle_id="abcd1234")
        assert out.kind == "escalate" and not fake_build["calls"], "nothing builds before the decision"
        assert out.note.startswith("Decide: the run wants to install apt:systemd — ")
        assert "maro answer abcd1234 allow" in out.note and "maro answer abcd1234 deny" in out.note


# ---------------------------------------------------------------------------
# The loop: a worker that requests → the same step runs again on the new image
# ---------------------------------------------------------------------------

def _fake_claude(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(fake))


def _plan(monkeypatch, steps):
    import loop_planning
    monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: list(steps))
    monkeypatch.setattr(loop_planning, "_shape_steps", lambda s, **k: list(s))


def _seen_text(kwargs) -> str:
    return "\n".join(str(v) for v in kwargs.values() if isinstance(v, str))


class TestLoop:
    def test_request_rebuilds_and_reruns_the_step_with_the_note(self, ws, tmp_path, monkeypatch, fake_build):
        _fake_claude(tmp_path, monkeypatch)
        _plan(monkeypatch, ["log in to yahoo", "list the inbox"])
        import runs
        import loop_execute
        import container_exec as ce
        from agent_loop import run_agent_loop
        events = _capture_emit(monkeypatch)
        seen = []

        def _worker(**kwargs):
            seen.append((kwargs["step_text"], _seen_text(kwargs)))
            ce._last_venue.set("container:maro-t")  # the step ran in the container
            attempt = sum(1 for s, _ in seen if s == kwargs["step_text"])
            if kwargs["step_text"] == "log in to yahoo" and attempt == 1:
                p = er.request_path(ce.run_scratch_dir())
                p.write_text(json.dumps(REQ))
                return {"status": "done", "result": "no browser here; requested one",
                        "summary": "requested chromium", "tokens_in": 0, "tokens_out": 0,
                        "inject_steps": []}
            return {"status": "done", "result": "ok", "summary": "ok",
                    "tokens_in": 0, "tokens_out": 0, "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)

        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=3,
                                    handle_id="abcd1234", project="yahoo-mail")
        assert [s for s, _ in seen] == ["log in to yahoo", "log in to yahoo", "list the inbox"]
        assert "Environment updated: installed apt:chromium, npm:playwright" in seen[1][1], \
            "the retry opens with what landed"
        assert "Environment updated" not in seen[0][1] and "Environment updated" not in seen[2][1]
        assert result.status != "interrupted"
        assert fake_build["calls"] and fake_build["calls"][0]["tag"] == er.image_tag("yahoo-mail", 1)
        assert "chromium" in fake_build["calls"][0]["dockerfile"]
        scratch = rd / "scratch"
        assert not (scratch / er.REQUEST_NAME).exists(), "consumed"
        assert list(scratch.glob("env-request.*.requested.json")), "archived, never deleted"
        assert not [e for e, _ in events if e in ("escalation", "operator_question")]
        trace = (rd / "build" / "trace.jsonl").read_text()
        assert "step.env_request" in trace and "env.built" in trace

    def test_retry_cap_stops_a_request_loop(self, ws, tmp_path, monkeypatch, fake_build):
        _fake_claude(tmp_path, monkeypatch)
        _plan(monkeypatch, ["log in to yahoo"])
        import runs
        import loop_execute
        import container_exec as ce
        from agent_loop import run_agent_loop
        fake_build["state"].update(ok=False, out="E: nope")
        n = {"calls": 0}

        def _worker(**kwargs):
            n["calls"] += 1
            ce._last_venue.set("container:maro-t")
            er.request_path(ce.run_scratch_dir()).write_text(json.dumps(REQ))
            return {"status": "done", "result": "asked again", "summary": "x",
                    "tokens_in": 0, "tokens_out": 0, "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=2,
                           handle_id="abcd1234", project="yahoo-mail")
        assert n["calls"] == 1 + er.MAX_RETRIES_PER_STEP, "the step is re-run at most twice for env reasons"

    def test_out_of_policy_pauses_and_escalates_to_the_orchestrator(self, ws, tmp_path, monkeypatch, fake_build):
        _fake_claude(tmp_path, monkeypatch)
        _plan(monkeypatch, ["run the service", "list the inbox"])
        import runs
        import loop_execute
        import container_exec as ce
        from agent_loop import run_agent_loop
        events = _capture_emit(monkeypatch)
        executed = []

        def _worker(**kwargs):
            executed.append(kwargs["step_text"])
            ce._last_venue.set("container:maro-t")
            er.request_path(ce.run_scratch_dir()).write_text(json.dumps(
                {"need": "a service manager", "apt": ["systemd"], "tried": "nothing else works"}))
            return {"status": "done", "result": "requested", "summary": "x",
                    "tokens_in": 0, "tokens_out": 0, "inject_steps": []}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = _mk_run("abcd1234")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the yahoo inbox", dry_run=False, max_steps=2,
                                    handle_id="abcd1234", project="yahoo-mail")
        assert executed == ["run the service"], "the run pauses; nothing else runs"
        assert result.status == "interrupted" and result.pause_reason == "awaiting-clarification"
        assert not fake_build["calls"], "nothing builds before the orchestrator decides"
        meta = _meta(rd)
        rec = meta["operator_ask"]
        assert rec["kind"] == "env_request" and rec["status"] == "pending"
        assert rec["escalate"] == ["apt:systemd"] and rec["project"] == "yahoo-mail"
        assert rec["question"].startswith("Decide: the run wants to install apt:systemd")
        assert [e for e, _ in events] == ["escalation"], "the orchestrator's event, not the user's question"
        payload = events[0][1]
        assert payload["point"] == "env_request" and payload["audience"] == "orchestrator"
        assert payload["request"] == {"apt": ["systemd"], "pip": [], "npm": []}
        assert payload["answer_with"] == 'maro answer abcd1234 "<your answer>"'
        assert "deny list" in payload["reason"]
        # the ledger names it
        rows = oa.list_asks()
        assert rows[0]["kind"] == "env_request" and "[install]" in oa.render_asks(rows)
        # the Telegram card says who decides
        import notify_telegram
        card = notify_telegram.format_message(dict(payload, event_type="escalation"))
        assert card.startswith("\U0001f527 maro wants to install software — the orchestrator decides")

        # --- the orchestrator allows: grant + build + resume with the outcome
        res = oa.answer("abcd1234", "allow — a browser is ordinary tooling", source="hermes-ssh")
        assert res["status"] == "queued", res
        assert fake_build["calls"] and "systemd" in fake_build["calls"][0]["dockerfile"]
        assert er.load_manifest("yahoo-mail")["grants"] == ["apt:systemd"]
        assert er.load_manifest("yahoo-mail")["apt"] == ["systemd"]
        rec = _meta(rd)["operator_ask"]
        assert rec["status"] == "answered" and rec["decision"] == "allow"
        assert rec["outcome"].startswith("The orchestrator ALLOWED it: a browser is ordinary tooling. Environment updated")
        import task_store
        task = [t for t in task_store.list_tasks() if t["job_id"] == res["job_id"]][0]
        assert "== Orchestrator decision ==" in task["reason"]
        assert "Environment updated: installed apt:systemd" in task["reason"]
        assert "The operator answered" not in task["reason"]
        # the same package never escalates again for the project
        assert er.evaluate({"need": "x", "apt": ["systemd"]}, grants=er.load_manifest("yahoo-mail")["grants"]).allowed["apt"] == ["systemd"]

    def test_deny_resumes_without_building(self, ws, monkeypatch, fake_build):
        rd = _mk_run("abcd1234")
        import runs
        out = er.handle({"need": "x", "apt": ["sudo"], "tried": "y"}, project="yahoo-mail",
                        container=True, handle_id="abcd1234")
        with runs.scoped_run_dir(rd):
            er.pause_for_request(out, handle_id="abcd1234", goal="g", project="yahoo-mail", step="s")
        res = oa.answer("abcd1234", "deny: not for a mail check")
        assert res["status"] == "queued" and not fake_build["calls"]
        rec = _meta(rd)["operator_ask"]
        assert rec["decision"] == "deny" and rec["outcome"].startswith("The orchestrator DENIED installing apt:sudo: not for a mail check")
        assert er.load_manifest("yahoo-mail")["grants"] == []

    def test_unclear_answer_is_neither(self, ws):
        assert er.parse_answer("Allow") == ("allow", "")
        assert er.parse_answer("deny it's huge") == ("deny", "it's huge")
        assert er.parse_answer("maybe later") == ("", "maybe later")
        verb, text = er.apply_answer({"project": "p", "escalate": ["apt:x"], "request": {"apt": ["x"]}}, "maybe later")
        assert verb == "" and "neither allowed nor denied" in text


# ---------------------------------------------------------------------------
# The frame + the docker launch
# ---------------------------------------------------------------------------

class TestFrameAndLaunch:
    def test_frame_carries_the_paragraph_in_the_container_lane_only(self, ws, monkeypatch):
        import container_exec as ce
        import step_exec
        monkeypatch.setattr(ce, "container_mode", lambda: "off")
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: "/host/run/scratch")
        assert "## Installing what you need" not in step_exec.execute_system_for_lane(), \
            "host lane: the worker has its own installers"
        monkeypatch.setattr(ce, "container_mode", lambda: "on")
        monkeypatch.setattr(ce, "container_suppressed", lambda: False)
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: False)
        cont = step_exec.execute_system_for_lane()
        assert "## Installing what you need" in cont and er.CONTAINER_REQUEST_PATH in cont
        import config
        monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "env.install.enabled" else d)
        assert "## Installing what you need" not in step_exec.execute_system_for_lane(), "off → no paragraph"

    def test_docker_argv_carries_the_env_and_the_project_image(self, ws, tmp_path, monkeypatch, fake_build):
        import llm
        import runs
        import container_exec as ce
        captured = {}

        class _Proc:
            pid = 4244
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _Proc()
        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        monkeypatch.setattr(ce, "hosted_free_container_env", lambda: {})
        monkeypatch.setattr(ce, "build_mount_map", lambda *a, **k: [])
        monkeypatch.setattr(ce, "introspection_provision", lambda: None)
        monkeypatch.setattr(ce, "attachment_ro_mounts", lambda: [])
        monkeypatch.setattr(ce, "kill_container", lambda name: None)
        rd = _mk_run("abcd1234", project="yahoo-mail")
        scratch = rd / "scratch"
        scratch.mkdir(exist_ok=True)
        monkeypatch.setattr(ce, "run_scratch_dir", lambda: str(scratch))

        def _launch():
            with runs.scoped_run_dir(rd):
                llm._run_subprocess_safe(["/opt/bin/notclaude", "-p", "x"], timeout=5, cwd=str(tmp_path),
                                         container_name="maro-t", executor_step=True)
            return " ".join(captured["cmd"])
        joined = _launch()
        assert f"{er.REQUEST_ENV}={er.CONTAINER_REQUEST_PATH}" in joined, joined
        assert ce.container_image() in joined, "no layer yet → the base image"
        br = er.build_layer("yahoo-mail", er.evaluate(REQ))
        joined = _launch()
        assert br.image in joined and ce.container_image() not in joined.replace(br.image, ""), \
            "a built layer → the project image"


# ---------------------------------------------------------------------------
# Docs + registry pins
# ---------------------------------------------------------------------------

def test_defaults_registry_names_every_key():
    text = (REPO / "docs" / "DEFAULTS.md").read_text(encoding="utf-8")
    for key in ("env.install.enabled", "env.install.sources", "env.install.deny", "env.install.build_timeout_s"):
        assert f"`{key}`" in text, key


def test_hermes_side_knows_the_decision_is_the_orchestrators():
    inbox = (REPO / "deploy" / "hermes" / "mini2-maro-inbox.sh").read_text(encoding="utf-8")
    skill = (REPO / "deploy" / "hermes" / "mini2-maro-dispatch-SKILL.md").read_text(encoding="utf-8")
    assert "env_request" in inbox and 'answer <handle_id> allow' in inbox
    assert "## Decide an install request" in skill and "answer <handle_id> deny <why>" in skill
    assert "${" not in inbox.split("prompt=")[1].split("hermes -z")[0].replace("${event}", "").replace("${event_file}", "").replace("${inbox}", ""), \
        "no unbound-variable prose in the brain prompt (the 2026-09-07 inbox death)"
