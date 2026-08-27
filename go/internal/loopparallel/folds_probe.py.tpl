import copy, dataclasses, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

# The two folds import these INSIDE the function body, so the fakes have to
# be in sys.modules before the call and not merely bound on the module.
fake_llm = types.ModuleType("llm")


class LLMTool:
    def __init__(self, **kw):
        self.kw = kw


fake_llm.LLMTool = LLMTool
sys.modules["llm"] = fake_llm

import loop_parallel as lp  # noqa: E402


class Boom(Exception):
    pass


class Ctx:
    """The LoopContext fields the two folds actually read.

    A stand-in rather than the real dataclass because the real one pulls in
    a terrain memory, a world-fact ledger and a state machine, none of
    which either fold touches -- and a fixture that constructs them is a
    fixture that can fail for reasons this tranche does not own.
    """

    def __init__(self, sc):
        self.goal = sc["goal"]
        self.project = sc["project"]
        self.loop_id = sc["loop_id"]
        self.verbose = sc["verbose"]
        self.ancestry_context = sc["ancestry"]
        self.pending_context = None
        self.adapter = types.SimpleNamespace(model_key=sc["model_key"])
        self.started_at = sc["started_at"]
        self.step_callback = None


def install(sc, calls, log_lines, err_buf, clock):
    lp.time = types.SimpleNamespace(monotonic=clock)
    lp.sys = types.SimpleNamespace(stderr=err_buf)

    class _Log:
        def _add(self, level):
            def _f(msg, *a):
                log_lines.append([level, msg % a if a else msg])
            return _f

        def __getattr__(self, name):
            return self._add(name)

    lp.log = _Log()

    def sched(name):
        def _f(**kw):
            calls.append([name, {
                "steps": list(kw["steps"]),
                "max_workers": kw["max_workers"],
                "project_dir": kw["project_dir"],
                "ancestry_context": kw["ancestry_context"],
                "incremental_context": kw["incremental_context"],
                "tools": [t.kw for t in kw["tools"]],
            }])
            if sc["beh"]["sched_raise"]:
                raise Boom(sc["beh"]["sched_raise"])
            return copy.deepcopy(sc["outcomes"])
        return _f

    lp._run_steps_parallel = sched("run_steps_parallel")
    lp._run_steps_dag = sched("run_steps_dag")

    def shape(steps, label=""):
        calls.append(["shape_steps", {"steps": list(steps), "label": label}])
        return [s.upper() for s in steps]

    lp._shape_steps = shape

    class _Orch:
        STATE_DONE = "done"

        def mark_item(self, project, index, state):
            calls.append(["mark_item",
                          {"project": project, "index": index, "state": state}])
            if sc["beh"]["mark_raise"]:
                raise Boom(sc["beh"]["mark_raise"])

        def append_next_items(self, project, items):
            calls.append(["append_next_items",
                          {"project": project, "items": list(items)}])
            if sc["beh"]["append_raise"]:
                raise Boom(sc["beh"]["append_raise"])
            return sc["beh"]["append_result"]

    lp._orch = lambda: _Orch()

    fake_metrics = types.ModuleType("metrics")

    def record_step_cost(**kw):
        calls.append(["record_step_cost", kw])
        if sc["beh"]["cost_raise"]:
            raise Boom(sc["beh"]["cost_raise"])

    fake_metrics.record_step_cost = record_step_cost
    sys.modules["metrics"] = fake_metrics

    fake_post = types.ModuleType("loop_post_step")

    def record_step_decisions(ctx, idx, oc, shared):
        calls.append(["record_step_decisions", {"step_idx": idx}])
        if sc["beh"]["decisions_raise"]:
            raise Boom(sc["beh"]["decisions_raise"])

    def record_step_world_facts(ctx, idx, oc):
        calls.append(["record_step_world_facts", {"step_idx": idx}])
        if sc["beh"]["facts_raise"]:
            raise Boom(sc["beh"]["facts_raise"])

    fake_post.record_step_decisions = record_step_decisions
    fake_post.record_step_world_facts = record_step_world_facts
    sys.modules["loop_post_step"] = fake_post


def make_clock(ticks):
    state = {"i": 0}

    def _f():
        i = state["i"]
        state["i"] = i + 1
        return ticks[i] if i < len(ticks) else ticks[-1]
    return _f


def run_batch(sc):
    calls, log_lines, err = [], [], io.StringIO()
    install(sc, calls, log_lines, err, make_clock(sc["ticks"]))
    ctx = Ctx(sc)
    step_outcomes = []
    completed = list(sc["completed_context"])
    remaining = list(sc["remaining_steps"])
    indices = list(sc["remaining_indices"])
    rec = {"name": sc["name"]}
    try:
        got = lp._run_parallel_batch(
            ctx, sc["step_text"], list(sc["peers"]),
            step_outcomes=step_outcomes,
            completed_context=completed,
            remaining_steps=remaining,
            remaining_indices=indices,
            loop_shared_ctx={},
            resolve_tools_fn=lambda: sc["tools"],
            parallel_fan_out=sc["fan_out"],
            proj_artifact_dir=sc["artifact_dir"],
            iteration=sc["iteration"],
            step_idx=sc["step_idx"],
            batch_item_indices=sc["item_indices"],
        )
        rec["ret"] = list(got)
    except Exception as exc:
        rec["error"] = type(exc).__name__ + ": " + str(exc)
    rec["step_outcomes"] = [dataclasses.asdict(o) for o in step_outcomes]
    rec["completed_context"] = completed
    rec["remaining_steps"] = remaining
    rec["remaining_indices"] = indices
    rec["calls"] = calls
    rec["log"] = log_lines
    rec["stderr"] = err.getvalue()
    return rec


def run_path(sc):
    calls, log_lines, err = [], [], io.StringIO()
    install(sc, calls, log_lines, err, make_clock(sc["ticks"]))
    ctx = Ctx(sc)
    cb = []
    if sc["beh"]["callback"]:
        def _cb(i, text, result, status):
            cb.append([i, text, result, status])
            if sc["beh"]["callback_raise"]:
                raise Boom(sc["beh"]["callback_raise"])
        ctx.step_callback = _cb
    rec = {"name": sc["name"]}
    try:
        res = lp._run_parallel_path(
            ctx, list(sc["steps"]),
            clean_steps=list(sc["clean_steps"]),
            deps={int(k): v for k, v in sc["deps"].items()},
            levels=sc["levels"],
            parallel_levels=sc["parallel_levels"],
            parallel_fan_out=sc["fan_out"],
            proj_fanout_dir=sc["artifact_dir"],
            loop_shared_ctx={},
            use_dag=sc["use_dag"],
            resolve_tools_fn=lambda: sc["tools"],
        )
        d = dataclasses.asdict(res)
        rec["result"] = {
            "loop_id": d["loop_id"], "project": d["project"],
            "goal": d["goal"], "status": d["status"],
            "total_tokens_in": d["total_tokens_in"],
            "total_tokens_out": d["total_tokens_out"],
            "elapsed_ms": d["elapsed_ms"],
            "stuck_reason": d["stuck_reason"],
            "audit_learning_allowed": d["audit_learning_allowed"],
            "steps": d["steps"],
        }
    except Exception as exc:
        rec["error"] = type(exc).__name__ + ": " + str(exc)
    rec["callback"] = cb
    rec["calls"] = calls
    rec["log"] = log_lines
    rec["stderr"] = err.getvalue()
    return rec


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_batch(s) if s["kind"] == "batch" else run_path(s)
                  for s in spec]))
