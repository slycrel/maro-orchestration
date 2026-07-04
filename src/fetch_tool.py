"""Unified fetch — one entry point over the repo's three fetch implementations.

BACKLOG "Unify fragmented web/content-fetch capability": `web_fetch.py`
(generic URL via Jina/BS4 + the X/Twitter fallback chain), `channels.py`
(GitHub/Reddit/YouTube structured queries), and the external x-capture salvage
bridge grew as disconnected one-offs with different failure modes depending on
which path a goal happened to hit. This module is the single seam callers (and
the worker LLM, via the tool registry) should use.

    from fetch_tool import fetch
    fetch("https://example.com/article")                  # auto → generic URL
    fetch("https://x.com/user/status/123")                # auto → X chain
    fetch("https://youtube.com/watch?v=abc")              # auto → transcript
    fetch("agent orchestration", mode="github_repos")     # platform query
    fetch("LocalLLaMA", mode="reddit_posts")

Registered in the default tool registry as tool name `fetch` (worker role) —
advertised to API-path workers via get_tools_for_role and dispatchable through
`registry.resolve_and_call` (step_exec's registry branch). Subprocess workers
(`claude -p`) have their own fetch tools and don't need this.

Every mode returns a string and never raises — failures come back as
descriptive `[...]` messages, matching web_fetch's contract.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

log = logging.getLogger("maro.fetch_tool")

_YT_RE = re.compile(r"(?:youtube\.com/watch|youtube\.com/shorts/|youtu\.be/)", re.I)
_URL_RE = re.compile(r"^https?://", re.I)

MODES = (
    "auto", "url", "youtube",
    "github_repos", "github_code", "github_issues",
    "reddit_posts", "reddit_search",
)


def fetch(target: str, *, mode: str = "auto", limit: int = 5) -> str:
    """Fetch content from the web through one interface. Never raises.

    Args:
        target: URL (url/auto/youtube modes) or query/subreddit (platform modes).
        mode:   One of MODES. "auto" routes URLs by host (YouTube → transcript,
                everything else → the generic chain, which itself special-cases
                X/Twitter posts + articles including the oEmbed fallback).
        limit:  Max items for the platform-query modes.
    """
    target = (target or "").strip()
    if not target:
        return "[fetch: empty target]"
    mode = (mode or "auto").strip().lower()
    if mode not in MODES:
        return f"[fetch: unknown mode {mode!r} — valid: {', '.join(MODES)}]"

    try:
        if mode == "auto":
            if not _URL_RE.match(target):
                return (f"[fetch: {target!r} is not a URL — for platform queries "
                        f"use mode github_repos|github_code|github_issues|"
                        f"reddit_posts|reddit_search]")
            if _YT_RE.search(target):
                mode = "youtube"
            else:
                mode = "url"

        if mode == "url":
            from web_fetch import fetch_url_content
            return fetch_url_content(target)
        if mode == "youtube":
            from channels import youtube_transcript
            return youtube_transcript(target)
        if mode in ("github_repos", "github_code", "github_issues"):
            from channels import github_search
            _type = {"github_repos": "repositories", "github_code": "code",
                     "github_issues": "issues"}[mode]
            return github_search(target, type=_type, limit=limit)
        if mode == "reddit_posts":
            from channels import reddit_posts
            return reddit_posts(target, limit=limit)
        if mode == "reddit_search":
            from channels import reddit_search
            return reddit_search(target, limit=limit)
    except Exception as exc:
        log.debug("fetch failed (%s, mode=%s): %s", target[:80], mode, exc)
        return f"[fetch failed ({mode}): {exc}]"
    return f"[fetch: unhandled mode {mode!r}]"  # unreachable; MODES is closed


# ---------------------------------------------------------------------------
# Tool-registry integration
# ---------------------------------------------------------------------------

FETCH_TOOL_NAME = "fetch"

FETCH_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "URL to fetch, or query/subreddit for platform modes.",
        },
        "mode": {
            "type": "string",
            "enum": list(MODES),
            "description": ("auto (default): route URL by host — YouTube → "
                            "transcript, X/Twitter → tweet chain, else generic "
                            "page fetch. Platform query modes: github_repos / "
                            "github_code / github_issues (target = search "
                            "query), reddit_posts (target = subreddit), "
                            "reddit_search (target = query)."),
        },
        "limit": {
            "type": "integer",
            "description": "Max items for platform-query modes (default 5).",
        },
    },
    "required": ["target"],
}

FETCH_TOOL_DESCRIPTION = (
    "Fetch web content through one interface: generic URLs (Jina/clean-text "
    "chain), X/Twitter posts (oEmbed fallback), YouTube transcripts, GitHub "
    "repo/code/issue search, Reddit posts/search. Returns text; failures are "
    "descriptive [bracketed] messages, never exceptions."
)


def fetch_handler(input_data: Dict[str, Any]) -> str:
    """`_handler` entry for tool_registry.resolve_and_call."""
    data = input_data or {}
    try:
        limit = int(data.get("limit") or 5)
    except Exception:
        limit = 5
    return fetch(str(data.get("target") or ""),
                 mode=str(data.get("mode") or "auto"), limit=limit)
