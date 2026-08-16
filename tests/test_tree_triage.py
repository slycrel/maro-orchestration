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


def _seed_stale_mix(tmp_path: Path) -> Path:
    """The 2026-08-15 incident shape: the tree copy carries commit A's
    edit but not commit B's, so its blob matches NO ancestor (A landed
    interleaved with B) while being strictly behind HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "doc.txt").write_text("base line\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "base")
    # Commit B: another session's hunk, landed first. Commit A': this
    # session's edit REPLAYED on top of B (the pre-rebase base+A blob
    # exists only in the discarded local commit, never in ancestry).
    (repo / "doc.txt").write_text("base line\nsession B landed hunk\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "B: landed from elsewhere")
    (repo / "doc.txt").write_text(
        "base line\nsession B landed hunk\nsession A edit\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "A: local edit (replayed)")
    # Materialization gap: tree copy = pre-rebase base + A only.
    (repo / "doc.txt").write_text("base line\nsession A edit\n")
    return repo


def test_stale_mix_is_behind_not_real_with_blame_evidence(tmp_path):
    """Pre-fix, this exact shape reported REAL (0 stale, 1 real) — the
    2026-08-15 misread. It must now surface as BEHIND with the landing
    commit named in the blame summary."""
    repo = _seed_stale_mix(tmp_path)

    report = _triage(repo)
    assert "BEHIND" in report
    assert "REAL" not in report
    assert "missing lines from" in report          # blame evidence
    assert "B: landed from elsewhere" in report    # names the landed commit
    assert "0 stale, 1 behind, 0 real." in report


def test_fix_leaves_behind_paths_alone(tmp_path):
    """Content can't distinguish stale-mix from a deletion-in-progress,
    so plain --fix must never restore BEHIND paths."""
    repo = _seed_stale_mix(tmp_path)

    out = _triage(repo, "--fix")
    assert "NOT restored" in out
    assert (repo / "doc.txt").read_text() == "base line\nsession A edit\n"


def test_fix_behind_restores_after_judgment(tmp_path):
    repo = _seed_stale_mix(tmp_path)

    _triage(repo, "--fix-behind")
    assert (repo / "doc.txt").read_text() == (
        "base line\nsession B landed hunk\nsession A edit\n")


def test_behind_blame_names_all_landing_commits_multi_hunk(tmp_path):
    """r1: sort -u sampled an arbitrary 3-of-N of the blamed commits by
    hash order and could drop the actual landing commit. First-appearance
    dedup must name every landing commit when the missing lines span
    several: tree = base + D's line only, missing B's and C's separated
    hunks (matches no ancestor blob — only D's commit has D's line, and
    it also has B's and C's)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "doc.txt").write_text("one\ntwo\nthree\nfour\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "base")
    (repo / "doc.txt").write_text("one\nfrom-B\ntwo\nthree\nfour\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "B lands hunk one")
    (repo / "doc.txt").write_text(
        "one\nfrom-B\ntwo\nthree\nfrom-C\nfour\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "C lands hunk two")
    (repo / "doc.txt").write_text(
        "one\nfrom-B\ntwo\nthree\nfrom-C\nfour\nfrom-D\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-qm", "D lands hunk three")
    # Stale-mix tree copy: keeps D's line (so no ancestor blob matches),
    # missing B's and C's hunks — deletion-only vs HEAD, two commits.
    (repo / "doc.txt").write_text("one\ntwo\nthree\nfour\nfrom-D\n")

    report = _triage(repo)
    assert "BEHIND" in report
    assert "B lands hunk one" in report
    assert "C lands hunk two" in report


def test_deletion_only_edit_reads_behind_but_survives_fix(tmp_path):
    """The honest ambiguity, pinned: a REAL deletion-only edit (another
    session removing lines mid-chunk) is content-identical to stale-mix
    and now reads BEHIND — the contract that matters is that --fix
    leaves it untouched."""
    repo = _seed_repo(tmp_path)
    (repo / "a.txt").write_text("")  # deleted the only line, kept file

    report = _triage(repo)
    assert "BEHIND" in report

    _triage(repo, "--fix")
    assert (repo / "a.txt").read_text() == ""
