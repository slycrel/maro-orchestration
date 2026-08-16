"""Tests for the operator attachment lane (`handle --attach`).

The lane exists because a goal could not carry a file: a study arrived as a
screenshot, so the operator hand-transcribed it into the goal text, which
turned the transcription into an unattributable claim two runs then had to
spend steps distrusting. These pin the two properties that make an
attachment better than that — it lands where a containerized worker can
actually read it, and it is labeled as operator-supplied rather than
retrieved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dispatch_envelope import (EnvelopeError, DispatchEnvelope,
                               land_operator_attachments,
                               operator_attachment_block,
                               store_attachments,
                               store_operator_attachments)


@pytest.fixture
def ws(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    return tmp_path


class TestStoreOperatorAttachments:

    def test_binary_content_survives_byte_for_byte(self, ws, tmp_path):
        # The case that opened the lane: a PNG, not text. store_attachments
        # writes str and would corrupt this.
        png = tmp_path / "figure.png"
        raw = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
        png.write_bytes(raw)
        [rec] = store_operator_attachments([str(png)], key="k1")
        assert Path(rec["path"]).read_bytes() == raw
        assert rec["bytes"] == len(raw)

    def test_provenance_sidecar_records_the_real_bytes_and_the_lane(
            self, ws, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hello")
        [rec] = store_operator_attachments([str(f)], key="k1")
        side = json.loads(
            Path(rec["path"] + ".provenance.json").read_text())
        assert side["lane"] == "operator"
        assert side["source"] == f"operator:{f}"
        assert side["sha256"] == rec["sha256"]

    def test_a_missing_attachment_refuses_rather_than_running_without_it(
            self, ws, tmp_path):
        # Silently dropping it would run the goal as if nothing was attached.
        with pytest.raises(EnvelopeError, match="not found"):
            store_operator_attachments([str(tmp_path / "nope.png")], key="k1")

    def test_a_directory_is_not_an_attachment(self, ws, tmp_path):
        with pytest.raises(EnvelopeError, match="not found or not a file"):
            store_operator_attachments([str(tmp_path)], key="k1")

    def test_oversize_is_refused_not_truncated(self, ws, tmp_path, monkeypatch):
        import dispatch_envelope as de
        monkeypatch.setattr(de, "_MAX_ATTACHMENT_BYTES", 10)
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * 50)
        with pytest.raises(EnvelopeError, match="over the"):
            de.store_operator_attachments([str(f)], key="k1")

    def test_same_name_different_content_does_not_overwrite(self, ws, tmp_path):
        a, b = tmp_path / "a" / "f.png", tmp_path / "b" / "f.png"
        a.parent.mkdir(); b.parent.mkdir()
        a.write_bytes(b"AAA"); b.write_bytes(b"BBB")
        recs = store_operator_attachments([str(a), str(b)], key="k1")
        assert len({r["path"] for r in recs}) == 2
        assert {Path(r["path"]).read_bytes() for r in recs} == {b"AAA", b"BBB"}

    def test_a_traversal_shaped_filename_lands_as_a_basename(self, ws, tmp_path):
        odd = tmp_path / "..evil.png"
        odd.write_bytes(b"x")
        [rec] = store_operator_attachments([str(odd)], key="../../escape")
        assert ".." not in Path(rec["path"]).name
        assert "ws" in rec["path"]


class TestLanding:

    def test_lands_into_the_run_tree_where_a_container_can_read_it(
            self, ws, tmp_path):
        # The workspace output dir is hard-excluded from the container mount
        # map; the run dir is the cwd and IS mounted. This copy is the whole
        # reason the lane works.
        f = tmp_path / "figure.png"
        f.write_bytes(b"\x89PNG data")
        store_operator_attachments([str(f)], key="k1")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        n = land_operator_attachments(run_dir, "k1")
        landed = run_dir / "fetch-raw" / "operator" / "figure.png"
        assert n >= 1
        assert landed.read_bytes() == b"\x89PNG data"

    def test_landing_is_idempotent(self, ws, tmp_path):
        f = tmp_path / "f.txt"; f.write_text("v1")
        store_operator_attachments([str(f)], key="k1")
        run_dir = tmp_path / "run"; run_dir.mkdir()
        land_operator_attachments(run_dir, "k1")
        (run_dir / "fetch-raw" / "operator" / "f.txt").write_text("edited")
        land_operator_attachments(run_dir, "k1")
        assert (run_dir / "fetch-raw" / "operator" / "f.txt").read_text() == "edited"

    def test_an_unknown_key_is_a_no_op_not_a_crash(self, ws, tmp_path):
        run_dir = tmp_path / "run"; run_dir.mkdir()
        assert land_operator_attachments(run_dir, "never-stored") == 0


class TestOperatorAttachmentBlock:

    def _block(self, ws, tmp_path):
        f = tmp_path / "figure.png"
        f.write_bytes(b"abc")
        return operator_attachment_block(
            store_operator_attachments([str(f)], key="k1"))

    def test_names_the_run_relative_path_not_the_workspace_one(
            self, ws, tmp_path):
        # An absolute output/ path is unreadable from inside the container,
        # so naming it would send the worker somewhere it cannot go.
        block = self._block(ws, tmp_path)
        assert "fetch-raw/operator/figure.png" in block
        assert "output/operator-attachments" not in block

    def test_labels_the_evidence_as_operator_supplied_not_retrieved(
            self, ws, tmp_path):
        block = self._block(ws, tmp_path)
        assert "NOT retrieved by this run" in block
        assert "never as a source you retrieved" in block

    def test_empty_when_nothing_is_attached(self):
        assert operator_attachment_block([]) == ""


class TestTrustLanesStaySeparate:

    def test_the_dispatcher_lane_is_still_text_only(self, ws):
        # Widening store_attachments to bytes would hand every dispatcher a
        # binary write primitive. The operator lane exists so it does not
        # have to be widened.
        env = DispatchEnvelope(user_ask="x", attached_artifacts=(
            {"name": "a.txt", "content": "plain", "source": "s"},))
        [rec] = store_attachments(env, key="j1")
        assert Path(rec["path"]).read_text() == "plain"

    def test_the_two_lanes_write_to_different_areas(self, ws, tmp_path):
        f = tmp_path / "f.txt"; f.write_text("op")
        [op] = store_operator_attachments([str(f)], key="k1")
        env = DispatchEnvelope(user_ask="x", attached_artifacts=(
            {"name": "f.txt", "content": "disp", "source": "s"},))
        [dp] = store_attachments(env, key="k1")
        assert "operator-attachments" in op["path"]
        assert "dispatch-artifacts" in dp["path"]
        assert op["path"] != dp["path"]


class TestTheProductionPath:
    """Trace the literal wiring, not just the units. The landing helper
    passing its own tests proves nothing about whether open_run actually
    calls it — which is the only thing that puts a file where the worker
    looks."""

    def test_open_run_lands_attachments_named_in_origin(self, ws, tmp_path):
        import runs
        f = tmp_path / "figure.png"
        f.write_bytes(b"\x89PNG real bytes")
        store_operator_attachments([str(f)], key="k-prod")

        rd = runs.open_run("attachrun1", prompt="a goal with a file",
                           lane="now",
                           origin={"operator_attachments": "k-prod"})
        landed = Path(rd) / "fetch-raw" / "operator" / "figure.png"
        assert landed.exists(), "open_run did not land the attachment"
        assert landed.read_bytes() == b"\x89PNG real bytes"

    def test_a_run_without_attachments_is_unaffected(self, ws, tmp_path):
        import runs
        rd = runs.open_run("attachrun2", prompt="no files here", lane="now")
        assert not (Path(rd) / "fetch-raw" / "operator").exists()
