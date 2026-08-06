"""tree-triage.sh classification pins.

The script's contract is two-valued: STALE (this tree is behind a landed
state — restore) vs REAL (another session's uncommitted work — never
touch). The deletion case is the hard one (adversarial review 2026-08-06
R3-5): a file missing from the tree is a `reset --mixed` materialization
gap only when some recent ancestor also lacked the path; if every
ancestor has it, the absence is a deletion in progress and --fix must
leave it alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "tree-triage.sh")

_GIT_ENV_ARGS = [
    "-c", "user.email=test@example.com",
    "-c", "user.name=triage-test",
    "-c", "commit.gpgsign=false",
]


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *_GIT_ENV_ARGS, *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return out.stdout


def _seed_repo(tmp_path: Path) -> Path:
    """Two commits: a.txt lives in both; b.txt only in the second."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("original a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "add a")
    (repo / "b.txt").write_text("landed b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "add b")
    return repo


def _triage(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["bash", SCRIPT, *args], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def test_missing_recently_added_file_is_stale_and_fix_restores_it(tmp_path):
    repo = _seed_repo(tmp_path)
    # Simulate the reset --mixed gap: b.txt in HEAD+index, absent from tree,
    # and an ancestor (the first commit) predates it.
    (repo / "b.txt").unlink()

    report = _triage(repo)
    assert "b.txt" in report and "STALE" in report

    _triage(repo, "--fix")
    assert (repo / "b.txt").read_text() == "landed b\n"


def test_deleted_long_tracked_file_is_real_and_fix_leaves_it(tmp_path):
    repo = _seed_repo(tmp_path)
    # Every recent ancestor has a.txt — no historical state explains its
    # absence. This is another session's rm; --fix must not resurrect it.
    (repo / "a.txt").unlink()

    report = _triage(repo)
    assert "REAL" in report
    assert "STALE" not in report.split("a.txt")[1].splitlines()[0]

    _triage(repo, "--fix")
    assert not (repo / "a.txt").exists()


def test_staged_deletion_is_real_even_for_recently_added_file(tmp_path):
    repo = _seed_repo(tmp_path)
    # `git rm` records intent in the index; reset --mixed leaves
    # index == HEAD, so a staged deletion can never be a reset artifact.
    _git(repo, "rm", "-q", "b.txt")

    report = _triage(repo)
    assert "REAL uncommitted work (staged)" in report

    _triage(repo, "--fix")
    assert not (repo / "b.txt").exists()


def test_modified_file_matching_ancestor_blob_is_stale(tmp_path):
    repo = _seed_repo(tmp_path)
    (repo / "a.txt").write_text("newer a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "update a")
    # Tree rolls back to the ancestor's exact content — behind, not ahead.
    (repo / "a.txt").write_text("original a\n")

    report = _triage(repo)
    assert "STALE (matches ancestor" in report

    _triage(repo, "--fix")
    assert (repo / "a.txt").read_text() == "newer a\n"


def test_genuinely_edited_file_is_real(tmp_path):
    repo = _seed_repo(tmp_path)
    (repo / "a.txt").write_text("someone's half-written work\n")

    report = _triage(repo)
    assert "REAL uncommitted work" in report

    _triage(repo, "--fix")
    assert (repo / "a.txt").read_text() == "someone's half-written work\n"
