import builtins, contextlib, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import agent_loop as al


class ReachedFence(BaseException):
    """Raised in place of the fence's first statement.

    The head has no seam either: it runs straight into the execution
    fence. So the probe drives the real function with _initialize_loop
    faked and the fence's FIRST call — `_ce.set_container_suppressed`,
    which the fence performs before anything else on purpose — raising.

    It derives from BaseException, not Exception, because every handler
    in the fence is `except Exception`. A sentinel the subject can catch
    is a sentinel that measures the subject's handlers instead of the
    span above them.
    """


class Marker:
    """An opaque object passed through untouched, identifiable in a
    record. run_agent_loop hands most of its signature to
    _initialize_loop without looking at it, and a fixture of plain
    strings could not tell "passed through" from "reconstructed"."""

    def __init__(self, tag):
        self.tag = tag

    def __repr__(self):
        return "<Marker %s>" % self.tag


class Ctx:
    """The context _initialize_loop returns.

    It carries the six fields the head rebinds and the one it ASSIGNS.
    channel is deliberately absent until run_agent_loop sets it, so a
    port that never performed the assignment leaves an AttributeError
    rather than a value that happened to match.
    """

    def __init__(self, sc):
        self.loop_id = sc["ctx_loop_id"]
        self.start_ts = sc["ctx_start_ts"]
        self.project = sc["ctx_project"]
        self.adapter = Marker("ctx-adapter")
        self.interrupt_queue = Marker("ctx-queue")
        self.perm_ctx = Marker("ctx-perm")
        self.run_worktree = None


def pv(v):
    """A canonical rendering both engines can produce."""
    if isinstance(v, Marker):
        return "<Marker %s>" % v.tag
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return [[k, pv(x)] for k, x in v.items()]
    if isinstance(v, (list, tuple)):
        return [pv(x) for x in v]
    return "<%s>" % type(v).__name__


def run_one(sc):
    calls = []

    def rec(name, **kw):
        calls.append({"call": name,
                      "kw": [[k, pv(v)] for k, v in kw.items()]})

    ctx = Ctx(sc)

    def initialize_loop(goal, **kw):
        # The ORDER of the keywords is the record: CPython evaluates them
        # left to right and a **kw dict preserves that order, so a port
        # that passes the same names in a different order is visible here
        # and nowhere else.
        calls.append({"call": "_initialize_loop",
                      "kw": [["goal", pv(goal)]]
                            + [[k, pv(v)] for k, v in kw.items()]})
        # After the record: the keywords are what this scenario is for,
        # and a raise above the append would throw them away.
        if sc["init_raises"]:
            raise ValueError("initialize refused")
        early = None
        if sc["early_return"]:
            # The status is a fixture knob because `is not None` is the
            # ONLY test Python makes here. A LoopResult with an empty
            # status is falsy-looking and still ends the run.
            early = al.LoopResult(loop_id="early", goal=sc["goal"],
                                  project=None, status=sc["early_status"],
                                  steps=[])
        return (ctx, early)
    al._initialize_loop = initialize_loop

    @contextlib.contextmanager
    def loop_id_scope(loop_id):
        rec("loop_id_scope.enter", loop_id=loop_id)
        if sc["scope_raises"]:
            raise RuntimeError("scope refused")
        try:
            yield
        finally:
            rec("loop_id_scope.exit", loop_id=loop_id)
            if sc["exit_raises"]:
                # An exception from __exit__ REPLACES the body's, which is
                # the only way to tell "the exit ran" from "the exit's
                # failure was swallowed".
                raise RuntimeError("scope exit failed")

    import captains_log as clog
    clog.loop_id_scope = loop_id_scope

    import llm
    for name, val in (("MODEL_CHEAP", sc["model_cheap"]),
                      ("MODEL_MID", sc["model_mid"]),
                      ("MODEL_POWER", sc["model_power"])):
        if sc["drop_tier_const"] == name:
            if hasattr(llm, name):
                delattr(llm, name)
        else:
            setattr(llm, name, val)

    import container_exec as _ce

    def set_container_suppressed(flag):
        rec("fence.reached", suppressed=flag)
        raise ReachedFence()
    _ce.set_container_suppressed = set_container_suppressed

    # _pyprobe_block evicts, installs the finder, and PROVES each name is
    # gone. This probe imports captains_log a few lines above to patch
    # loop_id_scope, and a meta-path finder is never consulted for a
    # module already in sys.modules — without the eviction the "dead
    # module" fixture ran the LIVE module and agreed with the port for
    # the wrong reason (L59 amendment 4).
    blocker = _pyprobe_block(sc["dead_modules"])

    out = {"name": sc["name"], "reached_fence": False, "result": None,
           "cls": "", "msg": ""}
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            r = al.run_agent_loop(sc["goal"], channel=Marker("channel"),
                                  **{k: (Marker(v[len("marker:"):])
                                         if isinstance(v, str)
                                         and v.startswith("marker:") else v)
                                     for k, v in sc["kwargs"].items()})
        out["result"] = {"loop_id": r.loop_id, "status": r.status}
    except ReachedFence:
        out["reached_fence"] = True
    except BaseException as e:
        out["cls"] = type(e).__name__
        out["msg"] = str(e)
    finally:
        _pyprobe_unblock(blocker)

    out["calls"] = calls
    # The assignment, observed on the object rather than through a return
    # value: `ctx.channel = channel` is the head's only WRITE.
    out["ctx_channel"] = (pv(ctx.channel) if hasattr(ctx, "channel")
                          else "<unset>")
    return out


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
