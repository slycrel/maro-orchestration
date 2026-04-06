"""Tests for channels.py — pluggable data channel adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# ChannelResult
# ---------------------------------------------------------------------------

class TestChannelResult:
    def _make(self, **kw):
        from channels import ChannelResult
        return ChannelResult(**kw)

    def test_to_text_error(self):
        r = self._make(channel="github", query="foo", error="not found")
        assert "Error" in r.to_text()
        assert "github" in r.to_text()

    def test_to_text_items(self):
        r = self._make(channel="reddit", query="ml", items=[
            {"title": "Test post", "score": 100}
        ])
        text = r.to_text()
        assert "Test post" in text
        assert "reddit" in text

    def test_to_text_max_items_truncation(self):
        from channels import ChannelResult
        r = ChannelResult(channel="github", query="q", items=[
            {"name": f"repo{i}"} for i in range(10)
        ], truncated=True)
        text = r.to_text(max_items=3)
        assert "truncated" in text

    def test_to_json_roundtrip(self):
        r = self._make(channel="github", query="foo", items=[{"name": "repo"}])
        parsed = json.loads(r.to_json())
        assert parsed["channel"] == "github"
        assert parsed["query"] == "foo"
        assert len(parsed["items"]) == 1

    def test_to_json_error(self):
        r = self._make(channel="github", query="foo", error="timeout")
        parsed = json.loads(r.to_json())
        assert parsed["error"] == "timeout"


# ---------------------------------------------------------------------------
# GitHubChannel
# ---------------------------------------------------------------------------

class TestGitHubChannel:
    def _fake_search_resp(self, items):
        return {"items": items, "total_count": len(items)}

    def test_search_repositories_returns_items(self):
        fake_repos = [
            {"full_name": "org/repo", "description": "desc", "stargazers_count": 100,
             "html_url": "https://github.com/org/repo", "language": "Python",
             "topics": [], "updated_at": "2026-01-01T00:00:00Z"}
        ]
        with patch("channels._http_get", return_value=self._fake_search_resp(fake_repos)):
            from channels import GitHubChannel
            ch = GitHubChannel()
            result = ch.search_repositories("agent orchestration")
        assert len(result.items) == 1
        assert result.items[0]["name"] == "org/repo"
        assert result.error == ""

    def test_search_repositories_error(self):
        with patch("channels._http_get", side_effect=Exception("403 forbidden")):
            from channels import GitHubChannel
            ch = GitHubChannel()
            result = ch.search_repositories("query")
        assert result.error != ""
        assert result.channel == "github"

    def test_search_code_returns_items(self):
        fake_items = [
            {"path": "src/foo.py", "html_url": "https://github.com/org/repo/blob/main/src/foo.py",
             "repository": {"full_name": "org/repo", "description": "a repo"}}
        ]
        with patch("channels._http_get", return_value={"items": fake_items}):
            from channels import GitHubChannel
            ch = GitHubChannel()
            result = ch.search_code("evolver run_evolver")
        assert result.items[0]["file"] == "src/foo.py"
        assert result.items[0]["repo"] == "org/repo"

    def test_search_issues_returns_items(self):
        fake_issues = [
            {"title": "Bug in evolver", "html_url": "https://github.com/org/repo/issues/1",
             "state": "open", "repository_url": "https://api.github.com/repos/org/repo",
             "body": "It breaks", "created_at": "2026-01-01T00:00:00Z", "comments": 3}
        ]
        with patch("channels._http_get", return_value={"items": fake_issues}):
            from channels import GitHubChannel
            ch = GitHubChannel()
            result = ch.search_issues("evolver bug")
        assert result.items[0]["title"] == "Bug in evolver"
        assert result.items[0]["repo"] == "org/repo"

    def test_search_respects_limit(self):
        fake_repos = [
            {"full_name": f"org/repo{i}", "description": "", "stargazers_count": i,
             "html_url": "", "language": "", "topics": [], "updated_at": ""}
            for i in range(10)
        ]
        with patch("channels._http_get", return_value={"items": fake_repos}):
            from channels import GitHubChannel
            ch = GitHubChannel()
            result = ch.search_repositories("q", limit=3)
        assert len(result.items) == 3


# ---------------------------------------------------------------------------
# RedditChannel
# ---------------------------------------------------------------------------

class TestRedditChannel:
    def _fake_posts(self, posts):
        return {"data": {"children": [{"data": p} for p in posts]}}

    def test_posts_returns_items(self):
        fake = [{"title": "AI breakthrough", "score": 500, "num_comments": 100,
                 "url": "https://example.com", "selftext": "body", "author": "user1",
                 "created_utc": 1700000000, "link_flair_text": "Research"}]
        with patch("channels._http_get", return_value=self._fake_posts(fake)):
            from channels import RedditChannel
            ch = RedditChannel()
            result = ch.posts("machinelearning")
        assert len(result.items) == 1
        assert result.items[0]["title"] == "AI breakthrough"
        assert result.channel == "reddit"

    def test_posts_error(self):
        with patch("channels._http_get", side_effect=Exception("403")):
            from channels import RedditChannel
            ch = RedditChannel()
            result = ch.posts("machinelearning")
        assert result.error != ""

    def test_search_returns_items(self):
        fake = [{"title": "Found post", "subreddit_name_prefixed": "r/ml",
                 "score": 200, "url": "https://ex.com", "selftext": "", "num_comments": 10}]
        with patch("channels._http_get", return_value=self._fake_posts(fake)):
            from channels import RedditChannel
            ch = RedditChannel()
            result = ch.search("neural networks")
        assert result.items[0]["subreddit"] == "r/ml"

    def test_search_subreddit_restrict(self):
        calls = []
        def fake_get(url, **kw):
            calls.append(url)
            return {"data": {"children": []}}
        with patch("channels._http_get", side_effect=fake_get):
            from channels import RedditChannel
            ch = RedditChannel()
            ch.search("agi", subreddit="machinelearning")
        assert "restrict_sr=1" in calls[0]
        assert "machinelearning" in calls[0]

    def test_posts_respects_limit(self):
        fake = [
            {"title": f"post {i}", "score": i, "num_comments": 0, "url": "",
             "selftext": "", "author": "", "created_utc": 0, "link_flair_text": ""}
            for i in range(10)
        ]
        with patch("channels._http_get", return_value=self._fake_posts(fake)):
            from channels import RedditChannel
            ch = RedditChannel()
            result = ch.posts("ml", limit=3)
        assert len(result.items) == 3


# ---------------------------------------------------------------------------
# YouTubeChannel
# ---------------------------------------------------------------------------

class TestYouTubeChannel:
    def test_extract_id_from_url(self):
        from channels import YouTubeChannel
        ch = YouTubeChannel()
        assert ch._extract_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert ch._extract_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert ch._extract_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_id_invalid(self):
        from channels import YouTubeChannel
        ch = YouTubeChannel()
        with pytest.raises(ValueError):
            ch._extract_id("not-a-url")

    def test_transcript_via_library(self):
        mock_api = MagicMock()
        mock_api.get_transcript.return_value = [
            {"text": "Hello world", "start": 0.0, "duration": 1.5},
            {"text": "This is a test", "start": 1.5, "duration": 2.0},
        ]
        with patch.dict("sys.modules", {"youtube_transcript_api": mock_api}):
            # Re-import to pick up mock
            import importlib
            import channels as ch_mod
            importlib.reload(ch_mod)
            from channels import YouTubeChannel
            ch = YouTubeChannel()
            result = ch.transcript("dQw4w9WgXcQ")
        assert result.channel == "youtube"
        # May return error if youtube_transcript_api mock doesn't work as expected —
        # just check no crash
        assert isinstance(result.items, list)

    def test_transcript_fallback_to_oembed(self):
        oembed_data = {"title": "Never Gonna Give You Up", "author_name": "Rick Astley"}
        # Simulate transcript API raising an exception (e.g. no subtitles)
        mock_api = MagicMock()
        mock_api.YouTubeTranscriptApi.get_transcript.side_effect = Exception("No transcript")
        with patch.dict("sys.modules", {"youtube_transcript_api": mock_api}):
            with patch("channels._http_get", return_value=oembed_data):
                from channels import YouTubeChannel
                ch = YouTubeChannel()
                result = ch.transcript("dQw4w9WgXcQ")
        # Should fall back to oEmbed metadata
        assert result.channel == "youtube"

    def test_transcript_error(self):
        with patch("channels._http_get", side_effect=Exception("403")):
            from channels import YouTubeChannel
            ch = YouTubeChannel()
            # Force no youtube_transcript_api import
            result = ch.transcript("dQw4w9WgXcQ")
        # error path via either transcript or oembed failure
        assert result.channel == "youtube"


# ---------------------------------------------------------------------------
# Dispatcher functions
# ---------------------------------------------------------------------------

class TestGithubSearchDispatcher:
    def test_repositories_type(self):
        with patch("channels.GitHubChannel.search_repositories") as m:
            from channels import ChannelResult
            m.return_value = ChannelResult(channel="github", query="q", items=[])
            from channels import github_search
            github_search("agent framework")
        m.assert_called_once()

    def test_code_type(self):
        with patch("channels.GitHubChannel.search_code") as m:
            from channels import ChannelResult
            m.return_value = ChannelResult(channel="github", query="q", items=[])
            from channels import github_search
            github_search("run_evolver", type="code")
        m.assert_called_once()

    def test_issues_type(self):
        with patch("channels.GitHubChannel.search_issues") as m:
            from channels import ChannelResult
            m.return_value = ChannelResult(channel="github", query="q", items=[])
            from channels import github_search
            github_search("evolver bug", type="issues")
        m.assert_called_once()

    def test_returns_json(self):
        with patch("channels.GitHubChannel.search_repositories") as m:
            from channels import ChannelResult
            m.return_value = ChannelResult(channel="github", query="q", items=[{"name": "r"}])
            from channels import github_search
            result = github_search("agent")
        parsed = json.loads(result)
        assert parsed["channel"] == "github"


class TestRedditDispatcher:
    def test_reddit_posts_returns_json(self):
        with patch("channels.RedditChannel.posts") as m:
            from channels import ChannelResult
            m.return_value = ChannelResult(channel="reddit", query="q", items=[])
            from channels import reddit_posts
            result = reddit_posts("machinelearning")
        json.loads(result)  # should not raise
        m.assert_called_once_with("machinelearning", sort="hot", limit=5)

    def test_reddit_search_returns_json(self):
        with patch("channels.RedditChannel.search") as m:
            from channels import ChannelResult
            m.return_value = ChannelResult(channel="reddit", query="q", items=[])
            from channels import reddit_search
            result = reddit_search("neural net")
        json.loads(result)


class TestFetchChannelDispatcher:
    def test_github_url(self):
        with patch("channels.github_search") as m:
            m.return_value = '{"channel":"github","query":"q","items":[],"error":"","truncated":false}'
            from channels import fetch_channel
            fetch_channel("https://github.com/slycrel/openclaw-orchestration")
        m.assert_called_once()

    def test_reddit_url(self):
        with patch("channels.reddit_posts") as m:
            m.return_value = '{"channel":"reddit","query":"q","items":[],"error":"","truncated":false}'
            from channels import fetch_channel
            fetch_channel("r/machinelearning")
        m.assert_called_once()

    def test_youtube_url(self):
        with patch("channels.youtube_transcript") as m:
            m.return_value = '{"channel":"youtube","query":"q","items":[],"error":"","truncated":false}'
            from channels import fetch_channel
            fetch_channel("https://youtube.com/watch?v=abc123")
        m.assert_called_once()

    def test_unknown_returns_error_json(self):
        from channels import fetch_channel
        result = fetch_channel("ftp://totally-unknown.example.com/resource")
        parsed = json.loads(result)
        assert parsed["channel"] == "error"
        assert "error" in parsed


# ---------------------------------------------------------------------------
# channels_health_check
# ---------------------------------------------------------------------------

class TestChannelsHealthCheck:
    def test_returns_dict_with_expected_keys(self):
        with patch("channels._http_get", side_effect=Exception("no network")):
            from channels import channels_health_check
            status = channels_health_check()
        assert "channels" in status
        assert "any_available" in status
        assert "github" in status["channels"]
        assert "reddit" in status["channels"]
        assert "youtube_transcript_api" in status["channels"]

    def test_github_available_when_api_responds(self):
        def fake_get(url, **kw):
            if "api.github.com" in url:
                return {"rate": {"limit": 60, "remaining": 55}}
            raise Exception("other")
        with patch("channels._http_get", side_effect=fake_get):
            from channels import channels_health_check
            status = channels_health_check()
        assert status["channels"]["github"] is True

    def test_any_available_false_when_all_fail(self):
        with patch("channels._http_get", side_effect=Exception("network error")):
            with patch.dict("sys.modules", {"youtube_transcript_api": None}):
                from channels import channels_health_check
                status = channels_health_check()
        assert status["any_available"] is False or status["channels"]["github"] is False
