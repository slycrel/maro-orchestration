import base64, io, json, os, re, sys
from pathlib import Path

SRC = sys.argv[1]
TMP = sys.argv[2]
sys.path.insert(0, SRC)

import config
import loop_finalize as lf

_REAL_CONFIG_GET = config.get

# The workspace this probe seeds is a temp tree the Go test owns. Assert it
# before anything writes: MARO_WORKSPACE is the store override, and a probe
# that mis-resolves it appends run risks into the operator's LIVE project
# records (2026-08-16, a live ledger destroyed exactly this way).
_FORBIDDEN = str((Path.home() / ".maro").resolve())


class Boom(RuntimeError):
    pass


class FakeLog:
    def __init__(self, sink):
        self.sink = sink

    def warning(self, fmt, *a):
        self.sink.append(fmt % a)

    def info(self, fmt, *a):
        pass

    def debug(self, fmt, *a):
        pass


_STAMP = re.compile(r"^## \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.M)


def normalize(text):
    # append_section_lines stamps each block with the wall clock. The two
    # runtimes cannot agree on that and there is nothing to learn from the
    # disagreement, so it is normalized away on BOTH sides identically —
    # the shape of the line still has to match, only its value is freed.
    return _STAMP.sub("## <STAMP>", text)


def seed(ws, sc):
    (ws / "runs").mkdir(parents=True, exist_ok=True)
    rdn = sc["run_dir_name"]
    if rdn:
        build = ws / "runs" / rdn / "build"
        build.mkdir(parents=True, exist_ok=True)
        if sc["verdicts_is_dir"]:
            (build / "closure_verdicts.jsonl").mkdir()
        elif sc["write_verdicts"]:
            blob = sc["verdicts_b64"]
            if blob:
                (build / "closure_verdicts.jsonl").write_bytes(
                    base64.b64decode(blob))
            else:
                (build / "closure_verdicts.jsonl").write_text(
                    sc["verdicts"], encoding="utf-8")
        if sc["scope_failed"]:
            (build / "scope-raw-FAILED.txt").write_text("raw", encoding="utf-8")
    if sc["write_risks"]:
        rp = ws / "projects" / sc["project"] / "RISKS.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        if sc["risks_pre_b64"]:
            rp.write_bytes(base64.b64decode(sc["risks_pre_b64"]))
        else:
            rp.write_text(sc["risks_pre"], encoding="utf-8")


def run_mint(sc):
    ws = (Path(TMP) / sc["name"]).resolve()
    assert not str(ws).startswith(_FORBIDDEN), ws
    ws.mkdir(parents=True, exist_ok=True)
    seed(ws, sc)
    os.environ["MARO_WORKSPACE"] = str(ws)

    mint = sc["risk_mint"]

    def fake_get(key, default=None):
        if key == "project.risk_mint":
            if mint == "raise":
                raise Boom("config exploded")
            return mint == "true"
        return _REAL_CONFIG_GET(key, default)

    logs = []
    config.get = fake_get
    lf.log = FakeLog(logs)
    try:
        n = lf._mint_run_risks_to_project(sc["project"], sc["loop_id"])
    finally:
        config.get = _REAL_CONFIG_GET

    rp = ws / "projects" / sc["project"] / "RISKS.md" if sc["project"] else None
    exists = bool(rp and rp.exists())
    # backslashreplace, not read_text: a fixture deliberately seeds a
    # RISKS.md that is not UTF-8, and the readback must render it rather
    # than raise. Each undecodable byte becomes \xNN, which is byte-exact
    # AND readable in a failure dump — unlike errors="replace", which
    # collapses distinct bytes to the same U+FFFD.
    body = normalize(rp.read_bytes().decode("utf-8", "backslashreplace")) \
        if exists else ""
    # The two runtimes seed sibling workspaces, so any absolute path an
    # OSError message carries differs by that one directory name and by
    # nothing else. Normalizing the root keeps the REST of the path — the
    # runs/<dir>/build/<file> tail is exactly what the message is for.
    warnings = [w.replace(str(ws), "<WS>") for w in logs]
    return {"name": sc["name"], "minted": n, "risks_exists": exists,
            "risks": body, "warnings": warnings}


def run_lesson(sc):
    if sc["which"] == "recovery":
        text = lf._recovery_plan_lesson_text(sc["failure_class"], sc["action"])
    else:
        text = lf._auto_diagnosis_lesson_text(sc["failure_class"],
                                              sc["recommendation"])
    return {"name": sc["name"], "text": text}


def run_registry(sc):
    logs = []
    lf.log = FakeLog(logs)
    drains = []
    for op in sc["ops"]:
        if op["op"] == "defer":
            fail = op["fail"]

            def fn(_fail=fail):
                if _fail:
                    raise Boom(_fail)

            lf.defer_maintenance_post_notify(op["handle"], fn)
        else:
            drains.append(lf.drain_deferred_maintenance(op["handle"]))
    return {"name": sc["name"], "drains": drains, "warnings": logs}


KINDS = {"mint": run_mint, "lesson": run_lesson, "registry": run_registry}

spec = json.loads(io.open(sys.argv[3], encoding="utf-8").read())
print(json.dumps([KINDS[sc["kind"]](sc) for sc in spec]))
