"""The step-result file-citation check (loop_execute.cited_py_not_found).

Extracted from the loop 2026-08-02 after it produced 6 and 7 false
"hallucinated file" flags in single probe runs. The flag is not cosmetic:
in run 887316fe it seeded an adversarial-review round that contested
correct claims and drove a quality-gate ESCALATE which closure had to
overrule at 0.95.
"""
import pytest

from loop_execute import cited_py_not_found


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A real project dir with one .py in a subdirectory, wired through
    orch_items.project_dir the way the loop resolves it."""
    import orch_items
    root = tmp_path / "projects"
    (root / "kata" / "nested").mkdir(parents=True)
    (root / "kata" / "nested" / "parser.py").write_text("x = 1")
    (root / "kata" / "check.py").write_text("y = 2")
    monkeypatch.setattr(orch_items, "projects_root", lambda: root)
    return "kata"


class TestProjectDirIsSearched:
    def test_project_file_is_not_flagged(self, project):
        """The bug. A run working in its own project dir cited real files
        and was told it invented them."""
        assert cited_py_not_found("I rewrote check.py and it passes", project) == set()

    def test_nested_project_file_is_not_flagged(self, project):
        """The corpora that triggered this were one level down."""
        assert cited_py_not_found("fixed parser.py per SPEC.md", project) == set()

    def test_without_project_the_same_citation_is_flagged(self, project):
        """Control: identical text, no project → still flagged. Proves the
        fix is the project lookup and not something incidental."""
        assert cited_py_not_found("I rewrote check.py and it passes", "") == {"check.py"}

    def test_unknown_file_still_flagged_with_project(self, project):
        """The guard must keep working — this is not 'stop checking'."""
        assert cited_py_not_found("see totally_made_up.py", project) == {"totally_made_up.py"}


class TestBasics:
    def test_no_citation_no_flag(self):
        assert cited_py_not_found("no files mentioned here") == set()

    def test_empty_result(self):
        assert cited_py_not_found("") == set()
        assert cited_py_not_found(None) == set()

    def test_packaging_names_allowlisted(self):
        got = cited_py_not_found("touched __init__.py, setup.py and conftest.py")
        assert got == set()

    def test_unresolvable_project_fails_open(self, monkeypatch):
        """A project that raises on lookup must not add flags — failing
        open can only reduce what is reported, never invent a flag."""
        import orch_items

        def boom(_slug):
            raise RuntimeError("no workspace")

        monkeypatch.setattr(orch_items, "project_dir", boom)
        # still flags what it genuinely cannot find, and does not raise
        assert cited_py_not_found("see nonexistent_thing.py", "kata") == \
            {"nonexistent_thing.py"}

    def test_repo_src_dir_is_searched(self, tmp_path, monkeypatch):
        """Sanity that the src/ leg still works, without depending on the
        cwd pytest happens to run from."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "some_module.py").write_text("z = 3")
        monkeypatch.chdir(tmp_path)
        assert cited_py_not_found("edited some_module.py") == set()
        assert cited_py_not_found("edited other_module.py") == {"other_module.py"}
