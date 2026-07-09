"""Phase (BACKLOG "General-purpose visualization server"): read-only HTTP
server for the static visualization pages `loop_report.py` writes under
`runs_root()` (per-run reports, the cross-run index).

Why this exists: viewed directly off disk (`file://`), the per-run report's
detail-tier `fetch()` of a step's call-record JSON is blocked by the
browser's opaque-origin rule for `file://`. This server serves the same
files over `http://` so that works, and is written generically (not scoped
to one report type) since the run-visibility report is unlikely to be the
last thing worth serving this way.

Guardrail (see archive/observe_dashboard.py, a killed predecessor): strictly
read-only, GET/HEAD only, no goal-submission/control surface, no directory
listing, defaults to loopback-only. `<run-dir>/source/` and `<run-dir>/artifact/`
(prompt text, raw `git bundle`/`git log`/`git diff` output — unlike
`build/calls/*.json`, these are NOT secret-scrubbed) are never reachable:
the handler allowlists exactly `index.html` at the document root and
`<run-dir-name>/build/**`, denying everything else before touching the
filesystem.
"""

from __future__ import annotations

import http.server
import logging
import os
from pathlib import Path
from typing import Optional, Type
from urllib.parse import unquote, urlsplit

log = logging.getLogger("maro.viz")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8787


def _resolve_allowed_path(url_path: str, root: Path) -> Optional[Path]:
    """Map a request path to a servable location under `root`, or None if denied.

    Allowlist (default-deny everything else) — checks *shape* only, not
    existence; a permitted-but-missing file is left to the caller (which
    404s it the normal way) rather than reported as 403:
      - "index.html" at the document root
      - "<run-dir-name>/build/**" (any file under a run-dir's build/ subtree)

    Never touches the filesystem for a request this rejects.
    """
    raw = unquote(urlsplit(url_path).path)
    segments = [s for s in raw.split("/") if s not in ("", ".")]
    if any(s == ".." for s in segments):
        return None
    if not segments:
        return None
    if segments == ["index.html"]:
        candidate = root / "index.html"
    elif len(segments) >= 3 and segments[1] == "build":
        candidate = root.joinpath(*segments)
    else:
        return None

    # Defense in depth alongside SimpleHTTPRequestHandler's own `..`-stripping
    # in translate_path — belt and suspenders, not a replacement for it.
    root_real = root.resolve()
    candidate_real = candidate.resolve()
    if root_real != candidate_real and root_real not in candidate_real.parents:
        return None
    return candidate_real


def _make_handler_class(root: Path) -> Type[http.server.SimpleHTTPRequestHandler]:
    root = root.resolve()

    class _ViewerHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, fmt, *args):  # route through logging, not stderr
            log.info("%s - %s", self.address_string(), fmt % args)

        def _reject(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if _resolve_allowed_path(self.path, root) is None:
                self._reject(403)
                return
            super().do_GET()

        def do_HEAD(self):
            if _resolve_allowed_path(self.path, root) is None:
                self._reject(403)
                return
            super().do_HEAD()

        def do_POST(self):
            self._reject(405)

        do_PUT = do_DELETE = do_PATCH = do_POST

        def list_directory(self, path):  # never browse — index.html only
            self._reject(403)
            return None

    return _ViewerHandler


def serve(host: Optional[str] = None, port: Optional[int] = None, root: Optional[Path] = None) -> None:
    """Blocking entrypoint — run the visualization server until interrupted."""
    if host is None:
        try:
            from config import get as _get
            host = _get("viz.host", _DEFAULT_HOST)
        except Exception:
            host = _DEFAULT_HOST
    if port is None:
        try:
            from config import get as _get
            port = int(_get("viz.port", _DEFAULT_PORT))
        except Exception:
            port = _DEFAULT_PORT
    if root is None:
        from runs import runs_root
        root = runs_root()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    handler_cls = _make_handler_class(root)
    httpd = http.server.ThreadingHTTPServer((host, port), handler_cls)
    print(f"maro viz server: http://{host}:{port}/ (root={root}, pid={os.getpid()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
