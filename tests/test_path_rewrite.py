"""Tests for path_rewrite — embedded-path rewriting when data changes host.

The dangerous half of this module is what it REFUSES to touch, so most of
these pin refusals: a hostile root that would rewrite the filesystem, a
git object whose name is its content hash, a sqlite page, a sibling
directory that merely shares a prefix with a real root.
"""

from __future__ import annotations

import json
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import path_rewrite
from path_rewrite import (BadRoot, build_map, rewrite_file, rewrite_tree,
                          skip_reason, validate_root)


def _map(*pairs):
    """A RewriteMap straight from (src, dst) pairs, ordering applied."""
    return build_map({f"r{i}": s for i, (s, _) in enumerate(pairs)},
                     {f"r{i}": d for i, (_, d) in enumerate(pairs)},
                     roles=tuple(f"r{i}" for i in range(len(pairs))))


class TestValidateRoot:

    def test_normalizes_trailing_and_doubled_slashes(self):
        assert validate_root("/home/clawd/.maro/") == "/home/clawd/.maro"
        assert validate_root("/home//clawd//.maro") == "/home/clawd/.maro"

    @pytest.mark.parametrize("bad", ["/", "/usr", "/etc", "/home", "/Users",
                                     "/tmp", "/var", "/private/tmp"])
    def test_rejects_system_roots(self, bad):
        # A crafted provenance claiming the source workspace was "/" would
        # otherwise rewrite every absolute path in the archive.
        with pytest.raises(BadRoot):
            validate_root(bad)

    def test_rejects_system_root_with_trailing_slash(self):
        # Normalization runs BEFORE the denylist, or "/usr/" ducks it.
        # Assert the REASON, not just the refusal: "/usr/" is rejected by
        # the shallowness rule either way, so a refusal alone cannot tell
        # a working denylist from a bypassed one.
        with pytest.raises(BadRoot, match="system directory"):
            validate_root("/usr/")

    def test_rejects_relative(self):
        with pytest.raises(BadRoot):
            validate_root("home/clawd/.maro")

    def test_rejects_too_shallow(self):
        with pytest.raises(BadRoot):
            validate_root("/clawdverylongname")

    def test_rejects_non_string_and_empty(self):
        for bad in (None, 42, [], "", "   "):
            with pytest.raises(BadRoot):
                validate_root(bad)

    def test_rejects_nul(self):
        with pytest.raises(BadRoot):
            validate_root("/home/clawd\x00/.maro")


class TestBuildMap:

    def test_same_root_is_a_no_op_not_a_fault(self):
        m = build_map({"workspace_root": "/home/a/ws"},
                      {"workspace_root": "/home/a/ws"})
        assert not m
        assert m.rejected == ()

    def test_missing_source_role_is_silent_missing_dest_is_recorded(self):
        m = build_map({"maro_user_dir": "/home/a/.maro"},
                      {"workspace_root": "/home/b/ws"})
        assert not m.pairs
        assert [r[0] for r in m.rejected] == ["maro_user_dir"]

    @pytest.mark.parametrize("bad,reason", [
        ("/", "resolves to filesystem root"),
        ("/usr", "system directory"),
        ("/nope", "too shallow"),
    ])
    def test_hostile_source_root_is_rejected_with_reason(self, bad, reason):
        m = build_map({"workspace_root": bad},
                      {"workspace_root": "/home/b/ws"})
        assert not m
        assert m.rejected[0][0] == "workspace_root"
        assert reason in m.rejected[0][2]

    def test_orders_longest_source_first(self):
        m = build_map(
            {"workspace_root": "/home/a/.maro/workspace",
             "maro_user_dir": "/home/a/.maro"},
            {"workspace_root": "/home/b/.maro/workspace",
             "maro_user_dir": "/home/b/.maro"})
        assert [s for s, _ in m.pairs] == ["/home/a/.maro/workspace",
                                           "/home/a/.maro"]

    def test_duplicate_source_roots_collapse(self):
        m = build_map({"workspace_root": "/home/a/ws", "repo_root": "/home/a/ws"},
                      {"workspace_root": "/home/b/ws", "repo_root": "/home/b/x"})
        assert len(m.pairs) == 1


class TestSubstitute:

    def test_rewrites_nested_root_before_its_parent(self):
        m = build_map(
            {"workspace_root": "/home/a/.maro/workspace",
             "maro_user_dir": "/home/a/.maro"},
            {"workspace_root": "/srv/ws", "maro_user_dir": "/home/b/.maro"})
        out, n = m.substitute(b"see /home/a/.maro/workspace/memory and "
                             b"/home/a/.maro/config.yml")
        assert out == b"see /srv/ws/memory and /home/b/.maro/config.yml"
        assert n == 2

    def test_does_not_fire_on_a_prefix_sibling_directory(self):
        # /home/a/.maro must not claim /home/a/.maro-acceptance-probe.
        m = _map(("/home/a/.maro", "/home/b/.maro"))
        out, n = m.substitute(b"/home/a/.maro-acceptance-probe/host-secret.txt")
        assert n == 0
        assert out == b"/home/a/.maro-acceptance-probe/host-secret.txt"

    def test_fires_at_end_of_token_and_in_prose(self):
        m = _map(("/home/a/ws", "/home/b/ws"))
        for text in (b"/home/a/ws", b"'/home/a/ws'", b"at /home/a/ws.\n",
                     b"/home/a/ws/runs/x", b'"/home/a/ws"'):
            _, n = m.substitute(text)
            assert n == 1, text

    def test_single_pass_never_rewrites_its_own_output(self):
        # B→C must not consume the A→B substitution's result. Sequential
        # per-pair passes would turn /a/x into /c/x here.
        m = build_map({"workspace_root": "/home/a/x", "repo_root": "/home/b/x"},
                      {"workspace_root": "/home/b/x", "repo_root": "/home/c/x"})
        out, n = m.substitute(b"/home/a/x")
        assert out == b"/home/b/x"
        assert n == 1

    def test_empty_map_is_a_no_op(self):
        m = build_map({}, {})
        assert m.substitute(b"/home/a/ws") == (b"/home/a/ws", 0)


class TestSkipReason:

    def test_skips_git_internals_at_any_depth(self, tmp_path):
        p = tmp_path / "obj"
        p.write_text("x")
        assert skip_reason("projects/repo/.git/objects/ab/cdef", p) \
            == "vcs-internal"
        assert skip_reason(".git/config", p) == "vcs-internal"

    def test_skips_database_and_binary_suffixes(self, tmp_path):
        p = tmp_path / "f"
        p.write_text("x")
        for rel in ("memory/correspondence.db", "x.db-wal", "a/b.sqlite3",
                    "img.PNG", "bundle.tar.gz", "m.pyc"):
            assert skip_reason(rel, p) == "binary-suffix", rel

    def test_skips_symlinks_without_following(self, tmp_path):
        target = tmp_path / "real.txt"
        target.write_text("/home/a/ws\n")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert skip_reason("link.txt", link) == "symlink"

    def test_skips_empty_and_missing(self, tmp_path):
        empty = tmp_path / "e.txt"
        empty.write_text("")
        assert skip_reason("e.txt", empty) == "empty"
        assert skip_reason("gone.txt", tmp_path / "gone.txt") == "missing"

    def test_allows_ordinary_text(self, tmp_path):
        p = tmp_path / "lesson.md"
        p.write_text("/home/a/ws\n")
        assert skip_reason("memory/lesson.md", p) == ""


class TestRewriteFile:

    def test_rewrites_and_preserves_mode_and_mtime(self, tmp_path):
        p = tmp_path / "f.jsonl"
        p.write_text('{"path": "/home/a/ws/runs/1"}\n')
        os.chmod(p, 0o600)
        old = (p.stat().st_mode, p.stat().st_mtime)
        time.sleep(0.01)
        status, n = rewrite_file(p, _map(("/home/a/ws", "/srv/w")))
        assert (status, n) == ("rewritten", 1)
        assert p.read_text() == '{"path": "/srv/w/runs/1"}\n'
        assert p.stat().st_mode == old[0]
        assert p.stat().st_mtime == pytest.approx(old[1], abs=0.001)

    def test_leaves_no_temp_file_behind(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("/home/a/ws\n")
        rewrite_file(p, _map(("/home/a/ws", "/srv/w")))
        assert [q.name for q in tmp_path.iterdir()] == ["f.txt"]

    def test_binary_content_is_skipped_even_with_a_text_suffix(self, tmp_path):
        # The NUL sniff is the catch-all behind the suffix list: a sqlite
        # db named .txt still has NUL in its header.
        p = tmp_path / "sneaky.txt"
        p.write_bytes(b"SQLite format 3\x00/home/a/ws/x")
        assert rewrite_file(p, _map(("/home/a/ws", "/srv/w"))) == ("binary", 0)
        assert b"/home/a/ws/x" in p.read_bytes()

    def test_oversize_is_skipped_not_truncated(self, tmp_path):
        p = tmp_path / "big.txt"
        p.write_text("/home/a/ws\n" * 100)
        before = p.read_bytes()
        assert rewrite_file(p, _map(("/home/a/ws", "/srv/w")),
                            max_bytes=10) == ("oversize", 0)
        assert p.read_bytes() == before

    def test_a_failed_swap_leaves_the_original_intact(self, tmp_path,
                                                      monkeypatch):
        # The rewrite writes a temp file and swaps it in. Writing over the
        # file directly would look identical in every other test here and
        # truncate imported data if the process died mid-write.
        p = tmp_path / "f.txt"
        p.write_text("/home/a/ws/x\n")
        monkeypatch.setattr(os, "replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError))
        status, n = rewrite_file(p, _map(("/home/a/ws", "/srv/w")))
        assert (status, n) == ("unreadable", 0)
        assert p.read_text() == "/home/a/ws/x\n"
        assert [q.name for q in tmp_path.iterdir()] == ["f.txt"]

    def test_unchanged_when_nothing_matches(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("nothing to see\n")
        assert rewrite_file(p, _map(("/home/a/ws", "/srv/w"))) \
            == ("unchanged", 0)


class TestRewriteTree:

    def test_touches_only_the_named_files(self, tmp_path):
        # The merge-import property: a file already in the workspace is
        # not in the extracted list and must survive untouched.
        (tmp_path / "mine.txt").write_text("/home/a/ws/keep\n")
        (tmp_path / "theirs.txt").write_text("/home/a/ws/change\n")
        rep = rewrite_tree(tmp_path, ["theirs.txt"],
                           _map(("/home/a/ws", "/srv/w")))
        assert rep.files_rewritten == 1
        assert (tmp_path / "mine.txt").read_text() == "/home/a/ws/keep\n"
        assert (tmp_path / "theirs.txt").read_text() == "/srv/w/change\n"

    def test_refuses_a_name_that_escapes_the_root(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("/home/a/ws\n")
        root = tmp_path / "ws"
        root.mkdir()
        rep = rewrite_tree(root, ["../outside.txt"],
                           _map(("/home/a/ws", "/srv/w")))
        assert rep.skipped.get("outside-root") == 1
        assert outside.read_text() == "/home/a/ws\n"

    def test_report_counts_every_skip_class(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "obj").write_text("/home/a/ws\n")
        (tmp_path / "db.db").write_text("/home/a/ws\n")
        (tmp_path / "ok.md").write_text("/home/a/ws x2 /home/a/ws\n")
        rep = rewrite_tree(tmp_path, [".git/obj", "db.db", "ok.md"],
                           _map(("/home/a/ws", "/srv/w")))
        assert rep.files_rewritten == 1
        assert rep.replacements == 2
        assert rep.skipped == {"vcs-internal": 1, "binary-suffix": 1}
        assert rep.files == [{"path": "ok.md", "replacements": 2}]
        assert (tmp_path / ".git" / "obj").read_text() == "/home/a/ws\n"

    def test_empty_map_does_nothing_at_all(self, tmp_path):
        (tmp_path / "f.txt").write_text("/home/a/ws\n")
        rep = rewrite_tree(tmp_path, ["f.txt"], build_map({}, {}))
        assert rep.files_scanned == 0
        assert (tmp_path / "f.txt").read_text() == "/home/a/ws\n"

    def test_record_carries_the_mapping_and_rejections(self, tmp_path):
        m = build_map({"workspace_root": "/home/a/ws", "repo_root": "/"},
                      {"workspace_root": str(tmp_path), "repo_root": "/srv/r"})
        rec = rewrite_tree(tmp_path, [], m).as_record()
        assert rec["mapping"] == [{"from": "/home/a/ws", "to": str(tmp_path)}]
        assert rec["rejected_roots"][0]["role"] == "repo_root"


# ---------------------------------------------------------------------------
# End to end, through the archive lane it was built for
# ---------------------------------------------------------------------------

class TestArchiveRoundTrip:

    @pytest.fixture
    def exported(self, monkeypatch, tmp_path):
        """Export a workspace whose files cite their own absolute path."""
        from maro_export import export_workspace
        src = tmp_path / "src-ws"
        (src / "memory").mkdir(parents=True)
        monkeypatch.setenv("MARO_WORKSPACE", str(src))
        (src / "memory" / "lessons.jsonl").write_text(
            json.dumps({"lesson": f"read {src}/runs/1/artifact"}) + "\n")
        (src / "playbook.md").write_text(f"# Playbook\nlives at {src}\n")
        # A git object and a database: both cite the path, neither may move.
        objdir = src / "projects" / "repo" / ".git" / "objects" / "ab"
        objdir.mkdir(parents=True)
        (objdir / "cdef").write_text(f"blob {src}/x")
        (src / "memory" / "corr.db").write_bytes(
            b"SQLite format 3\x00" + str(src).encode() + b"/page")
        archive = export_workspace(output_path=tmp_path / "a.tar.gz")
        return src, archive

    def _dest(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest-ws"
        dest.mkdir()
        monkeypatch.setenv("MARO_WORKSPACE", str(dest))
        return dest

    def test_import_rewrites_paths_and_records_them(self, exported,
                                                    monkeypatch, tmp_path):
        from maro_export import import_workspace
        src, archive = exported
        dest = self._dest(monkeypatch, tmp_path)
        import_workspace(archive)

        assert str(src) not in (dest / "playbook.md").read_text()
        assert str(dest) in (dest / "playbook.md").read_text()
        assert str(dest) in (dest / "memory" / "lessons.jsonl").read_text()

        rec_files = list((dest / ".import-meta").glob("*/path-rewrite.json"))
        assert len(rec_files) == 1
        rec = json.loads(rec_files[0].read_text())
        assert rec["mapping"] == [{"from": str(src), "to": str(dest)}]
        assert rec["files_rewritten"] == 2
        assert sorted(f["path"] for f in rec["files"]) == [
            "memory/lessons.jsonl", "playbook.md"]

    def test_git_objects_and_databases_survive_byte_identical(
            self, exported, monkeypatch, tmp_path):
        from maro_export import import_workspace
        src, archive = exported
        dest = self._dest(monkeypatch, tmp_path)
        import_workspace(archive)
        obj = dest / "projects" / "repo" / ".git" / "objects" / "ab" / "cdef"
        assert obj.read_text() == f"blob {src}/x"
        assert str(src).encode() in (dest / "memory" / "corr.db").read_bytes()

    def test_custody_records_the_transform(self, exported, monkeypatch,
                                           tmp_path):
        from maro_export import import_workspace
        src, archive = exported
        dest = self._dest(monkeypatch, tmp_path)
        import_workspace(archive)
        prov = json.loads(next(
            (dest / ".import-meta").glob("*/provenance.json")).read_text())
        ev = prov["custody"][-1]
        assert ev["event"] == "import"
        assert ev["transformed"] is True
        assert ev["path_rewrite"]["files_rewritten"] == 2

    def test_no_rewrite_paths_reproduces_the_archive_exactly(
            self, exported, monkeypatch, tmp_path):
        from maro_export import import_workspace
        src, archive = exported
        dest = self._dest(monkeypatch, tmp_path)
        import_workspace(archive, rewrite_paths=False)
        assert (dest / "playbook.md").read_text() == \
            f"# Playbook\nlives at {src}\n"
        assert not list((dest / ".import-meta").glob("*/path-rewrite.json"))
        prov = json.loads(next(
            (dest / ".import-meta").glob("*/provenance.json")).read_text())
        assert prov["custody"][-1]["transformed"] is False

    def test_shape_digest_still_verifies_after_a_rewrite(self, exported,
                                                         monkeypatch, tmp_path,
                                                         capsys):
        # The digest covers archive member names+sizes, so rewriting the
        # extracted bytes must not make an honest archive look tampered.
        from maro_export import import_workspace
        src, archive = exported
        self._dest(monkeypatch, tmp_path)
        import_workspace(archive)
        err = capsys.readouterr().err
        assert "workspace shape digest: OK" in err
        assert "MISMATCH" not in err

    def test_reimport_on_the_source_machine_changes_nothing(
            self, exported, monkeypatch, tmp_path, capsys):
        from maro_export import import_workspace
        src, archive = exported
        monkeypatch.setenv("MARO_WORKSPACE", str(src))
        import_workspace(archive)
        assert (src / "playbook.md").read_text() == \
            f"# Playbook\nlives at {src}\n"
        assert "nothing to rewrite" in capsys.readouterr().err

    def test_merge_import_leaves_existing_files_alone(self, exported,
                                                      monkeypatch, tmp_path):
        from maro_export import import_workspace
        src, archive = exported
        dest = self._dest(monkeypatch, tmp_path)
        (dest / "mine.md").write_text(f"my own note about {src}\n")
        import_workspace(archive)
        assert (dest / "mine.md").read_text() == f"my own note about {src}\n"

    def test_export_records_the_repo_root(self, exported):
        src, archive = exported
        with tarfile.open(archive) as tar:
            prov = json.loads(
                tar.extractfile("meta/provenance.json").read().decode())
        assert prov["source"]["repo_root"] == str(
            Path(path_rewrite.__file__).resolve().parents[1])


# --- review round 1 (2026-08-16, sonnet x6) ---------------------------------
# Six lenses, four confirmed defects. Each fix gets the test that would
# have caught it; the class each one belongs to is named, because the
# next reader's question is "could this have come back somewhere else".

class TestPostCommitFailureIsNotReportedAsNoOp:
    """QA + Minimalist, HIGH consensus: os.utime shared os.replace's try
    block, so a metadata failure AFTER the atomic commit returned
    ("unreadable", 0) for a file whose bytes had already changed — and
    the custody `transformed` stamp is computed from that count."""

    def test_utime_failure_after_the_swap_still_reports_rewritten(
            self, tmp_path, monkeypatch):
        p = tmp_path / "f.txt"
        p.write_text("/home/a/ws/x\n")
        monkeypatch.setattr(os, "utime",
                            lambda *a, **k: (_ for _ in ()).throw(OSError))
        status, n = rewrite_file(p, _map(("/home/a/ws", "/srv/w")))
        assert (status, n) == ("rewritten", 1)
        assert p.read_text() == "/srv/w/x\n"

    def test_the_report_counts_it_so_transformed_cannot_lie(
            self, tmp_path, monkeypatch):
        (tmp_path / "f.txt").write_text("/home/a/ws/x\n")
        monkeypatch.setattr(os, "utime",
                            lambda *a, **k: (_ for _ in ()).throw(OSError))
        rep = rewrite_tree(tmp_path, ["f.txt"], _map(("/home/a/ws", "/srv/w")))
        assert rep.files_rewritten == 1
        assert rep.files == [{"path": "f.txt", "replacements": 1}]


class TestLeftBoundary:
    """Architect + Security, independent: the match guarded only its right
    edge, so a root fired as a SUFFIX of a longer path — the same
    confidently-wrong-path class the right-edge guard exists to stop."""

    @pytest.mark.parametrize("text", [
        b"backup at /mnt/backup/home/a/ws/old",     # a mirror, not the ws
        b"see notes/home/a/ws/todo",                # a relative path
        b"http://example.com/home/a/ws/x",          # a URL
        b"prefix-/home/a/ws",                       # a longer token
    ])
    def test_a_root_that_is_only_a_suffix_does_not_fire(self, text):
        _, n = _map(("/home/a/ws", "/srv/w")).substitute(text)
        assert n == 0

    @pytest.mark.parametrize("text", [
        b"/home/a/ws", b" /home/a/ws", b"'/home/a/ws'", b'"/home/a/ws"',
        b"at /home/a/ws.\n", b"(/home/a/ws)", b"root=/home/a/ws",
        b'{"p": "/home/a/ws/x"}',
    ])
    def test_real_occurrences_still_fire(self, text):
        _, n = _map(("/home/a/ws", "/srv/w")).substitute(text)
        assert n == 1, text


class TestSourceRootDepthUnderSharedDirectories:
    """Architect + Security, HIGH consensus: the denylist was exact-match,
    so /usr/lib, /var/log, /home/<user> and /Users/<user> all validated —
    generic fragments that turn the rewrite into mass corruption."""

    @pytest.mark.parametrize("bad", ["/home/clawd", "/Users/jeremy", "/usr/lib",
                                     "/var/log", "/tmp/foo", "/opt/app",
                                     "/root/x", "/srv/ws"])
    def test_one_level_under_a_shared_directory_is_refused_as_a_source(
            self, bad):
        with pytest.raises(BadRoot, match="shared directory"):
            validate_root(bad)

    @pytest.mark.parametrize("ok", ["/home/clawd/.maro",
                                    "/home/clawd/.maro/workspace",
                                    "/home/clawd/claude/maro-orchestration",
                                    "/opt/maro/workspace"])
    def test_a_real_install_root_still_validates(self, ok):
        assert validate_root(ok) == ok

    def test_the_destination_side_is_not_held_to_it(self):
        # Our own computed path. Refusing MARO_WORKSPACE=/srv/ws would
        # disable the rewrite on a legitimately-configured machine to
        # defend against an input we generated ourselves.
        assert validate_root("/srv/ws", strict=False) == "/srv/ws"
        m = build_map({"workspace_root": "/home/a/.maro/workspace"},
                      {"workspace_root": "/srv/ws"})
        assert m.pairs == (("/home/a/.maro/workspace", "/srv/ws"),)

    def test_a_hostile_root_is_dropped_with_a_reason_not_applied(self):
        m = build_map({"workspace_root": "/usr/lib"},
                      {"workspace_root": "/home/b/.maro/workspace"})
        assert not m
        assert "shared directory" in m.rejected[0][2]
        # And nothing is rewritten through it.
        assert m.substitute(b'File "/usr/lib/python3.12/x.py"')[1] == 0

    def test_relative_segments_are_refused(self):
        with pytest.raises(BadRoot, match="relative segment"):
            validate_root("/opt/../etc/passwd")


class TestBinarySniffCoversTheWholeFile:
    """Skeptic, HIGH: the NUL sniff read only the first 8 KiB, so a
    NUL-free-header binary got a path spliced into its binary tail —
    while the docstring claimed it caught every unknown-extension binary."""

    def test_nul_beyond_the_sniff_window_still_skips(self, tmp_path):
        p = tmp_path / "model.ckpt2"          # extension not in the denylist
        p.write_bytes(b"A" * 9000 + b"/home/a/ws/x" + b"\x00" * 10)
        before = p.read_bytes()
        assert rewrite_file(p, _map(("/home/a/ws", "/srv/w"))) == ("binary", 0)
        assert p.read_bytes() == before


class TestFailureIsAttributable:
    """QA, MEDIUM: skips were bare counts, so an operator asking "why does
    this file still cite the old machine" could not find which file
    failed. Screens stay counts; unexpected I/O failures get names."""

    def test_an_unreadable_file_is_named_in_the_record(self, tmp_path,
                                                       monkeypatch):
        (tmp_path / "f.txt").write_text("/home/a/ws/x\n")
        monkeypatch.setattr(os, "replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError))
        rep = rewrite_tree(tmp_path, ["f.txt"], _map(("/home/a/ws", "/srv/w")))
        assert rep.failed == ["f.txt"]
        assert rep.as_record()["failed"] == ["f.txt"]
        assert "FAILED to rewrite" in rep.summary()

    def test_deliberate_screens_stay_counts_without_paths(self, tmp_path):
        (tmp_path / "x.db").write_text("/home/a/ws\n")
        rep = rewrite_tree(tmp_path, ["x.db"], _map(("/home/a/ws", "/srv/w")))
        assert rep.skipped == {"binary-suffix": 1}
        assert rep.failed == []

    def test_a_leftover_temp_file_is_never_rewritten(self, tmp_path):
        # Killed mid-swap: retention says never delete it, so the screens
        # must recognize it instead of treating it as ordinary text.
        p = tmp_path / "f.txt.maro-rewrite.tmp"
        p.write_text("/home/a/ws/x\n")
        rep = rewrite_tree(tmp_path, [p.name], _map(("/home/a/ws", "/srv/w")))
        assert rep.skipped == {"rewrite-leftover": 1}
        assert p.read_text() == "/home/a/ws/x\n"


class TestEscapeSequencesAreLeftBoundaries:
    """Found by the first LIVE box→Mac import, 2026-08-16 — not by the
    review, and not by any synthetic fixture. Inside a JSON transcript a
    path usually follows an escape, so the byte before the root is the
    `n` of `\\n`, which the plain lookbehind read as an identifier
    character. 5,543 occurrences of the primary root survived the first
    import because of it."""

    @pytest.mark.parametrize("prefix", [rb"\n", rb"\t", rb"\r", rb"\n\n"])
    def test_a_root_after_an_escape_still_rewrites(self, prefix):
        m = _map(("/home/a/ws", "/srv/w"))
        out, n = m.substitute(b"notes.md" + prefix + b"/home/a/ws/projects/x")
        assert n == 1, prefix
        assert out.endswith(b"/srv/w/projects/x")

    def test_the_real_shape_from_a_captured_transcript(self):
        m = _map(("/home/clawd/.maro/workspace", "/Users/j/box-copy/workspace"))
        raw = (rb'{"output": "the-unix-command/artifacts/tr-examples.md\n\n'
               rb'/home/clawd/.maro/workspace/projects/summarize-x"}')
        out, n = m.substitute(raw)
        assert n == 1
        assert b"/Users/j/box-copy/workspace/projects/summarize-x" in out

    def test_a_letter_that_is_not_an_escape_still_blocks(self):
        # The exception is the escape, not the letter: `n` on its own must
        # still block, or the suffix-of-a-longer-path bug comes right back.
        m = _map(("/home/a/ws", "/srv/w"))
        assert m.substitute(b"green/home/a/ws/x")[1] == 0
        assert m.substitute(b"/mnt/backup/home/a/ws/old")[1] == 0


class TestBackspaceEscapeIsAlsoABoundary:
    """Skeptic + Architect, 2026-08-16: the escape fix covered \\n \\t \\r
    \\f \\v but not \\b, a real JSON string escape — the same undercount
    the fix exists to close, one escape over."""

    @pytest.mark.parametrize("esc", [rb"\b", rb"\f", rb"\v"])
    def test_every_listed_escape_is_a_left_boundary(self, esc):
        m = _map(("/home/a/ws", "/srv/w"))
        out, n = m.substitute(b"notes.md" + esc + b"/home/a/ws/x")
        assert n == 1, esc
        assert out.endswith(b"/srv/w/x")
