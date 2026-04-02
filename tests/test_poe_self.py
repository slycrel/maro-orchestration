"""Tests for poe_self.py — persistent Poe identity block."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import poe_self
from poe_self import (
    _IDENTITY_FALLBACK,
    clear_cache,
    load_poe_identity,
    with_poe_identity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure identity cache is cleared between tests."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# load_poe_identity — fallback
# ---------------------------------------------------------------------------


class TestLoadPoeIdentityFallback:
    def test_missing_file_returns_fallback(self, tmp_path):
        missing = tmp_path / "nonexistent.md"
        with patch.object(poe_self, "_identity_path", return_value=missing):
            result = load_poe_identity(use_cache=False)
        assert result == _IDENTITY_FALLBACK.strip() or _IDENTITY_FALLBACK in result

    def test_fallback_is_non_empty(self, tmp_path):
        missing = tmp_path / "nonexistent.md"
        with patch.object(poe_self, "_identity_path", return_value=missing):
            result = load_poe_identity(use_cache=False)
        assert result.strip()

    def test_empty_file_returns_fallback(self, tmp_path):
        f = tmp_path / "POE_IDENTITY.md"
        f.write_text("")
        with patch.object(poe_self, "_identity_path", return_value=f):
            result = load_poe_identity(use_cache=False)
        assert result == _IDENTITY_FALLBACK.strip() or _IDENTITY_FALLBACK in result


# ---------------------------------------------------------------------------
# load_poe_identity — file reading
# ---------------------------------------------------------------------------


class TestLoadPoeIdentityFromFile:
    def _write_identity(self, tmp_path: Path, content: str) -> Path:
        f = tmp_path / "POE_IDENTITY.md"
        f.write_text(content, encoding="utf-8")
        return f

    def test_reads_content_after_separator(self, tmp_path):
        f = self._write_identity(tmp_path, "# Poe\nThis file is...\n---\nCore content here.")
        with patch.object(poe_self, "_identity_path", return_value=f):
            result = load_poe_identity(use_cache=False)
        assert "Core content here." in result

    def test_strips_header_preamble(self, tmp_path):
        f = self._write_identity(tmp_path, "# Poe — Self Identity Block\nThis file is injected.\n---\nActual identity.")
        with patch.object(poe_self, "_identity_path", return_value=f):
            result = load_poe_identity(use_cache=False)
        assert "# Poe" not in result
        assert "This file is injected" not in result
        assert "Actual identity." in result

    def test_truncates_at_max_chars(self, tmp_path):
        long_content = "# Poe\n---\n" + "x" * 3000
        f = self._write_identity(tmp_path, long_content)
        with patch.object(poe_self, "_identity_path", return_value=f):
            result = load_poe_identity(use_cache=False, max_chars=100)
        assert "[identity truncated]" in result
        assert len(result) < 200  # well under 3000

    def test_no_truncation_under_limit(self, tmp_path):
        f = self._write_identity(tmp_path, "# Poe\n---\nShort content.")
        with patch.object(poe_self, "_identity_path", return_value=f):
            result = load_poe_identity(use_cache=False, max_chars=2000)
        assert "[identity truncated]" not in result


# ---------------------------------------------------------------------------
# load_poe_identity — caching
# ---------------------------------------------------------------------------


class TestLoadPoeIdentityCache:
    def test_caches_result_on_second_call(self, tmp_path):
        f = tmp_path / "POE_IDENTITY.md"
        f.write_text("# Poe\n---\nCached content.")

        call_count = 0
        original_read = Path.read_text

        def counting_read(self_path, *args, **kwargs):
            nonlocal call_count
            if self_path == f:
                call_count += 1
            return original_read(self_path, *args, **kwargs)

        with patch.object(poe_self, "_identity_path", return_value=f):
            with patch.object(Path, "read_text", counting_read):
                load_poe_identity(use_cache=True)
                load_poe_identity(use_cache=True)

        assert call_count == 1  # file read only once

    def test_use_cache_false_bypasses_cache(self, tmp_path):
        f = tmp_path / "POE_IDENTITY.md"
        f.write_text("# Poe\n---\nFirst content.")

        with patch.object(poe_self, "_identity_path", return_value=f):
            r1 = load_poe_identity(use_cache=False)
            f.write_text("# Poe\n---\nUpdated content.")
            r2 = load_poe_identity(use_cache=False)

        assert "Updated content." in r2
        assert "First content." not in r2

    def test_clear_cache_resets(self, tmp_path):
        f = tmp_path / "POE_IDENTITY.md"
        f.write_text("# Poe\n---\nIdentity.")
        with patch.object(poe_self, "_identity_path", return_value=f):
            load_poe_identity(use_cache=True)
            assert poe_self._IDENTITY_CACHE is not None
            clear_cache()
            assert poe_self._IDENTITY_CACHE is None


# ---------------------------------------------------------------------------
# with_poe_identity
# ---------------------------------------------------------------------------


class TestWithPoeIdentity:
    def test_prepends_identity_to_prompt(self, tmp_path):
        f = tmp_path / "POE_IDENTITY.md"
        f.write_text("# Poe\n---\nI am Poe.")
        with patch.object(poe_self, "_identity_path", return_value=f):
            result = with_poe_identity("Do a task.", use_cache=False) if False else \
                     _with_poe_identity_via_patch("Do a task.", f)
        assert "I am Poe." in result
        assert "Do a task." in result

    def test_identity_comes_before_prompt(self):
        with patch("poe_self.load_poe_identity", return_value="IDENTITY BLOCK"):
            result = with_poe_identity("BASE PROMPT")
        idx_identity = result.index("IDENTITY BLOCK")
        idx_prompt = result.index("BASE PROMPT")
        assert idx_identity < idx_prompt

    def test_separator_between_identity_and_prompt(self):
        with patch("poe_self.load_poe_identity", return_value="ID"):
            result = with_poe_identity("PROMPT", separator="\n\n---\n\n")
        assert "---" in result

    def test_custom_separator(self):
        with patch("poe_self.load_poe_identity", return_value="ID"):
            result = with_poe_identity("PROMPT", separator="|||")
        assert "|||" in result

    def test_empty_identity_returns_prompt_unchanged(self):
        with patch("poe_self.load_poe_identity", return_value=""):
            result = with_poe_identity("BASE PROMPT")
        assert result == "BASE PROMPT"

    def test_whitespace_only_identity_returns_prompt_unchanged(self):
        with patch("poe_self.load_poe_identity", return_value="   \n  "):
            result = with_poe_identity("BASE PROMPT")
        assert result == "BASE PROMPT"

    def test_includes_who_i_am_header(self):
        with patch("poe_self.load_poe_identity", return_value="Poe identity"):
            result = with_poe_identity("PROMPT")
        assert "## Who I Am" in result

    def test_returns_string(self):
        with patch("poe_self.load_poe_identity", return_value="ID"):
            result = with_poe_identity("PROMPT")
        assert isinstance(result, str)


def _with_poe_identity_via_patch(prompt: str, identity_file: Path) -> str:
    """Helper: call with_poe_identity with a specific identity file."""
    with patch.object(poe_self, "_identity_path", return_value=identity_file):
        clear_cache()
        return with_poe_identity(prompt)
