"""stdlib-only HTTP server for the /v1/systemone wire contract.

ThreadingHTTPServer + http.server + json. No FastAPI/uvicorn/pydantic --
house rule is to swipe code over third-party deps, and the wire contract is
small enough not to need a framework.
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .config import Config, load_config
from .engine import Engine
from .pmi import PMICache
from .prompt import build_state_prompt
from .schema import ValidationError, parse_request
from .scoring import ScoringError, combine

logger = logging.getLogger("pcd_sidecar")


class SidecarState:
    """Shared, mostly-immutable state handed to every request handler.
    `inference_lock` serializes calls into the engine: this is a
    single-model CPU/GPU box, not a batching server, and torch/mlx forward
    passes are not guaranteed safe to interleave from multiple threads."""

    def __init__(self, engine: Engine, pmi_cache: PMICache, config: Config):
        self.engine = engine
        self.pmi_cache = pmi_cache
        self.config = config
        self.inference_lock = threading.Lock()


def build_engine(config: Config) -> Engine:
    backend = config.backend
    if config.device == "mlx":
        backend = "mlx"
    if backend == "mlx":
        from .engine_mlx import MlxEngine

        return MlxEngine(config.model_id, dtype=config.dtype, threads=config.threads)
    from .engine import TorchEngine

    return TorchEngine(
        config.model_id, device=config.device, dtype=config.dtype, threads=config.threads
    )


def answer_all_questions(state: SidecarState, model_name: str, raw_state, questions) -> dict:
    engine = state.engine
    answers = {}
    input_tokens = 0
    with state.inference_lock:
        for qid, question in questions.items():
            prompt_text = build_state_prompt(engine.tokenizer, raw_state, question)
            candidate_texts = [text for _, text in question.candidates]
            state_logprobs = engine.score_candidates(prompt_text, candidate_texts)
            baseline_logprobs = state.pmi_cache.baseline_logprobs(question)
            answers[qid] = combine(question, state_logprobs, baseline_logprobs)
            if hasattr(engine, "token_count"):
                input_tokens += engine.token_count(prompt_text)
    return {
        "model": engine.model_id,
        "answers": answers,
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }


# MAX_BODY bounds one request. A System One state is a few KB; 4 MiB is
# generous for a whole run's results and small enough that a stalled or
# hostile upload cannot pin memory.
MAX_BODY = 4 << 20
# READ_TIMEOUT bounds how long a handler thread waits on the socket for
# the body it was promised (BaseHTTPRequestHandler.timeout → socket
# settimeout). Without it a Content-Length the peer never sends holds a
# thread forever.
READ_TIMEOUT = 30


def make_handler(state: SidecarState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pcd-sidecar/0.1"
        timeout = READ_TIMEOUT

        def log_message(self, fmt, *args):  # quieter, timestamped one-liners
            logger.info("%s - %s", self.address_string(), fmt % args)

        def _send_json(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def do_GET(self):
            if self.path == "/healthz":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "model": state.engine.model_id,
                        "device": state.engine.device,
                        "loaded": True,
                    },
                )
            else:
                self._send_json(404, {"error": "not found"})

        def _read_body(self, length: int):
            """Read exactly `length` bytes under ONE absolute deadline. The
            class timeout alone is an idle timeout: a peer trickling a byte
            every few seconds never trips it (review r2). Each socket wait
            gets only what is left of the budget; None means it ran out."""
            if not length:
                return b""
            deadline = time.monotonic() + self.timeout
            chunks = []
            got = 0
            while got < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.connection.settimeout(remaining)
                # read1, not read: BufferedReader.read(n) loops over raw
                # recvs until it has n bytes, and each raw recv only sees
                # the IDLE timeout — a trickling peer never trips it.
                # read1 returns after one raw recv, so the deadline is
                # re-checked per chunk.
                chunk = self.rfile.read1(min(65536, length - got))
                if not chunk:
                    return None
                chunks.append(chunk)
                got += len(chunk)
            return b"".join(chunks)

        def do_POST(self):
            if self.path != "/v1/systemone":
                self._send_json(404, {"error": "not found"})
                return

            # framing is validated BEFORE any byte is read: a negative or
            # non-numeric length is a 400, an oversized one a 413, and
            # the read itself is bounded by both the length and the
            # socket timeout. Nothing here can escape without an HTTP
            # reply — a stray exception would drop the connection and
            # look, from the client's side, like the model hung.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send_json(400, {"error": "invalid Content-Length"})
                return
            if length < 0:
                self._send_json(400, {"error": "invalid Content-Length"})
                return
            if length > MAX_BODY:
                self._send_json(413, {"error": f"body exceeds {MAX_BODY} bytes"})
                return
            try:
                raw = self._read_body(length)
            except (socket.timeout, OSError) as e:
                self._send_json(408, {"error": f"body not received: {e}"})
                return
            if raw is None:
                self._send_json(408, {"error": f"body not received within {self.timeout}s"})
                return
            try:
                body = json.loads((raw or b"{}").decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._send_json(400, {"error": f"invalid JSON: {e}"})
                return

            try:
                model_name, raw_state, questions = parse_request(body)
            except ValidationError as e:
                self._send_json(400, {"error": str(e)})
                return

            try:
                response = answer_all_questions(state, model_name, raw_state, questions)
            except (ScoringError, ValueError) as e:
                self._send_json(500, {"error": str(e)})
                return
            except Exception as e:  # inference failure of any other kind
                logger.exception("inference failure")
                self._send_json(500, {"error": f"inference failure: {e}"})
                return

            self._send_json(200, response)

    return Handler


def run(config: Optional[Config] = None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = config or load_config()
    logger.info("loading model %s (device=%s backend=%s)", config.model_id, config.device, config.backend)
    engine = build_engine(config)
    pmi_cache = PMICache(engine, enabled=config.pmi_enabled)
    state = SidecarState(engine, pmi_cache, config)

    handler_cls = make_handler(state)
    httpd = ThreadingHTTPServer(("0.0.0.0", config.port), handler_cls)
    logger.info(
        "pcd-sidecar listening on 0.0.0.0:%d (model=%s device=%s pmi=%s)",
        config.port,
        engine.model_id,
        engine.device,
        config.pmi_enabled,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    run()


if __name__ == "__main__":
    sys.exit(main())
