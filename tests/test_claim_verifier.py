"""Tests for claim_verifier.py — file-path hallucination detection."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claim_verifier import (
    extract_file_claims,
    verify_file_claims,
    annotate_result,
    is_synthesis_step,
    ClaimReport,
)


# ---------------------------------------------------------------------------
# extract_file_claims
# ---------------------------------------------------------------------------

class TestExtractFileClaims:
    def test_extracts_src_path(self):
        text = "The main logic is in src/agent_loop.py and tests/test_e2e_smoke.py."
        claims = extract_file_claims(text)
        assert "src/agent_loop.py" in claims
        assert "tests/test_e2e_smoke.py" in claims

    def test_extracts_bare_py_file(self):
        text = "Found a bug in memory.py near line 400."
        claims = extract_file_claims(text)
        assert "memory.py" in claims

    def test_extracts_markdown_file(self):
        text = "See ARCHITECTURE.md for the design rationale."
        claims = extract_file_claims(text)
        assert "ARCHITECTURE.md" in claims

    def test_deduplicates(self):
        text = "src/memory.py is large. Also see src/memory.py."
        claims = extract_file_claims(text)
        assert claims.count("src/memory.py") == 1

    def test_skips_stdlib_names(self):
        text = "Uses os.py and json.py for standard operations."
        claims = extract_file_claims(text)
        assert "os.py" not in claims
        assert "json.py" not in claims

    def test_empty_text_returns_empty(self):
        assert extract_file_claims("") == []

    def test_text_with_no_files_returns_empty(self):
        text = "The system uses a multi-step approach with retries."
        assert extract_file_claims(text) == []

    def test_extracts_docs_path(self):
        text = "Design documented in docs/ARCHITECTURE.md"
        claims = extract_file_claims(text)
        assert "docs/ARCHITECTURE.md" in claims

    def test_extracts_persona_yaml(self):
        text = "The garrytan persona is at personas/garrytan.yaml."
        claims = extract_file_claims(text)
        assert "personas/garrytan.yaml" in claims


# ---------------------------------------------------------------------------
# verify_file_claims
# ---------------------------------------------------------------------------

class TestVerifyFileClaims:
    def test_existing_file_is_verified(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("# real")
        text = "The module is at src/real.py."
        report = verify_file_claims(text, project_root=tmp_path)
        assert "src/real.py" in report.verified
        assert "src/real.py" not in report.not_found

    def test_nonexistent_file_is_not_found(self, tmp_path):
        text = "The module lat.py handles everything."
        report = verify_file_claims(text, project_root=tmp_path)
        assert "lat.py" in report.not_found
        assert "lat.py" not in report.verified

    def test_has_hallucinations_true(self, tmp_path):
        text = "See ghost_module.py for the implementation."
        report = verify_file_claims(text, project_root=tmp_path)
        assert report.has_hallucinations is True

    def test_has_hallucinations_false(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("")
        report = verify_file_claims("See src/real.py", project_root=tmp_path)
        assert report.has_hallucinations is False

    def test_hallucination_rate_all_bad(self, tmp_path):
        text = "See fake1.py and fake2.py for details."
        report = verify_file_claims(text, project_root=tmp_path)
        assert report.hallucination_rate == 1.0

    def test_hallucination_rate_mixed(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("")
        text = "See src/real.py and ghost.py."
        report = verify_file_claims(text, project_root=tmp_path)
        # 1 verified, 1 not_found → 50%
        assert report.hallucination_rate == 0.5

    def test_no_claims_returns_empty_report(self, tmp_path):
        text = "The system is working correctly."
        report = verify_file_claims(text, project_root=tmp_path)
        assert report.raw_claims == []
        assert report.verified == []
        assert report.not_found == []

    def test_summary_includes_not_found(self, tmp_path):
        text = "See fake.py for details."
        report = verify_file_claims(text, project_root=tmp_path)
        assert "NOT FOUND" in report.summary()

    def test_summary_all_good(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "x.py").write_text("")
        report = verify_file_claims("See src/x.py", project_root=tmp_path)
        summary = report.summary()
        assert "verified" in summary


# ---------------------------------------------------------------------------
# annotate_result
# ---------------------------------------------------------------------------

class TestAnnotateResult:
    def test_clean_result_unchanged(self, tmp_path):
        text = "All steps completed successfully."
        annotated = annotate_result(text, project_root=tmp_path)
        assert annotated == text

    def test_hallucination_triggers_annotation(self, tmp_path):
        text = "See ghost_module.py for details."
        annotated = annotate_result(text, project_root=tmp_path)
        assert annotated != text
        assert "claim-verifier" in annotated
        assert "NOT_FOUND" in annotated or "ghost_module.py" in annotated

    def test_only_if_hallucinations_false_always_annotates(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("")
        text = "See src/real.py."
        annotated = annotate_result(text, project_root=tmp_path, only_if_hallucinations=False)
        assert "claim-verifier" in annotated

    def test_annotation_appended_not_prepended(self, tmp_path):
        text = "The result of step 3: ghost.py handles it."
        annotated = annotate_result(text, project_root=tmp_path)
        assert annotated.startswith(text)


# ---------------------------------------------------------------------------
# is_synthesis_step
# ---------------------------------------------------------------------------

class TestIsSynthesisStep:
    @pytest.mark.parametrize("step", [
        "Synthesize all findings into a report",
        "Summarize the research results",
        "Write final report based on prior steps",
        "Compile findings from steps 1-5",
        "Aggregate results and produce summary",
        "Review all step outputs and write conclusion",
    ])
    def test_synthesis_patterns_detected(self, step):
        assert is_synthesis_step(step) is True

    @pytest.mark.parametrize("step", [
        "Read src/memory.py",
        "Run pytest tests/",
        "Check the config value",
        "Fetch market data from API",
    ])
    def test_non_synthesis_not_detected(self, step):
        assert is_synthesis_step(step) is False

    def test_case_insensitive(self):
        assert is_synthesis_step("SUMMARIZE the findings") is True
