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

    def test_names_the_mounted_absolute_path(self, ws, tmp_path):
        # This test previously asserted the RUN-RELATIVE path, encoding the
        # bug rather than the requirement: for a project-scoped run the
        # container cwd is the project dir, so `fetch-raw/operator/...`
        # resolved to nothing and a live run's five filesystem searches all
        # came back empty. The reachable path is the stored one, which
        # llm.py bind-mounts read-only.
        block = self._block(ws, tmp_path)
        assert "operator-attachments" in block
        assert str(ws) in block or "/operator-attachments/" in block
        assert "fetch-raw/operator/figure.png" not in block

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


class TestTheMountIsWhatMakesItReachable:
    """The landing and the block are both inert without the mount. A
    project-scoped run's container cwd is the project dir; the run dir is
    not mounted, which is why the first live attempt failed."""

    def test_the_attachment_area_is_inside_a_mounted_root(self, ws, tmp_path,
                                                          monkeypatch):
        import container_exec as ce
        from config import output_dir, workspace_root
        f = tmp_path / "figure.png"
        f.write_bytes(b"x")
        [rec] = store_operator_attachments([str(f)], key="k1")

        project = workspace_root() / "projects" / "p"
        project.mkdir(parents=True)
        area = str(output_dir() / "operator-attachments")
        mounts = ce.build_mount_map(str(project), ro_mounts=[area])

        assert any(m == "ro" and rec["path"].startswith(h) for h, m in mounts), \
            "the stored attachment is not inside any mounted root"

    def test_the_run_dir_is_NOT_mounted_for_a_project_run(self, ws, tmp_path):
        # Pins the fact that caused the bug, so a future change that starts
        # relying on run-dir reachability fails loudly here first.
        import container_exec as ce
        from config import workspace_root
        project = workspace_root() / "projects" / "p2"
        project.mkdir(parents=True)
        run_dir = str(workspace_root() / "runs" / "abc123")
        mounts = ce.build_mount_map(str(project))
        assert not any(run_dir.startswith(h) for h, _ in mounts)


class TestAttachmentMountSeam:
    """Pins the WIRING, not just build_mount_map. The live failure was not
    a bad mount map — it was that nothing put the attachment area into it.

    Rewritten 2026-08-16: the first version asserted the seam offered the
    whole `operator-attachments` AREA, which is the cross-run leak four
    review lenses found. Those assertions were pins on the bug, so they had
    to change with it — the seam is now scoped to the running run's key.
    """

    def _open(self, key):
        import runs
        return runs.open_run(f"seam-{key}", prompt="p", lane="now",
                             origin={"operator_attachments": key})

    def test_nothing_is_offered_before_anything_is_stored(self, ws):
        import container_exec as ce
        assert ce.attachment_ro_mounts() == []

    def test_this_runs_key_is_offered(self, ws, tmp_path):
        import container_exec as ce
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        store_operator_attachments([str(f)], key="k1")
        self._open("k1")
        mounts = ce.attachment_ro_mounts()
        assert any(m.endswith("operator-attachments/k1") for m in mounts)

    def test_a_stored_attachment_is_inside_what_the_seam_offers(
            self, ws, tmp_path):
        import container_exec as ce
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        [rec] = store_operator_attachments([str(f)], key="k1")
        self._open("k1")
        assert any(rec["path"].startswith(m) for m in ce.attachment_ro_mounts())

    def test_the_seam_feeds_a_mount_map_that_covers_the_file(
            self, ws, tmp_path):
        import container_exec as ce
        from config import workspace_root
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        [rec] = store_operator_attachments([str(f)], key="k1")
        self._open("k1")
        project = workspace_root() / "projects" / "p3"
        project.mkdir(parents=True)
        mounts = ce.build_mount_map(str(project),
                                    ro_mounts=ce.attachment_ro_mounts())
        assert any(m == "ro" and rec["path"].startswith(h) for h, m in mounts)


class TestMountIsScopedToThisRun:
    """Four review lenses, independently, 2026-08-16: the first version
    mounted the WHOLE operator-attachments/dispatch-artifacts areas, so
    every containerized run could read every attachment ever stored — from
    any goal, any operator, any day. The prompt block calls these files
    "supplied by the person who set THIS goal"; area-wide reach contradicted
    the contract shipped alongside it."""

    def _run_with_key(self, key):
        import runs
        return runs.open_run(f"scoped-{key}", prompt="p", lane="now",
                             origin={"operator_attachments": key})

    def test_another_runs_attachments_are_not_mounted(self, ws, tmp_path,
                                                      monkeypatch):
        import container_exec as ce
        mine = tmp_path / "mine.png"; mine.write_bytes(b"MINE")
        theirs = tmp_path / "theirs.png"; theirs.write_bytes(b"THEIRS")
        [rec_mine] = store_operator_attachments([str(mine)], key="key-mine")
        [rec_theirs] = store_operator_attachments([str(theirs)], key="key-theirs")

        self._run_with_key("key-mine")
        mounts = ce.attachment_ro_mounts()
        assert any(rec_mine["path"].startswith(m) for m in mounts)
        assert not any(rec_theirs["path"].startswith(m) for m in mounts), (
            "another run's attachment is inside this run's mount set")

    def test_a_run_with_no_attachments_mounts_nothing(self, ws, tmp_path):
        import container_exec as ce, runs
        f = tmp_path / "x.png"; f.write_bytes(b"x")
        store_operator_attachments([str(f)], key="somebody-elses")
        runs.open_run("no-attach", prompt="p", lane="now")
        assert ce.attachment_ro_mounts() == [], (
            "absence of a key must not fall back to the whole area")

    def test_no_run_context_mounts_nothing(self, ws, tmp_path):
        import container_exec as ce
        assert ce.attachment_ro_mounts() == []


class TestAttachmentInputHardening:

    def test_a_symlinked_attachment_is_refused_and_names_its_target(
            self, ws, tmp_path):
        # A symlink hides WHAT is attached: the name says one thing, the
        # bytes come from wherever it points, and they land in an area a
        # container mounts and a model reads.
        secret = tmp_path / "host-secret.txt"
        secret.write_text("private")
        link = tmp_path / "innocent.png"
        link.symlink_to(secret)
        with pytest.raises(EnvelopeError, match="symlink"):
            store_operator_attachments([str(link)], key="k1")

    def test_a_dot_dot_key_cannot_address_the_parent(self, ws, tmp_path):
        from dispatch_envelope import _safe_key
        assert _safe_key("..") == "dispatch"
        assert _safe_key(".") == "dispatch"
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        [rec] = store_operator_attachments([str(f)], key="..")
        assert "/operator-attachments/dispatch/" in rec["path"]
        assert "/operator-attachments/../" not in rec["path"]


class TestLandingKeepsBothOnCollision:
    """Minimalist lens, 2026-08-16: landing skipped any same-named target,
    so two distinct files silently became one — while the storage sibling
    disambiguates in exactly that case."""

    def test_identical_bytes_are_an_idempotent_no_op(self, ws, tmp_path):
        f = tmp_path / "f.txt"; f.write_text("same")
        store_operator_attachments([str(f)], key="k1")
        rd = tmp_path / "run"; rd.mkdir()
        assert land_operator_attachments(rd, "k1") >= 1
        assert land_operator_attachments(rd, "k1") == 0
        assert (rd / "fetch-raw" / "operator" / "f.txt").read_text() == "same"

    def test_differing_bytes_keep_both(self, ws, tmp_path):
        rd = tmp_path / "run"; rd.mkdir()
        f = tmp_path / "f.txt"; f.write_text("first")
        store_operator_attachments([str(f)], key="k1")
        land_operator_attachments(rd, "k1")
        # A second, different file arriving under the same name.
        dest = rd / "fetch-raw" / "operator"
        f.write_text("second")
        store_operator_attachments([str(f)], key="k2")
        land_operator_attachments(rd, "k2")
        bodies = {p.read_text() for p in dest.glob("f*.txt")}
        assert bodies == {"first", "second"}, bodies


class TestIOFailuresSurfaceAsTheLanesOwnRefusal:
    """Recovered QA finding (2026-08-17, from the ReportFindings payload the
    harness never read): the CLI catches EnvelopeError only, so a bare
    OSError from a full disk or unreadable file gave the operator a raw
    traceback instead of the refusal this lane promises."""

    def test_an_unreadable_file_raises_envelope_error(self, ws, tmp_path,
                                                      monkeypatch):
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        monkeypatch.setattr(Path, "read_bytes",
                            lambda self: (_ for _ in ()).throw(OSError("EACCES")))
        with pytest.raises(EnvelopeError, match="unreadable"):
            store_operator_attachments([str(f)], key="k1")

    def test_a_failed_store_write_raises_envelope_error(self, ws, tmp_path,
                                                        monkeypatch):
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        real = Path.write_bytes
        def boom(self, data):
            if "operator-attachments" in str(self):
                raise OSError("ENOSPC")
            return real(self, data)
        monkeypatch.setattr(Path, "write_bytes", boom)
        with pytest.raises(EnvelopeError, match="could not store"):
            store_operator_attachments([str(f)], key="k1")
