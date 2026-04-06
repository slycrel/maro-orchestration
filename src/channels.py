"""Pluggable data channel adapters (Agent-Reach steal).

Provides platform-specific structured access to GitHub, Reddit, and YouTube —
complementing web_fetch.py (which handles generic URL scraping via Jina).

Each channel has a standard interface:
    result: ChannelResult = channel.fetch(query_or_url, **kwargs)

Unified dispatcher:
    result = fetch_channel(query_or_url)  # auto-detects platform

Tools registered for agent use:
    github_search(query, type="repositories")  -> ChannelResult
    reddit_posts(subreddit, sort="hot", limit=5) -> ChannelResult
    youtube_transcript(video_url_or_id) -> ChannelResult
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.channels")

_USER_AGENT = "poe-orchestration/1.0 (research; github.com/slycrel/openclaw-orchestration)"
_TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ChannelResult:
    """Structured result from a data channel fetch."""
    channel: str          # "github" | "reddit" | "youtube" | "error"
    query: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    truncated: bool = False  # True if results were trimmed to fit context

    def to_text(self, max_items: Optional[int] = None) -> str:
        """Render as LLM-friendly text block."""
        if self.error:
            return f"[{self.channel}] Error: {self.error}"
        shown = self.items[:max_items] if max_items else self.items
        lines = [f"[{self.channel}] {len(self.items)} result(s) for: {self.query!r}"]
        for i, item in enumerate(shown):
            lines.append(f"\n--- Result {i+1} ---")
            for k, v in item.items():
                if v:
                    lines.append(f"{k}: {str(v)[:300]}")
        if self.truncated:
            lines.append(f"\n[truncated — {len(self.items) - len(shown)} more items omitted]")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "channel": self.channel,
            "query": self.query,
            "items": self.items,
            "error": self.error,
            "truncated": self.truncated,
        })


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: Optional[Dict[str, str]] = None) -> dict:
    """Fetch URL and parse JSON. Raises urllib.error.HTTPError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# GitHub channel
# ---------------------------------------------------------------------------

class GitHubChannel:
    """GitHub REST API v3 — no auth required for public repos (60 req/hour)."""

    BASE = "https://api.github.com"

    def search_repositories(
        self,
        query: str,
        *,
        sort: str = "stars",
        order: str = "desc",
        limit: int = 5,
    ) -> ChannelResult:
        """Search public GitHub repositories."""
        params = urllib.parse.urlencode({
            "q": query, "sort": sort, "order": order, "per_page": min(limit, 10)
        })
        url = f"{self.BASE}/search/repositories?{params}"
        try:
            data = _http_get(url, headers={"Accept": "application/vnd.github.v3+json"})
        except Exception as exc:
            return ChannelResult(channel="github", query=query, error=str(exc)[:200])

        items = []
        for repo in data.get("items", [])[:limit]:
            items.append({
                "name": repo.get("full_name", ""),
                "description": repo.get("description", ""),
                "stars": repo.get("stargazers_count", 0),
                "url": repo.get("html_url", ""),
                "language": repo.get("language", ""),
                "topics": ", ".join(repo.get("topics", [])[:5]),
                "updated": repo.get("updated_at", "")[:10],
            })

        return ChannelResult(channel="github", query=query, items=items)

    def search_code(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> ChannelResult:
        """Search code across public GitHub repos."""
        params = urllib.parse.urlencode({"q": query, "per_page": min(limit, 10)})
        url = f"{self.BASE}/search/code?{params}"
        try:
            data = _http_get(url, headers={"Accept": "application/vnd.github.v3+json"})
        except Exception as exc:
            return ChannelResult(channel="github", query=query, error=str(exc)[:200])

        items = []
        for item in data.get("items", [])[:limit]:
            repo = item.get("repository", {})
            items.append({
                "file": item.get("path", ""),
                "repo": repo.get("full_name", ""),
                "url": item.get("html_url", ""),
                "repo_description": repo.get("description", ""),
            })

        return ChannelResult(channel="github", query=query, items=items)

    def search_issues(
        self,
        query: str,
        *,
        state: str = "open",
        limit: int = 5,
    ) -> ChannelResult:
        """Search GitHub issues and PRs."""
        full_query = f"{query} state:{state}"
        params = urllib.parse.urlencode({"q": full_query, "per_page": min(limit, 10)})
        url = f"{self.BASE}/search/issues?{params}"
        try:
            data = _http_get(url, headers={"Accept": "application/vnd.github.v3+json"})
        except Exception as exc:
            return ChannelResult(channel="github", query=query, error=str(exc)[:200])

        items = []
        for issue in data.get("items", [])[:limit]:
            items.append({
                "title": issue.get("title", ""),
                "repo": issue.get("repository_url", "").replace(f"{self.BASE}/repos/", ""),
                "state": issue.get("state", ""),
                "url": issue.get("html_url", ""),
                "body_preview": (issue.get("body") or "")[:200],
                "created": (issue.get("created_at") or "")[:10],
                "comments": issue.get("comments", 0),
            })

        return ChannelResult(channel="github", query=query, items=items)


# ---------------------------------------------------------------------------
# Reddit channel
# ---------------------------------------------------------------------------

class RedditChannel:
    """Reddit public JSON API — no auth required for reading public subreddits."""

    BASE = "https://www.reddit.com"

    def posts(
        self,
        subreddit: str,
        *,
        sort: str = "hot",
        limit: int = 5,
        time_filter: str = "week",
    ) -> ChannelResult:
        """Fetch top posts from a subreddit.

        Args:
            subreddit:   Subreddit name (without /r/ prefix).
            sort:        "hot" | "new" | "top" | "rising"
            limit:       Number of posts (max 25).
            time_filter: For sort=top: "hour" | "day" | "week" | "month" | "year" | "all"
        """
        params = {"limit": min(limit, 25)}
        if sort == "top":
            params["t"] = time_filter
        query_str = urllib.parse.urlencode(params)
        url = f"{self.BASE}/r/{subreddit}/{sort}.json?{query_str}"

        try:
            data = _http_get(url)
        except Exception as exc:
            return ChannelResult(channel="reddit", query=f"r/{subreddit}", error=str(exc)[:200])

        items = []
        posts = data.get("data", {}).get("children", [])
        for post_wrapper in posts[:limit]:
            post = post_wrapper.get("data", {})
            items.append({
                "title": post.get("title", ""),
                "score": post.get("score", 0),
                "comments": post.get("num_comments", 0),
                "url": post.get("url", ""),
                "selftext_preview": (post.get("selftext") or "")[:300],
                "author": post.get("author", ""),
                "created": post.get("created_utc", ""),
                "flair": post.get("link_flair_text") or "",
            })

        return ChannelResult(channel="reddit", query=f"r/{subreddit}/{sort}", items=items)

    def search(
        self,
        query: str,
        *,
        subreddit: Optional[str] = None,
        sort: str = "relevance",
        limit: int = 5,
        time_filter: str = "year",
    ) -> ChannelResult:
        """Search Reddit posts."""
        params = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": min(limit, 25),
            "type": "link",
        }
        if subreddit:
            url = f"{self.BASE}/r/{subreddit}/search.json?{urllib.parse.urlencode(params)}&restrict_sr=1"
        else:
            url = f"{self.BASE}/search.json?{urllib.parse.urlencode(params)}"

        try:
            data = _http_get(url)
        except Exception as exc:
            return ChannelResult(channel="reddit", query=query, error=str(exc)[:200])

        items = []
        posts = data.get("data", {}).get("children", [])
        for post_wrapper in posts[:limit]:
            post = post_wrapper.get("data", {})
            items.append({
                "title": post.get("title", ""),
                "subreddit": post.get("subreddit_name_prefixed", ""),
                "score": post.get("score", 0),
                "url": post.get("url", ""),
                "selftext_preview": (post.get("selftext") or "")[:300],
                "comments": post.get("num_comments", 0),
            })

        return ChannelResult(channel="reddit", query=query, items=items)


# ---------------------------------------------------------------------------
# YouTube channel
# ---------------------------------------------------------------------------

class YouTubeChannel:
    """YouTube transcript fetching (no API key required)."""

    _ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")

    def _extract_id(self, url_or_id: str) -> str:
        m = self._ID_RE.search(url_or_id)
        if m:
            return m.group(1)
        if re.match(r"^[A-Za-z0-9_-]{11}$", url_or_id):
            return url_or_id
        raise ValueError(f"Cannot extract YouTube video ID from: {url_or_id!r}")

    def transcript(
        self,
        url_or_id: str,
        *,
        max_chars: int = 4000,
    ) -> ChannelResult:
        """Fetch YouTube video transcript via youtube-transcript-api if available,
        falling back to page metadata only.

        Args:
            url_or_id: YouTube URL or video ID.
            max_chars: Maximum characters of transcript to include.
        """
        query = url_or_id
        try:
            video_id = self._extract_id(url_or_id)
        except ValueError as exc:
            return ChannelResult(channel="youtube", query=query, error=str(exc))

        # Try youtube-transcript-api first
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
            entries = YouTubeTranscriptApi.get_transcript(video_id)
            full_text = " ".join(e["text"] for e in entries)
            truncated = len(full_text) > max_chars
            return ChannelResult(
                channel="youtube",
                query=query,
                items=[{
                    "video_id": video_id,
                    "transcript": full_text[:max_chars],
                    "word_count": len(full_text.split()),
                }],
                truncated=truncated,
            )
        except ImportError:
            pass
        except Exception as exc:
            return ChannelResult(channel="youtube", query=query,
                                  error=f"transcript unavailable: {str(exc)[:150]}")

        # Fallback: return video page metadata via oEmbed
        oembed_url = f"https://www.youtube.com/oembed?url=https://youtu.be/{video_id}&format=json"
        try:
            data = _http_get(oembed_url)
            return ChannelResult(
                channel="youtube",
                query=query,
                items=[{
                    "video_id": video_id,
                    "title": data.get("title", ""),
                    "author": data.get("author_name", ""),
                    "note": "Transcript unavailable (install youtube-transcript-api). Title/author only.",
                }],
            )
        except Exception as exc:
            return ChannelResult(channel="youtube", query=query,
                                  error=f"oEmbed failed: {str(exc)[:150]}")


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------

_github = GitHubChannel()
_reddit = RedditChannel()
_youtube = YouTubeChannel()


def github_search(query: str, *, type: str = "repositories", limit: int = 5) -> str:
    """Search GitHub. type: repositories | code | issues.

    Returns JSON string of ChannelResult.
    """
    if type == "code":
        result = _github.search_code(query, limit=limit)
    elif type == "issues":
        result = _github.search_issues(query, limit=limit)
    else:
        result = _github.search_repositories(query, limit=limit)
    return result.to_json()


def reddit_posts(subreddit: str, *, sort: str = "hot", limit: int = 5) -> str:
    """Fetch top posts from a Reddit subreddit. sort: hot | new | top | rising.

    Returns JSON string of ChannelResult.
    """
    result = _reddit.posts(subreddit, sort=sort, limit=limit)
    return result.to_json()


def reddit_search(query: str, *, subreddit: Optional[str] = None, limit: int = 5) -> str:
    """Search Reddit. Optionally restrict to a specific subreddit.

    Returns JSON string of ChannelResult.
    """
    result = _reddit.search(query, subreddit=subreddit, limit=limit)
    return result.to_json()


def youtube_transcript(url_or_id: str, *, max_chars: int = 4000) -> str:
    """Fetch YouTube transcript (requires youtube-transcript-api). Falls back to metadata.

    Returns JSON string of ChannelResult.
    """
    result = _youtube.transcript(url_or_id, max_chars=max_chars)
    return result.to_json()


def fetch_channel(url_or_query: str) -> str:
    """Auto-dispatch to the right channel based on URL pattern.

    Detects: github.com → github_search; reddit.com or r/ → reddit_posts;
    youtube.com or youtu.be → youtube_transcript; else returns error.

    Returns JSON string of ChannelResult.
    """
    q = url_or_query.lower()
    if "github.com" in q:
        # Extract GitHub query from URL or pass directly
        m = re.search(r"github\.com/([^/]+/[^/?#]+)", url_or_query)
        query = m.group(1) if m else url_or_query
        return github_search(query, type="repositories")
    elif "reddit.com" in q or q.startswith("r/"):
        m = re.search(r"r/([A-Za-z0-9_]+)", url_or_query)
        if m:
            return reddit_posts(m.group(1))
        return reddit_search(url_or_query)
    elif "youtube.com" in q or "youtu.be" in q:
        return youtube_transcript(url_or_query)
    else:
        return ChannelResult(
            channel="error",
            query=url_or_query,
            error="Cannot auto-detect channel. Use github_search(), reddit_posts(), or youtube_transcript() directly.",
        ).to_json()


# ---------------------------------------------------------------------------
# Health check (for poe-doctor)
# ---------------------------------------------------------------------------

def channels_health_check() -> Dict[str, Any]:
    """Return health status for channel availability."""
    checks: Dict[str, bool] = {
        "github": False,
        "reddit": False,
        "youtube_transcript_api": False,
    }

    # GitHub: simple unauthenticated ping
    try:
        _http_get("https://api.github.com/rate_limit")
        checks["github"] = True
    except Exception:
        pass

    # Reddit: simple subreddit ping
    try:
        _http_get("https://www.reddit.com/r/machinelearning/hot.json?limit=1")
        checks["reddit"] = True
    except Exception:
        pass

    # youtube-transcript-api
    try:
        import youtube_transcript_api  # noqa: F401
        checks["youtube_transcript_api"] = True
    except ImportError:
        pass

    return {
        "channels": checks,
        "any_available": any(checks.values()),
    }
