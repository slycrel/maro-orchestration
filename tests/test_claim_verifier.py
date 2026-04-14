"""Tests for claim_verifier.py — file-path and symbol hallucination detection."""

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
    extract_symbol_claims,
    verify_symbol_claims,
    verify_all_claims,
    SymbolReport,
    CompoundClaimReport,
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

    def test_backtick_wrapped_path_not_truncated(self):
        # Regression: `models.py` was producing "odels.py" because the lookbehind
        # only blocked `m` (preceded by backtick) but allowed match to start at `o`
        # (preceded by word char `m`). Fix tightens lookbehind to `(?<![\w...])`.
        text = "Updated `models.py` and `reviews.py` today."
        claims = extract_file_claims(text)
        assert "odels.py" not in claims
        assert "eviews.py" not in claims

    def test_single_quote_wrapped_path_not_truncated(self):
        text = "Check 'handle.py' and 'main.py' please."
        claims = extract_file_claims(text)
        assert "andle.py" not in claims
        assert "ain.py" not in claims

    def test_paren_wrapped_path_not_truncated(self):
        text = "The fix (evolver.py) is in place."
        claims = extract_file_claims(text)
        assert "volver.py" not in claims

    def test_word_char_followed_path_not_fragmented(self):
        # "inmodels.py" should not yield "odels.py" or "models.py" — it's a word.
        text = "The symbol inmodels.py is weird."
        claims = extract_file_claims(text)
        assert "odels.py" not in claims
        assert "models.py" not in claims


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

    def test_bare_filename_found_via_src_fallback(self, tmp_path):
        """A bare 'memory.py' is found via fuzzy src/ lookup even without path prefix."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "memory.py").write_text("# memory module")
        # Bare filename reference — no src/ prefix
        report = verify_file_claims("The memory.py module handles this.", project_root=tmp_path)
        assert "memory.py" in report.verified

    def test_bare_filename_found_via_tests_fallback(self, tmp_path):
        """A bare test file name is found via fuzzy tests/ lookup."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_memory.py").write_text("# tests")
        report = verify_file_claims("See test_memory.py for coverage.", project_root=tmp_path)
        assert "test_memory.py" in report.verified


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


# ---------------------------------------------------------------------------
# extract_symbol_claims
# ---------------------------------------------------------------------------

class TestExtractSymbolClaims:
    def test_backtick_function_call(self):
        claims = extract_symbol_claims("The `apply_suggestion()` function does X.")
        assert "apply_suggestion" in claims

    def test_backtick_name_no_parens(self):
        claims = extract_symbol_claims("See `scan_outcomes_for_signals` for details.")
        assert "scan_outcomes_for_signals" in claims

    def test_def_keyword(self):
        claims = extract_symbol_claims("I added `def run_agent_loop` to agent_loop.py.")
        assert "run_agent_loop" in claims

    def test_class_keyword(self):
        claims = extract_symbol_claims("The class Director handles routing.")
        assert "Director" in claims

    def test_contextual_function_word(self):
        claims = extract_symbol_claims("The verify_post_apply function was added.")
        assert "verify_post_apply" in claims

    def test_short_names_skipped(self):
        # Names shorter than 5 chars shouldn't appear
        claims = extract_symbol_claims("The `db()` and `run()` calls.")
        assert "db" not in claims
        assert "run" not in claims

    def test_dunder_names_skipped(self):
        claims = extract_symbol_claims("The `__init__` and `__repr__` methods.")
        assert "__init__" not in claims
        assert "__repr__" not in claims

    def test_stdlib_builtins_skipped(self):
        claims = extract_symbol_claims("Call `print()`, `range()`, and `isinstance()`.")
        assert "print" not in claims
        assert "range" not in claims

    def test_empty_text_returns_empty(self):
        assert extract_symbol_claims("") == []

    def test_deduplicates(self):
        text = "`apply_suggestion()` is called twice in `apply_suggestion()`."
        claims = extract_symbol_claims(text)
        assert claims.count("apply_suggestion") == 1


# ---------------------------------------------------------------------------
# verify_symbol_claims
# ---------------------------------------------------------------------------

class TestVerifySymbolClaims:
    def test_known_real_symbol_verified(self):
        """A function that actually exists in src/ is verified."""
        root = Path(__file__).parent.parent
        # verify_file_claims definitely exists in claim_verifier.py
        report = verify_symbol_claims(
            "The `verify_file_claims` function handles path checking.",
            project_root=root,
        )
        assert "verify_file_claims" in report.verified
        assert "verify_file_claims" not in report.not_found

    def test_nonexistent_symbol_not_found(self):
        """A clearly made-up function name is flagged as not found."""
        root = Path(__file__).parent.parent
        report = verify_symbol_claims(
            "The `xyzzy_frobnicate_banana` function was added.",
            project_root=root,
        )
        assert "xyzzy_frobnicate_banana" in report.not_found

    def test_empty_text_returns_empty_report(self):
        report = verify_symbol_claims("No code symbols here.", project_root=Path("."))
        assert report.raw_claims == []
        assert report.verified == []
        assert report.not_found == []

    def test_symbol_report_has_hallucinations_true(self):
        report = SymbolReport(
            raw_claims=["fake_func"],
            verified=[],
            not_found=["fake_func"],
        )
        assert report.has_hallucinations is True

    def test_symbol_report_has_hallucinations_false(self):
        report = SymbolReport(
            raw_claims=["real_func"],
            verified=["real_func"],
            not_found=[],
        )
        assert report.has_hallucinations is False

    def test_symbol_report_summary_with_not_found(self):
        report = SymbolReport(
            raw_claims=["missing_fn"],
            verified=[],
            not_found=["missing_fn"],
        )
        assert "NOT FOUND" in report.summary()
        assert "missing_fn" in report.summary()

    def test_custom_project_root(self, tmp_path):
        """Symbols in custom project root are found."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "mymodule.py").write_text("def custom_function_xyz():\n    pass\n")
        report = verify_symbol_claims(
            "The `custom_function_xyz` was implemented.",
            project_root=tmp_path,
        )
        assert "custom_function_xyz" in report.verified


# ---------------------------------------------------------------------------
# verify_all_claims / CompoundClaimReport
# ---------------------------------------------------------------------------

class TestVerifyAllClaims:
    def test_returns_compound_report(self):
        result = verify_all_claims("No claims here.", project_root=Path("."))
        assert isinstance(result, CompoundClaimReport)
        assert isinstance(result.file_report, ClaimReport)
        assert isinstance(result.symbol_report, SymbolReport)

    def test_no_hallucinations_when_all_clean(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "util.py").write_text("def helper_function():\n    pass\n")
        (tmp_path / "myfile.py").write_text("")
        result = verify_all_claims(
            "The `helper_function` was added to myfile.py.",
            project_root=tmp_path,
        )
        assert result.file_report.verified  # myfile.py found
        assert result.symbol_report.verified  # helper_function found
        assert not result.has_hallucinations

    def test_has_hallucinations_on_bad_file(self, tmp_path):
        result = verify_all_claims(
            "Updated ghostfile.py with new logic.",
            project_root=tmp_path,
        )
        assert result.has_hallucinations
        assert "ghostfile.py" in result.not_found_files

    def test_has_hallucinations_on_bad_symbol(self, tmp_path):
        (tmp_path / "src").mkdir()
        result = verify_all_claims(
            "The `nonexistent_banana_func` function was added.",
            project_root=tmp_path,
        )
        assert result.has_hallucinations
        assert "nonexistent_banana_func" in result.not_found_symbols


# ---------------------------------------------------------------------------
# annotate_result with symbol checking
# ---------------------------------------------------------------------------

class TestAnnotateResultWithSymbols:
    def test_symbol_not_found_annotated(self, tmp_path):
        (tmp_path / "src").mkdir()
        result = annotate_result(
            "The `phantom_function_xyz` was added.",
            project_root=tmp_path,
            check_symbols=True,
        )
        assert "SYMBOL_CLAIMS_NOT_FOUND" in result
        assert "phantom_function_xyz" in result

    def test_no_annotation_when_clean(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "real.py").write_text("def actual_function_abc():\n    pass\n")
        result = annotate_result(
            "Called `actual_function_abc()` successfully.",
            project_root=tmp_path,
            check_symbols=True,
        )
        # No hallucinations → text returned unchanged
        assert "SYMBOL_CLAIMS_NOT_FOUND" not in result
        assert "claim-verifier" not in result

    def test_check_symbols_false_skips_symbol_check(self, tmp_path):
        (tmp_path / "src").mkdir()
        result = annotate_result(
            "The `phantom_function_xyz` was added.",
            project_root=tmp_path,
            check_symbols=False,
        )
        assert "SYMBOL_CLAIMS_NOT_FOUND" not in result
