"""Tests for secret_scrub.py — scrub() (secret-shaped strings) and
scrub_identifiers() (known local identifiers, PORTABLE_LEARNING_DESIGN §4)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from secret_scrub import scrub, scrub_identifiers


# ---------------------------------------------------------------------------
# scrub() — existing secret-shaped-string coverage (no dedicated test file
# existed before this chunk; a light baseline here guards the module).
# ---------------------------------------------------------------------------

class TestScrub:
    def test_anthropic_key_redacted(self):
        assert scrub("key=sk-ant-abcdefghijklmnopqrstuvwx") == "key=[REDACTED]"

    def test_github_token_redacted(self):
        assert scrub("ghp_abcdefghijklmnopqrstuvwx123456") == "[REDACTED]"

    def test_plain_string_untouched(self):
        assert scrub("just a normal sentence") == "just a normal sentence"

    def test_recurses_into_dict_and_list(self):
        obj = {"a": ["ok", "token=abcdefghsecretvalue123"]}
        result = scrub(obj)
        assert result["a"][0] == "ok"
        assert "[REDACTED]" in result["a"][1]


# ---------------------------------------------------------------------------
# scrub_identifiers() — $HOME, username, hostname, deny-list redaction
# ---------------------------------------------------------------------------

class TestScrubIdentifiers:
    def test_home_path_redacted_with_stable_token(self):
        s = scrub_identifiers("cd /home/jeremy/workspace && run", home="/home/jeremy")
        assert s == "cd [HOME]/workspace && run"

    def test_username_redacted_standalone(self):
        s = scrub_identifiers(
            "ask jeremy to review", home="/home/jeremy", hostname="mac-mini",
        )
        assert s == "ask [USER] to review"

    def test_username_not_redacted_as_substring_of_other_word(self):
        # "jeremy" must not clobber inside "jeremyville" — word-boundary match only.
        s = scrub_identifiers("visiting jeremyville", home="/home/jeremy", hostname="mac-mini")
        assert s == "visiting jeremyville"

    def test_hostname_redacted_with_stable_token(self):
        s = scrub_identifiers(
            "reachable at mac-mini.local", home="/home/jeremy", hostname="mac-mini",
        )
        assert s == "reachable at [HOST].local"

    def test_denylist_entries_redacted(self):
        s = scrub_identifiers(
            "contact slycrel@gmail.com for access",
            home="/home/jeremy", hostname="mac-mini",
            denylist=["slycrel@gmail.com"],
        )
        assert s == "contact [REDACTED] for access"

    def test_denylist_empty_by_default(self):
        # Caller must explicitly assemble the deny-list; nothing is hardcoded.
        s = scrub_identifiers("contact slycrel@gmail.com", home="/home/jeremy", hostname="mac-mini")
        assert "slycrel@gmail.com" in s

    def test_recurses_into_nested_structures(self):
        obj = {
            "path": "/home/jeremy/skills/edge-scan.md",
            "notes": ["seen on mac-mini", "owner: jeremy"],
        }
        result = scrub_identifiers(obj, home="/home/jeremy", hostname="mac-mini")
        assert result["path"] == "[HOME]/skills/edge-scan.md"
        assert result["notes"][0] == "seen on [HOST]"
        assert result["notes"][1] == "owner: [USER]"

    def test_non_string_values_untouched(self):
        obj = {"count": 3, "ok": True, "ratio": 0.5, "nothing": None}
        result = scrub_identifiers(obj, home="/home/jeremy", hostname="mac-mini")
        assert result == obj

    def test_home_prefix_and_trailing_username_both_redacted(self):
        # The $HOME prefix collapses to [HOME] first; the standalone "jeremy"
        # left over in the filename is a separate match caught by the
        # username pass.
        s = scrub_identifiers("/home/jeremy/jeremy-notes.md", home="/home/jeremy", hostname="mac-mini")
        assert s == "[HOME]/[USER]-notes.md"
        assert "/home/jeremy" not in s

    def test_defaults_derive_home_and_hostname_when_unset(self):
        # No explicit home/hostname/denylist — must not raise, must return a string.
        result = scrub_identifiers("some arbitrary text with no identifiers")
        assert isinstance(result, str)
