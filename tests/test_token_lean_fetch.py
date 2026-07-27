"""Tests for the token-lean fetch path: the Cloudflare markdown tier, raw-page
capture, and the subprocess-worker CLI.

Context (docs/history/2026-07-27-tire-runs-examination.md): a research step
burned 2.14M input tokens curling raw retailer HTML because the capped markdown
chain was unreachable from the `claude -p` backend. These tests pin the three
pieces that close that hole.

No network — every backend is monkeypatched. A test that reaches the real
r.jina.ai/markdown.new would be both flaky and a silent cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import web_fetch  # noqa: E402
import fetch_tool  # noqa: E402


@pytest.fixture
def capture_env(tmp_path, monkeypatch):
    """Point captures at a tmp dir and enable them."""
    monkeypatch.setenv("MARO_FETCH_CAPTURE_DIR", str(tmp_path / "fetch-raw"))
    monkeypatch.setenv("MARO_FETCH_CAPTURE", "1")
    return tmp_path / "fetch-raw"


@pytest.fixture
def no_capture(monkeypatch):
    """Disable capture so tier tests exercise only the text chain."""
    monkeypatch.setenv("MARO_FETCH_CAPTURE", "0")


# ---------------------------------------------------------------------------
# Tier ordering — Jina → Cloudflare → raw HTTP
# ---------------------------------------------------------------------------

class TestTierChain:
    def test_jina_success_does_not_call_cloudflare(self, monkeypatch, no_capture):
        """The CF tier is a fallback, not an extra request on the happy path."""
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "j" * 200)
        called = []
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch",
                            lambda u, **k: called.append(u) or "cf")
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "j" * 200 in out
        assert called == []

    def test_cloudflare_used_when_jina_fails(self, monkeypatch, no_capture):
        """Jina 403s/rate-limits on some hosts; CF must catch those."""
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "")
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch", lambda u, **k: "c" * 200)
        raw = []
        monkeypatch.setattr(web_fetch, "_http_get_bytes",
                            lambda *a, **k: raw.append(a) or (200, b"<p>raw</p>", "utf-8"))
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "c" * 200 in out
        assert raw == [], "CF succeeded — the raw-HTML tier must not run"

    def test_raw_html_tier_is_last_resort(self, monkeypatch, no_capture):
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "")
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch", lambda u, **k: "")
        monkeypatch.setattr(web_fetch, "_http_get_bytes",
                            lambda *a, **k: (200, b"<html><body><p>fallback text</p></body></html>", "utf-8"))
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "fallback text" in out

    def test_login_wall_from_jina_falls_through_to_cloudflare(self, monkeypatch, no_capture):
        """A short 'log in / sign up' stub is a failure, not content."""
        monkeypatch.setattr(web_fetch, "_jina_fetch",
                            lambda u, **k: "Please log in or sign up to continue")
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch", lambda u, **k: "real" * 60)
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "real" in out

    def test_x_urls_never_reach_the_markdown_tiers(self, monkeypatch, no_capture):
        """X is login-walled — the authenticated chain owns it."""
        monkeypatch.setattr(web_fetch, "fetch_x_tweet", lambda u: "[x-chain]")
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch",
                            lambda u, **k: pytest.fail("CF must not see X URLs"))
        assert web_fetch.fetch_url_content("https://x.com/u/status/1") == "[x-chain]"

    def test_output_stays_within_the_cap(self, monkeypatch, no_capture):
        """The whole point: a huge page cannot blow the step budget."""
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "")
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch", lambda u, **k: "")
        huge = "<html><body>" + ("word " * 500_000) + "</body></html>"
        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, huge.encode(), "utf-8"))
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert len(out) < web_fetch._MAX_TEXT_CHARS + 500


class TestCloudflareTierParsing:
    def _resp(self, monkeypatch, body: str, status: int = 200):
        class _R:
            def __init__(self):
                self.status = status

            def read(self, _n=None):
                return body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(web_fetch.urllib.request, "urlopen", lambda *a, **k: _R())

    def test_plain_markdown_response(self, monkeypatch):
        self._resp(monkeypatch, "# Title\n\nbody text")
        assert "# Title" in web_fetch._cf_markdown_fetch("https://example.com")

    def test_json_envelope_response(self, monkeypatch):
        """markdown.new may return JSON depending on which tier served it."""
        self._resp(monkeypatch, '{"result": {"markdown": "# From JSON"}}')
        assert web_fetch._cf_markdown_fetch("https://example.com") == "# From JSON"

    def test_string_result_envelope(self, monkeypatch):
        """`{"success":true,"result":"# md"}` is the shape Cloudflare's own
        Markdown endpoint documents; treating it as a failure sent a valid
        response to the raw-HTML fallback."""
        self._resp(monkeypatch, '{"success": true, "result": "# From string"}')
        assert web_fetch._cf_markdown_fetch("https://example.com") == "# From string"

    def test_proxy_tiers_refuse_private_and_credentialed_urls(self, monkeypatch):
        """Handing an internal or credentialed URL to a third party discloses it."""
        monkeypatch.setattr(web_fetch.urllib.request, "urlopen",
                            lambda *a, **k: pytest.fail("must not contact the proxy"))
        for bad in ("http://192.168.0.1/x", "http://169.254.169.254/",
                    "https://tok:secret@example.com/x", "http://localhost/x"):
            assert web_fetch._cf_markdown_fetch(bad) == "", bad
            assert web_fetch._jina_fetch(bad) == "", bad

    def test_failure_returns_empty_never_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("network down")
        monkeypatch.setattr(web_fetch.urllib.request, "urlopen", _boom)
        assert web_fetch._cf_markdown_fetch("https://example.com") == ""

    def test_cf_output_is_capped(self, monkeypatch):
        self._resp(monkeypatch, "x" * 200_000)
        assert len(web_fetch._cf_markdown_fetch("https://example.com")) <= web_fetch._MAX_TEXT_CHARS


# ---------------------------------------------------------------------------
# Raw capture — full fidelity on disk, a pointer in context
# ---------------------------------------------------------------------------

class TestCapture:
    def test_capture_writes_file_and_manifest(self, capture_env, monkeypatch):
        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>full</html>", "utf-8"))
        path = web_fetch.capture_raw("https://example.com/p")
        assert path is not None and path.exists()
        assert path.read_text() == "<html>full</html>"
        assert (capture_env / "index.jsonl").exists()

    def test_capture_reuses_existing_file_without_refetching(self, capture_env, monkeypatch):
        calls = []
        monkeypatch.setattr(web_fetch, "_http_get_bytes",
                            lambda *a, **k: calls.append(1) or (200, b"<html>x</html>", "utf-8"))
        first = web_fetch.capture_raw("https://example.com/p")
        second = web_fetch.capture_raw("https://example.com/p")
        assert first == second
        assert len(calls) == 1, "content-addressed capture must not re-download"

    def test_capture_accepts_prefetched_html_without_a_request(self, capture_env, monkeypatch):
        """The raw-HTTP tier already holds the bytes — no second request."""
        monkeypatch.setattr(web_fetch, "_http_get_bytes",
                            lambda *a, **k: pytest.fail("must not re-fetch"))
        path = web_fetch.capture_raw("https://example.com/p", body=b"<html>given</html>")
        assert path is not None and path.read_text() == "<html>given</html>"

    def test_capture_filename_is_hash_derived_not_url_derived(self, capture_env, monkeypatch):
        """A URL-derived name would be a path-traversal vector."""
        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>x</html>", "utf-8"))
        path = web_fetch.capture_raw("https://example.com/../../etc/passwd?a=b")
        assert path is not None
        assert path.parent == capture_env
        assert path.name.endswith(".html") and "/" not in path.stem
        assert ".." not in path.name

    def test_planted_symlink_cannot_redirect_the_write(self, capture_env, monkeypatch, tmp_path):
        """The capture dir is worker-writable; a planted symlink must not
        turn a capture into an arbitrary-file overwrite.

        Verified exploitable before the O_NOFOLLOW|O_EXCL guard: write_text()
        followed the symlink and replaced the victim's contents with bytes the
        fetched page controlled.
        """
        import hashlib
        import os

        victim = tmp_path / "victim.txt"
        victim.write_text("ORIGINAL")
        capture_env.mkdir(parents=True, exist_ok=True)
        url = "https://evil.example.com/x"
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        os.symlink(victim, capture_env / f"{digest}.html.tmp")

        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>PWNED</html>", "utf-8"))
        web_fetch.capture_raw(url)

        assert victim.read_text() == "ORIGINAL", "symlink redirected the capture write"

    def test_planted_symlink_at_final_path_is_not_reused(self, capture_env, monkeypatch, tmp_path):
        """A symlinked <digest>.html would otherwise be 'reused' and its path
        handed to the model, which the prompt tells to parse it — turning the
        capture store into a local-file disclosure primitive."""
        import hashlib
        import os

        secret = tmp_path / "secret.txt"
        secret.write_text("SUPER-SECRET-TOKEN")
        capture_env.mkdir(parents=True, exist_ok=True)
        url = "https://evil.example.com/y"
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        os.symlink(secret, capture_env / f"{digest}.html")

        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>fresh</html>", "utf-8"))
        path = web_fetch.capture_raw(url)

        assert path is not None
        assert not path.is_symlink()
        assert "SUPER-SECRET-TOKEN" not in path.read_text()
        assert secret.read_text() == "SUPER-SECRET-TOKEN"

    def test_symlinked_capture_dir_is_refused(self, tmp_path, monkeypatch):
        """A capture dir symlinked into <rundir>/build/ would put attacker HTML
        in the tree viz_server serves."""
        real = tmp_path / "build"
        real.mkdir()
        link = tmp_path / "fetch-raw"
        link.symlink_to(real, target_is_directory=True)
        monkeypatch.setenv("MARO_FETCH_CAPTURE_DIR", str(link))
        assert web_fetch.capture_dir() is None

    def test_capture_respects_byte_budget(self, capture_env, monkeypatch):
        capture_env.mkdir(parents=True, exist_ok=True)
        (capture_env / "old.html").write_text("x" * 5000)
        monkeypatch.setenv("MARO_FETCH_CAPTURE_BUDGET", "1000")
        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>new</html>", "utf-8"))
        assert web_fetch.capture_raw("https://example.com/new") is None

    def test_manifest_redacts_credentials_and_query(self, capture_env, monkeypatch):
        """The manifest outlives the run — a presigned signature must not sit
        in plaintext on disk."""
        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>x</html>", "utf-8"))
        web_fetch.capture_raw("https://example.com/o?X-Amz-Signature=DEADBEEF")
        manifest = (capture_env / "index.jsonl").read_text()
        assert "DEADBEEF" not in manifest
        assert "<redacted>" in manifest

    def test_capture_refuses_private_and_credentialed_urls(self, capture_env, monkeypatch):
        """capture_raw fetches directly from this host — the SSRF primitive."""
        monkeypatch.setattr(web_fetch, "_http_get_bytes",
                            lambda *a, **k: pytest.fail("must not fetch a blocked URL"))
        for bad in ("http://169.254.169.254/latest/meta-data/",
                    "http://192.168.1.5/admin",
                    "http://127.0.0.1:8080/",
                    "https://user:pw@example.com/x",
                    "file:///etc/passwd"):
            assert web_fetch.capture_raw(bad) is None, bad

    def test_capture_stores_bytes_verbatim_not_reencoded_text(self, capture_env, monkeypatch):
        """A non-UTF-8 page must land on disk exactly as the server sent it.

        Capture used to store `errors="replace"`-decoded text re-encoded as
        UTF-8, so every byte outside the declared charset was silently rewritten
        to U+FFFD — the captured file was not what the origin served, which
        defeats keeping it for re-extraction.
        """
        raw = "<html><body>Preis: 25€ — Größe</body></html>".encode("windows-1252")
        monkeypatch.setattr(web_fetch, "_http_get_bytes",
                            lambda *a, **k: (200, raw, "windows-1252"))
        path = web_fetch.capture_raw("https://example.com/de")
        assert path is not None
        assert path.read_bytes() == raw
        assert b"\xef\xbf\xbd" not in path.read_bytes()  # no U+FFFD replacement chars

    def test_extraction_is_pure_no_capture_side_effect(self, capture_env, monkeypatch):
        """_extract_text does text only — capture happens at one seam above it.

        When the two were interleaved, "get me the text" silently performed a
        second origin request, which is how the SSRF path and the double-fetch
        got in.
        """
        capture_env.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "m" * 200)
        text, body = web_fetch._extract_text("https://example.com/a")
        assert text == "m" * 200
        assert body is None, "a proxy tier holds no origin bytes"
        assert list(capture_env.glob("*.html")) == [], "extraction wrote to disk"

    def test_raw_tier_capture_reuses_its_own_bytes(self, capture_env, monkeypatch):
        """The tier that already downloaded the page must not fetch it twice."""
        calls = []
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "")
        monkeypatch.setattr(web_fetch, "_cf_markdown_fetch", lambda u, **k: "")
        monkeypatch.setattr(
            web_fetch, "_http_get_bytes",
            lambda *a, **k: calls.append(1) or (200, b"<html><p>hello world</p></html>", "utf-8"))
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "hello world" in out
        assert "raw HTML saved" in out
        assert len(calls) == 1, f"expected one origin request, got {len(calls)}"

    def test_capture_disabled_by_env(self, capture_env, monkeypatch):
        monkeypatch.setenv("MARO_FETCH_CAPTURE", "0")
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "m" * 200)
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "raw HTML saved" not in out

    def test_capture_failure_never_breaks_the_fetch(self, capture_env, monkeypatch):
        """fetch_url_content's 'never raises' contract outranks capture."""
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "m" * 200)

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(web_fetch, "capture_raw", _boom)
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "m" * 200 in out
        assert "raw HTML saved" not in out

    def test_content_carries_a_pointer_not_the_html(self, capture_env, monkeypatch):
        """The context cost of capture is one path line, not the page."""
        monkeypatch.setattr(web_fetch, "_jina_fetch", lambda u, **k: "summary text" * 20)
        monkeypatch.setattr(web_fetch, "_http_get_bytes", lambda *a, **k: (200, b"<html>" + b"z" * 100_000 + b"</html>", "utf-8"))
        out = web_fetch.fetch_url_content("https://example.com/a")
        assert "raw HTML saved" in out
        assert "z" * 100 not in out, "captured HTML must never enter the returned text"
        assert len(out) < 1000


# ---------------------------------------------------------------------------
# CLI — the affordance subprocess workers actually have
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_prints_fetched_content(self, monkeypatch, capsys):
        monkeypatch.setattr(fetch_tool, "fetch", lambda *a, **k: "[Content from u]\nhello")
        rc = fetch_tool.main(["https://example.com"])
        assert rc == 0
        assert "hello" in capsys.readouterr().out

    def test_cli_max_chars_truncates(self, monkeypatch, capsys):
        monkeypatch.setattr(fetch_tool, "fetch", lambda *a, **k: "abcdefghij" * 100)
        fetch_tool.main(["https://example.com", "--max-chars", "50"])
        out = capsys.readouterr().out
        assert "truncated by --max-chars" in out
        assert len(out) < 200

    def test_cli_no_capture_sets_env(self, monkeypatch, capsys):
        monkeypatch.setattr(fetch_tool, "fetch", lambda *a, **k: "content")
        monkeypatch.delenv("MARO_FETCH_CAPTURE", raising=False)
        fetch_tool.main(["https://example.com", "--no-capture"])
        import os
        assert os.environ["MARO_FETCH_CAPTURE"] == "0"

    def test_cli_reports_failure_via_exit_code(self, monkeypatch, capsys):
        """A bracketed-only result means nothing was fetched."""
        monkeypatch.setattr(fetch_tool, "fetch", lambda *a, **k: "[Could not connect to u]")
        assert fetch_tool.main(["https://example.com"]) == 1

    def test_cli_passes_mode_through(self, monkeypatch, capsys):
        seen = {}
        monkeypatch.setattr(fetch_tool, "fetch",
                            lambda t, **k: seen.update(target=t, **k) or "ok")
        fetch_tool.main(["LocalLLaMA", "--mode", "reddit_posts", "--limit", "3"])
        assert seen["mode"] == "reddit_posts" and seen["limit"] == 3


# ---------------------------------------------------------------------------
# The prompt seam — a worker only uses what it is told exists
# ---------------------------------------------------------------------------

class TestExecutePromptWiring:
    def test_prompt_names_a_cli_that_actually_runs(self):
        """Not just 'the path exists' — run it.

        The first version of this test asserted host-side file existence, which
        proves nothing about whether a worker can invoke the command. Executing
        it is the cheapest honest proof. (Still host-only: whether the command
        resolves inside the executor container is a separate, unverified
        question — tracked in BACKLOG, not asserted here.)
        """
        import shlex
        import subprocess

        from step_exec import EXECUTE_SYSTEM, _fetch_cli_path

        assert "__FETCH_CLI__" not in EXECUTE_SYSTEM, "sentinel left unreplaced"
        resolved = _fetch_cli_path()
        assert resolved in EXECUTE_SYSTEM, "prompt does not name the resolved command"

        proc = subprocess.run(shlex.split(resolved) + ["--help"],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"{resolved} --help failed: {proc.stderr[:400]}"
        assert "--no-capture" in proc.stdout

    def test_prompt_forbids_curling_pages_into_context(self):
        from step_exec import EXECUTE_SYSTEM
        assert "NEVER `curl` a web page into context" in EXECUTE_SYSTEM
