import io, json, os, sys, threading, time, contextlib

SRC = sys.argv[1]
sys.path.insert(0, SRC)
os.environ["COLUMNS"] = "80"

import agent_loop as al


class FakeResult:
    def __init__(self, status, summary):
        self.status = status
        self._summary = summary

    def summary(self):
        return self._summary


def run_main(sc):
    calls = []

    def stub(goal, **kw):
        calls.append(dict(kw, goal=goal))
        # No .get defaults here: the scenario always carries both fields, and
        # a default would let an omitted fixture field silently agree with the
        # port's own default instead of testing it.
        b = sc["run"]
        return FakeResult(b["status"], b["summary"])

    al.run_agent_loop = stub
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = al.main(sc["argv"])
        except SystemExit as e:
            code = e.code
    return {"name": sc["name"], "exit": code, "stdout": out.getvalue(),
            "stderr": err.getvalue(), "calls": calls}


def run_parallel(sc):
    lock = threading.Lock()
    started = []
    inflight = [0]
    peak = [0]
    # Every key is present by construction (see the scenario builders): a
    # default here would be a value the fixture never chose.
    beh = sc["beh"]

    def stub(goal, **kw):
        with lock:
            started.append(goal)
            inflight[0] += 1
            peak[0] = max(peak[0], inflight[0])
        time.sleep(beh["sleep"])
        with lock:
            inflight[0] -= 1
        if goal in beh["raise_on"]:
            raise ValueError("boom " + goal)
        return "R:" + goal

    al.run_agent_loop = stub
    out = {"name": sc["name"]}
    try:
        res = al.run_parallel_loops(sc["goals"], max_workers=sc["max_workers"])
        out["results"] = list(res)
    except Exception as exc:
        out["error"] = type(exc).__name__ + ": " + str(exc)
    out["started"] = started
    out["peak"] = peak[0]
    return out


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
recs = []
for sc in spec:
    recs.append(run_main(sc) if sc["kind"] == "main" else run_parallel(sc))
print(json.dumps(recs))
