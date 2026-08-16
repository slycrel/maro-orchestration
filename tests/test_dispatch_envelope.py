"""Dispatch-envelope box-side intake (docs/DISPATCH_ENVELOPE.md).

The load-bearing contract: user_ask is THE goal; operator fields ride a
dedicated context channel and are structurally excluded from lesson
extraction; prose dispatches keep working unchanged; a declared-but-broken
envelope fails loud (machine-to-machine — silence would run JSON soup as
a goal).
"""
import hashlib
import json
import re
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

    def test_truncated_declared_envelope_fails_loud(self):
        # A payload that names the contract version but doesn't parse is a
        # mangled typed dispatch, not prose — running it as a goal would
        # execute the truncation. Fail loud (2026-07-29 review find).
        truncated = _payload()[:-10]
        assert ENVELOPE_VERSION in truncated
        with pytest.raises(EnvelopeError):
            parse_dispatch_payload(truncated)

    def test_non_json_without_version_mention_is_still_prose(self):
        # The loud path is scoped to payloads that NAME the contract; ordinary
        # brace-leading prose stays on the fallback lane.
        assert parse_dispatch_payload('{"half": "an object"') is None

    def test_json_without_version_key_returns_none(self):
        # A goal that happens to BE a JSON object is still prose to us —
        # only the version key opts into the contract.
        assert parse_dispatch_payload('{"user_ask": "hi"}') is None

    def test_json_array_returns_none(self):
        assert parse_dispatch_payload('["a", "b"]') is None

    def test_a_different_envelope_version_is_not_parsed_as_this_one(self):
        # The version key is a contract, not a feature flag. A v2 dispatcher
        # sending v2 semantics must NOT be read with v1 field meanings — that
        # is a silent misparse, the worst shape of forward-compat failure.
        # Mutation sweep 2026-08-16: `!= ENVELOPE_VERSION` weakened to a
        # presence check survived the whole suite.
        assert parse_dispatch_payload(_payload(envelope="maro-dispatch/v2")) is None
        assert parse_dispatch_payload(_payload(envelope=None)) is None
        assert parse_dispatch_payload(_payload(envelope=7)) is None

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

    def test_a_non_object_artifact_fails_as_an_envelope_error(self):
        # Not just "it fails" — it must fail as EnvelopeError, because that
        # is the type handle_queue catches to refuse the job cleanly before
        # queueing. Without the element type check the loop does
        # `"str".get(...)` and an AttributeError escapes that handler as an
        # unhandled crash (mutation sweep 2026-08-16).
        for bad in (["oops"], [None], [["a"]], [42]):
            with pytest.raises(EnvelopeError):
                parse_dispatch_payload(_payload(attached_artifacts=bad))

    def test_the_goal_is_stripped_before_it_becomes_the_goal(self):
        # user_ask goes on to be the run's goal string, which is compared,
        # fingerprinted and logged; leading/trailing whitespace from a
        # dispatcher's here-doc must not ride along into it.
        env = parse_dispatch_payload(_payload(user_ask="  do the thing\n\n"))
        assert env.user_ask == "do the thing"


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

    def test_hostile_characters_are_scrubbed_out_of_the_filename(
            self, monkeypatch, tmp_path):
        # The traversal test above passes with EITHER guard in _safe_name
        # (basename or whitelist) — each alone keeps the write inside the
        # dir, so neither is pinned by it. This pins the whitelist's OTHER
        # job: an artifact name is a label, and a dispatcher must not be able
        # to choose the bytes of a filename we then hand to the filesystem
        # and print into a prompt (mutation sweep 2026-08-16).
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "a b;rm -rf.md", "content": "x"},
            {"name": "we\nird\t$(id).md", "content": "y"}]))
        stored = store_attachments(env, key="job-chars")
        for rec in stored:
            got = Path(rec["path"]).name
            assert re.fullmatch(r"[A-Za-z0-9._-]+", got), f"unscrubbed: {got!r}"

    def test_a_very_long_artifact_name_still_lands_with_its_sidecar(
            self, monkeypatch, tmp_path):
        # The cap is not cosmetic: the sidecar adds ".provenance.json" (16
        # chars) to the same name, so an uncapped 245-char name raises
        # ENAMETOOLONG and store_attachments fails the whole dispatch by
        # contract (mutation sweep 2026-08-16 — dropping `[:120]` survived).
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "n" * 245 + ".md", "content": "x"}]))
        stored = store_attachments(env, key="job-long")
        path = Path(stored[0]["path"])
        assert path.read_text() == "x"
        assert path.with_name(path.name + ".provenance.json").exists()

    def test_two_artifacts_with_the_same_name_both_survive(
            self, monkeypatch, tmp_path):
        # A dispatch that promised two reference files must deliver two. With
        # the dedup gone the second overwrites the first, both `stored`
        # records point at one path, and the operator block tells the model
        # a file is there that holds someone else's content — a silent
        # substitution, not an error (mutation sweep 2026-08-16).
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "notes.md", "content": "FIRST"},
            {"name": "notes.md", "content": "SECOND"},
            {"name": "notes/../notes.md", "content": "THIRD"}]))
        stored = store_attachments(env, key="job-dup")
        paths = [Path(r["path"]) for r in stored]
        assert len({str(p) for p in paths}) == 3, "artifacts collided on disk"
        assert [p.read_text() for p in paths] == ["FIRST", "SECOND", "THIRD"]

    def test_the_sidecar_sha_is_of_the_bytes_that_landed(
            self, monkeypatch, tmp_path):
        # The sidecar is the provenance record; its whole job is letting a
        # later reader check that the file on disk is what the dispatcher
        # sent. A blank/constant sha is a provenance record that certifies
        # nothing, and the existing sidecar test only asserted source/bytes
        # (mutation sweep 2026-08-16).
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "notes.md", "content": "hello"}]))
        path = Path(store_attachments(env, key="job-sha")[0]["path"])
        side = json.loads(
            path.with_name(path.name + ".provenance.json").read_text())
        assert side["sha256"] == hashlib.sha256(
            path.read_bytes()).hexdigest()

    def test_no_artifacts_writes_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = DispatchEnvelope(user_ask="do it")
        assert store_attachments(env, key="job-3") == []
        assert not (tmp_path / "output" / "dispatch-artifacts").exists()


class TestLandInRunDir:
    """Artifacts-travel rider: the dispatch-side copy is the record; the
    run-dir copy under fetch-raw/dispatch/ is the one that travels (the
    container mount map hard-excludes the workspace output tree)."""

    def _stored(self, monkeypatch, tmp_path, key="job-9"):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        env = parse_dispatch_payload(_payload(attached_artifacts=[
            {"name": "notes.md", "content": "hello",
             "source": "https://example.com/notes"}]))
        store_attachments(env, key=key)

    def test_copies_artifacts_and_sidecars(self, monkeypatch, tmp_path):
        from dispatch_envelope import land_in_run_dir
        self._stored(monkeypatch, tmp_path)
        rd = tmp_path / "runs" / "r1"
        rd.mkdir(parents=True)
        assert land_in_run_dir(rd, "job-9") == 2   # artifact + sidecar
        landed = rd / "fetch-raw" / "dispatch"
        assert (landed / "notes.md").read_text() == "hello"
        side = json.loads(
            (landed / "notes.md.provenance.json").read_text())
        assert side["source"] == "https://example.com/notes"

    def test_idempotent_second_call_copies_nothing(self, monkeypatch,
                                                   tmp_path):
        from dispatch_envelope import land_in_run_dir
        self._stored(monkeypatch, tmp_path)
        rd = tmp_path / "runs" / "r1"
        rd.mkdir(parents=True)
        assert land_in_run_dir(rd, "job-9") == 2
        assert land_in_run_dir(rd, "job-9") == 0

    def test_missing_dispatch_dir_is_quiet_zero(self, monkeypatch, tmp_path):
        from dispatch_envelope import land_in_run_dir
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        rd = tmp_path / "runs" / "r1"
        rd.mkdir(parents=True)
        assert land_in_run_dir(rd, "never-stored") == 0
        assert not (rd / "fetch-raw").exists()

    def test_create_run_dir_lands_envelope_artifacts(self, monkeypatch,
                                                     tmp_path):
        # The live seam: origin carries dispatch_envelope + job_id (no run
        # dir existed at dispatch time), create_run_dir does the landing.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        self._stored(monkeypatch, tmp_path, key="job-live")
        from dispatch_envelope import ENVELOPE_VERSION as _V
        import runs as runs_mod
        rd = runs_mod.create_run_dir(
            "handle-env-1", prompt="summarize the gist",
            extra_metadata={"origin": {"dispatch_envelope": _V,
                                       "job_id": "job-live"}})
        assert (rd / "fetch-raw" / "dispatch" / "notes.md").read_text() \
            == "hello"

    def test_create_run_dir_without_envelope_origin_lands_nothing(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        self._stored(monkeypatch, tmp_path, key="job-x")
        import runs as runs_mod
        rd = runs_mod.create_run_dir(
            "handle-env-2", prompt="a plain run",
            extra_metadata={"origin": {"job_id": "job-x"}})
        assert not (rd / "fetch-raw").exists()


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


class TestEnqueueGoalBoundary:
    def test_malformed_envelope_refused_before_queueing(
            self, monkeypatch, tmp_path):
        # Same contract as dispatch.py cmd_enqueue: a broken typed payload
        # bounces to its sender at enqueue time, never sits queued.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import task_store
        queued = []
        monkeypatch.setattr(task_store, "enqueue",
                            lambda **kw: queued.append(kw) or {"job_id": "j1"})
        from handle_queue import enqueue_goal
        with pytest.raises(EnvelopeError):
            enqueue_goal(json.dumps({"envelope": ENVELOPE_VERSION}))
        assert not queued, "malformed envelope must never be queued"

    def test_valid_envelope_and_prose_both_enqueue(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import task_store
        queued = []
        monkeypatch.setattr(task_store, "enqueue",
                            lambda **kw: queued.append(kw) or {"job_id": "j1"})
        from handle_queue import enqueue_goal
        enqueue_goal(_payload())          # typed, well-formed
        enqueue_goal("just do the thing")  # prose fallback lane
        assert len(queued) == 2
        # Raw payload rides the queue untouched — intake parses at drain time.
        assert queued[0]["reason"] == _payload()


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
