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
  0. Direct twitter CLI (`twitter tweet <id> --json`) — tweet + REPLIES,
     urls pre-resolved; the only reply-aware rung (BACKLOG #26)
  1. Jina Reader (fast, public, root post only)
  2. Authenticated wrapper CLI (OpenClaw x-twitter-cli.sh, single post)
  3. Direct fetch (works for some public content)
  4. oEmbed API (publish.twitter.com) — returns tweet text + author + timestamp
  5. Report access failure with clear diagnostic message
"""

from __future__ import annotations

import html as html_lib
import http.client
import os
import re
import socket
import stat
import subprocess
import tempfile
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

# Raw-capture: bytes read for the on-disk artifact. Deliberately larger than
# the 500KB in-context read cap — the capture exists so a later step can
# re-extract (JSON-LD prices, tables) from the FULL page without any of it
# passing through a context window.
_MAX_CAPTURE_BYTES = 3_000_000

# Per-capture-dir ceiling. A deep-research run over hundreds of unique URLs
# would otherwise accumulate unboundedly. Override with MARO_FETCH_CAPTURE_BUDGET.
_DEFAULT_CAPTURE_BUDGET = 200_000_000

_UA_STANDARD = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# Minimal UA for redirect-following — t.co returns 301 with this, 200+JS with Chrome UA
_UA_REDIRECT = "Mozilla/5.0 (compatible; MaroBot/1.0)"

# Path to OpenClaw's authenticated X scraping CLI.
# Override via X_CLI_SCRIPT env var; falls back to the standard OpenClaw install location.
_X_CLI_SCRIPT = Path(
    os.environ.get(
        "X_CLI_SCRIPT",
        str(Path.home() / ".openclaw" / "workspace" / "external" / "github-clean"
            / "poly-proto" / "scripts" / "x-twitter-cli.sh"),
    )
)
_X_CLI_TIMEOUT = 90  # seconds — Playwright can be slow
# Direct twitter-CLI thread fetch (BACKLOG #26) — plain API calls, no
# Playwright, so a tighter budget than the wrapper's.
_X_DIRECT_CLI_TIMEOUT = 45
# Known install locations checked when `twitter` isn't on PATH (SSH-dispatched
# runs inherit a bare non-interactive PATH without linuxbrew).
_X_DIRECT_CLI_FALLBACKS = (
    Path("/home/linuxbrew/.linuxbrew/bin/twitter"),
    Path.home() / ".local" / "bin" / "twitter",
)
_X_THREAD_MAX_OTHER_REPLIES = 8

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

def _http_get_bytes(url: str, timeout: int = _MAX_URL_FETCH_SECS, ua: str = _UA_STANDARD,
                    max_bytes: int = 500_000) -> Tuple[int, bytes, str]:
    """Return (status_code, body_bytes, charset). Never raises.

    The single origin-egress point in this module. Returns undecoded bytes so
    the capture path can store exactly what the server sent — decoding with
    `errors="replace"` and re-encoding as UTF-8 (what this used to do before
    handing bytes to the capture) silently rewrites any non-UTF-8 page, which
    defeats the point of keeping the raw file for later re-extraction.
    """
    if not is_safe_public_url(url):
        # Own gate at the egress point, not only at callers: Tier-3 raw
        # fetch reached here with NO caller-side gate at all (2026-07-29),
        # so a private-literal URL the proxy tiers refused fell through to
        # a direct fetch. The pinned opener below closes the DNS half.
        return 0, b"", "utf-8"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        with _pinned_opener().open(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)  # default cap 500KB raw
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            m = re.search(r"charset=([^\s;]+)", ct, re.I)
            if m:
                charset = m.group(1).strip()
            return resp.status, raw, charset
    except urllib.error.HTTPError as e:
        return e.code, b"", "utf-8"
    except Exception:
        return 0, b"", "utf-8"


def _http_get(url: str, timeout: int = _MAX_URL_FETCH_SECS, ua: str = _UA_STANDARD,
              max_bytes: int = 500_000) -> Tuple[int, str]:
    """Return (status_code, text). Never raises. Thin decode over _http_get_bytes."""
    status, raw, charset = _http_get_bytes(url, timeout=timeout, ua=ua, max_bytes=max_bytes)
    if not raw:
        return status, ""
    return status, raw.decode(charset, errors="replace")


def _resolve_redirect(url: str, _depth: int = 0) -> str:
    """Follow redirects (e.g. t.co) and return final URL.

    Uses low-level http.client so we can read the Location header from each
    hop without following it automatically.
    """
    if _depth > 5:
        return url
    # Each hop is arbitrary attacker-influenceable egress (Location points
    # wherever the previous server says) — gate it like any other fetch.
    if not is_safe_public_url(url):
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        if parsed.scheme == "https":
            import ssl
            conn = _PinnedHTTPSConnection(host, timeout=5,
                                          context=ssl.create_default_context())
        else:
            conn = _PinnedHTTPConnection(host, timeout=5)

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


def _fetch_x_thread_direct(handle: str, tweet_id: str) -> str:
    """Fetch a tweet AND its reply timeline via the twitter CLI directly.

    BACKLOG #26 — the only rung with reply CONTENT. `twitter tweet <id>
    --json` returns {ok, data: [post, ...]}: the target post plus its
    replies, with t.co urls pre-resolved. The author's OWN replies are the
    point: the "Repo👇" pattern puts the real link in the first self-reply
    (runs 1dac0e17 + 75a88777 both burned steps hunting a repo whose link
    sat there). Cookie handling swiped from the poly-proto wrapper; no
    dependency on it — that wrapper renders the single post and drops
    reply content. Requires the twitter binary and the cookie cache;
    returns "" on any failure so the ladder falls through. Never logs
    cookie values.
    """
    import shutil
    # SSH-dispatched runs get a bare non-interactive PATH (no linuxbrew),
    # so which() alone silently skips this rung — calm-echo 2026-07-17
    # fell through to root-only Jina and re-hunted a link the thread had.
    binary = shutil.which("twitter") or next(
        (str(p) for p in _X_DIRECT_CLI_FALLBACKS if p.is_file()), "")
    env = _x_cookie_env()
    if not binary or not env:
        return ""
    try:
        result = subprocess.run(
            [binary, "tweet", tweet_id, "--json"],
            capture_output=True, text=True,
            timeout=_X_DIRECT_CLI_TIMEOUT, env=env,
        )
        if result.returncode != 0:
            return ""
        # extract_json also tolerates warning lines the CLI prints before
        # the JSON envelope ({ok, schema_version, data: [post, ...]}).
        from llm_parse import extract_json
        payload = extract_json(result.stdout, dict, log_tag="web_fetch.x_thread")
        if not payload:
            return ""
        posts = payload.get("data", [])
        if isinstance(posts, dict):  # older CLI schema: {"posts": [...]}
            posts = posts.get("posts", [])
        posts = [p for p in posts if isinstance(p, dict) and p.get("text")]
        if not posts:
            return ""
    except Exception:
        return ""

    root = next((p for p in posts if str(p.get("id", "")) == str(tweet_id)),
                posts[0])
    author_sn = (root.get("author") or {}).get("screenName") or handle

    def _fmt(p: dict, cap: int = 0) -> str:
        a = p.get("author") or {}
        text = (p.get("text") or "").strip()
        if cap and len(text) > cap:
            text = text[:cap] + "…"
        line = f"@{a.get('screenName', '?')}"
        when = p.get("createdAtISO") or p.get("createdAt") or ""
        if when:
            line += f" ({when})"
        line += f":\n{text}"
        urls = [u for u in (p.get("urls") or []) if u]
        if urls:
            line += "\nLinks: " + " ".join(urls[:5])
        return line

    m = root.get("metrics") or {}
    metric_bits = " · ".join(
        f"{k} {m[k]}" for k in ("likes", "retweets", "replies", "views")
        if m.get(k) is not None)
    out = [_fmt(root)]
    if metric_bits:
        out.append(f"Metrics: {metric_bits}")

    others = [p for p in posts if p is not root]
    own = [p for p in others
           if (p.get("author") or {}).get("screenName") == author_sn]
    rest = [p for p in others if not any(p is o for o in own)]
    if own:
        out.append(f"\nAuthor follow-up posts ({len(own)}) — payload links "
                   "(\"Repo👇\", \"link below\") usually live here:")
        out.extend(_fmt(p) for p in own)
    if rest:
        shown = rest[:_X_THREAD_MAX_OTHER_REPLIES]
        out.append(f"\nReplies from others ({len(shown)} of {len(rest)} shown):")
        out.extend(_fmt(p, cap=300) for p in shown)
    return "\n".join(out)[:_MAX_TEXT_CHARS]


def _html_to_text(html: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Strip HTML to readable prose, capped at max_chars."""
    if not _BS4:
        # Fallback: strip script/style blocks first, then remove remaining tags.
        text = re.sub(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"<[^>]+>", " ", text)
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
    if not is_safe_public_url(url):
        return ""  # same disclosure boundary as the Cloudflare tier
    jina_url = _JINA_BASE + url
    status, body = _http_get(jina_url, ua="MaroBot/1.0 (+https://github.com/slycrel/openclaw-orchestration)")
    if status != 200 or not body:
        return ""
    # Jina response is already markdown — just cap length
    return body.strip()[:max_chars]


# ---------------------------------------------------------------------------
# Cloudflare markdown.new — second URL-to-markdown tier
# ---------------------------------------------------------------------------

def _cf_markdown_fetch(url: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Fetch a URL via Cloudflare's markdown.new and return clean markdown.

    Second markdown tier behind Jina. Jina is a single point of failure — it
    403s on some hosts (see skills/social_search.md) and rate-limits — and a
    worker with no working markdown path falls back to curling raw HTML, which
    is the exact 2.14M-token failure this tier exists to prevent.

    markdown.new is free, needs no auth, and is rate-limited to 500 req/day per
    IP. Its own pipeline escalates to Cloudflare Browser Rendering for JS-heavy
    pages, so it recovers cases where Jina returns thin content. Public URLs
    only — login-walled pages (X/Twitter) are handled upstream by the X chain.

    Returns markdown capped at max_chars, or "" on any failure.
    """
    # Handing an internal URL to a third party discloses it; handing one with
    # credentials discloses those too.
    if not is_safe_public_url(url):
        return ""
    try:
        import json as _json
        payload = _json.dumps({"url": url}).encode("utf-8")
        req = urllib.request.Request(
            _CF_MARKDOWN_BASE,
            data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": "MaroBot/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_MAX_URL_FETCH_SECS) as resp:
            if resp.status != 200:
                return ""
            body = resp.read(1_000_000).decode("utf-8", errors="replace")
    except Exception:
        return ""

    if not body:
        return ""
    # Response may be raw markdown or a JSON envelope depending on tier.
    text = body.strip()
    if text.startswith("{"):
        try:
            import json as _json
            data = _json.loads(text)
            if isinstance(data, dict):
                # `{"success":true,"result":"# md"}` is the shape Cloudflare's own
                # Markdown endpoint documents — handle the string form before
                # descending, or a valid response reads as a failure.
                result = data.get("result")
                if isinstance(result, str) and result.strip():
                    text = result.strip()
                else:
                    inner = result if isinstance(result, dict) else data
                    for key in ("markdown", "content", "text", "data"):
                        val = inner.get(key) if isinstance(inner, dict) else None
                        if isinstance(val, str) and val.strip():
                            text = val.strip()
                            break
                    else:
                        return ""
        except Exception:
            return ""
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Raw-page capture — full fidelity on disk, nothing extra in context
# ---------------------------------------------------------------------------

def capture_dir() -> Optional[Path]:
    """Directory for raw page captures, or None if none can be resolved.

    Resolution order: explicit env override → the active run-dir → the
    workspace output dir. Deliberately NOT under `<rundir>/build/` — that
    tree is what viz_server serves, and captures are unscrubbed third-party
    HTML that must never be served back over HTTP.
    """
    override = os.environ.get("MARO_FETCH_CAPTURE_DIR")
    candidates: List[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    else:
        try:
            from runs import current_run_dir
            rd = current_run_dir()
            if rd is not None:
                candidates.append(Path(rd) / "fetch-raw")
        except Exception:
            pass
        try:
            from config import output_dir
            candidates.append(Path(output_dir()) / "fetch-raw")
        except Exception:
            pass
    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True, mode=0o700)
            # A symlinked capture dir defeats the whole placement argument: point
            # it at <rundir>/build/ and viz_server serves attacker-controlled
            # HTML to the operator's browser. Refuse rather than follow.
            if cand.is_symlink():
                continue
            return cand
        except Exception:
            continue
    return None


def _capture_enabled() -> bool:
    """Raw capture is on unless explicitly disabled."""
    return os.environ.get("MARO_FETCH_CAPTURE", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _capture_budget_bytes() -> int:
    """Per-directory ceiling on captured bytes. 0 disables the ceiling."""
    try:
        return int(os.environ.get("MARO_FETCH_CAPTURE_BUDGET", _DEFAULT_CAPTURE_BUDGET))
    except Exception:
        return _DEFAULT_CAPTURE_BUDGET


def _captured_bytes(cdir: Path) -> int:
    """Bytes already captured in `cdir`. Cheap enough at capture scale."""
    total = 0
    try:
        for p in cdir.glob("*.html"):
            try:
                total += p.stat().st_size
            except OSError:
                continue
    except Exception:
        return 0
    return total


def _safe_manifest_url(url: str) -> str:
    """URL with credentials and query stripped, for durable storage.

    The manifest outlives the run and is read by humans and later steps. A
    presigned S3 signature or a `?token=` in the query would otherwise sit in
    plaintext on disk indefinitely.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        redacted = parsed._replace(netloc=netloc, query="", fragment="")
        out = urllib.parse.urlunsplit(redacted)
        return out + ("?<redacted>" if parsed.query else "")
    except Exception:
        return "<unparseable-url>"


# ---------------------------------------------------------------------------
# Outbound URL policy — one gate for every egress in this module
# ---------------------------------------------------------------------------

def _is_public_ip(ip) -> bool:
    """Shared predicate for the literal gate and the resolver vetting —
    keeping them in lockstep is the point of extracting it."""
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def is_safe_public_url(url: str) -> bool:
    """True if `url` is an ordinary public http(s) URL safe to fetch or proxy.

    Every outbound path in this module runs through here: the proxy tiers hand
    the URL to a third party, and the direct-fetch paths run it before egress.
    Without a gate, a worker (or a redirect from an attacker-controlled page)
    can point either at loopback, RFC1918, link-local, or cloud-metadata
    addresses and have the response persisted to disk with a pointer handed
    back to the model.

    Literal-address checks only — no DNS resolution here, so a hostname that
    resolves to a private address still passes THIS gate. The direct-fetch
    layer closes that half: _http_get_bytes and _resolve_redirect connect via
    the pinned classes below, which resolve, vet every address, and connect
    to the vetted IP only (no second lookup — DNS rebinding between check and
    connect buys nothing).
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    # Credentials in the URL would be handed to the proxy tiers verbatim.
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").strip("[]")
    if not host:
        return False
    if host.lower() in _SKIP_DOMAINS or host.lower().endswith((".local", ".internal")):
        return False
    try:
        import ipaddress
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a name — the resolver vetting below owns this case
    return _is_public_ip(ip)


def _vet_resolved_ips(host: str) -> list:
    """Resolve `host` and return its addresses iff every one is public.

    Refusal is all-or-nothing: a split answer ([public, private]) is treated
    as hostile (rebinding / split-horizon smuggling), not as a tiebreak —
    returns [] and the connection is refused. Resolution failure also refuses.
    """
    import ipaddress
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    ips: list = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]  # drop IPv6 zone id
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return []
        if not _is_public_ip(ip):
            return []
        if addr not in ips:
            ips.append(addr)
    return ips


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to a resolve-time-vetted IP, never a fresh DNS answer."""

    def connect(self):
        ips = _vet_resolved_ips(self.host)
        if not ips:
            raise OSError(f"refusing non-public or unresolvable host: {self.host!r}")
        last_err = None
        for addr in ips:  # getaddrinfo order; all entries already vetted
            try:
                self.sock = socket.create_connection((addr, self.port), self.timeout)
                return
            except OSError as e:
                last_err = e
        raise last_err


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        ips = _vet_resolved_ips(self.host)
        if not ips:
            raise OSError(f"refusing non-public or unresolvable host: {self.host!r}")
        last_err = None
        raw = None
        for addr in ips:
            try:
                raw = socket.create_connection((addr, self.port), self.timeout)
                break
            except OSError as e:
                last_err = e
        if raw is None:
            raise last_err
        # SNI + certificate validation against the NAME — pinning must not
        # weaken TLS to connect-by-IP semantics.
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-gates every redirect hop — a public page 302ing to metadata/RFC1918
    is the classic indirection the literal gate alone can't see."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_safe_public_url(newurl):
            raise urllib.error.HTTPError(
                newurl, code, "redirect to non-public URL refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def _pinned_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _PinnedHTTPHandler, _PinnedHTTPSHandler, _SafeRedirectHandler)


def capture_raw(url: str, *, body: Optional[bytes] = None,
                timeout: int = _MAX_URL_FETCH_SECS) -> Optional[Path]:
    """Store `url`'s raw bytes on disk. Returns the path or None.

    Kept deliberately separate from the text-extraction chain: the markdown
    tiers return *converted* text, so the original HTML (JSON-LD blocks, price
    tables, attributes the converters drop) would otherwise be unrecoverable
    without a re-fetch. A later step can grep/parse the captured file directly —
    full fidelity on disk, zero extra tokens in context.

    Pass `body` when the caller already downloaded the page (the raw-HTTP tier)
    so capture costs no extra request; omit it and the page is fetched once.
    Bytes, not str: storing a decoded-and-re-encoded copy would misrepresent
    any non-UTF-8 page as UTF-8.

    Content-addressed by URL hash, so a repeat fetch of the same URL reuses the
    existing file instead of re-downloading. Never raises.
    """
    cdir = capture_dir()
    if cdir is None:
        return None
    if body is None and not is_safe_public_url(url):
        # Capture would be a second, unproxied request straight from this host —
        # the SSRF primitive. No policy pass, no fetch.
        return None
    try:
        import hashlib
        digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:16]
        # Hash-derived name only — never URL-derived (path-traversal safe).
        path = cdir / f"{digest}.html"
        # lstat, not exists(): a planted symlink at <digest>.html pointing at a
        # secret would otherwise be "reused" and its path handed to the model,
        # which the prompt tells to parse it — a file-disclosure primitive.
        try:
            st = os.lstat(path)
            if stat.S_ISREG(st.st_mode) and st.st_size > 0:
                return path
            os.unlink(path)  # symlink or non-regular: never trust it
        except FileNotFoundError:
            pass

        budget = _capture_budget_bytes()
        if budget and _captured_bytes(cdir) >= budget:
            return None  # quota exhausted — surfaced by the caller's note

        if body is not None:
            status = 200
        else:
            status, body, _charset = _http_get_bytes(
                url, timeout=timeout, max_bytes=_MAX_CAPTURE_BYTES)
        if status != 200 or not body:
            return None

        # Unpredictable temp name + O_NOFOLLOW|O_EXCL. A fixed `<digest>.html.tmp`
        # was both a symlink-planting target (verified exploitable: the write
        # followed the link and overwrote a victim file) and a collision point
        # for two lanes capturing the same URL, where the loser's os.replace
        # raised and silently returned None.
        fd, tmp_name = tempfile.mkstemp(dir=str(cdir), prefix=f".{digest}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        try:
            import json as _json
            entry = {"url": _safe_manifest_url(url), "path": str(path),
                     "bytes": len(body), "status": status,
                     "truncated": len(body) >= _MAX_CAPTURE_BYTES,
                     "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            line = _json.dumps(entry) + "\n"
            # O_APPEND|O_NOFOLLOW: a symlinked manifest is an arbitrary-append
            # primitive. Single write() of one line keeps concurrent appends
            # from interleaving mid-record.
            mfd = os.open(cdir / "index.jsonl",
                          os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                os.write(mfd, line.encode("utf-8"))
            finally:
                os.close(mfd)
        except Exception:
            pass  # manifest is a convenience; the capture itself already landed
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# X/Twitter-specific fetching
# ---------------------------------------------------------------------------

def fetch_x_tweet(url: str) -> str:
    """Return text content for an X/Twitter tweet URL.

    Tries in order:
    0. Direct twitter CLI — tweet + REPLIES (the only reply-aware rung)
    1. Jina Reader (fast, public, root post only)
    2. Authenticated wrapper CLI (single post)
    3. Direct fetch (works occasionally for public content)
    4. oEmbed API (text+author, t.co links resolved)
    5. Honest failure report
    """
    # Extract tweet ID
    m = _X_POST_RE.search(url)
    if not m:
        return f"[Could not parse X URL: {url}]"

    handle, tweet_id = m.group(1), m.group(2)
    clean_url = f"https://twitter.com/{handle}/status/{tweet_id}"

    # ---- 0. Direct CLI thread fetch — must run BEFORE the reply-blind rungs:
    # a root-only success from Jina would return early and hide the thread
    # (exactly how runs 1dac0e17/75a88777 missed the author's "Repo:" reply).
    thread = _fetch_x_thread_direct(handle, tweet_id)
    if thread:
        return f"[Tweet {handle}/{tweet_id} — via authenticated CLI, with replies]\n{thread}"

    # ---- 1. Jina Reader — gets full rendered tweet + thread text (fast, public) ---
    jina_content = _jina_fetch(url, max_chars=8_000)
    if jina_content and len(jina_content) > 200:
        _lower = jina_content.lower()
        if not ("log in" in _lower and "sign up" in _lower and len(jina_content) < 500):
            return f"[Tweet {handle}/{tweet_id} — via Jina]\n{jina_content}"

    # ---- 2. Authenticated wrapper CLI (OpenClaw x-twitter-cli.sh) — single post --
    if _x_cli_available():
        cli_content = _fetch_via_x_cli("post", url)
        if cli_content and len(cli_content) > 50:
            return f"[Tweet {handle}/{tweet_id} — via authenticated CLI]\n{cli_content}"

    # ---- 3. Direct fetch ------------------------------------------------
    status, html = _http_get(url)
    if status == 200 and html:
        text = _html_to_text(html, max_chars=8_000)
        if len(text) > 200:
            return f"[Tweet {handle}/{tweet_id}]\n{text}"

    # ---- 4. oEmbed ------------------------------------------------------
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

    # ---- 5. Failure report -----------------------------------------------
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


def _is_login_wall(text: str) -> bool:
    """True for the short login-wall stubs the markdown tiers return."""
    lower = text.lower()
    return "log in" in lower and "sign up" in lower and len(text) < 500


def _capture_note(url: str, *, body: Optional[bytes] = None) -> str:
    """Capture the raw page and return a one-line pointer (or "").

    The pointer is the whole point: ~15 tokens telling a later step that full
    fidelity is on disk, instead of the 100k-500k tokens the page itself costs.
    """
    try:
        if not _capture_enabled():
            return ""
        path = capture_raw(url, body=body)
    except Exception:
        return ""  # capture is a convenience — it must never fail a fetch
    if path is None:
        return ""
    # Say "truncated" when it is. Calling a 500KB prefix "full" would send a
    # later step looking for a field that was never stored.
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    note = f"\n[raw HTML saved: {path} ({size:,} bytes"
    if size >= _MAX_CAPTURE_BYTES:
        note += ", TRUNCATED at the capture cap"
    return note + ") — parse/grep this file for detail the summary above drops; do NOT cat it into context]"


def fetch_url_content(url: str) -> str:
    """Fetch any URL and return stripped text content.

    Handles X/Twitter specially. For all others the chain is markdown-first —
    Jina Reader, then Cloudflare markdown.new, then raw HTTP + BS4 stripping —
    with every tier capped at _MAX_TEXT_CHARS. Raw HTML is captured to disk
    alongside (see `capture_raw`) and referenced by path only.

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

    # Extraction and capture are separate concerns with ONE join point at the
    # end. They used to be interleaved — three returns each calling
    # _capture_note() — which is how the capture re-fetch became a hidden side
    # effect of "get me the text", and with it the SSRF path and the silent
    # second origin request (adversarial review 2026-07-27, architect lens).
    text, origin_bytes = _extract_text(url)
    if text.startswith("["):
        return text  # a bracketed diagnostic, not content — nothing to capture
    return f"[Content from {url}]\n{text}{_capture_note(url, body=origin_bytes)}"


def _extract_text(url: str) -> Tuple[str, Optional[bytes]]:
    """Return (text_or_bracketed_diagnostic, origin_bytes_if_we_have_them).

    Pure extraction — no disk writes, no capture. The second element is the
    origin response when a tier already downloaded it, so the caller can
    capture without paying for a second request.
    """
    # Tier 1 — Jina Reader (clean markdown, handles JS rendering)
    jina_content = _jina_fetch(url)
    if jina_content and len(jina_content) > 100 and not _is_login_wall(jina_content):
        return jina_content, None

    # Tier 2 — Cloudflare markdown.new. Jina 403s/rate-limits on some hosts;
    # without a second markdown tier those pages fall through to raw HTML,
    # which is what blew a single step to 2.14M input tokens.
    cf_content = _cf_markdown_fetch(url)
    if cf_content and len(cf_content) > 100 and not _is_login_wall(cf_content):
        return cf_content, None

    # Tier 3 — raw HTTP + HTML stripping (sites that block both markdown tiers).
    # Read to the capture cap, not the 500KB in-context cap: this same response
    # is what gets captured, and a 500KB prefix stored as "the raw page" hides
    # any JSON-LD past that offset. Text extraction caps itself at 20k chars.
    status, raw, charset = _http_get_bytes(url, max_bytes=_MAX_CAPTURE_BYTES)
    if status == 0:
        return f"[Could not connect to {url}]", None
    if status in (401, 402, 403):
        return (
            f"[Access to {url} blocked (HTTP {status} — "
            "authentication or subscription required). "
            "Content unavailable without login.]"
        ), None
    if status == 404:
        return f"[Page not found: {url} (HTTP 404)]", None
    if status != 200:
        return f"[HTTP {status} fetching {url}]", None
    if not raw:
        return f"[Empty response from {url}]", None

    text = _html_to_text(raw.decode(charset, errors="replace"))
    if not text:
        return f"[No readable text found at {url}]", raw

    return text, raw


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
# Cloudflare markdown.new: free, no-auth URL→markdown (500 req/day/IP), escalates
# to Cloudflare Browser Rendering for JS-heavy pages. Second tier behind Jina.
_CF_MARKDOWN_BASE = os.environ.get("MARO_CF_MARKDOWN_URL", "https://markdown.new/")

_SKIP_DOMAINS = frozenset([
    "publish.twitter.com",
    "platform.twitter.com",
    "abs.twimg.com",
    "localhost",
    "127.0.0.1",
    "r.jina.ai",  # don't recurse into Jina itself
    "markdown.new",  # nor into the Cloudflare markdown tier
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


if __name__ == "__main__":  # pragma: no cover - CLI entry
    # Delegate to the unified seam so `python3 src/web_fetch.py <url>` and
    # `python3 src/fetch_tool.py <url>` behave identically — docs and BACKLOG
    # name this module, and a worker that guesses either one should not fail.
    import sys as _sys
    _here = str(Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from fetch_tool import main as _main
    raise SystemExit(_main())
