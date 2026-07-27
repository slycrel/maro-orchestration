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

Two entry points, one implementation:
  - API-path workers: registered in the default tool registry as tool name
    `fetch` (worker role), advertised via get_tools_for_role and dispatchable
    through `registry.resolve_and_call` (step_exec's registry branch).
  - Subprocess (`claude -p`) workers: the `main()` CLI at the bottom of this
    file — `python3 src/fetch_tool.py <url>`. They have no tool registry, and
    WebFetch/WebSearch are disallowed for them (llm.py), so without the CLI
    their only page-reading affordance is Bash+curl. The docstring here used to
    claim they "have their own fetch tools and don't need this"; that was wrong,
    and the cost of it was a research step that curled raw retailer HTML for
    2.14M input tokens (docs/history/2026-07-27-tire-runs-examination.md).

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


# ---------------------------------------------------------------------------
# CLI — the seam for subprocess (`claude -p`) workers
# ---------------------------------------------------------------------------
#
# API-path workers reach `fetch` through the tool registry. Subprocess workers
# have no registry — their only affordance is Bash, and WebFetch/WebSearch are
# disallowed (llm.py), so before this CLI existed the sole way to read a page
# was `curl` → raw HTML → context. One tire-research step cost 2.14M input
# tokens ($1.21) that way and hit the run's cost hard-stop
# (docs/history/2026-07-27-tire-runs-examination.md). This entry point makes
# the capped markdown chain reachable from a shell, so the cheap path is also
# the available one.

def main(argv=None) -> int:
    """`python3 src/fetch_tool.py <target>` — print fetched content to stdout."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="fetch_tool",
        description="Token-lean web fetch: returns clean markdown/text, never raw HTML.",
    )
    parser.add_argument("target", help="URL, or query/subreddit for platform modes")
    parser.add_argument("--mode", default="auto", choices=list(MODES),
                        help="fetch mode (default: auto — routes URLs by host)")
    parser.add_argument("--limit", type=int, default=5,
                        help="max items for platform-query modes (default 5)")
    parser.add_argument("--max-chars", type=int, default=0,
                        help="truncate output to N chars (0 = the built-in ~20k cap)")
    parser.add_argument("--no-capture", action="store_true",
                        help="skip saving the raw page to disk")
    args = parser.parse_args(argv)

    if args.no_capture:
        import os
        os.environ["MARO_FETCH_CAPTURE"] = "0"

    out = fetch(args.target, mode=args.mode, limit=args.limit)
    if args.max_chars and args.max_chars > 0 and len(out) > args.max_chars:
        out = out[:args.max_chars] + "\n[truncated by --max-chars]"
    print(out)
    # Bracketed-failure contract: `[...]`-only output means nothing was fetched.
    return 1 if out.startswith("[") and "\n" not in out.strip() else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    import sys as _sys
    from pathlib import Path as _Path
    # Support `python3 src/fetch_tool.py` (sys.path[0] is src/, imports resolve)
    # and `python3 -m fetch_tool` from a PYTHONPATH=src environment alike.
    _here = str(_Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    raise SystemExit(main())
