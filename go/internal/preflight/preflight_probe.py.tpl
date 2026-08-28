import contextlib, io, json, sys

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import pre_flight as pf


class Rec:
    """Stands in for pre_flight's module-level `log`.

    logging formats LAZILY — `log.info(fmt, *args)` does not build the
    string unless a handler wants it — so what is recorded here is the
    format string and its arguments applied the way logging would. A port
    that formatted eagerly and a port that formatted the same string
    differently are both visible; a port that logged at the wrong LEVEL is
    visible too, which is the point of keeping the level in the record.
    """

    def __init__(self):
        self.lines = []

    def _add(self, level, fmt, args):
        self.lines.append([level, (fmt % args) if args else fmt])

    def info(self, fmt, *a):
        self._add("INFO", fmt, a)

    def debug(self, fmt, *a):
        self._add("DEBUG", fmt, a)

    def warning(self, fmt, *a):
        self._add("WARNING", fmt, a)

    def log(self, level, fmt, *a):
        import logging
        self._add(logging.getLevelName(level), fmt, a)


def pv(v):
    """A canonical rendering both engines can produce.

    Floats are tagged with their repr rather than emitted as JSON numbers.
    Go's encoder writes float64(1) as `1` and CPython writes `1.0`, so an
    untagged float would compare equal to an int and hide the exact
    divergence the round()/None distinction in this file is about.

    Dicts become PAIR LISTS, which keeps their order and lets a key be
    something other than a string — `scope_breakdown` is keyed by a value
    read out of a record, not by a JSON object key.
    """
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, float):
        return ["<float>", repr(v)]
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        return [[pv(k), pv(x)] for k, x in v.items()]
    if isinstance(v, (list, tuple)):
        return [pv(x) for x in v]
    return "<%s>" % type(v).__name__


def pv_review(r):
    if r is None:
        return None
    return {"scope": r.scope,
            "scope_note": pv(r.scope_note),
            "flags": [[f.kind, f.step, pv(f.message), f.severity]
                      for f in r.flags],
            "milestone": pv(r.milestone_step_indices),
            "raw": r.raw}


def build_review(sc):
    return pf.PlanReview(
        scope=sc["scope"],
        scope_note=sc["scope_note"],
        flags=[pf.PlanFlag(kind=f[0], step=f[1], message=f[2], severity=f[3])
               for f in sc["flags"]],
        milestone_step_indices=list(sc["milestone"]),
        raw=sc.get("raw", ""))


class Resp:
    """The reviewer's response object. `content` is absent for the
    "no attribute" fixture, because `resp.content` is inside the same
    `except Exception` as the call and the two are indistinguishable to
    review_plan — which is exactly the claim being pinned."""

    def __init__(self, sc):
        if sc["mode"] != "no_content":
            self.content = sc["content"]


def run_one(sc):
    kind = sc["kind"]
    log = Rec()
    old_log = pf.log
    pf.log = log
    out = {"name": sc["name"], "cls": "", "msg": ""}
    try:
        out.update(dispatch(kind, sc, log))
    except BaseException as e:
        out["cls"] = type(e).__name__
        out["msg"] = str(e)
    finally:
        pf.log = old_log
    out["log"] = log.lines
    return out


def dispatch(kind, sc, log):
    if kind == "review_system":
        return {"text": pf._REVIEW_SYSTEM}

    if kind == "heuristic":
        return {"scope": pf._heuristic_scope(sc["steps"])}

    if kind == "review_obj":
        r = build_review(sc)
        return {"has_concerns": r.has_concerns,
                "summary": r.summary(),
                "format_for_log": r.format_for_log()}

    if kind == "parse":
        return {"review": pv_review(pf._parse_review(sc["raw"]))}

    if kind == "review_plan":
        return run_review_plan(sc)

    if kind == "stats":
        return run_stats(sc)

    raise AssertionError("unknown kind %r" % kind)


def run_review_plan(sc):
    calls = []

    import llm
    had = hasattr(llm, "LLMMessage")
    saved = getattr(llm, "LLMMessage", None)
    if sc["drop_llm_message"] and had:
        del llm.LLMMessage

    class Adapter:
        def __init__(self, spec):
            self.spec = spec

        def complete(self, messages, **kw):
            calls.append({"call": "complete",
                          "name": self.spec["name"],
                          "messages": [[m.role, m.content] for m in messages],
                          "kw": [[k, pv(v)] for k, v in kw.items()]})
            if self.spec["mode"] == "raise":
                raise RuntimeError(self.spec["content"])
            return Resp(self.spec)

    def build_reviewers():
        for spec in sc["reviewers"]:
            if spec["mode"] == "generator_raises":
                # The generator itself failing is NOT the inner handler's
                # business: it is raised by the `for` statement, so it
                # skips past the per-reviewer except and lands on the
                # outer one. The record shows the yields that already
                # happened, which is what makes the LAZINESS observable.
                calls.append({"call": "generator_raises", "name": spec["name"]})
                raise RuntimeError(spec["content"])
            calls.append({"call": "yield", "name": spec["name"]})
            yield (spec["name"], Adapter(spec))

    old_build = pf._build_reviewers
    pf._build_reviewers = build_reviewers
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            r = pf.review_plan(sc["goal"], sc["steps"], None,
                               verbose=sc["verbose"])
    finally:
        pf._build_reviewers = old_build
        if had:
            llm.LLMMessage = saved
    return {"review": pv_review(r), "calls": calls,
            "stderr": err.getvalue()}


def run_stats(sc):
    import os
    import orch_items
    from pathlib import Path

    # Each scenario gets its own directory, named by the spec, so the
    # two engines see the SAME tree: the probe runs every scenario before
    # the Go side runs any, and a shared filename would leave only the
    # last one's fixture on disk.
    d = os.path.join(sys.argv[3], sc["dir"])
    os.makedirs(d, exist_ok=True)
    default = os.path.join(d, "preflight_calibration.jsonl")
    if sc["write_file"]:
        io.open(default, "w", encoding="utf-8", newline="").write(sc["body"])
    if sc["write_bytes"]:
        io.open(default, "wb").write(bytes(sc["write_bytes"]))
    adir = os.path.join(d, "adir")
    if sc["write_dir"]:
        os.makedirs(adir, exist_ok=True)

    old = getattr(orch_items, "memory_dir", None)
    if sc["memory_dir_fails"]:
        if old is not None:
            del orch_items.memory_dir
    else:
        orch_items.memory_dir = lambda: Path(d)
    try:
        kind = sc["cal_path_kind"]
        if kind == "none":
            res = pf.preflight_calibration_stats()
        elif kind == "default":
            res = pf.preflight_calibration_stats(cal_path=default)
        elif kind == "missing":
            res = pf.preflight_calibration_stats(
                cal_path=os.path.join(d, "nope.jsonl"))
        elif kind == "empty":
            res = pf.preflight_calibration_stats(cal_path="")
        elif kind == "dir":
            res = pf.preflight_calibration_stats(cal_path=adir)
        elif kind == "int":
            res = pf.preflight_calibration_stats(cal_path=123)
        else:
            raise AssertionError("unknown cal_path_kind %r" % kind)
    finally:
        if old is not None:
            orch_items.memory_dir = old
        elif hasattr(orch_items, "memory_dir"):
            del orch_items.memory_dir
    return {"stats": pv(res)}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
