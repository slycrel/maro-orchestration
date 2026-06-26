"""Tests for the filesystem ground-truth fabrication check (artifact_check.py).

Covers the done≠achieved gap: a step claims a write but produces no artifact.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from artifact_check import (  # noqa: E402
    ArtifactVerdict,
    changed_since,
    check_fabrication,
    extract_write_claims,
    snapshot_dir,
)


# --- extract_write_claims -------------------------------------------------

def test_extracts_basic_write_claims():
    assert extract_write_claims("Wrote the output to fizzbuzz.py") == ["fizzbuzz.py"]
    assert extract_write_claims("Saved results to data/out.json") == ["data/out.json"]
    assert extract_write_claims("Created the file as report.md") == ["report.md"]


def test_dedupes_claims():
    txt = "Wrote to a.py. Later saved to a.py again."
    assert extract_write_claims(txt) == ["a.py"]


def test_ignores_non_file_targets():
    # No extension => not a file claim.
    assert extract_write_claims("Saved to memory") == []
    assert extract_write_claims("Wrote the value into the database") == []
    assert extract_write_claims("Stored as a draft") == []


def test_ignores_bare_mentions_without_verb():
    # A path mention with no write verb is not a claim.
    assert extract_write_claims("The script fizzbuzz.py prints numbers") == []


def test_empty_and_none_text():
    assert extract_write_claims("") == []
    assert extract_write_claims(None) == []


# --- snapshot_dir / changed_since ----------------------------------------

def test_snapshot_empty_for_missing_root():
    assert snapshot_dir(None) == {}
    assert snapshot_dir("/nonexistent/path/xyz") == {}


def test_snapshot_and_changed_detects_new_file(tmp_path):
    before = snapshot_dir(tmp_path)
    assert before == {}
    (tmp_path / "new.py").write_text("print(1)")
    changed = changed_since(before, tmp_path)
    assert "new.py" in changed


def test_changed_skips_unmodified(tmp_path):
    (tmp_path / "stable.py").write_text("x")
    before = snapshot_dir(tmp_path)
    # No change => empty diff.
    assert changed_since(before, tmp_path) == set()


def test_snapshot_skips_vcs_dirs(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref")
    (tmp_path / "real.py").write_text("x")
    snap = snapshot_dir(tmp_path)
    assert "real.py" in snap
    assert not any(k.startswith(".git") for k in snap)


# --- check_fabrication ----------------------------------------------------

def test_fabricated_when_claim_but_no_artifact(tmp_path):
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Wrote the solution to fizzbuzz.py", str(tmp_path), before)
    assert v.fabricated is True
    assert v.claims == ["fizzbuzz.py"]
    assert v.missing == ["fizzbuzz.py"]
    assert v.changed_count == 0


def test_not_fabricated_when_file_in_diff(tmp_path):
    before = snapshot_dir(tmp_path)
    (tmp_path / "fizzbuzz.py").write_text("print('fizz')")
    v = check_fabrication("Wrote the solution to fizzbuzz.py", str(tmp_path), before)
    assert v.fabricated is False
    assert v.changed_count >= 1


def test_not_fabricated_when_claimed_file_exists_in_project_dir(tmp_path):
    # File already existed before the step (existence escape — real work elsewhere).
    (tmp_path / "fizzbuzz.py").write_text("old")
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Wrote the solution to fizzbuzz.py", str(tmp_path), before)
    assert v.fabricated is False


def test_not_fabricated_for_absolute_path_that_exists(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("{}")
    before = {}  # diff irrelevant; absolute path exists
    other = tmp_path / "elsewhere"
    other.mkdir()
    v = check_fabrication(f"Saved results to {target}", str(other), before)
    assert v.fabricated is False


def test_not_fabricated_when_no_claim(tmp_path):
    before = snapshot_dir(tmp_path)
    v = check_fabrication("Analyzed the data and found three patterns.", str(tmp_path), before)
    assert v.fabricated is False
    assert v.claims == []


def test_multiple_claims_one_real_not_fabricated(tmp_path):
    before = snapshot_dir(tmp_path)
    (tmp_path / "a.py").write_text("x")
    v = check_fabrication("Wrote to a.py and saved to b.py", str(tmp_path), before)
    # b.py absent, but a.py landed => real work happened, not fabrication.
    assert v.fabricated is False


def test_fail_open_on_bad_snapshot(tmp_path):
    # A malformed before-snapshot must not raise; verdict should be safe.
    # The file must exist so changed_since actually compares mtime (float) against
    # the bad value (str), triggering the internally-caught TypeError => fail-open.
    (tmp_path / "x.py").write_text("data")
    v = check_fabrication("Wrote to x.py", str(tmp_path), {"x.py": "not-a-float"})
    assert isinstance(v, ArtifactVerdict)
    assert v.fabricated is False
