"""Lightweight URL fetching + content extraction for orchestration steps.

The main entry point is `enrich_step_with_urls(step_text, extra_context)`.
It finds all URLs in the step, pre-fetches and strips each one, and returns
an enriched context block that can be injected into the step prompt — keeping
raw HTML OUT of the LLM's context window.

Compression benchmarks on typical pages:
  - Wikipedia article:   ~32k tokens → ~4.5k tokens  (86% reduction)
  - News article:        ~20k tokens → ~3k tokens     (85% reduction)
  - GitHub README:       ~15k tokens → ~5k tokens     (67% reduction)
  - X/Twitter (direct):  302/402 → oEmbed fallback (~0.5k tokens)

X-specific strategy (in priority order):
  1. Direct fetch (works for some public content)
  2. oEmbed API (publish.twitter.com) — returns tweet text + author + timestamp
  3. Resolve t.co shortlinks and recurse on the target
  4. Report access failure with clear diagnostic message
"""

from __future__ import annotations

import html as html_lib
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_MAX_TEXT_CHARS  = 20_000   # ~5k tokens — enough for any single page
_MAX_URL_FETCH_SECS = 12
_MAX_URLS_PER_STEP  = 5     # cap to avoid unbounded expansion

_UA_STANDARD = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# Minimal UA for redirect-following — t.co returns 301 with this, 200+JS with Chrome UA
_UA_REDIRECT = "Mozilla/5.0 (compatible; PoeBot/1.0)"

# Path to OpenClaw's authenticated X scraping CLI
_X_CLI_SCRIPT = Path(
    "/home/clawd/.openclaw/workspace/external/github-clean/poly-proto/scripts/x-twitter-cli.sh"
)
_X_CLI_TIMEOUT = 90  # seconds — Playwright can be slow

# Patterns that tell us a URL is an X/Twitter post or article
_X_POST_RE = re.compile(
    r"https?://(?:x|twitter)\.com/(\w+)/status/(\d+)", re.I
)
_X_ARTICLE_RE = re.compile(
    r"https?://(?:x|twitter)\.com/i/article/\d+", re.I
)
_TCO_RE = re.compile(r"https?://t\.co/[A-Za-z0-9]+")
_URL_RE = re.compile(
    r"https?://[^\s\)\]\>\"\']+",
    re.I,
)


# ---------------------------------------------------------------------------
# Core fetch + strip
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = _MAX_URL_FETCH_SECS, ua: str = _UA_STANDARD) -> Tuple[int, str]:
    """Return (status_code, text). Never raises."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(500_000)  # cap at 500KB raw
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            m = re.search(r"charset=([^\s;]+)", ct, re.I)
            if m:
                charset = m.group(1).strip()
            return resp.status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def _resolve_redirect(url: str, _depth: int = 0) -> str:
    """Follow redirects (e.g. t.co) and return final URL.

    Uses low-level http.client so we can read the Location header from each
    hop without following it automatically.
    """
    if _depth > 5:
        return url
    try:
        import http.client
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        if parsed.scheme == "https":
            import ssl
            conn = http.client.HTTPSConnection(host, timeout=5,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, timeout=5)

        conn.request("HEAD", path, headers={"User-Agent": _UA_REDIRECT})
        resp = conn.getresponse()
        status = resp.status
        loc = resp.getheader("Location", "")
        conn.close()

        if status in (301, 302, 303, 307, 308) and loc:
            # Make relative URLs absolute
            if loc.startswith("/"):
                loc = f"{parsed.scheme}://{host}{loc}"
            if loc != url:
                return _resolve_redirect(loc, _depth + 1)
        return url
    except Exception as _e:
        return url


def _x_cli_available() -> bool:
    """True if the OpenClaw x-twitter-cli.sh script exists and is executable."""
    return _X_CLI_SCRIPT.is_file() and os.access(_X_CLI_SCRIPT, os.X_OK)


_X_COOKIE_CACHE = Path.home() / ".cache" / "twitter-cli" / "cookies.json"


def _x_cookie_env() -> dict:
    """Read auth_token + ct0 from the twitter-cli cookie cache.

    Returns a dict with TWITTER_AUTH_TOKEN and TWITTER_CT0 set, or {} if
    the cache file doesn't exist or is missing the needed keys.
    """
    try:
        import json as _json
        data = _json.loads(_X_COOKIE_CACHE.read_text(encoding="utf-8"))
        auth_token = data.get("auth_token", "")
        ct0 = data.get("ct0", "")
        if auth_token and ct0:
            env = os.environ.copy()
            env["TWITTER_AUTH_TOKEN"] = auth_token
            env["TWITTER_CT0"] = ct0
            return env
    except Exception:
        pass
    return {}


def _fetch_via_x_cli(command: str, url: str) -> str:
    """Run x-twitter-cli.sh <command> <url> and return the captured markdown.

    The script writes a .md file and emits 'wrote_md=/path' on stdout.
    Injects TWITTER_AUTH_TOKEN/CT0 env vars from the cookie cache so the
    upgraded twitter-cli (v0.8.5+) authenticates correctly.
    Returns stripped markdown content, or "" on failure.
    """
    try:
        env = _x_cookie_env() or None  # None = inherit parent env
        result = subprocess.run(
            [str(_X_CLI_SCRIPT), command, url],
            capture_output=True,
            text=True,
            timeout=_X_CLI_TIMEOUT,
            env=env,
        )
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            if line.startswith("wrote_md="):
                md_path = line[len("wrote_md="):].strip()
                try:
                    content = Path(md_path).read_text(encoding="utf-8")
                    return content[:_MAX_TEXT_CHARS]
                except Exception:
                    return ""
        return ""
    except Exception:
        return ""


def _html_to_text(html: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Strip HTML to readable prose, capped at max_chars."""
    if not _BS4:
        # Fallback: strip tags with regex
        text = re.sub(r"<[^>]+>", " ", html)
        text = html_lib.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    soup = BeautifulSoup(html, "html.parser")
    # Remove noise
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "form", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Prefer <main> or <article> if present
    body = soup.find("main") or soup.find("article") or soup.find("body") or soup

    text = body.get_text(separator="\n", strip=True)
    # Collapse repeated blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = html_lib.unescape(text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Jina Reader — URL-to-markdown proxy
# ---------------------------------------------------------------------------

def _jina_fetch(url: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Fetch a URL via Jina Reader (r.jina.ai) which returns clean markdown.

    Jina renders JavaScript-heavy pages server-side and strips navigation/boilerplate.
    Returns the markdown content capped at max_chars, or "" on failure.
    """
    jina_url = _JINA_BASE + url
    status, body = _http_get(jina_url, ua="PoeBot/1.0 (+https://github.com/slycrel/openclaw-orchestration)")
    if status != 200 or not body:
        return ""
    # Jina response is already markdown — just cap length
    return body.strip()[:max_chars]


# ---------------------------------------------------------------------------
# X/Twitter-specific fetching
# ---------------------------------------------------------------------------

def fetch_x_tweet(url: str) -> str:
    """Return text content for an X/Twitter tweet URL.

    Tries in order:
    1. Direct fetch (works occasionally for public content)
    2. oEmbed API (always works for public tweets; returns text+author)
    3. Resolve t.co links within the oEmbed and summarise what we can
    4. Honest failure report
    """
    # Extract tweet ID
    m = _X_POST_RE.search(url)
    if not m:
        return f"[Could not parse X URL: {url}]"

    handle, tweet_id = m.group(1), m.group(2)
    clean_url = f"https://twitter.com/{handle}/status/{tweet_id}"

    # ---- 0. Jina Reader — gets full rendered tweet + thread text (fast, public) ---
    jina_content = _jina_fetch(url, max_chars=8_000)
    if jina_content and len(jina_content) > 200:
        _lower = jina_content.lower()
        if not ("log in" in _lower and "sign up" in _lower and len(jina_content) < 500):
            return f"[Tweet {handle}/{tweet_id} — via Jina]\n{jina_content}"

    # ---- 1. Authenticated CLI (OpenClaw x-twitter-cli.sh) — auth-required content --
    if _x_cli_available():
        cli_content = _fetch_via_x_cli("post", url)
        if cli_content and len(cli_content) > 50:
            return f"[Tweet {handle}/{tweet_id} — via authenticated CLI]\n{cli_content}"

    # ---- 2. Direct fetch ------------------------------------------------
    status, html = _http_get(url)
    if status == 200 and html:
        text = _html_to_text(html, max_chars=8_000)
        if len(text) > 200:
            return f"[Tweet {handle}/{tweet_id}]\n{text}"

    # ---- 3. oEmbed ------------------------------------------------------
    oembed_url = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(clean_url)}&omit_script=true"
    status, body = _http_get(oembed_url, timeout=10)
    if status == 200 and body:
        import json
        try:
            data = json.loads(body)
            author = data.get("author_name", handle)
            html_frag = data.get("html", "")
            # Extract tweet text from blockquote
            m2 = re.search(r'<p[^>]*>(.*?)</p>', html_frag, re.S)
            tweet_text = ""
            if m2:
                tweet_text = re.sub(r"<[^>]+>", "", m2.group(1))
                tweet_text = html_lib.unescape(tweet_text).strip()

            # Resolve t.co links and show where they point (one level only — don't
            # recursively fetch linked tweets to avoid cascading timeouts)
            tco_links = _TCO_RE.findall(html_frag)
            resolved_links: List[str] = []
            for tco in tco_links[:3]:
                final = _resolve_redirect(tco)
                if final and final != tco:
                    resolved_links.append(f"  {tco} → {final}")

            lines = [f"[Tweet by @{author}]", tweet_text]
            if resolved_links:
                lines.append("\nLinks in tweet (resolved):")
                lines.extend(resolved_links)
            return "\n".join(lines)
        except Exception:
            pass

    # ---- 4. Failure report -----------------------------------------------
    return (
        f"[Tweet {handle}/{tweet_id}: access blocked (HTTP {status}). "
        f"This tweet may require authentication or may have been deleted. "
        f"URL: {url}]"
    )


def fetch_x_article(url: str) -> str:
    """Fetch an X/Twitter article (x.com/i/article/...).

    X native articles (x.com/i/article/) are client-side rendered and not
    accessible via static HTTP or Playwright in headless mode. Returns a
    descriptive notice so the caller can search for the content via other means
    (author profile, external search, web archives) without hanging on a slow CLI.
    """
    return (
        f"[X Article at {url}: X native articles are not accessible via automated fetch — "
        "the page requires JavaScript execution that the article capture script cannot complete. "
        "To find the content: search for the author's recent posts on their profile, "
        "check web.archive.org, or search for the article title on the web.]"
    )


def fetch_url_content(url: str) -> str:
    """Fetch any URL and return stripped text content.

    Handles X/Twitter specially. For all others: fetch HTML, strip, truncate.
    Returns a descriptive failure message on error — never raises.
    """
    # Handle t.co shortlinks
    if "t.co/" in url:
        resolved = _resolve_redirect(url)
        if resolved and resolved != url:
            url = resolved

    # X/Twitter articles (require authenticated session)
    if _X_ARTICLE_RE.search(url):
        return fetch_x_article(url)

    # X/Twitter posts
    if _X_POST_RE.search(url):
        return fetch_x_tweet(url)

    # Regular pages — try Jina Reader first (returns clean markdown, handles JS rendering)
    jina_content = _jina_fetch(url)
    if jina_content and len(jina_content) > 100:
        # Skip Jina results that are just login walls (common pattern)
        _lower = jina_content.lower()
        if not ("log in" in _lower and "sign up" in _lower and len(jina_content) < 500):
            return f"[Content from {url}]\n{jina_content}"

    # Fallback: raw HTTP + HTML stripping (for sites that block Jina or return errors)
    status, html = _http_get(url)
    if status == 0:
        return f"[Could not connect to {url}]"
    if status in (401, 402, 403):
        return (
            f"[Access to {url} blocked (HTTP {status} — "
            "authentication or subscription required). "
            "Content unavailable without login.]"
        )
    if status == 404:
        return f"[Page not found: {url} (HTTP 404)]"
    if status != 200:
        return f"[HTTP {status} fetching {url}]"
    if not html:
        return f"[Empty response from {url}]"

    text = _html_to_text(html)
    if not text:
        return f"[No readable text found at {url}]"

    return f"[Content from {url}]\n{text}"


# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------

def extract_urls_from_text(text: str) -> List[str]:
    """Find all URLs in a block of text. Deduplicated, order preserved."""
    seen = set()
    result = []
    for url in _URL_RE.findall(text):
        # Strip trailing punctuation
        url = url.rstrip(".,;:!?)'\"")
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


# ---------------------------------------------------------------------------
# Step enrichment — main entry point
# ---------------------------------------------------------------------------

_JINA_BASE = "https://r.jina.ai/"   # Jina Reader: converts any URL to clean markdown

_SKIP_DOMAINS = frozenset([
    "publish.twitter.com",
    "platform.twitter.com",
    "abs.twimg.com",
    "localhost",
    "127.0.0.1",
    "r.jina.ai",  # don't recurse into Jina itself
])

_SKIP_EXTENSIONS = frozenset([
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf",
])


def _should_fetch(url: str) -> bool:
    """True if this URL is worth fetching for content."""
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname or ""
        if hostname in _SKIP_DOMAINS:
            return False
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in _SKIP_EXTENSIONS):
            return False
        return True
    except Exception:
        return False


def enrich_step_with_urls(
    step_text: str,
    extra_context: str = "",
    max_urls: int = _MAX_URLS_PER_STEP,
) -> str:
    """Pre-fetch URLs found in step_text + extra_context.

    Returns a block of pre-fetched content to prepend to the step's user
    message. If no URLs are found or none are fetchable, returns "".

    The returned block includes an instruction for the LLM to use the
    provided content rather than re-fetching.
    """
    combined = f"{step_text}\n{extra_context}"
    urls = extract_urls_from_text(combined)
    urls = [u for u in urls if _should_fetch(u)][:max_urls]

    if not urls:
        return ""

    blocks: List[str] = []
    for url in urls:
        content = fetch_url_content(url)
        if content:
            blocks.append(content)

    if not blocks:
        return ""

    # Second pass: follow X/Twitter URLs and t.co shortlinks found in fetched content.
    # Catches X articles and quoted tweets linked from the first-pass content.
    fetched_set = set(urls)
    # Normalise to base tweet ID to avoid /photo/N variants re-fetching the same tweet
    _fetched_tweet_ids: set = set()
    for u in urls:
        m = _X_POST_RE.search(u)
        if m:
            _fetched_tweet_ids.add(m.group(2))

    second_pass_limit = 3  # cap extra fetches to keep tokens bounded
    second_pass_count = 0
    for content_block in list(blocks):
        if second_pass_count >= second_pass_limit:
            break
        for linked in extract_urls_from_text(content_block):
            if second_pass_count >= second_pass_limit:
                break
            if linked in fetched_set:
                continue
            if not _should_fetch(linked):
                continue

            is_x_post = bool(_X_POST_RE.search(linked))
            is_x_article = bool(_X_ARTICLE_RE.search(linked))
            is_tco = bool(_TCO_RE.search(linked))
            is_x_domain = "twitter.com" in linked or "x.com" in linked

            if not (is_x_post or is_x_article or is_tco or is_x_domain):
                continue

            # Resolve t.co shortlinks first so we can deduplicate properly
            if is_tco:
                resolved = _resolve_redirect(linked)
                if resolved and resolved != linked:
                    if resolved in fetched_set:
                        fetched_set.add(linked)
                        continue
                    linked = resolved
                    is_x_post = bool(_X_POST_RE.search(linked))
                    is_x_article = bool(_X_ARTICLE_RE.search(linked))

            # Skip if we already have this tweet ID (catches /photo/N variants)
            m = _X_POST_RE.search(linked)
            if m and m.group(2) in _fetched_tweet_ids:
                fetched_set.add(linked)
                continue

            fetched_set.add(linked)
            if m:
                _fetched_tweet_ids.add(m.group(2))
            sub = fetch_url_content(linked)
            if sub:
                blocks.append(sub)
                second_pass_count += 1

    header = (
        "=== PRE-FETCHED URL CONTENT ===\n"
        "The following content was fetched before this step. "
        "Use it directly — do NOT call WebFetch for these URLs again.\n\n"
    )
    return header + "\n\n---\n\n".join(blocks) + "\n\n=== END PRE-FETCHED CONTENT ==="
