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
