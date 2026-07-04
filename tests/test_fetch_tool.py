"""Tests for the unified fetch tool (BACKLOG fetch unification). No network —
every backend is monkeypatched."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetch_tool as ft  # noqa: E402


class TestRouting:
    def test_auto_url_routes_to_generic_chain(self, monkeypatch):
        import web_fetch
        monkeypatch.setattr(web_fetch, "fetch_url_content",
                            lambda url: f"[generic:{url}]")
        out = ft.fetch("https://example.com/a")
        assert out == "[generic:https://example.com/a]"

    def test_auto_x_url_goes_through_generic_chain(self, monkeypatch):
        # fetch_url_content owns the X special-casing (oEmbed chain) — the
        # facade must NOT re-route around it.
        import web_fetch
        seen = []
        monkeypatch.setattr(web_fetch, "fetch_url_content",
                            lambda url: seen.append(url) or "[x-chain]")
        assert ft.fetch("https://x.com/user/status/123") == "[x-chain]"
        assert seen == ["https://x.com/user/status/123"]

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://youtube.com/shorts/abc123",
    ])
    def test_auto_youtube_routes_to_transcript(self, monkeypatch, url):
        import channels
        monkeypatch.setattr(channels, "youtube_transcript",
                            lambda t, **kw: f"[yt:{t}]")
        assert ft.fetch(url) == f"[yt:{url}]"

    def test_auto_non_url_is_a_helpful_error(self):
        out = ft.fetch("agent orchestration frameworks")
        assert out.startswith("[fetch:")
        assert "github_repos" in out

    @pytest.mark.parametrize("mode,gh_type", [
        ("github_repos", "repositories"),
        ("github_code", "code"),
        ("github_issues", "issues"),
    ])
    def test_github_modes(self, monkeypatch, mode, gh_type):
        import channels
        calls = []
        monkeypatch.setattr(channels, "github_search",
                            lambda q, type, limit: calls.append((q, type, limit)) or "[gh]")
        assert ft.fetch("query", mode=mode, limit=3) == "[gh]"
        assert calls == [("query", gh_type, 3)]

    def test_reddit_modes(self, monkeypatch):
        import channels
        monkeypatch.setattr(channels, "reddit_posts", lambda s, limit: f"[posts:{s}:{limit}]")
        monkeypatch.setattr(channels, "reddit_search", lambda q, limit: f"[search:{q}:{limit}]")
        assert ft.fetch("LocalLLaMA", mode="reddit_posts") == "[posts:LocalLLaMA:5]"
        assert ft.fetch("agent memory", mode="reddit_search", limit=2) == "[search:agent memory:2]"

    def test_unknown_mode_and_empty_target(self):
        assert "unknown mode" in ft.fetch("x", mode="gopher")
        assert ft.fetch("") == "[fetch: empty target]"

    def test_backend_exception_becomes_message(self, monkeypatch):
        import web_fetch
        def _boom(url):
            raise RuntimeError("socket exploded")
        monkeypatch.setattr(web_fetch, "fetch_url_content", _boom)
        out = ft.fetch("https://example.com")
        assert out.startswith("[fetch failed") and "socket exploded" in out


class TestHandler:
    def test_handler_coerces_inputs(self, monkeypatch):
        import channels
        monkeypatch.setattr(channels, "reddit_posts", lambda s, limit: f"[{s}:{limit}]")
        out = ft.fetch_handler({"target": "LocalLLaMA", "mode": "reddit_posts",
                                "limit": "7"})
        assert out == "[LocalLLaMA:7]"

    def test_handler_tolerates_garbage(self):
        assert ft.fetch_handler({}) == "[fetch: empty target]"
        out = ft.fetch_handler({"target": "https://e.co", "limit": "lots",
                                "mode": None})
        assert isinstance(out, str)


class TestRegistryIntegration:
    def test_fetch_registered_with_handler(self):
        from tool_registry import registry
        td = registry.get("fetch")
        assert td is not None
        assert callable(getattr(td, "_handler", None))

    def test_worker_sees_fetch_verifier_does_not(self):
        from tool_registry import registry, worker_context, PermissionContext, ROLE_VERIFIER
        worker_names = [t.name for t in registry.get_tools(worker_context())]
        assert "fetch" in worker_names
        verifier_names = [t.name for t in
                          registry.get_tools(PermissionContext(role=ROLE_VERIFIER))]
        assert "fetch" not in verifier_names

    def test_resolve_and_call_dispatches(self, monkeypatch):
        import web_fetch
        monkeypatch.setattr(web_fetch, "fetch_url_content", lambda url: "[ok]")
        from tool_registry import registry
        assert registry.resolve_and_call("fetch", {"target": "https://e.co"}) == "[ok]"
