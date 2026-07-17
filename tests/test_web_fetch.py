"""Tests for web_fetch URL extraction and HTML stripping utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import web_fetch
from web_fetch import (
    _html_to_text,
    extract_urls_from_text,
    _should_fetch,
    enrich_step_with_urls,
    _TCO_RE,
    _X_ARTICLE_RE,
    fetch_x_article,
    fetch_url_content,
)


# ---------------------------------------------------------------------------
# _html_to_text
# ---------------------------------------------------------------------------

def test_html_to_text_strips_tags():
    html = "<p>Hello <b>world</b></p>"
    result = _html_to_text(html)
    assert "Hello" in result
    assert "world" in result
    assert "<" not in result


def test_html_to_text_removes_scripts():
    html = "<html><script>var x=1;</script><body><p>Content</p></body></html>"
    result = _html_to_text(html)
    assert "Content" in result
    assert "var x" not in result


def test_html_to_text_truncates_at_max():
    html = "<p>" + "a " * 100_000 + "</p>"
    result = _html_to_text(html, max_chars=1000)
    assert len(result) <= 1000


def test_html_to_text_decodes_entities():
    html = "<p>Tom &amp; Jerry &lt;rock&gt;</p>"
    result = _html_to_text(html)
    assert "Tom & Jerry" in result
    assert "<rock>" in result


# ---------------------------------------------------------------------------
# extract_urls_from_text
# ---------------------------------------------------------------------------

def test_extract_urls_basic():
    text = "Check out https://example.com and http://foo.bar/path?q=1"
    urls = extract_urls_from_text(text)
    assert "https://example.com" in urls
    assert "http://foo.bar/path?q=1" in urls


def test_extract_urls_deduplicates():
    text = "https://x.com/foo https://x.com/foo https://y.com"
    urls = extract_urls_from_text(text)
    assert urls.count("https://x.com/foo") == 1


def test_extract_urls_strips_trailing_punctuation():
    text = "See https://example.com. And https://foo.com!"
    urls = extract_urls_from_text(text)
    assert "https://example.com" in urls
    assert "https://foo.com" in urls
    assert all(not u.endswith(".") for u in urls)


def test_extract_urls_empty():
    assert extract_urls_from_text("no urls here") == []


# ---------------------------------------------------------------------------
# _should_fetch
# ---------------------------------------------------------------------------

def test_should_fetch_skips_images():
    assert not _should_fetch("https://example.com/image.png")
    assert not _should_fetch("https://example.com/style.css")
    assert not _should_fetch("https://example.com/app.js")


def test_should_fetch_skips_known_noisy_domains():
    assert not _should_fetch("https://publish.twitter.com/embed")
    assert not _should_fetch("https://platform.twitter.com/widgets.js")


def test_should_fetch_allows_normal_urls():
    assert _should_fetch("https://example.com/article")
    assert _should_fetch("https://github.com/owner/repo")
    assert _should_fetch("https://x.com/user/status/123")


# ---------------------------------------------------------------------------
# tco regex
# ---------------------------------------------------------------------------

def test_tco_regex_matches_clean():
    assert _TCO_RE.findall("https://t.co/AbCdEfGhIj") == ["https://t.co/AbCdEfGhIj"]


def test_tco_regex_stops_at_html():
    html = '<a href="https://t.co/AbCdEfGhIj">link</a>'
    matches = _TCO_RE.findall(html)
    assert matches == ["https://t.co/AbCdEfGhIj"]


def test_tco_regex_no_false_positive():
    assert _TCO_RE.findall("https://twitter.com/user/status/123") == []


# ---------------------------------------------------------------------------
# enrich_step_with_urls — mocked network
# ---------------------------------------------------------------------------

def _make_fetch_map(url_to_content: dict):
    """Return a fetch_url_content mock that looks up urls in the map."""
    def mock_fetch(url):
        return url_to_content.get(url, f"[no mock for {url}]")
    return mock_fetch


def test_enrich_no_urls():
    result = enrich_step_with_urls("Just do some arithmetic: 2 + 2")
    assert result == ""


def test_enrich_skips_image_urls():
    result = enrich_step_with_urls("Look at https://example.com/photo.png")
    assert result == ""


def test_enrich_includes_fetched_content(monkeypatch):
    monkeypatch.setattr(
        web_fetch, "fetch_url_content",
        lambda url: f"[Content from {url}]\nSome article text."
    )
    step = "Read the article at https://example.com/article and summarise it"
    block = enrich_step_with_urls(step)
    assert "PRE-FETCHED URL CONTENT" in block
    assert "Some article text." in block
    assert "do NOT call WebFetch" in block


def test_enrich_caps_at_max_urls(monkeypatch):
    monkeypatch.setattr(
        web_fetch, "fetch_url_content",
        lambda url: f"[Content from {url}]\nText."
    )
    # 10 URLs — should only fetch max_urls (default 5)
    urls = " ".join(f"https://example.com/page{i}" for i in range(10))
    block = enrich_step_with_urls(f"Read all these: {urls}", max_urls=3)
    # At most 3 fetches
    count = block.count("[Content from")
    assert count <= 3


def test_enrich_returns_empty_on_all_failures(monkeypatch):
    monkeypatch.setattr(web_fetch, "fetch_url_content", lambda url: "")
    step = "Read https://example.com/something"
    result = enrich_step_with_urls(step)
    assert result == ""


def test_enrich_end_to_end_header_format(monkeypatch):
    monkeypatch.setattr(
        web_fetch, "fetch_url_content",
        lambda url: "[Content from https://x.com/foo/status/1]\nTweet text here."
    )
    block = enrich_step_with_urls("Analyse https://x.com/foo/status/1")
    assert block.startswith("=== PRE-FETCHED URL CONTENT ===")
    assert block.endswith("=== END PRE-FETCHED CONTENT ===")


# ---------------------------------------------------------------------------
# X article routing
# ---------------------------------------------------------------------------

def test_x_article_regex_matches():
    assert _X_ARTICLE_RE.search("https://x.com/i/article/1234567890")
    assert _X_ARTICLE_RE.search("https://twitter.com/i/article/9876543210")


def test_x_article_regex_no_false_positives():
    assert not _X_ARTICLE_RE.search("https://x.com/user/status/123")
    assert not _X_ARTICLE_RE.search("https://x.com/i/web/status/123")


_CLI_TWEET_CONTENT = "# X CLI Capture (12345)\n\n- Author: Test User (@testuser)\n\n## Content\nAuthenticated tweet content fetched via Poe's X session. More text here."


def test_fetch_x_article_returns_notice():
    result = fetch_x_article("https://x.com/i/article/123")
    assert "X Article" in result
    # Should explain why it's inaccessible and suggest alternatives
    assert any(kw in result.lower() for kw in ["not accessible", "cannot", "not available", "javascript"])


def test_fetch_x_article_includes_url():
    result = fetch_x_article("https://x.com/i/article/9876543210")
    assert "9876543210" in result


def test_fetch_url_content_routes_x_article():
    result = fetch_url_content("https://x.com/i/article/9876543210")
    assert "X Article" in result
    assert "9876543210" in result


def test_fetch_tweet_uses_cli_first(monkeypatch):
    """When CLI is available and returns content, it should be used before direct fetch.

    Jina is tried first in the code, so stub it to return nothing (hermetic — no
    live network) and assert the CLI fallback is what populates the result.
    """
    monkeypatch.setattr(web_fetch, "_jina_fetch", lambda *a, **k: "")
    monkeypatch.setattr(web_fetch, "_x_cli_available", lambda: True)
    monkeypatch.setattr(web_fetch, "_fetch_via_x_cli", lambda cmd, url: _CLI_TWEET_CONTENT)
    result = fetch_url_content("https://x.com/user/status/12345")
    assert "Authenticated tweet content" in result
    assert "authenticated CLI" in result


# ---------------------------------------------------------------------------
# _fetch_x_thread_direct — reply-aware CLI rung (BACKLOG #26)
# ---------------------------------------------------------------------------

_THREAD_JSON = """{"ok": true, "schema_version": 1, "data": [
  {"id": "100", "text": "This repo unlocks 271 FREE skills.\\n\\nRepo\\ud83d\\udc47",
   "author": {"screenName": "origauthor", "name": "Orig Author"},
   "createdAtISO": "2026-07-16T17:22:38Z",
   "metrics": {"likes": 129, "retweets": 17, "replies": 7, "views": 7544},
   "urls": []},
  {"id": "101", "text": "Repo:\\nhttps://t.co/abc",
   "author": {"screenName": "origauthor"},
   "urls": ["https://github.com/example/awesome-skills"]},
  {"id": "102", "text": "@origauthor looks neat but does it survive a real repo?",
   "author": {"screenName": "somereplier"}, "urls": []},
  {"id": "103", "text": "Trade crypto in one app.",
   "author": {"screenName": "AdvertiserApp"}, "urls": []}
]}"""


def _fake_cli_run(stdout, returncode=0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = ""
    return r


def _wire_thread_cli(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/twitter")
    monkeypatch.setattr(web_fetch, "_x_cookie_env",
                        lambda: {"PATH": "/usr/bin", "TWITTER_AUTH_TOKEN": "x",
                                 "TWITTER_CT0": "y"})
    monkeypatch.setattr(web_fetch.subprocess, "run",
                        lambda *a, **kw: _fake_cli_run(stdout, returncode))


def test_thread_direct_surfaces_author_followup_links(monkeypatch):
    """The author's self-reply (the "Repo👇" payload) must appear with its
    pre-resolved link — the exact content runs 1dac0e17/75a88777 missed."""
    _wire_thread_cli(monkeypatch, _THREAD_JSON)
    out = web_fetch._fetch_x_thread_direct("origauthor", "100")
    assert "Author follow-up posts (1)" in out
    assert "https://github.com/example/awesome-skills" in out
    assert "271 FREE skills" in out                       # root text
    assert "Metrics: likes 129" in out
    assert "somereplier" in out                           # other replies kept
    assert "Replies from others (2 of 2 shown)" in out


def test_thread_direct_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert web_fetch._fetch_x_thread_direct("h", "1") == ""


def test_thread_direct_no_cookies_returns_empty(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/twitter")
    monkeypatch.setattr(web_fetch, "_x_cookie_env", lambda: {})
    assert web_fetch._fetch_x_thread_direct("h", "1") == ""


def test_thread_direct_garbage_output_returns_empty(monkeypatch):
    _wire_thread_cli(monkeypatch, "WARNING something broke, no json here")
    assert web_fetch._fetch_x_thread_direct("h", "1") == ""


def test_thread_direct_nonzero_exit_returns_empty(monkeypatch):
    _wire_thread_cli(monkeypatch, _THREAD_JSON, returncode=1)
    assert web_fetch._fetch_x_thread_direct("h", "1") == ""


def test_fetch_x_tweet_prefers_thread_rung(monkeypatch):
    """Rung 0 must run BEFORE Jina — a root-only Jina success would return
    early and hide the reply thread."""
    monkeypatch.setattr(web_fetch, "_fetch_x_thread_direct",
                        lambda h, t: "THREAD CONTENT")
    called = {"jina": False}
    def _jina(url, max_chars=8000):
        called["jina"] = True
        return "x" * 300
    monkeypatch.setattr(web_fetch, "_jina_fetch", _jina)
    out = web_fetch.fetch_x_tweet("https://x.com/someone/status/100")
    assert "with replies" in out and "THREAD CONTENT" in out
    assert called["jina"] is False


def test_fetch_x_tweet_falls_through_when_thread_empty(monkeypatch):
    monkeypatch.setattr(web_fetch, "_fetch_x_thread_direct", lambda h, t: "")
    monkeypatch.setattr(web_fetch, "_jina_fetch",
                        lambda url, max_chars=8000: "j" * 300)
    out = web_fetch.fetch_x_tweet("https://x.com/someone/status/100")
    assert "via Jina" in out
