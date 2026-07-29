"""Dispatch-envelope box-side intake (docs/DISPATCH_ENVELOPE.md).

The load-bearing contract: user_ask is THE goal; operator fields ride a
dedicated context channel and are structurally excluded from lesson
extraction; prose dispatches keep working unchanged; a declared-but-broken
envelope fails loud (machine-to-machine — silence would run JSON soup as
a goal).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dispatch_envelope import (
    ENVELOPE_VERSION,
    DispatchEnvelope,
    EnvelopeError,
    operator_block,
    parse_dispatch_payload,
    store_attachments,
)


def _payload(**over):
    data = {"envelope": ENVELOPE_VERSION, "user_ask": "summarize the gist"}
    data.update(over)
    return json.dumps(data)


class TestParse:
    def test_prose_returns_none(self):
        assert parse_dispatch_payload("just do the thing") is None

    def test_non_json_braces_returns_none(self):
        assert parse_dispatch_payload("{not json at all") is None

    def test_json_without_version_key_returns_none(self):
        # A goal that happens to BE a JSON object is still prose to us —
        # only the version key opts into the contract.
        assert parse_dispatch_payload('{"user_ask": "hi"}') is None

    def test_json_array_returns_none(self):
        assert parse_dispatch_payload('["a", "b"]') is None

    def test_non_string_returns_none(self):
        assert parse_dispatch_payload(None) is None
        assert parse_dispatch_payload(42) is None

    def test_minimal_envelope_parses(self):
        env = parse_dispatch_payload(_payload())
        assert isinstance(env, DispatchEnvelope)
        assert env.user_ask == "summarize the gist"
        assert env.operator_context == ""
        assert env.operator_constraints == ()
        assert env.attached_artifacts == ()

    def test_full_envelope_parses(self):
        env = parse_dispatch_payload(_payload(
            operator_context="  User pasted this at 9am.  ",
            operator_constraints=["No public posts.", "  ", "Stay offline."],
            attached_artifacts=[{"name": "ref.md", "content": "body",
                                 "source": "https://example.com/x"}],
        ))
        assert env.operator_context == "User pasted this at 9am."
        assert env.operator_constraints == ("No public posts.", "Stay offline.")
        assert env.attached_artifacts[0]["name"] == "ref.md"

    def test_declared_without_user_ask_fails_loud(self):
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(json.dumps({"envelope": ENVELOPE_VERSION}))

    def test_empty_user_ask_fails_loud(self):
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(_payload(user_ask="   "))

    def test_non_string_operator_context_fails_loud(self):
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(_payload(operator_context={"a": 1}))

    def test_non_string_list_constraints_fail_loud(self):
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(_payload(operator_constraints=[1, 2]))

    def test_artifact_missing_name_fails_loud(self):
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(
                _payload(attached_artifacts=[{"content": "x"}]))

    def test_artifact_missing_content_fails_loud(self):
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(
                _payload(attached_artifacts=[{"name": "a.md"}]))


class TestOperatorBlock:
    def test_empty_envelope_renders_nothing(self):
        env = DispatchEnvelope(user_ask="do it")
        assert operator_block(env) == ""

    def test_block_is_labeled_advisory_and_carries_fields(self):
        env = DispatchEnvelope(
            user_ask="do it",
            operator_context="Came in via Telegram.",
            operator_constraints=("No public posts.",),
        )
        block = operator_block(
            env, [{"name": "ref.md", "path": "/tmp/ref.md",
                   "source": "https://example.com"}])
        assert "advisory" in block
        assert "NOT part of the user's ask" in block
        assert "Came in via Telegram." in block
        assert "- No public posts." in block
        assert "ref.md → /tmp/ref.md (source: https://example.com)" in block
        assert block.endswith("== End operator context ==")

    def test_user_ask_never_appears_in_block(self):
        env = DispatchEnvelope(user_ask="UNIQUE-ASK-MARKER",
                               operator_context="framing")
        assert "UNIQUE-ASK-MARKER" not in operator_block(env)


class TestStoreAttachments:
    def test_writes_content_and_provenance_sidecar(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "notes.md", "content": "hello",
             "source": "https://example.com/notes"}]))
        stored = store_attachments(env, key="job-1")
        assert len(stored) == 1
        path = Path(stored[0]["path"])
        assert path.read_text() == "hello"
        side = json.loads(
            path.with_name(path.name + ".provenance.json").read_text())
        assert side["source"] == "https://example.com/notes"
        assert side["bytes"] == 5
        assert side["dispatch_key"] == "job-1"

    def test_traversal_names_are_flattened_to_basenames(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "../../evil.md", "content": "x"},
            {"name": "..\\..\\win.md", "content": "y"}]))
        stored = store_attachments(env, key="job-2")
        root = (tmp_path / "output" / "dispatch-artifacts" / "job-2").resolve()
        for rec in stored:
            p = Path(rec["path"]).resolve()
            assert p.parent == root, f"{p} escaped the artifact dir"

    def test_no_artifacts_writes_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = DispatchEnvelope(user_ask="do it")
        assert store_attachments(env, key="job-3") == []
        assert not (tmp_path / "output" / "dispatch-artifacts").exists()


def _task(reason, job_id="job-env-1"):
    return {"job_id": job_id, "source": "user_goal", "reason": reason,
            "origin": {}}


class TestHandleTaskIntake:
    """The routing seam: message == user_ask exactly; operator text reaches
    handle() only via the operator_context kwarg. This IS the extraction
    exclusion — reflect_and_record receives the goal, never the context
    channel."""

    def _recorder(self, monkeypatch, calls):
        import handle as handle_mod
        def _fake_handle(message, **kw):
            calls.append((message, kw))
            return handle_mod.HandleResult(
                handle_id="h1", lane="agenda", lane_confidence=1.0,
                classification_reason="test", message=message,
                status="done", result="")
        monkeypatch.setattr(handle_mod, "handle", _fake_handle)

    def test_typed_envelope_splits_goal_from_operator_context(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from handle_queue import handle_task
        calls = []
        self._recorder(monkeypatch, calls)
        handle_task(_task(_payload(
            operator_context="User pasted this in Telegram.",
            operator_constraints=["Do not post anything publicly."],
        )), dry_run=True)
        assert len(calls) == 1
        message, kw = calls[0]
        assert message == "summarize the gist"
        assert "Telegram" not in message
        op = kw.get("operator_context") or ""
        assert "Telegram" in op
        assert "Do not post anything publicly." in op
        assert "advisory" in op
        assert kw["origin"].get("dispatch_envelope") == ENVELOPE_VERSION

    def test_prose_reason_passes_through_unchanged(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from handle_queue import handle_task
        calls = []
        self._recorder(monkeypatch, calls)
        handle_task(_task("just do the thing"), dry_run=True)
        message, kw = calls[0]
        assert message == "just do the thing"
        assert kw.get("operator_context") is None
        assert "dispatch_envelope" not in kw["origin"]

    def test_malformed_declared_envelope_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from handle_queue import handle_task
        calls = []
        self._recorder(monkeypatch, calls)
        with pytest.raises(EnvelopeError):
            handle_task(_task(json.dumps({"envelope": ENVELOPE_VERSION})),
                        dry_run=True)
        assert not calls, "malformed envelope must never reach handle()"

    def test_envelope_artifacts_land_on_disk_and_in_block(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from handle_queue import handle_task
        calls = []
        self._recorder(monkeypatch, calls)
        handle_task(_task(_payload(attached_artifacts=[
            {"name": "ref.md", "content": "reference body",
             "source": "https://example.com/r"}]), job_id="job-art"),
            dry_run=True)
        message, kw = calls[0]
        op = kw.get("operator_context") or ""
        assert "ref.md" in op
        stored = list((tmp_path / "output" / "dispatch-artifacts"
                       / "job-art").glob("ref.md"))
        assert stored and stored[0].read_text() == "reference body"


class TestExtractionSeamPin:
    def test_lesson_extraction_has_no_operator_channel(self):
        # Structural pin: the exclusion holds because reflect_and_record's
        # interface cannot receive operator text — no param carries context.
        # If someone adds one, this fails and the exclusion needs re-proving.
        import inspect
        from memory import reflect_and_record
        params = inspect.signature(reflect_and_record).parameters
        offenders = [p for p in params if "operator" in p or "context" in p]
        assert not offenders, (
            f"reflect_and_record grew a context-shaped param {offenders} — "
            "the dispatch-envelope extraction exclusion rests on this "
            "interface staying goal+outcome only (docs/DISPATCH_ENVELOPE.md)")
