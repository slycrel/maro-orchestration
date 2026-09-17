import json
import math
import socket
import threading
import urllib.error
import urllib.request

import pytest

from pcd_sidecar.config import Config
from pcd_sidecar.pmi import PMICache
from pcd_sidecar.server import SidecarState, make_handler
from http.server import ThreadingHTTPServer

from conftest import FakeEngine


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FailingEngine(FakeEngine):
    def score_candidates(self, prompt_text, candidate_texts):
        raise RuntimeError("simulated inference failure")


@pytest.fixture
def running_server():
    engine = FakeEngine()
    config = Config(
        model_id=engine.model_id, device="cpu", dtype=None, port=0, threads=None,
        pmi_enabled=True, backend="torch",
    )
    state = SidecarState(engine, PMICache(engine, enabled=True), config)
    handler_cls = make_handler(state)
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", engine, state
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(base_url, payload):
    req = urllib.request.Request(
        f"{base_url}/v1/systemone",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_healthz(running_server):
    base_url, engine, _ = running_server
    with urllib.request.urlopen(f"{base_url}/healthz") as resp:
        body = json.loads(resp.read())
    assert resp.status == 200
    assert body == {"ok": True, "model": engine.model_id, "device": engine.device, "loaded": True}


def test_full_request_all_three_question_types(running_server):
    base_url, engine, _ = running_server
    payload = {
        "model": "jev-1",
        "state": {"goal": "write flatten()", "result": "def flatten(...): ..."},
        "questions": {
            "outcome": {
                "type": "choice",
                "instructions": "did the result satisfy the goal?",
                "criteria": {"done": "fully satisfied", "blocked": "cannot proceed", "unclear": "ambiguous"},
            },
            "matches_goal": {
                "type": "noul",
                "instructions": "does the result address the stated goal?",
                "criteria": {"true": "it does", "false": "it does not"},
            },
            "quality": {
                "type": "score",
                "instructions": "rate the quality of the result",
                "criteria": ["poor", "adequate", "excellent"],
            },
        },
    }
    status, body = _post(base_url, payload)
    assert status == 200
    assert body["model"] == engine.model_id
    assert body["usage"]["output_tokens"] == 0
    assert body["usage"]["input_tokens"] > 0
    answers = body["answers"]
    assert set(answers) == {"outcome", "matches_goal", "quality"}

    outcome = answers["outcome"]
    assert outcome["type"] == "choice"
    assert outcome["choice"] in {"done", "blocked", "unclear"}
    assert math.isclose(sum(outcome["probabilities"].values()), 1.0, rel_tol=1e-6)

    noul = answers["matches_goal"]
    assert noul["type"] == "noul"
    assert 0.0 <= noul["noul"] <= 1.0

    score = answers["quality"]
    assert score["type"] == "score"
    assert score["legend"] == {"0": "poor", "1": "adequate", "2": "excellent"}
    assert math.isclose(sum(score["probabilities"].values()), 1.0, rel_tol=1e-6)


def test_malformed_json_is_400(running_server):
    base_url, _, _ = running_server
    req = urllib.request.Request(
        f"{base_url}/v1/systemone", data=b"{not json", method="POST"
    )
    try:
        urllib.request.urlopen(req)
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        body = json.loads(e.read())
        assert "error" in body and set(body) == {"error"}


def test_invalid_request_shape_is_400(running_server):
    base_url, _, _ = running_server
    status, body = _post(base_url, {"model": "m", "state": 5, "questions": {}})
    assert status == 400
    assert set(body) == {"error"}


def test_engine_failure_is_500_never_a_fallback_answer():
    engine = _FailingEngine()
    config = Config(model_id=engine.model_id, device="cpu", dtype=None, port=0, threads=None,
                     pmi_enabled=True, backend="torch")
    state = SidecarState(engine, PMICache(engine, enabled=True), config)
    handler_cls = make_handler(state)
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        payload = {
            "model": "m",
            "state": "s",
            "questions": {"q": {"type": "noul", "instructions": "i"}},
        }
        status, body = _post(f"http://127.0.0.1:{port}", payload)
        assert status == 500
        assert set(body) == {"error"}
        assert "choice" not in body and "noul" not in body and "answers" not in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_unknown_path_is_404(running_server):
    base_url, _, _ = running_server
    try:
        urllib.request.urlopen(f"{base_url}/nope")
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def _raw(base_url, request_bytes):
    """Send raw bytes on a fresh socket and return (status, body-json)."""
    host, port = base_url.replace("http://", "").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(request_bytes)
        s.settimeout(5)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        status = int(head.split(b" ")[1])
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        while len(rest) < length:
            chunk = s.recv(4096)
            if not chunk:
                break
            rest += chunk
        return status, json.loads(rest)


def _request(headers, body=b""):
    lines = [b"POST /v1/systemone HTTP/1.1", b"Host: x", b"Connection: close"] + [
        h.encode() for h in headers
    ]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


def test_bad_framing_and_encoding_are_bounded_4xx(running_server):
    """Review r1 (all four lenses): a non-numeric or negative Content-Length
    escaped the handler as an uncaught exception (dropped connection, no
    HTTP reply), a negative length reached read(-1) = read-to-EOF, an
    oversized one was uncapped, and invalid UTF-8 raised past the
    JSONDecodeError handler. Every shape now gets a structured 4xx."""
    base_url, _, _ = running_server
    status, body = _raw(base_url, _request(["Content-Length: abc"]))
    assert status == 400 and set(body) == {"error"}
    status, body = _raw(base_url, _request(["Content-Length: -1"]))
    assert status == 400 and set(body) == {"error"}
    status, body = _raw(base_url, _request(["Content-Length: 999999999999"]))
    assert status == 413 and set(body) == {"error"}
    status, body = _raw(base_url, _request(["Content-Length: 2"], b"\xff\xfe"))
    assert status == 400 and "invalid JSON" in body["error"]


def test_a_body_that_never_arrives_times_out_with_a_reply():
    """A Content-Length the peer never honours must not hold a handler
    thread forever: the socket read is bounded and answered 408."""
    from pcd_sidecar.server import make_handler

    engine = FakeEngine()
    config = Config(
        model_id=engine.model_id, device="cpu", dtype=None, port=0, threads=None,
        pmi_enabled=True, backend="torch",
    )
    state = SidecarState(engine, PMICache(engine, enabled=True), config)
    handler_cls = make_handler(state)
    handler_cls.timeout = 0.5  # the class-level socket deadline, shrunk
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(_request(["Content-Length: 10"], b"{"))  # promises 10, sends 1
            s.settimeout(10)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            assert data.startswith(b"HTTP/1.0 408") or data.startswith(b"HTTP/1.1 408"), data[:80]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_trickling_body_still_hits_the_deadline():
    """The read deadline is absolute, not idle: a peer that sends one byte
    every 0.3 s against a 1 s budget never trips an idle timeout but must
    still be answered 408 within the budget (review r2)."""
    import time as _time
    from pcd_sidecar.server import make_handler

    engine = FakeEngine()
    config = Config(
        model_id=engine.model_id, device="cpu", dtype=None, port=0, threads=None,
        pmi_enabled=True, backend="torch",
    )
    state = SidecarState(engine, PMICache(engine, enabled=True), config)
    handler_cls = make_handler(state)
    handler_cls.timeout = 1.0
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(_request(["Content-Length: 4000"]))
            start = _time.monotonic()
            s.setblocking(False)
            data = b""
            while _time.monotonic() - start < 6:
                try:
                    s.sendall(b"{")
                except (BlockingIOError, BrokenPipeError, ConnectionResetError):
                    pass
                _time.sleep(0.3)
                try:
                    chunk = s.recv(4096)
                    if chunk:
                        data += chunk
                    if b"\r\n\r\n" in data:
                        break
                except (BlockingIOError, ConnectionResetError):
                    continue
            elapsed = _time.monotonic() - start
            assert data.startswith(b"HTTP/1.0 408") or data.startswith(b"HTTP/1.1 408"), data[:80]
            assert elapsed < 2.5, elapsed  # a 1 s budget plus slack; a 3 s deadline would fail here
    finally:
        httpd.shutdown()
        httpd.server_close()
